from app.models.models import Listing, User
from app.services.marketplace_mutations import apply_marketplace_operation


def test_marketplace_operation_changes_only_requested_destinations(db_session):
    user = User(email="mutation-scope@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, title="Item", listing_price=50, marketplace_data={"marketplace_overrides": {"mercari": {"price": 55}}})
    result = apply_marketplace_operation(listing, marketplaces=["ebay", "facebook"], field="price", action="percentage_change", value=-10)
    assert listing.marketplace_data["marketplace_overrides"]["ebay"]["price"] == 45
    assert listing.marketplace_data["marketplace_overrides"]["facebook"]["price"] == 45
    assert listing.marketplace_data["marketplace_overrides"]["mercari"]["price"] == 55
    assert "mercari" in result["untouched_markets"]


def test_canonical_operation_is_explicit_and_records_operator_provenance(db_session):
    user = User(email="mutation-canonical@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, listing_price=40)
    apply_marketplace_operation(listing, marketplaces=["canonical"], field="price", value=39.99)
    assert listing.listing_price == 39.99
    assert not listing.marketplace_data.get("marketplace_overrides")


def test_invalid_target_is_rejected(db_session):
    user = User(email="mutation-invalid@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id)
    try:
        apply_marketplace_operation(listing, marketplaces=["unknown"], field="price", value=1)
    except ValueError as exc:
        assert "Unsupported marketplace" in str(exc)
    else:
        raise AssertionError("invalid marketplace target should fail")
