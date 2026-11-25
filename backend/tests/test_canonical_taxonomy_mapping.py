import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.services import canonical_taxonomy_mapping as mappings
from app.api.taxonomy_registry import ApplyConfirmedMappingRequest, apply_confirmed_mapping_to_cohort
from app.models.models import Listing, User
from app.models.enums import ListingStatus
from app.services.inventory_taxonomy_cohorts import _identity


def _mapping_schema(db_session):
    raw = db_session.connection().connection
    raw.executescript("""
      DROP TABLE IF EXISTS marketplace_taxonomy_mappings;
      DROP TABLE IF EXISTS canonical_product_concepts;
      DROP TABLE IF EXISTS marketplace_taxonomy_categories;
      DROP TABLE IF EXISTS marketplace_taxonomy_versions;
    """)
    raw.executescript((Path(__file__).parents[1] / "migrations/20261023_marketplace_taxonomy_registry.sql").read_text())
    raw.executescript((Path(__file__).parents[1] / "migrations/20261024_canonical_taxonomy_mappings.sql").read_text())
    now = "2026-10-23 12:00:00"
    db_session.execute(text("""INSERT INTO marketplace_taxonomy_versions
      (version_id,marketplace,scope_key,native_version,source,captured_at,last_checked_at,verification_status,
       completeness_status,staleness_status,category_count,terminal_count,unresolved_count,is_current)
      VALUES ('ebay:EBAY_US:0:134','ebay','EBAY_US:0','134','official','2026-10-23 11:00',:now,
       'authoritatively_verified','complete','fresh',1,1,0,TRUE)"""), {"now": now})
    db_session.execute(text("""INSERT INTO marketplace_taxonomy_categories
      (version_id,marketplace,category_key,category_id,category_name,parent_category_key,parent_category_id,
       full_path,path_json,depth,terminal_status,publishability_status,required_attributes_json,data_source,
       source_evidence_json,last_verified_at,verification_status,staleness_status,automated_selection_eligible)
      VALUES ('ebay:EBAY_US:0:134','ebay','EBAY_US:0:169291','169291','Handbags',NULL,NULL,
       'Root > Women > Handbags','[]',2,'terminal','publishable',:required,'official_taxonomy_api',:evidence,
       :now,'authoritatively_verified','fresh',TRUE)"""), {"now": now, "required": json.dumps([]), "evidence": json.dumps({"api": "taxonomy"})})
    db_session.commit()


def test_concepts_are_tenant_scoped_and_operator_mappings_are_versioned(db_session):
    _mapping_schema(db_session)
    first = mappings.create_or_update_concept(db_session, owner_user_id=10,
        concept_name="Women's handbag", concept_description="A handbag carried by women",
        structured_facts={"form": "bag", "department": "women"}, evidence=["inventory title", "source facts"])
    second = mappings.create_or_update_concept(db_session, owner_user_id=11, concept_name="Women's handbag")
    assert first["concept_id"] != second["concept_id"]
    with pytest.raises(LookupError):
        mappings.get_concept(db_session, owner_user_id=10, concept_id=second["concept_id"])

    candidates = mappings.concept_candidates(db_session, owner_user_id=10, concept_id=first["concept_id"], marketplaces=["ebay"])
    assert candidates["mapping_state"] == "candidate_generation_only"
    assert candidates["automatic_selection_permitted"] is False
    mapping = mappings.confirm_category_mapping(db_session, owner_user_id=10, concept_id=first["concept_id"],
        marketplace="ebay", version_id="ebay:EBAY_US:0:134", category_key="EBAY_US:0:169291",
        rationale="The reviewed inventory item is a women's handbag and this destination leaf is the verified handbag category.",
        evidence={"listing_id": 55, "operator_note": "reviewed product description"})
    assert mapping["verification_status"] == "operator_confirmed"
    assert bool(mapping["operator_confirmed"]) is True
    assert mapping["taxonomy_native_version"] == "134"
    assert mapping["confidence_score"] == 1.0
    assert mapping["evidence"]["operator_evidence"]["listing_id"] == 55

    db_session.execute(text("UPDATE marketplace_taxonomy_versions SET is_current=FALSE WHERE version_id='ebay:EBAY_US:0:134'"))
    assert mappings.invalidate_stale_mappings(db_session, owner_user_id=10, marketplace="ebay") == 1
    stale = mappings.get_mapping(db_session, owner_user_id=10, mapping_id=mapping["mapping_id"])
    assert stale["verification_status"] == "invalidated"
    assert bool(stale["operator_confirmed"]) is False
    assert stale["invalidation_reason"] == "Saved taxonomy version is no longer current"


def test_inferred_or_unknown_leaf_cannot_be_operator_confirmed_as_verified_mapping(db_session):
    _mapping_schema(db_session)
    concept = mappings.create_or_update_concept(db_session, owner_user_id=10, concept_name="Handbag")
    db_session.execute(text("""UPDATE marketplace_taxonomy_categories SET terminal_status='unknown',
       publishability_status='unknown',verification_status='inferred_candidate',automated_selection_eligible=FALSE
       WHERE category_key='EBAY_US:0:169291'"""))
    db_session.commit()
    with pytest.raises(ValueError, match="verified terminal leaf"):
        mappings.confirm_category_mapping(db_session, owner_user_id=10, concept_id=concept["concept_id"],
            marketplace="ebay", version_id="ebay:EBAY_US:0:134", category_key="EBAY_US:0:169291",
            rationale="A category is a good textual match for this concept.")
    assert db_session.execute(text("SELECT COUNT(*) FROM marketplace_taxonomy_mappings")).scalar_one() == 0


def test_confirmed_category_cohort_apply_is_owner_scoped_idempotent_and_preserves_overrides(db_session):
    _mapping_schema(db_session)
    user = User(email="taxonomy-cohort-owner@example.com")
    db_session.add(user)
    db_session.flush()
    source = Listing(user_id=user.id, status=ListingStatus.draft, title="Example tote bag",
                     category_suggestion="Bags", source_metadata={"asin": "B012345678"},
                     marketplace_data={})
    same_item = Listing(user_id=user.id, status=ListingStatus.draft, title="Example tote bag",
                        category_suggestion="Bags", source_metadata={"asin": "B012345678"},
                        marketplace_data={})
    different_item = Listing(user_id=user.id, status=ListingStatus.draft, title="Example backpack",
                             category_suggestion="Bags", source_metadata={"asin": "B087654321"},
                             marketplace_data={})
    override = Listing(user_id=user.id, status=ListingStatus.draft, title="Example tote bag",
                       category_suggestion="Bags", source_metadata={"asin": "B012345678"},
                       marketplace_data={"marketplace_category_mappings": {"ebay": {"mapping_id": "manual-other", "verification_status": "operator_confirmed"}}})
    db_session.add_all([source, same_item, different_item, override])
    db_session.commit()
    concept = mappings.create_or_update_concept(db_session, owner_user_id=user.id, concept_name="Example tote bag")
    mapping = mappings.confirm_category_mapping(db_session, owner_user_id=user.id, concept_id=concept["concept_id"],
        marketplace="ebay", version_id="ebay:EBAY_US:0:134", category_key="EBAY_US:0:169291",
        rationale="The current verified leaf accurately describes this exact ASIN product.",
        evidence={"posterpro_listing_id": source.id})
    source.marketplace_data = {"marketplace_category_mappings": {"ebay": {
        "mapping_id": mapping["mapping_id"], "verification_status": "operator_confirmed",
        "terminal_status": "terminal", "publishability_status": "publishable", "staleness_status": "fresh",
    }}}
    db_session.commit()

    request = ApplyConfirmedMappingRequest(marketplace="ebay", source_listing_id=source.id,
        listing_ids=[source.id, same_item.id, different_item.id, override.id])
    result = apply_confirmed_mapping_to_cohort(request, db_session, user)
    assert result["applied"] == [same_item.id]
    assert result["already_applied"] == [source.id]
    assert result["identity_mismatch"] == [different_item.id]
    assert result["preserved_existing_selection"] == [override.id]
    db_session.refresh(same_item)
    assert same_item.category_id == "169291"
    assert same_item.marketplace_data["marketplace_category_mappings"]["ebay"]["mapping_id"] == mapping["mapping_id"]

    retried = apply_confirmed_mapping_to_cohort(request, db_session, user)
    assert retried["applied"] == []
    assert retried["already_applied"] == [source.id, same_item.id]
    assert retried["published_or_queued"] is False


def test_bulk_category_identity_separates_variants_and_bundle_quantities():
    product_a = Listing(user_id=2, title="Bluetooth headphones", category_suggestion="Electronics",
                        source_metadata={"asin": "B012345678"}, item_specifics={"Color": "Black", "Package Quantity": "1"})
    product_b = Listing(user_id=2, title="Bluetooth headphones", category_suggestion="Electronics",
                        source_metadata={"asin": "B012345678"}, item_specifics={"Color": "White", "Package Quantity": "1"})
    bundle = Listing(user_id=2, title="Bluetooth headphones", category_suggestion="Electronics",
                     source_metadata={"asin": "B012345678"}, item_specifics={"Color": "Black", "Package Quantity": "2"})
    assert _identity(product_a)[0] != _identity(product_b)[0]
    assert _identity(product_a)[0] != _identity(bundle)[0]


def test_confirmed_cohort_apply_requires_explicit_source_and_rolls_back(db_session, monkeypatch):
    _mapping_schema(db_session)
    user = User(email="taxonomy-transaction-owner@example.com")
    db_session.add(user)
    db_session.flush()
    source = Listing(user_id=user.id, status=ListingStatus.draft, title="Example headphones",
                     category_suggestion="Electronics", source_metadata={"asin": "B012345678"}, marketplace_data={})
    target = Listing(user_id=user.id, status=ListingStatus.draft, title="Example headphones",
                     category_suggestion="Electronics", source_metadata={"asin": "B012345678"}, marketplace_data={})
    db_session.add_all([source, target]); db_session.commit()
    concept = mappings.create_or_update_concept(db_session, owner_user_id=user.id, concept_name="Example headphones")
    mapping = mappings.confirm_category_mapping(db_session, owner_user_id=user.id, concept_id=concept["concept_id"],
        marketplace="ebay", version_id="ebay:EBAY_US:0:134", category_key="EBAY_US:0:169291",
        rationale="Verified exact headphone product category.", evidence={"posterpro_listing_id": source.id})
    source.marketplace_data = {"marketplace_category_mappings": {"ebay": {
        "mapping_id": mapping["mapping_id"], "verification_status": "operator_confirmed",
        "terminal_status": "terminal", "publishability_status": "publishable", "staleness_status": "fresh",
    }}}
    db_session.commit()
    with pytest.raises(HTTPException) as source_error:
        apply_confirmed_mapping_to_cohort(ApplyConfirmedMappingRequest(
            marketplace="ebay", source_listing_id=source.id, listing_ids=[target.id]), db_session, user)
    assert "explicitly" in source_error.value.detail

    original_flush = db_session.flush
    def fail_final_flush(*args, **kwargs):
        raise RuntimeError("simulated transaction failure")
    monkeypatch.setattr(db_session, "flush", fail_final_flush)
    with pytest.raises(RuntimeError, match="simulated transaction failure"):
        apply_confirmed_mapping_to_cohort(ApplyConfirmedMappingRequest(
            marketplace="ebay", source_listing_id=source.id, listing_ids=[source.id, target.id]), db_session, user)
    monkeypatch.setattr(db_session, "flush", original_flush)
    db_session.expire_all()
    assert db_session.get(Listing, target.id).category_id is None
    assert db_session.get(Listing, target.id).marketplace_data == {}
