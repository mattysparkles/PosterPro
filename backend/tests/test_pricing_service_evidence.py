from app.models.enums import ListingStatus
from app.models.models import Listing, User
from app.services.pricing_service import PricingService


def test_auto_pricing_does_not_turn_image_estimate_into_seller_price(db_session):
    user = User(email="pricing-service-unresearched@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Identified but unresearched part",
        estimated_value=24.99,
        listing_price=None,
        suggested_price=None,
        buy_it_now_price=None,
        marketplace_data={},
    )
    db_session.add(listing)
    db_session.commit()

    result = PricingService().generate_pricing(db_session, listing.id)

    db_session.refresh(listing)
    assert result["status"] == "research_required"
    assert result["recommendation_eligible"] is False
    assert result["buy_it_now_price"] is None
    assert listing.listing_price is None
    assert listing.suggested_price is None
    assert listing.buy_it_now_price is None
    assert PricingService().get_pricing(db_session, listing.id)["buy_it_now_price"] is None


def test_pricing_service_returns_evidence_recommendation_without_applying_it(monkeypatch, db_session):
    user = User(email="pricing-service-review@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Research supported product",
        listing_price=29.0,
        suggested_price=29.0,
        buy_it_now_price=29.0,
    )
    db_session.add(listing)
    db_session.commit()
    research = {
        "recommendation_eligible": True,
        "recommended_price": 34.95,
        "quick_sale_price": 30.75,
        "floor_price": 25.86,
        "provenance": "sold_comps",
        "included_comps": [{"title": "Same model sold", "price": 35.0, "source_url": "https://example.invalid/comp"}],
    }
    monkeypatch.setattr(
        "app.services.pricing_research_service.PricingResearchService.build_research",
        lambda *_args, **_kwargs: research,
    )

    result = PricingService().generate_pricing(db_session, listing.id)

    db_session.refresh(listing)
    assert result["recommendation_eligible"] is True
    assert result["buy_it_now_price"] == 34.95
    assert result["applied"] is False
    assert listing.listing_price == 29.0
    assert listing.suggested_price == 29.0
    assert listing.buy_it_now_price == 29.0
    assert listing.marketplace_data["pricing_analysis"]["provenance"] == "sold_comps"
