import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.services.marketplace_taxonomy_registry import (
    _ebay_aspect_cache,
    rank_taxonomy_candidates,
    search_registry_categories,
    synchronize_local_taxonomies,
    taxonomy_registry_status,
    _attribute_schema_state,
    _store_version,
)
from app.services.mercari_taxonomy import import_taxonomy
from app.api.taxonomy_registry import get_traversal_checkpoint, save_traversal_checkpoint, TraversalCheckpointRequest
from types import SimpleNamespace


def _registry_schema(db_session):
    db_session.connection().connection.executescript("""
        DROP TABLE IF EXISTS marketplace_taxonomy_categories;
        DROP TABLE IF EXISTS marketplace_taxonomy_versions;
    """)
    path = Path(__file__).parents[1].joinpath("migrations/20261023_marketplace_taxonomy_registry.sql")
    db_session.connection().connection.executescript(path.read_text())


def _checkpoint_schema(db_session):
    path = Path(__file__).parents[1].joinpath("migrations/20261101_taxonomy_traversal_checkpoints.sql")
    db_session.connection().connection.executescript(path.read_text())


def test_traversal_checkpoints_are_resumable_revisioned_and_tenant_scoped(db_session):
    _checkpoint_schema(db_session)
    owner = SimpleNamespace(id=2)
    checkpoint = {"schema_version": 1, "marketplace": "facebook", "source_version": "seller-form-v1",
                  "frontier": [["Household"]], "completed_paths": [], "categories": {}, "status": "in_progress"}
    saved = save_traversal_checkpoint(TraversalCheckpointRequest(
        marketplace="facebook", source_key="seller-account", source_version="seller-form-v1",
        expected_revision=0, checkpoint=checkpoint), db_session, owner)
    assert saved["revision"] == 1
    assert get_traversal_checkpoint("facebook", "seller-account", db_session, owner)["checkpoint"] == checkpoint
    other = SimpleNamespace(id=3)
    assert get_traversal_checkpoint("facebook", "seller-account", db_session, other)["checkpoint"] is None
    try:
        save_traversal_checkpoint(TraversalCheckpointRequest(
            marketplace="facebook", source_key="seller-account", source_version="seller-form-v1",
            expected_revision=0, checkpoint=checkpoint), db_session, owner)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("stale checkpoint revision was accepted")


def test_checkpoint_api_rejects_false_complete_branch_or_uninspected_leaf_schema(db_session):
    _checkpoint_schema(db_session)
    owner = SimpleNamespace(id=2)
    bad = {"schema_version": 1, "marketplace": "mercari", "source_version": "v1",
           "frontier": [], "completed_paths": [""], "unresolved_paths": {}, "status": "complete",
           "categories": {"x": {"terminal_status": "confirmed_leaf", "required_fields_schema_status": "uncollected"}}}
    try:
        save_traversal_checkpoint(TraversalCheckpointRequest(
            marketplace="mercari", source_key="source", source_version="v1",
            expected_revision=0, checkpoint=bad), db_session, owner)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
    else:
        raise AssertionError("checkpoint API accepted a leaf without a verified schema")


def _legacy_ebay_schema(db_session, synced_at):
    db_session.connection().connection.executescript("DROP TABLE IF EXISTS ebay_taxonomy_categories;")
    db_session.execute(text("""
        CREATE TABLE ebay_taxonomy_categories (
            marketplace TEXT NOT NULL, category_tree_id TEXT NOT NULL, category_tree_version TEXT NOT NULL,
            category_id TEXT NOT NULL, category_name TEXT NOT NULL, parent_category_id TEXT,
            leaf BOOLEAN NOT NULL, full_path TEXT NOT NULL, depth INTEGER NOT NULL,
            last_synced_at TIMESTAMP NOT NULL, source TEXT NOT NULL,
            PRIMARY KEY(marketplace,category_tree_id,category_id)
        )
    """))
    db_session.execute(text("""
        INSERT INTO ebay_taxonomy_categories VALUES
        ('EBAY_US','0','134','0','Root',NULL,FALSE,'Root',0,:synced,'ebay_taxonomy_full_tree'),
        ('EBAY_US','0','134','123','Handbags', '0',FALSE,'Root > Handbags',1,:synced,'ebay_taxonomy_full_tree'),
        ('EBAY_US','0','134','456','Shoulder Bags','123',TRUE,'Root > Handbags > Shoulder Bags',2,:synced,'ebay_taxonomy_full_tree')
    """), {"synced": synced_at})


def test_registry_imports_ebay_as_authoritative_and_keeps_aspect_requirements(db_session):
    _registry_schema(db_session)
    _legacy_ebay_schema(db_session, datetime.now(UTC).replace(tzinfo=None))
    db_session.execute(text("""
        CREATE TABLE IF NOT EXISTS marketplace_metadata_cache (
            id INTEGER PRIMARY KEY, marketplace TEXT, cache_key TEXT, payload TEXT,
            source_version TEXT, expires_at TIMESTAMP, updated_at TIMESTAMP
        )
    """))
    aspect_payload = {"aspects": [
        {"localizedAspectName": "Exterior Material", "aspectConstraint": {"aspectRequired": True}, "aspectValues": [{"localizedValue": "Leather"}]},
        {"localizedAspectName": "Style", "aspectConstraint": {"aspectRequired": False}, "aspectUsage": "RECOMMENDED"},
        {"localizedAspectName": "Exterior Color", "aspectConstraint": {"aspectRequired": True}},
        {"localizedAspectName": "Department", "aspectConstraint": {"aspectRequired": False}},
        {"localizedAspectName": "Size", "aspectConstraint": {"aspectRequired": False}, "aspectValues": [{"valueConstraints": [{"applicableForLocalizedAspectName": "Style"}]}]},
    ]}
    db_session.execute(text("""
        INSERT INTO marketplace_metadata_cache(marketplace,cache_key,payload,source_version,expires_at,updated_at,created_at)
        VALUES ('ebay','ebay:EBAY_US:0:134:456:aspects',:payload,'ebay_taxonomy_v1',:expires,:updated,:updated)
    """), {"payload": json.dumps(aspect_payload), "expires": datetime.now(UTC).replace(tzinfo=None) + timedelta(days=7), "updated": datetime.now(UTC).replace(tzinfo=None)})
    tree_payload = {"categoryTreeVersion": "134", "rootCategoryNode": {"category": {"categoryId": "0", "categoryName": "Root"},
        "childCategoryTreeNodes": [{"category": {"categoryId": "123", "categoryName": "Handbags"}, "childCategoryTreeNodes": [
            {"category": {"categoryId": "456", "categoryName": "Shoulder Bags"}, "leafCategoryTreeNode": True}]}]}}
    db_session.execute(text("""INSERT INTO marketplace_metadata_cache(marketplace,cache_key,payload,source_version,expires_at,updated_at,created_at)
        VALUES ('ebay','ebay:EBAY_US:category_tree',:payload,'ebay_taxonomy_tree_0_v134',:expires,:updated,:updated)"""),
        {"payload": json.dumps(tree_payload), "expires": datetime.now(UTC).replace(tzinfo=None) + timedelta(days=7), "updated": datetime.now(UTC).replace(tzinfo=None)})
    db_session.commit()

    cached = _ebay_aspect_cache(db_session)[("EBAY_US", "0", "134", "456")]
    assert cached["source_version"] == "ebay_taxonomy_v1"

    result = synchronize_local_taxonomies(db_session, ["ebay"])
    assert result["provider_requests"] == 0
    assert result["snapshots"][0]["categories"] == 3
    category = search_registry_categories(db_session, "ebay", query="Shoulder Bags")["items"][0]
    assert category["verification_status"] == "authoritatively_verified"
    assert category["terminal_status"] == "terminal"
    assert category["publishability_status"] == "publishable"
    assert bool(category["automated_selection_eligible"]) is True
    assert {row["localizedAspectName"] for row in category["required_attributes"]} == {"Exterior Material", "Exterior Color"}
    assert {row["localizedAspectName"] for row in category["recommended_attributes"]} == {"Style"}
    assert {row["localizedAspectName"] for row in category["optional_attributes"]} == {"Department"}
    assert {row["localizedAspectName"] for row in category["conditional_attributes"]} == {"Size"}
    state = taxonomy_registry_status(db_session)["marketplaces"][0]["versions"][0]
    assert state["schema_complete_count"] == 1
    assert state["schema_uncollected_count"] == 2
    assert state["schema_verified_empty_count"] == 0


def test_attribute_schema_state_distinguishes_unknown_empty_incomplete_and_stale():
    assert _attribute_schema_state({"source_evidence_json": "{}", "required_attributes_json": None}) == "uncollected"
    assert _attribute_schema_state({"source_evidence_json": '{"aspect_cache":{"staleness_status":"fresh"}}',
        "required_attributes_json": "[]", "optional_attributes_json": "[]",
        "recommended_attributes_json": "[]", "conditional_attributes_json": "[]"}) == "verified_empty"
    assert _attribute_schema_state({"source_evidence_json": '{"aspect_cache":{"staleness_status":"fresh"}}',
        "required_attributes_json": "[]", "optional_attributes_json": "[{\"name\":\"Style\"}]",
        "recommended_attributes_json": "[]", "conditional_attributes_json": "[]"}) == "complete"
    assert _attribute_schema_state({"source_evidence_json": '{"aspect_cache":{"staleness_status":"stale"}}',
        "required_attributes_json": "[]"}) == "stale"
    assert _attribute_schema_state({"source_evidence_json": '{"aspect_cache":{"staleness_status":"fresh"}}',
        "required_attributes_json": None}) == "incomplete"


def test_incremental_facebook_observation_cannot_downgrade_verified_category_or_schema(db_session):
    _registry_schema(db_session)
    version = {"version_id": "facebook:user-2:seller-form:v2", "marketplace": "facebook",
        "scope_key": "facebook:user-2", "native_version": "v2", "source": "seller_form",
        "evidence": {"batch": 1}, "verification_status": "live_browser_verified",
        "completeness_status": "partial", "staleness_status": "fresh"}
    base = {"version_id": version["version_id"], "marketplace": "facebook", "category_key": "same-key",
        "category_id": "native-7", "category_name": "Tools", "parent_key": None, "parent_id": None,
        "path": "Home > Tools", "path_json": '["Home","Tools"]', "depth": 2, "terminal": "terminal",
        "publishability": "unknown", "required": '[{"name":"Condition","required":true}]', "optional": "[]",
        "recommended": "[]", "conditional": "[]", "source": "seller_form",
        "evidence": json.dumps({"required_fields_schema_status": "verified_complete", "schema_evidence": {"complete": True, "submission_performed": False}, "batch": 1}),
        "verified": datetime.now(UTC).replace(tzinfo=None), "verification": "live_browser_verified", "staleness": "fresh", "eligible": False}
    _store_version(db_session, version, [dict(base)])
    partial = dict(base)
    partial.update({"category_id": None, "terminal": "unknown", "required": "[]", "evidence": json.dumps({
        "required_fields_schema_status": "incomplete", "schema_evidence": {"complete": False}, "batch": 2}),
        "verified": None, "verification": "live_browser_verified"})
    _store_version(db_session, {**version, "evidence": {"batch": 2}}, [partial])
    row = db_session.execute(text("""SELECT terminal_status,category_id,required_attributes_json,source_evidence_json
        FROM marketplace_taxonomy_categories WHERE version_id=:version AND category_key='same-key'"""),
        {"version": version["version_id"]}).mappings().one()
    evidence = json.loads(row["source_evidence_json"])
    assert row["terminal_status"] == "terminal"
    assert row["category_id"] == "native-7"
    assert json.loads(row["required_attributes_json"])[0]["name"] == "Condition"
    assert evidence["required_fields_schema_status"] == "verified_complete"
    assert len(evidence["observations"]) >= 2


def test_stale_ebay_snapshot_is_visible_but_not_eligible_for_automated_selection(db_session):
    _registry_schema(db_session)
    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31)
    _legacy_ebay_schema(db_session, old)
    db_session.execute(text("""CREATE TABLE IF NOT EXISTS marketplace_metadata_cache (
        id INTEGER PRIMARY KEY,marketplace TEXT,cache_key TEXT,payload TEXT,source_version TEXT,
        expires_at TIMESTAMP,updated_at TIMESTAMP,created_at TIMESTAMP)"""))
    db_session.execute(text("""INSERT INTO marketplace_metadata_cache
        (marketplace,cache_key,payload,source_version,expires_at,updated_at,created_at)
        VALUES ('ebay','ebay:EBAY_US:category_tree',:payload,'ebay_taxonomy_tree_0_v134',:expires,:updated,:updated)"""),
        {"payload": json.dumps({"categoryTreeVersion": "134"}), "expires": old, "updated": old})
    db_session.commit()

    synchronize_local_taxonomies(db_session, ["ebay"])

    status = taxonomy_registry_status(db_session)
    ebay = status["marketplaces"][0]
    assert ebay["coverage_status"] == "partial"
    assert all(version["staleness_status"] == "stale" for version in ebay["versions"])
    leaf = search_registry_categories(db_session, "ebay", query="Shoulder Bags")["items"][0]
    assert leaf["staleness_status"] == "stale"
    assert bool(leaf["automated_selection_eligible"]) is False


def test_partial_mercari_import_and_unobserved_facebook_are_never_upgraded(db_session):
    _registry_schema(db_session)
    db_session.connection().connection.executescript("""
        DROP TABLE IF EXISTS mercari_taxonomy_mappings;
        DROP TABLE IF EXISTS mercari_taxonomy_categories;
        DROP TABLE IF EXISTS mercari_taxonomy_versions;
    """)
    schema = Path(__file__).parents[1].joinpath("migrations/20261019_mercari_taxonomy.sql")
    db_session.connection().connection.executescript(schema.read_text())
    import_taxonomy(db_session, [{"category_path": ["Home", "Closet", "Hanging Organizers"], "category_id": "42"}],
                    source="user_supplied_fixture", version_id="fixture-partial-v1", source_complete=False)
    category_key = db_session.execute(text("SELECT category_key FROM mercari_taxonomy_categories WHERE version_id='fixture-partial-v1' LIMIT 1")).scalar_one()
    db_session.execute(text("""INSERT INTO mercari_taxonomy_mappings
        (mapping_key,mapping_label,version_id,category_key,approved_by,rationale)
        VALUES ('operator:closet-organizer','Closet organizer','fixture-partial-v1',:category,2,'operator-confirmed fixture')"""),
        {"category": category_key})

    result = synchronize_local_taxonomies(db_session, ["mercari", "facebook"])
    mapping = db_session.execute(text("SELECT mapping_label,rationale FROM mercari_taxonomy_mappings WHERE mapping_key='operator:closet-organizer'")).mappings().one()
    assert mapping["mapping_label"] == "Closet organizer"
    assert mapping["rationale"] == "operator-confirmed fixture"
    assert result["unavailable_marketplaces"] == ["facebook"]
    mercari_category = search_registry_categories(db_session, "mercari", query="Hanging Organizers")["items"][0]
    assert mercari_category["verification_status"] == "imported_unverified"
    assert mercari_category["terminal_status"] == "unknown"
    assert bool(mercari_category["automated_selection_eligible"]) is False
    status = taxonomy_registry_status(db_session)
    states = {item["marketplace"]: item["coverage_status"] for item in status["marketplaces"]}
    assert states == {"ebay": "unobserved", "mercari": "partial", "facebook": "unobserved"}


def test_facebook_live_taxonomy_observations_are_tenant_scoped_and_coverage_is_measured(db_session):
    _registry_schema(db_session)
    for user_id, label in ((2, "Fishing Reels User Two"), (3, "Fishing Reels User Three")):
        version_id = f"facebook:user-{user_id}:fixture"
        scope = f"facebook:account-user-{user_id}:ui-observed"
        db_session.execute(text("""INSERT INTO marketplace_taxonomy_versions
            (version_id,marketplace,scope_key,source,captured_at,verification_status,completeness_status,
             staleness_status,category_count,terminal_count,unresolved_count,is_current)
            VALUES (:id,'facebook',:scope,'fixture',:now,'live_browser_verified','partial','fresh',1,0,1,TRUE)"""),
            {"id": version_id, "scope": scope, "now": datetime.now(UTC).replace(tzinfo=None)})
        db_session.execute(text("""INSERT INTO marketplace_taxonomy_categories
            (version_id,marketplace,category_key,category_name,full_path,path_json,depth,terminal_status,
             publishability_status,required_attributes_json,data_source,verification_status,staleness_status)
            VALUES (:id,'facebook',:key,:name,:path,:json,2,'unknown','unknown',NULL,'fixture','live_browser_verified','fresh')"""),
            {"id": version_id, "key": f"user-{user_id}:fishing-reels", "name": label,
             "path": f"Sports > {label}", "json": json.dumps(["Sports", label])})
    db_session.commit()

    owner_two = taxonomy_registry_status(db_session, owner_user_id=2)
    facebook = next(row for row in owner_two["marketplaces"] if row["marketplace"] == "facebook")
    assert {version["scope_key"] for version in facebook["versions"]} == {"facebook:account-user-2:ui-observed"}
    version = facebook["versions"][0]
    assert version["discovered_category_count"] == 1
    assert version["verified_publishable_leaf_count"] == 0
    assert version["required_attribute_schema_count"] == 0
    assert facebook["confirmed_reusable_mapping_count"] == 0

    visible = search_registry_categories(db_session, "facebook", query="Fishing Reels", owner_user_id=2)
    assert [item["category_name"] for item in visible["items"]] == ["Fishing Reels User Two"]
    private = search_registry_categories(db_session, "facebook", query="Fishing Reels",
        version_id="facebook:user-3:fixture", owner_user_id=2)
    assert private["items"] == []
    candidates = rank_taxonomy_candidates(db_session, canonical_concept="Fishing Reels",
        marketplaces=["facebook"], owner_user_id=2)
    observed = candidates["marketplaces"][0]["candidates"]
    assert [item["category_name"] for item in observed] == ["Fishing Reels User Two"]
    assert observed[0]["publishability_status"] == "unknown"
    assert observed[0]["automated_selection_eligible"] is False


def test_registry_sync_is_idempotent_for_same_source_version(db_session):
    _registry_schema(db_session)
    _legacy_ebay_schema(db_session, datetime.now(UTC).replace(tzinfo=None))
    db_session.commit()

    synchronize_local_taxonomies(db_session, ["ebay"])
    synchronize_local_taxonomies(db_session, ["ebay"])

    versions = db_session.execute(text("SELECT COUNT(*) FROM marketplace_taxonomy_versions WHERE marketplace='ebay'" )).scalar_one()
    categories = db_session.execute(text("SELECT COUNT(*) FROM marketplace_taxonomy_categories WHERE marketplace='ebay'" )).scalar_one()
    assert versions == 1
    assert categories == 3
    row = db_session.execute(text("SELECT completeness_status,last_checked_at FROM marketplace_taxonomy_versions WHERE marketplace='ebay'")).mappings().one()
    assert row["completeness_status"] == "partial"
    assert row["last_checked_at"] is None


def test_repeated_local_sync_preserves_live_verification_evidence_and_checked_at(db_session):
    _registry_schema(db_session)
    _legacy_ebay_schema(db_session, datetime.now(UTC).replace(tzinfo=None))
    db_session.commit()
    synchronize_local_taxonomies(db_session, ["ebay"])
    old_checked = datetime(2026, 1, 2, 3, 4, 5)
    db_session.execute(text("UPDATE ebay_taxonomy_categories SET source='legacy_import' WHERE category_id='456'"))
    db_session.execute(text("""UPDATE marketplace_taxonomy_categories
        SET verification_status='live_browser_verified',last_verified_at=:verified,
            source_evidence_json=:evidence WHERE category_id='456'"""),
        {"verified": old_checked, "evidence": json.dumps({"diagnostic_id": 99, "verified": True})})
    db_session.execute(text("UPDATE marketplace_taxonomy_versions SET last_checked_at=:checked WHERE marketplace='ebay'"), {"checked": old_checked})
    db_session.commit()

    synchronize_local_taxonomies(db_session, ["ebay"])
    row = db_session.execute(text("""SELECT c.verification_status,c.last_verified_at,c.source_evidence_json,
        v.last_checked_at,v.completeness_status FROM marketplace_taxonomy_categories c
        JOIN marketplace_taxonomy_versions v USING(version_id) WHERE c.category_id='456'""")).mappings().one()
    evidence = json.loads(row["source_evidence_json"])
    assert row["verification_status"] == "live_browser_verified"
    assert str(row["last_verified_at"]) == "2026-01-02 03:04:05"
    assert str(row["last_checked_at"]) == "2026-01-02 03:04:05"
    assert row["completeness_status"] == "partial"
    assert any(item.get("diagnostic_id") == 99 for item in evidence["observations"])


def test_facebook_persisted_form_label_is_verified_but_not_promoted_to_leaf(db_session):
    _registry_schema(db_session)
    db_session.connection().connection.executescript("""
        DROP TABLE IF EXISTS marketplace_extension_jobs;
        CREATE TABLE marketplace_extension_jobs (
          id INTEGER PRIMARY KEY,user_id INTEGER,marketplace TEXT,action TEXT,status TEXT,
          completed_at TIMESTAMP,result TEXT,payload_snapshot TEXT
        );
    """)
    result = {"submission_performed": False, "page_url": "https://www.facebook.com/marketplace/create/item",
              "field_results": [{"field": "category", "filled": True, "verified_value": "Category Household",
                 "interaction_diagnostic": {"stage": "SELECTION_VERIFIED", "first_visible_options": ["Furniture", "Household", "Garden"]}}]}
    db_session.execute(text("""INSERT INTO marketplace_extension_jobs
      VALUES (164,2,'facebook','DIAGNOSTIC','COMPLETED',:completed,:result,:payload)"""),
      {"completed": datetime(2026, 10, 6), "result": json.dumps(result), "payload": json.dumps({})})
    db_session.commit()
    result = synchronize_local_taxonomies(db_session, ["facebook"])
    assert result["snapshots"][0]["completeness_status"] == "partial"
    category = search_registry_categories(db_session, "facebook", query="Household")["items"][0]
    assert category["verification_status"] == "live_browser_verified"
    assert category["terminal_status"] == "unknown"
    assert category["category_id"] is None
    assert not category["automated_selection_eligible"]
    assert "visible_options" not in category["source_evidence"]


def test_mercaris_failed_leaf_diagnostic_is_kept_as_non_verifying_evidence(db_session):
    _registry_schema(db_session)
    db_session.connection().connection.executescript("""
        DROP TABLE IF EXISTS mercari_taxonomy_mappings;
        DROP TABLE IF EXISTS mercari_taxonomy_categories;
        DROP TABLE IF EXISTS mercari_taxonomy_versions;
        DROP TABLE IF EXISTS marketplace_extension_jobs;
    """)
    schema = Path(__file__).parents[1].joinpath("migrations/20261019_mercari_taxonomy.sql")
    db_session.connection().connection.executescript(schema.read_text())
    db_session.connection().connection.executescript("""CREATE TABLE marketplace_extension_jobs (
        id INTEGER PRIMARY KEY,marketplace TEXT,action TEXT,status TEXT,completed_at TIMESTAMP,
        result TEXT,payload_snapshot TEXT,created_at TIMESTAMP)""")
    source_version = import_taxonomy(db_session, [{"category_path": ["Home", "Storage & Organization", "Hanging Closet Organizers"]}],
        source="partial_reference", version_id="storage-ref", source_complete=False)
    failed = {"submission_performed": False, "field_results": [{"field": "category", "filled": False,
        "error_code": "LEAF_SELECTION_REQUIRED", "interaction_diagnostic": {"stage": "LEAF_SELECTION_REQUIRED"}}]}
    db_session.execute(text("""INSERT INTO marketplace_extension_jobs VALUES
        (217,'mercari','DIAGNOSTIC','AWAITING_OPERATOR_REVIEW',:completed,:result,:payload,:created)"""),
        {"completed": datetime(2026, 10, 22), "created": datetime(2026, 10, 22),
         "result": json.dumps(failed), "payload": json.dumps({"marketplace_payload": {"category_path": ["Home", "Storage & Organization", "Hanging Closet Organizers"]}})})
    db_session.commit()
    synchronize_local_taxonomies(db_session, ["mercari"])
    category = search_registry_categories(db_session, "mercari", query="Hanging Closet Organizers")["items"][0]
    assert category["terminal_status"] == "unknown"
    assert category["verification_status"] == "imported_unverified"
    attempt = category["source_evidence"]["non_verifying_diagnostic_attempts"][0]
    assert attempt["diagnostic_job_id"] == 217
    assert attempt["error_code"] == "LEAF_SELECTION_REQUIRED"
    assert attempt["verified"] is False


def test_taxonomy_candidate_ranking_is_review_only_even_for_verified_leaf(db_session):
    _registry_schema(db_session)
    _legacy_ebay_schema(db_session, datetime.now(UTC).replace(tzinfo=None))
    db_session.commit()
    synchronize_local_taxonomies(db_session, ["ebay"])
    result = rank_taxonomy_candidates(db_session, canonical_concept="Shoulder bag", marketplaces=["ebay"])
    destination = result["marketplaces"][0]
    assert destination["candidates"]
    candidate = destination["candidates"][0]
    assert candidate["category_id"] == "456"
    assert candidate["terminal_status"] == "terminal"
    assert candidate["mapping_confidence"] <= 0.35
    assert candidate["automatic_selection_permitted"] is False
    assert candidate["operator_review_required"] is True
    assert result["mapping_state"] == "candidate_generation_only"
