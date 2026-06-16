from uuid import uuid4

import pytest

from app.core import database as database_module
from app.models.enums import EbayPublishStatus
from app.models.models import Cluster, Listing, MarketplaceAccount
import app.api.ebay as ebay_api
import app.services.ebay_service as ebay_service

def seed_listing(user_id: int) -> int:
    db = database_module.SessionLocal()
    cluster = Cluster(user_id=user_id, title_hint="Lamp")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    listing = Listing(user_id=user_id, cluster_id=cluster.id, title="Lamp", description="Desc")
    db.add(listing)
    db.commit()
    db.refresh(listing)
    db.close()
    return listing.id


@pytest.mark.anyio
async def test_publish_success(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "eBay Owner",
            "email": f"demo-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    async def fake_publish(listing, db):
        listing.ebay_publish_status = EbayPublishStatus.POSTED
        listing.ebay_listing_id = "12345"
        listing.marketplace_data = {"ebay_url": "https://www.ebay.com/itm/12345"}
        db.add(listing)
        db.commit()
        return {"listing_id": "12345", "status": "POSTED", "ebay_url": "https://www.ebay.com/itm/12345"}

    monkeypatch.setattr(ebay_service, "publish_listing_to_ebay", fake_publish)
    monkeypatch.setattr(ebay_api, "publish_listing_to_ebay", fake_publish)
    monkeypatch.setattr(ebay_api, "validate_marketplace_readiness", lambda **kwargs: [])

    response = await async_client.post(
        f"/listings/{listing_id}/publish/ebay",
        json={"confirm_live_publish": True, "confirmation_phrase": "QUEUE LIVE EBAY READY LISTINGS"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "POSTED"


@pytest.mark.anyio
async def test_publish_failure(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "eBay Owner",
            "email": f"demo-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    async def fake_publish(_listing, _db):
        raise ebay_service.EbayIntegrationError("retry exhausted")

    monkeypatch.setattr(ebay_service, "publish_listing_to_ebay", fake_publish)
    monkeypatch.setattr(ebay_api, "publish_listing_to_ebay", fake_publish)
    monkeypatch.setattr(ebay_api, "validate_marketplace_readiness", lambda **kwargs: [])

    response = await async_client.post(
        f"/listings/{listing_id}/publish/ebay",
        json={"confirm_live_publish": True, "confirmation_phrase": "QUEUE LIVE EBAY READY LISTINGS"},
    )
    assert response.status_code == 400
    assert "retry exhausted" in response.json()["detail"]


@pytest.mark.anyio
async def test_publish_attempt_audit_trail_records_payload_hash_and_translation(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Audit Owner",
            "email": f"audit-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    account_db = database_module.SessionLocal()
    account = MarketplaceAccount(
        user_id=user_id,
        marketplace="ebay",
        external_account_id="ebay-user-audit",
        access_token="token",
        refresh_token="refresh",
    )
    account_db.add(account)
    account_db.commit()
    account_db.close()

    async def fake_plan(listing, _db, allow_create_policies=True, marketplace_id="EBAY_US"):
        return {
            "category": {"category_id": "30090", "category_name": "Camera & Photo", "metadata_available": True},
            "category_summary": {},
            "aspect_summary": {"required": ["Brand"], "recommended": [], "missing_required": [], "unsupported": []},
            "policy_settings": {
                "payment_policy_id": "payment-1",
                "fulfillment_policy_id": "fulfillment-1",
                "return_policy_id": "return-1",
                "merchant_location_key": f"posterpro-{listing.user_id}",
                "shipping_service_code": "USPSGroundAdvantage",
                "handling_time_days": 1,
                "local_pickup_allowed": False,
                "calculated_shipping": False,
                "package_weight_required": True,
                "package_dimensions_required": True,
            },
            "policy_ids": {},
            "inventory_item_payload": {"sku": f"posterpro-{listing.user_id}-{listing.id}"},
            "offer_payload": {"sku": f"posterpro-{listing.user_id}-{listing.id}", "categoryId": "30090"},
            "payload_preview": {
                "sku": f"posterpro-{listing.user_id}-{listing.id}",
                "title": listing.title,
                "description": listing.description,
                "condition": "NEW",
                "conditionDescription": listing.description,
                "category_id": "30090",
                "quantity": 1,
                "price": 19.99,
                "currency": "USD",
                "product_identifiers": {},
                "item_specifics": {"Brand": ["Lamp"]},
                "image_urls": ["/media/uploads/lamp.jpg"],
                "packageWeightAndSize": {"package_weight": 2.0, "package_dimensions": {"length": 10, "width": 8, "height": 6}},
                "shipping_policy": {"service": "USPSGroundAdvantage"},
                "marketplaceId": "EBAY_US",
                "listing_format": "FIXED_PRICE",
                "duration": "GTC",
                "site": "EBAY_US",
            },
            "image_summary": {"image_count": 1, "actual_image_count": 1, "reference_only": False},
            "shipping_summary": {"shipping_profile": {"package_weight": 2.0, "package_dimensions": {"length": 10, "width": 8, "height": 6}}, "shipping_policy": {}},
            "account_summary": {"external_account_id": "ebay-user-audit"},
        }

    async def fake_get_or_refresh_account(_user_id, _db):
        return account

    async def fake_create_inventory_location(*args, **kwargs):
        return {"merchantLocationKey": f"posterpro-{user_id}"}

    async def fake_create_or_replace_item(*args, **kwargs):
        return {"sku": f"posterpro-{user_id}-{listing_id}", "response": {"status": "OK"}}

    async def fake_create_offer_for_item(*args, **kwargs):
        raise ebay_service.EbayIntegrationError("Invalid access token")

    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_get_or_refresh_account)
    monkeypatch.setattr(ebay_service, "create_inventory_location", fake_create_inventory_location)
    monkeypatch.setattr(ebay_service, "create_or_replace_item", fake_create_or_replace_item)
    monkeypatch.setattr(ebay_service, "create_offer_for_item", fake_create_offer_for_item)
    monkeypatch.setattr(ebay_api, "validate_marketplace_readiness", lambda **kwargs: [])

    response = await async_client.post(
        f"/listings/{listing_id}/publish/ebay",
        json={"confirm_live_publish": True, "confirmation_phrase": "QUEUE LIVE EBAY READY LISTINGS"},
    )
    assert response.status_code == 400

    inspect_db = database_module.SessionLocal()
    stored_listing = inspect_db.get(Listing, listing_id)
    assert stored_listing.publish_attempts
    attempt = stored_listing.publish_attempts[0]
    assert attempt.payload_hash
    assert attempt.payload_snapshot["listing_id"] == listing_id
    assert attempt.translated_error["code"] == "EBAY_OAUTH_EXPIRED"
    assert "token" not in str(attempt.payload_snapshot).lower()
    inspect_db.close()
