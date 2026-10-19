"""Evidence-backed bridge between marketplace rows and physical stock.

Legacy listings are not automatically treated as physical inventory: doing so
would make a guessed title match capable of blocking (or consuming) stock.
An explicit product id is preferred, followed by an exact barcode/GTIN match.
This keeps the bridge safe while manifest/scanner intake progressively links
more listings to the unified inventory ledger.
"""
from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import InventoryEvent, InventoryProduct, InventoryStock, Listing


_PRODUCT_ID_KEYS = ("inventory_product_id", "physical_inventory_product_id", "product_id")
_IDENTIFIER_KEYS = ("barcode", "upc", "ean", "gtin", "asin")


def _mapping_values(value: Any, keys: tuple[str, ...]) -> list[str]:
    if not isinstance(value, dict):
        return []
    result: list[str] = []
    for key in keys:
        raw = value.get(key)
        if raw is not None and str(raw).strip():
            result.append(str(raw).strip())
    return result


def linked_product(db: Session, listing: Listing) -> tuple[InventoryProduct | None, str | None]:
    """Return only a deterministically linked physical product.

    The metadata shapes intentionally support older importers and the newer
    ``physical_inventory`` envelope without requiring a destructive listing
    migration.
    """
    sources = [listing.marketplace_data, listing.source_metadata, listing.item_specifics]
    for source in sources:
        nested = source.get("physical_inventory") if isinstance(source, dict) else None
        for candidate in (source, nested):
            for raw_id in _mapping_values(candidate, _PRODUCT_ID_KEYS):
                if raw_id.isdigit():
                    product = db.get(InventoryProduct, int(raw_id))
                    if product and product.user_id == listing.user_id:
                        return product, "explicit_product_id"

    identifiers: list[str] = []
    for source in sources:
        nested = source.get("physical_inventory") if isinstance(source, dict) else None
        identifiers.extend(_mapping_values(source, _IDENTIFIER_KEYS))
        identifiers.extend(_mapping_values(nested, _IDENTIFIER_KEYS))
    for value in dict.fromkeys(identifiers):
        rows = db.execute(select(InventoryProduct).where(InventoryProduct.user_id == listing.user_id)).scalars()
        matches = [row for row in rows if value in _mapping_values(row.identifiers_json, _IDENTIFIER_KEYS)]
        if len(matches) == 1:
            return matches[0], "exact_identifier"
    return None, None


def availability_for_listing(db: Session, listing: Listing) -> dict[str, Any]:
    product, linkage = linked_product(db, listing)
    requested = max(0, int(listing.quantity or 0))
    if not product:
        return {"tracked": False, "requested_quantity": requested, "available_quantity": None, "product_id": None, "linkage": None}
    rows = db.execute(
        select(InventoryStock).where(
            InventoryStock.user_id == listing.user_id,
            InventoryStock.product_id == product.id,
        )
    ).scalars().all()
    on_hand = sum(max(0, int(row.on_hand or 0)) for row in rows)
    reserved = sum(max(0, int(row.reserved or 0)) for row in rows)
    available = max(0, on_hand - reserved)
    return {
        "tracked": True,
        "product_id": product.id,
        "product_key": product.product_key,
        "product_title": product.title,
        "linkage": linkage,
        "requested_quantity": requested,
        "on_hand_quantity": on_hand,
        "reserved_quantity": reserved,
        "available_quantity": available,
        "sufficient": available >= requested,
    }


def record_confirmed_sale_depletion(db: Session, listing: Listing, *, sale_id: int, quantity: int) -> dict[str, Any]:
    """Consume linked physical stock exactly once for a confirmed remote sale.

    The caller owns the surrounding transaction. The inventory event's stable
    operation key makes webhook/poll replay safe even before the sale poller's
    wider de-duplication is considered.
    """
    availability = availability_for_listing(db, listing)
    if not availability["tracked"]:
        return {"status": "not_tracked", **availability}
    amount = max(1, int(quantity or 1))
    operation_id = "sale-" + hashlib.sha256(f"{listing.user_id}:{sale_id}".encode()).hexdigest()[:56]
    existing = db.execute(select(InventoryEvent).where(InventoryEvent.user_id == listing.user_id, InventoryEvent.operation_id == operation_id)).scalar_one_or_none()
    if existing:
        return {"status": "replayed", **availability}

    product_id = int(availability["product_id"])
    rows = db.execute(
        select(InventoryStock)
        .where(InventoryStock.user_id == listing.user_id, InventoryStock.product_id == product_id)
        .order_by(InventoryStock.location_key, InventoryStock.id)
        .with_for_update()
    ).scalars().all()
    remaining = amount
    consumed: list[dict[str, Any]] = []
    for row in rows:
        if remaining <= 0:
            break
        # A reservation protects stock from movement, but a confirmed sale is
        # the terminal consumer. Consume it before unreserved stock where
        # possible, never letting either counter go negative.
        take = min(remaining, max(0, int(row.on_hand or 0)))
        if not take:
            continue
        reserved_used = min(take, max(0, int(row.reserved or 0)))
        row.on_hand -= take
        row.reserved -= reserved_used
        row.sold += take
        remaining -= take
        consumed.append({"location_key": row.location_key, "quantity": take, "reserved_released": reserved_used})

    db.add(InventoryEvent(
        user_id=listing.user_id,
        operation_id=operation_id,
        event_type="marketplace_sale_depleted" if remaining == 0 else "marketplace_sale_stock_shortfall",
        entity_type="inventory_product",
        entity_key=str(product_id),
        payload_json={"sale_id": sale_id, "listing_id": listing.id, "requested_quantity": amount, "consumed": consumed, "shortfall": remaining},
    ))
    return {"status": "depleted" if remaining == 0 else "shortfall", "consumed": consumed, "shortfall": remaining, **availability}
