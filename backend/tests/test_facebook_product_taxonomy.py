from pathlib import Path

import pytest
from sqlalchemy import text

from app.services.facebook_product_taxonomy import import_meta_product_categories, parse_meta_product_categories
from app.services.marketplace_taxonomy_registry import search_registry_categories, taxonomy_registry_status
from app.services import canonical_taxonomy_mapping


def _registry_schema(db_session):
    db_session.connection().connection.executescript("""
      DROP TABLE IF EXISTS marketplace_taxonomy_categories;
      DROP TABLE IF EXISTS marketplace_taxonomy_versions;
    """)
    path = Path(__file__).parents[1].joinpath("migrations/20261023_marketplace_taxonomy_registry.sql")
    db_session.connection().connection.executescript(path.read_text())


def test_official_meta_catalog_taxonomy_parser_builds_parent_and_leaf_nodes():
    rows, digest = parse_meta_product_categories(
        b"\xef\xbb\xbfcategory_id,category\n1,Home\n2,Home > Kitchen\n3,Home > Kitchen > Cookware\n4,Electronics\n"
    )
    assert len(digest) == 64
    paths = {tuple(row["path"]): row for row in rows}
    assert paths[("Home",)]["terminal"] is False
    assert paths[("Home", "Kitchen")]["terminal"] is False
    assert paths[("Home", "Kitchen", "Cookware")]["category_id"] == "3"
    assert paths[("Home", "Kitchen", "Cookware")]["terminal"] is True
    assert paths[("Electronics",)]["category_id"] == "4"


def test_imported_meta_taxonomy_is_reference_only_and_never_marketplace_publishable(db_session):
    _registry_schema(db_session)
    csv_data = "category_id,category\n1,Home\n2,Home > Kitchen\n3,Home > Kitchen > Cookware\n4,Electronics\n"
    result = import_meta_product_categories(db_session, csv_data)
    assert result["category_count"] == 4
    assert result["terminal_count"] == 2
    assert result["marketplace_publishable"] is False

    item = search_registry_categories(db_session, "facebook", query="Cookware", version_id=result["version_id"])["items"][0]
    assert item["category_id"] == "3"
    assert item["terminal_status"] == "terminal"
    assert item["publishability_status"] == "reference_only"
    assert not item["automated_selection_eligible"]
    assert item["required_attributes"] is None
    assert item["source_evidence"]["marketplace_form_category_equivalence"] == "unverified"
    facebook = next(item for item in taxonomy_registry_status(db_session)["marketplaces"] if item["marketplace"] == "facebook")
    assert facebook["coverage_status"] == "partial"


def test_meta_catalog_import_is_idempotent_for_identical_official_source(db_session):
    _registry_schema(db_session)
    data = "category_id,category\n1,Home\n2,Home > Kitchen\n"
    first = import_meta_product_categories(db_session, data)
    second = import_meta_product_categories(db_session, data)
    assert first["version_id"] == second["version_id"]
    assert db_session.execute(text("SELECT COUNT(*) FROM marketplace_taxonomy_versions")).scalar_one() == 1
    assert db_session.execute(text("SELECT COUNT(*) FROM marketplace_taxonomy_categories")).scalar_one() == 2


def test_meta_catalog_leaf_cannot_be_confirmed_as_marketplace_mapping(db_session):
    _registry_schema(db_session)
    from pathlib import Path
    mapping_migration = Path(__file__).parents[1] / "migrations/20261024_canonical_taxonomy_mappings.sql"
    db_session.connection().connection.executescript(mapping_migration.read_text())
    result = import_meta_product_categories(db_session, "category_id,category\n1,Home\n2,Home > Kitchen\n")
    concept = canonical_taxonomy_mapping.create_or_update_concept(db_session, owner_user_id=2, concept_name="Kitchen item")
    category = search_registry_categories(db_session, "facebook", query="Kitchen", version_id=result["version_id"])["items"][-1]
    with pytest.raises(ValueError, match="verified terminal leaf"):
        canonical_taxonomy_mapping.confirm_category_mapping(db_session, owner_user_id=2,
            concept_id=concept["concept_id"], marketplace="facebook", version_id=result["version_id"],
            category_key=category["category_key"], rationale="Commerce catalog category only; not Marketplace form evidence.")
    assert db_session.execute(text("SELECT COUNT(*) FROM marketplace_taxonomy_mappings")).scalar_one() == 0


def test_duplicate_meta_path_preserves_all_source_ids_without_claiming_one():
    rows, _ = parse_meta_product_categories("category_id,category\n1,Home\n2,Home\n")
    assert len(rows) == 1
    assert rows[0]["category_id"] is None
    assert rows[0]["source_category_ids"] == ["1", "2"]
