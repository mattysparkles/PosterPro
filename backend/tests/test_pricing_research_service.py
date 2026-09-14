from datetime import UTC, datetime, timedelta

from app.models.enums import ListingStatus
from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceListing, ListingRevision, User
from app.services.listing_ai import ListingAIService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.marketplace_preflight import MarketplacePreflightService
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


def test_high_confidence_sold_evidence_flags_severe_underpricing_but_weak_evidence_does_not(db_session):
    _, listing = _seed_listing(db_session, title="Pokemon Base Set Ninetales Holo Card 1999", category="Trading Cards", listing_price=20)
    listing.item_specifics = {"Brand": "Pokemon", "Model": "Ninetales", "UPC": "012345678901"}
    db_session.add(listing)
    db_session.commit()
    comps = [
        {"title": "Pokemon Base Set Ninetales Holo Card 1999", "price": amount, "comp_type": "sold", "condition": "Used", "source_marketplace": "ebay", "matched_identifiers": {"upc": "012345678901"}}
        for amount in (98, 100, 110)
    ]
    result = PricingResearchService().build_research(db_session, listing, external_comparables=comps)
    assert result["underpricing_risk"]["level"] == "SEVERE", (result["underpricing_risk"], [(row["relevance_score"], row["include"], row["mismatch_flags"]) for row in result["included_comps"] + result["excluded_comps"]])
    assert result["underpricing_risk"]["sold_comparable_count"] == 3
    assert result["underpricing_risk"]["sold_median"] == 100

    weak = PricingResearchService().build_research(db_session, listing, external_comparables=[
        {"title": "Possibly related card", "price": 100, "comp_type": "sold", "condition": "Used", "source_marketplace": "ebay"},
    ])
    assert weak["underpricing_risk"]["level"] == "NONE"


def test_underpricing_leave_acknowledges_current_evidence_without_price_change(db_session, monkeypatch):
    from types import SimpleNamespace
    from app.api.intelligence import PricingDecisionRequest, decide_underpricing
    from app.services.pricing_intelligence_service import PricingIntelligenceService

    user, listing = _seed_listing(db_session, listing_price=20)
    risk = {"level": "SEVERE", "evidence_signature": "evidence-1", "current_price": 20, "sold_median": 100}
    monkeypatch.setattr(PricingIntelligenceService, "recommend_price", lambda self, _db, _id: {"underpricing_risk": risk, "recommended_price": 96})
    result = decide_underpricing(listing.id, PricingDecisionRequest(action="leave_price_as_is"), db_session, user)
    db_session.refresh(listing)
    assert result["status"] == "ACKNOWLEDGED"
    assert listing.listing_price == 20
    assert listing.marketplace_data["pricing_underpricing_acknowledgement"]["evidence_signature"] == "evidence-1"


def test_underpricing_auto_fix_queues_only_update_for_confirmed_marketplace_identity(db_session, monkeypatch):
    from types import SimpleNamespace
    from app.api.intelligence import PricingDecisionRequest, decide_underpricing
    from app.services.pricing_intelligence_service import PricingIntelligenceService
    import app.api.marketplace_jobs as marketplace_jobs

    user, listing = _seed_listing(db_session, listing_price=20)
    db_session.add(MarketplaceListing(listing_id=listing.id, marketplace=MarketplaceName.facebook, marketplace_listing_id="fb-exact-123", status=MarketplaceListingStatus.PUBLISHED))
    db_session.commit()
    risk = {"level": "SEVERE", "evidence_signature": "evidence-2", "current_price": 20, "sold_median": 100}
    monkeypatch.setattr(PricingIntelligenceService, "recommend_price", lambda self, _db, _id: {"underpricing_risk": risk, "recommended_price": 96})
    monkeypatch.setattr(marketplace_jobs, "_enqueue_priority", lambda *_args, **_kwargs: SimpleNamespace(id="celery-test-task"))

    result = decide_underpricing(listing.id, PricingDecisionRequest(action="auto_fix_price"), db_session, user)
    db_session.refresh(listing)
    jobs = db_session.query(MarketplaceCrosspostJob).filter_by(listing_id=listing.id).all()
    revisions = db_session.query(ListingRevision).filter_by(listing_id=listing.id).all()
    assert result["status"] == "PRICE_UPDATED"
    assert listing.listing_price == 96
    assert len(jobs) == 1
    assert jobs[0].execution_plan["operation"] == "update"
    assert jobs[0].execution_plan["external_listing_id"] == "fb-exact-123"
    assert jobs[0].target_marketplaces == ["facebook"]
    assert len(revisions) == 1


def test_underpricing_pause_blocks_new_publish_and_queues_exact_ebay_end(db_session, monkeypatch):
    from types import SimpleNamespace
    from app.api.intelligence import PricingDecisionRequest, decide_underpricing
    import app.api.marketplace_jobs as marketplace_jobs
    from app.services.pricing_intelligence_service import PricingIntelligenceService
    from app.services.marketplace_preflight import MarketplacePreflightService

    user, listing = _seed_listing(db_session, listing_price=20)
    listing.ebay_listing_id = "ebay-exact-456"
    db_session.add(MarketplaceListing(listing_id=listing.id, marketplace=MarketplaceName.ebay, marketplace_listing_id="ebay-exact-456", status=MarketplaceListingStatus.PUBLISHED))
    db_session.commit()
    risk = {"level": "POTENTIAL", "evidence_signature": "evidence-3", "current_price": 20, "sold_median": 50}
    monkeypatch.setattr(PricingIntelligenceService, "recommend_price", lambda self, _db, _id: {"underpricing_risk": risk, "recommended_price": 48})
    monkeypatch.setattr(marketplace_jobs, "_enqueue_priority", lambda *_args, **_kwargs: SimpleNamespace(id="end-task"))

    result = decide_underpricing(listing.id, PricingDecisionRequest(action="pause_listing"), db_session, user)
    db_session.refresh(listing)
    assert result["status"] == "PAUSED_FOR_REVIEW"
    assert result["manual_end_required"] == []
    end_job = db_session.query(MarketplaceCrosspostJob).filter_by(listing_id=listing.id).one()
    assert end_job.execution_plan["operation"] == "end"
    assert end_job.execution_plan["external_listing_id"] == "ebay-exact-456"
    assert end_job.target_marketplaces == ["ebay"]
    assert listing.marketplace_data["pricing_review_pause"]["active"] is True
    blockers = MarketplacePreflightService().preflight_listing(db_session, listing, "ebay")["blockers"]
    assert any(blocker.get("code") == "PRICING_REVIEW_PAUSED" for blocker in blockers)


def test_severe_underpricing_notification_is_idempotent_for_same_evidence(db_session, monkeypatch):
    from app.models.models import IntakeNotification
    from app.services.pricing_intelligence_service import PricingIntelligenceService

    user, listing = _seed_listing(db_session, listing_price=20)
    service = PricingIntelligenceService()
    risk = {"level": "SEVERE", "evidence_signature": "same-evidence", "current_price": 20, "sold_median": 100, "sold_comparable_count": 4}
    monkeypatch.setattr(service.research, "build_research", lambda *_args, **_kwargs: {"underpricing_risk": risk, "recommended_price": 99})
    service.recommend_price(db_session, listing.id)
    service.recommend_price(db_session, listing.id)
    notices = db_session.query(IntakeNotification).filter_by(user_id=user.id, notification_type="pricing_underpricing_severe").all()
    assert len(notices) == 1
    assert notices[0].metadata_json["listing_id"] == listing.id
    assert notices[0].metadata_json["evidence_signature"] == "same-evidence"


def test_severe_underpricing_requires_acknowledgement_before_publish(db_session):
    _, listing = _seed_listing(db_session, listing_price=20)
    listing.marketplace_data = {"pricing_analysis": {"underpricing_risk": {"level": "SEVERE", "evidence_signature": "risk-v1"}}}
    blockers = MarketplacePreflightService()._base_blockers(listing, "mercari", listing.marketplace_data["pricing_analysis"], {})
    assert any(row["code"] == "UNDERPRICING_REVIEW_REQUIRED" for row in blockers)
    listing.marketplace_data["pricing_underpricing_acknowledgement"] = {"evidence_signature": "risk-v1"}
    blockers = MarketplacePreflightService()._base_blockers(listing, "mercari", listing.marketplace_data["pricing_analysis"], {})
    assert all(row["code"] != "UNDERPRICING_REVIEW_REQUIRED" for row in blockers)


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

    def fake_llm(_signals, **_kwargs):
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
