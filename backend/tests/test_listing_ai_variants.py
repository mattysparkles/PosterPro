from app.services.listing_ai import ListingAIService, build_listing_description
from app.prompts.templates import get_prompt_template


def test_google_listing_intelligence_prompt_requires_substantive_original_sales_copy():
    prompt = get_prompt_template("generate_listing_intelligence")
    assert "professional, buyer-facing sales copy" in prompt
    assert "never reproduce customer-facing source prose" in prompt
    assert "Never mention PosterPro, AI" in prompt
    assert "natural product-appropriate call to action" in prompt
from app.services.marketplace_field_mapper import apply_generated_marketplace_drafts, marketplace_description_variants
from types import SimpleNamespace


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


def test_long_form_marketplaces_keep_rich_copy_separate_from_mercari():
    canonical = "A detailed product overview with useful buyer facts. " * 30
    listing = SimpleNamespace(canonical_description=canonical, description=canonical, marketplace_descriptions={})
    variants = marketplace_description_variants(listing)
    assert len(variants["canonical"]) > 1000
    assert len(variants["ebay"]) > 1000
    assert len(variants["facebook"]) > 1000
    assert len(variants["mercari"]) <= 1000
    assert variants["ebay"] != variants["mercari"]


def test_generated_marketplace_drafts_are_persisted_without_overwriting_operator_copy():
    listing = SimpleNamespace(
        description="Verified master description.",
        canonical_description="Verified master description.",
        marketplace_descriptions={"facebook": "Human Facebook copy.", "_provenance": {"facebook": "operator_edited"}},
        marketplace_data={},
    )
    apply_generated_marketplace_drafts(listing, {
        "ebay": {"description": "Detailed eBay variant with specifications."},
        "facebook": {"description": "Generated Facebook variant that must not replace the human copy."},
        "mercari": {"description": "Mercari condensed variant."},
    })
    assert listing.marketplace_descriptions["ebay"] == "Detailed eBay variant with specifications."
    assert listing.marketplace_descriptions["facebook"] == "Human Facebook copy."
    assert listing.marketplace_descriptions["mercari"] == "Mercari condensed variant."
    assert listing.marketplace_descriptions["_provenance"]["facebook"] == "operator_edited"


def test_regenerate_generated_channel_copy_uses_new_canonical_but_preserves_operator_variant():
    from app.services.marketplace_field_mapper import persist_marketplace_description_variants
    listing = SimpleNamespace(
        description="Clean factual copy about the item and its visible details.",
        canonical_description="Clean factual copy about the item and its visible details.",
        marketplace_descriptions={
            "ebay": "Old practical, well-specified option. Add it to your setup while available.",
            "mercari": "Old practical, well-specified option.",
            "facebook": "Operator-approved unique copy.",
            "_provenance": {"ebay": "generated", "mercari": "generated", "facebook": "operator_edited"},
        },
        marketplace_data={},
    )
    persist_marketplace_description_variants(listing, regenerate_generated=True)
    assert listing.marketplace_descriptions["ebay"] == listing.canonical_description
    assert listing.marketplace_descriptions["mercari"] == listing.canonical_description
    assert listing.marketplace_descriptions["facebook"] == "Operator-approved unique copy."


def test_fallback_copy_does_not_leak_internal_photo_or_generation_diagnostics():
    generated = ListingAIService()._fallback_generation({
        "title_hint": "Two Multi-Signed New York Yankees Baseballs",
        "source_type": "intake_head_slate",
        "image_count": 16,
        "voice_transcript": "Two baseballs signed by several Yankees players. Signatures are unidentified and unauthenticated.",
    })
    text = generated["description"].lower()
    for forbidden in (
        "only one photo is attached", "draft was generated", "source signals",
        "practical, well-specified", "for buyers who need", "add it to your setup",
    ):
        assert forbidden not in text


def test_description_builder_keeps_product_copy_factual_and_filters_internal_notes():
    description = build_listing_description(
        title="Two Multi-Signed New York Yankees Baseballs",
        condition_notes="Used; signatures are unidentified and unauthenticated",
        photo_notes=["Draft was generated from intake voice source signals", "Only one photo is attached"],
    )
    assert "Two Multi-Signed New York Yankees Baseballs." in description
    assert "unidentified and unauthenticated" in description
    assert "generated from" not in description.lower()
    assert "only one photo" not in description.lower()
    assert "add it to your setup" not in description.lower()
