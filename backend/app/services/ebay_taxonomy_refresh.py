"""Bounded, authenticated refresh of PosterPro's supported eBay taxonomy trees."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.models.models import MarketplaceAccount, MarketplaceMetadataCache
from app.services.ebay_service import (
    EbayAPIClient,
    EbayIntegrationError,
    get_known_ebay_category_tree,
    get_known_ebay_us_category_tree,
    get_or_refresh_account,
    get_required_item_specifics,
)
from app.services.marketplace_taxonomy_registry import _json, _load_json, synchronize_local_taxonomies


SUPPORTED_TREES = {"EBAY_US": "0", "EBAY_MOTORS_US": "100"}
MAX_ASPECT_CATEGORIES = 50


def _flatten_tree(node: dict[str, Any], marketplace: str, tree_id: str, tree_version: str,
                  parent_id: str | None = None, path: tuple[str, ...] = (), depth: int = 0) -> list[dict[str, Any]]:
    category = node.get("category") if isinstance(node.get("category"), dict) else {}
    category_id = str(category.get("categoryId") or "").strip()
    name = str(category.get("categoryName") or "").strip()
    if not category_id or not name:
        raise EbayIntegrationError("eBay taxonomy response contains a category node without an ID or name")
    current_path = path + (name,)
    children = node.get("childCategoryTreeNodes") if isinstance(node.get("childCategoryTreeNodes"), list) else []
    leaf_flag = node.get("leafCategoryTreeNode")
    if leaf_flag is True:
        leaf = True
    elif children or leaf_flag is False:
        leaf = False
    else:
        raise EbayIntegrationError("eBay taxonomy response omitted terminal-leaf metadata for a node without children")
    rows = [{
        "marketplace": marketplace, "tree_id": tree_id, "tree_version": tree_version,
        "category_id": category_id, "category_name": name, "parent_id": parent_id,
        "leaf": leaf, "full_path": " > ".join(current_path),
        "depth": int(node.get("categoryTreeNodeLevel", depth) or depth),
    }]
    for child in children:
        if not isinstance(child, dict):
            raise EbayIntegrationError("eBay taxonomy response contains an invalid child node")
        rows.extend(_flatten_tree(child, marketplace, tree_id, tree_version, category_id, current_path, depth + 1))
    if children and leaf:
        # Provider leaf flags are authoritative; retain them but report any
        # structural inconsistency instead of silently dropping descendants.
        rows[0]["provider_leaf_conflict"] = True
    return rows


async def refresh_ebay_taxonomies(db: Session, *, user_id: int, marketplaces: list[str] | None = None,
                                  refresh_aspects: bool = True) -> dict[str, Any]:
    selected = list(dict.fromkeys(str(value).upper() for value in (marketplaces or list(SUPPORTED_TREES))))
    if not selected or any(value not in SUPPORTED_TREES for value in selected):
        raise ValueError("Only EBAY_US and EBAY_MOTORS_US taxonomy trees are supported")
    account: MarketplaceAccount = await get_or_refresh_account(user_id, db)
    client = EbayAPIClient(account.access_token)
    now = datetime.utcnow()
    output = []
    for marketplace in selected:
        tree_id_expected = SUPPORTED_TREES[marketplace]
        default_tree = await client.request("GET", "/commerce/taxonomy/v1/get_default_category_tree_id",
                                            params={"marketplace_id": marketplace})
        tree_id = str(default_tree.get("categoryTreeId") or "")
        provider_version = str(default_tree.get("categoryTreeVersion") or "")
        if tree_id != tree_id_expected or not provider_version:
            raise EbayIntegrationError(f"eBay default tree lookup returned an unexpected {marketplace} tree")
        cache_key = f"ebay:{marketplace}:category_tree"
        tree_cache = db.query(MarketplaceMetadataCache).filter(MarketplaceMetadataCache.cache_key == cache_key).first()
        cached_payload = tree_cache.payload if tree_cache and isinstance(tree_cache.payload, dict) else {}
        cache_fresh = bool(tree_cache and tree_cache.expires_at and tree_cache.expires_at > now
                           and str(cached_payload.get("categoryTreeVersion") or "") == provider_version
                           and isinstance(cached_payload.get("rootCategoryNode"), dict))
        if cache_fresh:
            # Copy nested JSON values so SQLAlchemy notices the version check
            # timestamp change on this non-mutable JSON column.
            payload = dict(cached_payload)
            evidence = dict(payload.get("_posterpro_taxonomy_evidence") if isinstance(payload.get("_posterpro_taxonomy_evidence"), dict) else {})
            evidence["version_checked_at"] = now.isoformat() + "Z"
            evidence.setdefault("tree_retrieved_at", tree_cache.updated_at.isoformat() + "Z" if tree_cache.updated_at else None)
            payload["_posterpro_taxonomy_evidence"] = evidence
            tree_cache.payload = payload
            tree_cache.expires_at = now + timedelta(days=30)
            db.add(tree_cache)
        elif marketplace == "EBAY_US":
            payload = await get_known_ebay_us_category_tree(account.access_token)
        else:
            payload = await get_known_ebay_category_tree(account.access_token, tree_id_expected, marketplace_id=marketplace)
        tree_version = str(payload.get("categoryTreeVersion") or "")
        root = payload.get("rootCategoryNode")
        if tree_id != tree_id_expected or tree_version != provider_version or not isinstance(root, dict):
            raise EbayIntegrationError(f"eBay returned an unexpected {marketplace} taxonomy tree identity or shape")
        if not cache_fresh:
            payload["_posterpro_taxonomy_evidence"] = {"tree_retrieved_at": now.isoformat() + "Z",
                                                       "version_checked_at": now.isoformat() + "Z",
                                                       "provider": "eBay Taxonomy API"}
        categories = _flatten_tree(root, marketplace, tree_id, tree_version)
        # A full-tree response has an actual root and internally consistent
        # parent links. We do not use the retrieval time to infer completeness.
        ids = {row["category_id"] for row in categories}
        if len(ids) != len(categories) or not categories or any(row["parent_id"] and row["parent_id"] not in ids for row in categories):
            raise EbayIntegrationError(f"eBay {marketplace} tree failed duplicate-ID or parent-child validation")

        recent_expiration_check = db.execute(text("""SELECT MAX(checked_at) FROM marketplace_taxonomy_expired_categories
            WHERE marketplace_id=:marketplace AND category_tree_id=:tree AND current_tree_version=:version"""),
            {"marketplace": marketplace, "tree": tree_id, "version": tree_version}).scalar()
        expiration_fresh = bool(recent_expiration_check and recent_expiration_check >= now - timedelta(days=30))
        if expiration_fresh:
            expired_count = db.execute(text("""SELECT COUNT(*) FROM marketplace_taxonomy_expired_categories
                WHERE marketplace_id=:marketplace AND category_tree_id=:tree AND current_tree_version=:version"""),
                {"marketplace": marketplace, "tree": tree_id, "version": tree_version}).scalar_one()
            expired = []
        else:
            expired_payload = await client.request(
                "GET", f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_expired_categories",
                headers={"X-Marketplace-Id": marketplace},
            )
            expired = expired_payload.get("expiredCategories") if isinstance(expired_payload, dict) else None
            if not isinstance(expired, list):
                raise EbayIntegrationError(f"eBay expired-category response for {marketplace} was malformed")
            db.execute(text("""INSERT INTO marketplace_taxonomy_expired_categories
          (marketplace_id,category_tree_id,current_tree_version,expired_category_id,replacement_category_id,checked_at,source)
          VALUES (:marketplace,:tree,:version,:expired,:replacement,:checked,'eBay Taxonomy API getExpiredCategories')
          ON CONFLICT(marketplace_id,category_tree_id,current_tree_version,expired_category_id) DO UPDATE SET
            replacement_category_id=excluded.replacement_category_id,checked_at=excluded.checked_at,source=excluded.source"""), [
            {"marketplace": marketplace, "tree": tree_id, "version": tree_version,
             "expired": str(item.get("fromCategoryId") or ""),
             "replacement": str(item.get("toCategoryId") or "") or None, "checked": now}
                for item in expired if isinstance(item, dict) and item.get("fromCategoryId")
            ])
            expired_count = len(expired)

        db.execute(text("""INSERT INTO ebay_taxonomy_categories
          (marketplace,category_tree_id,category_tree_version,category_id,category_name,parent_category_id,
           leaf,full_path,depth,last_synced_at,source)
          VALUES (:marketplace,:tree_id,:tree_version,:category_id,:category_name,:parent_id,:leaf,:full_path,:depth,:checked,'official_taxonomy_api')
          ON CONFLICT(marketplace,category_tree_id,category_id) DO UPDATE SET
           category_tree_version=excluded.category_tree_version,category_name=excluded.category_name,
           parent_category_id=excluded.parent_category_id,leaf=excluded.leaf,full_path=excluded.full_path,
           depth=excluded.depth,last_synced_at=excluded.last_synced_at,source=excluded.source"""), [
            {**row, "checked": now} for row in categories
        ])
        cache_key = f"ebay:{marketplace}:category_tree"
        tree_cache = tree_cache or MarketplaceMetadataCache(marketplace="ebay", cache_key=cache_key)
        tree_cache.payload = payload
        tree_cache.source_version = f"ebay_taxonomy_tree_{tree_id}_v{tree_version}"
        tree_cache.expires_at = now + timedelta(days=30)
        if not cache_fresh:
            tree_cache.updated_at = now
        db.add(tree_cache)
        active_category_ids = {row["category_id"] for row in categories}
        expired_ids = {str(item.get("fromCategoryId")) for item in expired if isinstance(item, dict) and item.get("fromCategoryId")}
        replacements = {str(item["fromCategoryId"]): str(item.get("toCategoryId") or "") or None
                        for item in expired if isinstance(item, dict) and item.get("fromCategoryId")}
        obsolete = expired_ids - active_category_ids
        if obsolete:
            db.execute(text("""UPDATE ebay_taxonomy_categories SET source='deprecated_from_official_taxonomy_api'
                WHERE marketplace=:marketplace AND category_tree_id=:tree AND category_id IN :category_ids""").bindparams(bindparam("category_ids", expanding=True)),
                {"marketplace": marketplace, "tree": tree_id, "category_ids": sorted(obsolete)})
            old_rows = db.execute(text("""SELECT c.version_id,c.category_key,c.source_evidence_json
                FROM marketplace_taxonomy_categories c JOIN marketplace_taxonomy_versions v ON v.version_id=c.version_id
                WHERE c.marketplace='ebay' AND v.scope_key=:scope
                  AND c.category_id IN :category_ids""").bindparams(bindparam("category_ids", expanding=True)),
                {"scope": f"{marketplace}:{tree_id}", "version": tree_version, "category_ids": sorted(obsolete)}).mappings().all()
            for old_row in old_rows:
                evidence = _load_json(old_row["source_evidence_json"], {})
                evidence = evidence if isinstance(evidence, dict) else {"prior_evidence": evidence}
                category_id = db.execute(text("SELECT category_id FROM marketplace_taxonomy_categories WHERE version_id=:v AND category_key=:k"),
                                         {"v": old_row["version_id"], "k": old_row["category_key"]}).scalar()
                evidence["expired_category"] = {"checked_at": now.isoformat() + "Z", "current_tree_version": tree_version,
                                                 "replacement_category_id": replacements.get(str(category_id)),
                                                 "source": "eBay Taxonomy API getExpiredCategories"}
                db.execute(text("""UPDATE marketplace_taxonomy_categories SET verification_status='deprecated',
                    publishability_status='not_publishable',automated_selection_eligible=FALSE,source_evidence_json=:evidence
                    WHERE version_id=:v AND category_key=:k"""),
                    {"evidence": _json(evidence), "v": old_row["version_id"], "k": old_row["category_key"]})
        output.append({"marketplace_id": marketplace, "tree_id": tree_id, "tree_version": tree_version,
                       "retrieved_at": payload.get("_posterpro_taxonomy_evidence", {}).get("tree_retrieved_at"),
                       "version_checked_at": now.isoformat() + "Z", "tree_fetched_this_request": not cache_fresh,
                       "category_count": len(categories),
                       "terminal_count": sum(bool(row["leaf"]) for row in categories),
                       "expired_category_mappings": expired_count, "expired_mappings_fetched_this_request": not expiration_fresh,
                       "source": "official_eBay_Taxonomy_API"})
    db.commit()

    aspect_summary = {"requested_categories": [], "refreshed": [], "failed": []}
    if refresh_aspects:
        active = db.execute(text("""SELECT DISTINCT l.category_id,COALESCE(NULLIF(l.source_metadata->>'taxonomy_marketplace',''),'EBAY_US') AS marketplace,
              COALESCE(NULLIF(l.source_metadata->>'taxonomy_tree_id',''),CASE WHEN l.category_id IN (
                SELECT category_id FROM ebay_taxonomy_categories WHERE marketplace='EBAY_MOTORS_US' AND category_tree_id='100') THEN '100' ELSE '0' END) AS tree_id
          FROM listings l
          WHERE l.user_id=:user_id AND l.category_id IS NOT NULL AND l.category_id<>''
            AND l.status NOT IN ('archived','posted','PUBLISHED')
          ORDER BY l.category_id LIMIT :limit"""), {"user_id": user_id, "limit": MAX_ASPECT_CATEGORIES}).mappings().all()
        requested = {(str(row["marketplace"]).upper(), str(row["tree_id"]), str(row["category_id"])) for row in active}
        # Handbags are a known editor acceptance path and must remain covered
        # even if no current row is assigned to this category.
        requested.add(("EBAY_US", "0", "169291"))
        for marketplace, tree_id, category_id in sorted(requested):
            if marketplace not in selected or tree_id != SUPPORTED_TREES[marketplace]:
                continue
            version = next((item["tree_version"] for item in output if item["marketplace_id"] == marketplace), None)
            cache_key = f"ebay:{marketplace}:{tree_id}:{version}:{category_id}:aspects"
            cached = db.query(MarketplaceMetadataCache).filter(MarketplaceMetadataCache.cache_key == cache_key).first()
            aspect_summary["requested_categories"].append({"marketplace_id": marketplace, "tree_id": tree_id, "category_id": category_id})
            if cached and cached.expires_at and cached.expires_at > now:
                continue
            try:
                aspect_payload = await get_required_item_specifics(account.access_token, category_id, marketplace, tree_id=tree_id)
                row = cached or MarketplaceMetadataCache(marketplace="ebay", cache_key=cache_key)
                row.payload = aspect_payload
                row.source_version = f"ebay_taxonomy_aspects_{tree_id}_v{version}"
                row.expires_at = now + timedelta(days=7)
                db.add(row)
                aspect_summary["refreshed"].append(category_id)
            except Exception as exc:  # preserve bounded refresh and report per-category failures
                aspect_summary["failed"].append({"category_id": category_id, "error": str(exc)[:200]})
        db.commit()
    registry = synchronize_local_taxonomies(db, ["ebay"])
    return {"status": "refreshed", "provider": "eBay Taxonomy API", "account_user_id": user_id,
            "trees": output, "aspects": aspect_summary, "registry": registry}
