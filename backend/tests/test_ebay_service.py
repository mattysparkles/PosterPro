import asyncio

from app.models.enums import EbayPublishStatus
from app.services import ebay_service


class DummyListing:
    def __init__(self):
        self.id = 5
        self.user_id = 1
        self.title = "Vintage Lamp"
        self.description = "Great condition"
        self.category_suggestion = "171485"
        self.suggested_price = 44.5
        self.ebay_publish_status = EbayPublishStatus.DRAFT
        self.marketplace_data = None
        self.ebay_listing_id = None
        self.publish_attempts = []


class DummyAccount:
    def __init__(self):
        self.access_token = "token"
        self.refresh_token = "refresh-token"


class DummyDB:
    def add(self, _obj):
        return None

    def flush(self):
        return None

    def commit(self):
        return None

    def refresh(self, _obj):
        return None


def test_create_offer_for_item_uses_policy_ids(monkeypatch):
    listing = DummyListing()
    account = DummyAccount()
    calls = {"attempt": 0}

    async def fake_request(self, method, path, **kwargs):
        payload = kwargs["payload"]
        if path == "/sell/inventory/v1/offer" and calls["attempt"] == 0:
            calls["attempt"] += 1
            raise ebay_service.EbayIntegrationError("not eligible for business policy")
        assert method == "POST"
        assert path == "/sell/inventory/v1/offer"
        assert payload["pricingSummary"]["price"]["currency"] == "USD"
        assert payload["listingPolicies"]["paymentPolicyId"] == "PAY-1"
        return {"offerId": "offer-123"}

    async def fake_policies(_token, marketplace_id="EBAY_US", create_if_missing=False):
        assert marketplace_id == "EBAY_US"
        assert create_if_missing is True
        return {
            "paymentPolicyId": "PAY-1",
            "fulfillmentPolicyId": "SHIP-1",
            "returnPolicyId": "RET-1",
        }

    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", fake_request)
    monkeypatch.setattr(ebay_service, "get_business_policy_ids", fake_policies)

    result = asyncio.run(ebay_service.create_offer_for_item(listing, account, "sku-1", category_id="171485"))
    assert result["offerId"] == "offer-123"


def test_publish_listing_to_ebay_failure_sets_failed(monkeypatch):
    listing = DummyListing()
    db = DummyDB()

    async def fake_get_account(_user_id, _db):
        return DummyAccount()

    async def fake_plan(_listing, _db, allow_create_policies=True):
        return {
            "category": {"category_id": "171485", "category_name": "Parts", "metadata_available": True},
            "policy_settings": {"merchant_location_key": "posterpro-1"},
            "inventory_item_payload": {"sku": "sku-1", "product": {"imageUrls": ["https://example.com/image.jpg"]}},
            "offer_payload": {"sku": "sku-1", "categoryId": "171485"},
            "payload_preview": {"item_specifics": {"Brand": ["Lamp"]}},
        }

    async def boom(*_args, **_kwargs):
        raise ebay_service.EbayIntegrationError("upstream down")

    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *args, **kwargs: None)
    monkeypatch.setattr(ebay_service, "_start_publish_attempt", lambda *args, **kwargs: object())
    monkeypatch.setattr(ebay_service, "_finish_publish_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_get_account)
    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "create_inventory_location", boom)

    try:
        asyncio.run(ebay_service.publish_listing_to_ebay(listing, db))
    except ebay_service.EbayIntegrationError:
        pass

    assert listing.ebay_publish_status == EbayPublishStatus.FAILED
    assert "upstream down" in listing.marketplace_data["error"]


def test_publish_listing_to_ebay_invalid_item_error_sets_failed(monkeypatch):
    listing = DummyListing()
    db = DummyDB()
    account = DummyAccount()
    calls = {"item": 0}

    async def fake_get_account(_user_id, _db):
        return account

    async def fake_location(_user_id, _db, **kwargs):
        return {"merchantLocationKey": "posterpro-1"}

    async def fake_plan(_listing, _db, allow_create_policies=True):
        return {
            "category": {"category_id": "171485", "category_name": "Parts", "metadata_available": True},
            "policy_settings": {"merchant_location_key": "posterpro-1"},
            "inventory_item_payload": {"sku": "sku-1", "product": {"imageUrls": ["https://example.com/image.jpg"]}},
            "offer_payload": {"sku": "sku-1", "categoryId": "171485"},
            "payload_preview": {"item_specifics": {"Brand": ["Lamp"]}},
        }

    async def fake_item(_listing, _account, **kwargs):
        calls["item"] += 1
        raise ebay_service.EbayIntegrationError("Invalid access token")

    async def fake_offer(_listing, _account, _sku, **kwargs):
        return {"offerId": "offer-1", "response": {}}

    async def fake_publish(_listing, _account, _offer_id):
        return {"listingId": "12345", "response": {}}

    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *args, **kwargs: None)
    monkeypatch.setattr(ebay_service, "_start_publish_attempt", lambda *args, **kwargs: object())
    monkeypatch.setattr(ebay_service, "_finish_publish_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_get_account)
    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "create_inventory_location", fake_location)
    monkeypatch.setattr(ebay_service, "create_or_replace_item", fake_item)
    monkeypatch.setattr(ebay_service, "create_offer_for_item", fake_offer)
    monkeypatch.setattr(ebay_service, "publish_offer", fake_publish)

    try:
        asyncio.run(ebay_service.publish_listing_to_ebay(listing, db))
    except ebay_service.EbayIntegrationError:
        pass

    assert calls["item"] == 1
    assert listing.ebay_publish_status == EbayPublishStatus.FAILED
