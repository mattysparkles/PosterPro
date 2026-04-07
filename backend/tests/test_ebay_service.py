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


class DummyAccount:
    def __init__(self):
        self.access_token = "token"


class DummyDB:
    def add(self, _obj):
        return None

    def commit(self):
        return None

    def refresh(self, _obj):
        return None


def test_create_offer_for_item_uses_policy_ids(monkeypatch):
    listing = DummyListing()
    account = DummyAccount()

    async def fake_request(self, method, path, **kwargs):
        assert method == "POST"
        assert path == "/sell/inventory/v1/offer"
        payload = kwargs["payload"]
        assert payload["pricingSummary"]["price"]["currency"] == "USD"
        assert payload["listingPolicies"]["paymentPolicyId"] == "PAY-1"
        return {"offerId": "offer-123"}

    async def fake_policies(_token, marketplace_id="EBAY_US"):
        assert marketplace_id == "EBAY_US"
        return {
            "paymentPolicyId": "PAY-1",
            "fulfillmentPolicyId": "SHIP-1",
            "returnPolicyId": "RET-1",
        }

    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", fake_request)
    monkeypatch.setattr(ebay_service, "get_business_policy_ids", fake_policies)

    result = asyncio.run(ebay_service.create_offer_for_item(listing, account, "sku-1"))
    assert result["offerId"] == "offer-123"


def test_publish_listing_to_ebay_failure_sets_failed(monkeypatch):
    listing = DummyListing()
    db = DummyDB()

    async def fake_get_account(_user_id, _db):
        return DummyAccount()

    async def boom(*_args, **_kwargs):
        raise ebay_service.EbayIntegrationError("upstream down")

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_get_account)
    monkeypatch.setattr(ebay_service, "create_inventory_location", boom)

    try:
        asyncio.run(ebay_service.publish_listing_to_ebay(listing, db))
    except ebay_service.EbayIntegrationError:
        pass

    assert listing.ebay_publish_status == EbayPublishStatus.FAILED
    assert "upstream down" in listing.marketplace_data["error"]
