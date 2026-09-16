from app.models.models import Listing, User
from app.services.canonical_readiness import canonical_listing_readiness


def test_reviewable_complete_listing_is_not_attention(db_session):
    user = User(email="readiness-review@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, description="Useful product description", category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg","operator_state":"approved","role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False})
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_REVIEW"
    assert result["attention_required"] is False


def test_blocked_listing_cannot_be_publishable(db_session):
    user = User(email="readiness-blocked@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="needs_attention", processing_blocking_reason="Missing identity", needs_review=True)
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["publishable"] is False


def test_destination_preflight_blocker_is_scoped_to_destination(db_session):
    user = User(email="readiness-destination@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, description="Useful product description", category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg","operator_state":"approved","role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False}, marketplace_data={"targets":["ebay","facebook"],"marketplace_preflight":{"by_marketplace":{"ebay":{"status":"blocked","blockers":[{"code":"REQUIRED_ASPECT","message":"Size is required"}]},"facebook":{"status":"ready","blockers":[],"warnings":[]}}}})
    db_session.add(listing); db_session.commit()
    ebay = canonical_listing_readiness(listing, marketplace="ebay")
    facebook = canonical_listing_readiness(listing, marketplace="facebook")
    assert ebay["attention_required"] is True
    assert ebay["publishable"] is False
    assert facebook["attention_required"] is False
