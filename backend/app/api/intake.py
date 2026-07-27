from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    IntakeBatchDraftRequest,
    IntakeBoundaryApplyRequest,
    IntakeBatchMergeRequest,
    IntakeBatchSplitRequest,
    IntakeFactUpdateRequest,
    IntakePhotoCorrectionRequest,
    IntakeSessionCreateRequest,
    IntakeSlateRecoveryRunRequest,
    IntakeSettingsRequest,
    IntakeSlateCreateRequest,
    IntakeSlateUpdateRequest,
    IntakeTimelineReconcileRequest,
    IntakeUnassignedAssignmentRequest,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import IntakePhoto, IntakePhotoBatch, IntakeSession, IntakeSlate, Listing, User
from app.services.intake_slate import IntakeSlateService

router = APIRouter(prefix="/intake", tags=["intake"])
service = IntakeSlateService()


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _serialize_session(row: IntakeSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "session_id": row.session_id,
        "name": row.name,
        "source_album_id": row.source_album_id,
        "source_folder_id": row.source_folder_id,
        "default_location": row.default_location,
        "item_prefix": row.item_prefix,
        "box_prefix": row.box_prefix,
        "status": row.status,
        "metadata_json": row.metadata_json or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _serialize_slate(row: IntakeSlate | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "intake_session_id": row.intake_session_id,
        "session_id": row.session_id,
        "item_id": row.item_id,
        "box_id": row.box_id,
        "location": row.location,
        "title": row.title,
        "brand": row.brand,
        "model": row.model,
        "condition": row.condition,
        "notes": row.notes,
        "flaws": row.flaws,
        "weight": row.weight,
        "length": row.length,
        "width": row.width,
        "height": row.height,
        "packed": bool(row.packed),
        "internal_notes": row.internal_notes,
        "qr_payload_json": row.qr_payload_json or {},
        "slate_image_id": row.slate_image_id,
        "listing_id": row.listing_id,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _serialize_photo(row: IntakePhoto) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "source_provider": row.source_provider,
        "source_photo_id": row.source_photo_id,
        "source_album_id": row.source_album_id,
        "source_folder_id": row.source_folder_id,
        "original_filename": row.original_filename,
        "local_path": row.local_path,
        "downloaded_url": row.downloaded_url,
        "content_hash": row.content_hash,
        "captured_at": _iso(row.captured_at),
        "uploaded_at": _iso(row.uploaded_at),
        "imported_at": _iso(row.imported_at),
        "image_type": row.image_type,
        "is_slate": bool(row.is_slate),
        "is_public_listing_candidate": bool(row.is_public_listing_candidate),
        "is_internal_only": bool(row.is_internal_only),
        "item_id": row.item_id,
        "batch_id": row.batch_id,
        "thumbnail_url": service.public_media_url(row.local_path),
        "display_url": service.public_media_url(row.local_path),
        "metadata_json": row.metadata_json or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _serialize_listing(row: Listing | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "title": row.title,
        "status": str(row.status),
        "listing_price": row.listing_price,
        "suggested_price": row.suggested_price,
        "category_id": row.category_id,
        "category_suggestion": row.category_suggestion,
        "condition": row.condition,
        "needs_review": bool(row.needs_review),
        "image_urls": row.image_urls or [],
        "marketplace_data": row.marketplace_data or {},
        "source_metadata": row.source_metadata or {},
        "updated_at": _iso(row.updated_at),
    }


def _serialize_batch(row: IntakePhotoBatch, *, slate: IntakeSlate | None, listing: Listing | None, photos: list[IntakePhoto], warnings: list[str]) -> dict[str, Any]:
    slate_photo = next((photo for photo in photos if photo.id == row.slate_id), None)
    return {
        "id": row.id,
        "user_id": row.user_id,
        "intake_session_id": row.intake_session_id,
        "session_id": row.session_id,
        "item_id": row.item_id,
        "slate_id": row.slate_id,
        "first_photo_id": row.first_photo_id,
        "last_photo_id": row.last_photo_id,
        "photo_count": row.photo_count,
        "public_photo_count": row.public_photo_count,
        "internal_photo_count": row.internal_photo_count,
        "draft_listing_id": row.draft_listing_id,
        "status": row.status,
        "metadata_json": row.metadata_json or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "slate": _serialize_slate(slate),
        "listing": _serialize_listing(listing),
        "photos": [_serialize_photo(photo) for photo in photos],
        "warnings": warnings,
        "first_public_photo": next((_serialize_photo(photo) for photo in photos if photo.is_public_listing_candidate), None),
        "slate_photo": _serialize_photo(slate_photo) if slate_photo else None,
    }


@router.get("/settings")
def get_intake_settings(
    current_user: User = Depends(get_current_user),
):
    return service.settings_for_user(current_user)


@router.put("/settings")
def update_intake_settings(
    payload: IntakeSettingsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.save_settings(
        db=db,
        user=current_user,
        payload=payload.model_dump(mode="json", exclude_none=True),
    )


@router.get("/sessions")
def list_intake_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sessions = service.list_sessions(db, user_id=current_user.id)
    return {"sessions": [_serialize_session(row) for row in sessions]}


@router.post("/sessions")
def create_intake_session(
    payload: IntakeSessionCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = service.get_or_create_session(db, user=current_user, payload=payload.model_dump(exclude_none=True))
    return {"session": _serialize_session(session)}


@router.post("/slates")
def create_intake_slate(
    payload: IntakeSlateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        slate, qr_payload, qr_data_url = service.create_slate(db, user=current_user, payload=payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "slate": _serialize_slate(slate),
        "qr_payload": qr_payload,
        "qr_data_url": qr_data_url,
    }


@router.patch("/slates/{slate_id}")
def update_intake_slate(
    slate_id: int,
    payload: IntakeSlateUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        slate = service.update_slate(db, user_id=current_user.id, slate_id=slate_id, payload=payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"slate": _serialize_slate(slate)}


@router.get("/queue")
def get_intake_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payload = service.queue_items(db, user_id=current_user.id)
    items = payload.get("batches") or []
    return {
        "batches": [
            _serialize_batch(
                item["batch"],
                slate=item.get("slate"),
                listing=item.get("listing"),
                photos=item.get("photos") or [],
                warnings=item.get("warnings") or [],
            )
            for item in items
        ],
        "unassigned_photos": [_serialize_photo(photo) for photo in (payload.get("unassigned_photos") or [])],
        "available_slates": [_serialize_slate(row) for row in (payload.get("available_slates") or [])],
        "slate_candidates": [_serialize_photo(photo) for photo in (payload.get("slate_candidates") or [])],
    }


@router.post("/monitor/run")
def run_intake_monitor(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = service.monitor_google_album(db, user=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "settings": service.settings_for_user(current_user),
        "result": result,
    }


@router.post("/monitor/sync-current")
def sync_intake_album_truth(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = service.sync_google_album_truth(db, user=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = service.queue_items(db, user_id=current_user.id)
    return {
        "result": result,
        "queue": {
            "batches": len(payload.get("batches") or []),
            "unassigned_photos": len(payload.get("unassigned_photos") or []),
            "slate_candidates": len(payload.get("slate_candidates") or []),
        },
    }


@router.post("/slates/reconcile-marked")
def reconcile_marked_slate_photos(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = service.reconcile_marked_slate_photos(db, user=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = service.queue_items(db, user_id=current_user.id)
    return {
        "result": result,
        "queue": {
            "batches": len(payload.get("batches") or []),
            "unassigned_photos": len(payload.get("unassigned_photos") or []),
            "slate_candidates": len(payload.get("slate_candidates") or []),
            "available_slates": len(payload.get("available_slates") or []),
        },
    }


@router.post("/unassigned/assign")
def assign_unassigned_photos(
    payload: IntakeUnassignedAssignmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        batch = service.assign_unassigned_photos_to_item(
            db,
            user_id=current_user.id,
            item_id=str(payload.item_id or "").strip(),
            photo_ids=payload.photo_ids or [],
            mark_ready_for_draft=bool(payload.mark_ready_for_draft),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == current_user.id, IntakeSlate.item_id == batch.item_id)).scalar_one_or_none()
    listing = db.get(Listing, batch.draft_listing_id) if batch.draft_listing_id else None
    photos = db.execute(select(IntakePhoto).where(IntakePhoto.batch_id == batch.id).order_by(IntakePhoto.imported_at.asc(), IntakePhoto.id.asc())).scalars().all()
    return {
        "batch": _serialize_batch(
            batch,
            slate=slate,
            listing=listing,
            photos=photos,
            warnings=service._batch_warnings(slate=slate, batch=batch, listing=listing, photos=photos),
        )
    }


@router.post("/photos/boundaries/apply")
def apply_intake_boundaries(
    payload: IntakeBoundaryApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = service.apply_photo_boundaries(
            db,
            user_id=current_user.id,
            boundaries=[
                {"photo_id": row.photo_id, "item_id": str(row.item_id or "").strip()}
                for row in (payload.boundaries or [])
            ],
            mark_ready_for_draft=bool(payload.mark_ready_for_draft),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refreshed = service.queue_items(db, user_id=current_user.id)
    return {
        "result": result,
        "queue": {
            "batches": len(refreshed.get("batches") or []),
            "unassigned_photos": len(refreshed.get("unassigned_photos") or []),
            "slate_candidates": len(refreshed.get("slate_candidates") or []),
        },
    }


@router.post("/batches/{batch_id}/draft")
def draft_intake_batch(
    batch_id: int,
    payload: IntakeBatchDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        listing = service.regenerate_batch_listing(
            db,
            user_id=current_user.id,
            batch_id=batch_id,
            force=bool(payload.force_regenerate),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"listing": _serialize_listing(listing)}


@router.patch("/photos/{photo_id}")
def correct_intake_photo(
    photo_id: int,
    payload: IntakePhotoCorrectionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        photo = service.update_photo(db, user_id=current_user.id, photo_id=photo_id, payload=payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"photo": _serialize_photo(photo)}


@router.post("/batches/{batch_id}/split")
def split_intake_batch(
    batch_id: int,
    payload: IntakeBatchSplitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        batch = service.split_batch(
            db,
            user_id=current_user.id,
            batch_id=batch_id,
            photo_ids=payload.photo_ids,
            new_item_id=payload.new_item_id,
            new_box_id=payload.new_box_id,
            location=payload.location,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refreshed = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.id == batch.id)).scalar_one()
    return {"batch": _serialize_batch(refreshed, slate=None, listing=None, photos=[], warnings=[])}


@router.post("/batches/merge")
def merge_intake_batches(
    payload: IntakeBatchMergeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        batch = service.merge_batches(
            db,
            user_id=current_user.id,
            source_batch_ids=payload.source_batch_ids,
            target_item_id=payload.target_item_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refreshed = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.id == batch.id)).scalar_one()
    return {"batch": _serialize_batch(refreshed, slate=None, listing=None, photos=[], warnings=[])}


@router.get("/export.csv")
def export_intake_csv(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    csv_content = service.export_csv(db, user_id=current_user.id)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="posterpro-intake.csv"'},
    )


@router.get("/timeline")
def intake_timeline(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {
        "items": [
            {
                "photo": _serialize_photo(row["photo"]),
                "timeline_key": row["timeline_key"],
                "late_arrival": row["late_arrival"],
            }
            for row in service.timeline_items(db, user_id=current_user.id)
        ]
    }


@router.post("/timeline/reconcile")
def reconcile_intake_timeline(
    payload: IntakeTimelineReconcileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.reconcile_timeline(
            db,
            user_id=current_user.id,
            photo_id=payload.photo_id,
            full_integrity_scan=bool(payload.full_integrity_scan),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/integrity-scan")
def run_intake_integrity_scan(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.run_integrity_scan(db, user=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backfill-canonical-items")
def backfill_intake_canonical_items(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"result": service.backfill_canonical_items(db, user_id=current_user.id)}


@router.post("/slate-recovery/run")
def run_deterministic_slate_recovery(
    payload: IntakeSlateRecoveryRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = service.run_slate_recovery_candidates(
        db,
        user_id=current_user.id,
        photo_ids=payload.photo_ids,
        limit=payload.limit,
        pipeline_version=payload.pipeline_version,
    )
    return {
        "result": result,
        "candidates_persisted": True,
        "assignments_changed": False,
        "listings_changed": False,
        "publication_actions_available": False,
        "message": "Candidates were persisted. No assignments were changed. No listings were changed.",
    }


@router.put("/items/{item_id}/facts/{field_name}")
def update_canonical_item_fact(
    item_id: str,
    field_name: str,
    payload: IntakeFactUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        fact = service.set_canonical_fact(
            db,
            user_id=current_user.id,
            item_id=item_id,
            field_name=field_name,
            value=payload.value,
            lock=bool(payload.lock),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "fact": {
            "id": fact.id,
            "field_name": fact.field_name,
            "value": fact.value_json,
            "source_type": fact.source_type,
            "precedence": fact.precedence,
            "locked": fact.is_locked,
            "current": fact.is_current,
        }
    }
