from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response
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
    IntakeVoiceIntelligenceRequest,
    IntakeVoiceTranscriptionRequest,
    IntakeSlateRecoveryRunRequest,
    IntakeSettingsRequest,
    IntakeSlateCreateRequest,
    IntakeSlateUpdateRequest,
    IntakeTimelineReconcileRequest,
    IntakeUnassignedAssignmentRequest,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import IntakePhoto, IntakePhotoBatch, IntakeSession, IntakeSlate, IntakeReconciliationEvent, Listing, User
from app.services.intake_slate import IntakeSlateService
from app.services.google_photos_oauth import (
    GooglePhotosOAuthError,
    build_auth_url,
    exchange_code_for_tokens,
    fetch_userinfo,
    google_photos_redirect_uri,
    get_google_photos_oauth_state,
    parse_oauth_state,
    save_google_photos_connection,
)
from app.workers.tasks import drain_intake_provider_media_task

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
        "metadata_json": row.metadata_json or {},
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
        "slate_id": (row.metadata_json or {}).get("official_slate_id"),
        "thumbnail_url": service.public_media_url(row.local_path),
        "display_url": service.public_media_url(row.local_path),
        "metadata_json": row.metadata_json or {},
        "classification": (row.metadata_json or {}).get("classification") or row.image_type,
        "classification_source": (row.metadata_json or {}).get("classification_source"),
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


def _google_photos_status(current_user: User) -> dict[str, Any]:
    settings_payload = service.settings_for_user(current_user)
    google_state = get_google_photos_oauth_state(current_user)
    album_url = str(settings_payload.get("album_url") or settings_payload.get("folder_id") or "").strip()
    connected = bool(google_state.get("connected"))
    connection_state = str(google_state.get("connection_state") or "not_connected")
    album_id = str(settings_payload.get("google_album_id") or "").strip() or (service._album_identifier(album_url) if album_url and "photos.app.goo.gl" not in album_url else None)
    effective_album_url = str(settings_payload.get("google_album_url") or "").strip() or (f"https://photos.google.com/album/{album_id}" if album_id else album_url)
    last_error = google_state.get("last_error")
    visible_count = None
    if album_url:
        try:
            visible_count = service.google_photos.extract_album_visible_count(album_url)
        except Exception as exc:
            last_error = last_error or str(exc)
            if connection_state == "connected" and not connected:
                connection_state = "album_access_error"
    bridge_health = None
    bridge_configured = False
    try:
        from app.services.automation_bridge import automation_bridge_ready, get_automation_bridge_health

        bridge_configured = automation_bridge_ready()
        if bridge_configured:
            try:
                bridge_health = get_automation_bridge_health()
            except Exception as exc:
                last_error = last_error or str(exc)
    except Exception:
        bridge_configured = False
    return {
        "connected": connected,
        "connection_state": connection_state,
        "account_label": google_state.get("account_email") or google_state.get("account_name") or google_state.get("account_subject") or (album_url or "Not connected"),
        "account_email": google_state.get("account_email"),
        "account_subject": google_state.get("account_subject"),
        "account_name": google_state.get("account_name"),
        "album_url": effective_album_url or None,
        "configured_album_url": album_url or None,
        "album_id": album_id,
        "visible_count": visible_count,
        "has_refresh_token": bool(google_state.get("has_refresh_token")),
        "token_expires_at": google_state.get("token_expires_at"),
        "last_connected_at": google_state.get("last_connected_at"),
        "last_error": last_error,
        "bridge_configured": bridge_configured,
        "bridge_health": bridge_health,
        "label": "PosterPro intake album",
        "last_synced_at": settings_payload.get("last_synced_at"),
        "last_upload_status": (settings_payload.get("last_monitor_result") or {}).get("last_upload_status") if isinstance(settings_payload.get("last_monitor_result"), dict) else None,
    }


@router.get("/google-photos/auth/url")
def get_google_photos_auth_url(
    current_user: User = Depends(get_current_user),
):
    try:
        return {
            "auth_url": build_auth_url(current_user.id),
            "redirect_uri": google_photos_redirect_uri(),
        }
    except GooglePhotosOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/google-photos/connect")
def connect_google_photos(
    current_user: User = Depends(get_current_user),
):
    try:
        auth_url = build_auth_url(current_user.id)
        return RedirectResponse(url=auth_url, status_code=302)
    except GooglePhotosOAuthError as exc:
        return RedirectResponse(
            url="/settings?tab=api-keys#google-photos-oauth&google_photos=missing-config",
            status_code=302,
        )
        raise HTTPException(status_code=400, detail=f"{exc}. Configure Google Photos OAuth client settings in Settings → API Keys.") from exc


@router.get("/google-photos/callback")
async def complete_google_photos_oauth(
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    try:
        user_id = parse_oauth_state(state)
        user = db.get(User, user_id)
        if not user:
            raise GooglePhotosOAuthError("Google OAuth callback user was not found")
        runtime_redirect = google_photos_redirect_uri()
        if not runtime_redirect:
            raise GooglePhotosOAuthError("Missing Google Photos redirect URI")
        token_payload = await exchange_code_for_tokens(code, runtime_redirect)
        userinfo = await fetch_userinfo(str(token_payload.get("access_token") or ""))
        state_payload = save_google_photos_connection(
            user=user,
            db=db,
            token_payload=token_payload,
            userinfo=userinfo,
            redirect_uri=runtime_redirect,
        )
    except GooglePhotosOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from app.core.config import reload_settings

    runtime_settings = reload_settings()
    callback_target = (runtime_settings.app_base_url or "").rstrip("/")
    if callback_target:
        return RedirectResponse(
            url=f"{callback_target}/settings/intake?google_photos=connected&account={state_payload.get('account_email') or ''}",
            status_code=302,
        )
    return {"connected": True, "google_photos": state_payload}


def _voice_suggestion_payload(analysis: dict[str, Any]) -> dict[str, Any]:
    suggested_fields = analysis.get("suggested_fields") if isinstance(analysis.get("suggested_fields"), dict) else {}
    item_specifics = suggested_fields.get("item_specifics") if isinstance(suggested_fields.get("item_specifics"), dict) else {}
    return {
        "title": suggested_fields.get("title"),
        "brand": suggested_fields.get("brand") or item_specifics.get("Brand"),
        "model": suggested_fields.get("model") or item_specifics.get("Model"),
        "condition": suggested_fields.get("condition"),
        "notes": suggested_fields.get("notes"),
        "quantity": suggested_fields.get("quantity") or item_specifics.get("Quantity"),
        "bundle_strategy": suggested_fields.get("bundle_strategy"),
        "marketplace_targets": suggested_fields.get("marketplace_targets") or [],
        "research_queries": suggested_fields.get("research_queries") or [],
        "pricing_instruction": suggested_fields.get("pricing_instruction"),
        "known_product_identity": suggested_fields.get("known_product_identity"),
        "confidence": analysis.get("confidence"),
        "item_specifics": item_specifics,
        "description": suggested_fields.get("description"),
        "category_suggestion": suggested_fields.get("category_suggestion"),
        "missing_information": suggested_fields.get("missing_information") or [],
        "photo_notes": suggested_fields.get("photo_notes") or [],
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
    rendered_asset = None
    rendered_label = None
    bridge_upload = None
    try:
        slate, qr_payload, qr_data_url = service.create_slate(db, user=current_user, payload=payload.model_dump(exclude_none=True))
        try:
            rendered_asset = service.render_slate_preview_asset(qr_payload, item_id=slate.item_id, session_id=slate.session_id)
            rendered_label = service.render_label_preview_asset(qr_payload, item_id=slate.item_id, session_id=slate.session_id)
        except Exception:
            rendered_asset = None
            rendered_label = None
        if rendered_asset is not None:
            try:
                bridge_upload = service.queue_rendered_slate_upload(
                    db,
                    user=current_user,
                    slate=slate,
                    qr_payload=qr_payload,
                    rendered_asset=rendered_asset,
                )
            except Exception:
                bridge_upload = {
                    "status": "BRIDGE_UPLOAD_FAILED",
                    "job_type": "google_photos_upload",
                    "execution_mode": "browser_assist",
                    "target_album_url": None,
                }
        slate = db.get(IntakeSlate, slate.id)
        if slate is not None:
            metadata = dict(slate.metadata_json or {})
            voice_intelligence = metadata.get("voice", {}).get("intelligence") if isinstance(metadata.get("voice"), dict) else None
            metadata["rendered_slate"] = rendered_asset or {}
            metadata["rendered_label"] = rendered_label or {}
            metadata["bridge_upload"] = bridge_upload or {}
            metadata["label_print_status"] = metadata.get("label_print_status") or "pending"
            metadata["voice_processing"] = {
                "recorded": bool((metadata.get("voice") or {}).get("audio")),
                "transcribed": bool((metadata.get("voice") or {}).get("transcript")),
                "enrichment_sent": bool(voice_intelligence),
                "structured_json_received": bool((voice_intelligence or {}).get("structured_listing_json")) if isinstance(voice_intelligence, dict) else False,
                "fields_populated": bool((voice_intelligence or {}).get("title")) if isinstance(voice_intelligence, dict) else False,
                "ai_metadata": (voice_intelligence or {}).get("ai_metadata") if isinstance(voice_intelligence, dict) else {},
            }
            metadata["google_photos"] = {
                **(metadata.get("google_photos") if isinstance(metadata.get("google_photos"), dict) else {}),
                "last_upload_status": (bridge_upload or {}).get("status"),
                "last_upload_error": (bridge_upload or {}).get("error"),
                "target_album_url": (bridge_upload or {}).get("target_album_url"),
                "google_album_url": (bridge_upload or {}).get("google_album_url"),
                "album_id": (bridge_upload or {}).get("album_id"),
                "google_media_id": (bridge_upload or {}).get("google_media_id"),
                "upload_method": (bridge_upload or {}).get("execution_mode"),
            }
            slate.metadata_json = metadata
            db.add(slate)
            db.commit()
            db.refresh(slate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "slate": _serialize_slate(slate),
        "qr_payload": qr_payload,
        "qr_data_url": qr_data_url,
        "rendered_slate_url": rendered_asset["storage_path"] if rendered_asset else None,
        "rendered_slate_data_url": rendered_asset["data_url"] if rendered_asset else None,
        "rendered_label_url": rendered_label["storage_path"] if rendered_label else None,
        "rendered_label_data_url": rendered_label["data_url"] if rendered_label else None,
        "bridge_upload": bridge_upload,
    }


@router.post("/slates/retroactive")
def create_retroactive_intake_slate(
    payload: IntakeSlateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a Slate using an explicit logical photo boundary.

    The existing Slate renderer/upload pipeline is reused; generated time and
    effective boundary are persisted separately so Google display order cannot
    alter PosterPro grouping semantics.
    """
    if not payload.retroactive or (payload.after_photo_id is None and payload.before_photo_id is None):
        raise HTTPException(status_code=400, detail="Retroactive Slate requires after_photo_id or before_photo_id")
    boundary_ids = [value for value in (payload.after_photo_id, payload.before_photo_id) if value is not None]
    boundary_photos = db.execute(select(IntakePhoto).where(IntakePhoto.id.in_(boundary_ids), IntakePhoto.user_id == current_user.id)).scalars().all()
    if len(boundary_photos) != len(set(boundary_ids)):
        raise HTTPException(status_code=400, detail="Boundary photos were not found in your intake timeline")
    by_id = {photo.id: photo for photo in boundary_photos}
    after = by_id.get(payload.after_photo_id); before = by_id.get(payload.before_photo_id)
    if after and before:
        after_key = (after.captured_at or after.imported_at or after.created_at, after.id)
        before_key = (before.captured_at or before.imported_at or before.created_at, before.id)
        if after_key >= before_key:
            raise HTTPException(status_code=400, detail="after_photo_id must precede before_photo_id in capture order")
    effective = payload.effective_boundary_at
    if after and before:
        left = after.captured_at or after.imported_at or after.created_at
        right = before.captured_at or before.imported_at or before.created_at
        if left and right and left < right:
            effective = (left + (right - left) / 2).isoformat()
    elif after:
        effective = (after.captured_at or after.imported_at or after.created_at).isoformat()
    try:
        slate, qr_payload, _ = service.create_slate(db, user=current_user, payload=payload.model_dump(exclude_none=True))
        metadata = dict(slate.metadata_json or {})
        metadata["retroactive_boundary"] = {
            "after_photo_id": payload.after_photo_id,
            "before_photo_id": payload.before_photo_id,
            "effective_boundary_at": effective,
            "generated_at": datetime.now(UTC).isoformat(),
            "type": "HEAD_RETROACTIVE",
        }
        slate.metadata_json = metadata
        db.commit(); db.refresh(slate)
        all_photos = db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id).order_by(IntakePhoto.captured_at, IntakePhoto.id)).scalars().all()
        ids = [p.id for p in all_photos]
        split_at = ids.index(payload.before_photo_id) if payload.before_photo_id in ids else len(ids)
        before_group = all_photos[:split_at]
        after_group = all_photos[split_at:]
        current_batch = next((p.batch_id for p in before_group if p.batch_id and p.batch_id == next((q.batch_id for q in after_group if q.batch_id), None)), None)
        moved = [p.id for p in after_group if current_batch and p.batch_id == current_batch]
        preview = {"status": "preview_ready", "after_photo_id": payload.after_photo_id, "before_photo_id": payload.before_photo_id,
                   "effective_boundary_at": effective, "current_group": {"batch_id": current_batch, "photo_ids": [p.id for p in before_group + after_group if p.batch_id == current_batch]},
                   "proposed_groups": {"before_photo_ids": [p.id for p in before_group if not current_batch or p.batch_id == current_batch], "after_photo_ids": moved},
                   "photos_moved": moved, "affected_drafts": sorted({p.batch_id for p in before_group + after_group if p.batch_id}), "manual_conflicts": [], "draft_invalidation_reason": "PHOTO_GROUP_CHANGED_BY_RETROACTIVE_SLATE"}
        return {"slate": _serialize_slate(slate), "qr_payload": qr_payload, "regroup_preview": preview}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/timeline/regroup/apply")
def apply_retroactive_regroup(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    slate_id = int(payload.get("slate_id") or 0)
    slate = db.get(IntakeSlate, slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")
    if slate.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Slate is not owned by the current user")
    metadata = dict(slate.metadata_json or {})
    boundary = metadata.get("retroactive_boundary") or {}
    before_id = boundary.get("before_photo_id")
    if not before_id:
        raise HTTPException(status_code=400, detail="Retroactive boundary is incomplete")
    boundary_photo = db.get(IntakePhoto, int(before_id))
    if not boundary_photo or boundary_photo.user_id != current_user.id:
        raise HTTPException(status_code=400, detail="Boundary photo is unavailable")
    scope_batch = boundary_photo.batch_id
    photos = db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id, IntakePhoto.batch_id == scope_batch).order_by(IntakePhoto.captured_at, IntakePhoto.id)).scalars().all() if scope_batch else db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id, IntakePhoto.id == boundary_photo.id)).scalars().all()
    idx = next((i for i,p in enumerate(photos) if p.id == int(before_id)), None)
    if idx is None: raise HTTPException(status_code=400, detail="Boundary photo is unavailable")
    target = next((p for p in photos[idx:] if p.batch_id), None)
    affected = [p for p in photos[idx:] if target and p.batch_id == target.batch_id]
    before_state = {"batch_id": scope_batch.id if scope_batch else None, "batch_slate_id": scope_batch.slate_id if scope_batch else None, "photos": {str(p.id): {"batch_id": p.batch_id, "item_id": p.item_id, "official_slate_id": (p.metadata_json or {}).get("official_slate_id")} for p in affected}}
    event = IntakeReconciliationEvent(user_id=current_user.id, event_type="timeline_retroactive_regroup", status="planned", source_media_id=slate.id, details_json={"slate_id": slate.id, "before": before_state, "boundary": boundary})
    db.add(event); db.flush()
    if target:
        scope_batch.slate_id = slate.id; db.add(scope_batch)
    metadata["retroactive_boundary"]["regroup_applied_at"] = datetime.now(UTC).isoformat()
    slate.metadata_json = metadata; db.add(slate); event.status = "completed"; event.details_json = {**(event.details_json or {}), "after": {"batch_id": scope_batch.id if scope_batch else None, "batch_slate_id": scope_batch.slate_id if scope_batch else slate.id, "photos": {str(p.id): {"batch_id": p.batch_id, "item_id": p.item_id, "official_slate_id": (p.metadata_json or {}).get("official_slate_id")} for p in affected}}}; db.commit()
    return {"status":"applied", "slate_id": slate.id, "event_id": event.id, "moved_photo_ids":[p.id for p in affected]}

@router.post("/timeline/regroup/undo")
def undo_retroactive_regroup(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    event_id = int((payload or {}).get("event_id") or 0)
    event = db.get(IntakeReconciliationEvent, event_id)
    if not event or event.user_id != current_user.id or event.event_type != "timeline_retroactive_regroup":
        raise HTTPException(status_code=404, detail="Regroup event not found")
    before = (event.details_json or {}).get("before") or {}
    batch_id = before.get("batch_id")
    if batch_id:
        batch = db.get(IntakePhotoBatch, int(batch_id))
        if batch and batch.user_id == current_user.id:
            batch.slate_id = before.get("batch_slate_id"); db.add(batch)
    photo_states = before.get("photos") if isinstance(before, dict) else before
    restored = []
    for photo_id, state in (photo_states or {}).items():
        photo = db.get(IntakePhoto, int(photo_id))
        if not photo or photo.user_id != current_user.id: continue
        photo.batch_id = state.get("batch_id"); photo.item_id = state.get("item_id"); db.add(photo); restored.append(photo.id)
    event.status = "undone"; event.details_json = {**(event.details_json or {}), "undone_at": datetime.now(UTC).isoformat(), "restored_photo_ids": restored}; db.commit()
    return {"status": "undone", "event_id": event.id, "restored_photo_ids": restored}


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


@router.post("/voice/analyze")
def analyze_voice_intake(
    payload: IntakeVoiceIntelligenceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session_payload = payload.current_session if isinstance(payload.current_session, dict) else {}
    form_payload = payload.current_form if isinstance(payload.current_form, dict) else {}
    slate = None
    if form_payload.get("item_id"):
        slate = db.execute(
            select(IntakeSlate).where(
                IntakeSlate.user_id == current_user.id,
                IntakeSlate.item_id == str(form_payload.get("item_id")).strip(),
            )
        ).scalar_one_or_none()
    result = service.build_voice_intelligence(
        db=db,
        user=current_user,
        slate=slate,
        transcript=str(payload.transcript or "").strip(),
        notes=payload.notes,
        current_form=form_payload,
        current_session=session_payload,
    )
    structured_listing_json = result.get("structured_listing_json") if isinstance(result.get("structured_listing_json"), dict) else None
    marketplace_drafts = result.get("marketplace_drafts") if isinstance(result.get("marketplace_drafts"), dict) else {}
    ai_metadata = result.get("ai_metadata") if isinstance(result.get("ai_metadata"), dict) else {}
    intelligence_state = result.get("intelligence_state") if isinstance(result.get("intelligence_state"), dict) else {}
    validation = {
        "schema_version": result.get("schema_version") or (structured_listing_json.get("schema_version") if isinstance(structured_listing_json, dict) else "posterpro_listing_intelligence_v1"),
        "structured_json_valid": bool(structured_listing_json),
        "errors": ai_metadata.get("validation_errors") or [],
    }
    return {
        "voice_intelligence": result,
        "confidence": result.get("confidence"),
        "suggested_fields": _voice_suggestion_payload(result),
        "structured_listing_json": structured_listing_json or result,
        "marketplace_drafts": marketplace_drafts,
        "ai_metadata": ai_metadata,
        "intelligence_state": intelligence_state,
        "validation": validation,
    }


@router.post("/voice/transcribe")
def transcribe_voice_note(
    payload: IntakeVoiceTranscriptionRequest,
    current_user: User = Depends(get_current_user),
):
    return service.transcribe_voice_audio(
        voice_audio_data_url=payload.voice_audio_data_url,
        fallback_transcript=payload.voice_notes,
    )


@router.post("/slates/{slate_id}/label/print")
def print_intake_label(
    slate_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    slate = db.get(IntakeSlate, slate_id)
    if slate is None or slate.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Slate not found")
    metadata = dict(slate.metadata_json or {})
    history = list(metadata.get("print_history") or [])
    history.append({"action": "print", "requested_at": datetime.utcnow().isoformat(), "copies": int(metadata.get("label_default_copies") or 2), "status": "pending"})
    metadata["print_history"] = history[-20:]
    metadata["label_print_status"] = "pending"
    metadata["label_status"] = "pending"
    slate.metadata_json = metadata
    db.add(slate)
    db.commit()
    db.refresh(slate)
    return {"slate": _serialize_slate(slate), "label_print_status": metadata["label_print_status"], "copies": int(metadata.get("label_default_copies") or 2)}


@router.post("/slates/{slate_id}/label/write-on-box")
def write_intake_label_on_box(
    slate_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    slate = db.get(IntakeSlate, slate_id)
    if slate is None or slate.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Slate not found")
    metadata = dict(slate.metadata_json or {})
    metadata["physical_marking_method"] = "manual"
    metadata["label_status"] = "written_on_box"
    metadata["label_print_status"] = "not_printed"
    metadata["written_on_box_at"] = datetime.utcnow().isoformat()
    slate.metadata_json = metadata
    db.add(slate)
    db.commit()
    db.refresh(slate)
    return {"slate": _serialize_slate(slate), "label_status": metadata["label_status"]}


@router.get("/google-photos/status")
def get_google_photos_status(
    current_user: User = Depends(get_current_user),
):
    return _google_photos_status(current_user)


@router.post("/slates/{slate_id}/bridge-upload")
def retry_intake_slate_bridge_upload(
    slate_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        bridge_upload = service.retry_rendered_slate_upload(db, user=current_user, slate_id=slate_id)
        slate = db.get(IntakeSlate, slate_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "slate": _serialize_slate(slate),
        "bridge_upload": bridge_upload,
    }


@router.get("/qr/{item_ref}")
def resolve_intake_qr(item_ref: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Return the complete operator record encoded by a Slate/label QR."""
    rows = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == current_user.id)).scalars().all()
    slate = next((row for row in rows if str(row.item_id) == item_ref or str((row.qr_payload_json or {}).get("canonical_item_uuid") or "") == item_ref), None)
    if slate is None:
        raise HTTPException(status_code=404, detail="Intake item not found")
    listing = db.get(Listing, slate.listing_id) if slate.listing_id else None
    return {"slate": _serialize_slate(slate), "listing": _serialize_listing(listing), "qr_target": f"/intake/qr/{item_ref}"}


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
        settings_payload = service.settings_for_user(current_user)
        if not settings_payload.get("enabled"):
            raise ValueError("Intake is not enabled for this account.")
        if not str(settings_payload.get("album_url") or settings_payload.get("folder_id") or "").strip():
            raise ValueError("No intake Google Photos album or Drive link is configured.")
        task = drain_intake_provider_media_task.apply_async(args=[current_user.id], countdown=1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "settings": service.settings_for_user(current_user),
        "result": {
            "queued": True,
            "task_name": "drain_intake_provider_media",
            "task_id": task.id,
            "message": "Intake album sync queued for the worker.",
        },
    }


@router.post("/monitor/sync-current")
def sync_intake_album_truth(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        settings_payload = service.settings_for_user(current_user)
        if not settings_payload.get("enabled"):
            raise ValueError("Intake is not enabled for this account.")
        if not str(settings_payload.get("album_url") or settings_payload.get("folder_id") or "").strip():
            raise ValueError("No intake Google Photos album or Drive link is configured.")
        task = drain_intake_provider_media_task.apply_async(args=[current_user.id], countdown=1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "result": {
            "queued": True,
            "task_name": "drain_intake_provider_media",
            "task_id": task.id,
            "message": "Intake album truth sync queued for the worker.",
        },
        "queue": {
            "batches": 0,
            "unassigned_photos": 0,
            "slate_candidates": 0,
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

@router.post("/timeline/classify")
def classify_timeline_assets(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ids = [int(v) for v in (payload.get("photo_ids") or [])]
    classification = str(payload.get("classification") or "PHOTO").upper()
    if classification not in {"PHOTO", "SLATE", "HEAD", "TAIL", "UNKNOWN"}:
        raise HTTPException(status_code=400, detail="Unsupported timeline classification")
    rows = db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id, IntakePhoto.id.in_(ids))).scalars().all()
    before = []
    for row in rows:
        meta = dict(row.metadata_json or {}); before.append({"id": row.id, "metadata_json": meta, "is_slate": row.is_slate, "image_type": row.image_type, "is_internal_only": row.is_internal_only}); meta["classification_source"] = "MANUAL_OPERATOR"; meta["classification"] = classification; row.metadata_json = meta
        row.is_slate = classification != "PHOTO"; row.image_type = classification.lower(); row.is_internal_only = row.is_slate; db.add(row)
        if row.is_slate and not (meta.get("official_slate_id") or (row.batch_id and db.get(IntakePhotoBatch, row.batch_id) and db.get(IntakePhotoBatch, row.batch_id).slate_id)):
            slate_payload = {"retroactive": True, "item_id": str(row.item_id or f"SLATE-{row.id}"), "title": meta.get("title") or row.original_filename or "", "notes": meta.get("notes") or "", "location": meta.get("location") or ""}
            official_slate, _, _ = service.create_slate(db, user=current_user, payload=slate_payload)
            meta["official_slate_id"] = official_slate.id; row.metadata_json = meta; db.add(row)
    db.add(IntakeReconciliationEvent(user_id=current_user.id, event_type="timeline_classification_change", status="completed", details_json={"before": before, "after": {"classification": classification, "photo_ids": ids}, "scope": "selected"}))
    db.commit()
    return {"updated": len(rows), "classification": classification, "official_slate_ids": [(row.metadata_json or {}).get("official_slate_id") for row in rows if (row.metadata_json or {}).get("official_slate_id")]}

@router.post("/timeline/reset-classifications")
def reset_timeline_classifications(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    payload = payload or {}; scope = payload.get("scope") or "all"; preserve = bool(payload.get("preserve_modern", True)); preview = bool(payload.get("preview", False))
    query = select(IntakePhoto).where(IntakePhoto.user_id == current_user.id)
    if scope == "photo_ids": query = query.where(IntakePhoto.id.in_([int(v) for v in payload.get("photo_ids", [])]))
    rows = db.execute(query).scalars().all(); candidates = []
    for row in rows:
        meta = dict(row.metadata_json or {}); classification = str(meta.get("classification") or row.image_type or "").upper(); modern = meta.get("classification_source") in {"SYSTEM_GENERATED", "MODERN_SLATE"} or meta.get("slate_provenance") == "generated";
        if (row.is_slate or classification not in {"", "PHOTO"} or meta.get("slate_detection_result") == "probable_slate_candidate") and not (preserve and modern): candidates.append(row)
    if preview: return {"preview": True, "scope": scope, "affected": len(candidates), "photo_count": sum(1 for r in rows if not r.is_slate), "slate_count": sum(1 for r in rows if r.is_slate), "modern_trusted": sum(1 for r in rows if (r.metadata_json or {}).get("classification_source") in {"SYSTEM_GENERATED", "MODERN_SLATE"})}
    before = []
    for row in candidates:
        meta = dict(row.metadata_json or {}); before.append({"id": row.id, "metadata_json": meta, "is_slate": row.is_slate, "image_type": row.image_type, "is_internal_only": row.is_internal_only}); meta["classification_source"] = "MANUAL_OPERATOR"; meta["previous_classification"] = meta.get("classification") or row.image_type; meta["classification"] = "PHOTO"; row.metadata_json = meta; row.is_slate = False; row.image_type = "photo"; row.is_internal_only = False; db.add(row)
    event = IntakeReconciliationEvent(user_id=current_user.id, event_type="timeline_classification_bulk_reset", status="completed", details_json={"before": before, "scope": scope}); db.add(event); db.commit(); return {"updated": len(candidates), "event_id": event.id}

@router.post("/timeline/classification/undo")
def undo_timeline_classification(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    event_id = payload.get("event_id") if payload else None; q = select(IntakeReconciliationEvent).where(IntakeReconciliationEvent.user_id == current_user.id, IntakeReconciliationEvent.event_type.in_(["timeline_classification_change", "timeline_classification_bulk_reset"])).order_by(IntakeReconciliationEvent.id.desc()); event = db.get(IntakeReconciliationEvent, int(event_id)) if event_id else db.execute(q).scalars().first()
    if not event or event.user_id != current_user.id or event.status == "undone": raise HTTPException(status_code=404, detail="Classification history not found")
    restored = 0
    for snap in (event.details_json or {}).get("before", []):
        row = db.get(IntakePhoto, snap.get("id"));
        if row and row.user_id == current_user.id: row.metadata_json = snap.get("metadata_json") or {}; row.is_slate = snap.get("is_slate"); row.image_type = snap.get("image_type"); row.is_internal_only = snap.get("is_internal_only"); restored += 1
    event.status = "undone"; db.commit(); return {"restored": restored, "event_id": event.id}


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
