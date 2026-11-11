"""Canonical listing option/variant inventory operations.

Variants are deliberately independent of marketplace connectors.  Connectors
consume the canonical rows and write MarketplaceVariant projections.
"""
from __future__ import annotations

from itertools import product
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.models import (
    Listing,
    ListingVariant,
    OptionPreset,
    ProductOption,
    ProductOptionValue,
    VariantInventoryEvent,
    VariantOptionValue,
)


SYSTEM_PRESETS: tuple[dict[str, Any], ...] = (
    {"name": "Apparel Size", "axes": [{"name": "Size", "values": ["XXS", "XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL", "6XL", "One Size"]}]},
    {"name": "Gender / Department", "axes": [{"name": "Gender / Department", "values": ["Men", "Women", "Unisex", "Boys", "Girls", "Kids"]}]},
    {"name": "Color", "axes": [{"name": "Color", "values": ["Black", "White", "Gray", "Silver", "Navy", "Blue", "Red", "Green", "Yellow", "Orange", "Pink", "Purple", "Brown", "Tan", "Beige", "Gold", "Multicolor"]}]},
    {"name": "Fit", "axes": [{"name": "Fit", "values": ["Regular", "Slim", "Relaxed", "Oversized", "Tall", "Petite", "Athletic"]}]},
    {"name": "Apparel Style", "axes": [{"name": "Apparel Style", "values": ["Short Sleeve", "Long Sleeve", "Sleeveless", "Tank", "Crew Neck", "V-Neck", "Polo", "Hoodie", "Sweatshirt"]}]},
    {"name": "Storage Capacity", "axes": [{"name": "Storage Capacity", "values": ["8 GB", "16 GB", "32 GB", "64 GB", "128 GB", "256 GB", "512 GB", "1 TB", "2 TB", "4 TB", "8 TB"]}]},
    {"name": "Pack Quantity", "axes": [{"name": "Pack Quantity", "values": ["1 Pack", "2 Pack", "3 Pack", "4 Pack", "5 Pack", "6 Pack", "8 Pack", "10 Pack", "12 Pack", "24 Pack"]}]},
    {"name": "Voltage", "axes": [{"name": "Voltage", "values": ["5V", "12V", "18V", "20V", "24V", "36V", "40V", "48V", "60V", "80V"]}]},
    {"name": "Tool / Battery Platform", "axes": [{"name": "Tool / Battery Platform", "values": ["Ryobi 18V", "Ryobi 40V", "Milwaukee M12", "Milwaukee M18", "DeWalt 20V MAX", "DeWalt 60V FLEXVOLT", "Kobalt 24V", "Kobalt 40V", "Greenworks 40V", "Greenworks 60V", "Greenworks 80V", "HART 20V", "RIDGID 18V"]}]},
    {"name": "Length", "axes": [{"name": "Length", "values": []}]},
    {"name": "Width", "axes": [{"name": "Width", "values": []}]},
    {"name": "Diameter", "axes": [{"name": "Diameter", "values": []}]},
    {"name": "Material", "axes": [{"name": "Material", "values": []}]},
    {"name": "Model / Version", "axes": [{"name": "Model / Version", "values": []}]},
)


def ensure_system_presets(db: Session) -> list[OptionPreset]:
    existing = {row.name: row for row in db.execute(select(OptionPreset).where(OptionPreset.scope == "system")).scalars().all()}
    changed = False
    for definition in SYSTEM_PRESETS:
        row = existing.get(definition["name"])
        if row is None:
            row = OptionPreset(scope="system", user_id=None, name=definition["name"], axes=definition["axes"])
            db.add(row)
            existing[row.name] = row
            changed = True
    if changed:
        db.commit()
    return list(existing.values())


def serialize_preset(row: OptionPreset) -> dict[str, Any]:
    return {
        "id": row.id, "scope": row.scope, "name": row.name,
        "axes": row.axes or [], "default_price": row.default_price,
        "price_modifiers": row.price_modifiers or {}, "sku_pattern": row.sku_pattern,
        "notes": row.notes,
    }


def _sku(parent_sku: str, values: list[str], pattern: str | None, index: int) -> str:
    if pattern:
        rendered = pattern.replace("{parent_sku}", parent_sku)
        for value in values:
            rendered = rendered.replace("{value}", value)
        # For one-axis presets, {size}/{color} convenience placeholders work.
        for key, value in (("size", values[0] if values else ""), ("color", values[1] if len(values) > 1 else "")):
            rendered = rendered.replace("{" + key + "}", value)
        rendered = rendered.strip()
        if rendered:
            return rendered[:255]
    suffix = "-".join("".join(ch for ch in str(value).upper() if ch.isalnum()) or "X" for value in values)
    return f"{parent_sku}-{suffix or index}"[:255]


def serialize_listing_variants(listing: Listing) -> dict[str, Any]:
    options = sorted(listing.product_options or [], key=lambda row: (row.position, row.id))
    variants = []
    for variant in sorted(listing.variants or [], key=lambda row: row.id):
        values = {}
        for link in variant.option_links or []:
            option = link.option_value.option if link.option_value else None
            if option:
                values[option.name] = link.option_value.value
        variants.append({
            "id": variant.id, "sku": variant.sku, "barcode": variant.barcode,
            "values": values, "quantity_on_hand": variant.quantity_on_hand,
            "quantity_reserved": variant.quantity_reserved,
            "quantity_available": variant.quantity_available, "price": variant.price,
            "compare_at_price": variant.compare_at_price, "cost": variant.cost,
            "status": variant.status,
            "marketplaces": [{"marketplace": row.marketplace, "remote_listing_id": row.remote_listing_id, "remote_variant_id": row.remote_variant_id, "remote_sku": row.remote_sku, "quantity": row.last_confirmed_quantity, "price": row.last_confirmed_price, "sync_status": row.sync_status} for row in (variant.marketplace_variants or [])],
        })
    return {"options": [{"name": row.name, "position": row.position, "values": [{"value": value.value, "position": value.position} for value in row.values] } for row in options], "variants": variants}


def replace_listing_variants(db: Session, listing: Listing, payload: dict[str, Any]) -> dict[str, Any]:
    option_defs = payload.get("options") or []
    variant_defs = payload.get("variants") or []
    if not isinstance(option_defs, list) or not isinstance(variant_defs, list):
        raise ValueError("options and variants must be arrays")
    # This endpoint is an explicit canonical structure replacement.  Existing
    # marketplace projections are intentionally not touched here; they are
    # reconciled by destination-specific sync workers after the save.
    listing.product_options.clear()
    listing.variants.clear()
    db.flush()
    option_map: dict[str, ProductOption] = {}
    for position, raw_option in enumerate(option_defs):
        name = str((raw_option or {}).get("name") or "").strip()
        values = (raw_option or {}).get("values") or []
        if not name or not isinstance(values, list):
            raise ValueError("each option requires a name and values")
        normalized_values = []
        for index, raw_value in enumerate(values):
            value = str((raw_value or {}).get("value") if isinstance(raw_value, dict) else raw_value).strip()
            if value and value.lower() not in {item.lower() for item in normalized_values}:
                normalized_values.append(value)
        if not normalized_values:
            raise ValueError(f"option {name} must contain at least one value")
        option = ProductOption(listing_id=listing.id, name=name, position=position)
        option.values = [ProductOptionValue(value=value, position=index) for index, value in enumerate(normalized_values)]
        db.add(option)
        option_map[name] = option
    db.flush()
    value_maps = {name: {value.value: value for value in option.values} for name, option in option_map.items()}
    seen: set[tuple[tuple[str, str], ...]] = set()
    for raw_variant in variant_defs:
        raw_values = (raw_variant or {}).get("values") or {}
        if not isinstance(raw_values, dict):
            raise ValueError("variant values must be an object")
        pairs = tuple(sorted((str(name), str(value).strip()) for name, value in raw_values.items() if str(value).strip()))
        if len(pairs) != len(option_map) or pairs in seen:
            raise ValueError("each variant must contain a unique value for every option")
        if any(name not in value_maps or value not in value_maps[name] for name, value in pairs):
            raise ValueError("variant contains an option value not selected for this listing")
        seen.add(pairs)
        quantity = int((raw_variant or {}).get("quantity_on_hand", (raw_variant or {}).get("quantity", 0)) or 0)
        if quantity < 0:
            raise ValueError("variant quantity cannot be negative")
        price = (raw_variant or {}).get("price")
        variant = ListingVariant(
            listing_id=listing.id,
            sku=str((raw_variant or {}).get("sku") or _sku(str((listing.source_metadata or {}).get("sku") or f"PP-{listing.id}"), [value for _, value in pairs], None, len(seen))).strip()[:255],
            barcode=str((raw_variant or {}).get("barcode") or "").strip() or None,
            quantity_on_hand=quantity,
            quantity_reserved=max(0, int((raw_variant or {}).get("quantity_reserved", 0) or 0)),
            price=float(price) if price not in (None, "") else None,
            compare_at_price=(float((raw_variant or {}).get("compare_at_price")) if (raw_variant or {}).get("compare_at_price") not in (None, "") else None),
            cost=(float((raw_variant or {}).get("cost")) if (raw_variant or {}).get("cost") not in (None, "") else None),
            status="active" if quantity > 0 else "sold_out",
        )
        variant.option_links = [VariantOptionValue(option_value=value_maps[name][value]) for name, value in pairs]
        db.add(variant)
    db.flush()
    return serialize_listing_variants(listing)


def decrement_variant(db: Session, *, variant_id: int, marketplace: str, external_event_id: str, quantity: int, details: dict | None = None) -> dict[str, Any]:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    existing = db.execute(select(VariantInventoryEvent).where(VariantInventoryEvent.marketplace == marketplace, VariantInventoryEvent.external_event_id == external_event_id)).scalar_one_or_none()
    if existing:
        variant = db.execute(select(ListingVariant).where(ListingVariant.id == existing.variant_id)).scalar_one()
        return {"idempotent": True, "variant_id": variant.id, "quantity_available": variant.quantity_available, "quantity_after": existing.quantity_after}
    variant = db.execute(select(ListingVariant).where(ListingVariant.id == variant_id).with_for_update()).scalar_one()
    available = variant.quantity_available
    if available < quantity:
        raise ValueError(f"inventory conflict: only {available} units available")
    variant.quantity_on_hand -= quantity
    if variant.quantity_available == 0:
        variant.status = "sold_out"
    event = VariantInventoryEvent(variant_id=variant.id, marketplace=marketplace, external_event_id=external_event_id, quantity_delta=-quantity, quantity_after=variant.quantity_available, details=details or {})
    db.add(event)
    db.flush()
    return {"idempotent": False, "variant_id": variant.id, "quantity_available": variant.quantity_available, "quantity_after": event.quantity_after}
