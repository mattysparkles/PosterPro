"""Evidence-preserving registry over the existing marketplace taxonomy sources."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


MARKETPLACES = ("ebay", "mercari", "facebook")
VERIFICATION_STATUSES = {
    "authoritatively_verified", "live_browser_verified", "imported_unverified",
    "inferred_candidate", "deprecated", "unknown",
}
STALE_AFTER = timedelta(days=30)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load_json(value: Any, fallback: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _status(value: str) -> str:
    return value if value in VERIFICATION_STATUSES else "unknown"


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            return None
    return None


def _staleness(checked_at: Any) -> str:
    checked = _timestamp(checked_at)
    if checked is None:
        return "unknown"
    return "stale" if datetime.now(UTC).replace(tzinfo=None) - checked > STALE_AFTER else "fresh"


def _table_exists(db: Session, table: str) -> bool:
    return inspect(db.get_bind()).has_table(table)


def _aspect_groups(aspects: Any) -> tuple[list | None, list | None, list | None, list | None]:
    if not isinstance(aspects, list):
        return None, None, None, None
    required: list[dict] = []
    optional: list[dict] = []
    recommended: list[dict] = []
    conditional: list[dict] = []
    for aspect in aspects:
        if not isinstance(aspect, dict):
            continue
        constraint = aspect.get("aspectConstraint") if isinstance(aspect.get("aspectConstraint"), dict) else {}
        values = aspect.get("aspectValues") if isinstance(aspect.get("aspectValues"), list) else []
        is_conditional = any(isinstance(value, dict) and value.get("valueConstraints") for value in values)
        if constraint.get("aspectRequired") is True:
            required.append(aspect)
        elif is_conditional:
            conditional.append(aspect)
        elif str(aspect.get("aspectUsage") or "").upper() == "RECOMMENDED":
            recommended.append(aspect)
        else:
            optional.append(aspect)
    return required, optional, recommended, conditional


def _attribute_schema_state(category: dict[str, Any]) -> str:
    """Classify schema evidence without treating an absent/empty cache as proof.

    A schema is complete only when its source evidence says it was retrieved.
    A genuinely empty list is meaningful only alongside that retrieval evidence.
    """
    evidence = category.get("source_evidence_json")
    if isinstance(evidence, str):
        evidence = _load_json(evidence, {})
    evidence = evidence if isinstance(evidence, dict) else {}
    cache = evidence.get("aspect_cache") or evidence.get("attribute_schema")
    if isinstance(cache, dict) and cache.get("staleness_status") == "stale":
        return "stale"
    if not isinstance(cache, dict):
        return "uncollected"
    if category.get("required_attributes_json") is None:
        return "incomplete"
    groups = [category.get(key) for key in (
        "required_attributes_json", "optional_attributes_json",
        "recommended_attributes_json", "conditional_attributes_json",
    )]
    parsed = [_load_json(value, None) if isinstance(value, str) else value for value in groups]
    if any(not isinstance(value, list) for value in parsed):
        return "incomplete"
    return "verified_empty" if not any(parsed) else "complete"


def _ebay_aspect_cache(db: Session) -> dict[tuple[str, str, str, str], dict]:
    if not _table_exists(db, "marketplace_metadata_cache"):
        return {}
    rows = db.execute(text("""
        SELECT cache_key, payload, source_version, updated_at, expires_at
          FROM marketplace_metadata_cache
         WHERE cache_key LIKE :pattern
    """), {"pattern": "ebay:%:aspects"}).mappings().all()
    result: dict[tuple[str, str, str, str], dict] = {}
    current = datetime.now(UTC).replace(tzinfo=None)
    pattern = re.compile(r"^ebay:([^:]+):([^:]+):([^:]+):([^:]+):aspects$")
    for row in rows:
        match = pattern.match(str(row["cache_key"] or ""))
        if not match:
            continue
        payload = _load_json(row["payload"], {})
        groups = _aspect_groups(payload.get("aspects") if isinstance(payload, dict) else None)
        expires = _timestamp(row["expires_at"])
        result[match.groups()] = {
            "groups": groups,
            "updated_at": _timestamp(row["updated_at"]),
            "staleness": "stale" if expires and expires < current else "fresh" if expires else "unknown",
            "source_version": str(row.get("source_version") or "ebay_taxonomy_v1"),
        }
    return result


def _ebay_tree_staleness(db: Session, marketplace_id: str, checked_at: Any, native_version: str) -> str:
    if _table_exists(db, "marketplace_metadata_cache"):
        cache = db.execute(text("""
            SELECT expires_at,updated_at,payload FROM marketplace_metadata_cache
             WHERE cache_key=:key ORDER BY updated_at DESC LIMIT 1
        """), {"key": f"ebay:{marketplace_id}:category_tree"}).mappings().first()
        if cache:
            payload = _load_json(cache["payload"], {})
            version = str(payload.get("categoryTreeVersion") or "") if isinstance(payload, dict) else ""
            if version and version != str(native_version):
                return "stale"
            expires = _timestamp(cache["expires_at"])
            if expires:
                return "stale" if expires < datetime.now(UTC).replace(tzinfo=None) else "fresh"
            return "unknown"
    # A local table's last import/update timestamp says when PosterPro copied
    # rows, not when the marketplace taxonomy was last verified.
    return "unknown"


def _cached_tree_node_count(payload: Any) -> tuple[str | None, int]:
    if not isinstance(payload, dict):
        return None, 0
    version = str(payload.get("categoryTreeVersion") or "") or None
    root = payload.get("rootCategoryNode")
    count = 0
    pending = [root] if isinstance(root, dict) else []
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        if isinstance(node.get("category"), dict):
            count += 1
        children = node.get("childCategoryTreeNodes")
        if isinstance(children, list):
            pending.extend(child for child in children if isinstance(child, dict))
    return version, count


def _store_version(db: Session, version: dict, categories: list[dict]) -> None:
    previous_version = db.execute(text("SELECT source_evidence_json FROM marketplace_taxonomy_versions WHERE version_id=:id"),
                                  {"id": version["version_id"]}).mappings().first()
    if previous_version:
        prior_evidence = _load_json(previous_version["source_evidence_json"], {})
        incoming_evidence = version.get("evidence") if isinstance(version.get("evidence"), dict) else {}
        observations = list(prior_evidence.get("observations") or []) if isinstance(prior_evidence, dict) else []
        prior_base = {k: v for k, v in prior_evidence.items() if k != "observations"} if isinstance(prior_evidence, dict) else {}
        for evidence in (prior_base, incoming_evidence):
            if evidence and evidence not in observations:
                observations.append(evidence)
        version["evidence"] = {**incoming_evidence, "observations": observations}
    db.execute(text("""
        UPDATE marketplace_taxonomy_versions
           SET is_current = FALSE
         WHERE marketplace = :marketplace AND scope_key = :scope AND version_id <> :version_id
    """), {"marketplace": version["marketplace"], "scope": version["scope_key"], "version_id": version["version_id"]})
    db.execute(text("""
        INSERT INTO marketplace_taxonomy_versions
          (version_id,marketplace,scope_key,native_version,source,source_url,source_evidence_json,
           captured_at,last_checked_at,verification_status,completeness_status,staleness_status,
           category_count,terminal_count,unresolved_count,is_current,notes)
        VALUES
          (:version_id,:marketplace,:scope,:native_version,:source,:source_url,:evidence,
           :captured,:checked,:verification,:completeness,:staleness,:count,:terminal,:unresolved,TRUE,:notes)
        ON CONFLICT(version_id) DO UPDATE SET
          source=excluded.source, source_url=excluded.source_url,
          source_evidence_json=excluded.source_evidence_json,
          verification_status=CASE
            WHEN marketplace_taxonomy_versions.verification_status='authoritatively_verified' THEN marketplace_taxonomy_versions.verification_status
            WHEN excluded.verification_status='authoritatively_verified' THEN excluded.verification_status
            WHEN marketplace_taxonomy_versions.verification_status='live_browser_verified' THEN marketplace_taxonomy_versions.verification_status
            ELSE excluded.verification_status END,
          completeness_status=excluded.completeness_status,
          staleness_status=excluded.staleness_status, category_count=excluded.category_count,
          terminal_count=excluded.terminal_count, unresolved_count=excluded.unresolved_count,
          last_checked_at=CASE WHEN :clear_last_checked THEN excluded.last_checked_at
            ELSE COALESCE(excluded.last_checked_at,marketplace_taxonomy_versions.last_checked_at) END,
          is_current=TRUE, notes=COALESCE(excluded.notes,marketplace_taxonomy_versions.notes)
    """), {
        "version_id": version["version_id"], "marketplace": version["marketplace"], "scope": version["scope_key"],
        "native_version": version.get("native_version"), "source": version["source"],
        "source_url": version.get("source_url"), "evidence": _json(version.get("evidence") or {}),
        "captured": version.get("captured_at") or datetime.now(UTC).replace(tzinfo=None),
        "checked": version.get("last_checked_at"), "verification": _status(version.get("verification_status", "unknown")),
        "completeness": version.get("completeness_status", "partial"), "staleness": version.get("staleness_status", "unknown"),
        "count": len(categories), "terminal": sum(row["terminal"] == "terminal" for row in categories),
        "unresolved": sum(row["terminal"] == "unknown" for row in categories), "notes": version.get("notes"),
        "clear_last_checked": bool(version.get("clear_last_checked")),
    })
    # Merge evidence before upsert so repeated local synchronization preserves
    # independent live-browser/provider observations and operator verification.
    rank = {"unknown": 0, "inferred_candidate": 1, "imported_unverified": 2,
            "live_browser_verified": 3, "authoritatively_verified": 4, "deprecated": 5}
    existing_rows = db.execute(text("""SELECT category_key,verification_status,source_evidence_json,last_verified_at,
        terminal_status,category_id,required_attributes_json,optional_attributes_json,recommended_attributes_json,
        conditional_attributes_json
        FROM marketplace_taxonomy_categories WHERE version_id=:version"""),
        {"version": version["version_id"]}).mappings().all()
    existing_by_key = {str(row["category_key"]): row for row in existing_rows}
    for category in categories:
        existing = existing_by_key.get(str(category["category_key"]))
        if existing is None:
            continue
        if rank.get(existing["verification_status"], 0) > rank.get(category["verification"], 0):
            category["verification"] = existing["verification_status"]
        prior = _load_json(existing["source_evidence_json"], {})
        incoming = _load_json(category["evidence"], {})
        prior_schema_status = prior.get("required_fields_schema_status") if isinstance(prior, dict) else None
        incoming_schema_status = incoming.get("required_fields_schema_status") if isinstance(incoming, dict) else None
        schema_rank = {"uncollected": 0, "incomplete": 1, "verified_empty": 2, "verified_complete": 3}
        if (schema_rank.get(str(prior_schema_status), 0) > schema_rank.get(str(incoming_schema_status), 0)):
            for column, key in (("required_attributes_json", "required"), ("optional_attributes_json", "optional"),
                                ("recommended_attributes_json", "recommended"), ("conditional_attributes_json", "conditional")):
                if existing[column] is not None:
                    category[key] = existing[column]
            category["terminal"] = existing["terminal_status"]
            category["verification"] = existing["verification_status"]
            incoming["required_fields_schema_status"] = prior_schema_status
            incoming["schema_evidence"] = prior.get("schema_evidence")
        if existing["category_id"] and not category.get("category_id"):
            category["category_id"] = existing["category_id"]
        if existing["terminal_status"] == "terminal" and category.get("terminal") == "unknown":
            category["terminal"] = "terminal"
        observations = list(prior.get("observations") or []) if isinstance(prior, dict) else []
        prior_base = {k: v for k, v in prior.items() if k != "observations"} if isinstance(prior, dict) else {}
        incoming_base = {k: v for k, v in incoming.items() if k != "observations"} if isinstance(incoming, dict) else {}
        for evidence in (prior_base, incoming_base):
            if evidence and evidence not in observations:
                observations.append(evidence)
        if category.get("marketplace") == "facebook":
            # Diagnostic snapshots contain surrounding form text (including
            # seller details) that is not taxonomy evidence. Keep only the
            # selected label and verification context in this registry.
            for observation in observations:
                if isinstance(observation, dict):
                    for key in ("visible_options", "first_visible_options", "seller_details"):
                        observation.pop(key, None)
        category["evidence"] = _json({**incoming_base, "observations": observations})
        prior_verified = _timestamp(existing["last_verified_at"])
        incoming_verified = _timestamp(category.get("verified"))
        category["verified"] = max((v for v in (prior_verified, incoming_verified) if v), default=None)
        if existing["verification_status"] == "authoritatively_verified":
            category["eligible"] = bool(category["eligible"] or existing["verification_status"] == "authoritatively_verified")

    insert = text("""
        INSERT INTO marketplace_taxonomy_categories
          (version_id,marketplace,category_key,category_id,category_name,parent_category_key,parent_category_id,
           full_path,path_json,depth,terminal_status,publishability_status,required_attributes_json,
           optional_attributes_json,recommended_attributes_json,conditional_attributes_json,data_source,
           source_evidence_json,last_verified_at,verification_status,staleness_status,automated_selection_eligible)
        VALUES
          (:version_id,:marketplace,:category_key,:category_id,:category_name,:parent_key,:parent_id,
           :path,:path_json,:depth,:terminal,:publishability,:required,:optional,:recommended,:conditional,
           :source,:evidence,:verified,:verification,:staleness,:eligible)
        ON CONFLICT(version_id,category_key) DO UPDATE SET
          category_id=excluded.category_id,category_name=excluded.category_name,parent_category_key=excluded.parent_category_key,
          parent_category_id=excluded.parent_category_id,full_path=excluded.full_path,path_json=excluded.path_json,
          depth=excluded.depth,terminal_status=excluded.terminal_status,publishability_status=excluded.publishability_status,
          required_attributes_json=excluded.required_attributes_json,optional_attributes_json=excluded.optional_attributes_json,
          recommended_attributes_json=excluded.recommended_attributes_json,conditional_attributes_json=excluded.conditional_attributes_json,
          data_source=excluded.data_source,source_evidence_json=excluded.source_evidence_json,last_verified_at=excluded.last_verified_at,
          verification_status=excluded.verification_status,staleness_status=excluded.staleness_status,
          automated_selection_eligible=excluded.automated_selection_eligible
    """)
    for offset in range(0, len(categories), 500):
        db.execute(insert, categories[offset:offset + 500])


def _sync_ebay(db: Session, selected: set[str] | None, aspects_cache: dict) -> list[dict]:
    if selected and "ebay" not in selected:
        return []
    if not _table_exists(db, "ebay_taxonomy_categories"):
        return []
    tree_rows = db.execute(text("""
        SELECT marketplace,category_tree_id,category_tree_version,MAX(source) AS source,
               COUNT(*) AS node_count,MAX(last_synced_at) AS last_synced_at
          FROM ebay_taxonomy_categories
         WHERE source NOT LIKE 'deprecated%'
         GROUP BY marketplace,category_tree_id,category_tree_version
         ORDER BY marketplace,category_tree_id,category_tree_version
    """)).mappings().all()
    results = []
    for tree in tree_rows:
        marketplace = "ebay"
        scope = f"{tree['marketplace']}:{tree['category_tree_id']}"
        if selected and marketplace not in selected:
            continue
        native_version = str(tree["category_tree_version"] or "unknown")
        version_id = f"ebay:{scope}:{native_version}"
        checked_at = _timestamp(tree["last_synced_at"])
        stale = _ebay_tree_staleness(db, str(tree["marketplace"]), checked_at, native_version)
        rows = db.execute(text("""
            SELECT category_id,category_name,parent_category_id,leaf,full_path,depth,last_synced_at,source
              FROM ebay_taxonomy_categories
             WHERE marketplace=:marketplace AND category_tree_id=:tree AND category_tree_version=:version
               AND source NOT LIKE 'deprecated%'
             ORDER BY depth,full_path
        """), {"marketplace": tree["marketplace"], "tree": str(tree["category_tree_id"]), "version": native_version}).mappings().all()
        categories = []
        for row in rows:
            category_id = str(row["category_id"])
            path = [part.strip() for part in str(row["full_path"] or "").split(" > ") if part.strip()]
            cache = aspects_cache.get((str(tree["marketplace"]), str(tree["category_tree_id"]), native_version, category_id))
            groups = cache["groups"] if cache else (None, None, None, None)
            terminal = bool(row["leaf"])
            verification = "authoritatively_verified" if "TAXONOMY" in str(row["source"] or "").upper() or "FULL_TREE" in str(row["source"] or "").upper() else "imported_unverified"
            evidence = {
                "source_table": "ebay_taxonomy_categories", "source_record": str(row["source"] or "unknown"),
                "provider": "eBay Taxonomy API" if verification == "authoritatively_verified" else "legacy_local_import",
                "marketplace_id": str(tree["marketplace"]), "category_tree_id": str(tree["category_tree_id"]),
                "category_tree_version": native_version,
                "aspect_cache": ({"source_version": cache["source_version"], "staleness_status": cache["staleness"]} if cache else None),
            }
            eligible = verification == "authoritatively_verified" and terminal and stale == "fresh"
            categories.append({
                "version_id": version_id, "marketplace": marketplace,
                "category_key": f"{scope}:{category_id}", "category_id": category_id,
                "category_name": str(row["category_name"]),
                "parent_key": f"{scope}:{row['parent_category_id']}" if row["parent_category_id"] is not None else None,
                "parent_id": str(row["parent_category_id"]) if row["parent_category_id"] is not None else None,
                "path": str(row["full_path"] or ""), "path_json": _json(path), "depth": int(row["depth"] or len(path)),
                "terminal": "terminal" if terminal else "branch",
                "publishability": "publishable" if terminal and verification == "authoritatively_verified" else "not_publishable" if not terminal else "unknown",
                "required": _json(groups[0]) if groups[0] is not None else None,
                "optional": _json(groups[1]) if groups[1] is not None else None,
                "recommended": _json(groups[2]) if groups[2] is not None else None,
                "conditional": _json(groups[3]) if groups[3] is not None else None,
                "source": str(row["source"] or "ebay_taxonomy"), "evidence": _json(evidence),
                "verified": _timestamp(row["last_synced_at"]) if verification == "authoritatively_verified" else None,
                "verification": verification, "staleness": stale, "eligible": eligible,
            })
        tree_cache = None
        if _table_exists(db, "marketplace_metadata_cache"):
            tree_cache = db.execute(text("SELECT payload,updated_at FROM marketplace_metadata_cache WHERE cache_key=:key ORDER BY updated_at DESC LIMIT 1"),
                                    {"key": f"ebay:{tree['marketplace']}:category_tree"}).mappings().first()
        tree_payload = _load_json(tree_cache["payload"], {}) if tree_cache else {}
        cached_version, cached_count = _cached_tree_node_count(tree_payload)
        provider_evidence = tree_payload.get("_posterpro_taxonomy_evidence") if isinstance(tree_payload, dict) else {}
        full_tree_evidence = cached_version == native_version and cached_count == len(categories)
        version = {
            "version_id": version_id, "marketplace": marketplace, "scope_key": scope,
            "native_version": native_version, "source": str(tree["source"] or "ebay_taxonomy"),
            "source_url": "https://developer.ebay.com/api-docs/sell/static/metadata/sell-categories.html",
            "evidence": {"provider": "eBay Taxonomy API", "category_tree_id": str(tree["category_tree_id"]),
                         "category_tree_version": native_version, "legacy_table_source": str(tree["source"] or "unknown"),
                         "tree_retrieved_at": (provider_evidence or {}).get("tree_retrieved_at"),
                         "version_checked_at": (provider_evidence or {}).get("version_checked_at")},
            "captured_at": checked_at, "verification_status": "authoritatively_verified" if all(c["verification"] == "authoritatively_verified" for c in categories) else "imported_unverified",
            "completeness_status": "complete" if full_tree_evidence else "partial",
            "staleness_status": stale,
            "last_checked_at": _timestamp((provider_evidence or {}).get("version_checked_at")) if full_tree_evidence else None,
            "notes": "Full local tree cache and normalized rows agree on version and node count." if full_tree_evidence else "Local categories are official-source records, but tree completeness is unproven until a full provider response is counted.",
        }
        _store_version(db, version, categories)
        results.append({"marketplace": "ebay", "scope": scope, "version_id": version_id, "categories": len(categories), "staleness_status": stale})
    return results


def _sync_mercari(db: Session, selected: set[str] | None) -> list[dict]:
    if selected and "mercari" not in selected:
        return []
    if not _table_exists(db, "mercari_taxonomy_versions") or not _table_exists(db, "mercari_taxonomy_categories"):
        return []
    source_version = db.execute(text("SELECT version_id,marketplace,source,source_url,captured_at,verification_status,source_completeness,unresolved_branches,notes FROM mercari_taxonomy_versions ORDER BY captured_at DESC LIMIT 1")).mappings().first()
    if not source_version:
        return []
    source_id = str(source_version["version_id"])
    version_id = f"mercari:{source_id}"
    source_rows = db.execute(text("""
        SELECT source_namespace,source_category_id,category_key,category_name,parent_category_key,
               parent_source_category_id,full_path,path_json,depth,is_leaf,leaf_status,verification_status,
               verification_evidence_job_id,path_verified_at,observed_at,metadata_json
          FROM mercari_taxonomy_categories WHERE version_id=:version ORDER BY depth,full_path
    """), {"version": source_id}).mappings().all()
    diagnostic_attempts: dict[str, list[dict]] = {}
    if _table_exists(db, "marketplace_extension_jobs"):
        attempts = db.execute(text("""SELECT id,status,completed_at,result,payload_snapshot
            FROM marketplace_extension_jobs WHERE marketplace='mercari' AND action='DIAGNOSTIC'
              AND status IN ('COMPLETED','BLOCKED_EXTERNAL','AWAITING_OPERATOR_REVIEW')
            ORDER BY created_at DESC LIMIT 300""")).mappings().all()
        for attempt in attempts:
            result = _load_json(attempt["result"], {})
            snapshot = _load_json(attempt["payload_snapshot"], {})
            requested_path = (((snapshot or {}).get("marketplace_payload") or {}).get("category_path")
                              if isinstance(snapshot, dict) else None)
            if not isinstance(requested_path, list) or not result.get("submission_performed") is False:
                continue
            category_field = next((item for item in (result.get("field_results") or [])
                                   if isinstance(item, dict) and item.get("field") == "category"), {})
            if category_field.get("filled") is True:
                continue
            normalized_path = [str(value).strip() for value in requested_path if str(value).strip()]
            if normalized_path:
                diagnostic_attempts.setdefault(" > ".join(normalized_path).casefold(), []).append({
                    "diagnostic_job_id": int(attempt["id"]), "status": str(attempt["status"]),
                    "completed_at": _timestamp(attempt["completed_at"]),
                    "error_code": category_field.get("error_code"),
                    "interaction_stage": ((category_field.get("interaction_diagnostic") or {}).get("stage")
                                          if isinstance(category_field.get("interaction_diagnostic"), dict) else None),
                    "submission_performed": False, "verified": False,
                })
    categories = []
    for row in source_rows:
        metadata = _load_json(row["metadata_json"], {})
        live_verified = row["verification_status"] == "path_verified" and row["verification_evidence_job_id"] is not None
        verification = "live_browser_verified" if live_verified else "imported_unverified"
        leaf_status = str(row["leaf_status"] or ("confirmed_leaf" if row["is_leaf"] else "unknown"))
        terminal = "terminal" if leaf_status == "confirmed_leaf" and live_verified else "branch" if leaf_status == "branch" else "unknown"
        verified_at = _timestamp(row["path_verified_at"]) if live_verified else None
        staleness = _staleness(verified_at) if verified_at else "unknown"
        external_id = str(row["source_category_id"]) if row["source_category_id"] is not None else None
        category_key = f"{row['source_namespace']}:{row['category_key']}"
        parent_key = f"{row['source_namespace']}:{row['parent_category_key']}" if row["parent_category_key"] else None
        evidence = {"source_table": "mercari_taxonomy_categories", "source_namespace": str(row["source_namespace"]),
                    "source_version_id": source_id, "source_category_id_semantics": (metadata or {}).get("id_semantics"),
                    "verification_evidence_job_id": row["verification_evidence_job_id"], "metadata": metadata}
        attempts_for_path = diagnostic_attempts.get(str(row["full_path"] or "").casefold())
        if attempts_for_path:
            evidence["non_verifying_diagnostic_attempts"] = attempts_for_path
        categories.append({
            "version_id": version_id, "marketplace": "mercari", "category_key": category_key,
            "category_id": external_id, "category_name": str(row["category_name"]),
            "parent_key": parent_key, "parent_id": str(row["parent_source_category_id"]) if row["parent_source_category_id"] is not None else None,
            "path": str(row["full_path"] or ""), "path_json": _json(_load_json(row["path_json"], [])), "depth": int(row["depth"] or 0),
            "terminal": terminal, "publishability": "publishable" if terminal == "terminal" else "not_publishable" if terminal == "branch" else "unknown",
            "required": None, "optional": None, "recommended": None, "conditional": None,
            "source": str(source_version["source"]), "evidence": _json(evidence), "verified": verified_at,
            "verification": verification, "staleness": staleness,
            "eligible": live_verified and terminal == "terminal" and staleness == "fresh",
        })
    version = {
        "version_id": version_id, "marketplace": "mercari", "scope_key": str(source_version["marketplace"] or "mercari_us"),
        "native_version": source_id, "source": str(source_version["source"]), "source_url": source_version["source_url"],
        "evidence": {"source_table": "mercari_taxonomy_versions", "source_version_id": source_id,
                     "source_completeness": str(source_version["source_completeness"] or "partial")},
        "captured_at": _timestamp(source_version["captured_at"]),
        "clear_last_checked": True,
        "verification_status": "live_browser_verified" if any(c["verification"] == "live_browser_verified" for c in categories) else "imported_unverified",
        "completeness_status": "partial" if source_version["source_completeness"] != "complete" or source_version["unresolved_branches"] else "complete",
        "staleness_status": "unknown",
        "notes": str(source_version["notes"] or "Existing Mercari source dataset; absence from this partial import does not establish that a destination category is unavailable."),
    }
    _store_version(db, version, categories)
    return [{"marketplace": "mercari", "scope": version["scope_key"], "version_id": version_id,
             "categories": len(categories), "completeness_status": version["completeness_status"]}]


def _sync_facebook_observations(db: Session, selected: set[str] | None) -> list[dict]:
    """Capture only category labels actually selected and read back in diagnostics.

    Facebook's ordinary Marketplace selector has no registered provider tree in
    this installation. A selected label therefore remains an observed UI
    candidate with unknown ancestry/leaf status; visible sibling options are
    stored as evidence, never promoted into asserted parent-child rows.
    """
    if selected and "facebook" not in selected:
        return []
    if not _table_exists(db, "marketplace_extension_jobs"):
        return []
    jobs = db.execute(text("""SELECT id,user_id,status,completed_at,result,payload_snapshot
        FROM marketplace_extension_jobs WHERE marketplace='facebook' AND action='DIAGNOSTIC'
          AND status='COMPLETED' AND completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 500""")).mappings().all()
    grouped: dict[int, dict[str, dict]] = {}
    latest: dict[int, datetime] = {}
    for job in jobs:
        result = _load_json(job["result"], {})
        if not isinstance(result, dict) or result.get("submission_performed") is not False:
            continue
        field = next((item for item in result.get("field_results", []) if isinstance(item, dict) and item.get("field") == "category"), None)
        if not field or field.get("filled") is not True:
            continue
        proof = field.get("interaction_diagnostic") if isinstance(field.get("interaction_diagnostic"), dict) else {}
        if str(proof.get("stage") or "").upper() not in {"SELECTION_VERIFIED", "CATEGORY_PERSISTENCE_VERIFIED"}:
            continue
        label = str(field.get("verified_value") or "").strip()
        label = re.sub(r"^category\s+", "", label, flags=re.I).strip()
        if not label or len(label) > 120:
            continue
        user_id = int(job["user_id"])
        current = _timestamp(job["completed_at"]) or datetime.now(UTC).replace(tzinfo=None)
        latest[user_id] = max(current, latest.get(user_id, current))
        key = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-") or f"observed-{job['id']}"
        grouped.setdefault(user_id, {})[key] = {
            "label": label, "job_id": int(job["id"]), "completed_at": current,
            "selected_option": str(proof.get("selected_option") or label)[:160],
            "page_url": (result.get("page_url") or "")[:300],
        }
    snapshots = []
    for user_id, observations in grouped.items():
        scope = f"facebook:account-user-{user_id}:ui-observed"
        version_id = f"facebook:{scope}:{max(item['job_id'] for item in observations.values())}"
        categories = []
        for key, observation in observations.items():
            label = observation["label"]
            categories.append({
                "version_id": version_id, "marketplace": "facebook", "category_key": f"user-{user_id}:{key}",
                "category_id": None, "category_name": label, "parent_key": None, "parent_id": None,
                "path": label, "path_json": _json([label]), "depth": 1, "terminal": "unknown",
                "publishability": "unknown", "required": None, "optional": None, "recommended": None,
                "conditional": None, "source": "authenticated_facebook_marketplace_form_diagnostic",
                "evidence": _json({"diagnostic_job_id": observation["job_id"], "completed_at": observation["completed_at"],
                                   "page_url": observation["page_url"],
                                   "selected_option": observation["selected_option"],
                                   "category_id_status": "not_exposed", "ancestry_status": "unknown",
                                   "terminal_status": "unknown", "submission_performed": False}),
                "verified": observation["completed_at"], "verification": "live_browser_verified",
                "staleness": _staleness(observation["completed_at"]), "eligible": False,
            })
        _store_version(db, {
            "version_id": version_id, "marketplace": "facebook", "scope_key": scope,
            "native_version": None, "source": "authenticated_facebook_marketplace_form_diagnostic",
            "source_url": "https://www.facebook.com/marketplace/create/item",
            "evidence": {"account_user_id": user_id, "diagnostic_job_ids": [item["job_id"] for item in observations.values()],
                         "tree_coverage": "observed_selection_labels_only", "no_catalog_api_evidence": True},
            "captured_at": min(item["completed_at"] for item in observations.values()),
            "last_checked_at": latest[user_id], "verification_status": "live_browser_verified",
            "completeness_status": "partial", "staleness_status": _staleness(latest[user_id]),
            "notes": "Only selected form labels were verified. No category IDs, hierarchy, terminal behavior, or catalog API access were observed.",
        }, categories)
        snapshots.append({"marketplace": "facebook", "scope": scope, "version_id": version_id,
                          "categories": len(categories), "completeness_status": "partial"})
    return snapshots


def synchronize_local_taxonomies(db: Session, marketplaces: list[str] | None = None) -> dict[str, Any]:
    """Copy existing local marketplace snapshots; never contacts a provider or browser."""
    selected = {str(value).strip().lower() for value in marketplaces or MARKETPLACES}
    invalid = selected - set(MARKETPLACES)
    if invalid:
        raise ValueError(f"Unsupported marketplace(s): {', '.join(sorted(invalid))}")
    if not _table_exists(db, "marketplace_taxonomy_versions") or not _table_exists(db, "marketplace_taxonomy_categories"):
        raise RuntimeError("Apply migration 20261023_marketplace_taxonomy_registry.sql before synchronizing taxonomy sources")
    aspects = _ebay_aspect_cache(db) if "ebay" in selected else {}
    imported = _sync_ebay(db, selected, aspects) + _sync_mercari(db, selected) + _sync_facebook_observations(db, selected)
    if _table_exists(db, "marketplace_taxonomy_mappings"):
        from app.services.canonical_taxonomy_mapping import invalidate_stale_mappings
        for marketplace in selected:
            invalidate_stale_mappings(db, marketplace=marketplace)
    db.commit()
    present = {item["marketplace"] for item in imported}
    return {
        "status": "synchronized",
        "source_mode": "local_snapshots_only",
        "provider_requests": 0,
        "snapshots": imported,
        "unavailable_marketplaces": [market for market in sorted(selected) if market not in present],
    }


def _facebook_scope_filter(owner_user_id: int | None, *, alias: str = "") -> tuple[str, dict[str, Any]]:
    scope_column = f"{alias}.scope_key" if alias else "scope_key"
    if owner_user_id is None:
        return "1=1", {}
    return f"({scope_column} NOT LIKE 'facebook:account-user-%' OR {scope_column} LIKE :owner_scope)", {
        "owner_scope": f"facebook:account-user-{int(owner_user_id)}:%",
    }


def taxonomy_registry_status(db: Session, *, owner_user_id: int | None = None) -> dict[str, Any]:
    if not _table_exists(db, "marketplace_taxonomy_versions"):
        return {"status": "migration_required", "migration": "20261023_marketplace_taxonomy_registry.sql", "marketplaces": []}
    scope_filter, scope_params = _facebook_scope_filter(owner_user_id, alias="v")
    rows = db.execute(text(f"""
        SELECT v.marketplace,v.scope_key,v.version_id,v.native_version,v.source,v.captured_at,
               v.last_checked_at,v.verification_status,v.completeness_status,v.staleness_status,
               v.category_count,v.terminal_count,v.unresolved_count,v.is_current,v.notes
          FROM marketplace_taxonomy_versions v
         WHERE v.is_current=TRUE AND (v.marketplace!='facebook' OR ({scope_filter}))
         ORDER BY v.marketplace,v.scope_key
    """), scope_params).mappings().all()
    by_market: dict[str, list[dict]] = {market: [] for market in MARKETPLACES}
    for row in rows:
        by_market.setdefault(str(row["marketplace"]), []).append(dict(row))
    result = []
    for marketplace in MARKETPLACES:
        versions = by_market.get(marketplace, [])
        for version in versions:
            detail = db.execute(text("""SELECT
                COUNT(*) AS discovered_category_count,
                COUNT(*) FILTER (WHERE terminal_status='branch') AS branch_count,
                COUNT(*) FILTER (WHERE terminal_status='terminal') AS terminal_candidate_count,
                COUNT(*) FILTER (WHERE terminal_status='unknown') AS unresolved_terminal_count,
                COUNT(*) FILTER (WHERE staleness_status='stale') AS stale_category_count,
                COUNT(*) FILTER (WHERE terminal_status='terminal' AND publishability_status='publishable'
                    AND verification_status IN ('authoritatively_verified','live_browser_verified')
                    AND staleness_status='fresh') AS verified_publishable_leaf_count,
                COUNT(*) FILTER (WHERE required_attributes_json IS NOT NULL
                    AND TRIM(required_attributes_json) NOT IN ('','null','[]','{}')) AS required_attribute_schema_count,
                COUNT(*) FILTER (WHERE terminal_status='terminal' AND publishability_status='publishable'
                    AND verification_status IN ('authoritatively_verified','live_browser_verified')
                    AND staleness_status='fresh' AND required_attributes_json IS NOT NULL
                    AND TRIM(required_attributes_json) NOT IN ('','null','[]','{}')) AS verified_leaf_schema_count,
                COUNT(*) FILTER (WHERE source_evidence_json LIKE '%"aspect_cache":{"%"staleness_status":"stale"%') AS schema_stale_count,
                COUNT(*) FILTER (WHERE source_evidence_json LIKE '%"aspect_cache":{"%'
                    AND source_evidence_json NOT LIKE '%"aspect_cache":{"%"staleness_status":"stale"%'
                    AND required_attributes_json IS NOT NULL AND (
                      TRIM(required_attributes_json) NOT IN ('','null','[]','{}')
                      OR TRIM(COALESCE(optional_attributes_json,'[]')) NOT IN ('','null','[]','{}')
                      OR TRIM(COALESCE(recommended_attributes_json,'[]')) NOT IN ('','null','[]','{}')
                      OR TRIM(COALESCE(conditional_attributes_json,'[]')) NOT IN ('','null','[]','{}'))) AS schema_complete_count,
                COUNT(*) FILTER (WHERE source_evidence_json LIKE '%"aspect_cache":{"%'
                    AND source_evidence_json NOT LIKE '%"aspect_cache":{"%"staleness_status":"stale"%'
                    AND required_attributes_json IS NOT NULL
                    AND TRIM(required_attributes_json) IN ('[]','[ ]')
                    AND TRIM(COALESCE(optional_attributes_json,'[]')) IN ('[]','[ ]')
                    AND TRIM(COALESCE(recommended_attributes_json,'[]')) IN ('[]','[ ]')
                    AND TRIM(COALESCE(conditional_attributes_json,'[]')) IN ('[]','[ ]')) AS schema_verified_empty_count,
                COUNT(*) FILTER (WHERE source_evidence_json LIKE '%"aspect_cache":{"%'
                    AND source_evidence_json NOT LIKE '%"aspect_cache":{"%"staleness_status":"stale"%'
                    AND required_attributes_json IS NULL) AS schema_incomplete_count,
                COUNT(*) FILTER (WHERE (source_evidence_json NOT LIKE '%"aspect_cache":{"%'
                    AND source_evidence_json NOT LIKE '%"attribute_schema":{"%')
                    OR source_evidence_json IS NULL) AS schema_uncollected_count
                FROM marketplace_taxonomy_categories WHERE version_id=:version"""),
                {"version": version["version_id"]}).mappings().one()
            for key in ("discovered_category_count", "branch_count", "terminal_candidate_count",
                        "verified_publishable_leaf_count", "required_attribute_schema_count",
                        "verified_leaf_schema_count", "stale_category_count", "schema_stale_count",
                        "schema_complete_count", "schema_verified_empty_count",
                        "schema_incomplete_count", "schema_uncollected_count"):
                version[key] = int(detail[key] or 0)
            version["unresolved_terminal_count"] = int(detail["unresolved_terminal_count"] or 0)
            version["unresolved_branch_count"] = int(version.get("unresolved_count") or 0)
        mapping_counts: dict[str, int] = {}
        if owner_user_id is not None and _table_exists(db, "marketplace_taxonomy_mappings"):
            mapped = db.execute(text("""SELECT m.marketplace,COUNT(*) AS count
                FROM marketplace_taxonomy_mappings m
                JOIN marketplace_taxonomy_versions v ON v.version_id=m.taxonomy_version_id AND v.is_current=TRUE
                JOIN marketplace_taxonomy_categories c ON c.version_id=m.taxonomy_version_id AND c.category_key=m.category_key
                WHERE m.owner_user_id=:owner AND m.marketplace=:marketplace AND m.invalidated_at IS NULL
                  AND m.verification_status='operator_confirmed' AND c.terminal_status='terminal'
                  AND c.publishability_status='publishable' AND c.verification_status IN ('authoritatively_verified','live_browser_verified')
                  AND c.staleness_status='fresh'
                GROUP BY m.marketplace"""), {"owner": owner_user_id, "marketplace": marketplace}).mappings().all()
            mapping_counts = {str(row["marketplace"]): int(row["count"] or 0) for row in mapped}
        result.append({
            "marketplace": marketplace,
            "coverage_status": "unobserved" if not versions else "partial" if any(v["completeness_status"] != "complete" for v in versions) else "complete",
            "version_count": len(versions),
            "confirmed_reusable_mapping_count": mapping_counts.get(marketplace, 0),
            "inventory_mapping_count": None,
            "inventory_mapping_note": "Listing-to-concept assignments are not counted by the taxonomy registry.",
            "versions": versions,
        })
    return {"status": "available", "marketplaces": result}


def search_registry_categories(db: Session, marketplace: str, *, query: str = "", version_id: str | None = None,
                               leaves_only: bool = False, limit: int = 100,
                               owner_user_id: int | None = None) -> dict[str, Any]:
    normalized = marketplace.strip().lower()
    if normalized not in MARKETPLACES:
        raise ValueError("Marketplace must be ebay, mercari, or facebook")
    params: dict[str, Any] = {"marketplace": normalized, "limit": max(1, min(int(limit), 250))}
    scope_filter, scope_params = _facebook_scope_filter(owner_user_id, alias="sv")
    params.update(scope_params)
    visible_version = "" if normalized != "facebook" else f" AND EXISTS (SELECT 1 FROM marketplace_taxonomy_versions sv WHERE sv.version_id=c.version_id AND ({scope_filter}))"
    version_filter = ""
    if version_id:
        version_filter = " AND c.version_id=:version_id" + visible_version
        params["version_id"] = version_id
    else:
        version_filter = " AND c.version_id IN (SELECT version_id FROM marketplace_taxonomy_versions WHERE marketplace=:marketplace AND is_current=TRUE)" + visible_version
    search_filter = ""
    if query.strip():
        search_filter = " AND (LOWER(c.category_name) LIKE :query OR LOWER(c.full_path) LIKE :query)"
        params["query"] = f"%{query.strip().lower()}%"
    leaf_filter = " AND c.terminal_status='terminal'" if leaves_only else ""
    rows = db.execute(text(f"""
        SELECT c.* FROM marketplace_taxonomy_categories c
         WHERE c.marketplace=:marketplace {version_filter}{search_filter}{leaf_filter}
         ORDER BY c.full_path LIMIT :limit
    """), params).mappings().all()
    output = []
    for row in rows:
        item = dict(row)
        for key in ("path_json", "required_attributes_json", "optional_attributes_json", "recommended_attributes_json", "conditional_attributes_json", "source_evidence_json"):
            target = key.removesuffix("_json")
            item[target] = _load_json(item.pop(key), None)
        output.append(item)
    return {"marketplace": normalized, "items": output, "count": len(output), "limit": params["limit"]}


def rank_taxonomy_candidates(db: Session, *, canonical_concept: str, product_evidence: list[str] | None = None,
                             marketplaces: list[str] | None = None, limit: int = 5,
                             owner_user_id: int | None = None) -> dict[str, Any]:
    """Return review candidates; retrieval score is never mapping proof."""
    selected = [str(value).strip().lower() for value in (marketplaces or MARKETPLACES)]
    if any(value not in MARKETPLACES for value in selected):
        raise ValueError("Marketplace candidates must be ebay, mercari, or facebook")
    concept = str(canonical_concept or "").strip()
    if len(concept) < 2 or len(concept) > 200:
        raise ValueError("Provide a canonical product concept between 2 and 200 characters")
    terms = {word for word in re.findall(r"[a-z0-9]+", " ".join([concept, *(product_evidence or [])]).lower()) if len(word) > 2}
    output = []
    for marketplace in selected:
        scope_filter, scope_params = _facebook_scope_filter(owner_user_id)
        scope_clause = f" AND ({scope_filter})" if marketplace == "facebook" else ""
        versions = db.execute(text(f"""SELECT version_id,native_version,scope_key FROM marketplace_taxonomy_versions
            WHERE marketplace=:marketplace AND is_current=TRUE{scope_clause} ORDER BY scope_key"""),
            {"marketplace": marketplace, **scope_params}).mappings().all()
        candidates = []
        for version in versions:
            rows = db.execute(text("""SELECT category_key,category_id,category_name,full_path,terminal_status,
                    publishability_status,required_attributes_json,verification_status,staleness_status,
                    automated_selection_eligible,data_source,source_evidence_json
                FROM marketplace_taxonomy_categories WHERE marketplace=:marketplace AND version_id=:version
                ORDER BY full_path LIMIT 3000"""), {"marketplace": marketplace, "version": version["version_id"]}).mappings().all()
            for row in rows:
                path_terms = {word for word in re.findall(r"[a-z0-9]+", str(row["full_path"]).lower()) if len(word) > 2}
                overlap = len(terms & path_terms)
                if not overlap:
                    continue
                retrieval = overlap / max(1, len(terms))
                confidence = min(0.35, 0.15 + 0.2 * retrieval)
                candidates.append((retrieval, row, version, confidence))
        candidates.sort(key=lambda item: (-item[0], item[1]["full_path"]))
        formatted = []
        for retrieval, row, version, confidence in candidates[:max(1, min(limit, 10))]:
            formatted.append({
                "marketplace": marketplace, "canonical_concept": concept,
                "category_id": row["category_id"], "category_key": row["category_key"],
                "category_name": row["category_name"], "full_path": row["full_path"],
                "terminal_status": row["terminal_status"], "publishability_status": row["publishability_status"],
                "automated_selection_eligible": bool(row["automated_selection_eligible"]),
                "data_source": row["data_source"], "verification_status": row["verification_status"],
                "staleness_status": row["staleness_status"],
                "taxonomy_version": version["native_version"], "registry_version_id": version["version_id"],
                "mapping_confidence": round(confidence, 3), "candidate_retrieval_score": round(retrieval, 3),
                "evidence_provenance": _load_json(row["source_evidence_json"], {}),
                "required_aspects": _load_json(row["required_attributes_json"], None),
                "automatic_selection_permitted": False, "operator_review_required": True,
                "review_reason": "Text retrieval ranks alternatives only; no verified concept mapping exists.",
            })
        output.append({"marketplace": marketplace, "taxonomy_versions": [dict(v) for v in versions],
                       "candidates": formatted, "status": "review_required" if formatted else "no_candidate"})
    return {"canonical_concept": concept, "mapping_state": "candidate_generation_only",
            "automatic_selection_permitted": False, "marketplaces": output}
