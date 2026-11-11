import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services.mercari_taxonomy import (
    APIFY_SOURCE, approve_mapping, export_categories, import_taxonomy,
    normalize_apify_rows, resolve_category, search_categories, taxonomy_status, verify_category_from_diagnostic,
    record_observed_path_from_diagnostic,
)


def _schema(db_session):
    sql = Path(__file__).parents[1].joinpath("migrations/20261019_mercari_taxonomy.sql").read_text()
    db_session.connection().connection.executescript(sql)


def test_actor_export_expands_hierarchy_and_keeps_id_namespace_unverified(db_session):
    _schema(db_session)
    rows = [{
        "mainCategoryNameL1": "Women", "mainCategoryIdL1": "1", "mainCategoryUrlL1": "https://www.mercari.com/us/category/women-1/",
        "subCategoryNameL2": "Tops & blouses", "subCategoryIdL2": "12", "subCategoryUrlL2": "https://www.mercari.com/us/category/tops-12/",
        "subCategoryNameL3": "Blouses", "subCategoryIdL3": "120", "subCategoryUrlL3": "https://www.mercari.com/us/category/blouses-120/",
    }, {
        "mainCategoryNameL1": "Women", "mainCategoryIdL1": "1", "mainCategoryUrlL1": "https://www.mercari.com/us/category/women-1/",
        "subCategoryNameL2": "Tops & blouses", "subCategoryIdL2": "12", "subCategoryUrlL2": "https://www.mercari.com/us/category/tops-12/",
        "subCategoryNameL3": "T-shirts", "subCategoryIdL3": "121", "subCategoryUrlL3": "https://www.mercari.com/us/category/tshirts-121/",
    }]
    result = import_taxonomy(db_session, rows, version_id="fixture-v1", source_complete=True)
    assert result["node_count"] == 4
    assert result["leaf_count"] == 2
    children = search_categories(db_session, "Tops", leaves_only=True)
    assert {row["full_path"] for row in children} == {"Women > Tops & blouses > Blouses", "Women > Tops & blouses > T-shirts"}
    assert all(row["source_namespace"] == "apify_mercari_category_url" for row in children)
    assert all(row["verification_status"] == "unverified" for row in children)
    exported = json.loads(export_categories(db_session, fmt="json"))
    assert exported["version_id"] == "fixture-v1" and len(exported["categories"]) == 4
    assert "full_path" in export_categories(db_session, fmt="csv")


def test_matching_suggests_only_and_mapping_requires_verified_leaf(db_session):
    _schema(db_session)
    import_taxonomy(db_session, [{"category_path": ["Women", "Tops & blouses", "Blouses"], "category_id": "A-1"}], version_id="v1", source_complete=True)
    suggestion = resolve_category(db_session, query="women blouse")
    assert suggestion["status"] == "review_required"
    assert suggestion["suggestions"][0]["selection_requires_operator_approval"] is True
    candidate = suggestion["suggestions"][0]
    with pytest.raises(ValueError, match="verified terminal"):
        approve_mapping(db_session, "womens_blouse", "Women's blouse", "v1", candidate["category_key"], 1)
    diagnostic = SimpleNamespace(id=77, marketplace="mercari", action="DIAGNOSTIC", status="BLOCKED_EXTERNAL",
                                 result={"submission_performed": False, "field_results": [{"field": "category", "filled": True, "verified_value": "Women > Tops & blouses > Blouses"}]},
                                 payload_snapshot={"marketplace_payload": {"category_path": ["Women", "Tops & blouses", "Blouses"]}})
    verified = verify_category_from_diagnostic(db_session, version_id="v1", category_key=candidate["category_key"], job=diagnostic)
    assert verified["status"] == "path_verified"
    approve_mapping(db_session, "womens_blouse", "Women's blouse", "v1", candidate["category_key"], 1, "verified in diagnostic-only form")
    resolved = resolve_category(db_session, mapping_key="womens_blouse", query="something unrelated")
    assert resolved["status"] == "approved_mapping"
    assert resolved["match"]["full_path"] == "Women > Tops & blouses > Blouses"


def test_live_non_submitting_diagnostic_promotes_only_exact_unknown_terminal(db_session):
    _schema(db_session)
    import_taxonomy(db_session, [{"category_path": ["Women", "Tops & blouses", "T-shirts"]}],
                    version_id="partial-live-v1", source_complete=False)
    candidate = search_categories(db_session, "T-shirts", version_id="partial-live-v1")[0]
    assert candidate["leaf_status"] == "unknown"
    diagnostic = SimpleNamespace(id=199, marketplace="mercari", action="DIAGNOSTIC", status="COMPLETED",
        result={"submission_performed": False, "field_results": [{"field": "category", "filled": True,
                 "verified_value": "Women > Tops & blouses > T-shirts", "interaction_diagnostic": {
                     "persistence": {"verified": True}, "destination_category_id": "observed-form-id-7"}}]},
        payload_snapshot={"marketplace_payload": {"category_path": ["Women", "Tops & blouses", "T-shirts"]}})
    verified = verify_category_from_diagnostic(db_session, version_id="partial-live-v1",
                                                category_key=candidate["category_key"], job=diagnostic)
    refreshed = search_categories(db_session, "T-shirts", version_id="partial-live-v1")[0]
    assert verified["status"] == "path_verified"
    assert refreshed["is_leaf"] is True and refreshed["leaf_status"] == "confirmed_leaf"
    assert refreshed["verification_status"] == "path_verified"


@pytest.mark.parametrize("result,verified_value", [
    ({"submission_performed": True, "field_results": [{"field": "category", "filled": True, "verified_value": "Women > Tops & blouses > T-shirts"}]}, "Women > Tops & blouses > T-shirts"),
    ({"submission_performed": False, "field_results": [{"field": "category", "filled": True, "verified_value": "Women > Tops & blouses"}]}, "Women > Tops & blouses"),
])
def test_unknown_path_is_not_promoted_without_no_submit_exact_persistence(db_session, result, verified_value):
    _schema(db_session)
    import_taxonomy(db_session, [{"category_path": ["Women", "Tops & blouses", "T-shirts"]}],
                    version_id="partial-reject-v1", source_complete=False)
    candidate = search_categories(db_session, "T-shirts", version_id="partial-reject-v1")[0]
    result = dict(result)
    result["field_results"] = [{"field": "category", "filled": True, "verified_value": verified_value}]
    diagnostic = SimpleNamespace(id=200, marketplace="mercari", action="DIAGNOSTIC", status="COMPLETED",
        result=result, payload_snapshot={"marketplace_payload": {"category_path": ["Women", "Tops & blouses", "T-shirts"]}})
    with pytest.raises(ValueError):
        verify_category_from_diagnostic(db_session, version_id="partial-reject-v1",
                                        category_key=candidate["category_key"], job=diagnostic)
    unchanged = search_categories(db_session, "T-shirts", version_id="partial-reject-v1")[0]
    assert unchanged["leaf_status"] == "unknown" and unchanged["verification_status"] == "unverified"


def test_observed_live_path_is_added_only_after_exact_non_submitting_persistence(db_session):
    _schema(db_session)
    import_taxonomy(db_session, [{"category_path": ["Women", "Tops & Blouses", "Basic Tees"]}],
                    version_id="partial-observed-v1", source_complete=False)
    path = ["Women", "Tops & blouses", "T-shirts"]
    job = SimpleNamespace(id=201, marketplace="mercari", action="DIAGNOSTIC", status="COMPLETED",
        result={"submission_performed": False, "field_results": [{"field": "category", "filled": True,
                 "verified_value": "Women > Tops & blouses > T-shirts", "interaction_diagnostic": {
                     "persistence": {"verified": True}, "destination_category_id": "observed-form-id-7"}}]},
        payload_snapshot={"marketplace_payload": {"category_path": path}})
    outcome = record_observed_path_from_diagnostic(db_session, version_id="partial-observed-v1", category_path=path, job=job)
    assert outcome["status"] == "path_verified"
    match = search_categories(db_session, "T-shirts", version_id="partial-observed-v1")
    live = next(row for row in match if row["source_namespace"] == "mercari_us_live_form_observed")
    assert live["full_path"] == "Women > Tops & blouses > T-shirts"
    assert live["leaf_status"] == "confirmed_leaf" and live["verification_status"] == "path_verified"
    assert live["metadata"]["validation_method"] == "authenticated_local_extension_live_form_diagnostic"
    assert live["metadata"]["form_selection_persisted"] is True
    assert live["metadata"]["observed_destination_category_id"] == "observed-form-id-7"
    assert live["metadata"]["source_category_id_equivalence"] == "unverified"


def test_taxonomy_status_counts_live_observations_without_claiming_imported_tree_complete(db_session):
    _schema(db_session)
    import_taxonomy(db_session, [{"category_path": ["Women", "Tops & blouses", "T-shirts"]}],
                    version_id="partial-status-v1", source_complete=False)
    candidate = search_categories(db_session, "T-shirts", version_id="partial-status-v1")[0]
    job = SimpleNamespace(id=221, marketplace="mercari", action="DIAGNOSTIC", status="COMPLETED",
        completed_at=None,
        result={"submission_performed": False, "field_results": [{"field": "category", "filled": True,
                 "verified_value": "Women > Tops & blouses > T-shirts", "interaction_diagnostic": {
                     "persistence": {"verified": True}}}]},
        payload_snapshot={"marketplace_payload": {"category_path": ["Women", "Tops & blouses", "T-shirts"]}})
    record_observed_path_from_diagnostic(db_session, version_id="partial-status-v1",
                                         category_path=["Women", "Tops & blouses", "T-shirts"], job=job)
    status = taxonomy_status(db_session)
    assert status["latest"]["node_count"] == 6
    assert status["latest"]["source_node_count"] == 3
    assert status["latest"]["leaf_count"] == 1
    assert status["latest"]["verified_leaf_count"] == 1
    assert status["coverage_status"] == "partial"


def test_apify_hierarchy_deduplicates_shared_parents():
    rows = [{"mainCategoryNameL1": "Women", "mainCategoryIdL1": "1", "subCategoryNameL2": "Tops", "subCategoryIdL2": "12", "subCategoryNameL3": "Blouses", "subCategoryIdL3": "120"},
            {"mainCategoryNameL1": "Women", "mainCategoryIdL1": "1", "subCategoryNameL2": "Tops", "subCategoryIdL2": "12", "subCategoryNameL3": "Shirts", "subCategoryIdL3": "121"}]
    nodes = normalize_apify_rows(rows, source_complete=True)
    assert len(nodes) == 4
    assert len({n["category_key"] for n in nodes}) == len(nodes)
    assert sum(n["is_leaf"] for n in nodes) == 2


def test_partial_actor_export_does_not_invent_leaf_classification(db_session):
    _schema(db_session)
    rows = [{"mainCategoryNameL1": "Women", "mainCategoryIdL1": "1",
             "subCategoryNameL2": "Tops & blouses", "subCategoryIdL2": "12"}]
    result = import_taxonomy(db_session, rows, version_id="partial-v1", source_complete=False)
    assert result["leaf_count"] == 0
    assert result["source_completeness"] == "partial"
    assert search_categories(db_session, "Tops", leaves_only=True) == []
    statuses = {row["category_name"]: row["leaf_status"] for row in search_categories(db_session, "")}
    assert statuses == {"Women": "branch", "Tops & blouses": "unknown"}


def test_child_first_json_rebuilds_parent_relations_and_paths(db_session):
    _schema(db_session)
    rows = [
        {"category_id": "leaf", "parent_category_id": "branch", "name": "Blouses"},
        {"category_id": "branch", "parent_category_id": "root", "name": "Tops"},
        {"category_id": "root", "name": "Women"},
    ]
    result = import_taxonomy(db_session, rows, source="official_template:fixture", version_id="ordered-v1", source_complete=True)
    assert result["node_count"] == 3
    leaf = search_categories(db_session, "Blouses")[0]
    assert leaf["full_path"] == "Women > Tops > Blouses"
    assert leaf["parent_source_category_id"] == "branch"
    assert result["root_count"] == 1 and result["max_depth"] == 3


def test_official_taxonomy_schema_import_preserves_fields_without_claiming_verification(db_session):
    _schema(db_session)
    rows = [{"category_id": "leaf-7", "parent_category_id": "root-1", "name": "Tools",
        "path": ["Home", "Tools"], "is_leaf": True,
        "required_fields": '[{"name":"Brand","required":true}]',
        "conditional_attributes": [{"name":"Material","when":{"category":"Tools"}}],
        "allowed_values": {"Material": ["Steel", "Plastic"]},
        "shipping_restrictions": {"parcel_only": True}, "schema_complete": True}]
    result = import_taxonomy(db_session, rows, source="mercari_official_export", version_id="official-schema-v1", source_complete=True)
    item = search_categories(db_session, "Tools", version_id=result["version_id"])[0]
    schema = item["metadata"]["seller_schema"]
    assert schema["required_fields"][0]["name"] == "Brand"
    assert schema["conditional_fields"][0]["name"] == "Material"
    assert schema["allowed_values"]["Material"] == ["Steel", "Plastic"]
    assert schema["shipping_eligibility"]["parcel_only"] is True
    assert schema["schema_complete"] is True
    assert schema["status"] == "imported_unverified"
    assert schema["seller_form_equivalence"] == "unverified"


def test_unknown_parent_is_reported_and_does_not_become_root_or_leaf(db_session):
    _schema(db_session)
    result = import_taxonomy(db_session, [{"category_id": "orphan", "parent_category_id": "missing", "name": "Orphan"}],
                             source="public_fixture", version_id="orphan-v1", source_complete=True)
    assert result["unresolved_branches"] == 1
    assert result["root_count"] == 0
    assert result["leaf_count"] == 0
    assert search_categories(db_session, "Orphan")[0]["leaf_status"] == "unknown"


def test_json_and_csv_round_trip_preserve_hierarchy_leaf_status_and_all_rows(db_session):
    _schema(db_session)
    rows = [
        {"category_id": "root", "name": "Home", "is_leaf": False},
        {"category_id": "branch", "parent_category_id": "root", "name": "Kitchen", "is_leaf": False},
        {"category_id": "leaf", "parent_category_id": "branch", "name": "Cookware", "is_leaf": True},
    ]
    import_taxonomy(db_session, rows, source="public_reference", version_id="roundtrip-v1")
    json_data = export_categories(db_session, version_id="roundtrip-v1", fmt="json")
    csv_data = export_categories(db_session, version_id="roundtrip-v1", fmt="csv")
    json_result = import_taxonomy(db_session, json_data, source="roundtrip-json", version_id="roundtrip-v2")
    csv_result = import_taxonomy(db_session, csv_data, source="roundtrip-csv", version_id="roundtrip-v3")
    assert json_result["node_count"] == csv_result["node_count"] == 3
    assert search_categories(db_session, "Cookware", version_id="roundtrip-v2")[0]["full_path"] == "Home > Kitchen > Cookware"
    assert search_categories(db_session, "Cookware", version_id="roundtrip-v3")[0]["leaf_status"] == "confirmed_leaf"
