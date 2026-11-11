"""Tenant-scoped canonical product concepts and evidence-backed category mappings."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.services import marketplace_taxonomy_registry as registry


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value: Any, fallback: Any):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_name(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _ensure_tables(db: Session) -> None:
    inspector = inspect(db.get_bind())
    if not inspector.has_table("canonical_product_concepts") or not inspector.has_table("marketplace_taxonomy_mappings"):
        raise RuntimeError("Apply migration 20261024_canonical_taxonomy_mappings.sql before using canonical taxonomy mappings")


def create_or_update_concept(db: Session, *, owner_user_id: int, concept_name: str,
                             concept_description: str | None = None,
                             structured_facts: dict | None = None,
                             evidence: list[str] | None = None) -> dict:
    _ensure_tables(db)
    normalized = _normalize_name(concept_name)
    if len(normalized) < 2:
        raise ValueError("Canonical concept name must contain at least two letters or numbers")
    concept_id = "concept-" + hashlib.sha256(f"{owner_user_id}:{normalized}".encode()).hexdigest()[:32]
    row = db.execute(text("SELECT * FROM canonical_product_concepts WHERE owner_user_id=:owner AND normalized_name=:name"),
                     {"owner": owner_user_id, "name": normalized}).mappings().first()
    now = _now()
    incoming_facts = structured_facts if isinstance(structured_facts, dict) else {}
    incoming_evidence = {"signals": [str(value).strip()[:300] for value in (evidence or []) if str(value).strip()][:30]}
    if row:
        prior_facts = _load(row["structured_facts_json"], {})
        prior_evidence = _load(row["evidence_json"], {})
        signals = list(prior_evidence.get("signals") or [])
        for signal in incoming_evidence["signals"]:
            if signal not in signals:
                signals.append(signal)
        facts = {**(prior_facts if isinstance(prior_facts, dict) else {}), **incoming_facts}
        db.execute(text("""UPDATE canonical_product_concepts SET concept_description=COALESCE(:description,concept_description),
            structured_facts_json=:facts,evidence_json=:evidence,updated_by=:user,updated_at=:now
            WHERE concept_id=:id AND owner_user_id=:owner"""), {
            "description": concept_description, "facts": _json(facts),
            "evidence": _json({**(prior_evidence if isinstance(prior_evidence, dict) else {}), "signals": signals}),
            "user": owner_user_id, "now": now, "id": row["concept_id"], "owner": owner_user_id,
        })
        concept_id = str(row["concept_id"])
    else:
        db.execute(text("""INSERT INTO canonical_product_concepts
            (concept_id,owner_user_id,normalized_name,concept_name,concept_description,structured_facts_json,evidence_json,
             classification_status,created_by,updated_by,created_at,updated_at)
            VALUES (:id,:owner,:name,:label,:description,:facts,:evidence,'candidate',:user,:user,:now,:now)"""), {
            "id": concept_id, "owner": owner_user_id, "name": normalized, "label": str(concept_name).strip(),
            "description": concept_description, "facts": _json(incoming_facts), "evidence": _json(incoming_evidence),
            "user": owner_user_id, "now": now,
        })
    db.commit()
    return get_concept(db, owner_user_id=owner_user_id, concept_id=concept_id)


def get_concept(db: Session, *, owner_user_id: int, concept_id: str) -> dict:
    _ensure_tables(db)
    row = db.execute(text("SELECT * FROM canonical_product_concepts WHERE concept_id=:id AND owner_user_id=:owner"),
                     {"id": concept_id, "owner": owner_user_id}).mappings().first()
    if not row:
        raise LookupError("Canonical product concept was not found in this operator account")
    item = dict(row)
    item["structured_facts"] = _load(item.pop("structured_facts_json"), {})
    item["evidence"] = _load(item.pop("evidence_json"), {})
    return item


def concept_candidates(db: Session, *, owner_user_id: int, concept_id: str,
                       marketplaces: list[str] | None = None, limit: int = 5) -> dict:
    concept = get_concept(db, owner_user_id=owner_user_id, concept_id=concept_id)
    facts = concept.get("structured_facts") or {}
    evidence = (concept.get("evidence") or {}).get("signals") or []
    evidence.extend(str(value) for value in facts.values() if isinstance(value, (str, int, float)))
    result = registry.rank_taxonomy_candidates(db, canonical_concept=concept["concept_name"],
        product_evidence=evidence, marketplaces=marketplaces, limit=limit,
        owner_user_id=owner_user_id)
    result["concept_id"] = concept_id
    return result


def confirm_category_mapping(db: Session, *, owner_user_id: int, concept_id: str, marketplace: str,
                             version_id: str, category_key: str, rationale: str,
                             evidence: dict | None = None) -> dict:
    _ensure_tables(db)
    concept = get_concept(db, owner_user_id=owner_user_id, concept_id=concept_id)
    market = str(marketplace or "").strip().lower()
    if market not in registry.MARKETPLACES:
        raise ValueError("Marketplace must be ebay, mercari, or facebook")
    row = db.execute(text("""SELECT c.*,v.native_version,v.scope_key,v.is_current
        FROM marketplace_taxonomy_categories c JOIN marketplace_taxonomy_versions v ON v.version_id=c.version_id
        WHERE c.marketplace=:market AND c.version_id=:version AND c.category_key=:key"""),
        {"market": market, "version": version_id, "key": category_key}).mappings().first()
    if not row or not row["is_current"]:
        raise ValueError("Category mapping is stale; choose a category from the current taxonomy version")
    if row["terminal_status"] != "terminal" or row["publishability_status"] != "publishable":
        raise ValueError("Only a currently verified terminal leaf can be confirmed as a reusable mapping")
    if row["verification_status"] not in {"authoritatively_verified", "live_browser_verified"} or row["staleness_status"] != "fresh":
        raise ValueError("Category verification is missing or stale; confirm the destination category first")
    if market == "facebook" and str(row["scope_key"]).startswith("facebook:account-user-"):
        observed_owner = re.search(r"facebook:account-user-(\d+):", str(row["scope_key"]))
        if observed_owner and int(observed_owner.group(1)) != owner_user_id:
            raise ValueError("This Facebook category observation belongs to a different authenticated operator session")
    if len(str(rationale or "").strip()) < 8:
        raise ValueError("Provide a brief rationale describing why this destination category fits the concept")
    now = _now()
    mapping_id = "mapping-" + hashlib.sha256(f"{owner_user_id}:{concept_id}:{market}:{version_id}:{category_key}".encode()).hexdigest()[:40]
    mapping_evidence = {"verification_source": row["data_source"],
        "category_evidence": _load(row["source_evidence_json"], {}),
        "operator_evidence": evidence if isinstance(evidence, dict) else {},
        "confirmation": "operator_selected_current_verified_leaf"}
    db.execute(text("""INSERT INTO marketplace_taxonomy_mappings
        (mapping_id,owner_user_id,concept_id,marketplace,taxonomy_version_id,taxonomy_native_version,category_key,
         category_id,category_path,mapping_rationale,evidence_json,confidence_score,verification_status,
         operator_confirmed,confirmed_by,confirmed_at,last_validated_at,created_at,updated_at)
        VALUES (:id,:owner,:concept,:market,:version,:native,:key,:category_id,:path,:rationale,:evidence,1.0,
          'operator_confirmed',TRUE,:user,:now,:now,:now,:now)
        ON CONFLICT(mapping_id) DO UPDATE SET mapping_rationale=excluded.mapping_rationale,evidence_json=excluded.evidence_json,
          confidence_score=1.0,verification_status='operator_confirmed',operator_confirmed=TRUE,
          confirmed_by=excluded.confirmed_by,confirmed_at=excluded.confirmed_at,last_validated_at=excluded.last_validated_at,
          invalidated_at=NULL,invalidation_reason=NULL,updated_at=excluded.updated_at"""), {
        "id": mapping_id, "owner": owner_user_id, "concept": concept_id, "market": market,
        "version": version_id, "native": row["native_version"], "key": category_key,
        "category_id": row["category_id"], "path": row["full_path"], "rationale": str(rationale).strip(),
        "evidence": _json(mapping_evidence), "user": owner_user_id, "now": now,
    })
    db.commit()
    return get_mapping(db, owner_user_id=owner_user_id, mapping_id=mapping_id)


def get_mapping(db: Session, *, owner_user_id: int, mapping_id: str) -> dict:
    _ensure_tables(db)
    row = db.execute(text("SELECT * FROM marketplace_taxonomy_mappings WHERE owner_user_id=:owner AND mapping_id=:id"),
                     {"owner": owner_user_id, "id": mapping_id}).mappings().first()
    if not row:
        raise LookupError("Category mapping was not found in this operator account")
    item = dict(row)
    item["evidence"] = _load(item.pop("evidence_json"), {})
    return item


def list_concept_mappings(db: Session, *, owner_user_id: int, concept_id: str) -> list[dict]:
    get_concept(db, owner_user_id=owner_user_id, concept_id=concept_id)
    invalidate_stale_mappings(db, owner_user_id=owner_user_id)
    rows = db.execute(text("""SELECT * FROM marketplace_taxonomy_mappings
        WHERE owner_user_id=:owner AND concept_id=:concept ORDER BY marketplace,created_at DESC"""),
        {"owner": owner_user_id, "concept": concept_id}).mappings().all()
    output = []
    for row in rows:
        item = dict(row)
        item["evidence"] = _load(item.pop("evidence_json"), {})
        output.append(item)
    return output


def invalidate_stale_mappings(db: Session, *, owner_user_id: int | None = None,
                              marketplace: str | None = None) -> int:
    if not inspect(db.get_bind()).has_table("marketplace_taxonomy_mappings"):
        return 0
    clauses = ["verification_status='operator_confirmed'", """NOT EXISTS (
          SELECT 1 FROM marketplace_taxonomy_versions v
          JOIN marketplace_taxonomy_categories c ON c.version_id=v.version_id
          WHERE v.version_id=marketplace_taxonomy_mappings.taxonomy_version_id AND v.is_current=TRUE
            AND c.category_key=marketplace_taxonomy_mappings.category_key AND c.terminal_status='terminal'
            AND c.verification_status IN ('authoritatively_verified','live_browser_verified')
            AND c.staleness_status='fresh')"""]
    params: dict[str, Any] = {"now": _now()}
    if owner_user_id is not None:
        clauses.append("owner_user_id=:owner"); params["owner"] = owner_user_id
    if marketplace is not None:
        clauses.append("marketplace=:market"); params["market"] = marketplace
    result = db.execute(text(f"""UPDATE marketplace_taxonomy_mappings SET
        verification_status='invalidated',operator_confirmed=FALSE,invalidated_at=:now,
        invalidation_reason='Saved taxonomy version is no longer current',updated_at=:now
        WHERE {' AND '.join(clauses)}"""), params)
    if result.rowcount:
        db.commit()
    return int(result.rowcount or 0)
