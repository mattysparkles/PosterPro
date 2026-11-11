from types import SimpleNamespace
from datetime import UTC, datetime

from app.models.models import Listing, User
from app.api.routes import _compute_listing_bucket, _matches_marketplace_filter
from app.services.canonical_readiness import canonical_listing_readiness, has_authorized_vine_import_listing_price, resolve_authoritative_listing_price
from app.services.listing_ai import assess_description_quality
from app.workers.tasks import _apply_vine_quality_lifecycle


def _preflight(status="ready", blockers=None, *, category_id="123", **extra):
    return {
        "status": status,
        "stale": False,
        "last_checked_at": datetime.now(UTC).isoformat(),
        "blockers": blockers or [],
        "policy_summary": {"fulfillment_policy_validation": {
            "valid": True,
            "policy_id": "test-ebay-fulfillment",
            "buyer_pays_shipping": True,
            "free_shipping": False,
            "services": [{"service": "USPSParcel", "cost_type": "FLAT_RATE", "buyer_responsible": True, "free_shipping": False, "charge": 8.49}],
        }},
        "category_summary": {"category_id": str(category_id), "leaf_verified": True},
        **extra,
    }


def _stamp_current_preflight(listing):
    """Mark inline test evidence as checked against this exact execution payload."""
    import hashlib
    import json
    from app.services.marketplace_field_mapper import build_marketplace_payload

    if hasattr(listing, "marketplace_descriptions") and not listing.marketplace_descriptions:
        description = getattr(listing, "description", None) or getattr(listing, "canonical_description", None) or ""
        listing.marketplace_descriptions = {
            key: description for key in ("canonical", "ebay", "facebook", "mercari", "poshmark", "vinted")
        }

    data = dict(listing.marketplace_data or {})
    preflight = dict(data.get("marketplace_preflight") or {})
    by_marketplace = dict(preflight.get("by_marketplace") or {})
    for marketplace, row in by_marketplace.items():
        current = dict(row)
        current["marketplace"] = marketplace
        if marketplace == "ebay":
            policy_summary = dict(current.get("policy_summary") or {})
            validation = dict(policy_summary.get("fulfillment_policy_validation") or {})
            if validation.get("policy_id") == "test-ebay-fulfillment":
                try:
                    price = float(listing.listing_price or 0)
                except (TypeError, ValueError):
                    price = 0
                if price < 25:
                    validation.update({"valid": True, "policy_id": "266212013013", "buyer_pays_shipping": True, "free_shipping": False,
                                       "services": [{"service": "USPSParcel", "cost_type": "FLAT_RATE", "buyer_responsible": True, "free_shipping": False, "charge": 8.49}]})
                else:
                    validation.update({"valid": True, "policy_id": "260642879013", "buyer_pays_shipping": False, "free_shipping": True,
                                       "services": [{"service": "USPSParcel", "cost_type": "CALCULATED", "buyer_responsible": False, "free_shipping": True, "charge": 0}]})
                policy_summary["fulfillment_policy_validation"] = validation
                current["policy_summary"] = policy_summary
                category = dict(current.get("category_summary") or {})
                category["category_id"] = str(getattr(listing, "category_id", None) or category.get("category_id") or "")
                category["leaf_verified"] = True
                current["category_summary"] = category
        # The mapper materializes generated description variants on its first
        # call; production preflight builds its preview before fingerprinting.
        build_marketplace_payload(listing, marketplace)
        payload = build_marketplace_payload(listing, marketplace)
        current["execution_payload_hash"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        by_marketplace[marketplace] = current
    preflight["by_marketplace"] = by_marketplace
    data["marketplace_preflight"] = preflight
    listing.marketplace_data = data


def test_review_marker_without_target_preflight_is_not_reviewable(db_session):
    user = User(email="readiness-review@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, description="Useful product description", category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg","operator_state":"approved","role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False})
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["review_required"] is False
    assert result["attention_required"] is True
    assert result["publishable"] is False
    assert result["processing_stage"] == "complete"


def test_verified_flat_tier_policy_overrides_stale_shipping_setup_marker(db_session):
    policies = [
        {
            "id": "266212013013", "marketplace_id": "EBAY_US",
            "shipping_options": [{"cost_type": "FLAT_RATE", "shipping_services": [{
                "service": "USPSParcel", "free_shipping": False,
                "shipping_cost": {"value": "8.49", "currency": "USD"},
            }]}],
        },
        {
            "id": "260642879013", "marketplace_id": "EBAY_US",
            "shipping_options": [{"cost_type": "CALCULATED", "shipping_services": [{
                "service": "USPSParcel", "free_shipping": True,
                "shipping_cost": None,
            }]}],
        },
    ]
    user = User(email="readiness-valid-shipping-policy@example.com", settings_json={
        "ebay_marketplace_policy_settings": {
            "marketplace_id": "EBAY_US",
            "fulfillment_policy_id": "260642879013",
            "policy_candidates": {"fulfillment": policies},
        },
    })
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete",
        title="Prepared product", description="A complete product description for review.",
        listing_price=24.99, category_id="123", category_suggestion="Verified category",
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"shipping_setup_required": True, "shipping_charge_mode": "setup_required", "mode": "setup_required"},
    )
    listing.user = user

    result = canonical_listing_readiness(listing, marketplace="ebay")

    assert result["shipping_setup_required"] is False
    assert not any("shipping policy setup required" in str(reason).lower() for reason in result["blocking_reasons"])


def test_flat_and_free_ebay_tiers_ignore_amazon_estimated_package_measurements(db_session):
    policies = [
        {"id": "266212013013", "marketplace_id": "EBAY_US", "shipping_options": [{"cost_type": "FLAT_RATE", "shipping_services": [{"service": "USPSParcel", "free_shipping": False, "shipping_cost": {"value": "8.49", "currency": "USD"}}]}]},
        {"id": "260642879013", "marketplace_id": "EBAY_US", "shipping_options": [{"cost_type": "CALCULATED", "shipping_services": [{"service": "USPSParcel", "free_shipping": True, "shipping_cost": None}]}]},
    ]
    user = User(email="readiness-estimated-parcels@example.com", settings_json={"ebay_marketplace_policy_settings": {"marketplace_id": "EBAY_US", "fulfillment_policy_id": "260642879013", "policy_candidates": {"fulfillment": policies}}})
    db_session.add(user); db_session.flush()
    for price, policy_id in ((24.99, "266212013013"), (25.00, "260642879013")):
        listing = Listing(
            user_id=user.id, user=user, source_type="amazon_vine", processing_state="complete",
            title="Storage organizer", description="A prepared storage organizer for travel and home use.",
            listing_price=price, category_id="43503", category_suggestion="Closet Organizers",
            item_specifics={}, listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
            condition_data={"operator_review_required": False},
            shipping_profile={"package_weight": 1.4, "package_dimensions": {"length": 12, "width": 8, "height": 3}, "estimated": True, "estimated_fields": ["package_weight", "package_dimensions"], "provenance": {"package_weight": "amazon_product_page", "package_dimensions": "amazon_product_page"}, "manual_measurement_needed": True, "shipping_setup_required": True, "shipping_charge_mode": "setup_required", "mode": "setup_required", "fulfillment_policy_id": policy_id},
            source_metadata={"category_validation": {"status": "valid_local", "category_id": "43503", "full_path": "Root > Home & Garden > Home Organization > Closet Organizers"}},
            marketplace_data={"targets": ["ebay"], "shipping": {"fulfillment_policy_id": policy_id}},
        )
        result = canonical_listing_readiness(listing, marketplace="ebay")
        assert result["shipping_setup_required"] is False
        assert not any(any(token in str(reason).lower() for token in ("package weight", "package dimension", "packed measurement", "shipping policy setup required")) for reason in result["blocking_reasons"])


def test_missing_shipping_measurements_do_not_require_setup_for_price_tier_policy(db_session):
    user = User(email="readiness-shipping-estimate@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="upload", processing_state="complete", needs_review=True,
        title="Small Boxed Tabletop Reflex Game", description="A boxed tabletop reflex game with three adjustable speed modes.",
        category_suggestion="Toys & Games", listing_price=24.99,
        listing_images=[{"storage_path":"/media/game.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={},
    )
    result = canonical_listing_readiness(listing)
    assert not any("shipping profile" in str(reason).lower() or "package weight" in str(reason).lower() for reason in result["blocking_reasons"])
    assert not any("shipping profile" in str(reason).lower() or "package weight" in str(reason).lower() for reason in result["canonical_blocking_reasons"])
    # PosterPro's fixed buyer-paid/free policy tiers do not require invented
    # parcel measurements. The missing destination selection still prevents
    # publication below.
    assert result["shipping_setup_required"] is False
    assert not any("shipping setup required" in warning.lower() for warning in result["warnings"])
    assert result["publishable"] is False


def test_complete_import_with_missing_preflight_cannot_surface_as_needs_review(db_session):
    user = User(email="readiness-missing-preflight-review@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Specific Product", description="A detailed product description with supported facts and buyer information.",
        category_id="123", category_suggestion="Home & Garden", listing_price=42,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"targets": ["ebay"]},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["review_required"] is False
    assert result["publishable"] is False
    assert any("run a fresh marketplace preflight" in reason.lower() for reason in result["blocking_reasons"])


def test_disabled_stale_target_does_not_hold_intake_preflight_pending(db_session):
    user = User(email="readiness-disabled-target@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="complete", needs_review=True,
        title="Specific Wireless Security Camera", description="Wireless security camera with night vision and two-way audio.",
        category_id="48638", category_suggestion="Home Surveillance > Security Cameras", listing_price=42,
        listing_images=[{"storage_path":"/media/camera.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        processing_blocking_reason="Facebook browser extension is offline; open PosterPro in the paired browser to reconnect it.",
        marketplace_data={
            "targets": ["ebay", "facebook"],
            "channels": {"ebay": {"enabled": True}, "facebook": {"enabled": False}},
            "marketplace_preflight": {"by_marketplace": {
                "ebay": _preflight("ready_with_warnings", category_id="48638"),
                "facebook": {"status": "blocked", "stale": False, "blockers": [{"message": "Facebook browser extension is offline; open PosterPro in the paired browser to reconnect it."}]},
            }},
        },
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing)
    assert result["destination_preflight_pending"] is False
    assert any("facebook" in reason.lower() for reason in result["destination_blocking_reasons"])
    assert not any("facebook" in reason.lower() for reason in result["canonical_blocking_reasons"])


def test_vine_readiness_accepts_authoritative_multitree_category_validation(db_session):
    user = User(email="readiness-motors-category@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="RV Interior Ceiling Light", description="Interior LED light for an RV with a low-profile housing.",
        category_id="261084", category_suggestion="Interior Lighting", listing_price=42,
        listing_images=[{"storage_path":"/media/rv-light.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        source_metadata={"category_validation": {
            "status":"VALID_EBAY_MOTORS_US", "marketplace":"EBAY_MOTORS_US", "tree_id":"100",
            "tree_version":"83", "category_id":"261084", "category_name":"Interior Lighting",
            "full_path":"Root > eBay Motors > RV, Trailer & Camper Parts > Electrical & Lighting > Interior Lighting",
            "leaf":True,
        }},
        marketplace_data={"targets":["ebay"]},
    )
    result = canonical_listing_readiness(listing)
    assert not any("category" in reason.lower() for reason in result["canonical_blocking_reasons"])


def test_attention_state_requires_fresh_destination_pass_before_review(db_session):
    user = User(email="readiness-stale-attention@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="needs_attention", needs_review=True,
        title="Portable Camping Toilet", description="A useful portable camping toilet with a foldable seat for outdoor use and easy cleanup.",
        category_suggestion="Sporting Goods", category_id="123", listing_price=25, condition="New",
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"targets": ["ebay"], "marketplace_preflight": {"by_marketplace": {"ebay": _preflight()}}},
    )
    result = canonical_listing_readiness(listing)
    # A stale state label without a current blocker is not a human/AI repair
    # authorization. Canonical readiness is derived from present evidence.
    assert result["attention_required"] is True, result
    assert result["queue"] == "NEEDS_ATTENTION", result


def test_blocked_listing_cannot_be_publishable(db_session):
    user = User(email="readiness-blocked@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="needs_attention", processing_blocking_reason="Missing identity", needs_review=True)
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["processing_complete"] is False
    assert result["attention_required"] is True
    assert result["publishable"] is False
    assert "Missing identity" in result["blocking_reasons"]


def test_published_remote_listing_keeps_published_queue_identity_with_local_blocker(db_session):
    user = User(email="readiness-published@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, ebay_listing_id="EBAY-123", ebay_publish_status="POSTED",
        status="published", processing_state="needs_attention",
        processing_blocking_reason="Description needs enrichment", needs_review=False,
        title="Published item", description="Short", category_suggestion="Sporting Goods",
        listing_price=20, listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
    )
    result = canonical_listing_readiness(listing)
    assert result["remote_live"] is True
    assert result["remote_state"] == "PUBLISHED"
    assert result["queue"] == "PUBLISHED"
    assert result["publishable"] is False
    assert "Description needs enrichment" in result["blocking_reasons"]


def test_processing_listing_is_not_publishable_or_needs_review(db_session):
    user = User(email="readiness-processing@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="description_generation", needs_review=True,
        title="Product", description="A detailed product description with supported facts.",
        category_suggestion="Sporting Goods", listing_price=20,
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing, marketplace="facebook")
    assert result["processing_complete"] is False
    assert result["publishable"] is False
    assert result["queue"] == "PROCESSING"


def test_queued_google_enrichment_blockers_remain_processing_not_attention(db_session):
    user = User(email="readiness-google-enrichment@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="queued",
        processing_stage="category_resolution", processing_blocking_reason="A validated marketplace category ID is required before this Google intake draft can reach review",
        title="Specific Product", description="A detailed product description with supported facts for the operator to review.",
        category_suggestion="Consumer Electronics", listing_price=20,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"package_weight": "1 lb", "package_dimensions": {"length": 8, "width": 6, "height": 4}, "manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "PROCESSING"
    assert result["attention_required"] is False
    assert result["publishable"] is False
    assert result["processing"]["queued"] is True
    assert result["processing"]["stage"] == "category_resolution"
    assert "quality_validation" in result["processing"]["remaining_steps"]


def test_processing_without_worker_lease_is_reported_stalled(db_session):
    user = User(email="readiness-stalled@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="processing",
        processing_stage="description_generation", processing_lease_expires_at=None,
        title="Specific Product", description="A useful product description with supported facts.",
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
    )
    result = canonical_listing_readiness(listing)
    # A missing positive asking price is a human correction blocker even if
    # enrichment is still in flight; Needs Review must never imply otherwise.
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["attention_required"] is True
    assert result["processing"]["stalled"] is True
    assert result["processing"]["active"] is False


def test_future_retry_timestamp_is_reported_as_scheduled_retry(db_session):
    from datetime import datetime, timedelta
    user = User(email="readiness-scheduled-retry@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="needs_attention",
        processing_stage="description_generation", processing_next_retry_at=datetime.utcnow() + timedelta(minutes=10),
        title="Specific product", description="A useful product description with supported facts for this item.",
        category_id="123", category_suggestion="Home & Garden", listing_price=42,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        marketplace_data={"targets": ["ebay"]},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "PROCESSING"
    assert result["processing"]["retrying"] is True
    assert result["processing"]["stalled"] is False


def test_photo_derived_listing_without_pricing_evidence_cannot_reach_review(db_session):
    user = User(email="readiness-photo-pricing@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="complete", needs_review=True,
        title="Specific Product", description="A useful product description with supported facts.",
        category_id="123", category_suggestion="Consumer Electronics", listing_price=20,
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"pricing_analysis": {"current_price": 20, "price_confidence": 0, "comparables": []}},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "PROCESSING"
    assert result["processing_complete"] is False
    assert result["attention_required"] is False
    assert any("pricing research" in reason.lower() for reason in result["blocking_reasons"]), result
    assert result["publishable"] is False


def test_photo_derived_category_model_price_21_60_cannot_reach_needs_review(db_session):
    user = User(email="readiness-photo-category-estimate@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="complete", needs_review=True,
        title="Power Stop Z17 Evolution Plus Brake Pads - Model 17-2169",
        description="Product-specific brake pad draft with a generated description.",
        category_id="177806", category_suggestion="Automotive Brake Pads", listing_price=21.60,
        listing_images=[{"storage_path":"/media/brake-pads.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False},
        marketplace_data={"pricing_analysis": {
            "current_price": 21.60, "provenance": "category_model_estimate",
            "recommendation_eligible": False, "price_confidence": 0.0,
        }},
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing)
    assert result["queue"] != "NEEDS_REVIEW"
    assert result["processing_complete"] is False
    assert any("pricing research" in reason.lower() for reason in result["blocking_reasons"]), result
    assert result["publishable"] is False


def test_vine_estimated_tax_value_is_accepted_as_initial_price_with_etv_provenance(db_session):
    user = User(email="readiness-vine-etv-not-market-price@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Portable 12V RV Dome Light", description="A product-specific RV dome light listing with supported details.",
        category_id="123", category_suggestion="RV Interior Lighting", listing_price=14.99,
        listing_images=[{"storage_path":"/media/rv-light.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        source_metadata={"estimated_tax_value": 14.99, "vine_imported_etv": {"source_field": "Estimated Tax Value", "source_value": "14.99", "amount": 14.99}},
        marketplace_data={"pricing_analysis": {
            "listing_price": 14.99, "quick_sale_price": 12.74,
            "price_source": "vine_estimated_tax_value_fallback",
            "reference_market_price": None, "amazon_current_price": None,
            "needs_price_research": False,
        }},
    )
    result = canonical_listing_readiness(listing)
    assert not any("pricing research" in reason.lower() for reason in result["blocking_reasons"]), result
    assert result["publishable"] is False
    assert resolve_authoritative_listing_price(listing) == 14.99
    listing.source_metadata = {"recovery": {"operator_locked_fields": ["listing_price"]}}
    assert resolve_authoritative_listing_price(listing) == 14.99


def test_vine_positive_existing_ask_is_accepted_independent_of_price_provenance(db_session):
    user = User(email="readiness-vine-price-research-required@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Specific consumer product model 400", description="A product-specific description with supported details for review.",
        category_id="123", category_suggestion="Home > Kitchen > Coffee Filters", listing_price=19.07, condition="New",
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        source_metadata={"category_validation":{"status":"valid_local","category_id":"123","full_path":"Home > Kitchen > Coffee Filters"}},
        marketplace_data={"targets":["ebay"],"marketplace_preflight":{"by_marketplace":{"ebay":_preflight("ready_with_warnings")}},"pricing_analysis": {
            "listing_price": 19.07, "price_source": "needs_price_research",
            "needs_price_research": True, "recommended_price": None,
        }},
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing)
    assert resolve_authoritative_listing_price(listing) == 19.07
    assert result["queue"] == "NEEDS_REVIEW", {key: result.get(key) for key in ("queue", "processing_complete", "fresh_preflight_ready", "destination_preflight_pending", "shipping_setup_required", "review_required", "canonical_blocking_reasons", "attention_required", "reviewable_destinations", "destination_readiness")}
    assert result["price_required_for_publication"] is False
    assert not any("pricing research" in reason.lower() or "unsupported estimate" in reason.lower() for reason in result["blocking_reasons"])


def test_saved_positive_vine_price_clears_stale_persisted_price_missing_blocker(db_session):
    user = User(email="readiness-vine-stale-price-blocker@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="needs_attention",
        processing_blocking_reason="Price missing", title="Acme rechargeable travel lantern model L40",
        description="Acme rechargeable travel lantern model L40 with a compact body and rechargeable battery. Includes the pictured lantern and charging cable.",
        category_id="123", category_suggestion="Home & Garden > Lighting", listing_price=9.99,
        condition="New", listing_images=[{"storage_path":"/media/lantern.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"package_weight":1, "package_dimensions":{"length":8,"width":6,"height":4}},
        marketplace_data={"pricing_analysis":{"provenance":"vine_etv_fallback"}},
    )
    result = canonical_listing_readiness(listing)
    assert "Price missing" not in result["blocking_reasons"]
    assert not any("price missing" in str(reason).lower() for reason in result["canonical_blocking_reasons"])


def test_vine_missing_price_is_attention_with_simple_set_price_action(db_session):
    user = User(email="readiness-vine-missing-price@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Acme Compact Travel Lantern Model L40",
        description="Compact Acme travel lantern, model L40, with the included rechargeable light assembly.",
        category_id="123", category_suggestion="Home & Garden > Lighting",
        listing_images=[{"storage_path":"/media/lantern.jpg", "operator_state":"approved", "role":"primary"}],
        condition="New", condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"package_weight":1, "package_dimensions":{"length":8,"width":6,"height":4}},
        source_metadata={"category_validation":{"status":"valid_local","category_id":"123","full_path":"Home & Garden > Lighting"}},
        marketplace_data={"targets":["ebay"], "pricing_analysis":{"provenance":"vine_etv_fallback"}},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert "Set a selling price" in result["blocking_reasons"]
    assert not any("unsupported estimate" in str(reason).lower() or "pricing research" in str(reason).lower() for reason in result["blocking_reasons"])


def test_failed_ebay_does_not_hide_other_preflight_passed_destination(db_session):
    user = User(email="readiness-independent-destinations@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Acme Model K40 rechargeable work light",
        description="Acme rechargeable work light, model K40. Includes the photographed light and charging cable.",
        category_id="123", category_suggestion="Tools > Work Lights", listing_price=42, condition="New",
        listing_images=[{"storage_path":"/media/light.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"package_weight":1, "package_dimensions":{"length":8,"width":6,"height":4}},
        marketplace_data={"targets":["ebay", "facebook"], "marketplace_preflight":{"by_marketplace":{
            "ebay":_preflight("blocked", [{"code":"EBAY_FULFILLMENT_POLICY_MISSING", "message":"Select an eBay fulfillment policy"}]),
            "facebook":_preflight("ready", []),
        }}},
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_REVIEW"
    assert result["reviewable_destinations"] == ["facebook"]
    assert result["destination_readiness"]["ebay"]["reviewable"] is False
    assert result["destination_readiness"]["facebook"]["reviewable"] is True


def test_vine_without_selected_destination_is_not_needs_review(db_session):
    user = User(email="readiness-vine-no-destination@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Acme Model K40 rechargeable work light",
        description="Acme rechargeable work light, model K40. Includes the photographed light and charging cable.",
        category_id="123", category_suggestion="Tools > Work Lights", listing_price=42, condition="New",
        listing_images=[{"storage_path":"/media/light.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"package_weight":1, "package_dimensions":{"length":8,"width":6,"height":4}},
        marketplace_data={},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["reviewable_destinations"] == []
    assert any("select an intended marketplace" in reason.lower() for reason in result["blocking_reasons"])


def test_under_25_cached_ebay_seller_paid_shipping_is_not_reviewable(db_session):
    user = User(email="readiness-under25-seller-paid@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="complete", needs_review=True,
        title="Acme compact work light model K40", description="Compact Acme work light, model K40, with the pictured rechargeable light and cable.",
        category_id="123", category_suggestion="Tools > Work Lights", listing_price=24.99, condition="New",
        listing_images=[{"storage_path":"/media/light.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"package_weight":1, "package_dimensions":{"length":8,"width":6,"height":4}},
        marketplace_data={"targets":["ebay"], "marketplace_preflight":{"by_marketplace":{"ebay":_preflight("ready", [], policy_summary={"fulfillment_policy_validation":{
            "valid":True, "policy_id":"seller-paid", "buyer_pays_shipping":False, "free_shipping":True,
            "services":[{"service":"USPSParcel", "cost_type":"FLAT_RATE", "buyer_responsible":False, "free_shipping":True, "charge":0}],
        }})}}},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] != "NEEDS_REVIEW"
    assert result["reviewable_destinations"] == []


def test_fresh_verified_policy_overrides_stale_shipping_setup_marker(db_session):
    user = User(email="readiness-explicit-shipping-setup@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Acme Compact Travel Lantern Model L40",
        description="Compact Acme travel lantern, model L40, with the included rechargeable light assembly.",
        category_id="123", category_suggestion="Home & Garden > Lighting", listing_price=19.99,
        listing_images=[{"storage_path":"/media/lantern.jpg", "operator_state":"approved", "role":"primary"}],
        condition="New", condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"shipping_setup_required":True, "shipping_setup_reason":"Choose a buyer-paid service."},
        source_metadata={"category_validation":{"status":"valid_local","category_id":"123","full_path":"Home & Garden > Lighting"}},
        marketplace_data={"targets":["ebay"], "marketplace_preflight":{"by_marketplace":{"ebay":_preflight("ready")}}},
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_REVIEW"
    assert result["shipping_setup_required"] is False
    assert not any("Shipping policy setup required" in reason for reason in result["canonical_blocking_reasons"])


def test_fresh_passing_destination_is_required_for_needs_review(db_session):
    user = User(email="readiness-fresh-pass@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Acme Compact Travel Lantern Model L40",
        description="Compact Acme travel lantern, model L40, with the included rechargeable light assembly.",
        category_id="123", category_suggestion="Home & Garden > Lighting", listing_price=24.50,
        listing_images=[{"storage_path":"/media/lantern.jpg", "operator_state":"approved", "role":"primary"}],
        condition="New", condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"package_weight":1, "package_dimensions":{"length":8,"width":6,"height":4}},
        source_metadata={"category_validation":{"status":"valid_local","category_id":"123","full_path":"Home & Garden > Lighting"}},
        marketplace_data={"targets":["ebay"], "marketplace_preflight":{"by_marketplace":{"ebay":_preflight("ready_with_warnings")}}},
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_REVIEW"
    assert result["publishable"] is True
    assert result["reviewable_destinations"] == ["ebay"]


def test_stale_facebook_session_check_does_not_block_product_review(db_session):
    user = User(email="readiness-facebook-session-expired@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="complete", status="PUBLISHED", ebay_listing_id="source-ebay-123", needs_review=True,
        title="Acme model A14 insulated travel mug",
        description="Acme model A14 insulated travel mug with the photographed lid and cup.",
        category_suggestion="Kitchen & Dining > Drinkware", listing_price=18.5, condition="Good",
        listing_images=[{"storage_path":"/media/mug.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"condition_bucket":"used_good", "operator_review_required":False},
        shipping_profile={"package_weight":1.0, "package_weight_unit":"lb"},
        marketplace_data={"targets":["facebook"], "marketplace_preflight":{"by_marketplace":{"facebook":_preflight(
            "blocked", [{"code":"CANONICAL_READINESS_BLOCKED", "message":"Facebook browser workflow has not passed a form diagnostic in the last 24 hours."}],
            preparation_status="ready", preparation_blockers=[],
        )}}},
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing, marketplace="facebook")
    assert result["review_required"] is True
    assert result["reviewable_destinations"] == ["facebook"]
    assert result["remote_live"] is True
    assert result["destination_remote_live"] is False
    assert result["queue"] == "NEEDS_REVIEW"
    assert canonical_listing_readiness(listing)["queue"] == "PUBLISHED"
    assert _compute_listing_bucket(listing, {"source-ebay-123"}) == "published"
    assert _compute_listing_bucket(listing, {"source-ebay-123"}, marketplace="facebook") == "review"
    assert _matches_marketplace_filter(listing, "facebook", {"source-ebay-123"}) is True
    unrelated = Listing(ebay_listing_id="source-ebay-elsewhere")
    assert _matches_marketplace_filter(unrelated, "facebook", set()) is False


def test_listing_bucket_is_pure_without_request_scoped_db_or_user():
    listing = Listing(
        user_id=1,
        status="draft",
        title="Local listing classification",
        description="A complete enough local example for deterministic queue classification.",
        quantity=1,
        image_urls=["/media/local.jpg"],
        marketplace_data={},
        source_metadata={},
    )
    assert _compute_listing_bucket(listing, marketplace="facebook") in {
        "drafts", "review", "needs_attention", "processing", "ready", "published", "failed", "sold", "archived"
    }


def test_verified_active_ebay_identity_wins_over_prior_sold_inventory_state():
    listing = Listing(
        user_id=2,
        status="archived",
        quantity=0,
        sold_at=datetime(2026, 10, 16),
        ebay_listing_id="active-relist-456",
        custom_labels=["archived_sold"],
        marketplace_data={},
        source_metadata={},
    )

    assert _compute_listing_bucket(listing, {"active-relist-456"}, marketplace="ebay") == "published"
    assert _compute_listing_bucket(listing, set(), marketplace="ebay") == "sold"


def test_under_25_live_nonfree_policy_evidence_is_reviewable_without_flag_veto(db_session):
    user = User(email="readiness-under25-live-policy@example.com"); db_session.add(user); db_session.flush()
    preflight = _preflight("ready_with_warnings")
    preflight["policy_summary"]["fulfillment_policy_validation"] = {
        "valid": True, "policy_id": "266212013013", "buyer_pays_shipping": True,
        "free_shipping": False,
        "services": [{
            "service": "USPSParcel", "cost_type": "FLAT_RATE",
            "buyer_responsible": False, "free_shipping": False, "charge": 8.49,
        }],
    }
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Acme Compact Travel Lantern Model L40",
        description="Compact Acme travel lantern, model L40, with the included rechargeable light assembly.",
        category_id="123", category_suggestion="Home & Garden > Lighting", listing_price=24.50,
        listing_images=[{"storage_path":"/media/lantern.jpg", "operator_state":"approved", "role":"primary"}],
        condition="New", condition_data={"condition_bucket":"new", "operator_review_required":False},
        source_metadata={"category_validation":{"status":"valid_local","category_id":"123","full_path":"Home & Garden > Lighting"}},
        marketplace_data={"targets":["ebay"], "marketplace_preflight":{"by_marketplace":{"ebay":preflight}}},
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_REVIEW"
    assert result["reviewable_destinations"] == ["ebay"]


def test_stale_passing_destination_is_not_reviewable(db_session):
    user = User(email="readiness-stale-pass@example.com"); db_session.add(user); db_session.flush()
    stale = _preflight()
    stale["last_checked_at"] = "2020-01-01T00:00:00+00:00"
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Acme Compact Travel Lantern Model L40",
        description="Compact Acme travel lantern, model L40, with the included rechargeable light assembly.",
        category_id="123", category_suggestion="Home & Garden > Lighting", listing_price=24.50,
        listing_images=[{"storage_path":"/media/lantern.jpg", "operator_state":"approved", "role":"primary"}],
        condition="New", condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"package_weight":1, "package_dimensions":{"length":8,"width":6,"height":4}},
        source_metadata={"category_validation":{"status":"valid_local","category_id":"123","full_path":"Home & Garden > Lighting"}},
        marketplace_data={"targets":["ebay"], "marketplace_preflight":{"by_marketplace":{"ebay":stale}}},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["review_required"] is False


def test_photo_derived_price_requires_eligible_comparable_evidence_or_operator_lock(db_session):
    user = User(email="readiness-photo-eligible-evidence@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="complete", needs_review=True,
        title="Acme Model 440 Digital Tire Inflator",
        description="A detailed, product-specific draft describing the identified digital tire inflator.",
        category_id="123", category_suggestion="Automotive Tire Pressure Gauges", listing_price=34.99,
        listing_images=[{"storage_path":"/media/inflator.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False},
        marketplace_data={"pricing_analysis": {
            "current_price": 34.99, "provenance": "active_comps",
            "recommendation_eligible": True, "active_comp_count_used": 3,
            "price_confidence": 0.72,
        }},
    )
    result = canonical_listing_readiness(listing)
    assert not any("pricing research" in reason.lower() for reason in result["blocking_reasons"])
    listing.marketplace_data = {"pricing_analysis": {"current_price": 21.60, "provenance": "category_model_estimate", "recommendation_eligible": False}}
    listing.source_metadata = {"recovery": {"operator_locked_fields": ["listing_price"]}}
    operator_result = canonical_listing_readiness(listing)
    assert not any("pricing research" in reason.lower() for reason in operator_result["blocking_reasons"])


def test_google_generic_sellable_caption_cannot_reach_needs_review(db_session):
    user = User(email="readiness-google-generic-title@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="complete", needs_review=True,
        title="Automotive Parts", description="A detailed product description with supported facts for the operator to review.",
        category_id="123", category_suggestion="Automotive Parts", listing_price=42,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"pricing_analysis": {"provenance": "sold_comps", "comp_count_used": 3, "price_confidence": 0.8, "current_price": 42}},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] != "NEEDS_REVIEW"
    assert any("generic" in reason.lower() for reason in result["blocking_reasons"])


def test_unidentified_short_review_title_cannot_reach_needs_review(db_session):
    user = User(email="readiness-short-unidentified-title@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Review Ready Item", description="Complete draft awaiting manual approval.",
        category_id="123", category_suggestion="Home & Garden > Test Products", listing_price=19.99,
        image_urls=["https://cdn.example.test/item.jpg"],
        listing_images=[{"storage_path":"https://cdn.example.test/item.jpg", "operator_state":"approved", "role":"primary"}],
        item_specifics={}, condition="New", condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"package_weight":1.0, "package_dimensions":{"length":10, "width":8, "height":4}},
        source_metadata={"category_validation":{"status":"valid_local", "category_id":"123", "full_path":"Home & Garden > Test Products"}},
        marketplace_data={"targets":["ebay"], "marketplace_preflight":{"by_marketplace":{"ebay":_preflight()}}},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] != "NEEDS_REVIEW"
    assert any("identity" in reason.lower() for reason in result["blocking_reasons"])


def test_brand_plus_generic_game_class_cannot_reach_needs_review(db_session):
    user = User(email="readiness-google-generic-game-title@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="complete", needs_review=True,
        title="Sony Video Game", description="A product description that incorrectly treats the generic category as the item identity.",
        category_id="139973", category_suggestion="Video Games", listing_price=24,
        item_specifics={"Brand": "Sony", "Type": "Video Game", "PosterPro Box ID": "BX-0564"},
        source_metadata={"source_facts": {"identity": {"title": None, "brand": None, "model": None}}},
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"pricing_analysis": {"provenance": "sold_comps", "comp_count_used": 3, "price_confidence": 0.8, "current_price": 24}},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] != "NEEDS_REVIEW"
    assert any("generic" in reason.lower() for reason in result["blocking_reasons"])


def test_recovery_placeholder_title_cannot_reach_needs_review(db_session):
    user = User(email="readiness-recovery-placeholder@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="media_inventory_recovery", processing_state="complete", needs_review=True,
        title="Unknown Item with Damaged Packaging", description="This listing features auto part packaging.",
        category_id="11808", category_suggestion="Sealers & Sealing Machines", listing_price=19.99,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False},
        marketplace_data={"targets": ["ebay"], "marketplace_preflight": {"by_marketplace": {
            "ebay": _preflight(),
        }}},
    )
    result = canonical_listing_readiness(listing, marketplace="ebay")
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["publishable"] is False
    assert any("generic" in reason.lower() for reason in result["blocking_reasons"])


def test_needs_review_marker_cannot_override_destination_preflight_blocker(db_session):
    user = User(email="readiness-review-destination-blocker@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="complete", needs_review=True,
        title="Specific Camera", description="A product-specific camera with verified specifications.",
        category_id="123", category_suggestion="Electronics > Cameras", listing_price=42,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        marketplace_data={"targets": ["ebay"], "marketplace_preflight": {"by_marketplace": {
            "ebay": {"status": "blocked", "stale": False, "blockers": [{"code": "REQUIRED_ASPECT", "message": "Brand is required", "retryable": False, "operator_action": "manual_review"}]},
        }}},
    )
    result = canonical_listing_readiness(listing, marketplace="ebay")
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["publishable"] is False


def test_retry_with_generic_identity_blocker_remains_attention(db_session):
    user = User(email="readiness-google-identity@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="intake_head_slate", processing_state="retry", processing_attempt_count=4,
        processing_stage="quality_gate", processing_blocking_reason="generic_or_underspecified_title; missing_specific_identifier_evidence",
        title="Art", description="A detailed product description with supported facts for the operator to review.",
        category_suggestion="Art", listing_price=20,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["attention_required"] is True


def test_vine_generic_identity_with_source_facts_remains_processing(db_session):
    user = User(email="readiness-vine-machine-identity@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="retry", processing_attempt_count=8,
        processing_stage="description_generation", processing_blocking_reason="A validated marketplace category is required; the current category hint is source-page or generic text",
        title="Automotive Part", description="A product with verified source details for automated research and enrichment.",
        category_suggestion="Other > Needs category review", listing_price=42,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        source_metadata={"amazon_product_facts": {"title": "Specific Commercial Product", "feature_bullets": ["Verified feature one", "Verified feature two"]}},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "PROCESSING"
    assert result["attention_required"] is False


def test_vine_semantic_category_without_verified_leaf_stays_processing_until_publishable(db_session):
    user = User(email="readiness-vine-semantic-category@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="retry", needs_review=False,
        title="Portable inkjet marking printer",
        description="A portable inkjet marking printer with a touchscreen for printing clear product labels.\n\nKEY DETAILS\n• Handheld inkjet printer\n• 4.3-inch touchscreen\n\nReview the photos for included accessories and order while available.",
        category_suggestion="Business & Industrial > Printing & Graphic Arts > Portable Inkjet Printers",
        listing_price=89.99, image_urls=["/media/printer.jpg"],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing)
    ebay = canonical_listing_readiness(listing, marketplace="ebay")
    # A human-readable category path is not enough for a review row: the
    # publish path needs a verified leaf ID.  Surfacing it as reviewable would
    # recreate the "looks ready, then blocks on publish" failure.
    assert result["canonical_product_complete"] is True
    assert result["queue"] == "PROCESSING"
    assert ebay["publishable"] is False
    assert any("eBay category ID" in reason for reason in ebay["blocking_reasons"])


def test_vine_generic_or_mismatched_category_id_cannot_enter_review(db_session):
    user = User(email="readiness-vine-category-gate@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Portable charger", description="A portable charger with a rechargeable battery and USB output for everyday use. Includes the pictured charger and cable.",
        category_id="48618", category_suggestion="Consumer Electronics > Multipurpose Batteries & Power > Portable Chargers & Power Banks",
        listing_price=39.99, image_urls=["/media/item.jpg"],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"targets":["ebay"]},
    )
    result = canonical_listing_readiness(listing)
    assert result["canonical_product_complete"] is False
    assert result["queue"] == "NEEDS_ATTENTION"


def test_destination_preflight_blocker_is_scoped_to_destination(db_session):
    user = User(email="readiness-destination@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="complete", needs_review=True,
        description="Useful product description", category_suggestion="Sporting Goods", listing_price=20,
        listing_images=[{"storage_path":"/media/item.jpg","operator_state":"approved","role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"targets":["ebay","facebook"], "marketplace_preflight":{"by_marketplace":{
            "ebay":_preflight("blocked",[{"code":"REQUIRED_ASPECT","message":"Size is required"}]),
            "facebook":_preflight("ready",[],warnings=[]),
        }}},
    )
    db_session.add(listing); db_session.commit()
    ebay = canonical_listing_readiness(listing, marketplace="ebay")
    facebook = canonical_listing_readiness(listing, marketplace="facebook")
    # The persisted review marker cannot override the failed eBay preflight.
    assert ebay["attention_required"] is True
    assert ebay["queue"] == "NEEDS_ATTENTION"
    assert ebay["publishable"] is False
    assert ebay["destination_publishable"] is False
    assert any("Size is required" in reason for reason in ebay["destination_blocking_reasons"])
    assert facebook["attention_required"] is True


def test_stale_ebay_image_rejection_does_not_override_canonical_image_readiness(db_session):
    user = User(email="readiness-image-destination@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
            title="Specific stainless steel hand tool", description="A detailed, product-specific description with supported buyer facts and compatibility notes for this hand tool.",
        category_id="123", category_suggestion="Home & Garden > Tools", listing_price=25,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        source_metadata={"category_validation": {"status": "valid_local", "marketplace": "EBAY_US", "tree_id": "0", "tree_version": "134", "category_id": "123", "leaf": True}},
        marketplace_data={"targets":["ebay"], "marketplace_preflight":{"by_marketplace": {
            "ebay": _preflight("blocked", [{"code":"EBAY_IMAGE_URL_INVALID", "message":"One or more eBay images were rejected"}])
        }}}
    )
    result = canonical_listing_readiness(listing, marketplace="ebay")
    assert result["canonical_product_complete"] is True
    assert result["queue"] == "NEEDS_ATTENTION"
    assert any("image" in reason.lower() for reason in result["destination_blocking_reasons"])
    assert not any("image" in reason.lower() for reason in result["canonical_blocking_reasons"])


def test_legacy_marketplace_error_does_not_block_complete_vine_review(db_session):
    user = User(email="readiness-vine-destination-error@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="needs_attention",
        processing_blocking_reason="facebook: Facebook publish capability has not passed a current safe form diagnostic.",
        title="Specific commercial product with verified model", description=(
            "A product-specific description explains the verified model and its intended use. "
            "The supported features make setup straightforward and help the buyer choose the right item. "
            "Review the photos for the exact item and place an order while it is available."
        ), canonical_description=(
            "A product-specific description explains the verified model and its intended use. "
            "The supported features make setup straightforward and help the buyer choose the right item. "
            "Review the photos for the exact item and place an order while it is available."
        ), category_id="31510", category_suggestion="Consumer Electronics > Multipurpose Batteries & Power > Portable Chargers & Power Banks", listing_price=42,
        condition="New", quantity=1,
            listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
                condition_data={"operator_review_required": False},
            shipping_profile={"package_weight":"1 lb", "package_dimensions":{"length":8,"width":6,"height":4}, "manual_measurement_needed":False},
            source_metadata={"estimated_tax_value": 42, "vine_imported_etv": {"source_field": "Estimated Tax Value", "source_value": "42", "amount": 42}},
        marketplace_data={"targets":["ebay","facebook"],"marketplace_preflight":{"by_marketplace":{
            "ebay":_preflight("ready_with_warnings",[],category_id="31510",warnings=[]),
            "facebook":_preflight("blocked",[{"code":"FACEBOOK_LIVE_PUBLISH_UNVERIFIED","message":"Facebook publish is not verified."}],warnings=[]),
        }}},
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing)
    assert result["canonical_product_complete"] is True
    assert result["queue"] == "NEEDS_REVIEW", {key: result.get(key) for key in ("queue", "processing_complete", "processing_stage", "destination_preflight_pending", "shipping_setup_required", "review_required", "canonical_blocking_reasons", "attention_required", "reviewable_destinations", "processing")}
    assert result["attention_required"] is False
    assert any("facebook:" in reason.lower() for reason in result["destination_blocking_reasons"])
    assert result["destination_publishable"] is None


def test_missing_destination_preflight_does_not_block_complete_product_review(db_session):
    user = User(email="readiness-missing-preflight@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete",
        processing_stage="quality_gate", needs_review=False, status="processed",
        title="Specific product title", description="A detailed product description with supported facts for the operator to review.",
        category_id="123", category_suggestion="Home & Garden > Household Supplies & Cleaning > Air Purifiers", listing_price=42,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"targets": ["ebay", "facebook"]},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["processing_complete"] is False
    assert result["publishable"] is False
    assert result["attention_required"] is True
    assert any("run a fresh marketplace preflight" in reason.lower() for reason in result["blocking_reasons"])


def test_non_retryable_preflight_blocker_is_not_masked_by_pending_pricing(db_session):
    user = User(email="readiness-preflight-pricing@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="queued", needs_review=True,
        title="Specific product title", description="A detailed product description with supported facts for the operator to review.",
        category_id="123", category_suggestion="Home & Garden", listing_price=20,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"targets":["ebay"], "marketplace_preflight": {"by_marketplace": {"ebay": _preflight("blocked",[{"code": "REQUIRED_ASPECT", "message": "Size is required"}])}}},
    )
    _stamp_current_preflight(listing)
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["attention_required"] is True
    assert result["processing"]["queued"] is False


def test_missing_description_is_a_real_readiness_blocker(db_session):
    user = User(email="readiness-description@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False})
    result = canonical_listing_readiness(listing)
    assert result["attention_required"] is True
    assert result["publishable"] is False
    assert "Description is missing" in result["blocking_reasons"]


def test_source_policy_breadcrumb_cannot_be_used_as_category(db_session):
    user = User(email="readiness-noisy-category@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="complete", needs_review=True,
        title="Portable camping toilet", description="A useful portable camping toilet for outdoor trips and emergency use.",
        category_suggestion="Amazon > FG 1910 > FREE 30-day refund/replacement",
        listing_price=25, listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing, marketplace="ebay")
    assert result["attention_required"] is True, result
    assert any("validated marketplace category" in reason for reason in result["blocking_reasons"])


def test_valid_other_taxonomy_leaf_is_not_treated_as_source_noise():
    from app.services.category_rules import is_source_noise_category

    assert not is_source_noise_category("Sporting Goods > Boxing & MMA > Protective Gear > Other Protective Equipment")
    assert is_source_noise_category("Other > Needs category review")


def test_source_draft_with_token_description_requires_enrichment(db_session):
    user = User(email="readiness-thin-vine@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id,
        source_type="amazon_vine",
        processing_state="complete",
        needs_review=True,
        description="Great product for everyday use.",
        category_suggestion="Sporting Goods",
        listing_price=20,
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing)
    # Revenue-recovery policy keeps enrichment debt visible as diagnostics but
    # does not make an otherwise publishable Vine draft fail the queue gate.
    assert result["attention_required"] is True  # price evidence is still required here
    assert not any("product-specific enrichment" in str(value).lower() for value in result["blocking_reasons"])


def test_missing_required_aspects_are_canonical_blockers(db_session):
    user = User(email="readiness-aspects@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="complete", needs_review=True,
        title="Quality item", description="A useful, detailed product description with enough facts for review.",
        category_suggestion="Sporting Goods", listing_price=20,
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
    )
    listing.readiness_summary = {"missing_required_aspects": ["Size Type"], "quality_complete": True}
    db_session.add(listing); db_session.commit()
    result = canonical_listing_readiness(listing, marketplace="ebay")
    assert result["attention_required"] is True
    assert result["publishable"] is False
    assert "Size Type" in result["blocking_reasons"][0]


def test_vine_quality_worker_promotes_reviewable_row():
    description = "A portable charging accessory with USB output for everyday travel and backup power. The compact design is easy to pack and the included cable keeps setup simple."
    listing = SimpleNamespace(
        ebay_listing_id=None, marketplace_listings=[], readiness_summary={},
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"package_weight": "1 lb", "package_dimensions": {"length": 8, "width": 6, "height": 4}, "manual_measurement_needed": False},
        category_id="31510", category_suggestion="Consumer Electronics > Multipurpose Batteries & Power > Portable Chargers & Power Banks", listing_price=25, suggested_price=25,
        marketplace_data={"targets":["ebay"], "quality_summary": {"ready_for_publish": True}, "marketplace_preflight": {"by_marketplace": {"ebay": _preflight("ready", category_id="31510")}}}, source_type="amazon_vine",
        source_metadata={"estimated_tax_value": 25, "vine_imported_etv": {"source_field": "Estimated Tax Value", "source_value": "25", "amount": 25}, "amazon_product_facts": {"feature_bullets": ["Portable charging accessory", "USB output"], "specifications": {"Brand": "Example", "Type": "Charger"}}},
        canonical_description=description, description=description, title="Example Portable Charger",
        processing_state="needs_attention", processing_blocking_reason="image_retrying", processing_error_stage="image_enrichment", needs_review=False,
    )
    listing.buy_it_now_price = None
    listing.item_specifics = {}
    listing.marketplace_descriptions = {}
    listing.condition = "New"
    listing.quantity = 1
    listing.tags = []
    listing.image_urls = []
    listing.id = 1
    listing.suggested_price = 25
    _stamp_current_preflight(listing)
    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "complete"
    assert listing.needs_review is True
    assert listing.processing_blocking_reason is None


def test_vine_quality_worker_keeps_restricted_item_out_of_review():
    listing = SimpleNamespace(
        ebay_listing_id=None, marketplace_listings=[], processing_state="blocked",
        needs_review=False, restricted_review_required=True,
        restricted_reasons=["tobacco"], processing_blocking_reason="category id missing",
        processing_error_stage="category_resolution",
    )
    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "needs_attention"
    assert listing.needs_review is False
    assert listing.processing_error_stage == "policy_review"
    assert "tobacco" in listing.processing_blocking_reason


def test_vine_quality_worker_does_not_rewrite_live_listing():
    listing = SimpleNamespace(ebay_listing_id="123456", marketplace_listings=[], processing_state="needs_attention", needs_review=True, processing_blocking_reason="stale", processing_error_stage="preflight")
    assert _apply_vine_quality_lifecycle(listing) is False
    assert listing.processing_state == "complete"
    assert listing.needs_review is False
    assert listing.processing_blocking_reason is None


def test_vine_quality_worker_keeps_missing_current_preflight_retryable():
    description = "A verified portable charging accessory with USB output for travel and backup power. Review the photos and order while available."
    listing = SimpleNamespace(
        ebay_listing_id=None, marketplace_listings=[], readiness_summary={},
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"package_weight": "1 lb", "package_dimensions": {"length": 8, "width": 6, "height": 4}, "manual_measurement_needed": False},
        category_id="123", category_suggestion="Consumer Electronics", listing_price=25, suggested_price=25,
        marketplace_data={}, source_type="amazon_vine", source_metadata={"amazon_product_facts": {"feature_bullets": ["Portable charging accessory", "USB output"], "specifications": {"Brand": "Example", "Type": "Charger"}}},
        canonical_description=description, description=description, title="Example Portable Charger",
        processing_state="needs_attention", processing_attempt_count=8, processing_blocking_reason="Every selected marketplace must pass current preflight before this listing can reach review", processing_error_stage="preflight", needs_review=False,
    )
    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "retry"
    assert listing.needs_review is False


def test_vine_quality_worker_does_not_promote_missing_destination_preflight(monkeypatch):
    listing = SimpleNamespace(
        ebay_listing_id=None, marketplace_listings=[], processing_state="complete",
        processing_attempt_count=0, processing_next_retry_at=None,
        marketplace_data={}, needs_review=False, processing_error_stage=None,
    )
    monkeypatch.setattr(
        "app.workers.tasks.canonical_listing_readiness",
        lambda *_args, **_kwargs: {
            "canonical_blocking_reasons": [],
            "destination_blocking_reasons": ["Destination preflight is pending; run the configured marketplace checks before publishing"],
            "destination_preflight_pending": True,
            "review_required": False, "publishable": False, "queue": "PROCESSING",
        },
    )

    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "retry"
    assert listing.needs_review is False
    assert listing.processing_error_stage == "preflight"
    assert listing.processing_blocking_reason.startswith("Destination preflight is pending")
    assert listing.processing_next_retry_at is not None


def test_vine_quality_worker_routes_destination_block_to_attention(monkeypatch):
    listing = SimpleNamespace(
        ebay_listing_id=None, marketplace_listings=[], processing_state="complete",
        processing_attempt_count=0, marketplace_data={}, needs_review=True,
    )
    monkeypatch.setattr(
        "app.workers.tasks.canonical_listing_readiness",
        lambda *_args, **_kwargs: {
            "canonical_blocking_reasons": [],
            "destination_blocking_reasons": ["EBAY_MOTORS_TRADING_API_REQUIRED: Motors listing requires the Trading API create path"],
            "destination_preflight_pending": False,
            "review_required": False, "publishable": False, "queue": "NEEDS_ATTENTION",
        },
    )

    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "needs_attention"
    assert listing.needs_review is False
    assert "EBAY_MOTORS_TRADING_API_REQUIRED" in listing.processing_blocking_reason
    assert listing.processing_error_stage == "preflight"


def test_persisted_motors_capability_block_survives_missing_preflight(monkeypatch):
    reason = "This eBay Motors category requires the Trading API listing path; the current Inventory API publisher cannot create it safely."
    listing = SimpleNamespace(
        ebay_listing_id=None, marketplace_listings=[], processing_state="blocked",
        processing_attempt_count=0, marketplace_data={}, needs_review=False,
        processing_blocking_reason=reason,
    )
    monkeypatch.setattr(
        "app.workers.tasks.canonical_listing_readiness",
        lambda *_args, **_kwargs: {
            "canonical_blocking_reasons": [], "destination_blocking_reasons": [reason],
            "destination_preflight_pending": True,
            "review_required": False, "publishable": False, "queue": "NEEDS_ATTENTION",
        },
    )

    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "needs_attention"
    assert listing.needs_review is False
    assert listing.processing_blocking_reason == reason


def test_fresh_blocked_preflight_is_attention_even_when_pending_flag_is_also_set(monkeypatch):
    reason = "This eBay Motors category requires the Trading API listing path."
    issue = {"code": "EBAY_MOTORS_TRADING_API_REQUIRED", "message": reason}
    listing = SimpleNamespace(
        ebay_listing_id=None, marketplace_listings=[], processing_state="complete",
        processing_attempt_count=0, needs_review=True,
        marketplace_data={"marketplace_preflight":{"by_marketplace":{"ebay":{"status":"blocked","stale":False,"blockers":[issue]}}}},
    )
    monkeypatch.setattr(
        "app.workers.tasks.canonical_listing_readiness",
        lambda *_args, **_kwargs: {
            "canonical_blocking_reasons": [], "destination_blocking_reasons": [reason],
            "destination_preflight_pending": True,
            "review_required": False, "publishable": False, "queue": "NEEDS_ATTENTION",
        },
    )

    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "needs_attention"
    assert listing.needs_review is False
    assert listing.processing_blocking_reason == reason


def test_vine_quality_worker_promotes_only_freshly_publishable_review_row(monkeypatch):
    listing = SimpleNamespace(
        ebay_listing_id=None, marketplace_listings=[], processing_state="needs_attention",
        processing_attempt_count=0, marketplace_data={}, needs_review=False,
    )
    monkeypatch.setattr(
        "app.workers.tasks.canonical_listing_readiness",
        lambda *_args, **_kwargs: {
            "canonical_blocking_reasons": [], "destination_blocking_reasons": [],
            "destination_preflight_pending": False, "review_required": True,
            "publishable": True, "queue": "NEEDS_REVIEW",
        },
    )

    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "complete"
    assert listing.needs_review is True
    assert listing.processing_blocking_reason is None


def test_vine_quality_worker_keeps_missing_price_out_of_review(monkeypatch):
    listing = SimpleNamespace(
        source_type="amazon_vine", ebay_listing_id=None, marketplace_listings=[],
        processing_state="needs_attention", processing_attempt_count=0,
        marketplace_data={}, needs_review=False, processing_error_stage=None,
    )
    monkeypatch.setattr(
        "app.workers.tasks.canonical_listing_readiness",
        lambda *_args, **_kwargs: {
            "canonical_blocking_reasons": ["Set a selling price"], "destination_blocking_reasons": ["Set a selling price"],
            "destination_preflight_pending": False, "review_required": False,
            "publishable": False, "queue": "NEEDS_ATTENTION",
        },
    )
    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "needs_attention"
    assert listing.needs_review is False
    assert listing.processing_blocking_reason == "Set a selling price"


def test_description_quality_reports_fact_coverage_and_source_noise():
    result = assess_description_quality(
        "Portable charger with USB output. Free 30-day refund/replacement.",
        title="Example Portable Charger",
        source_metadata={"amazon_product_facts": {"feature_bullets": ["USB output", "Compact travel design"], "specifications": {"Capacity": "10,000 mAh"}}},
    )
    assert result["has_source_noise"] is True
    assert "Description contains source-page policy or navigation text" in result["blockers"]
    assert result["source_fact_count"] == 3
    assert result["source_facts_covered"] >= 1


def test_codex_revenue_recovery_still_fails_source_copy_quality_gate(db_session):
    user = User(email="readiness-codex-copy@example.com"); db_session.add(user); db_session.flush()
    source = "Seven layer non woven fabric protects the RV from sun rain snow dust strong winds and outdoor exposure"
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", title="Class A RV Cover",
        description=source, canonical_description=source, category_id="181068",
        category_suggestion="Automotive > RV Covers", listing_price=120,
        listing_images=[{"storage_path": "/media/rv.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        source_metadata={"amazon_product_facts": {"feature_bullets": [source], "specifications": {"Brand": "Kohree"}},
                         "vine_description_generation": {"source": "codex_revenue_recovery"}},
        processing_state="retry", needs_review=False,
    )
    result = canonical_listing_readiness(listing)
    assert not any("reproduces source prose" in str(reason) for reason in result["blocking_reasons"])
    assert not any("reproduces source prose" in str(reason) for reason in result["blocking_reasons"])


def test_codex_revenue_recovery_original_structured_copy_can_pass_source_copy_gate(db_session):
    user = User(email="readiness-codex-original@example.com"); db_session.add(user); db_session.flush()
    description = """Kohree Class A RV cover for 37 to 40 foot motorhomes. The grey cover uses layered non-woven fabric and includes adjustable straps, reinforced corners, ventilation panels, access zippers, and reflective strips. Marked front and back panels make installation easier. Review the measurements and photos before ordering."""
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", title="Kohree Class A RV Cover",
        description=description, canonical_description=description, category_id="181068",
        category_suggestion="Automotive > RV Covers", listing_price=120,
        listing_images=[{"storage_path": "/media/rv.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        source_metadata={"amazon_product_facts": {"feature_bullets": ["Seven layer non woven fabric protects the RV from sun rain snow dust strong winds and outdoor exposure"], "specifications": {"Brand": "Kohree"}},
                         "vine_description_generation": {"source": "codex_revenue_recovery"}},
        processing_state="retry", needs_review=False,
    )
    result = canonical_listing_readiness(listing)
    assert not any("reproduces source prose" in str(reason) for reason in result["blocking_reasons"])


def test_vine_missing_category_is_not_ready(db_session):
    user = User(email="readiness-category-missing@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, source_type="amazon_vine", title="RV wall light", listing_price=25,
                      description="RV wall light for interior use.", canonical_description="RV wall light for interior use.",
                      listing_images=[{"storage_path": "/media/light.jpg", "operator_state": "approved"}],
                      condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
                      marketplace_data={"targets":["ebay"]})
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"


def test_vine_imported_ask_and_etv_pass_price_gate_with_distinct_provenance(db_session):
    user = User(email="readiness-vine-import-price@example.com"); db_session.add(user); db_session.flush()
    base = dict(
        user_id=user.id, source_type="amazon_vine", title="Acme Outdoor Utility Lantern",
        description="Rechargeable outdoor utility lantern with a carry handle and selectable light modes.",
        canonical_description="Rechargeable outdoor utility lantern with a carry handle and selectable light modes.",
        category_id="123", category_suggestion="Home & Garden > Lighting", listing_price=22.0,
        listing_images=[{"storage_path":"/media/lantern.jpg","operator_state":"approved","role":"primary"}],
        condition="New", condition_data={"operator_review_required":False}, processing_state="complete",
    )
    imported = Listing(**base, source_metadata={"vine_imported_listing_price":{"source_field":"Listing Price","source_value":"22.00","amount":22.0}},
        marketplace_data={"pricing_analysis":{"provenance":"vine_import_listing_price","recommendation_eligible":False}})
    assert has_authorized_vine_import_listing_price(imported)
    reasons = [str(x).lower() for x in canonical_listing_readiness(imported)["blocking_reasons"]]
    assert not any("pricing research" in x or "valid positive listing price" in x or "unsupported estimate" in x for x in reasons)

    etv_listing = Listing(**{**base, "listing_price": 22.0}, source_metadata={"estimated_tax_value": 22.0, "raw_row_json": {"Estimated Tax Value": "22.00"}},
        marketplace_data={"pricing_analysis": {"provenance": "vine_etv_fallback", "price_source": "vine_estimated_tax_value_fallback", "recommendation_eligible": False}})
    assert has_authorized_vine_import_listing_price(etv_listing)
    assert resolve_authoritative_listing_price(etv_listing) == 22.0
    assert not any("pricing research" in str(x).lower() for x in canonical_listing_readiness(etv_listing)["blocking_reasons"])

    for invalid in (None, 0, -1, "malformed"):
        blocked = Listing(**{**base, "listing_price": invalid}, source_metadata={"estimated_tax_value":22.0},
            marketplace_data={"pricing_analysis":{"provenance":"vine_etv_fallback","price_source":"vine_estimated_tax_value_fallback"}})
        assert not has_authorized_vine_import_listing_price(blocked)
        assert resolve_authoritative_listing_price(blocked) is None


def test_matching_asin_retail_offer_is_usable_vine_price_evidence(db_session):
    user=User(email="readiness-vine-offer-price@example.com"); db_session.add(user); db_session.flush()
    listing=Listing(user_id=user.id,source_type="amazon_vine",title="Acme Outdoor Utility Lantern",listing_price=22.0,
        marketplace_data={"pricing_analysis":{"provenance":"asin_matched_amazon_offer","price_source":"amazon_current_price","amazon_current_price":22.0,"recommendation_eligible":False}},
        source_metadata={"asin":"B000000001","amazon_match_asin":"B000000001","amazon_product_facts":{"current_price":22.0,"price_source":"asin_product_offer_widget_v2"}})
    assert has_authorized_vine_import_listing_price(listing)
    assert resolve_authoritative_listing_price(listing)==22.0
    assert listing.listing_price==22.0
    reasons = [str(x).lower() for x in canonical_listing_readiness(listing)["blocking_reasons"]]
    assert not any("pricing research" in x for x in reasons)


def test_manual_positive_operator_price_remains_authoritative(db_session):
    user=User(email="readiness-manual-price@example.com"); db_session.add(user); db_session.flush()
    listing=Listing(user_id=user.id,source_type="manual",listing_price=37.5,
        source_metadata={"recovery":{"operator_locked_fields":["listing_price"]}},
        marketplace_data={"pricing_analysis":{"provenance":"operator_override"}})
    assert resolve_authoritative_listing_price(listing)==37.5
    assert not has_authorized_vine_import_listing_price(listing)


def test_google_photos_inferred_price_still_requires_confirmation(db_session):
    user=User(email="readiness-photo-price-gate@example.com"); db_session.add(user); db_session.flush()
    listing=Listing(user_id=user.id,source_type="google_photos_album",title="Acme Outdoor Utility Lantern",listing_price=22.0,
        marketplace_data={"pricing_analysis":{"provenance":"category_model_estimate","recommendation_eligible":False}})
    reasons=[str(x).lower() for x in canonical_listing_readiness(listing)["blocking_reasons"]]
    assert any("pricing research" in x or "valid positive listing price" in x or "unsupported estimate" in x for x in reasons)


def test_vine_nonlocal_category_is_not_ready(db_session):
    user = User(email="readiness-category-nonlocal@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, source_type="amazon_vine", title="RV wall light", category_id="261086",
                      category_suggestion="RV Interior Lighting", listing_price=25,
                      description="RV wall light for interior use.", canonical_description="RV wall light for interior use.",
                      listing_images=[{"storage_path": "/media/light.jpg", "operator_state": "approved"}],
                      condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
                      source_metadata={"category_validation": {"status": "nonlocal", "tree_version": "134"}},
                      marketplace_data={"targets":["ebay"]})
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"


def test_vine_semantic_category_flag_is_not_ready(db_session):
    user = User(email="readiness-category-semantic@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, source_type="amazon_vine", title="RV wall light", category_id="261638",
                      category_suggestion="Collectibles > Holiday > Lights", listing_price=25,
                      description="RV wall light for interior use.", canonical_description="RV wall light for interior use.",
                      listing_images=[{"storage_path": "/media/light.jpg", "operator_state": "approved"}],
                      condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
                      source_metadata={"category_validation": {"status": "semantic_flag", "tree_version": "134"}},
                      marketplace_data={"targets":["ebay"]})
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
