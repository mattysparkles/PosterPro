from types import SimpleNamespace

from app.services.facebook_category_resolver import resolve_facebook_category


def listing(title, description="", category_suggestion=None, shipping_profile=None):
    return SimpleNamespace(
        title=title, description=description, category_suggestion=category_suggestion,
        item_specifics={}, source_metadata={}, marketplace_data={}, shipping_profile=shipping_profile or {},
    )


def test_pillow_does_not_map_to_furniture():
    decision = resolve_facebook_category(listing("Smart AI Anti-Snore System Pillow"))
    assert "Pillow" in decision.category
    assert decision.category != "Furniture"
    assert decision.supports_shipping


def test_access_control_does_not_map_to_furniture():
    decision = resolve_facebook_category(listing("TT-Lock RFID fingerprint access control door system"))
    assert "Security" in decision.category
    assert decision.category != "Furniture"
    assert decision.supports_shipping


def test_ordinary_electronics_use_shipping_capable_category():
    decision = resolve_facebook_category(listing("72V Battery BMS protection controller board"))
    assert decision.category == "Electronics / Electrical Components"
    assert decision.supports_shipping


def test_common_vine_products_with_shipping_use_evidence_based_categories():
    cases = {
        "Corner Cut RV Mattress Topper with right-angle cut": "Home / Bedding / Mattresses",
        "Heated Drinking Water Hose for RV, 75FT": "Home Improvement / Plumbing",
        "Club Car Tempo LED Deluxe Light Kit": "Automotive / Parts & Accessories",
    }
    for title, expected in cases.items():
        decision = resolve_facebook_category(listing(title))
        assert decision.category == expected
        assert decision.supports_shipping


def test_furniture_requires_strong_evidence():
    assert resolve_facebook_category(listing("RV bedroom door light")).category != "Furniture"
    assert resolve_facebook_category(listing("Paint sprayer", "Ideal for furniture and cabinets")).category != "Furniture"
    decision = resolve_facebook_category(listing("Solid wood nightstand bedside table"))
    assert decision.category == "Furniture"
    assert not decision.supports_shipping


def test_unknown_shippable_product_is_blockable_not_furniture():
    decision = resolve_facebook_category(listing("Item B0GZNL3TKC"))
    assert decision.category is None
    assert decision.source == "shipping_guardrail"
