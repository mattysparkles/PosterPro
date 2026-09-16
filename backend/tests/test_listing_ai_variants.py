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
