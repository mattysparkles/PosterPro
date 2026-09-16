"""Field- and marketplace-scoped listing mutations.

All callers (UI, bulk workflows, and the agent layer) can use this small
contract without accidentally overwriting destinations that were not targeted.
"""
from __future__ import annotations

from typing import Any

from app.models.models import Listing


SUPPORTED_MARKETS = {"ebay", "facebook", "mercari", "poshmark", "vinted", "etsy", "offerup"}


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
    if field not in {"price", "shipping", "title", "description", "category", "condition", "item_specifics"}:
        raise ValueError(f"Unsupported mutation field: {field}")
    if action not in {"set", "percentage_change", "clear"}:
        raise ValueError(f"Unsupported mutation action: {action}")

    data = dict(listing.marketplace_data or {})
    overrides = dict(data.get("marketplace_overrides") or {})
    changed: list[dict[str, Any]] = []

    def calculate(previous: Any) -> Any:
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
            attr = {"price": "listing_price", "title": "title", "description": "description", "category": "category_suggestion", "condition": "condition"}.get(field)
            if attr is None:
                raise ValueError(f"Canonical mutation does not support {field}")
            previous = getattr(listing, attr, None)
            updated = calculate(previous)
            setattr(listing, attr, updated)
            changed.append({"marketplace": "canonical", "field": field, "before": previous, "after": updated})
            continue
        market = dict(overrides.get(target) or {})
        previous = market.get(field)
        if previous is None and field == "price":
            previous = listing.listing_price
        updated = calculate(previous)
        market[field] = updated
        market.setdefault("provenance", {})[field] = "operator_edited"
        overrides[target] = market
        changed.append({"marketplace": target, "field": field, "before": previous, "after": updated})

    data["marketplace_overrides"] = overrides
    listing.marketplace_data = data
    return {"changed": changed, "untouched_markets": sorted(SUPPORTED_MARKETS - set(targets))}
