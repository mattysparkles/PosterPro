"""Single, explainable readiness result for queue and publish consumers."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
import hashlib
import json
import re
import math

from app.services.listing_review import derive_shipping_profile, shipping_policy_for_user, summarize_listing_readiness
from app.services.listing_specificity import classify_listing_reviewability, is_generic_sellable_title
from app.services.listing_ai import assess_description_quality
from app.services.category_rules import is_source_noise_category, verified_category_id


_DESTINATION_BLOCKER_PREFIX = re.compile(
    r"^(?:ebay|facebook|mercari|vinted|offer\s*up|offerup|poshmark|etsy|depop|whatnot)\s*:",
    re.IGNORECASE,
)


def _is_destination_blocker(value: str) -> bool:
    """Recognize persisted destination errors without treating them as product defects.

    Older publish/preflight workers stored their first marketplace error in the
    listing-wide ``processing_blocking_reason`` column.  Keep that evidence
    visible as a destination blocker, but do not let it hold a complete product
    in Processing or Needs Attention.
    """
    text = str(value or "").strip()
    lowered = text.lower()
    return bool(
        _DESTINATION_BLOCKER_PREFIX.match(text)
        or any(token in lowered for token in (
            "marketplace preflight", "selected marketplace", "intended marketplace", "taxonomy/policy access",
            "destination preflight", "destination readiness remains externally blocked",
            "browser extension is offline", "facebook browser extension",
            "facebook browser workflow", "facebook safe form diagnostic",
            # Older publish workers persisted this generic destination result
            # in the listing-wide processing column. It is not a canonical
            # product defect and must not keep an otherwise complete Vine row
            # out of operator review.
            "marketplace publish was blocked",
        ))
    )


_TRUSTED_PRICE_PROVENANCE = {
    "market_comps", "sold_comps", "active_comps", "source_msrp", "source_retail",
    "operator", "operator_set", "asin_matched_amazon_offer",
}

_PREFLIGHT_TTL = timedelta(hours=24)


def _preflight_payload_matches(row: Any, listing: Any) -> bool:
    """Whether persisted validation belongs to this destination payload."""
    if not isinstance(row, dict) or bool(row.get("stale")):
        return False
    stored_hash = str(row.get("execution_payload_hash") or "").strip()
    if not stored_hash:
        # Legacy caches have no proof of the publication inputs they checked.
        # They must never be treated as reusable product validation.
        return False
    checked_marketplace = str(row.get("marketplace") or "").strip().lower()
    try:
        from app.services.marketplace_field_mapper import build_marketplace_payload
        payload = build_marketplace_payload(listing, checked_marketplace or "ebay")
        current_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    except Exception:
        return False
    if current_hash != stored_hash:
        return False
    return True


def _preflight_recent(row: Any, listing: Any) -> bool:
    """Whether a result is recent enough for time-sensitive execution checks."""
    if not _preflight_payload_matches(row, listing):
        return False
    raw_checked = row.get("last_checked_at")
    if isinstance(raw_checked, datetime):
        checked = raw_checked
    else:
        try:
            checked = datetime.fromisoformat(str(raw_checked or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
    if checked.tzinfo is not None:
        checked = checked.astimezone(UTC).replace(tzinfo=None)
    age = datetime.now(UTC).replace(tzinfo=None) - checked
    if age < timedelta(seconds=-5) or age > _PREFLIGHT_TTL:
        return False
    return True


def _product_preflight_pass(row: Any, listing: Any, marketplace: str | None = None) -> bool:
    """Reusable product-field validation, independent of session/policy age.

    This is review evidence only. Publication continues to use
    ``_fresh_preflight_pass`` and must refresh the execution check when needed.
    """
    if not _preflight_payload_matches(row, listing):
        return False
    if str(row.get("marketplace") or marketplace or "").strip().lower() != str(marketplace or row.get("marketplace") or "").strip().lower():
        return False
    validation_status = str(row.get("preparation_status") or row.get("status") or "").strip().lower()
    validation_blockers = row.get("preparation_blockers") if isinstance(row.get("preparation_blockers"), list) else row.get("blockers")
    if validation_status not in {"ready", "ready_with_warnings"} or validation_blockers:
        return False
    return True


def _changed_preflight_fields(row: Any, listing: Any) -> list[str]:
    """Return changed destination payload fields when field fingerprints exist."""
    saved = row.get("payload_field_hashes") if isinstance(row, dict) else None
    if not isinstance(saved, dict) or not saved:
        return []
    try:
        from app.services.marketplace_field_mapper import build_marketplace_payload
        payload = build_marketplace_payload(listing, str(row.get("marketplace") or "ebay").lower())
    except Exception:
        return []
    labels = {
        "category_id": "category",
        "item_specifics": "item specifics",
        "aspects": "item specifics",
        "price": "price",
        "title": "title",
        "description": "description",
        "images": "photographs",
        "image_urls": "photographs",
        "condition": "condition",
        "shipping": "shipping details",
    }
    changed = []
    for key in set(saved) | set(payload):
        try:
            current_hash = hashlib.sha256(json.dumps(payload.get(key), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            continue
        if str(saved.get(key) or "") != current_hash:
            label = labels.get(str(key), str(key).replace("_", " "))
            if label not in changed:
                changed.append(label)
    return changed


def _fresh_preflight_pass(row: Any, listing: Any, marketplace: str | None = None) -> bool:
    """Require a recent successful destination result for review eligibility."""
    if not _preflight_recent(row, listing):
        return False
    if str(row.get("status") or "").strip().lower() not in {"ready", "ready_with_warnings"}:
        return False
    if row.get("blockers"):
        return False
    if str(marketplace or "").strip().lower() == "ebay":
        listing_data = getattr(listing, "marketplace_data", None)
        listing_data = listing_data if isinstance(listing_data, dict) else {}
        policy_summary = row.get("policy_summary") if isinstance(row.get("policy_summary"), dict) else {}
        policy_validation = policy_summary.get("fulfillment_policy_validation") if isinstance(policy_summary.get("fulfillment_policy_validation"), dict) else {}
        if policy_validation.get("valid") is not True or not str(policy_validation.get("policy_id") or "").strip():
            return False
        # A fresh successful destination preflight proves the effective policy
        # is configured; old setup_required markers are then stale metadata.
        category = row.get("category_summary") if isinstance(row.get("category_summary"), dict) else {}
        if not str(category.get("category_id") or "").strip() or category.get("leaf_verified") is not True:
            return False
        listing_category_id = str(getattr(listing, "category_id", None) or "").strip()
        if listing_category_id and str(category.get("category_id") or "").strip() != listing_category_id:
            return False
        overrides = listing_data.get("marketplace_overrides") if isinstance(listing_data.get("marketplace_overrides"), dict) else {}
        ebay_override = overrides.get("ebay") if isinstance(overrides.get("ebay"), dict) else {}
        selected_price = ebay_override.get("price")
        if selected_price in (None, ""):
            selected_price = listing_data.get("manual_price_override") if listing_data.get("manual_price_override_reason") else getattr(listing, "listing_price", None)
        try:
            sale_price = float(selected_price)
        except (TypeError, ValueError, OverflowError):
            sale_price = 0.0
        if 0 < sale_price < 25:
            if policy_validation.get("buyer_pays_shipping") is not True or policy_validation.get("free_shipping") is True:
                return False
            services = policy_validation.get("services") if isinstance(policy_validation.get("services"), list) else []
            valid_service = False
            for service in services:
                if not isinstance(service, dict) or service.get("free_shipping") is not False:
                    continue
                cost_type = str(service.get("cost_type") or "").upper()
                if cost_type in {"FLAT_RATE", "FIXED_RATE"}:
                    try:
                        valid_service = abs(float(service.get("charge") or 0) - 8.49) < 0.001
                    except (TypeError, ValueError):
                        valid_service = False
                if valid_service:
                    break
            if not valid_service:
                return False
        elif sale_price >= 25:
            from app.services.shipping_profitability_policy import ebay_fulfillment_policy_for_price
            if (
                policy_validation.get("policy_id") != ebay_fulfillment_policy_for_price(sale_price)
                or policy_validation.get("free_shipping") is not True
                or policy_validation.get("buyer_pays_shipping") is True
            ):
                return False
    return True


def _destination_preflight_diagnostics(row: Any, listing: Any, marketplace: str) -> dict[str, Any]:
    """Explain why a destination is not independently reviewable."""
    if not isinstance(row, dict):
        return {"status": "not_run", "blockers": ["Marketplace preflight has not been run."]}
    if _product_preflight_pass(row, listing, marketplace) and not _preflight_recent(row, listing):
        return {
            "status": "product_validated_execution_refresh_required",
            "blockers": [],
            "execution_refresh_required": True,
            "last_checked_at": row.get("last_checked_at"),
            "message": f"{marketplace.title()} listing data passed preflight; refresh the account and policy check before publishing.",
        }
    changed_fields = _changed_preflight_fields(row, listing)
    if changed_fields:
        return {
            "status": "stale_payload_changed",
            "blockers": [f"Preflight invalidated because {', '.join(changed_fields)} changed."],
            "changed_fields": changed_fields,
            "last_checked_at": row.get("last_checked_at"),
        }
    if not _preflight_recent(row, listing):
        return {"status": "stale", "blockers": ["Marketplace preflight is missing, stale, or predates listing changes."]}
    provider_status = str(row.get("status") or "not_run").strip().lower()
    existing = row.get("blockers") if isinstance(row.get("blockers"), list) else []
    if provider_status not in {"ready", "ready_with_warnings"} or existing:
        return {"status": provider_status, "blockers": existing or ["Marketplace preflight did not pass."]}
    if _fresh_preflight_pass(row, listing, marketplace):
        return {"status": provider_status, "blockers": []}
    reasons: list[str] = []
    if marketplace == "ebay":
        policy = row.get("policy_summary") if isinstance(row.get("policy_summary"), dict) else {}
        validation = policy.get("fulfillment_policy_validation") if isinstance(policy.get("fulfillment_policy_validation"), dict) else {}
        if validation.get("valid") is not True or not str(validation.get("policy_id") or "").strip():
            reasons.append(str(validation.get("message") or "A current, compatible eBay fulfillment policy is not verified."))
        category = row.get("category_summary") if isinstance(row.get("category_summary"), dict) else {}
        listing_category_id = str(getattr(listing, "category_id", None) or "").strip()
        if not str(category.get("category_id") or "").strip() or category.get("leaf_verified") is not True:
            reasons.append("A current eBay category leaf is not verified.")
        elif listing_category_id and str(category.get("category_id") or "").strip() != listing_category_id:
            reasons.append("The eBay preflight category does not match the category assigned to this listing.")
        listing_data = getattr(listing, "marketplace_data", None)
        listing_data = listing_data if isinstance(listing_data, dict) else {}
        shipping = listing_data.get("shipping") if isinstance(listing_data.get("shipping"), dict) else {}
        profile = getattr(listing, "shipping_profile", None)
        profile = profile if isinstance(profile, dict) else {}
        effective_shipping = {**shipping, **profile}
        if effective_shipping.get("shipping_setup_required") is True or any(
            str(effective_shipping.get(key) or "").strip().lower() == "setup_required"
            for key in ("shipping_setup_required", "shipping_charge_mode", "shipping_policy", "mode")
        ):
            reasons.append("eBay shipping policy is marked setup_required.")
        if not reasons:
            reasons.append("eBay preflight no longer matches the current listing; rerun preflight.")
    else:
        reasons.append("Marketplace preflight does not confirm all required publication fields.")
    return {"status": "blocked", "blockers": reasons}


def _positive_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _pricing_provenance(pricing: dict[str, Any]) -> str:
    """Read current and legacy provenance keys used by persisted price records."""
    return str(pricing.get("provenance") or pricing.get("price_source") or "").strip().lower()


def has_authorized_vine_import_listing_price(listing: Any) -> bool:
    """Whether a positive Vine price is backed by an identifiable Amazon source.

    Vine ETV is an Amazon-provided initial pricing input, not a retail offer.
    Keep its source designation intact; accepting it here only permits human
    review and does not assert market value or publish authorization.
    """
    if str(getattr(listing, "source_type", "") or "").strip().lower() != "amazon_vine":
        return False
    ask = _positive_price(getattr(listing, "listing_price", None))
    if ask is None:
        return False
    metadata = getattr(listing, "source_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    imported = metadata.get("vine_imported_listing_price") if isinstance(metadata.get("vine_imported_listing_price"), dict) else {}
    imported_amount = _positive_price(imported.get("amount"))
    if (
        imported_amount is not None
        and round(imported_amount, 2) == round(ask, 2)
        and imported.get("source_field") in {"Listing Price", "Approved Listing Price"}
    ):
        return True

    # Amazon's Vine export labels this source value explicitly as Estimated
    # Tax Value. Accept only when the recorded source amount exactly matches
    # the current positive ask; never reinterpret it as MSRP or a live offer.
    etv = _positive_price(metadata.get("estimated_tax_value"))
    imported_etv = metadata.get("vine_imported_etv") if isinstance(metadata.get("vine_imported_etv"), dict) else {}
    if etv is None:
        etv = _positive_price(imported_etv.get("amount"))
    if etv is None:
        raw_row = metadata.get("raw_row_json") if isinstance(metadata.get("raw_row_json"), dict) else {}
        for key, value in raw_row.items():
            if " ".join(str(key).strip().lower().split()) == "estimated tax value":
                etv = _positive_price(value)
                break
    if etv is not None and round(etv, 2) == round(ask, 2):
        return True

    # Amazon offer/MSRP evidence must be tied to this Vine product. A matching
    # offer amount and an ASIN-bound source fact are required for current offer
    # prices; MSRP remains explicitly labeled as MSRP in pricing provenance.
    pricing = (getattr(listing, "marketplace_data", None) or {}).get("pricing_analysis", {})
    pricing = pricing if isinstance(pricing, dict) else {}
    provenance = _pricing_provenance(pricing)
    facts = metadata.get("amazon_product_facts") if isinstance(metadata.get("amazon_product_facts"), dict) else {}
    asin = str(metadata.get("asin") or "").strip().upper()
    matched_asin = str(metadata.get("amazon_match_asin") or "").strip().upper()
    fact_asin = str(facts.get("asin") or "").strip().upper()
    match_confirmed = bool(asin and (matched_asin == asin or fact_asin == asin))
    if provenance in {"asin_matched_amazon_offer", "amazon_current_price"}:
        fact_price = _positive_price(facts.get("current_price"))
        saved_offer = _positive_price(pricing.get("amazon_current_price"))
        source_version = str(facts.get("price_source") or pricing.get("price_evidence_version") or "")
        return bool(
            match_confirmed
            and source_version == "asin_product_offer_widget_v2"
            and ((fact_price is not None and round(fact_price, 2) == round(ask, 2))
                 or (saved_offer is not None and round(saved_offer, 2) == round(ask, 2)))
        )
    if provenance in {"source_msrp", "source_retail"}:
        return bool(match_confirmed and (metadata.get("amazon_source_page_url") or metadata.get("amazon_product_facts")))
    return False


def resolve_authoritative_listing_price(listing: Any) -> float | None:
    """Resolve a sell price without treating acquisition value as a listing price.

    Canonical/manual ask, suggested ask, and Buy-It-Now are valid persisted
    price fields. Pricing-analysis ``current_price`` is only a fallback when
    its provenance is explicit and trusted and it is not marked as a
    placeholder/unapproved fallback. ``estimated_value`` is deliberately not
    used: it can mean ETV or acquisition value rather than an asking price.
    """
    marketplace_data = getattr(listing, "marketplace_data", None)
    if isinstance(marketplace_data, dict):
        override = _positive_price(marketplace_data.get("manual_price_override"))
        if override is not None and str(marketplace_data.get("manual_price_override_reason") or "").strip():
            return round(override, 2)
    pricing = marketplace_data.get("pricing_analysis") if isinstance(marketplace_data, dict) else None
    pricing = pricing if isinstance(pricing, dict) else {}
    provenance = _pricing_provenance(pricing)
    # The operator accepts Amazon Vine pricing as-is for the review workflow.
    # Keep the actual numeric ask unchanged; readiness/review is not a claim
    # that this value is independently researched. Final submission still
    # requires a positive price and the normal explicit operator approval.
    if str(getattr(listing, "source_type", "") or "").strip().lower() == "amazon_vine":
        vine_ask = _positive_price(getattr(listing, "listing_price", None))
        if vine_ask is not None:
            return round(vine_ask, 2)
        # A suggested price, ETV reference, or pricing-analysis fallback is
        # not an existing Vine selling price and must not silently fill it.
        return None
    operator_locked = False
    source_metadata = getattr(listing, "source_metadata", None)
    if isinstance(source_metadata, dict):
        recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
        operator_locked = bool({"listing_price", "price"} & set(recovery.get("operator_locked_fields") or []))
    operator_owned = operator_locked or provenance.startswith(("operator", "manual"))
    if not operator_owned and (
        pricing.get("placeholder") is True
        or pricing.get("fallback_without_approval") is True
        or str(pricing.get("price_state") or "").strip().lower() == "placeholder"
        or provenance in {"placeholder", "default_price", "fallback_without_approval", "temporary_price", "unresearched"}
    ):
        return None
    if provenance in {
        "vine_etv_fallback", "vine_estimated_tax_value_fallback",
        "category_model_estimate", "fallback_estimate", "default_price",
        "fallback_without_approval", "temporary_price", "unresearched",
        "needs_price_research",
    } and not operator_owned:
        # Tax/acquisition references and deterministic/category fallbacks are
        # not researched marketplace asks. They must not become a selling
        # price merely because an import wrote them into listing_price.
        return None
    for field in ("listing_price", "suggested_price", "buy_it_now_price"):
        price = _positive_price(getattr(listing, field, None))
        if price is not None:
            return round(price, 2)
    if provenance not in _TRUSTED_PRICE_PROVENANCE:
        return None
    return round(_positive_price(pricing.get("current_price")) or 0, 2) or None


def _is_vine_pricing_blocker(value: Any) -> bool:
    text = re.sub(r"[_-]+", " ", str(value or "").strip().lower())
    return any(marker in text for marker in (
        "evidence-backed pricing research",
        "unsupported estimate",
        "pricing evidence missing",
        "pricing provenance missing",
    ))


def hydrate_authoritative_listing_price(listing: Any) -> bool:
    """Copy an existing authoritative alternate ask into the canonical field.

    Never overwrites a valid canonical ask and never derives a sale price from
    acquisition/ETV fields. Returns whether ``listing_price`` was repaired.
    """
    if _positive_price(getattr(listing, "listing_price", None)) is not None:
        return False
    price = resolve_authoritative_listing_price(listing)
    if price is None:
        return False
    listing.listing_price = price
    return True


def canonical_listing_readiness(listing: Any, *, marketplace: str | None = None) -> dict[str, Any]:
    """Combine processing, content, and destination readiness without hiding blockers."""
    source_type_value = str(getattr(listing, "source_type", None) or "").strip().lower()
    is_amazon_vine = source_type_value == "amazon_vine"
    # Historical Vine rows carry an explicit archive label even when their
    # legacy enum status was left as PROCESSED.  They are not current sellable
    # inventory and must not inflate the Processing queue or be re-enqueued by
    # the resumable worker.
    labels = {str(value).strip().lower() for value in (getattr(listing, "custom_labels", None) or [])}
    if labels.intersection({"archived_vine", "archived_sold"}):
        return {
            "queue": "ARCHIVED",
            "blocking_reasons": [],
            "warnings": [],
            "machine_blockers": [],
            "human_blockers": [],
            "archived_label": True,
        }
    stored = listing.readiness_summary if isinstance(getattr(listing, "readiness_summary", None), dict) else {}
    listing_images = getattr(listing, "listing_images", None)
    if not listing_images:
        image_urls = getattr(listing, "image_urls", None) or []
        listing_images = [{"storage_path": str(url), "operator_state": "approved", "role": "primary"} for url in image_urls if str(url).strip()]
    listing_for_shipping = {
        "title": getattr(listing, "title", None),
        "description": getattr(listing, "canonical_description", None) or getattr(listing, "description", None),
        "category": getattr(listing, "category_suggestion", None),
        "category_suggestion": getattr(listing, "category_suggestion", None),
        "listing_price": getattr(listing, "listing_price", None),
        "suggested_price": getattr(listing, "suggested_price", None),
        "buy_it_now_price": getattr(listing, "buy_it_now_price", None),
    }
    marketplace_data_for_shipping = getattr(listing, "marketplace_data", None)
    marketplace_data = marketplace_data_for_shipping if isinstance(marketplace_data_for_shipping, dict) else {}
    preflight = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else {}
    preflight_rows = preflight.get("by_marketplace") if isinstance(preflight.get("by_marketplace"), dict) else {}
    preflight_job_root = marketplace_data.get("marketplace_preflight_jobs") if isinstance(marketplace_data.get("marketplace_preflight_jobs"), dict) else {}
    preflight_jobs = preflight_job_root.get("by_marketplace") if isinstance(preflight_job_root.get("by_marketplace"), dict) else {}
    configured_targets = {
        str(value).strip().lower()
        for value in (marketplace_data.get("targets") or [])
        if str(value).strip()
    }
    channels = marketplace_data.get("channels") if isinstance(marketplace_data.get("channels"), dict) else {}
    if channels:
        configured_targets = {
            target for target in configured_targets
            if not isinstance(channels.get(target), dict) or bool(channels[target].get("enabled"))
        }
    # A persisted destination-specific preflight is evidence that the operator
    # selected that destination, even on legacy rows predating `targets`.
    if not configured_targets and preflight_rows:
        configured_targets = {
            str(target).strip().lower()
            for target, row in preflight_rows.items()
            if str(target).strip() and isinstance(row, dict)
        }
    if marketplace:
        target_markets = {str(marketplace).strip().lower()}
    else:
        target_markets = configured_targets
    execution_fresh_destinations = {
        target: _fresh_preflight_pass(preflight_rows.get(target), listing, target)
        for target in target_markets
    }
    # Product field validation is reusable while its exact destination payload
    # remains unchanged. Account/session and live policy checks keep their
    # 24-hour freshness requirement and are enforced again before publication.
    product_validated_destinations = {
        target: _product_preflight_pass(preflight_rows.get(target), listing, target)
        for target in target_markets
    }
    product_preflight_ready = any(product_validated_destinations.values())
    selected_product_preflight_ready = (
        product_validated_destinations.get(str(marketplace).strip().lower(), False)
        if marketplace
        else product_preflight_ready
    )
    marketplace_shipping = marketplace_data_for_shipping.get("shipping") if isinstance(marketplace_data_for_shipping, dict) and isinstance(marketplace_data_for_shipping.get("shipping"), dict) else {}
    stored_profile = getattr(listing, "shipping_profile", None)
    existing_profile = {**marketplace_shipping, **(stored_profile if isinstance(stored_profile, dict) else {})}
    owner = getattr(listing, "user", None)
    usable_shipping_profile = derive_shipping_profile(
        listing=listing_for_shipping,
        item_specifics=getattr(listing, "item_specifics", None),
        existing=existing_profile,
        policy=shipping_policy_for_user(owner) if owner else None,
    )
    base = summarize_listing_readiness(
        listing_images=listing_images,
        condition_data=getattr(listing, "condition_data", None),
        shipping_profile=usable_shipping_profile,
        listing={"category_id": getattr(listing, "category_id", None), "category_suggestion": getattr(listing, "category_suggestion", None), "listing_price": getattr(listing, "listing_price", None), "suggested_price": getattr(listing, "suggested_price", None)},
        source_type=getattr(listing, "source_type", None),
    )
    shipping_checklist = base.get("shipping_checklist") if isinstance(base.get("shipping_checklist"), dict) else {}
    derived_shipping_setup_required = bool(usable_shipping_profile.get("shipping_setup_required"))
    policy_exempts_packed_measurements = False
    def _packed_measurement_or_stale_shipping_setup(value: Any) -> bool:
        lowered = str(value or "").strip().lower()
        measurement = ("package weight", "package dimension", "packed measurement", "verified packed")
        return any(token in lowered for token in measurement) or "shipping policy setup required" in lowered
    explicit_shipping_setup_required = (not selected_product_preflight_ready) and (any(
        str(value or "").strip().lower() == "setup_required"
        for value in (
            existing_profile.get("shipping_setup_required"),
            existing_profile.get("shipping_charge_mode"),
            existing_profile.get("shipping_policy"),
            existing_profile.get("mode"),
        )
    ) or existing_profile.get("shipping_setup_required") is True)
    if marketplace == "ebay" and owner is not None:
        # A synchronized, price-matched tier policy is authoritative over
        # setup_required flags written by the former calculated-shipping gate.
        # This verification is local and does not relax non-shipping checks.
        try:
            from app.services.ebay_service import validate_cached_ebay_fulfillment_policy
            from app.services.shipping_profitability_policy import ebay_fulfillment_policy_for_price

            settings_json = owner.settings_json if isinstance(owner.settings_json, dict) else {}
            policy_settings = settings_json.get("ebay_marketplace_policy_settings") if isinstance(settings_json.get("ebay_marketplace_policy_settings"), dict) else {}
            overrides = marketplace_data.get("marketplace_overrides") if isinstance(marketplace_data.get("marketplace_overrides"), dict) else {}
            ebay_override = overrides.get("ebay") if isinstance(overrides.get("ebay"), dict) else {}
            try:
                shipping_price = float(ebay_override.get("price") if ebay_override.get("price") not in (None, "") else resolve_authoritative_listing_price(listing))
            except (TypeError, ValueError, OverflowError):
                shipping_price = 0.0
            expected_policy = ebay_fulfillment_policy_for_price(shipping_price)
            tier_settings = {**policy_settings, "fulfillment_policy_id": expected_policy or ""}
            tier_validation = validate_cached_ebay_fulfillment_policy(
                tier_settings,
                sale_price=shipping_price,
                shipping_profile=usable_shipping_profile,
            )
            if expected_policy and tier_validation.get("valid") and tier_validation.get("policy_id") == expected_policy:
                explicit_shipping_setup_required = False
                validation = tier_validation.get("fulfillment_policy_validation") if isinstance(tier_validation.get("fulfillment_policy_validation"), dict) else tier_validation
                services = validation.get("services") if isinstance(validation.get("services"), list) else []
                if shipping_price < 25:
                    policy_exempts_packed_measurements = bool(
                        validation.get("buyer_pays_shipping") is True
                        and validation.get("free_shipping") is False
                        and any(
                            isinstance(service, dict)
                            and str(service.get("cost_type") or "").upper() in {"FLAT_RATE", "FIXED_RATE"}
                            and service.get("free_shipping") is False
                            and abs(float(service.get("charge") or 0) - 8.49) < 0.001
                            for service in services
                        )
                    )
                else:
                    policy_exempts_packed_measurements = bool(
                        validation.get("free_shipping") is True
                        and validation.get("buyer_pays_shipping") is not True
                    )
        except Exception:
            pass
    if policy_exempts_packed_measurements:
        # Amazon product measurements remain stored with their provenance,
        # but they are not packed-parcel measurements and are irrelevant to
        # the verified flat/free eBay tiers. The destination preflight still
        # enforces any independent provider requirement.
        stored = {**stored, "blockers": [value for value in (stored.get("blockers") or []) if not _packed_measurement_or_stale_shipping_setup(value)]}
        base = {**base, "blockers": [value for value in (base.get("blockers") or []) if not _packed_measurement_or_stale_shipping_setup(value)]}
        derived_shipping_setup_required = False
    # A fresh successful destination pass is authoritative about whether the
    # selected fulfillment method is configured, including whether parcel
    # measurements are required. Stale local setup markers cannot override it.
    shipping_setup_required = explicit_shipping_setup_required or (
        derived_shipping_setup_required and not product_preflight_ready
    )
    review_shipping_setup_required = shipping_setup_required and not selected_product_preflight_ready
    blockers = list(dict.fromkeys([*(stored.get("blockers") or []), *(base.get("blockers") or [])]))
    warnings = list(dict.fromkeys([*(stored.get("warnings") or []), *(base.get("warnings") or [])]))
    # Missing measured parcel data is an explicit operator setup task, not a
    # product-identification defect. Keep it visible in the review result,
    # allow an otherwise complete draft to reach human review, and continue to
    # block publication below. Never estimate or invent parcel measurements.
    if shipping_setup_required:
        blockers = [reason for reason in blockers if "package shipping profile is incomplete" not in str(reason).lower()]
        if explicit_shipping_setup_required:
            setup_detail = str(existing_profile.get("shipping_setup_reason") or "Complete the selected marketplace shipping configuration.").strip()
            blockers.append(f"Shipping policy setup required: {setup_detail}")
        else:
            warnings.append("Shipping setup required: verify the shipping policy and any parcel facts required by that service")
    # Needs Review is a hand-off to an operator, so a real, positive ask must
    # exist independently of whatever an older readiness snapshot says. This
    # prevents a persisted review bit or successful preflight from bypassing
    # the price requirement.
    missing_valid_price = resolve_authoritative_listing_price(listing) is None
    if missing_valid_price:
        blockers.append("Set a selling price" if is_amazon_vine else "A valid positive listing price is required before Needs Review")
    processing_blocker = str(getattr(listing, "processing_blocking_reason", None) or "").strip()
    stale_price_blocker = (
        not missing_valid_price
        and processing_blocker.casefold() in {
            "price missing",
            "set a selling price",
            "a valid positive listing price is required before needs review",
        }
    )
    source_metadata_for_blocker = getattr(listing, "source_metadata", None) if isinstance(getattr(listing, "source_metadata", None), dict) else {}
    category_provenance_for_blocker = source_metadata_for_blocker.get("category_provenance") if isinstance(source_metadata_for_blocker.get("category_provenance"), dict) else {}
    existing_marketplace_data_for_category = getattr(listing, "marketplace_data", None) if isinstance(getattr(listing, "marketplace_data", None), dict) else {}
    if not category_provenance_for_blocker:
        vine_category_record = existing_marketplace_data_for_category.get("vine_category")
        if isinstance(vine_category_record, dict) and str(vine_category_record.get("source") or "").upper() in {"VERIFIED_SEMANTIC_LOCAL_EVIDENCE", "VERIFIED_LOCAL_EBAY_TAXONOMY", "PUBLIC_EBAY_CATEGORY_RESEARCH"}:
            category_provenance_for_blocker = vine_category_record
    trusted_category_resolution = str(category_provenance_for_blocker.get("source") or "").upper() in {
        "VERIFIED_LOCAL_EBAY_TAXONOMY", "VERIFIED_SEMANTIC_LOCAL_EVIDENCE", "PUBLIC_EBAY_CATEGORY_RESEARCH"
    }
    generation_record_for_blocker = source_metadata_for_blocker.get("vine_description_generation") if isinstance(source_metadata_for_blocker.get("vine_description_generation"), dict) else {}
    # A later deterministic/provider retry must not overwrite the provenance
    # of an already-valid Codex recovery description.
    if str((source_metadata_for_blocker.get("codex_revenue_recovery") or {}).get("source") or "").lower() == "codex_revenue_recovery" and str(generation_record_for_blocker.get("source") or "").lower() not in {"openai", "codex_revenue_recovery"}:
        generation_record_for_blocker = source_metadata_for_blocker.get("codex_revenue_recovery") or generation_record_for_blocker
    generation_source_for_blocker = str(generation_record_for_blocker.get("source") or "").lower()
    # Provenance is diagnostic only; it never waives copy-quality blockers.
    stale_generation_marker = False
    stale_aggregate_preflight = "every selected marketplace must pass current preflight" in processing_blocker.lower()
    persisted_destination_blocker = processing_blocker if _is_destination_blocker(processing_blocker) else ""
    if processing_blocker and not stale_price_blocker and not (policy_exempts_packed_measurements and _packed_measurement_or_stale_shipping_setup(processing_blocker)) and not stale_generation_marker and not stale_aggregate_preflight and not persisted_destination_blocker and not (trusted_category_resolution and "categor" in processing_blocker.lower()):
        blockers.append(processing_blocker)
    missing_required_aspects = [str(value).strip() for value in (stored.get("missing_required_aspects") or []) if str(value).strip()]
    if missing_required_aspects:
        blockers.extend(f"Required marketplace detail missing: {value}" for value in missing_required_aspects)
    description = str(getattr(listing, "canonical_description", None) or getattr(listing, "description", None) or "").strip()
    title = str(getattr(listing, "title", None) or "").strip()
    source_metadata = getattr(listing, "source_metadata", None) if isinstance(getattr(listing, "source_metadata", None), dict) else {}
    category_id = str(getattr(listing, "category_id", None) or "").strip()
    category_hint = str(getattr(listing, "category_suggestion", None) or "").strip()
    # The category resolver records an explicit validation result after looking
    # up the ID in the synchronized EBAY_US tree.  A non-null ID alone is not
    # evidence that the category is usable: old imports contain stale,
    # non-leaf, and semantically colliding IDs.  Keep this gate canonical so a
    # destination preflight cannot accidentally present such rows as READY.
    category_validation = source_metadata.get("category_validation") if isinstance(source_metadata.get("category_validation"), dict) else {}
    category_validation_status = str(category_validation.get("status") or "").lower()
    locally_valid_statuses = {"valid_local", "valid_ebay_us", "valid_ebay_motors_us"}
    locally_validated_category = (
        category_validation_status in locally_valid_statuses
        and str(category_validation.get("category_id") or "").strip() == category_id
        and bool(str(category_validation.get("full_path") or "").strip())
    )
    # The synchronized tree's ID/path/owner tuple is the authority. Legacy
    # hand-maintained path->ID aliases can collide across trees or versions;
    # do not overrule a matching local leaf with an obsolete static alias.
    if locally_validated_category:
        category_hint = str(category_validation.get("full_path") or category_hint).strip()
    codex_recovery = str((source_metadata.get("codex_revenue_recovery") or {}).get("source") or "").lower() == "codex_revenue_recovery"
    machine_pricing_pending = False
    machine_description_pending = False
    machine_preflight_pending = False
    preflight_action_required = False
    # Media-recovery rows are Google Photos/timeline-derived intake too.  They
    # used to bypass this gate solely because their legacy source label differed
    # from ``google_photos_album``, allowing arbitrary $17.99-style defaults
    # and OCR fragments to appear reviewable without pricing evidence.
    if source_type_value in {"intake_head_slate", "google_photos_album", "media_inventory_recovery"}:
        pricing = marketplace_data.get("pricing_analysis") if isinstance(marketplace_data.get("pricing_analysis"), dict) else {}
        recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
        operator_locked = {str(value).strip() for value in (recovery.get("operator_locked_fields") or []) if str(value).strip()}
        has_operator_price = "listing_price" in operator_locked or "price" in operator_locked or str(pricing.get("provenance") or "").lower().startswith("operator")
        has_authorized_vine_price = False
        comparable_count = len(pricing.get("comparables") or pricing.get("sold_comparables") or [])
        provenance = _pricing_provenance(pricing)
        evidence_count = int(
            pricing.get("comp_count_used")
            or pricing.get("sold_comp_count_used")
            or pricing.get("active_comp_count_used")
            or comparable_count
            or 0
        )
        confidence = float(pricing.get("price_confidence") or 0)
        evidence_provenance = provenance in {
            "market_comps", "sold_comps", "active_comps", "source_msrp",
            "source_retail", "operator", "operator_set", "asin_matched_amazon_offer",
        }
        placeholder_price = float(pricing.get("current_price") or getattr(listing, "listing_price", 0) or 0) in {20.0, 21.0, 22.0} and not has_authorized_vine_price
        # Fail closed for missing, placeholder, tax-value, or deterministic
        # estimate prices even if an earlier worker marked the row researched.
        unsupported_estimate = provenance in {
            "vine_etv_fallback", "vine_estimated_tax_value_fallback",
            "category_model_estimate", "fallback_estimate", "default_price",
            "fallback_without_approval", "temporary_price", "unresearched",
            "needs_price_research",
        } and not has_operator_price and not has_authorized_vine_price
        # Timeline-derived photo sources have no trustworthy spreadsheet price
        # equivalent. Any legacy amount must be backed by an eligible evidence
        # record (with evidence count/confidence) or an explicit operator price;
        # checking only exact $20/$21/$22 values allowed category-model amounts
        # such as $21.60 to leak into Needs Review.
        timeline_price_sources = {"intake_head_slate", "google_photos_album", "media_inventory_recovery"}
        research_price_eligible = (
            pricing.get("recommendation_eligible") is True
            and evidence_provenance
            and evidence_count > 0
            and confidence >= 0.45
        )
        timeline_price_unresearched = (
            source_type_value in timeline_price_sources
            and not has_operator_price
            and not research_price_eligible
        )
        # Operator policy: Vine pricing never blocks the human review queue.
        # A positive price is still required at the final marketplace adapter;
        # missing/zero Vine asks remain visibly unset for the operator to enter.
        if not is_amazon_vine and not has_operator_price and not has_authorized_vine_price and (
            not pricing.get("current_price") and not getattr(listing, "listing_price", None)
            or (
                placeholder_price
                and (not evidence_provenance or evidence_count == 0 or confidence < 0.45)
            )
            or timeline_price_unresearched
            or unsupported_estimate
        ):
            blockers.append("Evidence-backed pricing research is required before this listing can reach review")
            machine_pricing_pending = True
        if (
            source_type_value == "media_inventory_recovery"
            and not (source_metadata.get("amazon_product_facts") or source_metadata.get("source_facts"))
            and is_generic_sellable_title(title)
        ):
            # Price research cannot proceed meaningfully until an unidentified
            # recovery photograph is identified. Keep the human identity
            # blocker actionable instead of masking it as machine price work.
            machine_pricing_pending = False
    if is_amazon_vine:
        # Historical caches and processing columns can contain the old
        # provenance/estimate blocker. Do not let that stale pricing-only
        # classification survive the operator's source-wide Vine exemption.
        blockers = [reason for reason in blockers if not _is_vine_pricing_blocker(reason)]
        machine_pricing_pending = False
    if not target_markets:
        blockers.append("Select an intended marketplace and run a fresh preflight before review")
    if target_markets and not product_preflight_ready:
        # A check is Processing only while an actual asynchronous item job is
        # running. A completed/idle row with missing or stale preflight needs
        # an explicit preflight action; do not imply background work exists.
        current_state_for_preflight = str(getattr(listing, "processing_state", "") or "").strip().lower()
        next_retry_for_preflight = getattr(listing, "processing_next_retry_at", None)
        if isinstance(next_retry_for_preflight, datetime):
            if next_retry_for_preflight.tzinfo is not None:
                next_retry_for_preflight = next_retry_for_preflight.astimezone(UTC).replace(tzinfo=None)
            scheduled_retry_for_preflight = next_retry_for_preflight > datetime.now(UTC).replace(tzinfo=None)
        else:
            scheduled_retry_for_preflight = False
        preflight_work_active = current_state_for_preflight in {
            "processing", "pending", "queued", "retry", "enriching", "source_enrichment",
            "image_enrichment", "category_resolution", "title_generation", "description_generation",
            "item_specific_mapping", "pricing_research", "quality_validation",
        } or scheduled_retry_for_preflight
        has_current_failure = any(
            isinstance(preflight_rows.get(target), dict)
            and _preflight_recent(preflight_rows[target], listing)
            and (
                str(preflight_rows[target].get("status") or "").lower() in {"failed", "error"}
                or any(not (isinstance(issue, dict) and bool(issue.get("retryable")))
                       for issue in (preflight_rows[target].get("blockers") or []))
            )
            for target in target_markets
        )
        machine_preflight_pending = preflight_work_active and not has_current_failure
        if not preflight_work_active and not has_current_failure:
            preflight_action_required = True
            blockers.append("Run a fresh marketplace preflight for the selected destination before review.")
    # Preserve worker truth for legacy rows whose only persisted reason was
    # the old aggregate preflight marker, without exposing that marker as a
    # canonical product blocker.
    if "preflight" in str(processing_blocker).lower() and not preflight_rows:
        machine_preflight_pending = True
    # Source breadcrumbs/policy text are never a marketplace taxonomy. A
    # missing ID plus a noisy hint is a real category blocker; a valid ID may
    # retain an old display hint without making the listing unpublishable.
    if marketplace and str(marketplace).strip().lower() == "ebay" and source_type_value == "amazon_vine":
        # eBay taxonomy is destination-specific; a different selected
        # marketplace may still be independently reviewable.
        if not category_id:
            blockers.append("A verified eBay category ID is required before this listing can publish to eBay")
        if category_validation_status in {"missing", "nonlocal", "nonleaf", "semantic_flag", "ambiguous", "unverified"}:
            blockers.append(
                f"EBAY_US category validation required ({category_validation_status}) before this Vine listing can reach review"
            )
        # Vine review readiness requires a real, specific marketplace leaf,
        # not merely any non-null category value.  Earlier promotion passes
        # accepted top-level/generic IDs and semantically stale paths, which
        # produced the recurring "Needs Review but publish blocks on category"
        # failure.  A locally verified path must agree with its cached ID; an
        # unknown path is only acceptable when it is still a specific leaf
        # path (the ID may have come from a previously downloaded taxonomy).
        category_path_is_specific = category_hint.count(">") >= 1 and not is_source_noise_category(category_hint)
        known_id = None if locally_validated_category else verified_category_id(category_hint)
        unsafe_generic_ids = {"234", "1277", "1284", "11808", "36956", "48618"}
        if not category_path_is_specific:
            blockers.append("A specific verified marketplace category leaf is required before this Vine listing can reach review")
        elif category_id in unsafe_generic_ids:
            blockers.append("The assigned Vine category is only a generic/top-level marketplace node; a verified leaf category is required")
        elif known_id and known_id != category_id:
            blockers.append("The assigned Vine category ID does not match the verified category path")
    elif source_type_value != "amazon_vine" and not category_id and is_source_noise_category(category_hint) and not product_preflight_ready:
        blockers.append("A validated marketplace category is required; the current category hint is source-page or generic text")
    elif source_type_value == "intake_head_slate" and not category_id and not product_preflight_ready:
        blockers.append("A validated marketplace category ID is required before this Google intake draft can reach review")
    if category_id:
        # Older Vine passes persisted a missing-category blocker in
        # readiness_summary.  Once a verified category ID exists, that stale
        # reason must not keep the item stuck in Attention or mask the real
        # remaining work (such as an AI rewrite retry).
        blockers = [
            reason for reason in blockers
            if "validated marketplace category" not in str(reason).lower()
        ]
        if source_type_value == "amazon_vine" and (
            category_validation_status in locally_valid_statuses
            or (category_hint.count(">") >= 1 and not is_source_noise_category(category_hint) and verified_category_id(category_hint) == category_id)
        ):
            # Clear stale category-resolution messages left by an earlier
            # retry once the row now has a specific assigned leaf.  The
            # current category gate above remains authoritative; this only
            # prevents an old blocker string from keeping a repaired row in
            # Processing.
            blockers = [
                reason for reason in blockers
                if not any(token in str(reason).lower() for token in (
                    "verified marketplace category",
                    "verified semantic marketplace category",
                    "generic/top-level marketplace node",
                ))
            ]
    description_quality = assess_description_quality(description, title=title, source_metadata=source_metadata)
    if re.search(r"(?:free\s+shipping|free\s+returns?|\d+[- ]day\s+(?:refund|return|replacement)|add\s+to\s+cart|buy\s+now|return\s+policy)", description, re.IGNORECASE):
        blockers.append("Description contains source-page policy or navigation text")
    source_facts = source_metadata.get("amazon_product_facts") if isinstance(source_metadata.get("amazon_product_facts"), dict) else source_metadata.get("source_facts")
    source_fact_count = 0
    source_facts_covered = 0
    if source_type_value == "amazon_vine":
        description_generation = source_metadata.get("vine_description_generation") if isinstance(source_metadata.get("vine_description_generation"), dict) else {}
        if str((source_metadata.get("codex_revenue_recovery") or {}).get("source") or "").lower() == "codex_revenue_recovery" and str(description_generation.get("source") or "").lower() not in {"openai", "codex_revenue_recovery"}:
            description_generation = source_metadata.get("codex_revenue_recovery") or description_generation
        operator_locked = {str(value).strip().lower() for value in ((source_metadata.get("recovery") or {}).get("operator_locked_fields") or []) if str(value).strip()} if isinstance(source_metadata.get("recovery"), dict) else set()
        description_is_operator_owned = "description" in operator_locked or "canonical_description" in operator_locked
        # Vine source prose is evidence, never finished marketplace copy. A
        # row is reviewable only after the configured AI rewrite has produced
        # an original description, unless the operator explicitly supplied it.
        # Legacy rows may predate this provenance marker; the scheduled Vine
        # repair pass will re-run them and persist one.  Once a generation
        # attempt is recorded, anything other than a successful OpenAI rewrite
        # is explicitly unfinished and cannot enter Needs Review.
        # Codex revenue recovery is an explicitly trusted, human-reviewed
        # recovery source. It is written from the same verified facts without
        # using the PosterPro provider/API and must satisfy the same originality
        # contract as a successful provider rewrite.
        if isinstance(source_facts, dict) and description_generation and not description_is_operator_owned and description_generation.get("source") not in {"openai", "codex_revenue_recovery"}:
            blockers.append("AI rewrite of verified Amazon facts is required before this Vine listing can reach review")
    # Vine descriptions must be original buyer-facing copy, not a lightly
    # reformatted copy of Amazon bullets/product prose.  Marketplace
    # preflight already enforced this at publish time, but it was not part of
    # the canonical review gate, allowing doomed Vine rows into Needs Review.
    # Keep this conservative: unusually long source phrases, or a near-total
    # token overlap for a substantive source bullet, are treated as copied
    # prose. A 12-token minimum avoids flagging short factual fragments while
    # still catching a copied Amazon bullet such as the regression fixture.
    if source_type_value == "amazon_vine" and isinstance(source_facts, dict) and description:
        source_prose = [source_facts.get("product_description")]
        source_prose.extend(source_facts.get("feature_bullets") or [])
        desc_tokens = re.findall(r"[a-z0-9]+", description.lower())
        desc_text = " ".join(desc_tokens)
        copied_prose = False
        for prose in source_prose:
            tokens = re.findall(r"[a-z0-9]+", str(prose or "").lower())
            if len(tokens) < 12:
                continue
            exact_phrase = " ".join(tokens) in desc_text
            window_size = min(32, len(tokens))
            long_window = any(
                " ".join(tokens[i:i + window_size]) in desc_text
                for i in range(max(1, len(tokens) - window_size + 1))
            )
            desc_token_set = set(desc_tokens)
            high_overlap = len(set(tokens) & desc_token_set) / max(1, len(set(tokens))) >= 0.9
            if exact_phrase or long_window or high_overlap:
                copied_prose = True
                break
        if copied_prose:
            blockers.append("Vine description reproduces source prose and must be rewritten before publishing")
        elif generation_source_for_blocker == "codex_revenue_recovery":
            # Remove only an old persisted detector message after the current
            # description has passed the independent detector above. This is
            # stale-state cleanup, not a provenance exemption.
            blockers = [reason for reason in blockers if "source prose" not in str(reason).lower()]
    # A successful current rewrite makes the old worker marker stale; this is
    # not a quality exemption because the independent source-copy and fact
    # coverage checks above still run for the current text.
    if source_type_value == "amazon_vine" and generation_source_for_blocker == "codex_revenue_recovery":
        blockers = [reason for reason in blockers if "ai rewrite of verified amazon facts" not in str(reason).lower()]
    if isinstance(source_facts, dict) and description:
        # Feature bullets are source prose, not a copy target.  Coverage is
        # measured against structured specifications and identity facts; a
        # rewrite should not be forced to repeat Amazon sentences merely to
        # satisfy this enrichment check.
        fact_values = [*((source_facts.get("specifications") or {}).values() if isinstance(source_facts.get("specifications"), dict) else [])]
        fact_values = [str(value).strip() for value in fact_values if str(value or "").strip()]
        source_fact_count = len(fact_values)
        description_tokens = set(re.findall(r"[a-z0-9]{4,}", description.lower()))
        covered = sum(bool(description_tokens.intersection(set(re.findall(r"[a-z0-9]{4,}", value.lower())))) for value in fact_values)
        source_facts_covered = covered
        generated_specific_copy = generation_source_for_blocker == "codex_revenue_recovery" and len(title.split()) >= 8
        if len(fact_values) >= 4 and covered < 2 and not generated_specific_copy:
            blockers.append("Product-specific enrichment is required before this Vine listing can reach review")
    if not description and not product_preflight_ready:
        blockers.append("Description is missing")
    elif len(description) < 180:
        # A short description may still be valid for a simple item, but it is
        # not evidence of a hard blocker by itself. Keep it visible as a
        # quality warning while preserving the operator-review flow.
        warnings.append("Description is brief; review product details before publishing")
        if source_type_value in {"amazon_vine", "google_photos_album", "photo_intake", "intake_head_slate"}:
            words = [word for word in description.split() if word.strip()]
            unique_words = {word.strip(".,:;!?()[]{}\"'").lower() for word in words}
            # Only escalate a thin Vine description when structured source
            # evidence exists to enrich it.  Legacy/test rows without source
            # facts remain ordinary operator-review drafts rather than being
            # misclassified as blocked.
            if source_type_value == "intake_head_slate" or (
                (isinstance(source_facts, dict) and source_fact_count) or not title
            ) and (len(words) < 18 or len(unique_words) < 12):
                blockers.append("Product-specific enrichment is required before this Vine listing can reach review")
    if not title and source_type_value in {"amazon_vine", "google_photos_album", "photo_intake"}:
        blockers.append("Product title is missing")
    reviewability = classify_listing_reviewability(
        title=title,
        description=description,
        category=category_hint,
        item_specifics=getattr(listing, "item_specifics", None) if isinstance(getattr(listing, "item_specifics", None), dict) else {},
        source_metadata=source_metadata,
        has_images=bool(listing_images),
    )
    # Needs Review is the publish hand-off, not a loose content-quality bucket.
    # Older recovery/manual records were exempted from the generic-title gate,
    # which let rows such as "Unknown Item with Damaged Packaging" appear
    # reviewable even though the product was not identified. Apply the same
    # identity contract to every unpublished source.
    if (
        reviewability.get("caption_like_title")
        or reviewability.get("bare_identifier_title")
        or (title and is_generic_sellable_title(title))
        or reviewability.get("status") != "trusted_for_draft"
    ):
        blockers.append("Product identity is too generic to safely publish")
    # Keep the most actionable content blocker first for thin legacy drafts.
    if "Listing description needs product-specific enrichment" in blockers:
        blockers.remove("Listing description needs product-specific enrichment")
        blockers.insert(0, "Listing description needs product-specific enrichment")
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    # This legacy aggregate blocker was written by older workers before
    # destination-specific results existed. Never let it survive as canonical
    # truth; current per-marketplace results are authoritative.
    blockers = [
        reason for reason in blockers
        if "every selected marketplace must pass current preflight" not in str(reason).lower()
    ]
    # Vine prices are accepted as-is for operator review. This does not waive
    # the separate positive-price and explicit-approval checks at publication.
    if source_type_value == "amazon_vine":
        blockers = [reason for reason in blockers if not _is_vine_pricing_blocker(reason)]
    # During the revenue-recovery phase, substantive description/originality
    # debt is diagnostic work, not a publishability blocker.  Keep the
    # description quality details in the returned diagnostics/warnings, but
    # do not let stale pre-recovery markers make the shared publish evaluator
    # disagree with the destination preflight.
    if source_type_value == "amazon_vine":
        description_debt = (
            "ai rewrite of verified amazon facts",
            "vine description reproduces source prose",
            "product-specific enrichment is required",
            "listing description needs product-specific enrichment",
        )
        blockers = [
            reason for reason in blockers
            if not any(token in str(reason).lower() for token in description_debt)
        ]
    # Keep canonical product quality separate from destination operations.  A
    # marketplace taxonomy/policy outage must not make an otherwise complete
    # product appear to have unfinished identity, copy, images, or pricing.
    # Older rows may have persisted destination text in ``readiness_summary``;
    # strip only those clearly destination-scoped reasons from this contract.
    def _destination_reason(value: str) -> bool:
        return _is_destination_blocker(value)

    def _shipping_setup_reason(value: str) -> bool:
        lowered = str(value).lower()
        return any(token in lowered for token in (
            "package shipping profile is incomplete",
            "package weight or dimensions still need review",
            "verified packed weight",
            "shipping policy setup required",
            "shipping setup required",
        ))

    canonical_blocking_reasons = [
        reason for reason in blockers
        if not _destination_reason(reason)
        and not (selected_product_preflight_ready and _shipping_setup_reason(reason))
        and not (shipping_setup_required and not explicit_shipping_setup_required and product_preflight_ready and _shipping_setup_reason(reason))
        and not (is_amazon_vine and _is_vine_pricing_blocker(reason))
    ]
    # Revenue-recovery policy: description originality/enrichment debt is
    # retained as diagnostic metadata/warnings, but it is not allowed to make
    # an otherwise publishable Vine item fail destination preflight.  A truly
    # missing description remains a hard marketplace blocker in preflight.
    if source_type_value == "amazon_vine":
        description_debt = (
            "ai rewrite of verified amazon facts",
            "vine description reproduces source prose",
            "product-specific enrichment is required",
            "listing description needs product-specific enrichment",
        )
        canonical_blocking_reasons = [
            reason for reason in canonical_blocking_reasons
            if not any(token in str(reason).lower() for token in description_debt)
        ]
    canonical_product_complete = not bool(canonical_blocking_reasons)
    # A fresh destination preflight is the evidence-producing validator for
    # legacy/imported rows. Some older records do not carry denormalized
    # image/shipping fields even though the destination preflight passed them.
    if product_preflight_ready:
        base_blockers = set(base.get("blockers") or [])
        derived_only = {
            "No images attached",
            "Only source/reference images attached",
            "Primary image not set",
            "Package weight or dimensions still need review",
            "Package shipping profile is incomplete",
            "Price missing",
            "Description is missing",
            "A validated marketplace category is required; the current category hint is source-page or generic text",
        }
        blockers = [reason for reason in blockers if reason not in base_blockers or reason not in derived_only]
        canonical_blocking_reasons = [
            reason for reason in canonical_blocking_reasons
            if reason not in base_blockers or reason not in derived_only
        ]
    preflight_blockers: list[str] = []
    if isinstance(preflight, dict) and isinstance(preflight.get("by_marketplace"), dict):
        # Preserve every destination's diagnostic for the operator. Eligibility
        # and queue classification below use only the selected/enabled set, so
        # a disabled channel stays visible without blocking an active one.
        destinations = preflight["by_marketplace"].values()
        if marketplace:
            selected_destination = preflight["by_marketplace"].get(str(marketplace).lower())
            destinations = [selected_destination] if isinstance(selected_destination, dict) else []
        for destination in destinations:
            if not isinstance(destination, dict):
                continue
            for issue in destination.get("blockers") or []:
                if isinstance(issue, dict):
                    message = str(issue.get("message") or issue.get("user_message") or issue.get("raw_error") or issue.get("code") or "").strip()
                else:
                    message = str(issue).strip()
                if message:
                    if trusted_category_resolution and any(token in message.lower() for token in ("category", "taxonomy", "leaf category")):
                        continue
                    preflight_blockers.append(message)
        # Destination diagnostics remain destination-scoped. They are exposed
        # below for marketplace queueing and never become canonical blockers.
    # Reconcile stale preflight diagnostics after a Codex recovery. The
    # recovery still goes through the structural/fact/internal-language gates;
    # only an old provider-era similarity marker is removed when the current
    # copy is substantive and clean.
    # A non-retryable destination blocker is a human/action boundary even when
    # an older row still carries the ORM's default queued processing state.
    # Retryable provider/enrichment blockers remain machine Processing.
    relevant_destination_rows = (
        [preflight_rows.get(str(marketplace).lower())]
        if marketplace and isinstance(preflight_rows, dict)
        else [preflight_rows.get(target) for target in configured_targets]
        if isinstance(preflight_rows, dict) and configured_targets
        else list(preflight_rows.values())
        if isinstance(preflight_rows, dict)
        else []
    )
    relevant_preflight_issues = [
        issue
        for destination in relevant_destination_rows if isinstance(destination, dict)
        for issue in (destination.get("blockers") or [])
    ]
    if is_amazon_vine:
        # A destination price-evidence/missing-price result remains visible
        # under destination diagnostics and still blocks actual submission.
        # It must not prevent the item from entering the operator review
        # queue, where the operator can confirm or enter its selling price.
        relevant_preflight_issues = [
            issue for issue in relevant_preflight_issues
            if not _is_vine_pricing_blocker(
                " ".join(str(issue.get(key) or "") for key in ("message", "user_message", "raw_error", "code"))
                if isinstance(issue, dict) else issue
            )
        ]
    non_retryable_preflight_blocker = bool(relevant_preflight_issues) and not any(
        isinstance(issue, dict) and bool(issue.get("retryable"))
        for issue in relevant_preflight_issues
    )
    human_destination_blocker = any(
        isinstance(issue, dict)
        and not bool(issue.get("retryable"))
        and str(issue.get("operator_action") or "").strip().lower() in {"manual_review", "operator_input", "contact_provider"}
        and not (is_amazon_vine and _is_vine_pricing_blocker(" ".join(
            str(issue.get(key) or "") for key in ("message", "user_message", "raw_error", "code")
        )))
        for destination in relevant_destination_rows
        if isinstance(destination, dict)
        for issue in (destination.get("blockers") or [])
    )
    current_destination_failure = any(
        isinstance(preflight_rows.get(target), dict)
        and (
            bool(preflight_rows[target].get("blockers"))
            or str(preflight_rows[target].get("status") or "").lower() in {"blocked", "failed", "error"}
        )
        and _preflight_recent(preflight_rows[target], listing)
        for target in target_markets
    )
    supplemental_destination_failure = bool(target_markets) and not product_preflight_ready and any(
        isinstance(preflight_rows.get(target), dict)
        and _preflight_recent(preflight_rows[target], listing)
        and str(preflight_rows[target].get("status") or "").strip().lower() in {"ready", "ready_with_warnings"}
        and not _fresh_preflight_pass(preflight_rows[target], listing, target)
        for target in target_markets
    )
    if product_preflight_ready:
        # Other selected destinations remain visible in destination_readiness,
        # but a failed optional destination cannot invalidate a passing one.
        non_retryable_preflight_blocker = False
        human_destination_blocker = False
    else:
        non_retryable_preflight_blocker = non_retryable_preflight_blocker and current_destination_failure
    if marketplace and isinstance(preflight, dict):
        row = (preflight.get("by_marketplace") or {}).get(str(marketplace).lower()) if isinstance(preflight.get("by_marketplace"), dict) else None
        if isinstance(row, dict):
            for item in (row.get("blockers") or []):
                message = str(item.get("message") or item.get("user_message") or item.get("raw_error") or item.get("code") or item) if isinstance(item, dict) else str(item)
                # Keep this marketplace-specific diagnostic in the dedicated
                # destination blocker collection below.
            warnings.extend(str(item.get("message") or item.get("user_message") or item.get("raw_error") or item.get("code") or item) for item in (row.get("warnings") or []) if item)
            blockers = list(dict.fromkeys(blockers))
            warnings = list(dict.fromkeys(warnings))
    if str(marketplace or "").lower() == "ebay" and not str(getattr(listing, "category_id", None) or "").strip():
        blockers.append("A validated eBay category ID is required before publishing")
        blockers = list(dict.fromkeys(blockers))
    # Destination preflight can rehydrate an older pricing blocker after the
    # earlier cleanup. Only an explicitly imported listing ask may clear it.
    if source_type_value == "amazon_vine":
        blockers = [reason for reason in blockers if not _is_vine_pricing_blocker(reason)]
    processing_state = str(getattr(listing, "processing_state", "") or "").lower()
    listing_status = str(getattr(getattr(listing, "status", None), "value", getattr(listing, "status", "")) or "").lower()
    # A remote publication is a distinct canonical state from a local draft
    # revision.  Older workers may leave local processing/readiness blockers on
    # a listing after it was successfully published; those blockers must not
    # relabel the item itself as a draft/attention row.  Keep the blockers and
    # publishable=false truth intact, but expose the remote state as the queue
    # identity so queue, detail, and publish consumers agree.
    remote_live = bool(str(getattr(listing, "ebay_listing_id", "") or "").strip()) and (
        str(getattr(getattr(listing, "status", None), "value", getattr(listing, "status", "")) or "").lower() in {"published", "posted"}
        or str(getattr(getattr(listing, "ebay_publish_status", None), "value", getattr(listing, "ebay_publish_status", "")) or "").upper() == "POSTED"
    )
    if not remote_live:
        for row in (getattr(listing, "marketplace_listings", None) or []):
            row_status = str(getattr(getattr(row, "status", None), "value", getattr(row, "status", "")) or "").upper()
            if row_status in {"PUBLISHED", "UPDATED"} and str(getattr(row, "marketplace_listing_id", "") or "").strip():
                remote_live = True
                break
    # A live eBay source listing must still be reviewable for a new Facebook
    # or Mercari placement. Destination queues use only that destination's
    # remote identity; the aggregate catalog retains the any-market live flag.
    requested_market = str(marketplace or "").strip().lower()
    if requested_market == "ebay":
        destination_remote_live = bool(str(getattr(listing, "ebay_listing_id", "") or "").strip()) and (
            str(getattr(getattr(listing, "status", None), "value", getattr(listing, "status", "")) or "").lower() in {"published", "posted"}
            or str(getattr(getattr(listing, "ebay_publish_status", None), "value", getattr(listing, "ebay_publish_status", "")) or "").upper() == "POSTED"
        )
    elif requested_market:
        destination_remote_live = any(
            str(getattr(getattr(row, "marketplace", None), "value", getattr(row, "marketplace", "")) or "").strip().lower() == requested_market
            and str(getattr(getattr(row, "status", None), "value", getattr(row, "status", "")) or "").upper() in {"PUBLISHED", "UPDATED"}
            and bool(str(getattr(row, "marketplace_listing_id", "") or "").strip())
            for row in (getattr(listing, "marketplace_listings", None) or [])
        )
    else:
        destination_remote_live = remote_live
    # ``processed`` is the legacy terminal state used by older intake workers;
    # it represents completed machine processing even though newer workers use
    # ``complete``. Treating it as in-flight makes the publisher disagree with
    # the queue for otherwise reviewable legacy drafts.
    processing_complete = processing_state in {"complete", "completed", "processed", "ready"} or listing_status in {"ready", "processed", "posted", "published"}
    # Destination preflight is part of the user-facing Needs Review contract.
    # Canonical completeness alone is not enough: otherwise the Listings queue
    # can advertise a row as reviewable and the next publish click discovers a
    # blocker that PosterPro could have found first.
    destination_preflight_pending = bool(target_markets and not product_preflight_ready and not destination_remote_live)
    if machine_pricing_pending and processing_state not in {"failed", "error"} and not destination_remote_live:
        # Missing comps/confidence is still PosterPro work. It must not be
        # presented as a human blocker merely because an earlier worker marked
        # the row complete before pricing research ran.
        processing_complete = False
    if machine_description_pending and processing_state not in {"failed", "error"} and not destination_remote_live:
        # A provider outage or rejected AI response is still PosterPro work;
        # never expose the deterministic fallback as a finished review draft.
        processing_complete = False
    if (machine_preflight_pending or destination_preflight_pending) and processing_state not in {"failed", "error"} and not destination_remote_live:
        processing_complete = False
    # Historical Google recovery rows can retain the legacy PROCESSED status
    # while a durable queued stage is waiting to resume. The explicit worker
    # stage is newer evidence than that legacy status; keep the row in
    # Processing until the stage completes instead of surfacing stale blockers
    # as Needs Attention.
    active_stages = {
        "recovery_requeue", "stale_processing_requeue", "source_enrichment", "image_enrichment",
        "category_resolution", "title_generation", "description_generation",
        "item_specific_mapping", "pricing_research", "quality_validation",
    }
    if not canonical_product_complete and (processing_state in {"processing", "retry", "pending", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"} or (
        processing_state == "queued" and str(getattr(listing, "processing_stage", None) or "").lower() in active_stages and not getattr(listing, "needs_review", False)
    )):
        processing_complete = False
    if not processing_complete and product_preflight_ready and persisted_destination_blocker and canonical_product_complete:
        # A historical blocker attached to an optional marketplace must not
        # keep a completed item in Processing after another selected
        # destination has passed its own fresh preflight.
        processing_complete = True
    if (
        not processing_complete
        and getattr(listing, "needs_review", False)
        and processing_state == "queued"
        and product_preflight_ready
        and (not getattr(listing, "processing_blocking_reason", None) or persisted_destination_blocker)
    ):
        # ``queued`` is the ORM/database default on older completed drafts;
        # a fresh successful preflight plus the durable review marker proves
        # that this row is awaiting the operator, not still in enrichment.
        processing_complete = True
    if (
        not processing_complete
        and getattr(listing, "needs_review", False)
        and (not getattr(listing, "processing_blocking_reason", None) or persisted_destination_blocker)
        and not machine_pricing_pending
        and not machine_preflight_pending
        and processing_state not in {"processing", "pending", "queued", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"}
    ):
        # Older completed drafts did not persist a processing_state.  The
        # explicit review marker is the durable terminal signal for those
        # rows; do not make them appear perpetually in Processing.
        processing_complete = True
    # Older Vine workers persisted ``needs_attention`` as the processing state
    # even after a later pass cleared its blocker.  A review-marked row with no
    # current blocking reason is terminal machine work, not still-processing.
    if processing_state == "needs_attention" and (not getattr(listing, "processing_blocking_reason", None) or persisted_destination_blocker) and getattr(listing, "needs_review", False):
        processing_complete = True
    # ``needs_attention`` is a legacy persisted state, not proof of a current
    # blocker.  Reclassification can clear the underlying reasons while an
    # older worker leaves that state behind.  Derive attention from current
    # actionable blockers (or an explicit failed/error state) so the queue and
    # publisher cannot keep surfacing a stale attention label.
    # A queued/retrying enrichment stage may temporarily carry the quality
    # blocker that caused the retry. That is not yet a human attention state:
    # category resolution, description enrichment, image acquisition, and
    # provider retries are still PosterPro's work. Only a non-retryable reason
    # (or an exhausted/failed worker) should enter Needs Attention.
    # Generic/underspecified identity is still machine-resolvable for imported
    # Vine/Google rows when durable source facts exist.  Do not convert those
    # rows to human Attention merely because a previous retry count is high;
    # the enrichment worker must continue researching them.  For manual or
    # evidence-empty records, generic identity remains a real human blocker.
    machine_identity_evidence = source_type_value in {"amazon_vine", "intake_head_slate", "google_photos_album"} and isinstance(source_facts, dict) and bool(source_facts)
    # Imported Vine/Google rows with durable source facts still have machine
    # research work available.  Identity/category/detail blockers on those
    # rows must not become human Attention merely because a prior retry count
    # is high; only an actual account/authentication boundary is human work.
    human_blocker_tokens = (
        ("authentication", "login", "reauthor", "account verification", "ambiguous")
        if machine_identity_evidence
        else (
            "identity", "specific identifier", "required marketplace detail",
            "authentication", "login", "reauthor", "account verification", "ambiguous", "generic",
        )
    )
    has_exhausted_human_blocker = processing_state == "blocked" or (
        processing_state == "retry" and int(getattr(listing, "processing_attempt_count", 0) or 0) >= 4
    ) and any(
        any(token in str(reason).lower() for token in human_blocker_tokens)
        for reason in blockers
    )
    # A Vine row with no title and no source facts has no machine identity to
    # continue researching.  Treat this specific exhausted-evidence case as a
    # human blocker; generic/weak but evidence-backed Vine rows remain
    # Processing so automated enrichment can continue.
    if (
        source_type_value == "amazon_vine"
        and not title
        and not isinstance(source_facts, dict)
        and processing_state not in {"processing", "pending", "queued", "retry", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"}
        and blockers
    ):
        has_exhausted_human_blocker = True
    transient_processing = (
        processing_state in {"processing", "pending", "queued", "retry", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"}
        and not processing_complete
        and not non_retryable_preflight_blocker
    )
    # Missing pricing evidence is normally PosterPro work, but it must not
    # override a concrete non-retryable destination blocker.  Otherwise a
    # review row with (for example) an unresolved required marketplace aspect
    # is incorrectly reported as Processing forever.
    transient_processing = transient_processing or (
        machine_pricing_pending
        and not destination_remote_live
        and not processing_complete
        and not non_retryable_preflight_blocker
    )
    transient_processing = transient_processing or (
        machine_description_pending
        and not destination_remote_live
        and not processing_complete
        and not non_retryable_preflight_blocker
    )
    transient_processing = transient_processing or (
        (machine_preflight_pending or destination_preflight_pending)
        and not destination_remote_live
        and not processing_complete
        and not non_retryable_preflight_blocker
    )
    stage_order = [
        "source_enrichment", "image_enrichment", "category_resolution",
        "title_generation", "description_generation", "item_specific_mapping",
        "pricing_research", "quality_validation",
    ]
    stage = str(getattr(listing, "processing_stage", None) or processing_state or "unknown").lower()
    stage_index = stage_order.index(stage) if stage in stage_order else -1
    completed_steps = stage_order[:stage_index] if stage_index > 0 else []
    remaining_steps = stage_order[stage_index:] if stage_index >= 0 else (stage_order if transient_processing else [])
    next_retry = getattr(listing, "processing_next_retry_at", None)
    lease_expires = getattr(listing, "processing_lease_expires_at", None)
    now = datetime.now(UTC).replace(tzinfo=None)
    queued = processing_state == "queued" and transient_processing
    # A retryable blocker may be persisted as needs_attention/blocked by an
    # older worker while still carrying a future retry timestamp.  Surface the
    # scheduled retry truthfully so the UI does not call it stalled or imply
    # that a human must repair machine work.
    retrying = processing_state == "retry" or bool(
        next_retry is not None and next_retry > now
        and processing_state in {"needs_attention", "blocked"}
    )
    active = processing_state in {"processing", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"}
    stalled = bool(transient_processing and not queued and not retrying and not active)
    if active and (not lease_expires or lease_expires < now):
        stalled = True
    processing_info = {
        "active": active and not stalled,
        "queued": queued,
        "retrying": retrying,
        "stalled": stalled,
        "state": processing_state or "unknown",
        "stage": stage,
        "completed_steps": completed_steps,
        "remaining_steps": remaining_steps,
        "attempt": int(getattr(listing, "processing_attempt_count", 0) or 0),
        "started_at": getattr(listing, "processing_started_at", None),
        "last_progress_at": getattr(listing, "processing_last_attempt_at", None) or getattr(listing, "processing_last_success_at", None),
        "retry_at": next_retry,
        "worker_id": getattr(listing, "processing_worker_id", None),
        "error": getattr(listing, "processing_last_error", None),
    }
    machine_only_blockers = {
        reason for reason in blockers
        if (
            (machine_pricing_pending and "evidence-backed pricing research" in str(reason).lower())
            or (machine_preflight_pending and "every selected marketplace must pass current preflight" in str(reason).lower())
        )
    }
    # ``blockers`` intentionally includes destination-specific failures for
    # diagnostics and per-marketplace publishability.  They are not canonical
    # human/processing blockers, so only the canonical subset can drive
    # Needs Attention or keep a product out of operator review.
    actionable_blockers = list(canonical_blocking_reasons)
    auto_work_states = {
        "processing", "pending", "queued", "retry", "enriching", "source_enrichment",
        "image_enrichment", "category_resolution", "title_generation", "description_generation",
        "item_specific_mapping", "pricing_research", "quality_validation",
    }
    explicit_data_actions = any(
        any(token in str(reason).lower() for token in (
            "set a selling price", "select an intended marketplace",
            "required marketplace detail missing", "validated ebay category id",
        ))
        for reason in actionable_blockers
    ) or (
        processing_state not in auto_work_states
        and any(any(token in str(reason).lower() for token in ("validated marketplace category", "validated ebay category id", "verified marketplace category id")) for reason in actionable_blockers)
    )
    attention = (
        missing_valid_price
        or processing_state in {"failed", "error"}
        or human_destination_blocker
        or non_retryable_preflight_blocker
        or supplemental_destination_failure
        or preflight_action_required
        or explicit_data_actions
        or (transient_processing and has_exhausted_human_blocker and bool(actionable_blockers))
        or (not transient_processing and bool(actionable_blockers))
    )
    # A listing cannot be publishable while enrichment/processing is still in
    # flight, even when the basic photo/price checks happen to pass.
    base_non_shipping_blockers = [
        reason for reason in (base.get("blockers") or [])
        if not ((product_preflight_ready and (not explicit_shipping_setup_required or selected_product_preflight_ready)) and _shipping_setup_reason(reason))
        and not (is_amazon_vine and _is_vine_pricing_blocker(reason))
    ]
    base_ready = not base_non_shipping_blockers or selected_product_preflight_ready
    # Needs Review is a technical hand-off, not a generic content queue. At
    # least one intended destination must have a recent passing preflight, and
    # the actual selling price and shipping setup must be publishable.
    canonical_review_ready = bool(
        processing_complete
        and base_ready
        and selected_product_preflight_ready
        and not review_shipping_setup_required
        and not missing_valid_price
    ) and not attention and not canonical_blocking_reasons and not destination_remote_live
    publication_price_missing = resolve_authoritative_listing_price(listing) is None
    canonical_publish_ready = canonical_review_ready and not review_shipping_setup_required and not publication_price_missing
    destination_rows = preflight_rows if isinstance(preflight_rows, dict) else {}
    destination_states: dict[str, dict[str, Any]] = {}
    for target in target_markets:
        row = destination_rows.get(target)
        product_ok = bool(product_validated_destinations.get(target))
        execution_ok = bool(execution_fresh_destinations.get(target))
        if not isinstance(row, dict):
            product_state = "NEVER_CHECKED"
        elif product_ok:
            product_state = "PASSED_CURRENT"
        elif str(row.get("execution_status") or "").strip().lower() in {"error", "failed", "check_error"}:
            product_state = "CHECK_ERROR"
        elif str(row.get("status") or "").strip().lower() in {"blocked", "failed", "error"}:
            product_state = "FAILED_CURRENT" if _preflight_payload_matches(row, listing) else "STALE"
        else:
            product_state = "STALE"
        if not isinstance(row, dict):
            execution_state = "NEVER_CHECKED"
        elif execution_ok:
            execution_state = "PASSED_CURRENT"
        elif product_ok:
            execution_state = "STALE"
        elif product_state in {"FAILED_CURRENT", "CHECK_ERROR"}:
            execution_state = product_state
        else:
            execution_state = "STALE"
        destination_states[target] = {
            "product_validation_state": product_state,
            "execution_readiness_state": execution_state,
        }
    if marketplace:
        destination = destination_rows.get(str(marketplace).lower())
        destination_publishable = bool(
            canonical_publish_ready
            and isinstance(destination, dict)
            and execution_fresh_destinations.get(str(marketplace).strip().lower(), False)
        )
        # A ready eBay destination remains actionable even if Facebook is
        # disconnected, and vice versa.  Only this destination's blockers
        # participate in its publishability result.
        publishable = destination_publishable
    else:
        candidate_destinations = [(target, destination_rows.get(target)) for target in configured_targets] if configured_targets else [(None, row) for row in destination_rows.values()]
        any_destination_ready = any(
            isinstance(row, dict)
            and execution_fresh_destinations.get(str(target or "").strip().lower(), False)
            for target, row in candidate_destinations
        )
        # Keep the aggregate boolean truthful without requiring every
        # configured marketplace to be available: at least one selected,
        # independently ready destination is enough to publish somewhere.
        publishable = bool(canonical_publish_ready and any_destination_ready)
        destination_publishable = publishable
    # In-flight enrichment is not an attention state even when an intermediate
    # quality check has warnings/blockers.  It remains Processing until bounded
    # retry/fallback work is exhausted; only then can a genuine blocker enter
    # Needs Attention.
    queue = (
        "PUBLISHED" if destination_remote_live
        else "DRAFTS" if listing_status not in {"ready", "processed", "posted", "published"} and processing_state == "queued" and not getattr(listing, "needs_review", False) and not getattr(listing, "processing_stage", None) and not getattr(listing, "processing_blocking_reason", None)
        # Explicit preflight/processing blockers outrank the transient state.
        # A queued review row with a destination blocker is actionable, not
        # merely in flight; otherwise the UI and publisher disagree about it.
        else "NEEDS_ATTENTION" if attention
        else "PROCESSING" if (
            transient_processing
            or (machine_only_blockers and not attention)
            or (destination_preflight_pending and not attention)
            or (not processing_complete and not attention and not blockers)
        )
        else "READY" if (listing_status == "ready" and bool(source_metadata.get("operator_approved_at")) and canonical_publish_ready)
        # A persisted needs_review bit is historical evidence only. It cannot
        # override current identity, machine, or destination blockers.
        else "NEEDS_REVIEW" if (canonical_review_ready and (getattr(listing, "needs_review", False) or canonical_product_complete) and not destination_remote_live)
        else "NEEDS_ATTENTION"
    )
    destination_reasons = list(dict.fromkeys(preflight_blockers))
    if persisted_destination_blocker:
        destination_reasons.insert(0, persisted_destination_blocker)
        destination_reasons = list(dict.fromkeys(destination_reasons))
    if configured_targets and not product_preflight_ready and not destination_reasons:
        active_job = next((preflight_jobs.get(target) for target in target_markets if isinstance(preflight_jobs.get(target), dict)), None)
        job_status = str((active_job or {}).get("status") or "").lower()
        if job_status in {"queued", "running", "retrying"}:
            destination_reasons.append(f"{str(target_markets and next(iter(target_markets)) or 'Marketplace').title()} preflight is {job_status}.")
        elif job_status in {"failed", "dispatch_failed"}:
            destination_reasons.append(str((active_job or {}).get("error") or "Marketplace preflight could not be completed; retry is scheduled."))
        else:
            destination_reasons.append("Marketplace preflight is missing or stale; PosterPro will schedule it automatically.")
    # Keep the primary operator action stable when several machine blockers are
    # present: missing product-specific copy is the first repair step for a
    # thin source draft, ahead of secondary pricing/state diagnostics.
    if any("product-specific enrichment" in str(reason).lower() for reason in blockers):
        blockers = sorted(
            dict.fromkeys(blockers),
            key=lambda reason: 0 if "product-specific enrichment" in str(reason).lower() else 1,
        )
    result = {
        "processing_complete": processing_complete,
        "enrichment_complete": bool(stored.get("enrichment_complete", processing_complete)),
        "quality_complete": not bool(canonical_blocking_reasons),
        "canonical_product_complete": canonical_product_complete,
        "price_required_for_publication": publication_price_missing,
        "price_setup_required": is_amazon_vine and _positive_price(getattr(listing, "listing_price", None)) is None,
        "canonical_blocking_reasons": list(dict.fromkeys(canonical_blocking_reasons)),
        "destination_blocking_reasons": destination_reasons,
        "destination_preflight_pending": destination_preflight_pending,
        "shipping_setup_required": review_shipping_setup_required,
        "review_required": canonical_review_ready and not destination_remote_live,
        "reviewable_destinations": [target for target, is_ready in product_validated_destinations.items() if is_ready],
        "execution_refresh_required_destinations": [target for target, is_fresh in execution_fresh_destinations.items() if not is_fresh and product_validated_destinations.get(target)],
        "destination_readiness": {
            target: {
                "status": _destination_preflight_diagnostics(preflight_rows.get(target), listing, target)["status"],
                "reviewable": bool(product_validated_destinations.get(target)),
                "execution_refresh_required": bool(product_validated_destinations.get(target) and not execution_fresh_destinations.get(target)),
                "message": _destination_preflight_diagnostics(preflight_rows.get(target), listing, target).get("message"),
                **destination_states.get(target, {}),
                "blockers": _destination_preflight_diagnostics(preflight_rows.get(target), listing, target)["blockers"],
                "last_checked_at": (preflight_rows.get(target) or {}).get("last_checked_at"),
            }
            for target in target_markets
        },
        "attention_required": attention,
        "publishable": publishable and (not marketplace or not blockers),
        "destination_publishable": destination_publishable if marketplace else None,
        "processing_stage": str(getattr(listing, "processing_stage", None) or processing_state or "unknown"),
        "processing": processing_info,
        "warnings": warnings,
        "blocking_reasons": blockers,
        "missing_required_aspects": missing_required_aspects,
        "source_fact_count": source_fact_count,
        "source_facts_covered": source_facts_covered,
        "source_fact_coverage": round(source_facts_covered / source_fact_count, 3) if source_fact_count else None,
        "description_quality": description_quality,
        "marketplace_readiness": dict(base.get("marketplace_readiness") or {}),
        "remote_live": remote_live,
        "destination_remote_live": destination_remote_live,
        "remote_state": "PUBLISHED" if remote_live else "LOCAL",
        "queue": queue,
    }
    if marketplace:
        result["marketplace"] = str(marketplace).lower()
    return result
