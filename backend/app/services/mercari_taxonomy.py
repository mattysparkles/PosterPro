"""Versioned Mercari US category imports and conservative category resolution."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy import update


APIFY_SOURCE = "apify:morkerr/mercari-category-url"
APIFY_URL = "https://apify.com/morkerr/mercari-category-url"
APIFY_NAMESPACE = "apify_mercari_category_url"


def _key(namespace: str, category_id: Any, path: list[str]) -> str:
    raw = f"{namespace}|{category_id or ''}|{'/'.join(path)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _value(row: dict[str, Any], *names: str) -> str | None:
    normalized = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        value = normalized.get(name.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _as_explicit_bool(value: Any) -> bool | None:
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "leaf"}:
            return True
        if normalized in {"false", "no", "0", "branch", "non-leaf", "nonleaf"}:
            return False
    return None


def _imported_seller_schema(row: dict[str, Any]) -> dict[str, Any] | None:
    """Preserve supplied schema evidence without elevating it to verified data."""
    aliases = {
        "required_fields": ("required_fields", "required_attributes", "required_item_specifics"),
        "conditional_fields": ("conditional_fields", "conditional_attributes", "conditionally_required_fields"),
        "optional_fields": ("optional_fields", "optional_attributes"),
        "recommended_fields": ("recommended_fields", "recommended_attributes"),
        "allowed_values": ("allowed_values", "enumerations", "permitted_values"),
        "field_constraints": ("field_constraints", "validation_constraints"),
        "dependencies": ("dependencies", "conditional_dependencies"),
        "shipping_eligibility": ("shipping_eligibility", "shipping_restrictions"),
        "schema_version": ("schema_version", "taxonomy_version"),
        "schema_complete": ("schema_complete", "required_fields_complete"),
        "schema_evidence": ("schema_evidence", "source_evidence"),
    }
    result: dict[str, Any] = {}
    normalized = {str(key).casefold(): value for key, value in row.items()}
    for target, keys in aliases.items():
        for key in keys:
            value = normalized.get(key.casefold())
            if value is not None and value != "":
                if isinstance(value, str) and value[:1] in "[{\"":
                    try:
                        value = json.loads(value)
                    except (ValueError, TypeError):
                        pass
                result[target] = value
                break
    if not result:
        return None
    return {"status": "imported_unverified", "seller_form_equivalence": "unverified", **result}


def normalize_apify_rows(rows: list[dict[str, Any]], *, source_complete: bool = False) -> list[dict[str, Any]]:
    """Expand the actor's L1/L2/L3 row format while keeping its IDs namespaced.

    These IDs identify category browse URLs; they are deliberately not marked
    compatible with the authenticated listing form until independently checked.
    """
    nodes: dict[str, dict[str, Any]] = {}
    for row in rows:
        levels = [
            (_value(row, "mainCategoryNameL1"), _value(row, "mainCategoryIdL1"), _value(row, "mainCategoryUrlL1")),
            (_value(row, "subCategoryNameL2"), _value(row, "subCategoryIdL2"), _value(row, "subCategoryUrlL2")),
            (_value(row, "subCategoryNameL3"), _value(row, "subCategoryIdL3"), _value(row, "subCategoryUrlL3")),
        ]
        path: list[str] = []
        parent_key = parent_id = None
        for depth, (name, category_id, url) in enumerate(levels, start=1):
            if not name:
                break
            path.append(name)
            node_key = _key(APIFY_NAMESPACE, category_id, path)
            nodes[node_key] = {
                "source_namespace": APIFY_NAMESPACE,
                "source_category_id": category_id,
                "category_key": node_key,
                "category_name": name,
                "category_url": url,
                "parent_category_key": parent_key,
                "parent_source_category_id": parent_id,
                "full_path": " > ".join(path),
                "path": path[:],
                "depth": depth,
                "is_leaf": False,
                "leaf_status": "branch",
                "verification_status": "unverified",
                "metadata": {"id_semantics": "Apify Mercari browse-category URL ID; not verified for listing form"},
            }
            if parent_key and parent_key in nodes:
                nodes[parent_key]["is_leaf"] = False
            parent_key, parent_id = node_key, category_id
    child_keys = {n["parent_category_key"] for n in nodes.values() if n["parent_category_key"]}
    for node in nodes.values():
        if node["category_key"] in child_keys:
            node["is_leaf"] = False
            node["leaf_status"] = "branch"
        else:
            # A missing child row is not evidence of a terminal node unless
            # the source acquisition itself was verified complete.
            node["is_leaf"] = bool(source_complete)
            node["leaf_status"] = "confirmed_leaf" if source_complete else "unknown"
    return list(nodes.values())


def parse_taxonomy_payload(payload: Any, source: str = APIFY_SOURCE, *, source_complete: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    if isinstance(payload, str):
        stripped = payload.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            payload = json.loads(payload)
        else:
            reader = csv.DictReader(io.StringIO(payload))
            payload = list(reader)
    if isinstance(payload, dict):
        rows = payload.get("items") or payload.get("categories") or payload.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("items", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Taxonomy payload must be a JSON/CSV list of category records")
    if source == APIFY_SOURCE and rows and any(any(str(key).lower() == "maincategorynamel1" for key in row) for row in rows):
        return normalize_apify_rows(rows, source_complete=source_complete), []
    nodes: list[dict[str, Any]] = []
    unresolved: list[str] = []
    pending = [dict(r) for r in rows]
    # Parse every row before connecting parent references, so child-first files
    # preserve relationships just as reliably as parent-first exports.
    for row in pending:
        path = row.get("path") or row.get("category_path") or row.get("full_path")
        if isinstance(path, str):
            path = [part.strip() for part in re.split(r"\s*(?:>|/|\|)\s*", path) if part.strip()]
        if not isinstance(path, list) or not path:
            path = [str(row.get("category_name") or row.get("name") or "").strip()]
        name = str(row.get("category_name") or row.get("name") or path[-1] or "").strip()
        if not name or not path or not path[-1]:
            unresolved.append(str(row.get("name") or "row without category name"))
            continue
        path = [str(x).strip() for x in path if str(x).strip()]
        namespace = str(row.get("source_namespace") or source.replace(":", "_").replace("/", "_"))
        external_id = row.get("source_category_id", row.get("category_id", row.get("id")))
        node_key = _key(namespace, external_id, path)
        declared_leaf_status = str(row.get("leaf_status") or "").strip().lower()
        explicit_leaf = _as_explicit_bool(row.get("is_leaf", row.get("leaf")))
        leaf_status = declared_leaf_status if declared_leaf_status in {"confirmed_leaf", "branch", "unknown"} else ("confirmed_leaf" if explicit_leaf is True else "branch" if explicit_leaf is False else "unknown")
        parent_id_value = row.get("parent_category_id", row.get("parent_source_category_id"))
        imported_schema = _imported_seller_schema(row)
        metadata = {"id_semantics": row.get("id_semantics") or "source-specific; listing-form equivalence unverified"}
        if imported_schema:
            metadata["seller_schema"] = imported_schema
        nodes.append({"source_namespace": namespace, "source_category_id": str(external_id) if external_id is not None else None,
                      "category_key": node_key, "category_name": name, "category_url": row.get("category_url") or row.get("url"),
                      "parent_category_key": None,
                      "parent_source_category_id": str(parent_id_value) if parent_id_value is not None else None,
                      "full_path": " > ".join(path), "path": path, "depth": len(path),
                      "is_leaf": leaf_status == "confirmed_leaf", "leaf_status": leaf_status,
                      "_ancestry_unresolved": False,
                      "_external_parent_id": str(parent_id_value) if parent_id_value is not None else None,
                      "verification_status": str(row.get("verification_status") or "unverified"),
                      "metadata": metadata})
    observed_paths = {tuple(node["path"]) for node in nodes}
    inferred_ancestors = []
    for node in nodes:
        for depth in range(1, len(node["path"])):
            path = node["path"][:depth]
            path_key = tuple(path)
            if path_key in observed_paths:
                continue
            observed_paths.add(path_key)
            namespace = node["source_namespace"]
            inferred_ancestors.append({
                "source_namespace": namespace, "source_category_id": None,
                "category_key": _key(namespace, None, path), "category_name": path[-1],
                "category_url": None, "parent_category_key": None, "parent_source_category_id": None,
                "full_path": " > ".join(path), "path": path, "depth": depth,
                "is_leaf": False, "leaf_status": "branch", "_ancestry_unresolved": False,
                "verification_status": "unverified",
                "metadata": {"id_semantics": "path-only ancestor inferred from complete category path; source category ID unavailable"},
            })
    nodes.extend(inferred_ancestors)
    unique_nodes = {node["category_key"]: node for node in nodes}
    by_path = {tuple(node["path"]): node for node in unique_nodes.values()}
    id_candidates: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for node in unique_nodes.values():
        if node["source_category_id"]:
            id_candidates.setdefault((node["source_namespace"], node["source_category_id"]), []).append(node)
    by_id = {key: values[0] for key, values in id_candidates.items() if len(values) == 1}
    for node in unique_nodes.values():
        parent = by_path.get(tuple(node["path"][:-1])) if len(node["path"]) > 1 else None
        if not parent and node.get("_external_parent_id"):
            parent = by_id.get((node["source_namespace"], node["_external_parent_id"]))
        if parent:
            node["parent_category_key"] = parent["category_key"]
            node["parent_source_category_id"] = parent["source_category_id"]
        elif len(node["path"]) > 1 or node.get("_external_parent_id"):
            unresolved.append(node["full_path"])
            node["_ancestry_unresolved"] = True

    # Rebuild paths from explicit parent IDs as well as supplied paths, while
    # detecting cycles and refusing to label broken ancestry as a root.
    def rebuild_path(node: dict[str, Any], seen: set[str] | None = None) -> list[str]:
        seen = set(seen or ())
        if node["category_key"] in seen:
            unresolved.append(f"cycle:{node['full_path']}")
            node["_ancestry_unresolved"] = True
            return node["path"]
        seen.add(node["category_key"])
        parent = unique_nodes.get(node["parent_category_key"])
        if parent:
            path = rebuild_path(parent, seen) + [node["category_name"]]
            node["path"] = path
            node["full_path"] = " > ".join(path)
            node["depth"] = len(path)
        return node["path"]

    for node in unique_nodes.values():
        rebuild_path(node)
    parent_keys = {node["parent_category_key"] for node in unique_nodes.values() if node["parent_category_key"]}
    for node in unique_nodes.values():
        node.pop("_external_parent_id", None)
        ancestry_unresolved = node.pop("_ancestry_unresolved", False)
        if node["category_key"] in parent_keys:
            node["is_leaf"] = False
            node["leaf_status"] = "branch"
        elif ancestry_unresolved:
            node["is_leaf"] = False
            node["leaf_status"] = "unknown"
        elif source_complete and node["leaf_status"] == "unknown" and not ancestry_unresolved:
            node["is_leaf"] = True
            node["leaf_status"] = "confirmed_leaf"
    return list(unique_nodes.values()), unresolved


def import_taxonomy(db: Session, payload: Any, *, source: str = APIFY_SOURCE, source_url: str | None = None,
                    verification_status: str = "unverified", created_by: int | None = None,
                    version_id: str | None = None, source_complete: bool = False) -> dict[str, Any]:
    nodes, unresolved = parse_taxonomy_payload(payload, source, source_complete=source_complete)
    if not nodes:
        raise ValueError("No usable taxonomy categories were found in the import")
    version_id = version_id or f"mercari-us-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{hashlib.sha1(json.dumps(nodes, sort_keys=True).encode()).hexdigest()[:8]}"
    captured_at = datetime.now(UTC).replace(tzinfo=None)
    roots = sum(1 for node in nodes if node["depth"] == 1 and not node["parent_source_category_id"])
    max_depth = max((node["depth"] for node in nodes), default=0)
    manifest = payload.get("manifest") if isinstance(payload, dict) and isinstance(payload.get("manifest"), dict) else {}
    manifest_note = str(manifest.get("note") or "").strip()[:1000]
    source_note = str(manifest.get("source") or "").strip()[:300]
    notes = "Imported IDs remain source-namespaced; completeness must be supported by acquisition evidence."
    if source_note or manifest_note:
        notes += f" Supplied manifest source: {source_note or 'unspecified'}. {manifest_note}".strip()
    db.execute(text("""INSERT INTO mercari_taxonomy_versions
      (version_id,marketplace,source,source_url,captured_at,verification_status,node_count,leaf_count,unresolved_branches,notes,created_by,source_completeness,root_count,max_depth)
      VALUES (:version,'mercari_us',:source,:url,:captured,:verification,:count,:leaves,:unresolved,:notes,:user,:completeness,:roots,:max_depth)"""),
      {"version": version_id, "source": source, "url": source_url or (APIFY_URL if source == APIFY_SOURCE else None),
       "captured": captured_at, "verification": verification_status, "count": len(nodes),
       "leaves": sum(bool(n["is_leaf"]) for n in nodes), "unresolved": len(unresolved),
       "notes": notes, "user": created_by,
       "completeness": "complete" if source_complete else "partial", "roots": roots, "max_depth": max_depth})
    insert = text("""INSERT INTO mercari_taxonomy_categories
          (version_id,source_namespace,source_category_id,category_key,category_name,category_url,parent_category_key,parent_source_category_id,
           full_path,path_json,depth,is_leaf,leaf_status,verification_status,observed_at,metadata_json)
          VALUES (:version,:namespace,:source_id,:key,:name,:url,:parent_key,:parent_id,:path,:path_json,:depth,:leaf,:leaf_status,:verification,:observed,:metadata)""")
    for offset in range(0, len(nodes), 500):
        batch = nodes[offset:offset + 500]
        db.execute(insert, [{"version": version_id, "namespace": node["source_namespace"], "source_id": node["source_category_id"],
           "key": node["category_key"], "name": node["category_name"], "url": node["category_url"],
           "parent_key": node["parent_category_key"], "parent_id": node["parent_source_category_id"],
           "path": node["full_path"], "path_json": json.dumps(node["path"]), "depth": node["depth"],
           "leaf": bool(node["is_leaf"]), "leaf_status": node["leaf_status"], "verification": node["verification_status"], "observed": captured_at,
           "metadata": json.dumps(node["metadata"], ensure_ascii=False)} for node in batch])
    db.commit()
    return {"version_id": version_id, "source": source, "node_count": len(nodes),
            "leaf_count": sum(bool(n["is_leaf"]) for n in nodes), "unresolved_branches": len(unresolved),
            "unresolved": unresolved[:100], "verification_status": verification_status,
            "source_completeness": "complete" if source_complete else "partial", "root_count": roots, "max_depth": max_depth}


def taxonomy_status(db: Session) -> dict[str, Any]:
    versions = db.execute(text("SELECT version_id,source,source_url,captured_at,verification_status,source_completeness,node_count,leaf_count,root_count,max_depth,unresolved_branches,notes FROM mercari_taxonomy_versions ORDER BY captured_at DESC")).mappings().all()
    active = versions[0] if versions else None
    coverage = "not_acquired"
    if active:
        if active["source_completeness"] != "complete" or active["unresolved_branches"]:
            coverage = "partial"
        elif active["verification_status"] not in {"path_verified", "operator_verified"}:
            coverage = "acquired_unverified"
        else:
            coverage = "complete_and_verified"
    unknown_terminal_count = 0
    if active:
        counts = db.execute(text("""SELECT COUNT(*) AS node_count,
          SUM(CASE WHEN c.leaf_status='confirmed_leaf' AND c.is_leaf IS TRUE THEN 1 ELSE 0 END) AS leaf_count,
          SUM(CASE WHEN c.verification_status IN ('path_verified','operator_verified')
                    AND c.leaf_status='confirmed_leaf' AND c.is_leaf IS TRUE THEN 1 ELSE 0 END) AS verified_leaf_count,
          SUM(CASE WHEN c.depth=1 THEN 1 ELSE 0 END) AS root_count,
          SUM(CASE WHEN c.leaf_status='branch' THEN 1 ELSE 0 END) AS branch_count,
          MAX(c.depth) AS max_depth
          FROM mercari_taxonomy_categories c WHERE c.version_id=:v"""), {"v": active["version_id"]}).mappings().one()
        unknown_terminal_count = db.execute(text("""SELECT COUNT(*) FROM mercari_taxonomy_categories c
          WHERE c.version_id=:v AND c.leaf_status='unknown' AND NOT EXISTS (
            SELECT 1 FROM mercari_taxonomy_categories child WHERE child.version_id=c.version_id AND child.parent_category_key=c.category_key
          )"""), {"v": active["version_id"]}).scalar_one()
        latest = dict(active)
        # Version row counts describe the original imported snapshot. Keep
        # them available as source_* fields, while presenting live registry
        # counts that include later, evidence-backed observations.
        latest["source_node_count"] = latest["node_count"]
        latest["source_leaf_count"] = latest["leaf_count"]
        latest["source_root_count"] = latest["root_count"]
        latest["node_count"] = int(counts["node_count"] or 0)
        latest["leaf_count"] = int(counts["leaf_count"] or 0)
        latest["verified_leaf_count"] = int(counts["verified_leaf_count"] or 0)
        latest["root_count"] = int(counts["root_count"] or 0)
        latest["branch_count"] = int(counts["branch_count"] or 0)
        latest["max_depth"] = int(counts["max_depth"] or 0)
    else:
        latest = None
    return {"marketplace": "mercari_us", "version_count": len(versions),
            "latest": latest, "versions": [dict(v) for v in versions[:20]],
            "unknown_terminal_count": int(unknown_terminal_count), "coverage_status": coverage}


def search_categories(db: Session, query: str, *, leaves_only: bool = False, limit: int = 50, version_id: str | None = None) -> list[dict[str, Any]]:
    if not version_id:
        version_id = db.execute(text("SELECT version_id FROM mercari_taxonomy_versions ORDER BY captured_at DESC LIMIT 1")).scalar()
    if not version_id:
        return []
    result = db.execute(text("""SELECT version_id,source_namespace,source_category_id,category_key,category_name,category_url,
      parent_category_key,parent_source_category_id,full_path,path_json,depth,is_leaf,leaf_status,verification_status,observed_at,metadata_json
      FROM mercari_taxonomy_categories WHERE version_id=:version AND (:leaf_filter IS FALSE OR (is_leaf IS TRUE AND leaf_status='confirmed_leaf'))
      AND (lower(category_name) LIKE :q OR lower(full_path) LIKE :q) ORDER BY is_leaf DESC,full_path LIMIT :limit"""),
      {"version": version_id, "leaf_filter": bool(leaves_only), "q": f"%{query.strip().lower()}%", "limit": max(1, min(limit, 200))})
    output = []
    for row in result.mappings():
        item = dict(row)
        item["path"] = json.loads(item.pop("path_json") or "[]")
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        item["is_leaf"] = bool(item["is_leaf"])
        output.append(item)
    return output


def resolve_category(db: Session, *, mapping_key: str | None = None, query: str = "", limit: int = 10) -> dict[str, Any]:
    if mapping_key:
        mapping = db.execute(text("""SELECT m.mapping_key,m.mapping_label,m.rationale,c.* FROM mercari_taxonomy_mappings m
          JOIN mercari_taxonomy_categories c ON c.version_id=m.version_id AND c.category_key=m.category_key
          WHERE m.mapping_key=:key AND c.is_leaf IS TRUE AND c.leaf_status='confirmed_leaf' AND c.verification_status IN ('path_verified','operator_verified')"""), {"key": mapping_key}).mappings().first()
        if mapping:
            return {"status": "approved_mapping", "match": dict(mapping), "suggestions": []}
    # Deterministic token overlap is a review aid only; it never auto-assigns.
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
    latest_version = db.execute(text("SELECT version_id FROM mercari_taxonomy_versions ORDER BY captured_at DESC LIMIT 1")).scalar()
    if not latest_version:
        candidates = []
    else:
        candidates = []
        for row in db.execute(text("""SELECT version_id,source_namespace,source_category_id,category_key,category_name,category_url,
          parent_category_key,parent_source_category_id,full_path,path_json,depth,is_leaf,leaf_status,verification_status,metadata_json
          FROM mercari_taxonomy_categories WHERE version_id=:v AND is_leaf IS TRUE AND leaf_status='confirmed_leaf' ORDER BY full_path"""), {"v": latest_version}).mappings():
            item = dict(row); item["path"] = json.loads(item.pop("path_json") or "[]"); item["metadata"] = json.loads(item.pop("metadata_json") or "{}"); item["is_leaf"] = bool(item["is_leaf"]); candidates.append(item)
    ranked = []
    for candidate in candidates:
        tokens = {w for w in re.findall(r"[a-z0-9]+", candidate["full_path"].lower()) if len(w) > 2}
        overlap = words & tokens
        if overlap:
            ranked.append((len(overlap) / max(1, len(words)), candidate))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["full_path"]))
    return {"status": "review_required" if ranked else "no_candidate", "match": None,
            "suggestions": [{**candidate, "score": round(score, 3), "selection_requires_operator_approval": True}
                            for score, candidate in ranked[:limit]]}


def approve_mapping(db: Session, mapping_key: str, mapping_label: str, version_id: str, category_key: str,
                    approved_by: int, rationale: str | None = None) -> None:
    exists = db.execute(text("SELECT 1 FROM mercari_taxonomy_categories WHERE version_id=:v AND category_key=:k AND is_leaf IS TRUE AND leaf_status='confirmed_leaf' AND verification_status IN ('path_verified','operator_verified')"), {"v": version_id, "k": category_key}).first()
    if not exists:
        raise ValueError("Only a verified terminal category can be approved as a reusable mapping")
    db.execute(text("""INSERT INTO mercari_taxonomy_mappings(mapping_key,mapping_label,version_id,category_key,approved_by,rationale)
      VALUES (:key,:label,:version,:category,:user,:rationale) ON CONFLICT(mapping_key) DO UPDATE SET
      mapping_label=excluded.mapping_label,version_id=excluded.version_id,category_key=excluded.category_key,
      approved_by=excluded.approved_by,approved_at=CURRENT_TIMESTAMP,rationale=excluded.rationale"""),
      {"key": mapping_key, "label": mapping_label, "version": version_id, "category": category_key, "user": approved_by, "rationale": rationale})
    db.commit()


def verify_category_from_diagnostic(db: Session, *, version_id: str, category_key: str, job: Any) -> dict[str, Any]:
    """Mark a path verified only from a successful non-submitting Mercari diagnostic.

    Browse IDs stay explicitly unverified for listing-form use; this validates
    the visible path and the extension's persisted category summary only.
    """
    row = db.execute(text("SELECT * FROM mercari_taxonomy_categories WHERE version_id=:v AND category_key=:k"),
                     {"v": version_id, "k": category_key}).mappings().first()
    if not row or row["leaf_status"] == "branch":
        raise ValueError("Only a non-branch taxonomy path can be tested")
    if str(getattr(job, "marketplace", "")).lower() != "mercari" or str(getattr(job, "action", "")).upper() != "DIAGNOSTIC":
        raise ValueError("Evidence must be a Mercari diagnostic job")
    if str(getattr(job, "status", "")).upper() not in {"COMPLETED", "BLOCKED_EXTERNAL", "AWAITING_OPERATOR_REVIEW"}:
        raise ValueError("Diagnostic must finish with a persisted result before verification")
    result = job.result if isinstance(job.result, dict) else {}
    if result.get("submission_performed") is not False:
        raise ValueError("Diagnostic must explicitly confirm that no submission was performed")
    field = next((item for item in result.get("field_results", []) if isinstance(item, dict) and item.get("field") == "category"), None)
    if not field or field.get("filled") is not True:
        raise ValueError("Diagnostic did not independently verify category selection")
    requested = ((job.payload_snapshot or {}).get("marketplace_payload") or {}).get("category_path")
    if not isinstance(requested, list) or [str(x).strip().casefold() for x in requested] != [str(x).strip().casefold() for x in json.loads(row["path_json"])]:
        raise ValueError("Diagnostic category path does not exactly match the imported taxonomy path")
    verified_value = str(field.get("verified_value") or "").strip().casefold()
    full_path = str(row["full_path"]).strip().casefold()
    category_name = str(row["category_name"]).strip().casefold()
    if verified_value not in {full_path, category_name}:
        raise ValueError("Diagnostic verified value does not match this taxonomy category")
    # A partial reference's unknown terminal can be confirmed by this exact
    # live-form evidence. Never promote an intermediate node that has children.
    child = db.execute(text("SELECT 1 FROM mercari_taxonomy_categories WHERE version_id=:v AND parent_category_key=:k LIMIT 1"),
                       {"v": version_id, "k": category_key}).first()
    if child:
        raise ValueError("This taxonomy path has known children and cannot be promoted to a leaf")
    db.execute(text("UPDATE mercari_taxonomy_categories SET is_leaf=TRUE, leaf_status='confirmed_leaf', verification_status='path_verified', verification_evidence_job_id=:job, path_verified_at=:at WHERE version_id=:v AND category_key=:k"),
               {"job": int(job.id), "at": datetime.now(UTC).replace(tzinfo=None), "v": version_id, "k": category_key})
    db.commit()
    return {"status": "path_verified", "version_id": version_id, "category_key": category_key,
            "category_path": json.loads(row["path_json"]), "diagnostic_job_id": job.id,
            "source_category_id_status": "not_assumed_compatible_with_listing_form"}


def record_observed_path_from_diagnostic(db: Session, *, version_id: str, category_path: list[str], job: Any,
                                        commit: bool = True) -> dict[str, Any]:
    """Persist an exact path only after the non-submitting live form confirms it."""
    path = [str(part).strip() for part in category_path]
    if not path or len(path) > 8 or any(not part or len(part) > 120 for part in path):
        raise ValueError("A category path must contain 1–8 non-empty labels")
    if str(getattr(job, "marketplace", "")).lower() != "mercari" or str(getattr(job, "action", "")).upper() != "DIAGNOSTIC":
        raise ValueError("Evidence must be a Mercari diagnostic job")
    if str(getattr(job, "status", "")).upper() not in {"COMPLETED", "BLOCKED_EXTERNAL", "AWAITING_OPERATOR_REVIEW"}:
        raise ValueError("Diagnostic must finish with a persisted result before verification")
    result = job.result if isinstance(job.result, dict) else {}
    if result.get("submission_performed") is not False:
        raise ValueError("Diagnostic must explicitly confirm that no submission was performed")
    requested = ((job.payload_snapshot or {}).get("marketplace_payload") or {}).get("category_path")
    if not isinstance(requested, list) or [str(x).strip().casefold() for x in requested] != [x.casefold() for x in path]:
        raise ValueError("Diagnostic request does not match this exact category path")
    field = next((item for item in result.get("field_results", []) if isinstance(item, dict) and item.get("field") == "category"), None)
    if not field or field.get("filled") is not True:
        raise ValueError("Diagnostic did not independently verify category selection")
    verified_value = str(field.get("verified_value") or "").strip().casefold()
    if verified_value not in {" > ".join(path).casefold(), path[-1].casefold()}:
        raise ValueError("Live form's persisted category does not match the requested path")

    now = getattr(job, "completed_at", None) or datetime.now(UTC).replace(tzinfo=None)
    if getattr(now, "tzinfo", None):
        now = now.astimezone(UTC).replace(tzinfo=None)
    category_evidence = field.get("interaction_diagnostic") if isinstance(field.get("interaction_diagnostic"), dict) else {}
    persistence_evidence = category_evidence.get("persistence") if isinstance(category_evidence.get("persistence"), dict) else {}
    category_id = category_evidence.get("destination_category_id")
    if not isinstance(category_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", category_id):
        category_id = None
    required_attributes = result.get("category_required_attributes")
    if not isinstance(required_attributes, list):
        required_attributes = [name for name in (result.get("missing_required_fields") or []) if isinstance(name, str)]
    namespace = "mercari_us_live_form_observed"
    final_key = _key(namespace, None, path)
    previous = db.execute(text("SELECT metadata_json FROM mercari_taxonomy_categories WHERE version_id=:v AND category_key=:k"),
                          {"v": version_id, "k": final_key}).mappings().first()
    previous_metadata = json.loads(previous["metadata_json"] or "{}") if previous else {}
    diagnostic_ids = set(previous_metadata.get("source_diagnostic_ids") or [])
    diagnostic_ids.add(int(job.id))
    metadata = {
        "validation_method": "authenticated_local_extension_live_form_diagnostic",
        "validation_timestamp": now.isoformat() + "Z",
        "marketplace_region": "US",
        "diagnostic_job_id": int(job.id),
        "source_diagnostic_ids": sorted(diagnostic_ids),
        "form_selection_persisted": persistence_evidence.get("verified") is True,
        "observed_destination_category_id": category_id,
        "category_specific_required_attributes": required_attributes[:40],
        "source_category_id_equivalence": "unverified",
    }
    parent_key = None
    for depth in range(1, len(path) + 1):
        prefix = path[:depth]
        key = _key(namespace, None, prefix)
        is_leaf = depth == len(path)
        db.execute(text("""INSERT INTO mercari_taxonomy_categories
          (version_id,source_namespace,source_category_id,category_key,category_name,category_url,parent_category_key,
           parent_source_category_id,full_path,path_json,depth,is_leaf,leaf_status,verification_status,
           verification_evidence_job_id,path_verified_at,observed_at,metadata_json)
          VALUES (:v,:namespace,NULL,:key,:name,NULL,:parent,NULL,:full,:path,:depth,:leaf,:leaf_status,:verification,
           :job,:verified_at,:observed,:metadata)
          ON CONFLICT(version_id,category_key) DO UPDATE SET
           is_leaf=excluded.is_leaf,leaf_status=excluded.leaf_status,verification_status=excluded.verification_status,
           verification_evidence_job_id=excluded.verification_evidence_job_id,path_verified_at=excluded.path_verified_at,
           observed_at=excluded.observed_at,metadata_json=excluded.metadata_json"""), {
            "v": version_id, "namespace": namespace, "key": key, "name": prefix[-1], "parent": parent_key,
            "full": " > ".join(prefix), "path": json.dumps(prefix), "depth": depth, "leaf": is_leaf,
            "leaf_status": "confirmed_leaf" if is_leaf else "branch",
            "verification": "path_verified" if is_leaf else "unverified",
            "job": int(job.id) if is_leaf else None, "verified_at": now if is_leaf else None, "observed": now,
            "metadata": json.dumps(metadata),
        })
        parent_key = key
    if commit:
        db.commit()
    return {"status": "path_verified", "version_id": version_id, "category_key": final_key,
            "category_path": path, "diagnostic_job_id": job.id, "source_category_id_status": "no ID assumed"}


def export_categories(db: Session, *, version_id: str | None = None, fmt: str = "json") -> str:
    rows = search_categories(db, "", limit=200, version_id=version_id)
    # search_categories applies a search predicate; use explicit bounded export query instead.
    if version_id is None:
        version_id = db.execute(text("SELECT version_id FROM mercari_taxonomy_versions ORDER BY captured_at DESC LIMIT 1")).scalar()
    if not version_id:
        rows = []
    else:
        rows = []
        for row in db.execute(text("SELECT source_namespace,source_category_id,category_key,category_name,category_url,parent_category_key,parent_source_category_id,full_path,path_json,depth,is_leaf,leaf_status,verification_status,metadata_json FROM mercari_taxonomy_categories WHERE version_id=:v ORDER BY full_path"), {"v": version_id}).mappings():
            item = dict(row); item["path"] = json.loads(item.pop("path_json") or "[]"); item["metadata"] = json.loads(item.pop("metadata_json") or "{}"); item["is_leaf"] = bool(item["is_leaf"]); rows.append(item)
    if fmt == "csv":
        output = io.StringIO(); fields = ["source_namespace", "source_category_id", "category_name", "category_url", "parent_source_category_id", "full_path", "depth", "is_leaf", "leaf_status", "verification_status"]
        writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({key: row.get(key) for key in fields})
        return output.getvalue()
    return json.dumps({"version_id": version_id, "marketplace": "mercari_us", "categories": rows}, ensure_ascii=False, indent=2)
