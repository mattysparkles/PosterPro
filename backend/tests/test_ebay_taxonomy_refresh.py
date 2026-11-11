import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, User
from app.services import ebay_taxonomy_refresh as refresh
from app.services.ebay_service import EbayIntegrationError


def _setup_refresh_schema(db_session):
    raw = db_session.connection().connection
    raw.executescript("""
      DROP TABLE IF EXISTS marketplace_taxonomy_categories;
      DROP TABLE IF EXISTS marketplace_taxonomy_versions;
      DROP TABLE IF EXISTS marketplace_taxonomy_expired_categories;
      DROP TABLE IF EXISTS ebay_taxonomy_categories;
    """)
    raw.executescript((Path(__file__).parents[1] / "migrations/20261023_marketplace_taxonomy_registry.sql").read_text())
    raw.executescript((Path(__file__).parents[1] / "migrations/20261024_ebay_taxonomy_expired_categories.sql").read_text())
    db_session.execute(text("""CREATE TABLE ebay_taxonomy_categories (
      marketplace TEXT,category_tree_id TEXT,category_tree_version TEXT,category_id TEXT,category_name TEXT,
      parent_category_id TEXT,leaf BOOLEAN,full_path TEXT,depth INTEGER,last_synced_at TIMESTAMP,source TEXT,
      PRIMARY KEY(marketplace,category_tree_id,category_id))"""))


def test_flatten_tree_uses_provider_leaf_flags_and_real_parent_links():
    tree = {"categoryTreeId": "0", "categoryTreeVersion": "v1", "rootCategoryNode": {
        "category": {"categoryId": "0", "categoryName": "Root"},
        "childCategoryTreeNodes": [{"category": {"categoryId": "10", "categoryName": "Bags"},
            "childCategoryTreeNodes": [{"category": {"categoryId": "11", "categoryName": "Handbags"}, "leafCategoryTreeNode": True}]}]}}
    rows = refresh._flatten_tree(tree["rootCategoryNode"], "EBAY_US", "0", "v1")
    assert len(rows) == 3
    assert rows[1]["leaf"] is False
    assert rows[2]["leaf"] is True
    assert rows[2]["parent_id"] == "10"
    assert rows[2]["full_path"] == "Root > Bags > Handbags"
    with pytest.raises(EbayIntegrationError, match="omitted terminal-leaf"):
        refresh._flatten_tree({"category": {"categoryId": "5", "categoryName": "No metadata"}}, "EBAY_US", "0", "v1")


def test_refresh_records_provider_version_counts_and_expired_category_mapping(db_session, monkeypatch):
    _setup_refresh_schema(db_session)
    user = User(email="taxonomy-refresh@example.com", is_admin=True)
    db_session.add(user); db_session.flush()
    db_session.add(MarketplaceAccount(user_id=user.id, marketplace=MarketplaceName.ebay,
        external_account_id="connected-ebay", access_token="token", refresh_token="refresh"))
    db_session.commit()
    provider = {"version": "134"}
    payload = {"categoryTreeId": "0", "categoryTreeVersion": "134", "rootCategoryNode": {
        "category": {"categoryId": "0", "categoryName": "Root"}, "childCategoryTreeNodes": [
            {"category": {"categoryId": "169291", "categoryName": "Women’s Bags & Handbags"}, "leafCategoryTreeNode": True}]}}

    async def fake_request(_client, method, path, params=None, payload=None, headers=None):
        if path.endswith("get_default_category_tree_id"):
            return {"categoryTreeId": "0", "categoryTreeVersion": provider["version"]}
        if path.endswith("get_expired_categories"):
            return {"expiredCategories": ([{"fromCategoryId": "123", "toCategoryId": "169291"}] if provider["version"] == "134"
                                           else [{"fromCategoryId": "169291", "toCategoryId": "170000"}])}
        if path == "/commerce/taxonomy/v1/category_tree/0":
            return payload_for_tree
        raise AssertionError(path)

    payload_for_tree = payload
    monkeypatch.setattr(refresh.EbayAPIClient, "request", fake_request)
    result = asyncio.run(refresh.refresh_ebay_taxonomies(db_session, user_id=user.id,
        marketplaces=["EBAY_US"], refresh_aspects=False))
    assert result["trees"][0]["tree_id"] == "0"
    assert result["trees"][0]["tree_version"] == "134"
    assert result["trees"][0]["category_count"] == 2
    assert result["trees"][0]["terminal_count"] == 1
    assert result["trees"][0]["expired_category_mappings"] == 1
    assert result["registry"]["snapshots"][0]["staleness_status"] == "fresh"
    stored = db_session.execute(text("SELECT replacement_category_id FROM marketplace_taxonomy_expired_categories WHERE expired_category_id='123'")).scalar_one()
    assert stored == "169291"
    category = db_session.execute(text("SELECT parent_category_id,leaf,source FROM ebay_taxonomy_categories WHERE category_id='169291'")).mappings().one()
    assert category["parent_category_id"] == "0"
    assert bool(category["leaf"]) is True
    assert category["source"] == "official_taxonomy_api"

    provider["version"] = "135"
    payload_for_tree = {"categoryTreeId": "0", "categoryTreeVersion": "135", "rootCategoryNode": {
        "category": {"categoryId": "0", "categoryName": "Root"}, "childCategoryTreeNodes": [
            {"category": {"categoryId": "170000", "categoryName": "Updated Handbags"}, "leafCategoryTreeNode": True}]}}
    drift = asyncio.run(refresh.refresh_ebay_taxonomies(db_session, user_id=user.id,
        marketplaces=["EBAY_US"], refresh_aspects=False))
    assert drift["trees"][0]["tree_version"] == "135"
    versions = db_session.execute(text("SELECT native_version,is_current FROM marketplace_taxonomy_versions WHERE marketplace='ebay' AND scope_key='EBAY_US:0' ORDER BY native_version")).mappings().all()
    assert [(row["native_version"], bool(row["is_current"])) for row in versions] == [("134", False), ("135", True)]
    deprecated = db_session.execute(text("""SELECT verification_status,publishability_status,source_evidence_json
        FROM marketplace_taxonomy_categories WHERE version_id='ebay:EBAY_US:0:134' AND category_id='169291'""")).mappings().one()
    assert deprecated["verification_status"] == "deprecated"
    assert deprecated["publishability_status"] == "not_publishable"
    assert '"replacement_category_id":"170000"' in deprecated["source_evidence_json"]
