from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceAccount, MarketplaceListing, MarketplaceMetadataCache, User
from app.api.marketplaces import _bulk_preflight_response_to_csv
from app.services.ebay_service import _cached_category_aspects, build_ebay_publish_plan
from app.services.ebay_service import _sync_ebay_marketplace_listing
from app.services.marketplace_error_translation import translate_marketplace_error
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.ebay_service import summarize_ebay_account_health
from app.services import marketplace_orchestrator


def _seed_user_and_listing(db_session, *, title="Canon EOS Camera", condition="Used"):
    user = User(email=f"{title.replace(' ', '-').lower()}@example.com")
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
        },
        listing_price=149.99,
    )
    db_session.add(listing)
    db_session.flush()
    return user, listing


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
            "fulfillmentPolicyId": "fulfillment-1",
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
            "fulfillmentPolicyId": "fulfillment-1",
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
            "fulfillmentPolicyId": "fulfillment-1",
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
            "fulfillmentPolicyId": "fulfillment-1",
            "returnPolicyId": "return-1",
        }

    monkeypatch.setattr("app.services.ebay_service.get_or_refresh_account", fake_get_or_refresh_account)
    monkeypatch.setattr("app.services.ebay_service.suggest_ebay_category", fake_suggest_ebay_category)
    monkeypatch.setattr("app.services.ebay_service._cached_category_aspects", fake_cached_category_aspects)
    monkeypatch.setattr("app.services.ebay_service.get_business_policy_ids", fake_get_business_policy_ids)
    monkeypatch.setattr("app.services.ebay_service._build_ebay_image_urls", lambda _listing: ["/media/uploads/starter.jpg"])

    plan = asyncio.run(build_ebay_publish_plan(listing, db_session, allow_create_policies=False))

    assert plan["category"]["category_id"] == "171485"


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
    assert result["policy_summary"]["browser_bridge_required"] is True


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

    assert "ACTUAL_PHOTOS_MISSING" in blocker_codes
    assert "EBAY_IMAGE_URL_INVALID" not in blocker_codes


def test_marketplace_error_translation_normalizes_common_failures():
    ebay_error = translate_marketplace_error("ebay", '{"message":"Invalid access token"}')
    facebook_error = translate_marketplace_error("facebook", "Browser not connected for assisted publish")
    ebay_shipping_error = translate_marketplace_error("ebay", "invalid shipping policy id")
    facebook_handoff_error = translate_marketplace_error("facebook", "final submit unsupported in this workspace")

    assert ebay_error["code"] == "EBAY_OAUTH_EXPIRED"
    assert ebay_error["retryable"] is False
    assert "Reconnect eBay" in ebay_error["fix_hint"]
    assert ebay_shipping_error["code"] == "EBAY_POLICY_MISSING"
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
        "delay",
        lambda job_id: called.append(job_id) or DummyTask(f"task-{job_id}"),
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
        "delay",
        lambda job_id: called.append(job_id) or DummyTask(f"task-{job_id}"),
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
