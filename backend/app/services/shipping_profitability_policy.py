"""Tenant shipping-policy normalization and calculated-rate evidence checks."""

from __future__ import annotations

from typing import Any


DEFAULT_SHIPPING_POLICY: dict[str, Any] = {
    "shipping_policy_enabled": True,
    "shipping_price_threshold": 25.0,
    "shipping_under_threshold_mode": "buyer_pays_shipping",
    # The under-threshold safeguard must not silently convert every other
    # listing to free shipping.  Existing explicit listing/operator settings
    # are preserved; a new listing without one is surfaced for setup.
    "shipping_at_or_above_threshold_mode": "preserve_existing",
    "currency": "USD",
    "preferred_carrier": "USPS",
    "preferred_service": "USPSGroundAdvantage",
    "buyer_paid_rate_type": "calculated",
    "minimum_estimated_net_proceeds": None,
    "minimum_gross_margin": None,
    "promotion_treatment": "include_known_costs",
    "negotiated_offer_treatment": "recheck_before_acceptance",
}

VERIFIED_PARCEL_MEASUREMENT_SOURCE = "operator_measured_packed_parcel"


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
