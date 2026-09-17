from app.services.listing_ai import ListingAIService


def test_mercari_fallback_preserves_structured_facts_and_limit():
    text = ListingAIService._mercari_description(
        "TrailCo Portable Camping Toilet",
        {"Brand": "TrailCo", "Type": "Portable Toilet"},
        "New",
        "Sporting Goods > Camping > Portable Toilets",
        source_metadata={"source_facts": {
            "feature_bullets": ["Foldable seat with splash-resistant design", "Removable waste tank for easier cleaning"],
            "specifications": {"Capacity": "5.3 gallons", "Material": "HDPE plastic"},
        }},
    )
    assert len(text) <= 1000
    assert "Foldable seat" in text
    assert "5.3 gallons" in text
    assert "refund" not in text.lower()


def test_draft_quality_requires_source_fact_coverage():
    service = ListingAIService()
    generated = {"title": "Portable toilet", "description": "A useful item for everyday use.", "item_specifics": {"Type": "Portable Toilet"}, "missing_information": []}
    facts = {"source_facts": {"feature_bullets": ["Foldable seat", "Removable waste tank", "Splash-resistant design"], "specifications": {"Capacity": "5.3 gallons", "Material": "HDPE plastic"}}}
    assert service._draft_quality(generated, image_signals=facts) == "partial"


def test_draft_quality_penalizes_source_navigation_noise():
    service = ListingAIService()
    generated = {"title": "Portable toilet", "description": "Portable toilet. Free 30-day refund and select delivery location.", "item_specifics": {"Type": "Portable Toilet"}, "missing_information": []}
    assert service._draft_quality(generated) == "weak"


def test_generate_rejects_thin_llm_copy_when_evidence_fallback_is_reviewable(monkeypatch):
    service = ListingAIService()

    monkeypatch.setattr(service, "_llm_generation", lambda *args, **kwargs: {
        "result": {
            "title": "TrailCo Portable Camping Toilet",
            "description": "A portable toilet for camping.",
        },
        "metadata": {"validation_status": "validated", "response_provider": "openai"},
    })
    generated = service.generate({
        "title_hint": "TrailCo Portable Camping Toilet",
        "source_type": "amazon_vine",
        "source_metadata": {"source_facts": {
            "feature_bullets": [
                "Foldable seat with splash-resistant design",
                "Removable waste tank for easier cleaning",
            ],
            "specifications": {"Capacity": "5.3 gallons", "Material": "HDPE plastic"},
        }},
    })

    assert generated["description"] != "A portable toilet for camping."
    assert "Foldable seat" in generated["description"]
    assert generated["ai_metadata"]["description_quality_gate"] == "fallback_evidence_composer"
