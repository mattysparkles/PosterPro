from app.models.models import Listing, User
from app.services.marketplace_mutations import apply_marketplace_operation, apply_marketplace_operation_plan, validate_marketplace_operation_plan
from app.services.marketplace_field_mapper import build_marketplace_payload


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
    assert not (listing.marketplace_data or {}).get("marketplace_overrides")
    assert listing.source_metadata["recovery"]["field_provenance"]["price"] == "human_operator"
    assert "price" in listing.source_metadata["recovery"]["operator_locked_fields"]


def test_canonical_description_operation_updates_rich_master_copy(db_session):
    user = User(email="mutation-description@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, description="Old copy", canonical_description="Old copy")
    result = apply_marketplace_operation(listing, marketplaces=["canonical"], field="description", value="New master copy")
    assert result["changed"][0]["after"] == "New master copy"
    assert listing.description == "New master copy"
    assert listing.canonical_description == "New master copy"


def test_destination_description_regeneration_queues_without_overwriting_copy(db_session):
    user = User(email="mutation-description-regen@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, description="Master copy", canonical_description="Master copy", marketplace_data={"marketplace_overrides": {"facebook": {"description": "Human Facebook copy"}}})
    result = apply_marketplace_operation(listing, marketplaces=["facebook"], field="description", action="regenerate")
    assert result["changed"][0]["after"] == "queued"
    assert listing.canonical_description == "Master copy"
    assert listing.marketplace_data["marketplace_overrides"]["facebook"]["description"] == "Human Facebook copy"
    assert listing.marketplace_data["description_regeneration_requests"][0]["status"] == "QUEUED"


def test_destination_description_edit_is_scoped_and_preserves_canonical_and_other_variants(db_session):
    user = User(email="mutation-description-scope@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id,
        description="Rich master copy",
        canonical_description="Rich master copy",
        marketplace_descriptions={"ebay": "eBay generated copy", "mercari": "Mercari copy"},
    )
    apply_marketplace_operation(listing, marketplaces=["ebay"], field="description", value="Operator eBay copy")
    assert listing.canonical_description == "Rich master copy"
    assert listing.marketplace_descriptions["ebay"] == "Operator eBay copy"
    assert listing.marketplace_descriptions["mercari"] == "Mercari copy"
    assert listing.marketplace_data["marketplace_description_provenance"]["ebay"] == "operator_edited"


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


def test_compound_plan_previews_without_mutating_untargeted_markets(db_session):
    user = User(email="mutation-plan@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, listing_price=50, marketplace_data={"marketplace_overrides": {"mercari": {"price": 55}}})
    db_session.add(listing); db_session.flush()
    before = dict(listing.marketplace_data)
    result = apply_marketplace_operation_plan([
        {"listing_ids": [listing.id], "markets": ["ebay", "facebook"], "field": "price", "action": "percentage_change", "value": -10},
        {"listing_ids": [listing.id], "markets": ["ebay"], "field": "shipping", "action": "set", "value": "free"},
    ], {listing.id: listing}, preview_only=True)
    assert result["preview"] is True
    assert listing.marketplace_data == before
    assert len(result["changes"]) == 3


def test_compound_plan_preview_does_not_mutate_nested_overrides(db_session):
    user = User(email="mutation-plan-deep-copy@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, listing_price=50, marketplace_data={"marketplace_overrides": {"ebay": {"price": 50}}})
    db_session.add(listing); db_session.flush()
    before = {"marketplace_overrides": {"ebay": {"price": 50}}}
    apply_marketplace_operation_plan([
        {"listing_id": listing.id, "markets": ["ebay"], "field": "shipping", "value": "free"},
    ], {listing.id: listing}, preview_only=True)
    assert listing.marketplace_data == before


def test_compound_plan_applies_exact_targets(db_session):
    user = User(email="mutation-plan-apply@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, listing_price=40, marketplace_data={"marketplace_overrides": {"mercari": {"price": 42}}})
    db_session.add(listing); db_session.flush()
    apply_marketplace_operation_plan([
        {"listing_id": listing.id, "marketplaces": ["ebay", "facebook"], "field": "price", "value": 35},
    ], {listing.id: listing})
    overrides = listing.marketplace_data["marketplace_overrides"]
    assert overrides["ebay"]["price"] == 35
    assert overrides["facebook"]["price"] == 35
    assert overrides["mercari"]["price"] == 42


def test_compound_plan_persists_canonical_and_variant_content_fields(db_session):
    user = User(email="mutation-plan-content@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id,
        description="Master copy",
        canonical_description="Master copy",
        marketplace_descriptions={"facebook": "Existing Facebook copy"},
    )
    db_session.add(listing); db_session.flush()
    apply_marketplace_operation_plan([
        {"listing_id": listing.id, "markets": ["canonical"], "field": "description", "value": "Updated master copy"},
        {"listing_id": listing.id, "markets": ["ebay"], "field": "description", "value": "Updated eBay copy"},
    ], {listing.id: listing})
    assert listing.description == "Updated master copy"
    assert listing.canonical_description == "Updated master copy"
    assert listing.marketplace_descriptions == {"facebook": "Existing Facebook copy", "ebay": "Updated eBay copy"}


def test_compound_plan_rejects_later_invalid_operation_without_mutation(db_session):
    user = User(email="mutation-plan-invalid@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, listing_price=40)
    db_session.add(listing); db_session.flush()
    try:
        apply_marketplace_operation_plan([
            {"listing_id": listing.id, "marketplaces": ["ebay"], "field": "price", "value": 35},
            {"listing_id": listing.id, "marketplaces": ["unknown"], "field": "price", "value": 30},
        ], {listing.id: listing})
    except ValueError:
        pass
    else:
        raise AssertionError("invalid compound operation should fail validation")
    assert not (listing.marketplace_data or {}).get("marketplace_overrides")


def test_compound_plan_supports_destination_specific_end(db_session):
    user = User(email="mutation-plan-end@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id)
    db_session.add(listing); db_session.flush()
    result = apply_marketplace_operation_plan([
        {"listing_id": listing.id, "markets": ["poshmark"], "field": "listing", "action": "end"},
    ], {listing.id: listing})
    assert result["changes"][0]["after"] == "end"
    assert listing.marketplace_data["marketplace_overrides"]["poshmark"]["status"] == "end"


def test_plan_validation_is_side_effect_free(db_session):
    user = User(email="mutation-plan-validate@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, listing_price=50)
    db_session.add(listing); db_session.flush()
    validate_marketplace_operation_plan([{"listing_id": listing.id, "markets": ["ebay"], "field": "price", "value": 45}], {listing.id: listing})
    assert listing.listing_price == 50
    assert not (listing.marketplace_data or {}).get("marketplace_overrides")


def test_free_shipping_command_is_normalized_for_marketplace_payload(db_session):
    user = User(email="mutation-shipping-command@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id,
        title="Camping item",
        description="Useful camping item",
        listing_price=40,
        shipping_profile={"mode": "calculated", "free_shipping": False, "domestic_service": "Ground"},
    )
    db_session.add(listing); db_session.flush()
    apply_marketplace_operation_plan([
        {"listing_id": listing.id, "marketplaces": ["ebay"], "field": "shipping", "value": "free"},
    ], {listing.id: listing})
    override = listing.marketplace_data["marketplace_overrides"]["ebay"]["shipping"]
    assert override["free_shipping"] is True
    assert override["shipping_method"] == "free"
    payload = build_marketplace_payload(listing, "ebay")
    assert payload["shipping_policy"]["free_shipping"] is True
    assert listing.shipping_profile["free_shipping"] is False
