from types import SimpleNamespace

from app.models.models import Listing, User
from app.services.canonical_readiness import canonical_listing_readiness
from app.services.listing_ai import assess_description_quality
from app.workers.tasks import _apply_vine_quality_lifecycle


def test_reviewable_complete_listing_is_not_attention(db_session):
    user = User(email="readiness-review@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, description="Useful product description", category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg","operator_state":"approved","role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False})
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_REVIEW"
    assert result["attention_required"] is False
    assert result["destination_publishable"] is None
    assert result["processing_stage"] == "complete"


def test_stale_attention_state_without_current_blocker_is_reclassified_for_queue(db_session):
    user = User(email="readiness-stale-attention@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="needs_attention", needs_review=True,
        title="Portable Camping Toilet", description="A useful portable camping toilet with a foldable seat for outdoor use and easy cleanup.",
        category_suggestion="Sporting Goods", category_id="123", listing_price=25,
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing)
    assert result["attention_required"] is False
    assert result["queue"] == "NEEDS_REVIEW"


def test_blocked_listing_cannot_be_publishable(db_session):
    user = User(email="readiness-blocked@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="needs_attention", processing_blocking_reason="Missing identity", needs_review=True)
    result = canonical_listing_readiness(listing)
    assert result["queue"] == "NEEDS_ATTENTION"
    assert result["publishable"] is False
    assert "Missing identity" in result["blocking_reasons"]


def test_published_remote_listing_keeps_published_queue_identity_with_local_blocker(db_session):
    user = User(email="readiness-published@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, ebay_listing_id="EBAY-123", ebay_publish_status="POSTED",
        status="published", processing_state="needs_attention",
        processing_blocking_reason="Description needs enrichment", needs_review=False,
        title="Published item", description="Short", category_suggestion="Sporting Goods",
        listing_price=20, listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}],
    )
    result = canonical_listing_readiness(listing)
    assert result["remote_live"] is True
    assert result["remote_state"] == "PUBLISHED"
    assert result["queue"] == "PUBLISHED"
    assert result["publishable"] is False
    assert "Description needs enrichment" in result["blocking_reasons"]


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
    assert ebay["destination_publishable"] is False
    assert facebook["attention_required"] is False


def test_missing_description_is_a_real_readiness_blocker(db_session):
    user = User(email="readiness-description@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, processing_state="complete", needs_review=True, category_suggestion="Sporting Goods", listing_price=20, listing_images=[{"storage_path":"/media/item.jpg", "operator_state":"approved", "role":"primary"}], condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False})
    result = canonical_listing_readiness(listing)
    assert result["attention_required"] is True
    assert result["publishable"] is False
    assert "Description is missing" in result["blocking_reasons"]


def test_source_policy_breadcrumb_cannot_be_used_as_category(db_session):
    user = User(email="readiness-noisy-category@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id, processing_state="complete", needs_review=True,
        title="Portable camping toilet", description="A useful portable camping toilet for outdoor trips and emergency use.",
        category_suggestion="Amazon > FG 1910 > FREE 30-day refund/replacement",
        listing_price=25, listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False}, shipping_profile={"manual_measurement_needed": False},
    )
    result = canonical_listing_readiness(listing, marketplace="ebay")
    assert result["attention_required"] is True
    assert any("validated marketplace category" in reason for reason in result["blocking_reasons"])


def test_valid_other_taxonomy_leaf_is_not_treated_as_source_noise():
    from app.services.category_rules import is_source_noise_category

    assert not is_source_noise_category("Sporting Goods > Boxing & MMA > Protective Gear > Other Protective Equipment")
    assert is_source_noise_category("Other > Needs category review")


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


def test_vine_quality_worker_promotes_reviewable_row():
    description = "A portable charging accessory with USB output for everyday travel and backup power. The compact design is easy to pack and the included cable keeps setup simple."
    listing = SimpleNamespace(
        ebay_listing_id=None, marketplace_listings=[], readiness_summary={},
        listing_images=[{"storage_path": "/media/item.jpg", "operator_state": "approved", "role": "primary"}],
        condition_data={"operator_review_required": False},
        shipping_profile={"package_weight": "1 lb", "package_dimensions": {"length": 8, "width": 6, "height": 4}, "manual_measurement_needed": False},
        category_id="123", category_suggestion="Consumer Electronics", listing_price=25, suggested_price=25,
        marketplace_data={"quality_summary": {"ready_for_publish": True}}, source_type="amazon_vine",
        source_metadata={"amazon_product_facts": {"feature_bullets": ["Portable charging accessory", "USB output"], "specifications": {"Brand": "Example", "Type": "Charger"}}},
        canonical_description=description, description=description, title="Example Portable Charger",
        processing_state="needs_attention", processing_blocking_reason="image_retrying", processing_error_stage="image_enrichment", needs_review=False,
    )
    assert _apply_vine_quality_lifecycle(listing) is True
    assert listing.processing_state == "complete"
    assert listing.needs_review is True
    assert listing.processing_blocking_reason is None


def test_vine_quality_worker_does_not_rewrite_live_listing():
    listing = SimpleNamespace(ebay_listing_id="123456", marketplace_listings=[])
    assert _apply_vine_quality_lifecycle(listing) is False


def test_description_quality_reports_fact_coverage_and_source_noise():
    result = assess_description_quality(
        "Portable charger with USB output. Free 30-day refund/replacement.",
        title="Example Portable Charger",
        source_metadata={"amazon_product_facts": {"feature_bullets": ["USB output", "Compact travel design"], "specifications": {"Capacity": "10,000 mAh"}}},
    )
    assert result["has_source_noise"] is True
    assert "Description contains source-page policy or navigation text" in result["blockers"]
    assert result["source_fact_count"] == 3
    assert result["source_facts_covered"] >= 1
