from app.models.models import Listing, User
from app.api.routes import _compute_listing_bucket
from app.services.vine_pricing_repair import repair_verified_vine_price_readiness


def test_repair_is_idempotent_source_scoped_and_never_changes_ask_or_approval(db_session):
    user = User(email="vine-price-repair@example.com", role="owner", is_admin=True)
    db_session.add(user); db_session.flush()
    verified = Listing(
        user_id=user.id, source_type="amazon_vine", title="Verified Vine imported ask", listing_price=22.0,
        source_metadata={
            "amazon_product_facts":{"current_price":22.0,"price_source":"asin_product_offer_widget_v2"},
            "vine_imported_listing_price":{"amount":22.0,"source_field":"Listing Price"},
        },
        marketplace_data={"pricing_analysis":{"provenance":"vine_etv_fallback","recommendation_eligible":False}},
        processing_state="needs_attention", processing_blocking_reason="PRICING_EVIDENCE_MISSING",
        needs_review=False,
    )
    etv_only = Listing(
        user_id=user.id, source_type="amazon_vine", title="ETV only", listing_price=22.0,
        source_metadata={"estimated_tax_value":22.0},
        marketplace_data={"pricing_analysis":{"provenance":"vine_etv_fallback","price_source":"vine_estimated_tax_value_fallback"}},
        processing_state="needs_attention", processing_blocking_reason="PRICING_EVIDENCE_MISSING",
        needs_review=False,
    )
    retail_only = Listing(
        user_id=user.id, source_type="amazon_vine", title="Retail reference only", listing_price=22.0,
        source_metadata={"amazon_product_facts":{"current_price":22.0,"price_source":"asin_product_offer_widget_v2"}},
        marketplace_data={"pricing_analysis":{"provenance":"amazon_current_price","price_source":"amazon_current_price","recommendation_eligible":False}},
        processing_state="needs_attention", processing_blocking_reason="PRICING_EVIDENCE_MISSING",
        needs_review=False,
    )
    db_session.add_all([verified, etv_only, retail_only]); db_session.commit()

    preview = repair_verified_vine_price_readiness(db_session, apply=False)
    assert preview["changed_records"] == 0
    assert preview["would_change_records"] == 3
    assert verified.listing_price == 22.0 and etv_only.listing_price == 22.0 and retail_only.listing_price == 22.0

    applied = repair_verified_vine_price_readiness(db_session, apply=True)
    assert applied["changed_records"] == 3
    db_session.refresh(verified); db_session.refresh(etv_only)
    assert verified.listing_price == 22.0
    assert verified.marketplace_data["pricing_analysis"]["provenance"] == "vine_etv_fallback"
    assert verified.needs_review is False  # source fixtures lack the remaining category/content readiness
    assert etv_only.marketplace_data["pricing_analysis"]["provenance"] == "vine_etv_fallback"
    assert etv_only.processing_blocking_reason is None
    db_session.refresh(retail_only)
    assert retail_only.marketplace_data["pricing_analysis"]["provenance"] == "amazon_current_price"
    assert retail_only.processing_blocking_reason is None
    assert applied["price_blocked_after"] == 0

    repeated = repair_verified_vine_price_readiness(db_session, apply=True)
    assert repeated["changed_records"] == 0


def test_repair_preserves_operator_owned_price_provenance(db_session):
    user = User(email="vine-price-repair-operator@example.com", role="owner", is_admin=True)
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", title="Operator-owned Vine price", listing_price=22.0,
        source_metadata={
            "amazon_product_facts":{"current_price":22.0,"price_source":"asin_product_offer_widget_v2"},
            "recovery":{"operator_locked_fields":["listing_price"],"field_provenance":{"listing_price":"operator_override"}},
        },
        marketplace_data={"pricing_analysis":{"provenance":"operator_override","price_source":"operator_override"}},
        processing_state="needs_attention", processing_blocking_reason="PRICING_EVIDENCE_MISSING",
        needs_review=False,
    )
    db_session.add(listing); db_session.commit()

    result = repair_verified_vine_price_readiness(db_session, apply=True)
    db_session.refresh(listing)

    assert result["changed_records"] == 1
    assert listing.listing_price == 22.0
    assert listing.marketplace_data["pricing_analysis"]["provenance"] == "operator_override"


def test_vine_missing_price_stays_attention_and_is_not_a_publish_price(db_session):
    from app.services.canonical_readiness import canonical_listing_readiness, resolve_authoritative_listing_price

    user = User(email="vine-missing-price-review@example.com", role="owner", is_admin=True)
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", status="PROCESSED", processing_state="complete",
        title="Acme Stainless Steel Pour-Over Coffee Dripper Model 204", description="A useful product description with relevant buyer details and included parts.",
        listing_price=None, suggested_price=19.99,
        category_id="cat-1", category_suggestion="Home > Kitchen",
        listing_images=[{"storage_path":"/media/vine.jpg", "operator_state":"approved", "role":"primary"}],
        condition_data={"operator_review_required":False}, shipping_profile={"manual_measurement_needed":False},
        source_metadata={
            "category_validation":{"status":"valid_local","category_id":"cat-1","full_path":"Home > Kitchen"},
            "vine_imported_listing_price":{"amount":22.0,"source_field":"Listing Price"},
        },
        marketplace_data={"targets":["ebay"], "pricing_analysis":{"provenance":"vine_etv_fallback","current_price":19.99,"recommendation_eligible":False}, "marketplace_preflight":{"by_marketplace":{"ebay":{"status":"ready_with_warnings","stale":False,"blockers":[]}}}},
    )
    db_session.add(listing); db_session.commit()
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["price_setup_required"] is True
    assert result["price_required_for_publication"] is True
    assert resolve_authoritative_listing_price(listing) is None


def test_vine_stale_price_only_attention_marker_does_not_override_catalog_readiness(db_session):
    from datetime import UTC, datetime
    import hashlib
    import json
    from app.services.marketplace_field_mapper import build_marketplace_payload
    user = User(email="vine-stale-queue@example.com", role="owner", is_admin=True)
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", status="draft",
        processing_state="needs_attention",
        processing_blocking_reason="Set a selling price",
        listing_price=22.0, title="Acme Stainless Steel Pour-Over Coffee Dripper Model 204",
        description="A product-specific description with included parts and useful buyer information.",
        category_id="cat-1", category_suggestion="Home > Kitchen",
        image_urls=["/media/dripper.jpg"],
        listing_images=[{"storage_path":"/media/dripper.jpg", "operator_state":"approved", "role":"primary"}],
        condition="New", condition_data={"condition_bucket":"new", "operator_review_required":False},
        shipping_profile={"package_weight":1,"package_dimensions":{"length":10,"width":8,"height":4}},
        source_metadata={"category_validation":{"status":"valid_local","category_id":"cat-1","full_path":"Home > Kitchen"}},
        marketplace_data={"targets":["ebay"], "marketplace_preflight":{"by_marketplace":{"ebay":{"status":"ready","marketplace":"ebay","stale":False,"blockers":[],"last_checked_at":datetime.now(UTC).isoformat()}}}},
        needs_review=False,
    )
    db_session.add(listing); db_session.flush()
    preflight = listing.marketplace_data["marketplace_preflight"]["by_marketplace"]["ebay"]
    preflight["execution_payload_hash"] = hashlib.sha256(json.dumps(build_marketplace_payload(listing, "ebay"), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    assert _compute_listing_bucket(listing) != "needs_attention"


def test_vine_compound_stale_pricing_marker_keeps_nonprice_attention_reason(db_session):
    user = User(email="vine-stale-queue-compound@example.com", role="owner", is_admin=True)
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", status="draft",
        processing_state="needs_attention",
        processing_blocking_reason="PRICING_EVIDENCE_MISSING; Required marketplace category missing",
    )
    db_session.add(listing); db_session.flush()

    assert _compute_listing_bucket(listing) == "needs_attention"


def test_repair_preserves_nonpricing_reason_in_compound_worker_message(db_session):
    user = User(email="vine-compound-reason@example.com", role="owner", is_admin=True)
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", title="Compound blocker", listing_price=10,
        processing_state="needs_attention",
        processing_blocking_reason="PRICING_EVIDENCE_MISSING; Required marketplace category missing",
    )
    db_session.add(listing); db_session.commit()
    repair_verified_vine_price_readiness(db_session, apply=True)
    db_session.refresh(listing)
    assert listing.processing_blocking_reason == "Required marketplace category missing"
