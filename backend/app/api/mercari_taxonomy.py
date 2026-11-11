"""Admin-managed, versioned Mercari US taxonomy imports and review tools."""
from __future__ import annotations

from typing import Any
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, is_effective_admin
from app.core.database import get_db
from app.models.models import MarketplaceExtensionJob, User
from app.services import mercari_taxonomy as taxonomy
from app.services.mercari_spreadsheet_export import official_workbook_category_rows

router = APIRouter(prefix="/admin/mercari-taxonomy", tags=["mercari-taxonomy"])
public_router = APIRouter(prefix="/mercari-taxonomy", tags=["mercari-taxonomy"])


def _require_admin(user: User) -> None:
    if not is_effective_admin(user):
        raise HTTPException(status_code=403, detail="Platform admin access required")


@public_router.get("/categories")
def operator_categories(q: str = Query("", max_length=200), leaves_only: bool = False,
                        limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    """Read-only reference lookup; imported/inferred nodes remain labelled as such."""
    del current_user
    try:
        rows = taxonomy.search_categories(db, q, leaves_only=leaves_only, limit=limit)
        return {"items": rows, "coverage": taxonomy.taxonomy_status(db).get("coverage_status", "unknown")}
    except Exception as exc:
        if "mercari_taxonomy" in str(exc).lower() or "no such table" in str(exc).lower():
            return {"items": [], "coverage": "not_acquired"}
        raise


class ImportRequest(BaseModel):
    payload: Any
    source: str = Field(default=taxonomy.APIFY_SOURCE, max_length=120)
    source_url: str | None = None
    verification_status: str = Field(default="unverified", pattern="^(unverified|partial)$")
    source_completeness: str = Field(default="partial", pattern="^(partial|complete)$")
    version_id: str | None = Field(default=None, max_length=80)


class MappingRequest(BaseModel):
    mapping_key: str = Field(min_length=1, max_length=255)
    mapping_label: str = Field(min_length=1, max_length=500)
    version_id: str
    category_key: str
    rationale: str | None = None


class VerifyRequest(BaseModel):
    version_id: str
    category_key: str | None = None
    category_path: list[str] | None = None
    diagnostic_job_id: int = Field(gt=0)


@router.get("/status")
def status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        return taxonomy.taxonomy_status(db)
    except Exception as exc:
        if "mercari_taxonomy_versions" in str(exc).lower() or "no such table" in str(exc).lower():
            return {"marketplace": "mercari_us", "version_count": 0, "latest": None, "versions": [],
                    "coverage_status": "migration_required", "migration": "20261019_mercari_taxonomy.sql"}
        raise


@router.post("/imports", status_code=201)
def import_dataset(payload: ImportRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        return taxonomy.import_taxonomy(db, payload.payload, source=payload.source, source_url=payload.source_url,
                                        verification_status=payload.verification_status, created_by=current_user.id,
                                        version_id=payload.version_id,
                                        source_complete=payload.source_completeness == "complete")
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback(); raise


@router.post("/official-workbook", status_code=201)
async def import_official_workbook(template: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Import only category IDs actually present in Mercari's uploaded workbook."""
    _require_admin(current_user)
    if not (template.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="Upload Mercari's official .xlsx template.")
    raw = await template.read()
    if not raw or len(raw) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Template must be non-empty and smaller than 15 MB.")
    fingerprint = hashlib.sha256(raw).hexdigest()
    try:
        rows = official_workbook_category_rows(raw)
        version_id = f"mercari-sheet-{fingerprint[:24]}"
        prior = db.execute(text("SELECT version_id,node_count,leaf_count,source_completeness FROM mercari_taxonomy_versions WHERE version_id=:v"), {"v": version_id}).mappings().first()
        if prior:
            return {"version_id": prior["version_id"], "source": "mercari_official_spreadsheet_template",
                    "node_count": int(prior["node_count"] or 0), "spreadsheet_compatible_id_count": len(rows),
                    "source_completeness": prior["source_completeness"], "template_sha256": fingerprint,
                    "already_imported": True,
                    "note": "This exact workbook was imported previously; no duplicate taxonomy version was created."}
        result = taxonomy.import_taxonomy(
            db, {"categories": rows, "manifest": {"source": "official Mercari spreadsheet template", "note": f"Workbook SHA-256 {fingerprint}; imported category IDs retain the spreadsheet namespace."}},
            source="mercari_official_spreadsheet_template",
            source_url="https://www.mercari.com/us/help_center/article/554/",
            verification_status="authoritatively_verified", created_by=current_user.id,
            version_id=version_id, source_complete=False,
        )
        return {**result, "template_sha256": fingerprint, "spreadsheet_compatible_id_count": len(rows), "source_completeness": "partial", "note": "Official spreadsheet IDs are verified for import use; category leaf status remains unknown unless explicit template evidence establishes it."}
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback(); raise


@router.get("/categories")
def categories(q: str = Query("", max_length=200), leaves_only: bool = False, limit: int = Query(50, ge=1, le=200),
              version_id: str | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return {"items": taxonomy.search_categories(db, q, leaves_only=leaves_only, limit=limit, version_id=version_id)}


@router.get("/export")
def export(fmt: str = Query("json", pattern="^(json|csv)$"), version_id: str | None = None,
           db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    result = taxonomy.export_categories(db, version_id=version_id, fmt=fmt)
    media = "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8"
    suffix = "csv" if fmt == "csv" else "json"
    return Response(result, media_type=media, headers={"Content-Disposition": f"attachment; filename=mercari-us-taxonomy.{suffix}"})


@router.post("/resolve")
def resolve(body: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    query = " ".join(str(body.get(key) or "") for key in ("title", "description", "inventory_category"))[:2000]
    return taxonomy.resolve_category(db, mapping_key=body.get("mapping_key"), query=query, limit=10)


@router.post("/verify")
def verify(payload: VerifyRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    job = db.get(MarketplaceExtensionJob, payload.diagnostic_job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Mercari diagnostic not found for this operator")
    if bool(payload.category_key) == bool(payload.category_path):
        raise HTTPException(status_code=422, detail="Provide exactly one of category_key or category_path")
    try:
        if payload.category_key:
            return taxonomy.verify_category_from_diagnostic(db, version_id=payload.version_id,
                                                            category_key=payload.category_key, job=job)
        return taxonomy.record_observed_path_from_diagnostic(db, version_id=payload.version_id,
                                                             category_path=payload.category_path or [], job=job)
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/mappings", status_code=201)
def save_mapping(payload: MappingRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        taxonomy.approve_mapping(db, payload.mapping_key.strip(), payload.mapping_label.strip(), payload.version_id,
                                 payload.category_key, current_user.id, payload.rationale)
        return {"status": "saved", "mapping_key": payload.mapping_key}
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
