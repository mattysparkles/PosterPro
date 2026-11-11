"""Tenant shipping-policy normalization and calculated-rate evidence checks."""

from __future__ import annotations

from typing import Any
from datetime import UTC, datetime


DEFAULT_SHIPPING_POLICY: dict[str, Any] = {
    "shipping_policy_enabled": True,
    "shipping_price_threshold": 25.0,
    "shipping_under_threshold_mode": "buyer_pays_shipping",
    "shipping_at_or_above_threshold_mode": "free_shipping",
    "currency": "USD",
    "preferred_carrier": "USPS",
    "preferred_service": "USPSGroundAdvantage",
    "buyer_paid_rate_type": "flat",
    "minimum_estimated_net_proceeds": None,
    "minimum_gross_margin": None,
    "promotion_treatment": "include_known_costs",
    "negotiated_offer_treatment": "recheck_before_acceptance",
}

VERIFIED_PARCEL_MEASUREMENT_SOURCE = "operator_measured_packed_parcel"

# Verified existing EBAY_US account policies. The free policy is intentionally
# preserved; only unpublished offers select between these references by price.
EBAY_UNDER_25_FULFILLMENT_POLICY_ID = "266212013013"
EBAY_FREE_SHIPPING_FULFILLMENT_POLICY_ID = "260642879013"


def ebay_fulfillment_policy_for_price(price: Any) -> str | None:
    """Return the account's verified two-tier policy, or None without a valid ask."""
    try:
        amount = float(price)
    except (TypeError, ValueError, OverflowError):
        return None
    if amount <= 0 or amount == float("inf") or amount != amount:
        return None
    return EBAY_UNDER_25_FULFILLMENT_POLICY_ID if amount < 25 else EBAY_FREE_SHIPPING_FULFILLMENT_POLICY_ID


def normalize_shipping_policy(policy: dict | None) -> dict[str, Any]:
    raw = dict(policy) if isinstance(policy, dict) else {}
    # User workflow preferences predate this service and use explicit
    # `shipping_` names.  Accept them here so every caller evaluates the same
    # per-operator policy instead of silently reverting to a global default.
    aliases = {
        "shipping_currency": "currency",
        "preferred_shipping_carrier": "preferred_carrier",
        "preferred_shipping_service": "preferred_service",
    }
    for source, target in aliases.items():
        if target not in raw and source in raw:
            raw[target] = raw[source]
    result = dict(DEFAULT_SHIPPING_POLICY)
    result.update({key: raw[key] for key in result if key in raw and raw[key] is not None})
    try:
        result["shipping_price_threshold"] = max(0.0, float(result["shipping_price_threshold"]))
    except (TypeError, ValueError):
        result["shipping_price_threshold"] = DEFAULT_SHIPPING_POLICY["shipping_price_threshold"]
    for key, default, allowed in (
        ("shipping_under_threshold_mode", "buyer_pays_shipping", {"buyer_pays_shipping", "free_shipping"}),
        ("shipping_at_or_above_threshold_mode", "preserve_existing", {"buyer_pays_shipping", "free_shipping", "preserve_existing"}),
    ):
        result[key] = str(result[key]).strip().lower()
        if result[key] not in allowed:
            result[key] = default
    result["shipping_policy_enabled"] = bool(result["shipping_policy_enabled"])
    result["currency"] = str(result["currency"] or "USD").upper()[:8]
    result["preferred_carrier"] = str(result["preferred_carrier"] or "USPS").strip()
    result["preferred_service"] = str(result["preferred_service"] or "USPSGroundAdvantage").strip()
    result["buyer_paid_rate_type"] = str(result["buyer_paid_rate_type"] or "calculated").strip().lower()
    if result["buyer_paid_rate_type"] not in {"calculated", "flat"}:
        result["buyer_paid_rate_type"] = "calculated"
    return result


def measured_parcel_readiness(profile: dict | None) -> dict[str, Any]:
    """Calculated shipping is allowed only from measured, eligible parcel data."""
    profile = profile if isinstance(profile, dict) else {}
    dimensions = profile.get("package_dimensions") if isinstance(profile.get("package_dimensions"), dict) else {}
    missing = []
    if not _positive(profile.get("package_weight")):
        missing.append("package_weight")
    for field in ("length", "width", "height"):
        if not _positive(dimensions.get(field)):
            missing.append(f"package_dimensions.{field}")
    estimated_fields = estimated_parcel_fields(profile)
    unverified_fields = unverified_parcel_fields(profile)
    restrictions = [field for field in ("hazmat", "oversize", "local_pickup_recommended") if profile.get(field)]
    estimated = bool(estimated_fields)
    return {"ready": not missing and not estimated and not unverified_fields and not restrictions, "missing_fields": missing, "estimated_measurements": estimated, "estimated_fields": estimated_fields, "unverified_measurement_fields": unverified_fields, "eligibility_restrictions": restrictions}


def estimated_parcel_fields(profile: dict | None) -> list[str]:
    """Return package fields whose values are estimates/defaults, not measurements."""
    profile = profile if isinstance(profile, dict) else {}
    dimensions = profile.get("package_dimensions") if isinstance(profile.get("package_dimensions"), dict) else {}
    known = {"package_weight", "package_dimensions"}
    fields = {
        str(field).strip()
        for field in (profile.get("estimated_fields") or [])
        if str(field).strip() in known
    } if isinstance(profile.get("estimated_fields"), list) else set()
    provenance = profile.get("provenance") if isinstance(profile.get("provenance"), dict) else {}
    for field in known:
        source = str(provenance.get(field) or "").strip().lower()
        if source and any(marker in source for marker in ("estimate", "approx", "default", "guess", "inferred")):
            fields.add(field)
    weight_text = str(profile.get("package_weight") or "").lower()
    if any(marker in weight_text for marker in ("estimate", "approx", "guess", "inferred")):
        fields.add("package_weight")
    # Legacy rows often had only a global estimated flag. Do not trust either
    # numeric package field until their provenance is explicitly measured.
    if profile.get("estimated") and not fields:
        fields.update(field for field in known if (profile.get(field) if field == "package_weight" else dimensions))
    return sorted(fields)


def unverified_parcel_fields(profile: dict | None) -> list[str]:
    """Find populated parcel measurements without explicit packed-item confirmation."""
    profile = profile if isinstance(profile, dict) else {}
    dimensions = profile.get("package_dimensions") if isinstance(profile.get("package_dimensions"), dict) else {}
    provenance = profile.get("provenance") if isinstance(profile.get("provenance"), dict) else {}
    populated = {
        "package_weight": _positive(profile.get("package_weight")),
        "package_dimensions": all(_positive(dimensions.get(key)) for key in ("length", "width", "height")),
    }
    return sorted(
        field for field, present in populated.items()
        if present and str(provenance.get(field) or "").strip().lower() != VERIFIED_PARCEL_MEASUREMENT_SOURCE
    )


def _positive(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and float(value) > 0
    except (TypeError, ValueError):
        return False


def listing_sale_price(listing: Any) -> float | None:
    """Return only a positive intended asking price, never ETV/acquisition value."""
    for field in ("listing_price", "suggested_price", "buy_it_now_price"):
        value = getattr(listing, field, None)
        try:
            amount = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if amount > 0:
            return round(amount, 2)
    return None


def has_approved_free_shipping_exception(listing: Any) -> bool:
    """Recognize only explicit, reasoned operator exceptions."""
    data = getattr(listing, "marketplace_data", None)
    data = data if isinstance(data, dict) else {}
    candidates = [data.get("shipping_policy_exception"), (data.get("shipping") or {}).get("shipping_policy_exception") if isinstance(data.get("shipping"), dict) else None]
    return any(
        isinstance(item, dict)
        and item.get("approved") is True
        and bool(str(item.get("reason") or "").strip())
        and bool(item.get("approved_by"))
        for item in candidates
    )


def repair_unpublished_under_threshold_shipping(db, *, apply: bool = False) -> dict[str, Any]:
    """Idempotently repair only unpublished, under-$25 seller-paid-free drafts."""
    from collections import Counter
    from app.models.models import Listing

    rows = db.query(Listing).all()
    by_source: Counter[str] = Counter()
    candidate_ids: list[int] = []
    published: Counter[str] = Counter()
    remote_identity: Counter[str] = Counter()
    inactive: Counter[str] = Counter()
    exceptions: Counter[str] = Counter()
    invalid_price: Counter[str] = Counter()
    changed = 0
    for listing in rows:
        source = str(listing.source_type or "unknown")
        data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        shipping = data.get("shipping") if isinstance(data.get("shipping"), dict) else {}
        profile = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
        effective_shipping = {**profile, **shipping}
        if not bool(effective_shipping.get("free_shipping")):
            continue
        price = listing_sale_price(listing)
        if price is not None and price >= 25:
            continue
        if has_approved_free_shipping_exception(listing):
            exceptions[source] += 1
            continue
        remote_rows = list(getattr(listing, "marketplace_listings", None) or [])
        status = str(getattr(listing.status, "value", listing.status) or "").lower()
        ebay_state = str(getattr(listing.ebay_publish_status, "value", listing.ebay_publish_status) or "").lower()
        if status in {"sold", "archived", "cancelled", "canceled"}:
            inactive[source] += 1
            continue
        remotely_published = bool(
            status in {"posted", "published", "active"}
            or ebay_state in {"posted", "published"}
            or any(str(getattr(item.status, "value", item.status) or "").lower() in {"published", "active"} for item in remote_rows)
        )
        if remotely_published:
            published[source] += 1
            continue
        if listing.ebay_listing_id or any(getattr(item, "marketplace_listing_id", None) for item in remote_rows):
            remote_identity[source] += 1
            continue
        # Do not revise records whose remote outcome is pending/unknown.
        if ebay_state in {"posting", "queued", "unknown", "remote_state_unknown"} or any(
            str(getattr(item.status, "value", item.status) or "").lower() in {"pending", "publishing", "unknown", "remote_state_unknown"}
            for item in remote_rows
        ):
            continue
        by_source[source] += 1
        if price is None:
            invalid_price[source] += 1
        candidate_ids.append(int(listing.id))
        if not apply:
            continue
        parcel = measured_parcel_readiness(profile)
        can_select_calculated_service = price is not None and parcel["ready"]
        shipping_mode = "calculated" if can_select_calculated_service else "setup_required"
        market = dict(data)
        shipping = dict(shipping)
        shipping.update({
            "mode": shipping_mode,
            "shipping_charge_mode": shipping_mode,
            "buyer_pays_shipping": price is not None,
            "free_shipping": False,
            "shipping_policy_threshold": 25.0,
            "shipping_policy_enforced_at": datetime.now(UTC).isoformat(),
            "shipping_setup_required": not can_select_calculated_service,
        })
        if can_select_calculated_service:
            shipping.update({"domestic_service": "USPSGroundAdvantage", "shipping_service": "USPSGroundAdvantage"})
        else:
            # Keep any historical service value for audit, but do not imply it
            # is selectable until packed parcel measurements are verified.
            shipping["requested_domestic_service"] = "USPSGroundAdvantage"
            shipping.pop("shipping_service", None)
        market["shipping"] = shipping
        listing.marketplace_data = market
        if isinstance(profile, dict):
            profile = {**profile, "mode": shipping_mode, "shipping_charge_mode": shipping_mode, "buyer_pays_shipping": price is not None, "free_shipping": False, "shipping_setup_required": not can_select_calculated_service}
            if can_select_calculated_service:
                profile["domestic_service"] = "USPSGroundAdvantage"
            else:
                profile["requested_domestic_service"] = "USPSGroundAdvantage"
                profile.pop("shipping_service", None)
            listing.shipping_profile = profile
        db.add(listing)
        changed += 1
    if apply and changed:
        db.commit()
    elif apply:
        db.rollback()
    else:
        db.rollback()
    return {
        "mode": "applied" if apply else "dry_run",
        "unpublished_candidates_by_source": dict(by_source),
        "invalid_or_missing_price_candidates_by_source": dict(invalid_price),
        "locally_marked_published_violations_approval_required": dict(published),
        "remote_identity_requires_state_reconciliation_by_source": dict(remote_identity),
        "inactive_historical_records_by_source": dict(inactive),
        "approved_exceptions_by_source": dict(exceptions),
        "candidate_count": len(candidate_ids),
        "corrected_count": changed,
        "candidate_listing_ids": [] if not apply else candidate_ids,
    }
