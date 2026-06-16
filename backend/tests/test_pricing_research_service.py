from datetime import datetime

from app.models.enums import ListingStatus
from app.models.models import Listing, User
from app.services.listing_ai import ListingAIService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.pricing_research_service import PricingResearchService, compute_listing_quality_summary, validate_marketplace_readiness


def _seed_listing(db_session, *, title="Nike Air Max 270", category="Shoes", condition="Used", listing_price=None):
    user = User(email=f"{title.replace(' ', '-').lower()}@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title=title,
        description="Used item with visible wear and included accessories.",
        category_suggestion=category,
        condition=condition,
        item_specifics={"Brand": "Nike", "Model": "Air Max 270"},
        image_urls=["/media/uploads/item.jpg"],
        listing_images=[{"storage_path": "/media/uploads/item.jpg", "source_platform": "upload", "role": "primary", "operator_state": "approved", "display_order": 0, "is_reference": False, "confidence": 1.0}],
        condition_data={"condition_bucket": "used", "condition_source": "operator", "condition_confidence": 0.95, "operator_review_required": False},
        shipping_profile={"package_weight": 2.0, "package_dimensions": {"length": 14, "width": 10, "height": 6}, "shipping_class_suggestion": "ups_ground", "manual_measurement_needed": False},
        listing_price=listing_price,
    )
    db_session.add(listing)
    db_session.flush()
    return user, listing


def test_pricing_research_normalizes_and_scores_comps(db_session):
    _, listing = _seed_listing(db_session)
    sold = Listing(
        user_id=listing.user_id,
        status=ListingStatus.ready,
        title="Nike Air Max 270 Running Shoes",
        category_suggestion="Shoes",
        condition="Used",
        sale_price=64.99,
        shipping_cost=8.0,
        item_specifics={"Brand": "Nike", "Model": "Air Max 270"},
    )
    sold.sold_at = datetime.utcnow()
    db_session.add(sold)
    db_session.commit()

    result = PricingResearchService().build_research(db_session, listing, external_comparables=[
        {"title": "Nike Air Max 270 Mens", "price": 72.0, "shipping_price": 10, "comp_type": "active", "condition": "Used", "source_marketplace": "ebay"},
        {"title": "Parts only broken sneaker lot", "price": 12.0, "comp_type": "sold", "condition": "For parts", "source_marketplace": "ebay"},
    ])

    assert result["comp_count_used"] >= 1
    assert any(comp["include"] for comp in result["included_comps"])
    assert any(comp["reason_excluded"] for comp in result["excluded_comps"])
    assert result["recommended_price"] > 0


def test_weak_no_comp_fallback_and_manual_override_preserved(db_session):
    _, listing = _seed_listing(db_session, title="Unknown Decor Item", category="Home")
    listing.marketplace_data = {"manual_price_override": 37.5, "manual_price_override_reason": "operator knows local demand"}
    db_session.add(listing)
    db_session.commit()

    result = PricingIntelligenceService().recommend_price(db_session, listing.id)
    assert result["warning"]
    assert result["current_price"] == 37.5
    assert result["manual_override_reason"] == "operator knows local demand"


def test_manual_comp_entry_influences_pricing(db_session):
    _, listing = _seed_listing(db_session, title="Keurig K-Classic", category="Small Appliances")
    listing.marketplace_data = {
        "pricing_manual_comps": [
            {"title": "Keurig K-Classic Brewer", "price": 55.0, "source_marketplace": "manual", "condition": "Used"},
            {"title": "Keurig K-Classic with accessories", "price": 62.0, "source_marketplace": "manual", "condition": "Used"},
        ]
    }
    db_session.add(listing)
    db_session.commit()

    result = PricingIntelligenceService().recommend_price(db_session, listing.id)
    assert result["comp_count_used"] >= 2
    assert result["recommended_price"] >= 40


def test_listing_quality_and_marketplace_blockers(db_session):
    _, listing = _seed_listing(db_session, listing_price=59.99)
    listing.status = ListingStatus.ready
    db_session.add(listing)
    db_session.commit()
    pricing = PricingIntelligenceService().recommend_price(db_session, listing.id)
    quality = compute_listing_quality_summary(listing, pricing_analysis=pricing)

    assert quality["score"] > 0
    assert quality["ready_for_ebay"] is True

    listing.shipping_profile = {"manual_measurement_needed": True}
    ebay_blockers = validate_marketplace_readiness(listing=listing, marketplace="ebay", pricing_analysis=pricing)
    facebook_blockers = validate_marketplace_readiness(listing=listing, marketplace="facebook", pricing_analysis=pricing)
    assert any("Shipping" in blocker for blocker in ebay_blockers)
    assert all("Shipping" not in blocker for blocker in facebook_blockers)


def test_listing_ai_avoids_unsupported_claims():
    service = ListingAIService()
    generated = service._sanitize_claims(
        "Authentic OEM replacement compatible with every model and warranty included",
        {"title_hint": "Generic accessory", "source_type": "upload"},
    )
    lowered = generated.lower()
    assert "authentic" not in lowered
    assert "oem" not in lowered
    assert "warranty" not in lowered
