from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.core import database as database_module
from app.models.enums import EbayPublishStatus, ListingStatus
from app.models.models import Listing
from app.services.canonical_readiness import canonical_listing_readiness, resolve_authoritative_listing_price


def _listing(user_id: int, **kwargs) -> Listing:
    defaults = {
        "user_id": user_id,
        "status": ListingStatus.draft,
        "title": "Aggregate test product",
        "description": "A concrete product description for aggregate testing.",
        "source_type": "amazon_vine",
        "quantity": 1,
        "needs_review": False,
    }
    defaults.update(kwargs)
    return Listing(**defaults)


@pytest.mark.anyio
async def test_listing_aggregate_is_filter_and_page_invariant(async_client):
    register = await async_client.post(
        "/auth/register",
        json={"full_name": "Aggregate Owner", "email": f"aggregate-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    db = database_module.SessionLocal()
    db.add_all([
        _listing(user_id, title="RV cover", listing_price=10),
        _listing(user_id, title="RV light", listing_price=20),
        _listing(user_id, title="RV missing price", listing_price=None, suggested_price=None),
        _listing(user_id, title="RV placeholder", listing_price=22, marketplace_data={"pricing_analysis": {"provenance": "placeholder"}}),
        _listing(user_id, title="Kitchen item", listing_price=100),
    ])
    db.commit()
    db.close()

    params = {"source_type": "amazon_vine", "queue": "all", "search": "RV", "sort_by": "created", "sort_dir": "desc"}
    page_one = (await async_client.get("/listings", params={**params, "page": 1, "page_size": 1})).json()
    page_two = (await async_client.get("/listings", params={**params, "page": 2, "page_size": 100})).json()
    sorted_other_way = (await async_client.get("/listings", params={**params, "page": 1, "page_size": 25, "sort_by": "updated", "sort_dir": "asc"})).json()
    assert page_one["total"] == page_two["total"] == 4
    assert page_one["aggregate_value"] == page_two["aggregate_value"] == 30
    assert page_one["average_value"] == page_two["average_value"] == 15
    assert page_one["missing_price_count"] == page_two["missing_price_count"] == 2
    assert (sorted_other_way["total"], sorted_other_way["aggregate_value"], sorted_other_way["average_value"]) == (4, 30, 15)


@pytest.mark.anyio
async def test_listing_aggregate_sold_prefers_confirmed_sale_price(async_client):
    register = await async_client.post(
        "/auth/register",
        json={"full_name": "Sold Aggregate Owner", "email": f"sold-aggregate-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    db = database_module.SessionLocal()
    db.add(_listing(user_id, title="Sold item", listing_price=99, sale_price=7, sold_at=datetime.utcnow(), quantity=0))
    db.commit()
    db.close()
    payload = (await async_client.get("/listings", params={"queue": "sold", "page": 1, "page_size": 25})).json()
    assert payload["total"] == 1
    assert payload["aggregate_value"] == 7
    assert payload["average_value"] == 7


@pytest.mark.anyio
async def test_published_summary_uses_entire_verified_population_and_live_prices(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={"full_name": "Published Aggregate Owner", "email": f"published-aggregate-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    external_ids = {f"LIVE-{index}" for index in range(100)}
    import app.api.routes as routes

    monkeypatch.setattr(routes, "cached_ebay_active_ids", lambda *_args: None)
    monkeypatch.setattr(routes, "last_ebay_active_ids", lambda *_args: external_ids)

    # 43 rows at $108.70 and 57 at $108.69 produce the operator's reference
    # total exactly. Thirty-five retain historical Vine ETV provenance, but
    # are verified remote listings and must still contribute their price.
    cents = [10870] * 43 + [10869] * 57
    db = database_module.SessionLocal()
    records = []
    for index, amount in enumerate(cents):
        identity = f"LIVE-{index}"
        price = amount / 100
        pricing = {"pricing_analysis": {"provenance": "vine_etv_fallback"}} if index < 35 else {}
        records.append(_listing(
            user_id,
            status=ListingStatus.PROCESSED,
            title=f"Published verified item {index}",
            listing_price=price,
            suggested_price=price,
            category_id="123",
            category_suggestion="Electronics > Accessories",
            ebay_listing_id=identity,
            ebay_publish_status=EbayPublishStatus.POSTED,
            shipping_profile={"package_weight": 1},
            marketplace_data={**pricing, "ebay_sync": {"ebay_listing_id": identity, "remote_price": f"{price:.2f}"}},
        ))
    db.add_all(records)
    db.commit()
    db.close()

    page_one = (await async_client.get("/listings", params={"queue": "published", "page": 1, "page_size": 25})).json()
    page_four = (await async_client.get("/listings", params={"queue": "published", "page": 4, "page_size": 25})).json()
    for payload in (page_one, page_four):
        assert payload["total"] == 100
        assert payload["aggregate_value"] == 10869.43
        assert payload["average_value"] == 108.69
        assert payload["priced_count"] == 100
        assert payload["missing_price_count"] == 0
        assert payload["price_source_counts"] == {"verified_remote": 100, "local_published_fallback": 0, "missing": 0}
        assert payload["expected_sold_fee_min_total"] > 0
        assert payload["expected_sold_fee_max_total"] == payload["expected_sold_fee_min_total"]
        assert payload["shipping_min_total"] > 0
        assert payload["shipping_max_total"] >= payload["shipping_min_total"]
    assert len(page_one["items"]) == len(page_four["items"]) == 25


@pytest.mark.anyio
async def test_published_summary_uses_remote_price_and_reports_true_missing_price(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={"full_name": "Published Price Source", "email": f"published-price-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    external_ids = {"LIVE-REMOTE", "LIVE-FALLBACK", "LIVE-MISSING", "SOLD-REMOTE"}
    import app.api.routes as routes

    monkeypatch.setattr(routes, "cached_ebay_active_ids", lambda *_args: None)
    monkeypatch.setattr(routes, "last_ebay_active_ids", lambda *_args: external_ids)
    db = database_module.SessionLocal()
    db.add_all([
        _listing(user_id, status=ListingStatus.PROCESSED, title="Remote-price listing", listing_price=None, suggested_price=None, ebay_listing_id="LIVE-REMOTE", marketplace_data={"ebay_sync": {"ebay_listing_id": "LIVE-REMOTE", "remote_price": "25.00"}}),
        _listing(user_id, status=ListingStatus.PROCESSED, title="Local-price fallback", listing_price=10, ebay_listing_id="LIVE-FALLBACK", marketplace_data={"ebay_sync": {"ebay_listing_id": "LIVE-FALLBACK"}}),
        _listing(user_id, status=ListingStatus.PROCESSED, title="True missing price", listing_price=None, suggested_price=None, ebay_listing_id="LIVE-MISSING", marketplace_data={"ebay_sync": {"ebay_listing_id": "LIVE-MISSING"}}),
        _listing(user_id, status=ListingStatus.PUBLISHED, title="Sold listing excluded", listing_price=50, ebay_listing_id="SOLD-REMOTE", sold_at=datetime.utcnow(), quantity=0),
        _listing(user_id, status=ListingStatus.PUBLISHED, title="Archived listing excluded", listing_price=75, ebay_listing_id="ARCHIVED-ONLY", custom_labels=["archived_ebay"]),
    ])
    db.commit()
    db.close()

    payload = (await async_client.get("/listings", params={"queue": "published", "page": 1, "page_size": 1})).json()
    assert payload["total"] == 3
    assert payload["aggregate_value"] == 35
    assert payload["priced_count"] == 2
    assert payload["average_value"] == 17.5
    assert payload["missing_price_count"] == 1
    assert payload["price_source_counts"] == {"verified_remote": 1, "local_published_fallback": 1, "missing": 1}
    assert payload["expected_sold_fee_min_total"] is None
    assert payload["expected_sold_fee_max_total"] is None
    assert payload["shipping_min_total"] is None
    assert payload["shipping_max_total"] is None


def test_listings_return_context_contract_is_present():
    root = Path(__file__).resolve().parents[2]
    listings_page = (root / "frontend/pages/listings.js").read_text()
    detail_page = (root / "frontend/pages/listings/[listingId].js").read_text()
    assert "return_to=${encodeURIComponent(listingsReturnUrl)}" in listings_page
    assert "returnToListings" in detail_page
    assert "router.push(returnToListings)" in detail_page


@pytest.mark.parametrize("invalid_price", [None, 0, -1, float("nan")])
def test_canonical_readiness_never_allows_review_without_a_valid_price(invalid_price):
    listing = _listing(
        2,
        status=ListingStatus.PROCESSED,
        source_type="manual",
        title="Makita Cordless Drill Kit 18V",
        description="Makita 18V cordless drill kit with charger and case.",
        category_suggestion="Tools > Power Tools > Drills",
        image_urls=["/media/drill.jpg"],
        listing_price=invalid_price,
        suggested_price=None,
        buy_it_now_price=None,
        needs_review=True,
        processing_state="complete",
    )
    readiness = canonical_listing_readiness(listing)
    assert readiness["queue"] == "NEEDS_ATTENTION"
    assert any("valid positive listing price" in str(reason).lower() for reason in readiness["blocking_reasons"])


def test_authoritative_price_resolver_uses_sell_price_fields_not_acquisition_value():
    assert resolve_authoritative_listing_price(_listing(2, listing_price=None, suggested_price=None, buy_it_now_price=19.99)) == 19.99
    assert resolve_authoritative_listing_price(_listing(2, listing_price=None, suggested_price=None, marketplace_data={
        "pricing_analysis": {"provenance": "sold_comps", "current_price": 24.5}
    })) == 24.5
    assert resolve_authoritative_listing_price(_listing(2, listing_price=None, suggested_price=None, estimated_value=500)) is None
    assert resolve_authoritative_listing_price(_listing(2, listing_price=None, suggested_price=None, marketplace_data={
        "pricing_analysis": {"provenance": "placeholder", "current_price": 24.5}
    })) is None


def test_price_correction_can_restore_review_when_other_gates_pass():
    listing = _listing(
        2,
        status=ListingStatus.PROCESSED,
        source_type="amazon_vine",
        title="Garden Shade Cloth Kit 78 Inch",
        description="Garden shade cloth kit with steel hoops. Shade for backyard garden.",
        canonical_description="Garden shade cloth kit with steel hoops. Shade for backyard garden.",
        category_id="123",
        category_suggestion="Garden > Shade Cloths",
        listing_price=None,
        suggested_price=None,
        condition="New",
        condition_data={"condition": "New"},
        shipping_profile={"package_weight": 1, "manual_measurement_needed": False, "package_dimensions": {"length": 12, "width": 10, "height": 6}},
        image_urls=["/media/shade.jpg"],
        needs_review=True,
        processing_state="complete",
        source_metadata={"category_validation": {"status": "valid_local", "category_id": "123", "full_path": "Garden > Shade Cloths"}},
        marketplace_data={
            "targets": ["ebay"],
            "pricing_analysis": {"provenance": "vine_etv_fallback"},
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "ready", "stale": False, "blockers": []}}},
        },
    )
    assert canonical_listing_readiness(listing)["queue"] == "NEEDS_ATTENTION"
    listing.listing_price = 33.98
    listing.suggested_price = 33.98
    listing.source_metadata = {"recovery": {"operator_locked_fields": ["listing_price"]}}
    assert canonical_listing_readiness(listing)["queue"] == "NEEDS_REVIEW"


@pytest.mark.anyio
async def test_needs_review_aggregate_excludes_vine_etv_fallback_prices(async_client):
    register = await async_client.post(
        "/auth/register",
        json={"full_name": "Review Price Owner", "email": f"review-price-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    preflight = {"marketplace_preflight": {"by_marketplace": {"ebay": {"status": "ready", "blockers": [], "stale": False}}}}
    vine_source = {
        "category_validation": {"status": "valid_local", "category_id": "123", "full_path": "Garden > Shade Cloths"},
    }
    db = database_module.SessionLocal()
    db.add_all([
        _listing(user_id, status=ListingStatus.PROCESSED, source_type="amazon_vine", title="Garden Shade Cloth Kit 78 Inch", description="Garden shade cloth kit with steel hoops. Shade for backyard garden.", canonical_description="Garden shade cloth kit with steel hoops. Shade for backyard garden.", category_id="123", category_suggestion="Garden > Shade Cloths", listing_price=33.98, suggested_price=33.98, condition="New", condition_data={"condition": "New"}, shipping_profile={"package_weight": 1, "manual_measurement_needed": False, "package_dimensions": {"length": 12, "width": 10, "height": 6}}, image_urls=["/media/shade.jpg"], needs_review=True, processing_state="complete", source_metadata=vine_source, marketplace_data={"targets": ["ebay"], "pricing_analysis": {"provenance": "vine_etv_fallback"}, **preflight}),
        _listing(user_id, status=ListingStatus.PROCESSED, source_type="amazon_vine", title="Makita Cordless Drill Kit 18V", description="Makita cordless drill kit with charger.", category_id="123", category_suggestion="Tools > Drills", listing_price=None, suggested_price=None, condition="New", image_urls=["/media/drill.jpg"], needs_review=True, processing_state="complete", source_metadata=vine_source, marketplace_data={"targets": ["ebay"], **preflight}),
    ])
    db.commit()
    db.close()

    first = (await async_client.get("/listings", params={"queue": "review", "page": 1, "page_size": 1})).json()
    second = (await async_client.get("/listings", params={"queue": "review", "page": 2, "page_size": 25})).json()
    assert first["total"] == second["total"] == 0
    assert first["aggregate_value"] == second["aggregate_value"] == 0
    assert first["average_value"] is second["average_value"] is None
    assert first["priced_count"] == second["priced_count"] == 0
    assert first["missing_price_count"] == second["missing_price_count"] == 0
    assert first["expected_sold_fee_min_total"] is None
    assert first["expected_sold_fee_max_total"] is None
    assert first["shipping_min_total"] is None
    assert first["net_before_shipping_total"] is None
    attention = (await async_client.get("/listings", params={"queue": "needs_attention", "page": 1, "page_size": 25})).json()
    assert attention["total"] == 2
    assert {row["title"] for row in attention["items"]} == {
        "Garden Shade Cloth Kit 78 Inch", "Makita Cordless Drill Kit 18V",
    }
