from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError
import pytest

from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceAccount, MarketplaceExtensionDevice, MarketplaceExtensionJob, MarketplaceListing, MarketplaceMetadataCache, User
from app.api.marketplaces import _bulk_preflight_response_to_csv
from app.api.routes import refresh_listing_readiness, update_listing
from app.api.schemas import ListingUpdateRequest, MarketplacePreflightResponse
from fastapi import BackgroundTasks, HTTPException
from app.services.ebay_service import _cached_category_aspects, build_ebay_publish_plan
from app.services.ebay_service import _sync_ebay_marketplace_listing
from app.services.marketplace_error_translation import translate_marketplace_error
from app.services.marketplace_preflight import MarketplacePreflightService, compute_payload_hash
from app.services.marketplace_field_mapper import build_marketplace_payload


def test_compact_preflight_preserves_qualified_category_identity():
    compact = MarketplacePreflightService()._compact_marketplace_preflight({
        "marketplace": "ebay",
        "status": "blocked",
        "category_summary": {
            "category_id": "26447",
            "taxonomy_marketplace": "EBAY_MOTORS_US",
            "taxonomy_tree_id": "100",
            "category_path": "Root > eBay Motors > Boat Parts > Covers",
        },
    })
    assert compact["category_summary"] == {
        "category_id": "26447",
        "taxonomy_marketplace": "EBAY_MOTORS_US",
        "taxonomy_tree_id": "100",
        "category_path": "Root > eBay Motors > Boat Parts > Covers",
    }
from app.services.ebay_service import summarize_ebay_account_health
from app.services import marketplace_orchestrator
from app.services import marketplace_preflight


def test_refresh_listing_readiness_promotes_only_shared_publishable_result(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session, title="Local category repair product")
    listing.processing_state = "needs_attention"
    listing.processing_blocking_reason = "A specific verified marketplace category leaf is required."
    listing.needs_review = False
    db_session.commit()

    monkeypatch.setattr(
        MarketplacePreflightService,
        "evaluate_publishability",
        lambda *_args, **_kwargs: {
            "publishable": True,
            "canonical": {"publishable": True, "review_required": True, "canonical_product_complete": True, "blocking_reasons": []},
            "preflight": {"status": "ready", "preparation_status": "ready", "preparation_blockers": []},
            "blockers": [],
            "warnings": [],
        },
    )
    result = refresh_listing_readiness(listing.id, "ebay", db_session, user)
    db_session.refresh(listing)
    assert result["publishable"] is True
    assert result["queue"] == "needs_review"
    assert listing.needs_review is True
    assert listing.processing_state == "complete"
    assert listing.processing_blocking_reason is None


def test_execution_only_blocker_keeps_destination_in_review_and_preserves_other_market(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session, title="Reviewable Facebook item")
    listing.needs_review = True
    listing.marketplace_data = {"destination_workflow": {"by_marketplace": {
        "facebook": {"status": "NEEDS_REVIEW", "preparation_status": "ready", "preparation_blockers": []},
    }}}
    db_session.commit()

    monkeypatch.setattr(
        MarketplacePreflightService,
        "evaluate_publishability",
        lambda *_args, **_kwargs: {
            "publishable": False,
            "canonical": {"publishable": False, "review_required": True, "canonical_product_complete": True, "blocking_reasons": []},
            "preflight": {
                "status": "blocked", "preparation_status": "ready", "preparation_blockers": [],
                "blockers": [{"code": "ACCOUNT_SESSION_EXPIRED", "message": "Refresh seller session before publishing."}],
            },
            "blockers": [{"code": "ACCOUNT_SESSION_EXPIRED", "message": "Refresh seller session before publishing."}],
            "warnings": [],
        },
    )

    result = refresh_listing_readiness(listing.id, "ebay", db_session, user)
    db_session.refresh(listing)
    assert result["publishable"] is False
    assert result["queue"] == "needs_review"
    assert listing.needs_review is True
    assert listing.marketplace_data["destination_workflow"]["by_marketplace"]["facebook"]["status"] == "NEEDS_REVIEW"
    assert listing.marketplace_data["destination_workflow"]["by_marketplace"]["ebay"]["status"] == "NEEDS_REVIEW"


def test_fresh_preflight_reconciles_old_missing_aspect_text_and_keeps_actual_issues(db_session):
    user, listing = _seed_user_and_listing(db_session, title="Vine Hanging Closet Organizer")
    listing.category_id = "43503"
    listing.category_suggestion = "Home & Garden > Closet Organizers"
    listing.item_specifics = {"Item Height": "24 in"}
    listing.processing_blocking_reason = "Item Height is required for this eBay category."
    db_session.commit()
    service = MarketplacePreflightService()
    result = {
        "marketplace": "ebay", "status": "blocked",
        "blockers": [{"code": "PRICE_REQUIRED", "field": "listing_price", "message": "A valid positive listing price is required before Needs Review"}],
        "warnings": [],
        "execution_payload_hash": "payload-hash",
        "payload_preview": {"payload": {"category_id": "43503", "item_specifics": {"Item Height": ["24 in"]}}},
        "category_summary": {"metadata_available": True},
    }
    service.cache_preflight_summary(db_session, listing, result)
    assert listing.processing_blocking_reason is None
    assert listing.marketplace_data["marketplace_preflight"]["by_marketplace"]["ebay"]["blocker_messages"] == ["A valid positive listing price is required before Needs Review"]

    listing.processing_blocking_reason = "Item Height is required for this eBay category."
    db_session.flush()
    result["blockers"] = [{"code": "EBAY_REQUIRED_ASPECT_MISSING", "field": "item_specifics.Item Height", "message": "Item Height is required for this eBay category."}]
    service.cache_preflight_summary(db_session, listing, result)
    assert listing.processing_blocking_reason == "Item Height is required for this eBay category."

    listing.processing_blocking_reason = "Item Height is required for this eBay category."
    result["status"] = "error"
    result["blockers"] = [{"code": "PREFLIGHT_CHECK_ERROR", "message": "eBay request failed"}]
    service.cache_preflight_summary(db_session, listing, result)
    assert listing.processing_blocking_reason == "Item Height is required for this eBay category."


def test_completed_ebay_preflight_clears_stale_missing_price_with_saved_positive_ask(db_session):
    _user, listing = _seed_user_and_listing(db_session, title="Vine LED Night Light")
    listing.source_type = "amazon_vine"
    listing.listing_price = 9.99
    listing.processing_blocking_reason = "Price missing"
    result = {
        "marketplace": "ebay", "status": "ready", "warnings": [],
        "blockers": [], "connection_blockers": [], "execution_payload_hash": "payload-hash",
        "payload_preview": {"payload": {"price": 9.99}}, "category_summary": {"metadata_available": True},
    }

    cached = MarketplacePreflightService().cache_preflight_summary(db_session, listing, result)

    assert listing.processing_blocking_reason is None
    assert cached["status"] == "ready"
    assert cached["blocker_messages"] == []


def test_completed_ebay_preflight_keeps_missing_price_blocker_without_saved_ask(db_session):
    _user, listing = _seed_user_and_listing(db_session, title="Vine LED Night Light")
    listing.source_type = "amazon_vine"
    listing.listing_price = None
    listing.suggested_price = None
    listing.processing_blocking_reason = "Price missing"
    result = {
        "marketplace": "ebay", "status": "blocked", "warnings": [],
        "blockers": [{"code": "PRICE_MISSING", "field": "listing_price", "message": "Set a selling price."}],
        "connection_blockers": [], "execution_payload_hash": "payload-hash",
        "payload_preview": {"payload": {"price": None}}, "category_summary": {"metadata_available": True},
    }

    cached = MarketplacePreflightService().cache_preflight_summary(db_session, listing, result)

    assert listing.processing_blocking_reason == "Price missing"
    assert any(code == "PRICE_MISSING" for code in cached["blocker_codes"])


def test_listing_editor_rejects_stale_patch_and_preserves_category_override_on_current_save(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session, title="Vine Hanging Closet Organizer")
    listing.category_id = "43503"
    listing.category_suggestion = "Home & Garden > Closet Organizers"
    listing.item_specifics = {"Brand": "Unbranded"}
    db_session.commit()
    opened_at = listing.updated_at
    stale = ListingUpdateRequest(expected_updated_at=opened_at - timedelta(seconds=1), title="Stale title")
    with pytest.raises(HTTPException) as raised:
        update_listing(listing.id, stale, BackgroundTasks(), db_session, user)
    assert raised.value.status_code == 409
    db_session.refresh(listing)
    assert listing.title == "Vine Hanging Closet Organizer"

    current = ListingUpdateRequest(
        expected_updated_at=listing.updated_at,
        category_id="43503",
        category_suggestion="Home & Garden > Closet Organizers",
        item_specifics={"Brand": "Unbranded", "Item Height": "24 in"},
        marketplace_data={"targets": ["ebay"]},
    )
    monkeypatch.setattr("app.services.ebay_taxonomy.category_by_id", lambda _db, category_id, tree_id, _marketplace: {
        "category_id": category_id, "category_name": "Closet Organizers", "full_path": "Home & Garden > Closet Organizers", "category_tree_id": tree_id, "marketplace": "EBAY_US", "leaf": True,
    } if tree_id == "0" else None)
    saved = update_listing(listing.id, current, BackgroundTasks(), db_session, user)
    assert saved["category_id"] == "43503"
    assert saved["item_specifics"]["Item Height"] == "24 in"
    assert saved["marketplace_data"]["marketplace_overrides"]["ebay"]["category"] == "43503"
    assert saved["source_metadata"]["operator_category_selection"]["category_id"] == "43503"

    legacy_user, legacy = _seed_user_and_listing(db_session, title="Legacy category lock without selection")
    legacy.category_id = "43503"
    legacy.source_metadata = {"recovery": {"field_provenance": {"category_id": "human_operator"}, "operator_locked_fields": ["category_id"]}}
    db_session.commit()
    legacy_saved = update_listing(
        legacy.id,
        ListingUpdateRequest(expected_updated_at=legacy.updated_at, item_specifics={"Item Height": "24 in"}),
        BackgroundTasks(), db_session, legacy_user,
    )
    assert legacy_saved["category_id"] == "43503"
    assert legacy_saved["source_metadata"].get("operator_category_selection") is None
    assert "category_id" in legacy_saved["source_metadata"]["recovery"]["operator_locked_fields"]


def test_listing_orm_version_rejects_stale_worker_snapshot(db_session):
    user, listing = _seed_user_and_listing(db_session, title="Vine Hanging Closet Organizer")
    db_session.commit()
    stale_session = __import__("app.core.database", fromlist=["SessionLocal"]).SessionLocal()
    try:
        stale = stale_session.get(Listing, listing.id)
        current = db_session.get(Listing, listing.id)
        current.item_specifics = {"Item Height": "24 in"}
        db_session.commit()
        stale.category_id = "20621"
        with pytest.raises(StaleDataError):
            stale_session.commit()
        stale_session.rollback()
        db_session.refresh(current)
        assert current.item_specifics["Item Height"] == "24 in"
        assert current.category_id != "20621"
    finally:
        stale_session.close()


def _seed_user_and_listing(db_session, *, title="Canon EOS Camera", condition="Used"):
    user = User(
        email=f"{title.replace(' ', '-').lower()}@example.com",
        settings_json={"ebay_marketplace_policy_settings": {
            "fulfillment_policy_id": "260642879013",
            "payment_policy_id": "pay",
            "return_policy_id": "return",
            "merchant_location_key": "loc",
            "marketplace_id": "EBAY_US",
            "policy_candidates": {"fulfillment": [{
                "id": "260642879013", "marketplace_id": "EBAY_US",
                "shipping_options": [{"cost_type": "CALCULATED", "shipping_services": [{
                    "service": "USPSParcel", "buyer_responsible": False,
                    "free_shipping": True, "shipping_cost": None,
                }]}],
            }]},
        }},
    )
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title=title,
        description="Used camera body with charger and strap included.",
        category_suggestion="30090",
        condition=condition,
        item_specifics={"Brand": "Canon", "Model": "EOS 80D", "UPC": "123456789012"},
        image_urls=["/media/uploads/camera-front.jpg"],
        listing_images=[
            {
                "storage_path": "/media/uploads/camera-front.jpg",
                "source_platform": "upload",
                "role": "primary",
                "operator_state": "approved",
                "display_order": 0,
                "is_reference": False,
                "confidence": 1.0,
            }
        ],
        condition_data={
            "condition_bucket": "used",
            "condition_source": "operator",
            "condition_confidence": 0.95,
            "operator_review_required": False,
            "item_condition_notes": "Used body with light wear.",
        },
        shipping_profile={
            "package_weight": 3.0,
            "package_dimensions": {"length": 14, "width": 11, "height": 8},
            "manual_measurement_needed": False,
            "parcel_measurement_confirmed": True,
            "provenance": {
                "package_weight": "operator_measured_packed_parcel",
                "package_dimensions": "operator_measured_packed_parcel",
            },
        },
        listing_price=149.99,
    )
    db_session.add(listing)
    db_session.flush()
    return user, listing


def test_needs_review_implies_publishability_for_same_eBay_evaluator(db_session, monkeypatch):
    """The queue and publish path must share one publishability contract."""
    user, listing = _seed_user_and_listing(db_session, title="Vine ready camera")
    listing.source_type = "amazon_vine"
    listing.category_id = "30090"
    listing.category_suggestion = "Cameras & Photo > Digital Cameras"
    listing.needs_review = True
    listing.processing_state = "complete"
    listing.source_metadata = {"category_validation": {"status": "valid_local", "leaf": True, "tree_id": "0", "tree_version": "134", "category_id": "30090", "full_path": listing.category_suggestion}}
    db_session.add(listing)
    db_session.commit()

    def ready_preflight(_self, _db, _listing, _marketplace, **_kwargs):
        return {
            "marketplace": "ebay", "status": "ready", "blockers": [], "warnings": [],
            "last_checked_at": datetime.now(UTC).isoformat(),
            "execution_payload_hash": compute_payload_hash(build_marketplace_payload(_listing, "ebay")),
            "policy_summary": {"fulfillment_policy_validation": {
                "valid": True, "policy_id": "260642879013", "free_shipping": True,
                "buyer_pays_shipping": False,
            }},
            "category_summary": {"category_id": "30090", "leaf_verified": True},
        }

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", ready_preflight)
    service = MarketplacePreflightService()
    evaluation = service.evaluate_publishability(db_session, listing, "ebay", refresh=True)
    assert evaluation["publishable"] is True
    assert evaluation["blockers"] == []


def test_unqualified_motors_category_is_revalidation_not_trading_blocker(db_session, monkeypatch):
    _user, listing = _seed_user_and_listing(db_session, title="RV replacement lamp")
    listing.category_id = "261084"
    listing.category_suggestion = "RV, Trailer & Camper Parts > Lighting > Interior Lighting"
    db_session.commit()

    async def motors_plan(*_args, **_kwargs):
        return {
            "category": {"category_id": "261084", "category_name": "Interior Lighting", "taxonomy_tree_id": "100", "metadata_available": True, "leaf_verified": True},
            "policy_settings": {"payment_policy_id": "pay", "fulfillment_policy_id": "260642879013", "return_policy_id": "return", "merchant_location_key": "loc", "marketplace_id": "EBAY_US", "policy_candidates": {"fulfillment": [{"id": "260642879013", "marketplace_id": "EBAY_US", "shipping_options": [{"cost_type": "CALCULATED", "shipping_services": [{"service": "USPSParcel", "buyer_responsible": False, "free_shipping": True, "shipping_cost": None}]}]}]}},
            "aspect_summary": {"required": [], "recommended": [], "missing_required": [], "unsupported": []},
            "inventory_item_payload": {"product": {"aspects": {}}},
            "shipping_summary": {"shipping_profile": {
                "package_weight": 2,
                "package_dimensions": {"length": 10, "width": 8, "height": 4},
                "estimated": True,
                "estimated_fields": ["package_weight", "package_dimensions"],
                "provenance": {"package_weight": "estimated_category_profile", "package_dimensions": "estimated_category_profile"},
            }},
            "image_summary": {"image_count": 1},
            "payload_preview": {"title": listing.title, "image_urls": ["https://example.test/image.jpg"]},
        }

    monkeypatch.setattr(marketplace_preflight, "build_ebay_publish_plan", motors_plan)
    result = MarketplacePreflightService()._build_ebay_preflight(
        db_session,
        listing,
        {"actual_image_count": 1, "manual_photo_needed": False},
        {"price_confidence": 1},
        allow_live_metadata_refresh=False,
    )

    codes = {blocker["code"] for blocker in result["blockers"]}
    assert "CATEGORY_REVALIDATION_REQUIRED" in codes
    assert "EBAY_WEIGHT_UNVERIFIED" not in codes
    assert "EBAY_DIMENSIONS_UNVERIFIED" not in codes
    assert "EBAY_MOTORS_TRADING_API_REQUIRED" not in codes


def test_estimated_parcel_facts_do_not_block_flat_or_free_tier_policies(db_session, monkeypatch):
    _user, listing = _seed_user_and_listing(db_session, title="Storage Organizer")
    listing.shipping_profile = {
        "package_weight": 1.2,
        "package_dimensions": {"length": 12, "width": 8, "height": 3},
        "estimated": True,
        "estimated_fields": ["package_weight", "package_dimensions"],
        "provenance": {"package_weight": "amazon_product_page", "package_dimensions": "amazon_product_page"},
    }
    db_session.commit()

    async def plan(_listing, *_args, **_kwargs):
        return {
            "category": {"category_id": "43503", "category_name": "Closet Organizers", "metadata_available": True, "leaf_verified": True},
            "policy_settings": {},
            "aspect_summary": {"required": [], "recommended": [], "missing_required": [], "unsupported": []},
            "inventory_item_payload": {"product": {"aspects": {}}},
            "shipping_summary": {"shipping_profile": listing.shipping_profile},
            "image_summary": {"image_count": 1},
            "payload_preview": {"title": listing.title, "image_urls": ["https://example.test/item.jpg"]},
        }

    def policy(_settings, *, sale_price, shipping_profile):
        if sale_price < 25:
            service = {"cost_type": "FLAT_RATE", "free_shipping": False, "buyer_responsible": True, "shipping_cost": {"value": "8.49", "currency": "USD"}}
            policy_id = "266212013013"
        else:
            service = {"cost_type": "CALCULATED", "free_shipping": True, "buyer_responsible": False, "shipping_cost": None}
            policy_id = "260642879013"
        return {"valid": True, "policy_id": policy_id, "services": [service]}

    monkeypatch.setattr(marketplace_preflight, "build_ebay_publish_plan", plan)
    monkeypatch.setattr("app.services.ebay_service.validate_cached_ebay_fulfillment_policy", policy)
    service = MarketplacePreflightService()
    for price in (24.99, 25.00):
        listing.listing_price = price
        result = service._build_ebay_preflight(db_session, listing, {"actual_image_count": 1, "manual_photo_needed": False}, {"price_confidence": 1}, allow_live_metadata_refresh=False)
        codes = {row["code"] for row in result["blockers"]}
        assert not codes.intersection({"EBAY_WEIGHT_MISSING", "EBAY_WEIGHT_UNVERIFIED", "EBAY_DIMENSIONS_MISSING", "EBAY_DIMENSIONS_UNVERIFIED"})


def test_verified_fitment_motors_category_routes_to_trading_publisher(db_session, monkeypatch):
    _user, listing = _seed_user_and_listing(db_session, title="UTV Turn Signal Kit with Vehicle Fitment")
    listing.source_metadata = {"amazon_product_facts": {"breadcrumbs": ["Automotive", "Motorcycle & Powersports"]}}
    listing.item_specifics = {"Compatible Vehicle": "UTV"}
    db_session.commit()

    async def motors_plan(*_args, **_kwargs):
        return {
            "category": {"category_id": "184888", "category_name": "Turn Signals", "taxonomy_marketplace": "EBAY_MOTORS_US", "taxonomy_tree_id": "100", "category_path": "Root > Motors > Turn Signals", "ownership_verified": True, "semantic_motors_classification": "TRUE_MOTORS", "metadata_available": True, "leaf_verified": True},
            "policy_settings": {"payment_policy_id": "pay", "fulfillment_policy_id": "260642879013", "return_policy_id": "return", "merchant_location_key": "loc", "marketplace_id": "EBAY_US", "policy_candidates": {"fulfillment": [{"id": "260642879013", "marketplace_id": "EBAY_US", "shipping_options": [{"cost_type": "CALCULATED", "shipping_services": [{"service": "USPSParcel", "buyer_responsible": False, "free_shipping": True, "shipping_cost": None}]}]}]}},
            "aspect_summary": {"required": [], "recommended": [], "missing_required": [], "unsupported": []},
            "inventory_item_payload": {"product": {"aspects": {}}},
            "shipping_summary": {"shipping_profile": {"package_weight": 2, "package_dimensions": {"length": 10, "width": 8, "height": 4}}},
            "image_summary": {"image_count": 1},
            "payload_preview": {"title": listing.title, "image_urls": ["https://example.test/image.jpg"]},
        }

    monkeypatch.setattr(marketplace_preflight, "build_ebay_publish_plan", motors_plan)
    result = MarketplacePreflightService()._build_ebay_preflight(
        db_session, listing, {"actual_image_count": 1, "manual_photo_needed": False},
        {"price_confidence": 1}, allow_live_metadata_refresh=False,
    )
    assert not any(blocker["code"] == "EBAY_MOTORS_TRADING_API_REQUIRED" for blocker in result["blockers"])
    codes = {blocker["code"] for blocker in result["blockers"]}
    assert "EBAY_WEIGHT_UNVERIFIED" not in codes
    assert "EBAY_DIMENSIONS_UNVERIFIED" not in codes
    summary = result["category_summary"]
    assert summary["taxonomy_marketplace"] == "EBAY_MOTORS_US"
    assert summary["taxonomy_tree_id"] == "100"
    assert summary["category_path"] == "Root > Motors > Turn Signals"
    assert summary["semantic_motors_classification"] == "TRUE_MOTORS"
    assert summary["publisher_path"] == "TRADING_API_MOTORS"
    assert any(blocker["code"] == "EBAY_TRADING_MERCHANT_LOCATION_UNVERIFIED" for blocker in result["blockers"])


def test_build_ebay_publish_plan_maps_preview_and_account_summary(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session)
    account = MarketplaceAccount(
        user_id=user.id,
        marketplace=MarketplaceName.ebay,
        external_account_id="ebay-user-1",
        access_token="token",
        refresh_token="refresh",
    )
    db_session.add(account)
    db_session.commit()

    async def fake_get_or_refresh_account(_user_id, _db):
        return account

    async def fake_suggest_ebay_category(_listing, _account, marketplace_id="EBAY_US"):
        return {"categoryId": "30090", "categoryName": "Camera & Photo"}

    async def fake_cached_category_aspects(_db, _account, category_id, *, marketplace_id="EBAY_US", force_refresh=False):
        return (
            {
                "aspects": [
                    {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True}},
                    {"localizedAspectName": "Model", "aspectConstraint": {"aspectRequired": True}},
                    {"localizedAspectName": "Color", "aspectConstraint": {"aspectRequired": False}},
                ]
            },
            "live",
            True,
        )

    async def fake_get_business_policy_ids(_access_token, marketplace_id="EBAY_US", create_if_missing=True):
        return {
            "paymentPolicyId": "payment-1",
            "fulfillmentPolicyId": "260642879013",
            "returnPolicyId": "return-1",
        }

    def fake_build_ebay_image_urls(_listing):
        return ["/media/uploads/camera-front.jpg"]

    monkeypatch.setattr("app.services.ebay_service.get_or_refresh_account", fake_get_or_refresh_account)
    monkeypatch.setattr("app.services.ebay_service.suggest_ebay_category", fake_suggest_ebay_category)
    monkeypatch.setattr("app.services.ebay_service._cached_category_aspects", fake_cached_category_aspects)
    monkeypatch.setattr("app.services.ebay_service.get_business_policy_ids", fake_get_business_policy_ids)
    monkeypatch.setattr("app.services.ebay_service._build_ebay_image_urls", fake_build_ebay_image_urls)

    plan = asyncio.run(build_ebay_publish_plan(listing, db_session, allow_create_policies=False))

    assert plan["sku"] == f"posterprou{user.id}l{listing.id}"
    assert plan["payload_preview"]["item_specifics"]["Brand"] == ["Canon"]
    assert plan["payload_preview"]["condition"] == "USED_GOOD"
    assert plan["policy_settings"]["shipping_service_code"] == "USPSGroundAdvantage"
    assert plan["account_summary"]["external_account_id"] == "ebay-user-1"
    assert plan["inventory_item_payload"]["packageWeightAndSize"] == {
        "weight": {"value": 3.0, "unit": "POUND"},
        "dimensions": {"length": 14, "width": 11, "height": 8, "unit": "INCH"},
    }
    assert "account" not in plan


def test_cached_marketplace_preflight_handles_mixed_timezone_datetimes(db_session):
    user, listing = _seed_user_and_listing(db_session)
    listing.marketplace_data = {
        "marketplace_preflight": {
            "by_marketplace": {
                "ebay": {
                    "status": "ready",
                    "last_checked_at": datetime.now(UTC).isoformat(),
                    "execution_payload_hash": compute_payload_hash(build_marketplace_payload(listing, "ebay")),
                    "blockers": [],
                    "warnings": [],
                    "payload_preview": {},
                }
            }
        }
    }
    listing.updated_at = datetime.now()
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    cached = MarketplacePreflightService()._cached_marketplace_preflight(listing, "ebay")

    assert cached is not None
    assert cached["cached"] is True


def test_worker_timestamp_update_keeps_preflight_but_payload_edit_invalidates_it(db_session):
    user, listing = _seed_user_and_listing(db_session)
    from app.services.marketplace_field_mapper import build_marketplace_payload

    payload_hash = compute_payload_hash(build_marketplace_payload(listing, "ebay"))
    listing.marketplace_data = {
        "marketplace_preflight": {
            "by_marketplace": {
                "ebay": {
                    "status": "ready_with_warnings",
                    "last_checked_at": datetime.now(UTC).isoformat(),
                    "execution_payload_hash": payload_hash,
                    "blockers": [],
                    "warnings": [],
                    "payload_preview": {},
                }
            }
        }
    }
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    # A queue/progress worker can update this timestamp without changing the
    # marketplace payload; that must not stale a valid eBay preflight.
    listing.updated_at = datetime.now() + timedelta(minutes=1)
    assert MarketplacePreflightService()._cached_marketplace_preflight(listing, "ebay") is not None

    listing.description = "A changed buyer-facing description requires fresh validation."
    assert MarketplacePreflightService()._cached_marketplace_preflight(listing, "ebay") is None


def test_sync_ebay_marketplace_listing_tolerates_duplicate_rows(db_session):
    user, listing = _seed_user_and_listing(db_session)
    older = MarketplaceListing(
        listing_id=listing.id,
        marketplace=MarketplaceName.ebay,
        status=MarketplaceListingStatus.PENDING,
    )
    newer = MarketplaceListing(
        listing_id=listing.id,
        marketplace=MarketplaceName.ebay,
        status=MarketplaceListingStatus.PENDING,
    )
    db_session.add_all([older, newer])
    db_session.commit()

    _sync_ebay_marketplace_listing(
        db_session,
        listing_id=listing.id,
        status=MarketplaceListingStatus.PUBLISHED,
        response={"listing_id": "1234567890"},
    )
    db_session.commit()

    rows = db_session.query(MarketplaceListing).filter(MarketplaceListing.listing_id == listing.id).all()
    assert len(rows) == 2
    assert any(row.status == MarketplaceListingStatus.PUBLISHED for row in rows)


def test_build_ebay_publish_plan_preserves_draft_specific_provenance(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session)
    listing.marketplace_data = {
        "ebay_item_specifics_provenance": {"Brand": "approximate", "Type": "derived"},
        "ebay_item_specifics_approximate": ["Brand"],
    }
    db_session.add(listing)
    db_session.commit()

    account = MarketplaceAccount(
        user_id=user.id,
        marketplace=MarketplaceName.ebay,
        external_account_id="ebay-user-2",
        access_token="token",
        refresh_token="refresh",
    )
    db_session.add(account)
    db_session.commit()

    async def fake_get_or_refresh_account(_user_id, _db):
        return account

    async def fake_suggest_ebay_category(_listing, _account, marketplace_id="EBAY_US"):
        return {"categoryId": "30090", "categoryName": "Camera & Photo"}

    async def fake_cached_category_aspects(_db, _account, category_id, *, marketplace_id="EBAY_US", force_refresh=False):
        return (
            {
                "aspects": [
                    {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True}},
                    {"localizedAspectName": "Model", "aspectConstraint": {"aspectRequired": True}},
                ]
            },
            "live",
            True,
        )

    async def fake_get_business_policy_ids(_access_token, marketplace_id="EBAY_US", create_if_missing=True):
        return {
            "paymentPolicyId": "payment-1",
            "fulfillmentPolicyId": "260642879013",
            "returnPolicyId": "return-1",
        }

    monkeypatch.setattr("app.services.ebay_service.get_or_refresh_account", fake_get_or_refresh_account)
    monkeypatch.setattr("app.services.ebay_service.suggest_ebay_category", fake_suggest_ebay_category)
    monkeypatch.setattr("app.services.ebay_service._cached_category_aspects", fake_cached_category_aspects)
    monkeypatch.setattr("app.services.ebay_service.get_business_policy_ids", fake_get_business_policy_ids)
    monkeypatch.setattr("app.services.ebay_service._build_ebay_image_urls", lambda _listing: ["/media/uploads/camera-front.jpg"])

    plan = asyncio.run(build_ebay_publish_plan(listing, db_session, allow_create_policies=False))

    assert plan["payload_preview"]["item_specifics_provenance"]["Brand"] == "approximate"
    assert "Brand" in plan["payload_preview"]["item_specifics_approximate"]


def test_build_ebay_publish_plan_drops_placeholder_specifics_and_invalid_eprel(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session, title="Bathroom Fan")
    listing.item_specifics = {
        "Brand": "Does Not Apply",
        "Type": "Exhaust Fan",
        "Compatible Model": "For ASIN",
        "EPREL Registration Number": "ABC-123-TOO-LONG",
        "Model": "B0TEST123",
    }
    db_session.add(listing)
    db_session.commit()

    account = MarketplaceAccount(
        user_id=user.id,
        marketplace=MarketplaceName.ebay,
        external_account_id="ebay-user-3",
        access_token="token",
        refresh_token="refresh",
    )
    db_session.add(account)
    db_session.commit()

    async def fake_get_or_refresh_account(_user_id, _db):
        return account

    async def fake_suggest_ebay_category(_listing, _account, marketplace_id="EBAY_US"):
        return {"categoryId": "122909", "categoryName": "Bathroom Exhaust Fans"}

    async def fake_cached_category_aspects(_db, _account, category_id, *, marketplace_id="EBAY_US", force_refresh=False):
        return (
            {
                "aspects": [
                    {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True}},
                    {"localizedAspectName": "Type", "aspectConstraint": {"aspectRequired": True}},
                    {"localizedAspectName": "Model", "aspectConstraint": {"aspectRequired": False}},
                    {"localizedAspectName": "Compatible Model", "aspectConstraint": {"aspectRequired": False}},
                    {"localizedAspectName": "EPREL Registration Number", "aspectConstraint": {"aspectRequired": False}},
                ]
            },
            "live",
            True,
        )

    async def fake_get_business_policy_ids(_access_token, marketplace_id="EBAY_US", create_if_missing=True):
        return {
            "paymentPolicyId": "payment-1",
            "fulfillmentPolicyId": "260642879013",
            "returnPolicyId": "return-1",
        }

    monkeypatch.setattr("app.services.ebay_service.get_or_refresh_account", fake_get_or_refresh_account)
    monkeypatch.setattr("app.services.ebay_service.suggest_ebay_category", fake_suggest_ebay_category)
    monkeypatch.setattr("app.services.ebay_service._cached_category_aspects", fake_cached_category_aspects)
    monkeypatch.setattr("app.services.ebay_service.get_business_policy_ids", fake_get_business_policy_ids)
    monkeypatch.setattr("app.services.ebay_service._build_ebay_image_urls", lambda _listing: ["/media/uploads/fan.jpg"])

    plan = asyncio.run(build_ebay_publish_plan(listing, db_session, allow_create_policies=False))

    specifics = plan["payload_preview"]["item_specifics"]
    assert specifics["Type"] == ["Exhaust Fan"]
    assert "Compatible Model" not in specifics
    assert "EPREL Registration Number" not in specifics
    assert specifics["Brand"] == ["Bathroom Fan"]


def test_build_ebay_publish_plan_uses_numeric_category_fallback_when_suggestion_is_text(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session, title="Marine Starter")
    listing.category_suggestion = "Starters, Alternators & ECU"
    db_session.add(listing)
    db_session.commit()

    account = MarketplaceAccount(
        user_id=user.id,
        marketplace=MarketplaceName.ebay,
        external_account_id="ebay-user-4",
        access_token="token",
        refresh_token="refresh",
    )
    db_session.add(account)
    db_session.commit()

    async def fake_get_or_refresh_account(_user_id, _db):
        return account

    async def fake_suggest_ebay_category(_listing, _account, marketplace_id="EBAY_US"):
        return {"categoryId": "171485", "categoryName": "Parts & Accessories"}

    async def fake_cached_category_aspects(_db, _account, category_id, *, marketplace_id="EBAY_US", force_refresh=False):
        return ({"aspects": []}, "live", True)

    async def fake_get_business_policy_ids(_access_token, marketplace_id="EBAY_US", create_if_missing=True):
        return {
            "paymentPolicyId": "payment-1",
            "fulfillmentPolicyId": "260642879013",
            "returnPolicyId": "return-1",
        }

    monkeypatch.setattr("app.services.ebay_service.get_or_refresh_account", fake_get_or_refresh_account)
    monkeypatch.setattr("app.services.ebay_service.suggest_ebay_category", fake_suggest_ebay_category)
    monkeypatch.setattr("app.services.ebay_service._cached_category_aspects", fake_cached_category_aspects)
    monkeypatch.setattr("app.services.ebay_service.get_business_policy_ids", fake_get_business_policy_ids)
    monkeypatch.setattr("app.services.ebay_service._build_ebay_image_urls", lambda _listing: ["/media/uploads/starter.jpg"])

    plan = asyncio.run(build_ebay_publish_plan(listing, db_session, allow_create_policies=False))

    assert plan["category"]["category_id"] == "171485"


def test_apply_repair_actions_persists_resolved_category_id(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session)
    listing.category_id = None
    listing.category_suggestion = "RYOBI ONE+ cordless blower"
    db_session.add(listing)
    db_session.commit()

    async def fake_build_ebay_publish_plan(_listing, _db, allow_create_policies=False):
        return {
            "category": {
                "category_id": "139968",
                "category_name": "Leaf Blowers",
                "source": "ebay_taxonomy",
            }
        }

    def fake_preflight_listing(_db, _listing, marketplace):
        return {
            "status": "ready_with_warnings",
            "blockers": [],
            "warnings": [],
            "category_summary": {"category_id": "139968", "category_name": "Leaf Blowers"},
            "image_summary": {"actual_image_present": True},
        }

    monkeypatch.setattr("app.services.marketplace_preflight.build_ebay_publish_plan", fake_build_ebay_publish_plan)
    service = MarketplacePreflightService()
    monkeypatch.setattr(service, "preflight_listing", fake_preflight_listing)

    result = service.apply_repair_actions(db_session, listing, apply_category_suggestion=True, validate_images=False)

    db_session.refresh(listing)
    assert listing.category_id == "139968"
    assert (listing.marketplace_data or {}).get("ebay_last_resolved_category", {}).get("category_id") == "139968"
    assert "category_id" in result["applied"]


def test_cached_category_aspects_uses_latest_duplicate_cache_row(db_session, monkeypatch):
    user, _listing = _seed_user_and_listing(db_session)
    account = MarketplaceAccount(
        user_id=user.id,
        marketplace=MarketplaceName.ebay,
        external_account_id="ebay-user-dup-cache",
        access_token="token",
        refresh_token="refresh",
    )
    db_session.add(account)
    db_session.flush()

    older = MarketplaceMetadataCache(
        marketplace="ebay",
        cache_key="ebay:EBAY_US:42132:aspects",
        payload={"aspects": [{"localizedAspectName": "Old"}]},
        source_version="ebay_taxonomy_v1",
        expires_at=datetime.utcnow() + timedelta(days=3),
    )
    newer = MarketplaceMetadataCache(
        marketplace="ebay",
        cache_key="ebay:EBAY_US:42132:aspects",
        payload={"aspects": [{"localizedAspectName": "New"}]},
        source_version="ebay_taxonomy_v1",
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db_session.add_all([older, newer])
    db_session.commit()

    async def fail_live_fetch(*_args, **_kwargs):
        raise AssertionError("live taxonomy fetch should not run")

    monkeypatch.setattr("app.services.ebay_service.get_required_item_specifics", fail_live_fetch)

    payload, source, available = asyncio.run(
        _cached_category_aspects(db_session, account, "42132", marketplace_id="EBAY_US")
    )

    assert available is True
    assert source == "cache"
    assert payload == newer.payload


def test_cached_category_aspects_falls_back_to_unified_registry(db_session, monkeypatch):
    user, _listing = _seed_user_and_listing(db_session)
    account = MarketplaceAccount(user_id=user.id, marketplace=MarketplaceName.ebay,
        external_account_id="ebay-registry-aspects", access_token="token", refresh_token="refresh")
    db_session.add(account); db_session.flush()
    raw = db_session.connection().connection
    raw.executescript("DROP TABLE IF EXISTS marketplace_taxonomy_categories; DROP TABLE IF EXISTS marketplace_taxonomy_versions;")
    migration = Path(__file__).parents[1] / "migrations/20261023_marketplace_taxonomy_registry.sql"
    raw.executescript(migration.read_text())
    evidence = {"source": "official_eBay_Taxonomy_API"}
    aspect = {"localizedAspectName": "Exterior Material", "aspectConstraint": {"aspectRequired": True}, "aspectValues": [{"localizedValue": "Leather"}]}
    db_session.execute(text("""INSERT INTO marketplace_taxonomy_versions
      (version_id,marketplace,scope_key,native_version,source,captured_at,last_checked_at,verification_status,
       completeness_status,staleness_status,category_count,terminal_count,unresolved_count,is_current,source_evidence_json)
      VALUES ('ebay:EBAY_US:0:134','ebay','EBAY_US:0','134','official_taxonomy_api',:now,:now,'authoritatively_verified',
       'complete','fresh',1,1,0,TRUE,:evidence)"""), {"now": datetime.utcnow(), "evidence": json.dumps(evidence)})
    db_session.execute(text("""INSERT INTO marketplace_taxonomy_categories
      (version_id,marketplace,category_key,category_id,category_name,parent_category_key,parent_category_id,
       full_path,path_json,depth,terminal_status,publishability_status,required_attributes_json,data_source,
       source_evidence_json,verification_status,staleness_status,automated_selection_eligible)
      VALUES ('ebay:EBAY_US:0:134','ebay','EBAY_US:0:169291','169291','Women’s Bags & Handbags',NULL,NULL,
       'Root > Women’s Bags & Handbags','[]',1,'terminal','publishable',:required,'official_taxonomy_api',:evidence,
       'authoritatively_verified','fresh',TRUE)"""), {"required": json.dumps([aspect]), "evidence": json.dumps(evidence)})
    db_session.commit()

    async def fail_live_fetch(*_args, **_kwargs):
        raise AssertionError("fresh registry aspect evidence should be reused")
    monkeypatch.setattr("app.services.ebay_service.get_required_item_specifics", fail_live_fetch)
    payload, source, available = asyncio.run(_cached_category_aspects(db_session, account, "169291", marketplace_id="EBAY_US", taxonomy_tree_id="0"))
    assert available is True
    assert source == "registry"
    assert payload["aspects"][0]["localizedAspectName"] == "Exterior Material"


def test_ebay_preflight_reports_missing_policies_and_aspects(db_session, monkeypatch):
    _, listing = _seed_user_and_listing(db_session)
    listing.item_specifics = {"Brand": "Canon"}
    db_session.add(listing)
    db_session.commit()

    async def fake_plan(_listing, _db, allow_create_policies=False, marketplace_id="EBAY_US"):
        return {
            "category": {"category_id": "30090", "category_name": "Camera & Photo", "metadata_available": True},
            "category_summary": {},
            "aspect_summary": {
                "required": ["Brand", "Model"],
                "recommended": ["Color"],
                "missing_required": ["Model"],
                "unsupported": [],
            },
            "policy_settings": {
                "payment_policy_id": "",
                "fulfillment_policy_id": "",
                "return_policy_id": "",
                "merchant_location_key": "",
                "shipping_service_code": "",
                "handling_time_days": 1,
                "local_pickup_allowed": False,
                "calculated_shipping": False,
                "package_weight_required": True,
                "package_dimensions_required": True,
            },
            "policy_ids": {},
            "inventory_item_payload": {},
            "offer_payload": {},
            "payload_preview": {
                "sku": "posterprou1l1",
                "title": listing.title,
                "description": listing.description,
                "condition": "USED_GOOD",
                "conditionDescription": listing.description,
                "category_id": "30090",
                "quantity": 1,
                "price": 149.99,
                "currency": "USD",
                "product_identifiers": {"UPC": "123456789012"},
                "item_specifics": {"Brand": ["Canon"]},
                "image_urls": listing.image_urls,
                "packageWeightAndSize": listing.shipping_profile,
                "shipping_policy": {},
                "marketplaceId": "EBAY_US",
                "listing_format": "FIXED_PRICE",
                "duration": "GTC",
                "site": "EBAY_US",
            },
            "image_summary": {"image_count": 1, "actual_image_count": 1, "reference_only": False},
            "shipping_summary": {"shipping_profile": listing.shipping_profile, "shipping_policy": {}},
        }

    monkeypatch.setattr("app.services.marketplace_preflight.build_ebay_publish_plan", fake_plan)

    result = MarketplacePreflightService().preflight_listing(db_session, listing, "ebay")
    blocker_codes = {item["code"] for item in result["blockers"]}

    assert result["status"] == "blocked"
    assert "EBAY_PAYMENT_POLICY_MISSING" in blocker_codes
    assert "EBAY_FULFILLMENT_POLICY_MISSING" in blocker_codes
    assert "EBAY_RETURN_POLICY_MISSING" in blocker_codes
    assert "EBAY_MERCHANT_LOCATION_MISSING" in blocker_codes
    assert "EBAY_REQUIRED_ASPECT_MISSING" in blocker_codes
    assert result["payload_preview"]["payload"]["sku"] == "posterprou1l1"


def test_preflight_blocks_unapproved_category_estimate_even_with_price_fields(db_session):
    user, listing = _seed_user_and_listing(db_session, title="Evidence gated item")
    listing.marketplace_data = {
        "pricing_analysis": {
            "current_price": 24.99,
            "recommended_price": None,
            "recommendation_eligible": False,
            "provenance": "category_model_estimate",
        }
    }
    blockers = MarketplacePreflightService()._base_blockers(
        listing,
        "ebay",
        listing.marketplace_data["pricing_analysis"],
        {"actual_image_count": 1, "shipping_checklist": {"weight_present": True, "package_dimensions_present": True}},
    )
    assert "PRICING_EVIDENCE_MISSING" in {item["code"] for item in blockers}

    listing.marketplace_data["manual_price_override"] = 24.5
    listing.marketplace_data["manual_price_override_reason"] = "Operator checked local sold history"
    blockers = MarketplacePreflightService()._base_blockers(
        listing,
        "ebay",
        listing.marketplace_data["pricing_analysis"],
        {"actual_image_count": 1, "shipping_checklist": {"weight_present": True, "package_dimensions_present": True}},
    )
    assert "PRICING_EVIDENCE_MISSING" not in {item["code"] for item in blockers}


def test_preflight_blocks_legacy_timeline_price_without_pricing_provenance(db_session):
    user, listing = _seed_user_and_listing(db_session, title="Timeline product")
    listing.source_type = "intake_head_slate"
    listing.listing_price = 22.0
    listing.suggested_price = 22.0
    listing.marketplace_data = {}
    blockers = MarketplacePreflightService()._base_blockers(
        listing,
        "ebay",
        {},
        {"actual_image_count": 1, "shipping_checklist": {"weight_present": True, "package_dimensions_present": True}},
    )
    assert "PRICING_EVIDENCE_MISSING" in {item["code"] for item in blockers}


def test_preflight_accepts_any_positive_vine_ask_without_price_provenance_but_requires_positive_price(db_session):
    _, listing = _seed_user_and_listing(db_session, title="Vine imported listing price")
    listing.source_type = "amazon_vine"
    listing.listing_price = 18.99
    listing.source_metadata = {"vine_imported_listing_price":{"source_field":"Listing Price","source_value":"18.99","amount":18.99}}
    listing.marketplace_data = {"pricing_analysis":{"provenance":"vine_import_listing_price","recommendation_eligible":False}}
    service = MarketplacePreflightService()
    args = {"actual_image_count":1,"shipping_checklist":{"weight_present":True,"package_dimensions_present":True}}
    blockers = service._base_blockers(listing,"ebay",listing.marketplace_data["pricing_analysis"],args)
    assert "PRICING_EVIDENCE_MISSING" not in {item["code"] for item in blockers}

    listing.source_metadata = {"estimated_tax_value":18.99}
    listing.marketplace_data = {"pricing_analysis":{"provenance":"vine_etv_fallback","price_source":"vine_estimated_tax_value_fallback","recommendation_eligible":False}}
    blockers = service._base_blockers(listing,"ebay",listing.marketplace_data["pricing_analysis"],args)
    assert "PRICING_EVIDENCE_MISSING" not in {item["code"] for item in blockers}

    listing.listing_price = None
    listing.suggested_price = 18.99
    blockers = service._base_blockers(listing,"ebay",listing.marketplace_data["pricing_analysis"],args)
    assert "PRICING_EVIDENCE_MISSING" not in {item["code"] for item in blockers}
    assert "PRICE_MISSING" in {item["code"] for item in blockers}


def test_facebook_preflight_flags_reference_only_images(db_session):
    _, listing = _seed_user_and_listing(db_session)
    listing.listing_images = [
        {
            "storage_path": "/media/uploads/camera-front.jpg",
            "source_platform": "amazon_vine",
            "role": "primary",
            "operator_state": "suggested",
            "display_order": 0,
            "is_reference": True,
            "confidence": 0.4,
            "warning": "source/reference image",
        }
    ]
    listing.image_urls = ["/media/uploads/camera-front.jpg"]
    listing.shipping_profile = {"local_pickup_recommended": True, "manual_measurement_needed": True}
    db_session.add(listing)
    db_session.commit()

    result = MarketplacePreflightService().preflight_listing(db_session, listing, "facebook")

    assert result["status"] == "blocked"
    assert any(issue["code"] == "FACEBOOK_PHOTOS_MISSING" for issue in result["blockers"])
    assert any(issue["code"] == "FACEBOOK_REFERENCE_ONLY" for issue in result["warnings"])
    assert result["policy_summary"]["browser_bridge_required"] is False
    assert result["policy_summary"]["browser_extension_required"] is True
    assert result["policy_summary"]["final_submit_supported"] is True
    assert result["policy_summary"]["final_submit_authorized_by_posterpro_publish"] is True


def test_facebook_connection_block_does_not_mean_listing_data_is_unprepared(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session)
    service = MarketplacePreflightService()
    monkeypatch.setattr("app.services.marketplace_preflight.summarize_listing_readiness", lambda **_kwargs: {"ready_for_publish": True, "actual_image_count": 1, "manual_photo_needed": False})
    monkeypatch.setattr("app.services.marketplace_preflight.compute_listing_quality_summary", lambda *_args, **_kwargs: {"status": "ready"})
    monkeypatch.setattr("app.services.marketplace_preflight.canonical_listing_readiness", lambda *_args, **_kwargs: {"canonical_product_complete": True, "canonical_blocking_reasons": [], "blocking_reasons": [], "publishable": False})
    monkeypatch.setattr(service, "_base_blockers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "_base_warnings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "_build_facebook_preflight", lambda *_args, **_kwargs: {"blockers": [], "warnings": [], "payload_preview": {"title": listing.title}, "policy_summary": {}, "category_summary": {}})
    monkeypatch.setattr("app.services.marketplace_preflight._facebook_publish_capability", lambda *_args, **_kwargs: {"publish_capable": False, "status": "BLOCKED", "reason": "Facebook is not connected."})

    result = service.preflight_listing(db_session, listing, "facebook")

    assert result["preparation_status"] == "ready"
    assert result["preparation_blockers"] == []
    assert result["execution_status"] == "blocked"
    assert [item["code"] for item in result["connection_blockers"]] == ["FACEBOOK_ASSISTED_WORKFLOW_UNVERIFIED"]
    assert result["blockers"] == result["execution_blockers"]
    api_result = MarketplacePreflightResponse.model_validate(result)
    assert api_result.preparation_status == "ready"
    assert api_result.execution_status == "blocked"


def test_facebook_first_supervised_publish_does_not_require_prior_live_listing(db_session):
    from app.services.marketplace_preflight import _facebook_publish_capability

    user, _listing = _seed_user_and_listing(db_session)
    device = MarketplaceExtensionDevice(
        user_id=user.id,
        device_key=f"facebook-device-{user.id}",
        token_hash=f"facebook-token-{user.id}",
        extension_version="0.3.6",
        last_seen_at=datetime.now(UTC).replace(tzinfo=None),
    )
    diagnostic = MarketplaceExtensionJob(
        user_id=user.id,
        marketplace="facebook",
        action="DIAGNOSTIC",
        status="COMPLETED",
        payload_version=1,
        payload_snapshot={"diagnostic": True},
        result={
            "capability_ready": True,
            "login_state": "LOGGED_IN",
            "page_state": "FORM_AVAILABLE",
            "field_results": [],
        },
    )
    db_session.add_all([device, diagnostic])
    db_session.flush()

    capability = _facebook_publish_capability(db_session, user.id)

    assert capability["safe_diagnostic_passed"] is True
    assert capability["assisted_workflow_capable"] is True
    assert capability["publish_capable"] is False
    assert capability["status"] == "ASSISTED_READY"
    assert "posterpro publish authorizes the extension to submit" in capability["reason"].lower()
    assert capability["final_submit_supported"] is True
    assert capability["execution_transport"] == "browser_extension"


def test_preflight_includes_canonical_readiness_blockers(db_session, monkeypatch):
    _, listing = _seed_user_and_listing(db_session)
    monkeypatch.setattr(
        "app.services.marketplace_preflight.canonical_listing_readiness",
        lambda *_args, **_kwargs: {
            "blocking_reasons": ["Description needs verified product-specific enrichment"],
            "publishable": False,
        },
    )

    result = MarketplacePreflightService().preflight_listing(db_session, listing, "facebook")

    assert result["status"] == "blocked"
    assert any(
        issue["code"] == "CANONICAL_READINESS_BLOCKED"
        and issue["message"] == "Description needs verified product-specific enrichment"
        for issue in result["blockers"]
    )


def test_destination_preflight_does_not_block_on_its_own_pending_marker(db_session, monkeypatch):
    _, listing = _seed_user_and_listing(db_session)
    service = MarketplacePreflightService()
    monkeypatch.setattr("app.services.marketplace_preflight.summarize_listing_readiness", lambda **_kwargs: {"ready_for_publish": True, "actual_image_count": 1, "manual_photo_needed": False})
    monkeypatch.setattr("app.services.marketplace_preflight.compute_listing_quality_summary", lambda *_args, **_kwargs: {"status": "ready"})
    monkeypatch.setattr("app.services.marketplace_preflight.canonical_listing_readiness", lambda *_args, **_kwargs: {
        "canonical_product_complete": True,
        "canonical_blocking_reasons": ["Destination preflight is pending; run the configured marketplace checks before publishing"],
        "blocking_reasons": ["Destination preflight is pending; run the configured marketplace checks before publishing"],
        "destination_preflight_pending": True,
        "publishable": False,
    })
    monkeypatch.setattr(service, "_base_blockers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "_base_warnings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "_build_facebook_preflight", lambda *_args, **_kwargs: {"blockers": [], "warnings": [], "payload_preview": {"title": listing.title}, "policy_summary": {}, "category_summary": {}})
    monkeypatch.setattr("app.services.marketplace_preflight._facebook_publish_capability", lambda *_args, **_kwargs: {"publish_capable": True, "status": "READY", "reason": "ready"})

    result = service.preflight_listing(db_session, listing, "facebook")

    assert not any(item["code"] == "CANONICAL_READINESS_BLOCKED" for item in result["blockers"])


def test_ebay_preflight_reference_only_images_do_not_also_raise_invalid_url(db_session, monkeypatch):
    _, listing = _seed_user_and_listing(db_session)
    listing.listing_images = [
        {
            "storage_path": "/media/uploads/camera-front.jpg",
            "source_platform": "amazon_vine",
            "role": "primary",
            "operator_state": "suggested",
            "display_order": 0,
            "is_reference": True,
            "confidence": 0.4,
        }
    ]
    listing.image_urls = ["/media/uploads/camera-front.jpg"]
    db_session.add(listing)
    db_session.commit()

    async def fake_plan(_listing, _db, allow_create_policies=False, marketplace_id="EBAY_US"):
        return {
            "category": {"category_id": "30090", "category_name": "Camera & Photo", "metadata_available": True},
            "aspect_summary": {"required": ["Brand"], "recommended": [], "missing_required": [], "unsupported": []},
            "policy_settings": {
                "payment_policy_id": "payment-1",
                "fulfillment_policy_id": "fulfillment-1",
                "return_policy_id": "return-1",
                "merchant_location_key": "posterpro-1",
                "shipping_service_code": "",
                "handling_time_days": 1,
                "local_pickup_allowed": False,
                "calculated_shipping": False,
                "package_weight_required": True,
                "package_dimensions_required": True,
            },
            "policy_ids": {},
            "inventory_item_payload": {},
            "offer_payload": {},
            "payload_preview": {"sku": "posterprou1l1"},
            "image_summary": {"image_count": 1, "actual_image_count": 0, "reference_only": True},
            "shipping_summary": {"shipping_profile": listing.shipping_profile, "shipping_policy": {}},
        }

    monkeypatch.setattr("app.services.marketplace_preflight.build_ebay_publish_plan", fake_plan)

    result = MarketplacePreflightService().preflight_listing(db_session, listing, "ebay")
    blocker_codes = {item["code"] for item in result["blockers"]}

    assert "ACTUAL_PHOTOS_MISSING" not in blocker_codes
    assert "EBAY_IMAGE_URL_INVALID" not in blocker_codes
    assert any(issue["code"] == "EBAY_REFERENCE_ONLY_IMAGES" for issue in result["warnings"])


def test_ebay_image_requirement_is_destination_specific(db_session, monkeypatch):
    _, listing = _seed_user_and_listing(db_session, title="Vine boxed product awaiting images")
    listing.source_type = "amazon_vine"
    listing.image_urls = []
    listing.listing_images = []
    db_session.add(listing); db_session.commit()

    async def fake_plan(_listing, _db, allow_create_policies=False, marketplace_id="EBAY_US"):
        return {
            "category": {"category_id": "30090", "category_name": "Camera & Photo", "metadata_available": True},
            "aspect_summary": {"required": [], "recommended": [], "missing_required": [], "unsupported": []},
            "policy_settings": {"payment_policy_id":"p", "fulfillment_policy_id":"f", "return_policy_id":"r", "merchant_location_key":"loc", "shipping_service_code":"USPSParcel", "handling_time_days":1, "local_pickup_allowed":False, "calculated_shipping":False, "package_weight_required":False, "package_dimensions_required":False},
            "policy_ids": {}, "inventory_item_payload": {}, "offer_payload": {}, "payload_preview": {"sku":"vinesku1"},
            "image_summary": {"image_count":0, "actual_image_count":0, "reference_only":False},
            "shipping_summary": {"shipping_profile":listing.shipping_profile, "shipping_policy":{}},
        }

    monkeypatch.setattr("app.services.marketplace_preflight.build_ebay_publish_plan", fake_plan)
    result = MarketplacePreflightService().preflight_listing(db_session, listing, "ebay")
    codes={issue["code"] for issue in result["blockers"]}

    assert "EBAY_IMAGES_MISSING" in codes
    assert "ACTUAL_PHOTOS_MISSING" not in codes
    assert not any(issue.get("message") == "No images attached" for issue in result["blockers"])
    assert any(issue["code"] == "IMAGE_ENRICHMENT_PENDING" for issue in result["warnings"])


def test_marketplace_error_translation_normalizes_common_failures():
    ebay_error = translate_marketplace_error("ebay", '{"message":"Invalid access token"}')
    facebook_error = translate_marketplace_error("facebook", "Browser not connected for assisted publish")
    ebay_shipping_error = translate_marketplace_error("ebay", "invalid shipping policy id")
    ebay_detector_error = translate_marketplace_error("ebay", "For safety reasons, certain smoke and carbon monoxide detectors aren't allowed. policy reference PI_PRS_UKSmokedetectors")
    ebay_account_restriction = translate_marketplace_error("ebay", "errorId 25019: You can't currently create new listings or revise existing listings; resolve open issues in eBay Messages")
    facebook_handoff_error = translate_marketplace_error("facebook", "final submit unsupported in this workspace")

    assert ebay_error["code"] == "EBAY_OAUTH_EXPIRED"
    assert ebay_error["retryable"] is False
    assert "Reconnect eBay" in ebay_error["fix_hint"]
    assert ebay_shipping_error["code"] == "EBAY_POLICY_MISSING"
    assert ebay_detector_error["code"] == "EBAY_PRODUCT_SAFETY_RESTRICTED"
    assert ebay_detector_error["retryable"] is False
    assert ebay_account_restriction["code"] == "EBAY_SELLER_RESTRICTED"
    assert ebay_account_restriction["operator_action"] == "resolve_ebay_account_issue"
    assert ebay_account_restriction["retryable"] is False
    assert ebay_shipping_error["operator_action"] == "fix_policies"
    assert facebook_error["code"] == "FACEBOOK_BROWSER_UNAVAILABLE"
    assert facebook_error["retryable"] is True
    assert facebook_handoff_error["code"] == "FACEBOOK_FINAL_SUBMIT_UNSUPPORTED"
    assert facebook_handoff_error["retryable"] is False


def test_queue_publish_blocks_when_preflight_reports_blockers(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session)
    listing.image_urls = []
    listing.listing_images = []
    db_session.add(listing)
    db_session.commit()

    def fake_preflight(self, _db, _listing, _marketplace):
        return {
            "blockers": [{"code": "TEST_BLOCKER", "message": "Blocked", "fix_hint": "Fix it"}],
            "warnings": [],
            "payload_preview": {},
            "policy_summary": {},
            "category_summary": {},
        }

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", fake_preflight)
    monkeypatch.setattr(marketplace_orchestrator.publish_listing_to_marketplace_task, "delay", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not queue")))

    results = marketplace_orchestrator.queue_publish(db_session, listing.id, ["facebook"])

    assert results[0]["status"] == "BLOCKED"
    assert results[0]["error_details"][0]["code"] == "TEST_BLOCKER"


def test_queue_publish_blocks_explicit_inflight_processing_even_when_preflight_is_clear(db_session, monkeypatch):
    user, listing = _seed_user_and_listing(db_session)
    listing.processing_state = "description_generation"
    db_session.add(listing)
    db_session.commit()

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", lambda self, _db, _listing, _marketplace: {
        "blockers": [], "warnings": [], "payload_preview": {}, "policy_summary": {}, "category_summary": {},
    })
    monkeypatch.setattr(marketplace_orchestrator.publish_listing_to_marketplace_task, "delay", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not queue")))

    results = marketplace_orchestrator.queue_publish(db_session, listing.id, ["facebook"])

    assert results[0]["status"] == "BLOCKED"
    assert "processing is not complete" in results[0]["error"].lower()


def test_launch_repair_queue_excludes_live_rows_and_returns_unpublished_blocked_rows(db_session, monkeypatch):
    user = User(email="repair-queue@example.com")
    db_session.add(user)
    db_session.flush()

    live_listing = Listing(
        user_id=user.id,
        status=ListingStatus.ready,
        title="Already Live",
        description="Already live",
        listing_price=25.0,
        ebay_publish_status="POSTED",
    )
    repair_listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Repair Me",
        description="Needs repair",
        listing_price=25.0,
        source_type="amazon_vine",
    )
    db_session.add_all([live_listing, repair_listing])
    db_session.commit()

    def fake_preflight(self, _db, listing, _marketplace):
        if listing.id == live_listing.id:
            return {
                "status": "published",
                "blockers": [],
                "warnings": [],
                "image_summary": {},
                "category_summary": {},
                "shipping_summary": {},
                "condition_summary": {},
                "quality_summary": {},
            }
        return {
            "status": "blocked",
            "blockers": [
                {"code": "CATEGORY_MISSING", "message": "Category missing"},
                {"code": "REFERENCE_IMAGES_ONLY", "message": "Reference only"},
            ],
            "warnings": [],
            "image_summary": {"attached_count": 1, "actual_count": 0, "reference_count": 1},
            "category_summary": {},
            "shipping_summary": {},
            "condition_summary": {},
            "quality_summary": {"score": 72},
        }

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", fake_preflight)

    report = MarketplacePreflightService().launch_repair_queue(
        db_session,
        [live_listing, repair_listing],
        marketplace="ebay",
        max_items=10,
        max_price=50,
    )

    assert report["summary"]["included"] == 1
    assert report["items"][0]["listing_id"] == repair_listing.id
    assert live_listing.id not in [item["listing_id"] for item in report["items"]]


def test_bulk_preflight_reports_mixed_ready_blocked_and_failed_rows(db_session, monkeypatch):
    user = User(email="bulk-preflight@example.com")
    db_session.add(user)
    db_session.flush()

    ready_listing = Listing(
        user_id=user.id,
        status=ListingStatus.ready,
        title="Ready Item",
        description="Ready item description.",
        listing_price=49.99,
    )
    blocked_listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Blocked Item",
        description="Blocked item description.",
        listing_price=0,
    )
    failed_listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Failed Item",
        description="Failed item description.",
        listing_price=25,
    )
    db_session.add_all([ready_listing, blocked_listing, failed_listing])
    db_session.commit()

    def fake_preflight(self, _db, listing, marketplace):
        if listing.id == ready_listing.id:
            status = "ready_with_warnings" if marketplace == "facebook" else "ready"
            warnings = [{"code": "PRICING_WEAK", "message": "Weak pricing", "severity": "warning"}] if marketplace == "facebook" else []
            return {
                "listing_id": listing.id,
                "marketplace": marketplace,
                "status": status,
                "blockers": [],
                "warnings": warnings,
                "missing_fields": [],
                "invalid_fields": [],
                "payload_preview": {"payload": {"sku": f"posterprou1l{listing.id}"}},
                "policy_summary": {},
                "category_summary": {},
                "shipping_summary": {},
                "image_summary": {"attached_count": 1, "actual_count": 1, "reference_only": False},
                "pricing_summary": {"current_price": 49.99, "price_confidence": 0.8},
                "condition_summary": {},
                "quality_summary": {"ready_for_publish_queue": True, "ready_for_ebay": True, "ready_for_facebook": True},
                "readiness_summary": {"shipping_checklist": {"weight_present": True}},
                "last_checked_at": datetime.utcnow(),
                "source_version": "test",
            }
        if listing.id == blocked_listing.id:
            return {
                "listing_id": listing.id,
                "marketplace": marketplace,
                "status": "blocked",
                "blockers": [{"code": "EBAY_PAYMENT_POLICY_MISSING", "message": "eBay payment policy is missing.", "field": "settings.ebay_marketplace_policy_settings.payment_policy_id", "fix_hint": "Add policy"}],
                "warnings": [],
                "missing_fields": ["settings.ebay_marketplace_policy_settings.payment_policy_id"],
                "invalid_fields": [],
                "payload_preview": {"payload": {"sku": f"posterprou1l{listing.id}"}},
                "policy_summary": {},
                "category_summary": {},
                "shipping_summary": {},
                "image_summary": {"attached_count": 1, "actual_count": 1, "reference_only": False},
                "pricing_summary": {"current_price": 25, "price_confidence": 0.6},
                "condition_summary": {},
                "quality_summary": {"ready_for_publish_queue": False},
                "readiness_summary": {"shipping_checklist": {"weight_present": True}},
                "last_checked_at": datetime.utcnow(),
                "source_version": "test",
            }
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", fake_preflight)

    report = MarketplacePreflightService().bulk_preflight_listing_report(
        db_session,
        [ready_listing, blocked_listing, failed_listing],
        ["ebay", "facebook"],
        force_refresh=True,
    )

    assert report["summary"]["total_listings_checked"] == 3
    assert report["summary"]["ready_for_ebay"] == 1
    assert report["summary"]["ready_for_facebook"] == 1
    assert report["summary"]["blocked"] >= 1
    assert report["summary"]["preflight_failed"] == 2
    assert report["summary"]["missing_policies"] >= 1
    assert report["summary"]["weak_pricing"] >= 1
    assert any(item["listing_id"] == failed_listing.id and item["marketplaces"]["ebay"]["status"] == "failed" for item in report["items"])


def test_bulk_publish_ready_dry_run_and_live_queue_behavior(db_session, monkeypatch):
    user = User(email="bulk-publish@example.com")
    db_session.add(user)
    db_session.flush()

    ready_listing = Listing(user_id=user.id, status=ListingStatus.ready, title="Ready Publish", description="Ready.", listing_price=42)
    warning_listing = Listing(user_id=user.id, status=ListingStatus.ready, title="Warning Publish", description="Warn.", listing_price=18)
    blocked_listing = Listing(user_id=user.id, status=ListingStatus.draft, title="Blocked Publish", description="Blocked.", listing_price=5)
    db_session.add_all([ready_listing, warning_listing, blocked_listing])
    db_session.commit()

    def fake_preflight(self, _db, listing, marketplace):
        if listing.id == blocked_listing.id:
            return {
                "listing_id": listing.id,
                "marketplace": marketplace,
                "status": "blocked",
                "blockers": [{"code": "FACEBOOK_PHOTOS_MISSING", "message": "Missing photos", "field": "listing_images", "fix_hint": "Upload photos"}],
                "warnings": [],
                "missing_fields": ["listing_images"],
                "invalid_fields": [],
                "payload_preview": {"payload": {}},
                "policy_summary": {},
                "category_summary": {},
                "shipping_summary": {},
                "image_summary": {"attached_count": 0, "actual_count": 0, "reference_only": False},
                "pricing_summary": {"current_price": 5, "price_confidence": 0.5},
                "condition_summary": {},
                "quality_summary": {"ready_for_publish_queue": False},
                "readiness_summary": {},
                "last_checked_at": datetime.utcnow(),
                "source_version": "test",
            }
        if listing.id == warning_listing.id:
            return {
                "listing_id": listing.id,
                "marketplace": marketplace,
                "status": "ready_with_warnings",
                "blockers": [],
                "warnings": [{"code": "PRICING_WEAK", "message": "Weak pricing", "field": "marketplace_data.pricing_analysis", "fix_hint": "Rerun pricing", "severity": "warning"}],
                "missing_fields": [],
                "invalid_fields": [],
                "payload_preview": {"payload": {}},
                "policy_summary": {},
                "category_summary": {},
                "shipping_summary": {},
                "image_summary": {"attached_count": 1, "actual_count": 1, "reference_only": False},
                "pricing_summary": {"current_price": 18, "price_confidence": 0.3},
                "condition_summary": {},
                "quality_summary": {"ready_for_publish_queue": True},
                "readiness_summary": {},
                "last_checked_at": datetime.utcnow(),
                "source_version": "test",
            }
        return {
            "listing_id": listing.id,
            "marketplace": marketplace,
            "status": "ready",
            "blockers": [],
            "warnings": [],
            "missing_fields": [],
            "invalid_fields": [],
            "payload_preview": {"payload": {}},
            "policy_summary": {},
            "category_summary": {},
            "shipping_summary": {},
            "image_summary": {"attached_count": 1, "actual_count": 1, "reference_only": False},
            "pricing_summary": {"current_price": 42, "price_confidence": 0.9},
            "condition_summary": {},
            "quality_summary": {"ready_for_publish_queue": True},
            "readiness_summary": {},
            "last_checked_at": datetime.utcnow(),
            "source_version": "test",
        }

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", fake_preflight)
    monkeypatch.setattr(marketplace_orchestrator, "_find_pending_marketplace_work", lambda *args, **kwargs: None)

    called = []

    class DummyTask:
        def __init__(self, task_id):
            self.id = task_id

    monkeypatch.setattr(
        marketplace_orchestrator.process_marketplace_crosspost_job_task,
        "apply_async",
        lambda args, **_kwargs: called.append(args[0]) or DummyTask(f"task-{args[0]}"),
    )

    dry_run_report = marketplace_orchestrator.bulk_publish_ready(
        db_session,
        [ready_listing.id, warning_listing.id, blocked_listing.id],
        ["ebay", "facebook"],
        dry_run=True,
    )

    assert called == []
    assert dry_run_report["summary"]["dry_run_ready"] == 2
    assert dry_run_report["summary"]["dry_run_blocked"] >= 2
    assert dry_run_report["summary"]["skipped_warning_requires_confirmation"] == 2

    live_report = marketplace_orchestrator.bulk_publish_ready(
        db_session,
        [ready_listing.id, warning_listing.id, blocked_listing.id],
        ["ebay", "facebook"],
        allow_warnings=False,
        dry_run=False,
        skip_already_queued=True,
    )

    assert live_report["summary"]["queued"] == 2
    assert live_report["summary"]["skipped_warning_requires_confirmation"] == 2
    assert live_report["summary"]["skipped_blocked"] >= 2
    assert len(called) == 1

    called.clear()
    allow_warning_report = marketplace_orchestrator.bulk_publish_ready(
        db_session,
        [ready_listing.id, warning_listing.id, blocked_listing.id],
        ["ebay", "facebook"],
        allow_warnings=True,
        dry_run=False,
        skip_already_queued=True,
    )
    assert allow_warning_report["summary"]["queued"] == 2
    assert allow_warning_report["summary"]["skipped_blocked"] >= 2
    assert len(called) == 1


def test_bulk_publish_ready_skips_already_queued_items(db_session, monkeypatch):
    user = User(email="bulk-publish-skip@example.com")
    db_session.add(user)
    db_session.flush()

    ready_listing = Listing(user_id=user.id, status=ListingStatus.ready, title="Ready Publish Skip", description="Ready.", listing_price=42)
    db_session.add(ready_listing)
    db_session.commit()

    def fake_preflight(self, _db, listing, marketplace):
        return {
            "listing_id": listing.id,
            "marketplace": marketplace,
            "status": "ready",
            "blockers": [],
            "warnings": [],
            "missing_fields": [],
            "invalid_fields": [],
            "payload_preview": {"payload": {}},
            "policy_summary": {},
            "category_summary": {},
            "shipping_summary": {},
            "image_summary": {"attached_count": 1, "actual_count": 1, "reference_only": False},
            "pricing_summary": {"current_price": 42, "price_confidence": 0.9},
            "condition_summary": {},
            "quality_summary": {"ready_for_publish_queue": True},
            "readiness_summary": {},
            "last_checked_at": datetime.utcnow(),
            "source_version": "test",
        }

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", fake_preflight)
    monkeypatch.setattr(
        marketplace_orchestrator,
        "_find_pending_marketplace_work",
        lambda _db, listing_id, marketplace: {"kind": "marketplace_listing", "id": 99} if listing_id == ready_listing.id and marketplace == "ebay" else None,
    )

    called = []

    class DummyTask:
        def __init__(self, task_id):
            self.id = task_id

    monkeypatch.setattr(
        marketplace_orchestrator.process_marketplace_crosspost_job_task,
        "apply_async",
        lambda args, **_kwargs: called.append(args[0]) or DummyTask(f"task-{args[0]}"),
    )

    report = marketplace_orchestrator.bulk_publish_ready(
        db_session,
        [ready_listing.id],
        ["ebay", "facebook"],
        dry_run=False,
        skip_already_queued=True,
    )

    assert report["summary"]["queued"] == 1
    assert report["summary"]["skipped_already_queued"] == 1
    assert len(called) == 1


def test_bulk_preflight_csv_export_contains_expected_columns():
    report = {
        "items": [
            {
                "listing_id": 1,
                "title": "Camera",
                "marketplaces": {
                    "ebay": {
                        "status": "blocked",
                        "blocker_count": 1,
                        "warning_count": 0,
                        "blocker_codes": ["EBAY_PAYMENT_POLICY_MISSING"],
                        "blocker_messages": ["eBay payment policy is missing."],
                        "warning_codes": [],
                        "warning_messages": [],
                        "missing_fields": ["settings.ebay_marketplace_policy_settings.payment_policy_id"],
                        "price": 19.99,
                        "category": "Camera & Photo",
                        "condition": "Used",
                        "image_count": 2,
                        "actual_image_count": 1,
                        "package_weight": 3.4,
                        "package_dimensions": {"length": 10, "width": 8, "height": 6},
                        "last_preflight_at": datetime.utcnow(),
                    }
                },
            }
        ]
    }
    csv_content = _bulk_preflight_response_to_csv(report)
    header = csv_content.splitlines()[0]
    assert "listing_id" in header
    assert "blocker_codes" in header
    assert "package_dimensions" in header


def test_launch_candidate_selector_excludes_already_published_and_risky_rows(db_session, monkeypatch):
    user = User(email="launch-candidates@example.com")
    db_session.add(user)
    db_session.flush()

    ready_listing = Listing(user_id=user.id, status=ListingStatus.ready, title="Ready Candidate", description="Ready.", listing_price=35)
    published_listing = Listing(user_id=user.id, status=ListingStatus.posted, title="Published Candidate", description="Live.", listing_price=28)
    pricey_listing = Listing(user_id=user.id, status=ListingStatus.ready, title="Pricy Candidate", description="Expensive.", listing_price=85)
    reference_listing = Listing(user_id=user.id, status=ListingStatus.ready, title="Reference Candidate", description="Needs photos.", listing_price=19)
    db_session.add_all([ready_listing, published_listing, pricey_listing, reference_listing])
    db_session.flush()

    reference_listing.listing_images = [
        {
            "storage_path": "/media/uploads/ref.jpg",
            "source_platform": "amazon_vine",
            "operator_state": "suggested",
            "display_order": 0,
            "is_reference": True,
            "confidence": 0.4,
        }
    ]
    db_session.add(reference_listing)
    db_session.commit()

    def fake_preflight(self, _db, listing, marketplace):
        if listing.id == ready_listing.id:
            return {
                "listing_id": listing.id,
                "marketplace": marketplace,
                "status": "ready",
                "blockers": [],
                "warnings": [],
                "missing_fields": [],
                "invalid_fields": [],
                "payload_preview": {"payload": {"sku": "posterpro-ready"}},
                "policy_summary": {},
                "category_summary": {},
                "shipping_summary": {"local_pickup_recommended": False, "oversize": False, "hazmat": False, "battery": False, "liquid": False},
                "image_summary": {"actual_image_present": True, "reference_only": False},
                "pricing_summary": {"current_price": 35, "price_confidence": 0.9},
                "condition_summary": {},
                "quality_summary": {"score": 95, "ready_for_publish_queue": True},
                "readiness_summary": {},
                "last_checked_at": datetime.utcnow(),
                "source_version": "test",
            }
        if listing.id == pricey_listing.id:
            return {
                "listing_id": listing.id,
                "marketplace": marketplace,
                "status": "ready",
                "blockers": [],
                "warnings": [],
                "missing_fields": [],
                "invalid_fields": [],
                "payload_preview": {"payload": {"sku": "posterpro-pricey"}},
                "policy_summary": {},
                "category_summary": {},
                "shipping_summary": {"local_pickup_recommended": False, "oversize": False, "hazmat": False, "battery": False, "liquid": False},
                "image_summary": {"actual_image_present": True, "reference_only": False},
                "pricing_summary": {"current_price": 85, "price_confidence": 0.9},
                "condition_summary": {},
                "quality_summary": {"score": 90, "ready_for_publish_queue": True},
                "readiness_summary": {},
                "last_checked_at": datetime.utcnow(),
                "source_version": "test",
            }
        if listing.id == reference_listing.id:
            return {
                "listing_id": listing.id,
                "marketplace": marketplace,
                "status": "ready",
                "blockers": [],
                "warnings": [{"code": "REFERENCE_ONLY_IMAGES", "message": "Only reference images are attached.", "field": "listing_images", "fix_hint": "Add actual photos", "severity": "warning"}],
                "missing_fields": ["listing_images"],
                "invalid_fields": [],
                "payload_preview": {"payload": {"sku": "posterpro-ref"}},
                "policy_summary": {},
                "category_summary": {},
                "shipping_summary": {"local_pickup_recommended": False, "oversize": False, "hazmat": False, "battery": False, "liquid": False},
                "image_summary": {"actual_image_present": False, "reference_only": True},
                "pricing_summary": {"current_price": 19, "price_confidence": 0.6},
                "condition_summary": {},
                "quality_summary": {"score": 30, "ready_for_publish_queue": False},
                "readiness_summary": {},
                "last_checked_at": datetime.utcnow(),
                "source_version": "test",
            }
        raise AssertionError("preflight should not run for published listings")

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", fake_preflight)

    report = MarketplacePreflightService().launch_candidates(
        db_session,
        [ready_listing, published_listing, pricey_listing, reference_listing],
        marketplace="ebay",
        max_items=10,
        max_price=50,
    )

    candidate_ids = [item["listing_id"] for item in report["candidates"]]
    excluded_reasons = " ".join(item.get("reason_excluded") or item.get("reason") or "" for item in report["excluded"])

    assert candidate_ids == [ready_listing.id]
    assert "already live or marked posted" in excluded_reasons
    assert "exceeds threshold" in excluded_reasons
    assert "No approved actual photos" in excluded_reasons


def test_ebay_account_health_reports_readiness_state(db_session):
    user = User(email="ebay-health@example.com")
    db_session.add(user)
    db_session.flush()
    account = MarketplaceAccount(
        user_id=user.id,
        marketplace=MarketplaceName.ebay,
        external_account_id="ebay-user-42",
        access_token="token",
        refresh_token=None,
    )
    db_session.add(account)
    db_session.commit()

    health = summarize_ebay_account_health(account)

    assert health["connected"] is True
    assert health["has_refresh_token"] is False
    assert health["import_ready"] is True
    assert health["reconnect_required"] is False
    assert "connected" in health["status_note"].lower()
