import pytest
from fastapi import HTTPException

from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, User
from app.services.pricing_research_service import PricingResearchService, validate_marketplace_readiness


def test_timeline_research_worker_is_bounded_and_never_changes_price_or_readiness(monkeypatch, db_session):
    from app.workers import tasks

    user = User(email="timeline-worker-evidence@example.com")
    db_session.add(user)
    db_session.flush()
    listings = [Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title=f"Timeline Product Model {index}", listing_price=18.75 + index,
        needs_review=False, quantity=1,
    ) for index in range(4)]
    db_session.add_all(listings)
    db_session.commit()
    before = {row.id: (row.listing_price, row.status, row.needs_review) for row in listings}
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    researched_ids = []

    async def fake_refresh(_db, listing):
        researched_ids.append(listing.id)
        return {"external_research_status": "results", "external_active_asking_count": 2,
                "external_sold_comparable_count": 0, "recommendation_eligible": True}

    monkeypatch.setattr(tasks, "refresh_listing_external_pricing", fake_refresh)
    result = tasks.research_google_intake_pricing_task.run(limit=2)
    assert result["selected"] == 2
    assert result["researched"] == 2
    assert researched_ids == [row.id for row in listings[:2]]
    assert {row.id: (row.listing_price, row.status, row.needs_review) for row in listings} == before


def test_timeline_research_worker_respects_identity_and_provider_retry_cadence(monkeypatch, db_session):
    from datetime import UTC, datetime, timedelta
    from app.workers import tasks

    user = User(email="timeline-worker-backoff@example.com")
    db_session.add(user)
    db_session.flush()
    now = datetime.now(UTC)
    recent_no_identity = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Unknown item", needs_review=False, quantity=1,
        marketplace_data={"pricing_analysis": {"external_research_status": "insufficient_identity",
            "external_research_date": (now - timedelta(hours=2)).isoformat()}},
    )
    old_no_identity = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Newly clarified item model 42", needs_review=False, quantity=1,
        marketplace_data={"pricing_analysis": {"external_research_status": "insufficient_identity",
            "external_research_date": (now - timedelta(days=2)).isoformat()}},
    )
    recent_provider_failure = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Product model 77", needs_review=False, quantity=1,
        marketplace_data={"pricing_analysis": {"external_research_status": "unavailable",
            "external_research_date": (now - timedelta(hours=2)).isoformat()}},
    )
    db_session.add_all([recent_no_identity, old_no_identity, recent_provider_failure])
    db_session.commit()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    selected = []

    async def fake_refresh(_db, listing):
        selected.append(listing.id)
        return {"external_research_status": "no_results", "external_active_asking_count": 0,
                "external_sold_comparable_count": 0, "recommendation_eligible": False}

    monkeypatch.setattr(tasks, "refresh_listing_external_pricing", fake_refresh)
    result = tasks.research_google_intake_pricing_task.run(limit=5)
    assert result["selected"] == 1
    assert selected == [old_no_identity.id]


def test_no_comparable_evidence_is_not_publication_price_evidence(db_session):
    user = User(email="pricing-evidence@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, status=ListingStatus.draft, title="Identified Test Product", description="A sufficiently detailed product description for the test item.",
        category_suggestion="Test category", condition="Used", quantity=1,
        item_specifics={"Brand": "Test", "Model": "Evidence"}, image_urls=["/media/test.jpg"],
        listing_images=[{"storage_path": "/media/test.jpg", "operator_state": "approved", "is_reference": False, "role": "primary"}],
        shipping_profile={"package_weight": 2, "package_dimensions": {"length": 12, "width": 8, "height": 4}, "manual_measurement_needed": False},
    )
    db_session.add(listing)
    db_session.commit()

    research = PricingResearchService().build_research(db_session, listing, external_comparables=[])
    assert research["provenance"] == "fallback_estimate"
    assert research["recommendation_eligible"] is False
    assert "not a publication-ready recommendation" in " ".join(validate_marketplace_readiness(listing=listing, marketplace="ebay", pricing_analysis=research))


def test_category_model_estimate_is_not_a_publication_ready_recommendation(db_session):
    user = User(email="category-estimate-evidence@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, status=ListingStatus.draft,
        title="Identified Test Product", description="A product with a model estimate but no market evidence.",
        category_suggestion="Test category", condition="Used", quantity=1,
        estimated_value=21.99, listing_price=21.99,
        item_specifics={"Brand": "Test", "Model": "Estimate"}, image_urls=["/media/test.jpg"],
        listing_images=[{"storage_path": "/media/test.jpg", "operator_state": "approved", "is_reference": False, "role": "primary"}],
    )
    db_session.add(listing)
    db_session.commit()

    research = PricingResearchService().build_research(db_session, listing, external_comparables=[])

    assert research["provenance"] == "category_model_estimate"
    assert research["recommended_price"] is None
    assert research["quick_sale_price"] is None
    assert research["unverified_estimate"]["amount"] > 0
    assert research["unverified_estimate"]["not_a_recommendation"] is True
    assert research["recommendation_eligible"] is False
    assert "not a publication-ready recommendation" in research["publication_price_blocker"]
    assert "not a publication-ready recommendation" in " ".join(
        validate_marketplace_readiness(listing=listing, marketplace="ebay", pricing_analysis=research)
    )


def test_apply_requires_research_evidence_or_documented_operator_price_and_blocks_live_items(monkeypatch, db_session):
    from app.api import intelligence
    from app.api.schemas import PricingApplyRequest

    user = User(email="pricing-apply-gate@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(user_id=user.id, title="Test item", listing_price=19.99, suggested_price=19.99)
    db_session.add(listing)
    db_session.commit()
    monkeypatch.setattr(
        intelligence.PricingIntelligenceService, "recommend_price",
        lambda *_args, **_kwargs: {"recommendation_eligible": False, "publication_price_blocker": "Research evidence is missing."},
    )

    with pytest.raises(HTTPException) as blocked:
        intelligence.apply_pricing_recommendation(listing.id, PricingApplyRequest(), db_session, user)
    assert blocked.value.status_code == 409
    assert db_session.get(Listing, listing.id).listing_price == 19.99

    with pytest.raises(HTTPException) as undocumented:
        intelligence.apply_pricing_recommendation(
            listing.id, PricingApplyRequest(operator_entered_price=23.5), db_session, user,
        )
    assert undocumented.value.status_code == 422

    applied = intelligence.apply_pricing_recommendation(
        listing.id,
        PricingApplyRequest(operator_entered_price=23.5, override_reason="Operator checked local demand"),
        db_session,
        user,
    )
    saved = db_session.get(Listing, listing.id)
    assert applied["applied_price"] == 23.5
    assert saved.listing_price == 23.5
    assert saved.marketplace_data["manual_price_override"] == 23.5
    assert saved.marketplace_data["manual_price_override_reason"] == "Operator checked local demand"

    db_session.add(MarketplaceListing(
        listing_id=listing.id, marketplace=MarketplaceName.ebay,
        status=MarketplaceListingStatus.PUBLISHED, marketplace_listing_id="remote-123",
    ))
    db_session.commit()
    with pytest.raises(HTTPException) as live_blocked:
        intelligence.apply_pricing_recommendation(
            listing.id, PricingApplyRequest(operator_entered_price=30, override_reason="Reviewed"), db_session, user,
        )
    assert live_blocked.value.status_code == 409


@pytest.mark.anyio
async def test_refresh_pricing_persists_real_active_asking_evidence_and_sold_comps_stay_zero(monkeypatch, db_session):
    from app.api import intelligence

    user = User(email="active-asking-evidence@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, status=ListingStatus.draft,
        title="Acme Model 123 Widget", description="Used Acme Model 123 Widget with visible model label.",
        category_suggestion="Widgets", condition="Used", quantity=1, listing_price=30,
        item_specifics={"Brand": "Acme", "Model": "123"}, image_urls=["/media/widget.jpg"],
    )
    db_session.add(listing)
    db_session.commit()

    async def fake_search(_listing):
        return {"status": "results", "source": "ebay_browse_active", "query": "Acme 123",
            "researched_at": "2026-10-18T00:00:00+00:00", "active_asking_count": 1,
            "sold_comparable_count": 0,
            "message": "Browse provides current asking prices only; no sold comparables were returned by this source.",
            "comparables": [{"source_marketplace": "ebay", "comp_type": "active",
                "source_label": "eBay active asking listing (not sold evidence)", "source_record_id": "v1|test|0",
                "title": "Acme Model 123 Widget", "price": 34.0, "shipping_price": 5.0,
                "currency": "USD", "condition": "Used", "sold_status": "asking_active",
                "research_date": "2026-10-18T00:00:00+00:00", "match_rationale": "Matched by query: Acme 123",
                "source_url": "https://www.ebay.com/itm/123", "shipping_inclusive": True,
                "matched_identifiers": {"model": "123"}}]}

    from app.services import external_pricing_research
    monkeypatch.setattr(external_pricing_research, "search_ebay_active_comparables", fake_search)
    result = await intelligence.refresh_pricing_recommendation(listing.id, db_session, user)

    assert result["provenance"] == "active_comps"
    assert result["recommendation_eligible"] is True
    assert result["sold_comp_count_used"] == 0
    assert result["external_sold_comparable_count"] == 0
    assert result["external_active_asking_count"] == 1
    assert "asking prices only" in result["external_research_warning"]
    stored = db_session.get(Listing, listing.id).marketplace_data["pricing_analysis"]
    assert stored["external_research_source"] == "ebay_browse_active"
    assert stored["included_comps"][0]["source_record_id"] == "v1|test|0"
    assert stored["included_comps"][0]["shipping_inclusive"] is True
