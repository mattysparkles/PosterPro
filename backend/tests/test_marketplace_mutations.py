from app.models.models import Listing
from app.services.marketplace_mutations import apply_marketplace_operation_plan


def test_compound_plan_preserves_untargeted_marketplaces_and_canonical_value():
    listing = Listing(
        id=123,
        user_id=7,
        listing_price=50,
        title="Canonical title",
        description="Canonical description",
        marketplace_data={
            "marketplace_overrides": {
                "ebay": {"price": 49},
                "facebook": {"price": 47},
                "mercari": {"price": 45},
            }
        },
    )

    result = apply_marketplace_operation_plan(
        [
            {"listing_ids": [123], "marketplaces": ["ebay", "facebook"], "field": "price", "action": "set", "value": 25},
            {"listing_ids": [123], "marketplaces": ["ebay"], "field": "shipping", "action": "set", "value": "free"},
        ],
        {123: listing},
    )

    overrides = listing.marketplace_data["marketplace_overrides"]
    assert overrides["ebay"]["price"] == 25
    assert overrides["facebook"]["price"] == 25
    assert overrides["ebay"]["shipping"]["free_shipping"] is True
    assert overrides["mercari"]["price"] == 45
    assert listing.listing_price == 50
    assert {change["marketplace"] for change in result["changes"]} == {"ebay", "facebook"}


def test_preview_is_non_mutating_and_reports_untouched_markets():
    listing = Listing(
        id=124,
        user_id=7,
        listing_price=50,
        title="Canonical title",
        marketplace_data={"marketplace_overrides": {"ebay": {"price": 49}, "mercari": {"price": 45}}},
    )

    result = apply_marketplace_operation_plan(
        [{"listing_ids": [124], "marketplaces": ["ebay"], "field": "price", "action": "set", "value": 25}],
        {124: listing},
        preview_only=True,
    )

    assert result["preview"] is True
    assert result["changes"][0]["before"] == 49
    assert result["changes"][0]["after"] == 25
    assert listing.marketplace_data["marketplace_overrides"]["mercari"]["price"] == 45
    assert listing.marketplace_data["marketplace_overrides"]["ebay"]["price"] == 49
