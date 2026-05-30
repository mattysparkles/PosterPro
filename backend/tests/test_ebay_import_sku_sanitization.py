import pytest

from app.services import ebay_service
from app.services.ebay_service import EbayIntegrationError, get_active_ebay_listings


@pytest.mark.anyio
async def test_get_active_ebay_listings_skips_invalid_sku_offers(monkeypatch):
    class _Account:
        access_token = "token"

    async def _fake_get_or_refresh_account(user_id, db):  # noqa: ARG001
        return _Account()

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", _fake_get_or_refresh_account)

    calls = []

    async def _fake_request(self, method, path, *, payload=None, params=None, headers=None, retries=3):  # noqa: ARG001
        calls.append((method, path, params))
        if path == "/sell/inventory/v1/offer" and params and params.get("limit") == 2:
            raise EbayIntegrationError(
                'eBay API request failed (400) for /sell/inventory/v1/offer: {"errors":[{"message":"This is an invalid value for a SKU."}]}'
            )
        if path == "/sell/inventory/v1/offer" and params and params.get("limit") == 1 and params.get("offset") == 0:
            return {
                "offers": [
                    {"sku": "BAD SKU!", "listingStatus": "ACTIVE", "pricingSummary": {"price": {"value": 12.34}}},
                ]
            }
        if path == "/sell/inventory/v1/offer" and params and params.get("limit") == 1 and params.get("offset") == 1:
            return {
                "offers": [
                    {"sku": "GOODSKU1", "listingStatus": "ACTIVE", "pricingSummary": {"price": {"value": 55.0}}, "listingId": "123"},
                ]
            }
        if path == "/sell/inventory/v1/inventory_item/GOODSKU1":
            return {
                "product": {"title": "Test", "description": "Desc", "imageUrls": []},
                "availability": {"shipToLocationAvailability": {"quantity": 1}},
                "condition": "USED",
            }
        raise AssertionError(f"Unexpected request: {method} {path} {params}")

    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", _fake_request, raising=True)

    items = await get_active_ebay_listings(1, db=None, limit=2)  # type: ignore[arg-type]
    assert len(items) == 1
    assert items[0]["source_identifiers"]["sku"] == "GOODSKU1"

