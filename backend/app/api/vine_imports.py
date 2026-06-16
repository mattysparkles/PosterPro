from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    VineImportActionRequest,
    VineImportBatchResponse,
    VineImportItemResponse,
    VineImportItemUpdateRequest,
)
from app.core.auth import ensure_user_owns_resource, ensure_vine_access, get_current_user
from app.core.database import get_db
from app.models.models import VineImportBatch, VineImportItem, User
from app.services.vine_import_service import VineImportService

router = APIRouter(prefix="/imports/vine", tags=["vine-imports"])
service = VineImportService()
logger = logging.getLogger(__name__)


@router.post("/upload", response_model=VineImportBatchResponse)
async def upload_vine_report(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_vine_access(current_user)
    payload = await file.read()
    user_id = getattr(current_user, "id", None)
    try:
        batch = service.create_batch_from_upload(
            db,
            current_user=current_user,
            filename=file.filename or "vine-report",
            file_bytes=payload,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Vine report upload parse failed", extra={"upload_filename": file.filename, "user_id": user_id})
        raise HTTPException(status_code=400, detail=f"Vine report upload failed: {exc}") from exc
    items = db.execute(select(VineImportItem).where(VineImportItem.batch_id == batch.id).order_by(VineImportItem.id.asc())).scalars().all()
    return VineImportBatchResponse.model_validate({**batch.__dict__, "items": items})


@router.get("/batches")
def list_vine_batches(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_vine_access(current_user)
    batches = db.execute(
        select(VineImportBatch).where(VineImportBatch.user_id == current_user.id).order_by(VineImportBatch.created_at.desc())
    ).scalars().all()
    return [VineImportBatchResponse.model_validate(batch) for batch in batches]


@router.get("/batches/{batch_id}", response_model=VineImportBatchResponse)
def get_vine_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_vine_access(current_user)
    batch = db.get(VineImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    ensure_user_owns_resource(current_user, batch.user_id)
    items = db.execute(select(VineImportItem).where(VineImportItem.batch_id == batch.id).order_by(VineImportItem.id.asc())).scalars().all()
    return VineImportBatchResponse.model_validate({**batch.__dict__, "items": items})


@router.post("/batches/{batch_id}/fetch-media")
def fetch_vine_media(
    batch_id: int,
    payload: VineImportActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_vine_access(current_user)
    batch = db.get(VineImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    ensure_user_owns_resource(current_user, batch.user_id)
    return service.fetch_media(db, batch=batch, item_ids=payload.item_ids)


@router.post("/batches/{batch_id}/repair-images")
def repair_vine_images(
    batch_id: int,
    payload: VineImportActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_vine_access(current_user)
    batch = db.get(VineImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    ensure_user_owns_resource(current_user, batch.user_id)
    return service.repair_vine_listing_images(
        db,
        user_id=current_user.id,
        batch_id=batch.id,
        listing_ids=payload.item_ids or None,
        include_archived=False,
        force_refresh=True,
        use_bridge_session=True,
        only_missing_images=False,
        limit=None,
    )


@router.post("/batches/{batch_id}/create-inventory")
def create_vine_inventory(
    batch_id: int,
    payload: VineImportActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_vine_access(current_user)
    batch = db.get(VineImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    ensure_user_owns_resource(current_user, batch.user_id)
    return service.create_inventory_records(
        db,
        batch=batch,
        item_ids=payload.item_ids,
        include_locked=payload.include_locked,
        include_cancelled=payload.include_cancelled,
    )


@router.post("/batches/{batch_id}/create-drafts")
def create_vine_drafts(
    batch_id: int,
    payload: VineImportActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_vine_access(current_user)
    batch = db.get(VineImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    ensure_user_owns_resource(current_user, batch.user_id)
    return service.create_listing_drafts(
        db,
        batch=batch,
        item_ids=payload.item_ids,
        include_cancelled=payload.include_cancelled,
        fetch_media_first=payload.fetch_media_first,
        require_media_for_asin=payload.require_media_for_asin,
        allow_drafts_without_media=payload.allow_drafts_without_media,
    )


@router.patch("/items/{item_id}", response_model=VineImportItemResponse)
def update_vine_item(
    item_id: int,
    payload: VineImportItemUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_vine_access(current_user)
    item = db.get(VineImportItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    ensure_user_owns_resource(current_user, item.user_id)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(item, key, value)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.post("/items/{item_id}/retry-discovery", response_model=VineImportItemResponse)
def retry_vine_item_discovery(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_vine_access(current_user)
    item = db.get(VineImportItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    ensure_user_owns_resource(current_user, item.user_id)
    service.retry_item_discovery(db, item=item)
    db.refresh(item)
    return item
