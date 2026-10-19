import asyncio
from types import SimpleNamespace

import pytest

from app.services import ebay_fitment_research as fitment

from app.models.enums import ListingStatus
from app.models.models import Listing, User


def _listing(*, brand="Dorman", mpn="600-103", gtin=None):
    return SimpleNamespace(
        brand=brand,
        item_specifics={**({"Brand": brand} if brand else {}), **({"MPN": mpn} if mpn else {}), **({"UPC": gtin} if gtin else {})},
        title="Dorman 4WD actuator",
    )


def test_fitment_research_requires_one_exact_brand_mpn_match(monkeypatch):
    calls = []

    async def fake_request(self, method, path, *, payload=None, params=None, headers=None, **kwargs):
        calls.append((method, path, payload, params, headers))
        if path.endswith("product_summary/search"):
            return {"productSummaries": [
                {"epid": "noise", "brand": "Other", "mpn": ["600103"], "title": "Dorman part"},
                {"epid": "exact", "brand": "Dorman", "mpn": ["600103"], "title": "Dorman actuator"},
            ]}
        if path.endswith("/product/exact"):
            return {"epid": "exact", "brand": "Dorman", "mpn": ["600103"], "title": "Dorman actuator", "aspects": []}
        if path.endswith("get_product_compatibilities"):
            return {"compatibilityDetails": [{"compatibilityProperties": [
                {"name": "Year", "value": "2007"}, {"name": "Make", "value": "Buick"},
                {"name": "Model", "value": "Rainier"},
            ]}]}
        raise AssertionError(path)

    monkeypatch.setattr(fitment.EbayAPIClient, "request", fake_request)
    result = asyncio.run(fitment.research_ebay_product_fitment(_listing(), SimpleNamespace(access_token="test")))

    assert result["status"] == "compatibility_records_found"
    assert result["product"]["epid"] == "exact"
    assert result["compatibilities"][0]["properties"] == [
        {"name": "Year", "value": "2007"}, {"name": "Make", "value": "Buick"},
        {"name": "Model", "value": "Rainier"},
    ]
    search_call = calls[0]
    assert search_call[3] == {"limit": 50, "mpn": "600-103"}
    compatibility_call = calls[-1]
    assert compatibility_call[2] == {"productIdentifier": {"epid": "exact"}}
    assert compatibility_call[4]["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_MOTORS_US"


def test_fitment_research_does_not_select_ambiguous_or_wrong_brand_products(monkeypatch):
    calls = []

    async def fake_request(self, method, path, *, payload=None, params=None, headers=None, **kwargs):
        calls.append(path)
        return {"productSummaries": [
            {"epid": "wrong-brand", "brand": "Other", "mpn": ["600103"]},
            {"epid": "dorman-a", "brand": "Dorman", "mpn": ["600103"]},
            {"epid": "dorman-b", "brand": "Dorman", "mpn": ["600-103"]},
        ]}

    monkeypatch.setattr(fitment.EbayAPIClient, "request", fake_request)
    result = asyncio.run(fitment.research_ebay_product_fitment(_listing(), SimpleNamespace(access_token="test")))

    assert result["status"] == "ambiguous_exact_catalog_match"
    assert len([path for path in calls if "/product/" in path]) == 0
    assert not any("get_product_compatibilities" in path for path in calls)


def test_fitment_research_empty_compatibility_is_not_a_no_fitment_claim(monkeypatch):
    async def fake_request(self, method, path, *, payload=None, params=None, headers=None, **kwargs):
        if path.endswith("product_summary/search"):
            return {"productSummaries": [{"epid": "exact", "brand": "Dorman", "mpn": ["600103"], "title": "Dorman actuator"}]}
        if path.endswith("/product/exact"):
            return {"epid": "exact", "brand": "Dorman", "mpn": ["600103"], "title": "Dorman actuator"}
        if path.endswith("get_product_compatibilities"):
            return {"compatibilityDetails": []}
        raise AssertionError(path)

    monkeypatch.setattr(fitment.EbayAPIClient, "request", fake_request)
    result = asyncio.run(fitment.research_ebay_product_fitment(_listing(), SimpleNamespace(access_token="test")))

    assert result["status"] == "compatibility_records_unavailable"
    assert result["compatibilities"] == []
    assert "do not infer vehicle compatibility" in result["blocker"]


def test_fitment_research_requires_brand_mpn_or_gtin_without_provider_call(monkeypatch):
    async def unexpected(*args, **kwargs):
        raise AssertionError("provider must not be queried without identity")

    monkeypatch.setattr(fitment.EbayAPIClient, "request", unexpected)
    result = asyncio.run(fitment.research_ebay_product_fitment(_listing(brand="", mpn=""), SimpleNamespace(access_token="test")))

    assert result["status"] == "insufficient_identity"
    assert result["compatibilities"] == []


def test_fitment_research_accepts_exact_gtin_when_catalog_metadata_agrees(monkeypatch):
    calls = []

    async def fake_request(self, method, path, *, payload=None, params=None, headers=None, **kwargs):
        calls.append((path, params, payload))
        if path.endswith("product_summary/search"):
            return {"productSummaries": [{"epid": "gtin-product", "gtin": ["00012345678905"], "title": "GTIN item"}]}
        if path.endswith("/product/gtin-product"):
            return {"epid": "gtin-product", "gtin": ["00012345678905"], "title": "GTIN item"}
        if path.endswith("get_product_compatibilities"):
            return {"compatibilityDetails": []}
        raise AssertionError(path)

    monkeypatch.setattr(fitment.EbayAPIClient, "request", fake_request)
    result = asyncio.run(fitment.research_ebay_product_fitment(
        _listing(brand="", mpn="", gtin="00012345678905"), SimpleNamespace(access_token="test"),
    ))

    assert calls[0][1] == {"limit": 50, "gtin": "00012345678905"}
    assert result["product"]["epid"] == "gtin-product"
    assert result["status"] == "compatibility_records_unavailable"


def test_fitment_research_api_persists_only_evidence_and_keeps_listing_unmodified(db_session, monkeypatch):
    from app.api import intelligence

    user = User(email="fitment-evidence@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, status=ListingStatus.draft, title="Dorman 4WD actuator",
        description="Part needs fitment review", condition="New", quantity=1,
        listing_price=25, item_specifics={"Brand": "Dorman", "MPN": "600-103"},
        marketplace_data={
            "pricing_analysis": {"recommendation_eligible": False},
            "vehicle_fitment_research": {"manufacturer_sources": [{"source_name": "Dorman", "source_url": "https://example.test/dorman"}]},
        },
    )
    db_session.add(listing)
    db_session.commit()
    original = (listing.title, listing.listing_price, listing.status, listing.processing_state)

    async def fake_account(user_id, db):
        assert user_id == user.id
        return SimpleNamespace(access_token="test")

    async def fake_research(item, account):
        assert item.id == listing.id
        return {"status": "compatibility_records_unavailable", "compatibilities": [], "blocker": "No sourced fitment."}

    monkeypatch.setattr(intelligence, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(intelligence, "research_ebay_product_fitment", fake_research)
    result = asyncio.run(intelligence.research_listing_fitment(listing.id, db_session, user))
    db_session.refresh(listing)

    assert result["listing_content_changed"] is False
    assert result["readiness_changed"] is False
    assert listing.marketplace_data["vehicle_fitment_research"]["status"] == "compatibility_records_unavailable"
    assert listing.marketplace_data["vehicle_fitment_research"]["manufacturer_sources"] == [
        {"source_name": "Dorman", "source_url": "https://example.test/dorman"}
    ]
    assert (listing.title, listing.listing_price, listing.status, listing.processing_state) == original
