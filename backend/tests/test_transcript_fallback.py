from app.services.listing_ai import ListingAIService, extract_explicit_transcript_fields


def test_explicit_transcript_fallback_science_fair():
    text = (
        "This is a Science Fair branded 160 in 1 electronic project kit, catalog number 28-258. "
        "It is used and appears pretty complete. Quantity is one."
    )
    fields = extract_explicit_transcript_fields(text)
    assert fields["brand"] == "Science Fair"
    assert fields["catalog_number"] == "28-258"
    assert fields["condition"] == "Used"
    assert fields["quantity"] == 1
    assert fields["verification_required"] is True
    generated = ListingAIService()._fallback_generation({"voice_transcript": text})
    assert generated["item_specifics"]["Brand"] == "Science Fair"
    assert generated["item_specifics"]["Catalog Number"] == "28-258"
    assert generated["title"].lower().startswith("science fair")
