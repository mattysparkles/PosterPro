import json
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services import canonical_taxonomy_mapping as mappings


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
