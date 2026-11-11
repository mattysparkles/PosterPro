"""Read-only taxonomy registry queries and an admin local-source synchronization action."""
from __future__ import annotations

import httpx
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, is_effective_admin
from app.core.database import get_db
from app.models.models import User
from app.services import canonical_taxonomy_mapping as mapping_service
from app.services import inventory_taxonomy_cohorts
from app.services import marketplace_taxonomy_registry as registry

router = APIRouter(tags=["taxonomy-registry"])


class SynchronizeRequest(BaseModel):
    marketplaces: list[str] = Field(default_factory=lambda: list(registry.MARKETPLACES), max_length=3)


class EbayRefreshRequest(BaseModel):
    marketplaces: list[str] = Field(default_factory=lambda: ["EBAY_US", "EBAY_MOTORS_US"], min_length=1, max_length=2)
    refresh_aspects: bool = True


class CandidateRequest(BaseModel):
    canonical_concept: str = Field(min_length=2, max_length=200)
    product_evidence: list[str] = Field(default_factory=list, max_length=30)
    marketplaces: list[str] = Field(default_factory=lambda: list(registry.MARKETPLACES), max_length=3)
    limit_per_marketplace: int = Field(default=5, ge=1, le=10)


class InventoryCohortCandidatesRequest(BaseModel):
    cohort_key: str = Field(min_length=8, max_length=64)


class CanonicalConceptRequest(BaseModel):
    concept_name: str = Field(min_length=2, max_length=200)
    concept_description: str | None = Field(default=None, max_length=2000)
    structured_facts: dict = Field(default_factory=dict, max_length=50)
    evidence: list[str] = Field(default_factory=list, max_length=30)


class ConfirmMappingRequest(BaseModel):
    marketplace: str
    taxonomy_version_id: str = Field(min_length=1, max_length=200)
    category_key: str = Field(min_length=1, max_length=220)
    rationale: str = Field(min_length=8, max_length=1000)
    evidence: dict = Field(default_factory=dict, max_length=30)


class TraversalCheckpointRequest(BaseModel):
    marketplace: str = Field(pattern="^(facebook|mercari)$")
    source_key: str = Field(min_length=1, max_length=120)
    source_version: str | None = Field(default=None, max_length=160)
    expected_revision: int = Field(ge=0)
    checkpoint: dict


def _require_admin(user: User) -> None:
    if not is_effective_admin(user):
        raise HTTPException(status_code=403, detail="Platform admin access required")


@router.get("/taxonomy-registry/status")
def status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return registry.taxonomy_registry_status(db, owner_user_id=current_user.id)


@router.get("/taxonomy-traversal/checkpoints/{marketplace}/{source_key}")
def get_traversal_checkpoint(marketplace: str, source_key: str,
                             db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if marketplace not in {"facebook", "mercari"}:
        raise HTTPException(status_code=422, detail="Only Facebook and Mercari traversal checkpoints are supported")
    row = db.execute(text("""
        SELECT source_version, checkpoint_json, revision, status, updated_at
          FROM marketplace_taxonomy_traversal_checkpoints
         WHERE owner_user_id=:owner AND marketplace=:marketplace AND source_key=:source_key
    """), {"owner": current_user.id, "marketplace": marketplace, "source_key": source_key}).mappings().first()
    if not row:
        return {"marketplace": marketplace, "source_key": source_key, "revision": 0, "checkpoint": None}
    return {"marketplace": marketplace, "source_key": source_key,
            "source_version": row["source_version"], "revision": row["revision"],
            "status": row["status"], "updated_at": row["updated_at"],
            "checkpoint": json.loads(row["checkpoint_json"])}


@router.put("/taxonomy-traversal/checkpoints")
def save_traversal_checkpoint(payload: TraversalCheckpointRequest,
                              db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    checkpoint = payload.checkpoint
    if (checkpoint.get("marketplace") != payload.marketplace
            or not isinstance(checkpoint.get("frontier"), list)
            or not isinstance(checkpoint.get("categories"), dict)
            or not isinstance(checkpoint.get("completed_paths"), list)):
        raise HTTPException(status_code=422, detail="Checkpoint marketplace/frontier/categories/completed_paths are invalid")
    if checkpoint.get("source_version") != payload.source_version:
        raise HTTPException(status_code=422, detail="Checkpoint source version does not match its persistence scope")
    status_value = str(checkpoint.get("status") or "in_progress")
    if status_value == "complete":
        unresolved = checkpoint.get("unresolved_paths")
        categories = list(checkpoint["categories"].values())
        if unresolved or checkpoint["frontier"]:
            raise HTTPException(status_code=422, detail="A checkpoint with unresolved paths or frontier entries cannot be complete")
        for category in categories:
            if (not isinstance(category, dict)
                    or category.get("terminal_status") not in {"branch", "confirmed_leaf"}
                    or (category.get("terminal_status") == "confirmed_leaf"
                        and category.get("required_fields_schema_status") not in {"verified_complete", "verified_empty"})):
                raise HTTPException(status_code=422, detail="Complete checkpoints require verified branch/leaf and required-field schema evidence")
    elif status_value not in {"in_progress", "checkpointed_with_error", "checkpointed_at_node_limit", "partial_unresolved"}:
        raise HTTPException(status_code=422, detail="Unsupported taxonomy checkpoint status")
    try:
        encoded = json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Checkpoint must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Checkpoint exceeds the 4 MiB safe limit; save a bounded traversal slice")
    existing = db.execute(text("""
        SELECT revision FROM marketplace_taxonomy_traversal_checkpoints
         WHERE owner_user_id=:owner AND marketplace=:marketplace AND source_key=:source_key
    """), {"owner": current_user.id, "marketplace": payload.marketplace,
           "source_key": payload.source_key}).mappings().first()
    if (existing["revision"] if existing else 0) != payload.expected_revision:
        raise HTTPException(status_code=409, detail="Checkpoint changed since it was loaded; reload before saving")
    revision = payload.expected_revision + 1
    params = {"owner": current_user.id, "marketplace": payload.marketplace, "source_key": payload.source_key,
              "source_version": payload.source_version, "checkpoint": encoded,
              "revision": revision, "status": status_value[:32]}
    if existing:
        db.execute(text("""
            UPDATE marketplace_taxonomy_traversal_checkpoints
               SET source_version=:source_version, checkpoint_json=:checkpoint,
                   revision=:revision, status=:status, updated_at=CURRENT_TIMESTAMP
             WHERE owner_user_id=:owner AND marketplace=:marketplace AND source_key=:source_key
        """), params)
    else:
        db.execute(text("""
            INSERT INTO marketplace_taxonomy_traversal_checkpoints
              (owner_user_id,marketplace,source_key,source_version,checkpoint_json,revision,status)
            VALUES (:owner,:marketplace,:source_key,:source_version,:checkpoint,:revision,:status)
        """), params)
    db.commit()
    return {"marketplace": payload.marketplace, "source_key": payload.source_key,
            "source_version": payload.source_version, "revision": revision, "status": params["status"]}


@router.get("/taxonomy-mappings/inventory-cohorts")
def inventory_cohorts(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """Show tenant-scoped inventory cohorts, confirmed mapping reuse, and real readiness."""
    return inventory_taxonomy_cohorts.inventory_taxonomy_cohort_report(
        db, owner_user_id=current_user.id, limit=limit)


@router.post("/taxonomy-mappings/inventory-cohorts/candidates")
def inventory_cohort_candidates(payload: InventoryCohortCandidatesRequest,
                                db: Session = Depends(get_db),
                                current_user: User = Depends(get_current_user)):
    report = inventory_taxonomy_cohorts.inventory_taxonomy_cohort_report(
        db, owner_user_id=current_user.id, limit=100)
    cohort = next((item for item in report["top_cohorts"] if item["cohort_key"] == payload.cohort_key), None)
    if not cohort:
        raise HTTPException(status_code=404, detail="Inventory cohort was not found in this operator account")
    evidence = [*cohort.get("category_hints", {}).keys(), *cohort.get("source_types", {}).keys()]
    result = registry.rank_taxonomy_candidates(
        db, canonical_concept=cohort["label"], product_evidence=evidence,
        marketplaces=list(registry.MARKETPLACES), limit=3, owner_user_id=current_user.id)
    return {"cohort": cohort, "candidate_state": "suggestions_only_operator_confirmation_required", **result}


@router.get("/taxonomy-registry/{marketplace}/categories")
def categories(
    marketplace: str,
    q: str = Query("", max_length=200),
    version_id: str | None = None,
    leaves_only: bool = False,
    limit: int = Query(100, ge=1, le=250),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return registry.search_registry_categories(db, marketplace, query=q, version_id=version_id,
                                                   leaves_only=leaves_only, limit=limit,
                                                   owner_user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/taxonomy-registry/candidates")
def candidates(payload: CandidateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return registry.rank_taxonomy_candidates(db, canonical_concept=payload.canonical_concept,
                                                 product_evidence=payload.product_evidence,
                                                 marketplaces=payload.marketplaces,
                                                 limit=payload.limit_per_marketplace,
                                                 owner_user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/taxonomy-mappings/concepts")
def save_canonical_concept(payload: CanonicalConceptRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return mapping_service.create_or_update_concept(db, owner_user_id=current_user.id,
            concept_name=payload.concept_name, concept_description=payload.concept_description,
            structured_facts=payload.structured_facts, evidence=payload.evidence)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/taxonomy-mappings/concepts/{concept_id}")
def get_canonical_concept(concept_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        concept = mapping_service.get_concept(db, owner_user_id=current_user.id, concept_id=concept_id)
        return {"concept": concept, "mappings": mapping_service.list_concept_mappings(db, owner_user_id=current_user.id, concept_id=concept_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/taxonomy-mappings/concepts/{concept_id}/candidates")
def canonical_concept_candidates(concept_id: str, marketplaces: str | None = None, limit: int = Query(5, ge=1, le=10),
                                 db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    selected = [value.strip() for value in (marketplaces or "ebay,mercari,facebook").split(",") if value.strip()]
    try:
        return mapping_service.concept_candidates(db, owner_user_id=current_user.id, concept_id=concept_id,
                                                  marketplaces=selected, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/taxonomy-mappings/concepts/{concept_id}/mappings")
def confirm_canonical_mapping(concept_id: str, payload: ConfirmMappingRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return mapping_service.confirm_category_mapping(db, owner_user_id=current_user.id, concept_id=concept_id,
            marketplace=payload.marketplace, version_id=payload.taxonomy_version_id,
            category_key=payload.category_key, rationale=payload.rationale, evidence=payload.evidence)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/admin/taxonomy-registry/synchronize")
def synchronize(payload: SynchronizeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        return registry.synchronize_local_taxonomies(db, payload.marketplaces)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/admin/taxonomy-registry/ebay/refresh")
async def refresh_ebay(payload: EbayRefreshRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        from app.services.ebay_taxonomy_refresh import refresh_ebay_taxonomies
        return await refresh_ebay_taxonomies(db, user_id=current_user.id,
                                             marketplaces=payload.marketplaces,
                                             refresh_aspects=payload.refresh_aspects)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"eBay taxonomy refresh failed: {str(exc)[:300]}") from exc


@router.post("/admin/taxonomy-registry/facebook/meta-product-taxonomy/refresh")
async def refresh_facebook_meta_product_taxonomy(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Import Meta's public catalog taxonomy as non-publishable reference data."""
    _require_admin(current_user)
    from app.services.facebook_product_taxonomy import SOURCE_URL, import_meta_product_categories
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(SOURCE_URL)
            response.raise_for_status()
        if len(response.content) > 5 * 1024 * 1024:
            raise ValueError("Meta category reference exceeded the 5 MB import limit")
        return import_meta_product_categories(db, response.content)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Meta product taxonomy download failed: {type(exc).__name__}") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Meta product taxonomy import failed: {type(exc).__name__}") from exc
