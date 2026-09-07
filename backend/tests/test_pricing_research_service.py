from datetime import UTC, datetime, timedelta

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


def test_generic_placeholder_titles_block_review_queue(db_session):
    user = User(email="generic-placeholder@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.ready,
        title="Automotive Parts",
        description="Recovered from preserved inventory photos.",
        category_suggestion="Automotive Parts",
        condition="Needs review",
        item_specifics={},
        image_urls=["/media/uploads/part.jpg"],
        listing_images=[{"storage_path":"/media/uploads/part.jpg","source_platform":"upload","role":"primary","operator_state":"approved","display_order":0,"is_reference":False,"confidence":1.0}],
        condition_data={"condition_bucket": "needs_review", "operator_review_required": True},
        shipping_profile={"package_weight": 3.0, "package_dimensions": {"length": 12, "width": 10, "height": 8}, "manual_measurement_needed": False},
    )
    db_session.add(listing)
    db_session.commit()

    pricing = PricingIntelligenceService().recommend_price(db_session, listing.id)
    quality = compute_listing_quality_summary(listing, pricing_analysis=pricing)
    blockers = validate_marketplace_readiness(listing=listing, marketplace="ebay", pricing_analysis=pricing)

    assert quality["specificity_status"] == "blocked_placeholder_data"
    assert quality["ready_for_publish_queue"] is False
    assert any("generic" in blocker.lower() or "specific" in blocker.lower() for blocker in quality["blockers"])
    assert any("generic" in blocker.lower() or "specific" in blocker.lower() for blocker in blockers)


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


def test_listing_ai_fallback_category_uses_product_keywords():
    service = ListingAIService()
    generated = service._fallback_generation(
        {
            "title_hint": "Pool Booster Pump Replacement",
            "source_type": "amazon_vine",
            "photo_keywords": ["pool", "pump"],
        }
    )
    assert "Collectibles" not in generated["category_suggestion"]
    assert "Pool Pumps" in generated["category_suggestion"]


def test_listing_ai_generate_exposes_structured_contract(monkeypatch):
    service = ListingAIService()

    def fake_llm(_signals):
        return {
            "result": {
                "schema_version": "posterpro_listing_intelligence_v1",
                "title": "Whirlpool Refrigerator Control Board",
                "description": "Structured draft description.",
                "category_suggestion": "Appliances > Parts & Accessories",
                "condition": "New - Open Box",
                "item_specifics": {"Brand": "Whirlpool", "Model": "W11478526"},
                "tags": ["whirlpool", "control board"],
                "missing_information": ["Verify exact part number from photos."],
                "photo_notes": ["Package label visible."],
                "research_queries": ["Whirlpool W11478526"],
                "estimated_value": 74.5,
                "draft_quality": "strong",
                "marketplace_targets": ["ebay", "facebook", "mercari"],
                "marketplace_drafts": {
                    "ebay": {"title": "Whirlpool Refrigerator Control Board"},
                    "mercari": {"description": "Mercari copy"},
                },
                "identity": {"product_name": "Whirlpool Refrigerator Control Board", "brand": "Whirlpool", "model": "W11478526", "confidence": 0.94},
                "inventory": {"quantity_on_hand": 1, "pack_size": 1, "units_per_sale": 1, "listing_quantity": 1, "bundle_strategy": "single"},
                "condition_details": {"canonical_condition": "New - Open Box", "defects": [], "included_items": ["Board"], "missing_items": []},
                "research": {"photo_research_required": True, "identifiers_to_verify": ["W11478526"], "research_instructions": ["Verify part number"], "pricing_instructions": ["Research sold comps"]},
                "canonical_listing": {"human_readable_name": "Whirlpool Refrigerator Control Board", "master_title": "Whirlpool Refrigerator Control Board", "master_description": "Structured draft description.", "keywords": ["whirlpool"], "features": ["OEM replacement"], "specifications": {"Brand": "Whirlpool"}, "category_candidates": ["Appliances > Parts & Accessories"]},
                "pricing": {"strategy": "compare_sold_comps", "price_hint": 74.5, "minimum_price": 49.99, "comparison_instruction": "Research sold comps"},
                "evidence": {"facts_from_user": ["Whirlpool"], "facts_inferred": ["Control board"], "facts_needing_verification": ["Exact part number"], "contradictions": []},
                "quality": {"overall_confidence": 0.94, "ready_for_photo_enrichment": True, "ready_for_draft": True, "blocking_questions": []},
            },
            "metadata": {
                "request_id": "req-123",
                "request_timestamp": "2026-09-02T12:00:00+00:00",
                "latency_ms": 42,
                "validation_status": "validated",
                "validation_errors": [],
                "response_provider": "openai",
                "raw_request_preview": "Whirlpool refrigerator control board",
            },
        }

    monkeypatch.setattr(service, "_llm_generation", fake_llm)

    generated = service.generate(
        {
            "title_hint": "Whirlpool refrigerator control board",
            "source_type": "intake_voice",
            "voice_transcript": "Whirlpool refrigerator control board, new open box.",
            "voice_notes": "Sell individually.",
            "photo_keywords": ["whirlpool", "control", "board"],
            "existing_specifics": {"Brand": "", "Model": ""},
            "source_metadata": {"session": {"default_location": "Storage"}},
        }
    )

    assert generated["schema_version"] == "posterpro_listing_intelligence_v1"
    assert generated["structured_listing_json"]["identity"]["product_name"] == "Whirlpool Refrigerator Control Board"
    assert generated["marketplace_drafts"]["mercari"]["description"] == "Mercari copy"
    assert generated["ai_metadata"]["request_id"] == "req-123"
    assert generated["intelligence_state"]["fields_populated"] is True
    assert generated["marketplace_targets"] == ["ebay", "facebook", "mercari"]


def test_pricing_research_handles_timezone_aware_staleness(db_session):
    _, listing = _seed_listing(db_session, title="Keurig Coffee Maker", category="Kitchen")
    listing.marketplace_data = {
        "pricing_analysis": {
            "generated_at": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
            "current_price": 49.99,
            "price_confidence": 0.8,
        }
    }
    db_session.add(listing)
    db_session.commit()

    result = PricingResearchService().build_research(db_session, listing)

    assert result["stale"] is True
    assert result["current_price"] > 0
