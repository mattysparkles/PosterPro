"""Field- and marketplace-scoped listing mutations.

All callers (UI, bulk workflows, and the agent layer) can use this small
contract without accidentally overwriting destinations that were not targeted.
"""
from __future__ import annotations

from datetime import datetime, UTC
from copy import deepcopy
from typing import Any

from app.models.models import Listing
from app.services.listing_provenance import mark_field_provenance


SUPPORTED_MARKETS = {"ebay", "facebook", "mercari", "poshmark", "vinted", "etsy", "offerup"}


def validate_marketplace_operation_plan(
    operations: list[dict[str, Any]],
    listings_by_id: dict[int, Listing],
) -> list[dict[str, Any]]:
    """Normalize and validate a compound mutation plan before applying it.

    Plans are intentionally explicit: every operation names its listing IDs,
    destination markets, field, action, and (when applicable) value.  The
    entire plan is validated first so one malformed operation cannot leave an
    earlier operation partially applied.
    """
    if not isinstance(operations, list) or not operations:
        raise ValueError("At least one mutation operation is required")
    normalized: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ValueError(f"Operation {index + 1} must be an object")
        raw_ids = operation.get("listing_ids", operation.get("item_ids", operation.get("listing_id")))
        if isinstance(raw_ids, (str, int)):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError(f"Operation {index + 1} must name listing IDs")
        listing_ids: list[int] = []
        for raw_id in raw_ids:
            try:
                listing_id = int(raw_id)
            except (TypeError, ValueError):
                raise ValueError(f"Operation {index + 1} has an invalid listing ID")
            if listing_id not in listings_by_id:
                raise ValueError(f"Listing {listing_id} is not available in this operation scope")
            listing_ids.append(listing_id)
        markets = operation.get("marketplaces", operation.get("markets"))
        if isinstance(markets, str):
            markets = [markets]
        if not isinstance(markets, list):
            raise ValueError(f"Operation {index + 1} must name marketplaces")
        field = str(operation.get("field") or "").strip()
        action = str(operation.get("action") or "set").strip()
        # Reuse the single-operation validator without mutating a listing.
        source = listings_by_id[listing_ids[0]]
        probe = Listing(
            id=source.id,
            user_id=source.user_id,
            listing_price=source.listing_price,
            title=source.title,
            description=source.description,
            category_suggestion=source.category_suggestion,
            condition=source.condition,
            marketplace_data=deepcopy(source.marketplace_data or {}),
            source_metadata=deepcopy(source.source_metadata or {}),
            canonical_description=getattr(source, "canonical_description", None),
            marketplace_descriptions=deepcopy(getattr(source, "marketplace_descriptions", None) or {}),
        )
        apply_marketplace_operation(probe, marketplaces=[str(value) for value in markets], field=field, action=action, value=operation.get("value"))
        # The probe mutation above is rolled back by restoring its original
        # dictionaries/attributes; validation must remain side-effect free.
        normalized.append({"listing_ids": sorted(set(listing_ids)), "marketplaces": [str(value) for value in markets], "field": field, "action": action, "value": operation.get("value")})
    return normalized


def apply_marketplace_operation_plan(
    operations: list[dict[str, Any]],
    listings_by_id: dict[int, Listing],
    *,
    preview_only: bool = False,
) -> dict[str, Any]:
    """Preview or apply a compound mutation plan atomically at the service layer."""
    # Validate without touching live objects by using shallow copies carrying
    # independent mutable JSON fields.
    probes = {
        listing_id: Listing(
            id=listing.id,
            user_id=listing.user_id,
            listing_price=listing.listing_price,
            title=listing.title,
            description=listing.description,
            category_suggestion=listing.category_suggestion,
            condition=listing.condition,
            marketplace_data=deepcopy(listing.marketplace_data or {}),
            source_metadata=deepcopy(listing.source_metadata or {}),
            canonical_description=getattr(listing, "canonical_description", None),
            marketplace_descriptions=deepcopy(getattr(listing, "marketplace_descriptions", None) or {}),
        )
        for listing_id, listing in listings_by_id.items()
    }
    normalized = validate_marketplace_operation_plan(operations, probes)
    changes: list[dict[str, Any]] = []
    for operation in normalized:
        for listing_id in operation["listing_ids"]:
            target = probes[listing_id]
            result = apply_marketplace_operation(target, marketplaces=operation["marketplaces"], field=operation["field"], action=operation["action"], value=operation.get("value"))
            for change in result["changed"]:
                changes.append({"listing_id": listing_id, **change})
    if not preview_only:
        for listing_id, probe in probes.items():
            original = listings_by_id[listing_id]
            for attr in ("listing_price", "title", "description", "canonical_description", "marketplace_descriptions", "category_suggestion", "condition", "marketplace_data", "source_metadata"):
                setattr(original, attr, getattr(probe, attr))
    return {"preview": bool(preview_only), "operations": normalized, "changes": changes}


def apply_marketplace_operation(
    listing: Listing,
    *,
    marketplaces: list[str],
    field: str,
    action: str = "set",
    value: Any = None,
) -> dict[str, Any]:
    """Apply exactly one field operation to exactly the requested markets.

    Canonical fields are changed only when ``marketplaces`` contains
    ``canonical``. Destination overrides live in ``marketplace_data`` and are
    recorded with operator provenance for later reconciliation.
    """
    targets = [str(m).strip().lower() for m in marketplaces if str(m).strip()]
    unknown = sorted(set(targets) - SUPPORTED_MARKETS - {"canonical", "all"})
    if unknown:
        raise ValueError(f"Unsupported marketplace target(s): {', '.join(unknown)}")
    if "all" in targets:
        targets = sorted(SUPPORTED_MARKETS)
    if not targets:
        raise ValueError("At least one marketplace target is required")
    if field not in {"price", "shipping", "title", "description", "category", "condition", "item_specifics", "listing"}:
        raise ValueError(f"Unsupported mutation field: {field}")
    if action not in {"set", "percentage_change", "clear", "end", "regenerate"}:
        raise ValueError(f"Unsupported mutation action: {action}")
    if action == "regenerate" and field != "description":
        raise ValueError("Regenerate currently targets descriptions only")
    if action == "end" and field != "listing":
        raise ValueError("The end action targets the listing field")

    data = dict(listing.marketplace_data or {})
    overrides = dict(data.get("marketplace_overrides") or {})
    descriptions = dict(getattr(listing, "marketplace_descriptions", None) or {})
    description_provenance = dict(data.get("marketplace_description_provenance") or {})
    changed: list[dict[str, Any]] = []

    def calculate(previous: Any) -> Any:
        if action == "regenerate":
            return previous
        if action == "end":
            return "end"
        if action == "clear":
            return None
        if action == "percentage_change":
            try:
                return round(float(previous) * (1 + float(value) / 100), 2)
            except (TypeError, ValueError):
                raise ValueError("Percentage changes require an existing numeric value")
        return value

    for target in targets:
        if target == "canonical":
            if field == "listing":
                raise ValueError("Listing end actions require a marketplace target")
            attr = {"price": "listing_price", "title": "title", "description": "description", "category": "category_suggestion", "condition": "condition"}.get(field)
            if attr is None:
                raise ValueError(f"Canonical mutation does not support {field}")
            previous = getattr(listing, attr, None)
            updated = calculate(previous)
            setattr(listing, attr, updated)
            # ``canonical_description`` is the authoritative rich master
            # copy. Keep the legacy column synchronized for older consumers,
            # but never let a destination override rewrite either canonical
            # field.
            if field == "description":
                listing.canonical_description = updated
            listing.source_metadata = mark_field_provenance(
                listing.source_metadata,
                field=field,
                provenance="human_operator",
                lock=True,
            )
            changed.append({"marketplace": "canonical", "field": field, "before": previous, "after": updated})
            continue
        market = dict(overrides.get(target) or {})
        if field == "listing":
            previous = market.get("status", "active")
            updated = calculate(previous)
            market["status"] = updated
            market.setdefault("provenance", {})["status"] = "operator_edited"
            overrides[target] = market
            changed.append({"marketplace": target, "field": field, "before": previous, "after": updated})
            continue
        if action == "regenerate" and field == "description":
            requests = list(data.get("description_regeneration_requests") or [])
            requests.append({"marketplace": target, "requested_by": "operator", "requested_at": datetime.now(UTC).isoformat(), "status": "QUEUED"})
            data["description_regeneration_requests"] = requests[-50:]
            changed.append({"marketplace": target, "field": field, "action": action, "before": market.get(field), "after": "queued"})
            continue
        previous = market.get(field)
        if field == "description" and target in descriptions:
            previous = descriptions.get(target)
        if previous is None and field == "price":
            previous = listing.listing_price
        updated = calculate(previous)
        market[field] = updated
        market.setdefault("provenance", {})[field] = "operator_edited"
        if field == "description":
            descriptions[target] = updated
            description_provenance[target] = "operator_edited"
        overrides[target] = market
        changed.append({"marketplace": target, "field": field, "before": previous, "after": updated})

    data["marketplace_overrides"] = overrides
    if descriptions:
        listing.marketplace_descriptions = descriptions
    if description_provenance:
        data["marketplace_description_provenance"] = description_provenance
    listing.marketplace_data = data
    return {"changed": changed, "untouched_markets": sorted(SUPPORTED_MARKETS - set(targets))}
