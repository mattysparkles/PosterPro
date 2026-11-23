"""Read-only taxonomy registry queries and an admin local-source synchronization action."""
from __future__ import annotations

import httpx
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, is_effective_admin
from app.core.database import get_db
from app.models.models import BulkJob, Listing, User
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


class ApplyConfirmedMappingRequest(BaseModel):
    marketplace: str = Field(pattern="^(ebay|facebook|mercari)$")
    source_listing_id: int = Field(gt=0)
    listing_ids: list[int] = Field(min_length=1, max_length=100)


class CategorySuggestionJobRequest(BaseModel):
    listing_ids: list[int] = Field(default_factory=list, max_length=10000)
    all_drafts: bool = False
    marketplaces: list[str] = Field(default_factory=lambda: ["facebook", "ebay", "mercari"], min_length=1, max_length=3)


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


@router.post("/taxonomy-category-suggestion-jobs")
def create_category_suggestion_job(payload: CategorySuggestionJobRequest,
                                   db: Session = Depends(get_db),
                                   current_user: User = Depends(get_current_user)):
    """Queue bounded review-only suggestions for selected listings or all drafts."""
    allowed = {"facebook", "ebay", "mercari"}
    marketplaces = list(dict.fromkeys(str(value).strip().lower() for value in payload.marketplaces))
    if not marketplaces or any(value not in allowed for value in marketplaces):
        raise HTTPException(status_code=422, detail="Marketplaces must be Facebook, eBay, or Mercari")
    if payload.all_drafts and payload.listing_ids:
        raise HTTPException(status_code=422, detail="Choose explicit listing IDs or all drafts, not both")
    if not payload.all_drafts and not payload.listing_ids:
        raise HTTPException(status_code=422, detail="Select listings or explicitly request all drafts")
    if payload.all_drafts:
        listing_ids = [int(row[0]) for row in db.query(Listing.id).filter(
            Listing.user_id == current_user.id, Listing.status.in_(["draft", "ready"]),
        ).order_by(Listing.id).limit(10001).yield_per(1000).all()]
    else:
        listing_ids = list(dict.fromkeys(int(value) for value in payload.listing_ids))
        owned = {int(row[0]) for row in db.query(Listing.id).filter(
            Listing.user_id == current_user.id, Listing.id.in_(listing_ids),
        ).all()}
        if owned != set(listing_ids):
            raise HTTPException(status_code=404, detail="One or more selected listings do not belong to this operator")
    if len(listing_ids) > 10000:
        raise HTTPException(status_code=413, detail="One category suggestion job is limited to 10,000 draft listings")
    job_id = str(uuid.uuid4())
    job = BulkJob(
        id=job_id, user_id=current_user.id, action="suggest_marketplace_categories",
        status="queued", total_items=len(listing_ids), processed_items=0, error_count=0, errors=[],
        filters={"scope": "all_drafts" if payload.all_drafts else "explicit_selection"},
        payload={"listing_ids": listing_ids, "marketplaces": marketplaces, "cursor": 0, "batch_size": 25, "format_version": 1},
    )
    db.add(job)
    db.commit()
    try:
        from app.workers.tasks import process_marketplace_taxonomy_suggestion_job
        process_marketplace_taxonomy_suggestion_job.delay(job_id)
    except Exception as exc:
        job.status = "queued"  # scheduler recovery will resume it
        db.add(job)
        db.commit()
        raise HTTPException(status_code=503, detail="Suggestion job was saved and will be resumed by the worker scheduler") from exc
    return {"job_id": job.id, "action": job.action, "status": job.status,
            "total_items": job.total_items, "processed_items": job.processed_items,
            "error_count": job.error_count, "marketplaces": marketplaces,
            "resume_safe": True, "publishing_performed": False}


@router.get("/taxonomy-category-suggestion-jobs/{job_id}")
def get_category_suggestion_job(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = db.get(BulkJob, job_id)
    if not job or job.action != "suggest_marketplace_categories":
        raise HTTPException(status_code=404, detail="Category suggestion job was not found")
    if int(job.user_id) != int(current_user.id):
        raise HTTPException(status_code=404, detail="Category suggestion job was not found")
    payload = job.payload if isinstance(job.payload, dict) else {}
    return {"job_id": job.id, "status": job.status, "total_items": job.total_items,
            "processed_items": job.processed_items, "error_count": job.error_count,
            "errors": job.errors or [], "marketplaces": payload.get("marketplaces") or [],
            "review_counts": payload.get("review_counts") or {},
            "resume_safe": True, "publishing_performed": False}


@router.post("/taxonomy-mappings/apply-confirmed-cohort")
def apply_confirmed_mapping_to_cohort(payload: ApplyConfirmedMappingRequest,
                                      db: Session = Depends(get_db),
                                      current_user: User = Depends(get_current_user)):
    """Idempotently reuse one confirmed map for exact-identity, same-owner listings."""
    from app.services.inventory_taxonomy_cohorts import _identity

    listing_ids = list(dict.fromkeys(int(value) for value in payload.listing_ids))
    if len(listing_ids) > 100:
        raise HTTPException(status_code=422, detail="Apply at most 100 explicitly selected listings per request")
    if payload.source_listing_id not in listing_ids:
        raise HTTPException(status_code=422, detail="Select the confirmed source listing explicitly; no source listing is added implicitly")

    try:
        # Lock the source and targets in one transaction. This makes retries
        # idempotent and prevents concurrent requests from replacing a manual
        # choice or applying from a source whose confirmation changed in flight.
        targets = db.query(Listing).filter(
            Listing.user_id == current_user.id, Listing.id.in_(listing_ids),
        ).order_by(Listing.id).with_for_update().all()
        if len(targets) != len(listing_ids):
            raise HTTPException(status_code=404, detail="One or more selected listings are missing or belong to another operator")
        by_id = {int(item.id): item for item in targets}
        source = by_id.get(payload.source_listing_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source listing was not found in the explicit selection")
        source_data = source.marketplace_data if isinstance(source.marketplace_data, dict) else {}
        mappings = source_data.get("marketplace_category_mappings") if isinstance(source_data.get("marketplace_category_mappings"), dict) else {}
        confirmed = mappings.get(payload.marketplace) if isinstance(mappings.get(payload.marketplace), dict) else {}
        if (confirmed.get("verification_status") != "operator_confirmed"
                or confirmed.get("terminal_status") != "terminal"
                or confirmed.get("publishability_status") != "publishable"
                or confirmed.get("staleness_status") != "fresh"
                or not confirmed.get("mapping_id")):
            raise HTTPException(status_code=409, detail="Source listing has no current operator-confirmed mapping for this marketplace")

        mapping = mapping_service.get_mapping(
            db, owner_user_id=current_user.id, mapping_id=str(confirmed["mapping_id"]),
        )
        if (mapping.get("marketplace") != payload.marketplace
                or mapping.get("invalidated_at") is not None
                or not bool(mapping.get("operator_confirmed"))
                or mapping.get("verification_status") != "operator_confirmed"):
            raise HTTPException(status_code=409, detail="Confirmed mapping is stale, revoked, or for a different marketplace")
        taxonomy = db.execute(text("""SELECT v.is_current,c.terminal_status,c.publishability_status,
                c.verification_status,c.staleness_status,c.required_attributes_json
          FROM marketplace_taxonomy_versions v
          JOIN marketplace_taxonomy_categories c ON c.version_id=v.version_id
         WHERE v.version_id=:version AND c.category_key=:category"""),
            {"version": mapping["taxonomy_version_id"], "category": mapping["category_key"]}).mappings().first()
        if (not taxonomy or not taxonomy["is_current"] or taxonomy["terminal_status"] != "terminal"
                or taxonomy["publishability_status"] != "publishable"
                or taxonomy["verification_status"] not in {"authoritatively_verified", "live_browser_verified"}
                or taxonomy["staleness_status"] != "fresh"):
            raise HTTPException(status_code=409, detail="The confirmed category is no longer a current verified publishable leaf")

        source_identity = _identity(source)[0]
        if not source_identity.startswith("id:"):
            raise HTTPException(status_code=409, detail="This product has no unambiguous ASIN/UPC/EAN/GTIN/MPN identity; review mappings individually")
        attributes = json.loads(taxonomy["required_attributes_json"]) if taxonomy["required_attributes_json"] else []
        applied: list[int] = []
        already_applied: list[int] = []
        preserved: list[int] = []
        rejected_identity: list[int] = []
        for listing in targets:
            if _identity(listing)[0] != source_identity:
                rejected_identity.append(int(listing.id))
                continue
            data = dict(listing.marketplace_data) if isinstance(listing.marketplace_data, dict) else {}
            current_maps = data.get("marketplace_category_mappings") if isinstance(data.get("marketplace_category_mappings"), dict) else {}
            current = current_maps.get(payload.marketplace) if isinstance(current_maps.get(payload.marketplace), dict) else {}
            if current.get("mapping_id") == mapping["mapping_id"]:
                already_applied.append(int(listing.id))
                continue
            channel = data.get("channels", {}).get(payload.marketplace, {}) if isinstance(data.get("channels"), dict) else {}
            shipping = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
            legacy_mercari = shipping.get("mercari", {}) if payload.marketplace == "mercari" and isinstance(shipping.get("mercari"), dict) else {}
            legacy_category = legacy_mercari.get("category_id") or legacy_mercari.get("category_path") if payload.marketplace == "mercari" else channel.get("category_id") or channel.get("category_path") if isinstance(channel, dict) else None
            ebay_category = listing.category_id if payload.marketplace == "ebay" else None
            if current or legacy_category or (ebay_category and str(ebay_category) != str(mapping.get("category_id") or "")):
                preserved.append(int(listing.id))
                continue
            applied_map = {
                "mapping_id": mapping["mapping_id"], "marketplace": payload.marketplace,
                "category_id": mapping.get("category_id"), "category_key": mapping["category_key"],
                "category_path": mapping["category_path"], "taxonomy_version_id": mapping["taxonomy_version_id"],
                "taxonomy_version": mapping.get("taxonomy_native_version"),
                "confidence": float(mapping.get("confidence_score") or 0),
                "verification_status": "operator_confirmed", "terminal_status": "terminal",
                "publishability_status": "publishable", "staleness_status": "fresh",
                "required_attributes": attributes, "supports_shipping": False,
                "applied_from_listing_id": int(source.id), "reuse_basis": source_identity.split(":", 1)[0],
                "confirmed_at": (mapping.get("confirmed_at").isoformat()
                    if hasattr(mapping.get("confirmed_at"), "isoformat")
                    else str(mapping.get("confirmed_at") or "") or None),
            }
            data["marketplace_category_mappings"] = {**current_maps, payload.marketplace: applied_map}
            if payload.marketplace == "ebay":
                listing.category_id = mapping.get("category_id")
                listing.category_suggestion = mapping.get("category_path")
            data.setdefault("taxonomy_mapping_audit", []).append({
                "mapping_id": mapping["mapping_id"], "marketplace": payload.marketplace,
                "source_listing_id": int(source.id), "action": "bulk_apply_exact_identity",
            })
            listing.marketplace_data = data
            applied.append(int(listing.id))
        db.flush()
        db.commit()
        return {
            "marketplace": payload.marketplace, "mapping_id": mapping["mapping_id"],
            "requested": len(listing_ids), "applied": applied, "already_applied": already_applied,
            "preserved_existing_selection": preserved, "identity_mismatch": rejected_identity,
            "retry_safe": True, "published_or_queued": False,
        }
    except Exception:
        db.rollback()
        raise


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
