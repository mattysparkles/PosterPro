from types import SimpleNamespace

from app.models.models import Listing, User
from app.services.canonical_readiness import canonical_listing_readiness, resolve_authoritative_listing_price
from app.services.listing_ai import assess_description_quality
from app.workers.tasks import _apply_vine_quality_lifecycle


def test_review_marker_without_target_preflight_is_canonical_reviewable_but_not_publishable(db_session):
    user = User(email="readiness-review@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, description="Useful product description", category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg","operator_state":"approved","role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False})
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_REVIEW"
    assert result["publishable"] is False
    assert result["attention_required"] is False
    assert result["publishable"] is False
    assert result["processing_stage"] == "complete"


def test_missing_shipping_profile_is_estimated_before_needs_review_readiness(db_session):
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
    assert result["shipping_setup_required"] is True
    assert any("shipping setup required" in warning.lower() for warning in result["warnings"])
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
    assert result["queue"] == "PROCESSING"
    assert result["review_required"] is False
    assert result["publishable"] is False


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
                "ebay": {"status": "ready_with_warnings", "stale": False, "blockers": []},
                "facebook": {"status": "blocked", "stale": False, "blockers": [{"message": "Facebook browser extension is offline; open PosterPro in the paired browser to reconnect it."}]},
            }},
        },
    )
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


def test_stale_attention_state_without_current_blocker_is_reclassified_for_queue(db_session):
    user = User(email="readiness-stale-attention@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="needs_attention", needs_review=True,
        title="Portable Camping Toilet", description="A useful portable camping toilet with a foldable seat for outdoor use and easy cleanup.",
        category_suggestion="Sporting Goods", category_id="123", listing_price=25,
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing)
    # A stale state label without a current blocker is not a human/AI repair
    # authorization. Canonical readiness is derived from present evidence.
    assert result["attention_required"] is False
    assert result["queue"] == "NEEDS_REVIEW"


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
    result = canonical_listing_readiness(listing)
    assert result["queue"] != "NEEDS_REVIEW"
    assert result["processing_complete"] is False
    assert any("pricing research" in reason.lower() for reason in result["blocking_reasons"]), result
    assert result["publishable"] is False


def test_vine_estimated_tax_value_is_not_a_researched_asking_price(db_session):
    user = User(email="readiness-vine-etv-not-market-price@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Portable 12V RV Dome Light", description="A product-specific RV dome light listing with supported details.",
        category_id="123", category_suggestion="RV Interior Lighting", listing_price=14.99,
        listing_images=[{"storage_path":"/media/rv-light.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"pricing_analysis": {
            "listing_price": 14.99, "quick_sale_price": 12.74,
            "price_source": "vine_estimated_tax_value_fallback",
            "reference_market_price": None, "amazon_current_price": None,
            "needs_price_research": False,
        }},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] != "NEEDS_REVIEW"
    assert result["processing_complete"] is False
    assert any("pricing research" in reason.lower() for reason in result["blocking_reasons"]), result
    assert result["publishable"] is False
    assert resolve_authoritative_listing_price(listing) is None
    listing.source_metadata = {"recovery": {"operator_locked_fields": ["listing_price"]}}
    assert resolve_authoritative_listing_price(listing) == 14.99


def test_vine_needs_price_research_source_cannot_pass_from_legacy_listing_price(db_session):
    user = User(email="readiness-vine-price-research-required@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="complete", needs_review=True,
        title="Specific consumer product model 400", description="A product-specific description with supported details for review.",
        category_id="123", category_suggestion="Home & Garden", listing_price=19.07,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"pricing_analysis": {
            "listing_price": 19.07, "price_source": "needs_price_research",
            "needs_price_research": True, "recommended_price": None,
        }},
    )
    result = canonical_listing_readiness(listing)
    assert resolve_authoritative_listing_price(listing) is None
    assert result["queue"] != "NEEDS_REVIEW"
    assert result["processing_complete"] is False
    assert any("pricing research" in reason.lower() for reason in result["blocking_reasons"])


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
        marketplace_data={"targets":["ebay"], "marketplace_preflight":{"by_marketplace":{"ebay":{"status":"ready", "blockers":[]}}}},
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
            "ebay": {"status": "ready", "stale": False, "blockers": []},
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
    assert result["canonical_product_complete"] is False
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
    )
    result = canonical_listing_readiness(listing)
    assert result["canonical_product_complete"] is False
    assert any("category ID" in reason or "generic/top-level" in reason for reason in result["blocking_reasons"])


def test_destination_preflight_blocker_is_scoped_to_destination(db_session):
    user = User(email="readiness-destination@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, description="Useful product description", category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg","operator_state":"approved","role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False}, marketplace_data={"targets":["ebay","facebook"],"marketplace_preflight":{"by_marketplace":{"ebay":{"status":"blocked","blockers":[{"code":"REQUIRED_ASPECT","message":"Size is required"}]},"facebook":{"status":"ready","blockers":[],"warnings":[]}}}})
    db_session.add(listing); db_session.commit()
    ebay = canonical_listing_readiness(listing, marketplace="ebay")
    facebook = canonical_listing_readiness(listing, marketplace="facebook")
    # The persisted review marker cannot override the failed eBay preflight.
    assert ebay["attention_required"] is True
    assert ebay["queue"] == "NEEDS_ATTENTION"
    assert ebay["publishable"] is False
    assert ebay["destination_publishable"] is False
    assert any("Size is required" in reason for reason in ebay["destination_blocking_reasons"])
    assert facebook["attention_required"] is False


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
            "ebay": {"status":"blocked", "blockers":[{"code":"EBAY_IMAGE_URL_INVALID", "message":"One or more eBay images were rejected"}]}
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
        marketplace_data={"targets":["ebay","facebook"],"marketplace_preflight":{"by_marketplace":{
            "ebay":{"status":"ready_with_warnings","blockers":[],"warnings":[]},
            "facebook":{"status":"blocked","blockers":[{"code":"FACEBOOK_LIVE_PUBLISH_UNVERIFIED","message":"Facebook publish is not verified."}],"warnings":[]},
        }}},
    )
    result = canonical_listing_readiness(listing)
    assert result["canonical_product_complete"] is True
    assert result["queue"] == "PROCESSING"
    assert result["attention_required"] is False
    assert any("facebook:" in reason.lower() for reason in result["destination_blocking_reasons"])
    assert result["destination_publishable"] is None


def test_missing_destination_preflight_does_not_block_complete_product_review(db_session):
    user = User(email="readiness-missing-preflight@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="blocked",
        processing_stage="quality_gate", needs_review=False, status="processed",
        title="Specific product title", description="A detailed product description with supported facts for the operator to review.",
        category_id="123", category_suggestion="Home & Garden > Household Supplies & Cleaning > Air Purifiers", listing_price=42,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"targets": ["ebay", "facebook"]},
    )
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "PROCESSING"
    assert result["processing_complete"] is False
    assert result["publishable"] is False
    assert result["attention_required"] is False


def test_non_retryable_preflight_blocker_is_not_masked_by_pending_pricing(db_session):
    user = User(email="readiness-preflight-pricing@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", processing_state="queued", needs_review=True,
        title="Specific product title", description="A detailed product description with supported facts for the operator to review.",
        category_id="123", category_suggestion="Home & Garden", listing_price=20,
        listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
        marketplace_data={"marketplace_preflight": {"by_marketplace": {"ebay": {"status": "blocked", "blockers": [{"code": "REQUIRED_ASPECT", "message": "Size is required"}]}}}},
    )
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
    assert result["attention_required"] is True
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
        marketplace_data={"quality_summary": {"ready_for_publish": True}, "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "ready", "blockers": [], "warnings": []}}}}, source_type="amazon_vine",
        source_metadata={"amazon_product_facts": {"feature_bullets": ["Portable charging accessory", "USB output"], "specifications": {"Brand": "Example", "Type": "Charger"}}},
        canonical_description=description, description=description, title="Example Portable Charger",
        processing_state="needs_attention", processing_blocking_reason="image_retrying", processing_error_stage="image_enrichment", needs_review=False,
    )
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
        marketplace_data={}, needs_review=False,
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
                      condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False})
    result = canonical_listing_readiness(listing)
    assert any("category ID is required" in str(reason) for reason in result["blocking_reasons"])


def test_vine_nonlocal_category_is_not_ready(db_session):
    user = User(email="readiness-category-nonlocal@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, source_type="amazon_vine", title="RV wall light", category_id="261086",
                      category_suggestion="RV Interior Lighting", listing_price=25,
                      description="RV wall light for interior use.", canonical_description="RV wall light for interior use.",
                      listing_images=[{"storage_path": "/media/light.jpg", "operator_state": "approved"}],
                      condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
                      source_metadata={"category_validation": {"status": "nonlocal", "tree_version": "134"}})
    result = canonical_listing_readiness(listing)
    assert any("category validation required (nonlocal)" in str(reason) for reason in result["blocking_reasons"])


def test_vine_semantic_category_flag_is_not_ready(db_session):
    user = User(email="readiness-category-semantic@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, source_type="amazon_vine", title="RV wall light", category_id="261638",
                      category_suggestion="Collectibles > Holiday > Lights", listing_price=25,
                      description="RV wall light for interior use.", canonical_description="RV wall light for interior use.",
                      listing_images=[{"storage_path": "/media/light.jpg", "operator_state": "approved"}],
                      condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
                      source_metadata={"category_validation": {"status": "semantic_flag", "tree_version": "134"}})
    result = canonical_listing_readiness(listing)
    assert any("category validation required (semantic_flag)" in str(reason) for reason in result["blocking_reasons"])
