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


def test_processing_listing_is_not_publishable_or_needs_review(db_session):
    user = User(email="readiness-processing@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="description_generation", needs_review=True,
        title="Product", description="A detailed product description with supported facts.",
        category_suggestion="Sporting Goods", listing_price=20,
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing, marketplace="facebook")
    assert result["processing_complete"] is False
    assert result["publishable"] is False
    assert result["queue"] == "PROCESSING"


def test_destination_preflight_blocker_is_scoped_to_destination(db_session):
    user = User(email="readiness-destination@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, description="Useful product description", category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg","operator_state":"approved","role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False}, marketplace_data={"targets":["ebay","facebook"],"marketplace_preflight":{"by_marketplace":{"ebay":{"status":"blocked","blockers":[{"code":"REQUIRED_ASPECT","message":"Size is required"}]},"facebook":{"status":"ready","blockers":[],"warnings":[]}}}})
    db_session.add(listing); db_session.commit()
    ebay = canonical_listing_readiness(listing, marketplace="ebay")
    facebook = canonical_listing_readiness(listing, marketplace="facebook")
    assert ebay["attention_required"] is True
    assert ebay["publishable"] is False
    assert facebook["attention_required"] is False


def test_missing_description_is_a_real_readiness_blocker(db_session):
    user = User(email="readiness-description@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False})
    result = canonical_listing_readiness(listing)
    assert result["attention_required"] is True
    assert result["publishable"] is False
    assert "Description is missing" in result["blocking_reasons"]


def test_source_draft_with_token_description_requires_enrichment(db_session):
    user = User(email="readiness-thin-vine@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id,
        source_type="amazon_vine",
        processing_state="complete",
        needs_review=True,
        description="Great product for everyday use.",
        category_suggestion="Sporting Goods",
        listing_price=20,
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing)
    assert result["attention_required"] is True
    assert "product-specific enrichment" in result["blocking_reasons"][0].lower()


def test_missing_required_aspects_are_canonical_blockers(db_session):
    user = User(email="readiness-aspects@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="complete", needs_review=True,
        title="Quality item", description="A useful, detailed product description with enough facts for review.",
        category_suggestion="Sporting Goods", listing_price=20,
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
    )
    listing.readiness_summary = {"missing_required_aspects": ["Size Type"], "quality_complete": True}
    db_session.add(listing); db_session.commit()
    result = canonical_listing_readiness(listing, marketplace="ebay")
    assert result["attention_required"] is True
    assert result["publishable"] is False
    assert "Size Type" in result["blocking_reasons"][0]
