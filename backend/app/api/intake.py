from __future__ import annotations

from datetime import datetime, UTC, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Body, UploadFile, File, Form
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from PIL import Image

from app.api.schemas import (
    IntakeBatchDraftRequest,
    IntakeBoundaryApplyRequest,
    IntakeBatchMergeRequest,
    IntakeBatchSplitRequest,
    IntakeFactUpdateRequest,
    IntakePhotoCorrectionRequest,
    IntakeSessionCreateRequest,
    IntakeVoiceIntelligenceRequest,
    IntakeSlateVoiceNoteRequest,
    IntakeVoiceTranscriptionRequest,
    IntakeSlateRecoveryRunRequest,
    IntakeSettingsRequest,
    IntakeSlateCreateRequest,
    IntakeSlateUpdateRequest,
    IntakeTimelineReconcileRequest,
    IntakeUnassignedAssignmentRequest,
)
from app.core.config import settings
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import IntakePhoto, IntakePhotoBatch, IntakeSession, IntakeSlate, IntakeReconciliationEvent, Listing, Sale, User, InventoryPrintJob, InventoryBin, InventoryBinAssignment, InventoryBinRevision
from secrets import token_urlsafe
from html import escape
from uuid import uuid4
from app.services.intake_slate import IntakeSlateService
from app.services.listing_review import normalize_listing_images
from app.services.google_photos_oauth import (
    GooglePhotosOAuthError,
    build_auth_url,
    exchange_code_for_tokens,
    fetch_userinfo,
    google_photos_redirect_uri,
    google_photos_oauth_ready,
    get_google_photos_oauth_state,
    parse_oauth_state,
    save_google_photos_connection,
)
from app.workers.tasks import drain_intake_provider_media_task

router = APIRouter(prefix="/intake", tags=["intake"])
service = IntakeSlateService()


def _timeline_boundary_photo(db: Session, *, user_id: int, value: Any) -> IntakePhoto | None:
    """Resolve a photo or Slate marker to the nearest real photo boundary."""
    if value in (None, ""):
        return None
    token = str(value)
    if token.startswith("slate-"):
        try:
            slate = db.execute(select(IntakeSlate).where(IntakeSlate.id == int(token.removeprefix("slate-")), IntakeSlate.user_id == user_id)).scalar_one_or_none()
        except (TypeError, ValueError):
            slate = None
        if slate is None:
            return None
        boundary = (slate.metadata_json or {}).get("retroactive_boundary") if isinstance(slate.metadata_json, dict) else {}
        value = slate.slate_image_id or (boundary or {}).get("after_photo_id") or (boundary or {}).get("before_photo_id")
    try:
        photo_id = int(value)
    except (TypeError, ValueError):
        return None
    return db.execute(select(IntakePhoto).where(IntakePhoto.id == photo_id, IntakePhoto.user_id == user_id)).scalar_one_or_none()


def _timeline_insert_capture_time(db: Session, *, user_id: int, after: Any, before: Any) -> tuple[datetime, dict[str, Any]]:
    after_photo = _timeline_boundary_photo(db, user_id=user_id, value=after)
    before_photo = _timeline_boundary_photo(db, user_id=user_id, value=before)
    after_time = after_photo.captured_at if after_photo else None
    before_time = before_photo.captured_at if before_photo else None
    if after_time and before_time and after_time < before_time:
        capture = after_time + (before_time - after_time) / 2
    elif after_time:
        capture = after_time + timedelta(microseconds=1)
    elif before_time:
        capture = before_time - timedelta(microseconds=1)
    else:
        capture = datetime.now(UTC)
    return capture, {"after": after, "before": before}


def _uploaded_capture_time(content: bytes) -> tuple[datetime, dict[str, Any]]:
    """Use EXIF DateTimeOriginal when available, with a clear UTC fallback.

    A device's timezone cannot be recovered from the common EXIF timestamp, so
    callers retain provenance rather than silently pretending it is certain.
    Clock calibration can later adjust the stored provider metadata.
    """
    try:
        exif = Image.open(BytesIO(content)).getexif()
        raw = exif.get(36867) or exif.get(306)
        subseconds = exif.get(37521)
        if raw:
            value = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
            if subseconds:
                micros = str(subseconds).strip().split()[0][:6].ljust(6, "0")
                if micros.isdigit():
                    value = value.replace(microsecond=int(micros))
            return value, {"capture_time_source": "exif", "exif_capture_time": str(raw), "exif_subsecond": str(subseconds) if subseconds else None, "timezone_assumption": "UTC_unverified"}
    except Exception:
        pass
    return datetime.now(UTC), {"capture_time_source": "upload_received"}


def _timeline_media_exists(path: str | None) -> bool:
    """Check a persisted local media URL without treating `/media` as OS root."""
    value = str(path or "").strip()
    if not value:
        return False
    if value.startswith(("https://", "http://", "data:")):
        return True
    if value.startswith("/media/"):
        value = str(service.storage.root / value.removeprefix("/media/"))
    try:
        return Path(value).is_file()
    except OSError:
        return False


def _timeline_marker_media_path(rendered_path: str | None, linked_photo: IntakePhoto | None) -> str | None:
    # The linked photographed Slate is retained as provenance/recovery evidence,
    # never as the active Timeline representation. Active markers use generated
    # modern artwork only; callers can expose the source separately as a legacy link.
    if _timeline_media_exists(rendered_path):
        return service.public_media_url(str(rendered_path))
    return None


def _resolve_timeline_photo_media_path(row: IntakePhoto) -> str:
    """Prefer a durable local image, reanchoring paths from older storage roots.

    Google Photos media URLs expire. Older Timeline rows can also contain an
    absolute path from before LocalStorage's root was made consistent. Search
    only known PosterPro-owned subdirectories and exact filenames; never walk
    the whole media tree for each row.
    """
    root = service.storage.root.resolve()
    raw = str(row.local_path or "").strip()
    candidates: list[Path] = []
    if raw.startswith("/media/"):
        candidates.append(root / raw.removeprefix("/media/"))
    elif raw:
        candidates.append(Path(raw))
        if not Path(raw).is_absolute():
            candidates.append(root / raw)
        parts = Path(raw).parts
        known_dirs = {"intake-google-photos", "google-photos", "intake", "imports", "uploads", "intake-items", "timeline-insertions", "intake-slates"}
        for index, part in enumerate(parts):
            if part in known_dirs and index + 1 < len(parts):
                candidates.append(root.joinpath(*parts[index:]))

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.is_file() and root in resolved.parents:
                return service.public_media_url(str(resolved))
        except OSError:
            continue

    names = [str(row.original_filename or "").strip(), Path(raw).name if raw else ""]
    source_id = str(row.source_photo_id or "").strip()
    if source_id:
        names.extend((source_id, f"{source_id}.jpg", f"{source_id}.jpeg", f"{source_id}.png"))
    for directory in ("intake-google-photos", "google-photos"):
        for name in dict.fromkeys(value for value in names if value):
            candidate = (root / directory / Path(name).name).resolve()
            try:
                if candidate.is_file() and root in candidate.parents:
                    return service.public_media_url(str(candidate))
            except OSError:
                continue
        if source_id:
            # Some older imports saved a collision-suffixed canonical copy
            # while the IntakePhoto row kept a stale/typoed absolute path.
            # Match only the exact normalized Google provider ID prefix in
            # PosterPro's dedicated Google-photo directory.
            source_slug = service._slug_token(source_id)
            def modified_at(path: Path) -> float:
                try:
                    return path.stat().st_mtime
                except OSError:
                    return 0

            matches = sorted(
                (root / directory).glob(f"google-photo-{source_slug}-*"),
                key=modified_at,
                reverse=True,
            ) if (root / directory).is_dir() else []
            for candidate in matches:
                try:
                    resolved = candidate.resolve()
                    if resolved.is_file() and root in resolved.parents and service._verify_image_file(resolved):
                        return service.public_media_url(str(resolved))
                except OSError:
                    continue
    remote = str(row.downloaded_url or "").strip()
    if remote.startswith(("https://", "http://")):
        return remote
    # Never send an unverified server filesystem path to the browser: it is
    # neither a public media URL nor evidence that the file is available.
    return ""


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
        "public_url": (row.qr_payload_json or {}).get("public_url"),
        "bin_code": (row.metadata_json or {}).get("bin_code"),
    }


def _serialize_photo(row: IntakePhoto, *, compact: bool = False) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    metadata = row.metadata_json or {}
    qr_payload = metadata.get("qr_payload") if isinstance(metadata.get("qr_payload"), dict) else {}
    role_value = str(metadata.get("timeline_role") or metadata.get("classification") or qr_payload.get("boundary_position") or row.image_type or "").strip().lower()
    if role_value in {"tail", "tail_slate"}:
        timeline_role = "TAIL"
    elif row.is_slate and role_value in {"head", "head_slate", "start"}:
        timeline_role = "HEAD"
    elif row.is_slate:
        # Older operator classifications persisted only ``is_slate`` and did
        # not record a directional boundary. Never invent HEAD in that case;
        # the timeline must preserve the evidence as an undirected Slate.
        timeline_role = "UNKNOWN"
    else:
        timeline_role = "PHOTO"
    if compact:
        metadata = {
            key: metadata.get(key)
            for key in ("classification", "classification_source", "official_slate_id", "slate_provenance", "timeline_primary", "timeline_primary_source", "timeline_primary_group_id", "timeline_role", "timeline_deleted", "title", "notes", "box_id", "location")
            if metadata.get(key) is not None
        }
    local_path = str(row.local_path or "")
    display_path = _resolve_timeline_photo_media_path(row)
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
        "slate_id": (row.metadata_json or {}).get("official_slate_id") or (row.metadata_json or {}).get("timeline_slate_id"),
        "thumbnail_url": service.public_media_url(display_path),
        "display_url": service.public_media_url(display_path),
        "metadata_json": metadata,
        "classification": metadata.get("classification") or row.image_type,
        "classification_source": metadata.get("classification_source"),
        "timeline_role": timeline_role,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _timeline_role(photo: dict[str, Any]) -> str:
    metadata = photo.get("metadata_json") if isinstance(photo.get("metadata_json"), dict) else {}
    slate = photo.get("slate") if isinstance(photo.get("slate"), dict) else {}
    explicit_role = (
        photo.get("timeline_role")
        or metadata.get("timeline_role")
        or metadata.get("classification")
        or (metadata.get("qr_payload", {}).get("boundary_position") if isinstance(metadata.get("qr_payload"), dict) else None)
        or slate.get("boundary_position")
    )
    role = str(
        explicit_role
        or ""
    ).strip().upper()
    if role in {"TAIL", "TAIL_SLATE"}:
        return "TAIL"
    if photo.get("is_slate") and not explicit_role:
        return "UNKNOWN"
    if photo.get("is_slate") and role in {"UNKNOWN", "UNSPECIFIED", "SLATE"}:
        return "UNKNOWN"
    return "HEAD" if role in {"SLATE", "HEAD", "HEAD_SLATE"} else "PHOTO"


def _assign_timeline_image_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach stable canonical image-group identity without using upload-batch IDs.

    A Head Slate starts a group after itself. A Tail Slate closes the preceding
    group and, when preceding photos were unassigned, assigns that run to the
    Tail Slate's canonical item identity. Existing item IDs and explicit group
    identities remain authoritative.
    """
    active_group: str | None = None
    pending: list[dict[str, Any]] = []
    staged: list[tuple[dict[str, Any], str | None, str]] = []

    def identity(entry: dict[str, Any]) -> str | None:
        photo = entry.get("photo") if isinstance(entry.get("photo"), dict) else {}
        slate = photo.get("slate") if isinstance(photo.get("slate"), dict) else {}
        value = (
            photo.get("image_group_id")
            or photo.get("item_id")
            or photo.get("canonical_group_id")
            or slate.get("item_id")
        )
        if value:
            return f"item:{str(value).strip()}"
        slate_id = photo.get("slate_id") or slate.get("id")
        if slate_id:
            return f"slate:{slate_id}"
        return None

    def flush_pending(fallback: str | None = None) -> None:
        nonlocal pending
        if not pending:
            return
        run_id = fallback or f"photo-run:{(pending[0].get('photo') or {}).get('id', 'unknown')}"
        for pending_entry in pending:
            pending_entry["_assigned_group_id"] = identity(pending_entry) or run_id
        pending = []

    for entry in items:
        photo = entry.get("photo") if isinstance(entry.get("photo"), dict) else {}
        role = _timeline_role(photo)
        photo["timeline_role"] = role
        own_identity = identity(entry)
        if role == "TAIL":
            tail_group = active_group or own_identity
            if tail_group is None and pending:
                tail_group = identity(pending[0])
            if tail_group is None:
                tail_group = f"tail:{photo.get('slate_id') or photo.get('id', 'unknown')}"
            if pending:
                flush_pending(tail_group)
            photo["_assigned_group_id"] = tail_group
            staged.append((entry, tail_group, role))
            active_group = None
            continue
        if role == "HEAD":
            flush_pending()
            active_group = own_identity or f"slate:{photo.get('slate_id') or photo.get('id', 'unknown')}"
            photo["_assigned_group_id"] = active_group
            staged.append((entry, active_group, role))
            continue
        if role == "UNKNOWN":
            # A legacy Slate is still a visual delimiter, but its direction
            # was not persisted. Start a neutral group without pretending it
            # was a Head Slate; an operator can assign HEAD/TAIL explicitly.
            flush_pending()
            active_group = own_identity or f"slate-unknown:{photo.get('slate_id') or photo.get('id', 'unknown')}"
            photo["_assigned_group_id"] = active_group
            staged.append((entry, active_group, role))
            continue
        if active_group is not None:
            photo["_assigned_group_id"] = active_group
            staged.append((entry, active_group, role))
        elif own_identity:
            photo["_assigned_group_id"] = own_identity
            staged.append((entry, own_identity, role))
        else:
            pending.append(entry)
            staged.append((entry, None, role))
    flush_pending()

    for entry, group_id, role in staged:
        photo = entry.get("photo") if isinstance(entry.get("photo"), dict) else {}
        if not group_id:
            group_id = entry.pop("_assigned_group_id", None) or identity(entry) or f"photo:{photo.get('id', 'unknown')}"
        else:
            entry.pop("_assigned_group_id", None)
        entry["image_group_id"] = group_id
        entry["group_role"] = role
        photo["image_group_id"] = group_id

    groups_with_photos = {
        entry.get("image_group_id")
        for entry in items
        if entry.get("image_group_id") and not bool((entry.get("photo") or {}).get("is_slate"))
    }
    first_seen: dict[str, int] = {}
    for entry in items:
        group_id = entry.get("image_group_id")
        if group_id in groups_with_photos and group_id not in first_seen:
            first_seen[group_id] = len(first_seen) + 1
        entry["image_group_index"] = first_seen.get(group_id, 0)
        photo = entry.get("photo") if isinstance(entry.get("photo"), dict) else {}
        photo["group_id"] = group_id
        photo["image_group_index"] = entry["image_group_index"]
        if entry.get("is_slate_marker"):
            photo["slate_number"] = photo.get("slate_number")
        else:
            photo["group_role"] = entry.get("group_role")
    return items


def _refresh_existing_timeline_draft_media(db: Session, *, user_id: int, item_ids: set[str]) -> int:
    """Refresh only already-existing, non-live drafts for affected canonical groups."""
    updated = 0
    for item_id in {str(value).strip() for value in item_ids if str(value).strip()}:
        batches = db.execute(
            select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == user_id, IntakePhotoBatch.item_id == item_id)
        ).scalars().all()
        for batch in batches:
            listing = db.get(Listing, batch.draft_listing_id) if batch.draft_listing_id else None
            if listing is None or service._listing_is_externally_active(db, listing):
                continue
            photos = db.execute(
                select(IntakePhoto).where(
                    IntakePhoto.user_id == user_id,
                    IntakePhoto.item_id == item_id,
                    IntakePhoto.is_public_listing_candidate.is_(True),
                    IntakePhoto.is_slate.is_(False),
                )
            ).scalars().all()
            photos = [photo for photo in photos if not service._timeline_photo_deleted(photo)]
            urls, listing_images = service._materialize_listing_images(
                item_id=item_id,
                title=listing.title or item_id,
                photos=photos,
            )
            listing.image_urls = urls
            listing.listing_images = normalize_listing_images(listing_images=listing_images, approved=True)
            db.add(listing)
            updated += 1
    return updated


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
        "oauth_ready": google_photos_oauth_ready(),
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
    if payload.client_event_id:
        prior = db.execute(
            select(IntakeSlate).where(
                IntakeSlate.user_id == current_user.id,
                IntakeSlate.session_id == payload.session_id,
                IntakeSlate.metadata_json["client_event_id"].as_string() == payload.client_event_id,
            ).limit(1)
        ).scalar_one_or_none()
        if prior is not None:
            stored = prior.metadata_json if isinstance(prior.metadata_json, dict) else {}
            slate_art = stored.get("rendered_slate") if isinstance(stored.get("rendered_slate"), dict) else {}
            label_art = stored.get("rendered_label") if isinstance(stored.get("rendered_label"), dict) else {}
            return {
                "slate": _serialize_slate(prior),
                "qr_payload": prior.qr_payload_json or {},
                "qr_data_url": service.build_qr_data_url(prior.qr_payload_json or {}),
                "rendered_slate_url": slate_art.get("storage_path"),
                "rendered_slate_data_url": None,
                "rendered_label_url": label_art.get("storage_path"),
                "bridge_upload": stored.get("bridge_upload"),
                "replayed": True,
            }
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
            if bool(service.settings_for_user(current_user).get("auto_print_box_labels")):
                copies = int((slate.metadata_json or {}).get("label_default_copies") or 2)
                db.add(InventoryPrintJob(user_id=current_user.id, slate_id=slate.id, listing_id=slate.listing_id,
                    job_type="box_label", copies=max(1, min(copies, 10)),
                    paper_profile=str(service.settings_for_user(current_user).get("box_label_paper_profile") or "receipt_58mm"),
                    payload_json={"public_url": (slate.qr_payload_json or {}).get("public_url"), "item_id": slate.item_id}))
                db.commit()
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
        "replayed": False,
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
    def resolve_marker(value, edge):
        if not isinstance(value, str) or not value.startswith("slate-"):
            return value
        try:
            marker_slate = db.execute(select(IntakeSlate).where(IntakeSlate.id == int(value.removeprefix("slate-")), IntakeSlate.user_id == current_user.id)).scalar_one_or_none()
        except (TypeError, ValueError):
            marker_slate = None
        boundary = (marker_slate.metadata_json or {}).get("retroactive_boundary") if marker_slate else None
        # A marker represents the boundary itself; use its preceding photo for
        # an ``after`` edge and following photo for a ``before`` edge below.
        return (boundary or {}).get(f"{edge}_photo_id") or (boundary or {}).get("after_photo_id") or (boundary or {}).get("before_photo_id") or (marker_slate.slate_image_id if marker_slate else None)

    after_photo_id = resolve_marker(payload.after_photo_id, "after")
    before_photo_id = resolve_marker(payload.before_photo_id, "before")
    if not payload.retroactive or (after_photo_id is None and before_photo_id is None):
        raise HTTPException(status_code=400, detail="Retroactive Slate requires after_photo_id or before_photo_id")
    boundary_ids = [value for value in (after_photo_id, before_photo_id) if value is not None]
    boundary_photos = db.execute(select(IntakePhoto).where(IntakePhoto.id.in_(boundary_ids), IntakePhoto.user_id == current_user.id)).scalars().all()
    if len(boundary_photos) != len(set(boundary_ids)):
        raise HTTPException(status_code=400, detail="Boundary photos were not found in your intake timeline")
    by_id = {photo.id: photo for photo in boundary_photos}
    after = by_id.get(after_photo_id); before = by_id.get(before_photo_id)
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
        # A one-sided "after" boundary is placed immediately after that photo
        # in capture chronology. This also makes a newest-first timeline's
        # left-edge insertion sort ahead of its newest photo.
        after_time = after.captured_at or after.imported_at or after.created_at or datetime.now(UTC)
        effective = (after_time + timedelta(microseconds=1)).isoformat()
    elif before:
        # A one-sided "before" boundary is placed immediately before that
        # photo in capture chronology (the right edge of a newest-first view).
        before_time = before.captured_at or before.imported_at or before.created_at or datetime.now(UTC)
        effective = (before_time - timedelta(microseconds=1)).isoformat()
    try:
        slate, qr_payload, _ = service.create_slate(
            db,
            user=current_user,
            payload=payload.model_dump(exclude_none=True),
            include_qr_data_url=False,
        )
        metadata = dict(slate.metadata_json or {})
        boundary_role = str(payload.boundary_position or "start").strip().lower()
        timeline_role = "TAIL" if boundary_role in {"tail", "end"} else "HEAD"
        qr_data = dict(slate.qr_payload_json or {})
        qr_data["boundary_position"] = "tail" if timeline_role == "TAIL" else "head"
        slate.qr_payload_json = qr_data
        metadata["timeline_role"] = timeline_role
        metadata["retroactive_boundary"] = {
            "after_photo_id": after_photo_id,
            "before_photo_id": before_photo_id,
            "effective_boundary_at": effective,
            "generated_at": datetime.now(UTC).isoformat(),
            "type": f"{timeline_role}_RETROACTIVE",
        }
        # Rendering the canonical Slate is local Pillow work, not a marketplace
        # or AI operation. Do it inline so the inserted timeline entry is the
        # real designed/QR-bearing Slate immediately. Enqueuing here made a
        # simple UI action wait on Redis and flash an unrelated placeholder.
        try:
            rendered = service.render_slate_preview_asset(
                qr_data, item_id=slate.item_id, session_id=slate.session_id
            )
            label = service.render_label_preview_asset(
                qr_data, item_id=slate.item_id, session_id=slate.session_id
            )
            generated_at = datetime.now(UTC).isoformat()
            metadata["rendered_slate"] = {
                "storage_path": rendered.get("storage_path"),
                "local_path": rendered.get("local_path"),
                "generated_from": "canonical_intake_slate_and_qr",
                "generated_at": generated_at,
            }
            metadata["rendered_slate_url"] = rendered.get("storage_path")
            metadata["rendered_slate_status"] = "ready"
            metadata["rendered_label"] = {
                "storage_path": label.get("storage_path"),
                "local_path": label.get("local_path"),
                "generated_from": "canonical_intake_slate_and_qr",
                "generated_at": generated_at,
            }
            metadata["rendered_label_status"] = "ready"
        except Exception as exc:
            # Keep the durable Slate and its timeline position; the explicit
            # repair operation can retry artwork without losing this record.
            metadata["rendered_slate_status"] = "failed"
            metadata["rendered_label_status"] = "failed"
            metadata["render_error"] = str(exc)[:300]
        metadata["label_print_status"] = metadata.get("label_print_status") or "pending"
        metadata["modern_slate"] = True
        slate.metadata_json = metadata
        db.commit(); db.refresh(slate)
        # A timeline Slate is an authoritative item boundary, not merely a
        # visual marker.  Rebuild the affected user's capture batches before
        # acknowledging the request so the next timeline read and listing
        # draft pipeline see the new grouping immediately.
        try:
            service.rebuild_batches_for_user(db, user_id=current_user.id)
            db.commit()
            db.refresh(slate)
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Slate saved but regrouping failed: {str(exc)[:240]}") from exc
        # The regroup preview only needs to inspect the group crossing this
        # exact boundary. Loading every intake photo for the account here made
        # a simple Slate insert scale with the entire photo archive (often
        # thousands of images) and returned large, unused ID arrays.
        current_batch = None
        moved: list[int] = []
        before_group: list[IntakePhoto] = []
        after_group: list[IntakePhoto] = []
        if after and before and after.batch_id and after.batch_id == before.batch_id:
            current_batch = after.batch_id
            batch_photos = db.execute(
                select(IntakePhoto)
                .where(
                    IntakePhoto.user_id == current_user.id,
                    IntakePhoto.batch_id == current_batch,
                )
                .order_by(IntakePhoto.captured_at, IntakePhoto.imported_at, IntakePhoto.id)
            ).scalars().all()
            split_at = next((index for index, photo in enumerate(batch_photos) if photo.id == before.id), len(batch_photos))
            before_group = batch_photos[:split_at]
            after_group = batch_photos[split_at:]
            moved = [photo.id for photo in after_group]
        preview = {"status": "preview_ready", "after_photo_id": after_photo_id, "before_photo_id": before_photo_id,
                   "effective_boundary_at": effective,
                   "current_group": {"batch_id": current_batch, "photo_ids": [p.id for p in before_group + after_group]},
                   "proposed_groups": {"before_photo_ids": [p.id for p in before_group], "after_photo_ids": moved},
                   "photos_moved": moved,
                   "affected_drafts": [current_batch] if current_batch else [],
                   "manual_conflicts": [],
                   "draft_invalidation_reason": "PHOTO_GROUP_CHANGED_BY_RETROACTIVE_SLATE" if current_batch else None}
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
    scope_batch = db.get(IntakePhotoBatch, boundary_photo.batch_id) if boundary_photo.batch_id else None
    photos = db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id, IntakePhoto.batch_id == scope_batch.id).order_by(IntakePhoto.captured_at, IntakePhoto.id)).scalars().all() if scope_batch else db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id, IntakePhoto.id == boundary_photo.id)).scalars().all()
    idx = next((i for i,p in enumerate(photos) if p.id == int(before_id)), None)
    if idx is None: raise HTTPException(status_code=400, detail="Boundary photo is unavailable")
    target = next((p for p in photos[idx:] if p.batch_id), None)
    affected = [p for p in photos[idx:] if target and p.batch_id == target.batch_id]
    before_state = {"batch_id": scope_batch.id if scope_batch else None, "batch_slate_id": scope_batch.slate_id if scope_batch else None, "photos": {str(p.id): {"batch_id": p.batch_id, "item_id": p.item_id, "official_slate_id": (p.metadata_json or {}).get("official_slate_id")} for p in affected}}
    event = IntakeReconciliationEvent(user_id=current_user.id, event_type="timeline_retroactive_regroup", status="planned", source_media_id=slate.id, details_json={"slate_id": slate.id, "before": before_state, "boundary": boundary})
    db.add(event); db.flush()
    if target and scope_batch:
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


@router.post("/slates/repair-modern-artwork")
def repair_modern_slate_artwork(
    limit: int = Query(default=500, ge=1, le=1000),
    dry_run: bool = Query(default=False),
    slate_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Inspect or repair generated canonical Slate artwork for this owner.

    ``dry_run=true`` never writes files or database rows.  A ``slate_id`` may
    be supplied for a narrowly targeted repair; the default remains bounded by
    ``limit`` so historical backfills cannot accidentally become unbounded.
    """
    return service.repair_modern_slate_artwork(
        db,
        user_id=current_user.id,
        limit=limit,
        dry_run=dry_run,
        slate_id=slate_id,
    )


@router.get("/slates/{slate_id}")
def get_intake_slate(
    slate_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    slate = db.execute(select(IntakeSlate).where(IntakeSlate.id == slate_id, IntakeSlate.user_id == current_user.id)).scalar_one_or_none()
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found")
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.transcribe_voice_audio(
        voice_audio_data_url=payload.voice_audio_data_url,
        fallback_transcript=payload.voice_notes,
        db=db,
        user_id=current_user.id,
    )


@router.post("/slates/{slate_id}/voice-notes")
def attach_intake_slate_voice_note(
    slate_id: int,
    payload: IntakeSlateVoiceNoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persist an independently captured voice note against its Slate.

    Packing and offline capture clients commonly finish audio after the item
    boundary was created.  Keep the original audio alongside the transcript
    and maintain an auditable history rather than overwriting it in-place.
    """
    try:
        slate, note, replayed = service.attach_voice_note(
            db,
            user_id=current_user.id,
            slate_id=slate_id,
            payload=payload.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"slate": _serialize_slate(slate), "voice_note": note, "replayed": replayed}


@router.post("/slates/{slate_id}/photos")
async def attach_intake_slate_photo(
    slate_id: int,
    photo: UploadFile = File(...),
    client_event_id: str | None = Form(default=None, max_length=128),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persist a workstation photo directly in its known capture group.

    This avoids treating a packing-station image as merely a loosely adjacent
    timeline insertion. Original bytes and EXIF provenance are kept intact.
    """
    slate = db.execute(
        select(IntakeSlate).where(IntakeSlate.id == slate_id).with_for_update()
    ).scalar_one_or_none()
    if slate is None or slate.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Slate not found")
    event_id = str(client_event_id or "").strip()[:128]
    if event_id:
        prior = db.execute(
            select(IntakePhoto).where(
                IntakePhoto.user_id == current_user.id,
                IntakePhoto.item_id == slate.item_id,
                IntakePhoto.metadata_json["workstation"]["client_event_id"].as_string() == event_id,
            ).limit(1)
        ).scalar_one_or_none()
        if prior is not None:
            return {"photo": _serialize_photo(prior), "slate_id": slate.id, "item_id": slate.item_id, "replayed": True}
    content = await photo.read()
    if not content or len(content) > 30_000_000:
        raise HTTPException(status_code=400, detail="Photo must be non-empty and at most 30 MB")
    filename = str(photo.filename or "workstation-photo.jpg")
    suffix = Path(filename).suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}:
        raise HTTPException(status_code=400, detail="Unsupported photo format")
    capture_time, provenance = _uploaded_capture_time(content)
    relative = Path("intake-workstation") / str(current_user.id) / str(slate.id) / f"{uuid4().hex}{suffix}"
    destination = service.storage.root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    batch = service._ensure_batch(db, user_id=current_user.id, item_id=slate.item_id, slate=slate)
    row = IntakePhoto(
        user_id=current_user.id,
        batch_id=batch.id,
        item_id=slate.item_id,
        source_provider="packing_workstation",
        source_photo_id=f"packing-{uuid4().hex}",
        original_filename=filename,
        local_path=f"/media/{relative.as_posix()}",
        captured_at=capture_time,
        uploaded_at=datetime.now(UTC),
        imported_at=datetime.now(UTC),
        image_type="photo",
        metadata_json={"workstation": {"slate_id": slate.id, "capture_group": slate.item_id, "client_event_id": event_id or None}, "capture_provenance": provenance},
    )
    db.add(row); db.flush()
    db.add(IntakeReconciliationEvent(
        user_id=current_user.id,
        event_type="workstation_photo_attached",
        status="completed",
        source_media_id=row.id,
        details_json={"slate_id": slate.id, "item_id": slate.item_id, "photo_id": row.id, **provenance},
    ))
    db.commit(); db.refresh(row)
    return {"photo": _serialize_photo(row), "slate_id": slate.id, "item_id": slate.item_id, "replayed": False}


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
    copies = int(metadata.get("label_default_copies") or 2)
    history = list(metadata.get("print_history") or [])
    history.append({"action": "print", "requested_at": datetime.utcnow().isoformat(), "copies": copies, "status": "queued"})
    metadata["print_history"] = history[-20:]
    metadata["label_print_status"] = "pending"
    metadata["label_status"] = "pending"
    slate.metadata_json = metadata
    db.add(slate)
    job = InventoryPrintJob(user_id=current_user.id, slate_id=slate.id, listing_id=slate.listing_id,
        job_type="box_label", copies=max(1, min(copies, 10)), paper_profile="receipt_58mm",
        payload_json={"public_url": (slate.qr_payload_json or {}).get("public_url"), "item_id": slate.item_id})
    db.add(job)
    db.commit()
    db.refresh(slate)
    return {"slate": _serialize_slate(slate), "label_print_status": metadata["label_print_status"], "copies": copies, "print_job_id": job.id, "status": job.status}


@router.get("/slates/{slate_id}/label/printable")
def printable_intake_label(slate_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    slate = db.get(IntakeSlate, slate_id)
    if slate is None or slate.user_id != current_user.id: raise HTTPException(status_code=404, detail="Slate not found")
    image = ((slate.metadata_json or {}).get("rendered_label") or {}).get("storage_path")
    if not image: raise HTTPException(status_code=409, detail="Rendered label is not available")
    copies = int((slate.metadata_json or {}).get("label_default_copies") or 2)
    html_doc = f'''<!doctype html><html><head><meta charset="utf-8"><title>PosterPro label {escape(slate.item_id)}</title><style>@page{{size:58mm auto;margin:0}}body{{margin:0;width:58mm}}img{{display:block;width:58mm;height:auto;page-break-after:always}}</style></head><body>{''.join(f'<img src="{escape(str(image))}" alt="label">' for _ in range(max(1,min(copies,10))))}<script>window.onload=()=>window.print()</script></body></html>'''
    return Response(content=html_doc, media_type="text/html")


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


@router.get("/public/item/{token}")
def public_item_scan(token: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Authenticated lookup; a printed URL identifies a record, never grants access."""
    slates = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == current_user.id)).scalars().all()
    slate = next((row for row in slates if str((row.qr_payload_json or {}).get("public_token") or "") == token), None)
    if slate is None:
        raise HTTPException(status_code=404, detail="Item label not found")
    listing = db.get(Listing, slate.listing_id) if slate.listing_id else None
    listing_data = _serialize_listing(listing) if listing else None
    sold = bool(listing and str(getattr(listing.status, "value", listing.status)).lower() == "sold")
    return {
        "item_id": slate.item_id, "title": slate.title or (listing.title if listing else None),
        "brand": slate.brand, "condition": slate.condition, "box_id": slate.box_id,
        "bin_code": (slate.metadata_json or {}).get("bin_code"), "location": slate.location,
        "photos": (listing_data or {}).get("image_urls", [])[:4], "listing": listing_data,
        "sale": {"sold": sold, "shipping_label_available": bool(sold and listing)},
        "shipping_label_url": f"/api/intake/items/{slate.item_id}/shipping-label/print" if sold else None,
    }


@router.get("/public/bin/{token}")
def public_bin_scan(token: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    bin_row = db.execute(select(InventoryBin).where(InventoryBin.nfc_token == token, InventoryBin.user_id == current_user.id)).scalar_one_or_none()
    if bin_row is None:
        raise HTTPException(status_code=404, detail="Bin tag not found")
    assignments = db.execute(select(InventoryBinAssignment).where(InventoryBinAssignment.bin_id == bin_row.id, InventoryBinAssignment.active.is_(True))).scalars().all()
    items = []
    for assignment in assignments:
        slate = db.get(IntakeSlate, assignment.slate_id) if assignment.slate_id else None
        listing = db.get(Listing, assignment.listing_id) if assignment.listing_id else None
        items.append({"item_id": assignment.item_id, "title": (slate.title if slate else None) or (listing.title if listing else None), "box_id": slate.box_id if slate else None, "photo": ((listing.image_urls or [None])[0] if listing else None)})
    return {"bin": {"code": bin_row.code, "label": bin_row.label, "nfc_token": bin_row.nfc_token}, "items": items, "count": len(items)}


@router.post("/slates/{slate_id}/bin")
def assign_slate_bin(slate_id: int, payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    slate = db.get(IntakeSlate, slate_id)
    if slate is None or slate.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Slate not found")
    code = str(payload.get("bin_code") or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="bin_code is required")
    bin_row = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id, InventoryBin.code == code)).scalar_one_or_none()
    if bin_row is None:
        bin_row = InventoryBin(user_id=current_user.id, code=code, nfc_token=token_urlsafe(24), container_uuid=str(uuid4()), label=str(payload.get("label") or code))
        db.add(bin_row); db.flush()
    elif not bin_row.container_uuid:
        # Adopt legacy totes without changing their NFC token or human code.
        bin_row.container_uuid = str(uuid4())
        db.add(bin_row)
    active = db.execute(select(InventoryBinAssignment).where(InventoryBinAssignment.user_id == current_user.id, InventoryBinAssignment.slate_id == slate.id, InventoryBinAssignment.active.is_(True))).scalar_one_or_none()
    previous = None
    if active:
        previous_bin = db.get(InventoryBin, active.bin_id); previous = previous_bin.code if previous_bin else None
        if active.bin_id != bin_row.id:
            active.active = False; active.unassigned_at = datetime.utcnow(); db.add(active); active = None
    if active is None:
        db.add(InventoryBinAssignment(user_id=current_user.id, bin_id=bin_row.id, slate_id=slate.id, listing_id=slate.listing_id, item_id=slate.item_id))
    metadata = dict(slate.metadata_json or {}); metadata["bin_code"] = code; metadata["bin_nfc_token"] = bin_row.nfc_token; slate.metadata_json = metadata; slate.location = code
    related_photos = db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id, IntakePhoto.item_id == slate.item_id)).scalars().all()
    for photo in related_photos:
        photo_meta = dict(photo.metadata_json or {}); photo_meta["bin_code"] = code; photo_meta["bin_nfc_token"] = bin_row.nfc_token; photo.metadata_json = photo_meta; db.add(photo)
    if slate.listing_id:
        listing = db.get(Listing, slate.listing_id)
        if listing is not None:
            listing.storage_unit_name = code; db.add(listing)
    db.add(InventoryBinRevision(user_id=current_user.id, listing_id=slate.listing_id, slate_id=slate.id, item_id=slate.item_id, from_bin_code=previous, to_bin_code=code, changed_by=current_user.id))
    db.add(slate); db.commit()
    return {"bin": {"code": code, "nfc_token": bin_row.nfc_token, "scan_url": f"/scan/bin/{bin_row.nfc_token}"}, "slate": _serialize_slate(slate), "previous_bin": previous}


@router.get("/slates/{slate_id}/bin/history")
def slate_bin_history(slate_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    slate = db.get(IntakeSlate, slate_id)
    if slate is None or slate.user_id != current_user.id: raise HTTPException(status_code=404, detail="Slate not found")
    rows = db.execute(select(InventoryBinRevision).where(InventoryBinRevision.user_id == current_user.id, InventoryBinRevision.slate_id == slate_id).order_by(InventoryBinRevision.created_at.desc())).scalars().all()
    return {"history": [{"from": row.from_bin_code, "to": row.to_bin_code, "created_at": _iso(row.created_at), "reason": row.reason} for row in rows]}


@router.get("/items/{item_id}/shipping-label/print")
def print_shipping_label(item_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == current_user.id, IntakeSlate.item_id == item_id)).scalar_one_or_none()
    if slate is None: raise HTTPException(status_code=404, detail="Item not found")
    listing = db.get(Listing, slate.listing_id) if slate.listing_id else None
    sale = db.execute(select(Sale).where(Sale.user_id == current_user.id, Sale.listing_id == (listing.id if listing else -1)).order_by(Sale.sold_at.desc())).scalars().first()
    if sale is None: raise HTTPException(status_code=409, detail="No confirmed sale exists for this item")
    details = sale.details if isinstance(sale.details, dict) else {}
    recipient = details.get("shipping_address") if isinstance(details.get("shipping_address"), dict) else {}
    lines = [recipient.get(key) for key in ("name", "line1", "line2", "city_state_zip") if recipient.get(key)]
    body = "<br>".join(escape(str(v)) for v in lines) or "Shipping address not available"
    html_doc = f'''<!doctype html><html><head><meta charset="utf-8"><title>Shipping label {escape(item_id)}</title><style>@page{{size:4in 6in;margin:0}}body{{width:4in;height:6in;margin:0;padding:.25in;font:14px Arial;box-sizing:border-box}}.to{{margin-top:1.3in;font-size:20px;line-height:1.5}}.ref{{position:absolute;bottom:.3in;font-size:12px}}</style></head><body><b>POSTERPRO SHIPPING</b><div class="to">{body}</div><div class="ref">Item {escape(item_id)} · Order {escape(str(sale.marketplace_order_id or '—'))}</div><script>window.onload=()=>window.print()</script></body></html>'''
    return Response(content=html_doc, media_type="text/html")


@router.get("/print-jobs/next")
def claim_next_print_job(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.status == "queued").order_by(InventoryPrintJob.created_at.asc()).limit(1)).scalar_one_or_none()
    if job is None: return {"job": None}
    job.status = "claimed"; job.claimed_at = datetime.utcnow(); db.add(job); db.commit(); db.refresh(job)
    return {"job": {"id": job.id, "job_type": job.job_type, "copies": job.copies, "paper_profile": job.paper_profile, "payload": job.payload_json or {}}}


@router.post("/print-jobs/{job_id}/complete")
def complete_print_job(job_id: int, payload: dict = Body(default={}), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = db.get(InventoryPrintJob, job_id)
    if job is None or job.user_id != current_user.id: raise HTTPException(status_code=404, detail="Print job not found")
    job.status = "failed" if payload.get("error") else "completed"; job.error = str(payload.get("error") or "") or None; job.completed_at = datetime.utcnow(); db.add(job); db.commit()
    return {"id": job.id, "status": job.status}


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


def _dedupe_timeline_media_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Collapse exact repeated media in the timeline view without deleting sources.

    Provider identities catch repeated imports of one Google asset; content hashes
    catch byte-identical copies imported under different provider IDs. Slate
    boundaries are never collapsed because repeated boundaries can be intentional.
    """
    result: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    collapsed = 0
    for entry in items:
        photo = entry.get("photo") if isinstance(entry, dict) else None
        if not isinstance(photo, dict) or entry.get("is_slate_marker") or photo.get("is_slate"):
            result.append(entry)
            continue
        metadata = photo.get("metadata_json") if isinstance(photo.get("metadata_json"), dict) else {}
        keys = []
        provider = str(photo.get("source_provider") or "").strip().lower()
        source_id = str(photo.get("source_photo_id") or "").strip()
        content_hash = str(photo.get("content_hash") or "").strip().lower()
        if provider and source_id:
            keys.append(f"source:{provider}:{source_id}")
        if content_hash:
            keys.append(f"hash:{content_hash}")
        duplicate_of = next((seen[key] for key in keys if key in seen), None)
        if duplicate_of is not None:
            collapsed += 1
            kept_photo = duplicate_of.get("photo") or {}
            kept_metadata = dict(kept_photo.get("metadata_json") or {})
            kept_metadata["timeline_duplicate_copies_collapsed"] = int(kept_metadata.get("timeline_duplicate_copies_collapsed") or 0) + 1
            kept_photo["metadata_json"] = kept_metadata
            continue
        result.append(entry)
        for key in keys:
            seen[key] = entry
    return result, collapsed


@router.get("/timeline")
def intake_timeline(
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    order: Literal["oldest", "newest"] = Query("oldest"),
    jump_group: int | None = Query(None, ge=1),
    jump_photo: str | None = Query(None, min_length=1, max_length=32),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # One-time compatibility bridge: convert legacy image Slates into durable
    # Slates before building the response. Their capture timestamp remains the
    # placement key, and all decoded/manual metadata is carried forward.
    legacy = db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id, IntakePhoto.is_slate.is_(True))).scalars().all()
    migrated = False
    for photo in legacy:
        meta = dict(photo.metadata_json or {})
        if meta.get("timeline_deleted") or meta.get("official_slate_id"):
            continue
        batch = db.get(IntakePhotoBatch, photo.batch_id) if photo.batch_id else None
        slate = db.get(IntakeSlate, batch.slate_id) if batch and batch.slate_id else None
        if slate is None:
            payload = {
                "retroactive": True,
                "item_id": str(photo.item_id or meta.get("item_id") or (batch.item_id if batch else "") or f"SLATE-{photo.id}"),
                "box_id": meta.get("box_id") or ((batch.metadata_json or {}).get("box_id") if batch else None),
                "location": meta.get("location") or ((batch.metadata_json or {}).get("location") if batch else None),
                "title": meta.get("title") or photo.original_filename or "",
                "notes": meta.get("notes") or meta.get("voice_notes") or "",
            }
            try:
                slate, _, _ = service.create_slate(db, user=current_user, payload=payload)
            except ValueError:
                slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == current_user.id, IntakeSlate.item_id == payload["item_id"])).scalar_one_or_none()
        if slate is None:
            continue
        slate.slate_image_id = photo.id
        slate.metadata_json = {**(slate.metadata_json or {}), "legacy_photo_id": photo.id, "legacy_metadata": meta, "legacy_replaced_at": datetime.now(UTC).isoformat()}
        meta["official_slate_id"] = slate.id
        meta["classification_source"] = meta.get("classification_source") or "MODERN_SLATE"
        photo.metadata_json = meta
        db.add_all([slate, photo]); migrated = True
    if migrated:
        db.commit()
    # Build the canonical chronology before paging. Paging the raw photos first
    # causes Slate markers whose source photo falls outside the window to be
    # appended at the end (or appear at a false boundary), which breaks item
    # grouping. Markers must replace/insert against the complete ordered stream
    # and only then be sliced for transport.
    timeline = service.timeline_items(db, user_id=current_user.id, limit=None, offset=0)
    batch_ids = {row["photo"].batch_id for row in timeline if row["photo"].batch_id}
    batches_by_id = {
        batch.id: batch
        for batch in db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.id.in_(batch_ids))).scalars().all()
    } if batch_ids else {}
    items = [
            {
                "photo": _serialize_photo(row["photo"], compact=True),
                "timeline_key": row["timeline_key"],
                "late_arrival": row["late_arrival"],
            }
            for row in timeline
        ]
    for item in items:
        photo = item["photo"]
        batch = batches_by_id.get(photo.get("batch_id"))
        if batch and batch.item_id:
            photo["canonical_group_id"] = batch.item_id
    # Render retroactive/system Slates as first-class timeline markers at the
    # requested boundary. They are not marketplace photos, but remain visible
    # with the same edit/voice-note links as any official Slate.
    slates = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == current_user.id)).scalars().all()
    slates = [slate for slate in slates if not bool((slate.metadata_json or {}).get("timeline_deleted"))]
    linked_photo_ids = {int(slate.slate_image_id) for slate in slates if slate.slate_image_id}
    linked_photos_by_id = {
        photo.id: photo
        for photo in db.execute(select(IntakePhoto).where(IntakePhoto.id.in_(linked_photo_ids))).scalars().all()
    } if linked_photo_ids else {}
    linked_photo_by_slate = {
        slate.id: linked_photos_by_id.get(int(slate.slate_image_id))
        for slate in slates
        if slate.slate_image_id
    }
    # Keep boundary resolution O(1).  The timeline can contain thousands of
    # photos; scanning the entire list once for every Slate made the first
    # page request needlessly quadratic (and could exceed the browser timeout).
    photo_positions = {
        str(item["photo"].get("id")): index
        for index, item in enumerate(items)
        if item.get("photo", {}).get("id") is not None
    }
    # Replace legacy image-based Slate rows in-place so their chronological
    # position is preserved exactly.
    for slate in slates:
        boundary = (slate.metadata_json or {}).get("retroactive_boundary") if isinstance(slate.metadata_json, dict) else None
        before_id = boundary.get("before_photo_id") if isinstance(boundary, dict) else None
        linked_photo = linked_photo_by_slate.get(slate.id)
        if linked_photo and (linked_photo.user_id != current_user.id or (linked_photo.metadata_json or {}).get("timeline_deleted")):
            linked_photo = None
        linked_photo_id = linked_photo.id if linked_photo else None
        position = photo_positions.get(str(before_id)) if before_id is not None else None
        after_id = boundary.get("after_photo_id") if isinstance(boundary, dict) else None
        after_position = photo_positions.get(str(after_id)) if after_id is not None else None
        linked_position = photo_positions.get(str(linked_photo_id)) if linked_photo_id is not None else None
        if linked_position is not None:
            position = linked_position
        elif position is None and after_position is not None:
            # timeline_items is chronological (oldest first) until the final
            # newest-first reversal. A one-sided after-boundary belongs just
            # after that photo in this canonical stream, not at the far end.
            position = after_position + 1
        if position is None:
            position = len(items)
        metadata = slate.metadata_json if isinstance(slate.metadata_json, dict) else {}
        qr = slate.qr_payload_json if isinstance(slate.qr_payload_json, dict) else {}
        legacy_metadata = metadata.get("legacy_metadata") if isinstance(metadata.get("legacy_metadata"), dict) else {}
        role_value = str(metadata.get("timeline_role") or legacy_metadata.get("timeline_role") or legacy_metadata.get("classification") or qr.get("boundary_position") or "head").strip().lower()
        slate_role = "TAIL" if role_value == "tail" or role_value.startswith("tail") else "HEAD"
        rendered = metadata.get("rendered_slate") if isinstance(metadata.get("rendered_slate"), dict) else {}
        marker_id = f"slate-{slate.id}"
        rendered_path = _timeline_marker_media_path(
            rendered.get("storage_path") or metadata.get("rendered_slate_url"),
            linked_photo,
        )
        # The marker only uses the legacy source display URL.  Do not build a
        # full photo payload for every linked Slate on every timeline read.
        legacy_source = _serialize_photo(linked_photo, compact=True) if linked_photo else None
        marker = {
            "id": marker_id,
            "user_id": current_user.id,
            "original_filename": f"Slate {slate.item_id or slate.id}",
            "local_path": rendered_path,
            "thumbnail_url": rendered_path,
            "display_url": rendered_path,
            "captured_at": _iso(linked_photo.captured_at) if linked_photo else None,
            "image_type": "slate",
            "is_slate": True,
            "is_internal_only": True,
            "item_id": slate.item_id,
            "source_photo_id": linked_photo.source_photo_id if linked_photo else None,
            "slate_id": slate.id,
            # Compatibility alias retained for older Timeline clients. The
            # directional meaning is always carried by timeline_role.
            "classification": "SLATE",
            "classification_source": "MODERN_SLATE",
            "timeline_role": slate_role,
            "canonical_group_id": slate.item_id,
            "metadata_json": {
                "classification": "SLATE",
                "classification_source": "MODERN_SLATE",
                "timeline_role": slate_role,
                "official_slate_id": slate.id,
                "legacy_photo_id": (metadata.get("legacy_photo_id") or linked_photo_id),
                "legacy_metadata": metadata.get("legacy_metadata") or {},
                "legacy_replaced_at": metadata.get("legacy_replaced_at"),
                "legacy_captured_at": _iso(linked_photo.captured_at) if linked_photo else None,
                "legacy_source_photo_id": linked_photo.source_photo_id if linked_photo else None,
                "legacy_source_image_url": (legacy_source or {}).get("display_url"),
                "modern_artwork_missing": rendered_path is None,
            },
            # Keep timeline markers compact; full QR/label payloads are loaded
            # only by the Slate editor/detail route.
            "slate": {
                "id": slate.id,
                "item_id": slate.item_id,
                "box_id": slate.box_id or legacy_metadata.get("box_id") or qr.get("box_id"),
                "location": slate.location or legacy_metadata.get("location") or qr.get("location"),
                "title": slate.title or legacy_metadata.get("title") or qr.get("title"),
                "brand": slate.brand or legacy_metadata.get("brand") or qr.get("brand"),
                "model": slate.model or legacy_metadata.get("model") or qr.get("model"),
                "condition": slate.condition or legacy_metadata.get("condition") or qr.get("condition"),
                "quantity": qr.get("quantity"),
                "dimensions": {
                    "length": slate.length or legacy_metadata.get("length") or qr.get("length"),
                    "width": slate.width or legacy_metadata.get("width") or qr.get("width"),
                    "height": slate.height or legacy_metadata.get("height") or qr.get("height"),
                    "weight": slate.weight or legacy_metadata.get("weight") or qr.get("weight"),
                },
                "notes": slate.notes or legacy_metadata.get("notes") or qr.get("notes"),
                "flaws": slate.flaws or legacy_metadata.get("flaws") or qr.get("flaws"),
                "voice_notes": (metadata.get("legacy_metadata") or {}).get("voice_notes") or (metadata.get("voice") or {}).get("notes"),
                "transcript": (metadata.get("legacy_metadata") or {}).get("transcript") or (metadata.get("voice") or {}).get("transcript"),
                "boundary_position": "tail" if slate_role == "TAIL" else "head",
            },
        }
        marker_row = {"photo": marker, "timeline_key": (items[linked_position]["timeline_key"] if linked_position is not None else [str((boundary or {}).get("effective_boundary_at") or ""), "slate", str(slate.id)]), "late_arrival": False, "is_slate_marker": True}
        if linked_position is not None:
            items[linked_position] = marker_row
        else:
            items.insert(position, marker_row)
            # Maintain the O(1) lookup map after an insertion.  This is still
            # linear in the number of inserted markers, but avoids the former
            # full-list scan for every boundary lookup and keeps behavior
            # identical for mixed legacy/retroactive Slates.
            for photo_id, current_position in list(photo_positions.items()):
                if current_position >= position:
                    photo_positions[photo_id] = current_position + 1
    items, duplicates_collapsed = _dedupe_timeline_media_items(items)
    _assign_timeline_image_groups(items)
    # Attach stable operator-facing numbering without changing the underlying
    # photo count when a legacy image is replaced by a modern Slate marker.
    # Slate numbers, image-group IDs and global photo numbers remain distinct.
    slate_number = 0
    photo_number = 0
    group_photo_number = 0
    for entry in items:
        photo = entry.get("photo") or {}
        if entry.get("is_slate_marker") or photo.get("is_slate"):
            slate_number += 1
            group_photo_number = 0
            photo["slate_number"] = slate_number
            photo.setdefault("metadata_json", {})["slate_number"] = slate_number
            continue
        photo_number += 1
        group_photo_number += 1
        group_letter = chr(64 + group_photo_number) if group_photo_number <= 26 else f"{group_photo_number}"
        photo["photo_number"] = photo_number
        photo["group_photo_number"] = group_photo_number
        photo["group_photo_label"] = f"{photo_number}-{group_letter}"
    # Keep the legacy/default API chronology stable for existing consumers, but
    # let the operator timeline request newest first. The complete canonical
    # stream (including Slate boundaries and assigned group membership) is
    # built before reversing. Sorting each photo independently can interleave
    # groups when a late-arriving photo has an old capture time; reversing the
    # canonical stream preserves every existing group as one contiguous block.
    if order == "newest":
        items.reverse()
    # Jump targets are resolved against the same fully numbered chronology as
    # ordinary pages, then return a bounded window around the target. This lets
    # the UI resume a distant group/photo without paging thousands of entries.
    jump_index = None
    if jump_photo:
        wanted_label = "".join(character for character in jump_photo.upper() if character.isalnum())
        jump_index = next((
            index for index, item in enumerate(items)
            if "".join(character for character in str((item.get("photo") or {}).get("group_photo_label") or "").upper() if character.isalnum()) == wanted_label
        ), None)
    elif jump_group is not None:
        jump_index = next((
            index for index, item in enumerate(items)
            if int(item.get("image_group_index") or (item.get("photo") or {}).get("image_group_index") or 0) == jump_group
        ), None)
    if jump_index is not None:
        offset = max(0, jump_index - min(limit // 2, 50))
    rendered_items = items[offset: offset + limit]
    return {
        "items": rendered_items,
        "total": len(items),
        "photo_count": sum(1 for row in items if not row.get("is_slate_marker") and not bool((row.get("photo") or {}).get("is_slate"))),
        "slate_count": sum(1 for row in items if row.get("is_slate_marker") or bool((row.get("photo") or {}).get("is_slate"))),
        "duplicates_collapsed": duplicates_collapsed,
        "offset": offset,
        "jump_index": jump_index,
        "limit": limit,
        "order": order,
    }

@router.get("/timeline/google-photos/photos")
def timeline_google_photos_photos(
    album_url: str = Query(..., min_length=8),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Enumerate a connected/shared Google Photos album for timeline insertion."""
    try:
        result = service.google_photos.enumerate_photo_entries(album_url, overall_timeout_seconds=90)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Google Photos could not be read: {exc}") from exc
    return {"items": result.entries[:500], "total": len(result.entries), "enumeration_complete": result.enumeration_complete, "provider_item_count": result.provider_item_count, "album_url": album_url}


@router.post("/timeline/photos/upload")
async def insert_timeline_uploaded_photo(
    photo: UploadFile = File(...),
    after: str | None = None,
    before: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    content = await photo.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded photo is empty")
    filename = str(photo.filename or "timeline-photo.jpg")
    suffix = Path(filename).suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}:
        raise HTTPException(status_code=400, detail="Unsupported photo format")
    capture, boundary = _timeline_insert_capture_time(db, user_id=current_user.id, after=after, before=before)
    relative = Path("timeline-insertions") / str(current_user.id) / f"{uuid4().hex}{suffix}"
    destination = service.storage.root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    metadata = {"timeline": {"capture_time": capture.isoformat(), "source_order": 0}, "retroactive_boundary": boundary, "inserted_by": current_user.id}
    row = IntakePhoto(user_id=current_user.id, source_provider="timeline_upload", source_photo_id=f"timeline-upload-{uuid4().hex}", original_filename=filename, local_path=f"/media/{relative.as_posix()}", captured_at=capture, uploaded_at=datetime.now(UTC), imported_at=datetime.now(UTC), image_type="photo", metadata_json=metadata)
    db.add(row); db.flush()
    db.add(IntakeReconciliationEvent(user_id=current_user.id, event_type="timeline_photo_inserted", status="completed", details_json={"photo_id": row.id, "source": "device_upload", "boundary": boundary}))
    db.commit(); db.refresh(row)
    return {"photo": _serialize_photo(row, compact=False), "photo_id": row.id, "boundary": boundary}


@router.post("/timeline/photos/google")
def insert_timeline_google_photo(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    source_url = str((payload or {}).get("url") or "").strip()
    if not source_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="A Google Photos image URL is required")
    capture, boundary = _timeline_insert_capture_time(db, user_id=current_user.id, after=payload.get("after"), before=payload.get("before"))
    source_id = str((payload or {}).get("source_photo_id") or uuid4().hex)
    filename = str((payload or {}).get("original_filename") or f"google-photo-{source_id}.jpg")
    metadata = {"timeline": {"capture_time": capture.isoformat(), "source_order": 0, "source_page_url": payload.get("album_url")}, "retroactive_boundary": boundary, "inserted_by": current_user.id, "google_photos": {"album_url": payload.get("album_url"), "source_photo_id": source_id}}
    row = IntakePhoto(user_id=current_user.id, source_provider="google_photos_timeline", source_photo_id=source_id, source_album_id=str(payload.get("album_id") or "") or None, original_filename=filename, local_path=source_url, downloaded_url=source_url, captured_at=capture, uploaded_at=datetime.now(UTC), imported_at=datetime.now(UTC), image_type="photo", metadata_json=metadata)
    db.add(row); db.flush()
    db.add(IntakeReconciliationEvent(user_id=current_user.id, event_type="timeline_photo_inserted", status="completed", details_json={"photo_id": row.id, "source": "google_photos", "source_photo_id": source_id, "boundary": boundary}))
    db.commit(); db.refresh(row)
    return {"photo": _serialize_photo(row, compact=False), "photo_id": row.id, "boundary": boundary}


@router.post("/timeline/classify")
def classify_timeline_assets(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    raw_ids = payload.get("photo_ids") or []
    photo_ids: set[int] = set()
    slate_ids: set[int] = set()
    selected_slates: list[IntakeSlate] = []
    for value in raw_ids:
        token = str(value)
        if token.startswith("slate-"):
            try:
                slate_ids.add(int(token.removeprefix("slate-")))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid Timeline Slate ID") from exc
        else:
            try:
                photo_ids.add(int(value))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Invalid Timeline photo ID") from exc
    if slate_ids:
        selected_slates = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == current_user.id, IntakeSlate.id.in_(slate_ids))).scalars().all()
        if len(selected_slates) != len(slate_ids):
            raise HTTPException(status_code=404, detail="Timeline Slate not found")
        photo_ids.update(int(slate.slate_image_id) for slate in selected_slates if slate.slate_image_id)
    ids = sorted(photo_ids)
    classification = str(payload.get("classification") or "PHOTO").upper()
    if classification not in {"PHOTO", "SLATE", "HEAD", "TAIL", "UNKNOWN"}:
        raise HTTPException(status_code=400, detail="Unsupported timeline classification")
    rows = db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id, IntakePhoto.id.in_(ids))).scalars().all()
    before = []
    for row in rows:
        meta = dict(row.metadata_json or {})
        before.append({"id": row.id, "metadata_json": meta, "is_slate": row.is_slate, "image_type": row.image_type, "is_internal_only": row.is_internal_only, "is_public_listing_candidate": row.is_public_listing_candidate, "item_id": row.item_id, "batch_id": row.batch_id})
        if classification == "PHOTO":
            official_slate_id = meta.get("official_slate_id")
            if official_slate_id:
                official_slate = db.execute(select(IntakeSlate).where(IntakeSlate.id == int(official_slate_id), IntakeSlate.user_id == current_user.id)).scalar_one_or_none()
                # Only retire a modern Slate when this exact photo is its
                # authoritative replacement/source. Inherited IDs on ordinary
                # photos are provenance and must not delete another marker.
                if official_slate and official_slate.slate_image_id == row.id:
                    slate_meta = dict(official_slate.metadata_json or {})
                    slate_meta.update({"timeline_deleted": True, "timeline_deleted_at": slate_meta.get("timeline_deleted_at") or datetime.now(UTC).isoformat(), "timeline_deleted_by": current_user.id})
                    official_slate.metadata_json = slate_meta
                    db.add(official_slate)
                meta.pop("official_slate_id", None)
            meta["classification_source"] = "MANUAL_OPERATOR"
            meta["classification"] = "PHOTO"
            meta.pop("timeline_role", None)
            meta.pop("qr_payload", None)
            meta.pop("timeline_slate_qr_payload", None)
            row.is_slate = False
            row.image_type = "photo"
            row.is_internal_only = False
            row.is_public_listing_candidate = not bool(meta.get("timeline_deleted"))
            row.metadata_json = meta
            db.add(row)
            continue

        role = "TAIL" if classification == "TAIL" else "HEAD"
        meta.update({"classification_source": "MANUAL_OPERATOR", "classification": role, "timeline_role": role})
        row.is_slate = True
        row.image_type = role.lower()
        row.is_internal_only = True
        row.is_public_listing_candidate = False
        batch = db.get(IntakePhotoBatch, row.batch_id) if row.batch_id else None

        # A Tail Slate closes the preceding photo group. Reuse that group's
        # canonical item identity when it exists; otherwise create a new
        # tail-positioned canonical Slate so preceding unassigned photos can be
        # recovered by the existing batch rebuild routine.
        group_item_id = str(row.item_id or (batch.item_id if batch else "") or "").strip()
        if role == "TAIL" and not group_item_id:
            ordered_rows = service._ordered_photos(db, user_id=current_user.id)
            row_index = next((i for i, candidate in enumerate(ordered_rows) if candidate.id == row.id), -1)
            previous = ordered_rows[row_index - 1] if row_index > 0 else None
            previous_batch = db.get(IntakePhotoBatch, previous.batch_id) if previous and previous.batch_id else None
            group_item_id = str((previous.item_id if previous else None) or (previous_batch.item_id if previous_batch else "") or "").strip()
        if role == "TAIL" and group_item_id:
            row.item_id = group_item_id
            existing_batch = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == current_user.id, IntakePhotoBatch.item_id == group_item_id)).scalar_one_or_none()
            if existing_batch:
                row.batch_id = existing_batch.id
            existing_slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == current_user.id, IntakeSlate.item_id == group_item_id)).scalar_one_or_none()
            if existing_slate:
                slate_id = existing_slate.id
                meta["timeline_slate_id"] = existing_slate.id
                qr_payload = dict(existing_slate.qr_payload_json or {})
                qr_payload.update({"item_id": group_item_id, "boundary_position": "tail"})
                meta["qr_payload"] = qr_payload
            else:
                slate_payload = {"retroactive": True, "boundary_position": "tail", "item_id": group_item_id, "title": meta.get("title") or row.original_filename or "", "notes": meta.get("notes") or "", "source_photo_id": row.id}
                official_slate, qr_payload, _ = service.create_slate(db, user=current_user, payload=slate_payload)
                slate_id = official_slate.id
                row.item_id = official_slate.item_id
                row.batch_id = existing_batch.id if existing_batch else row.batch_id
                meta["official_slate_id"] = official_slate.id
                meta["qr_payload"] = qr_payload
                official_slate.slate_image_id = row.id
                db.add(official_slate)
        else:
            slate_payload = {
                "retroactive": True,
                "boundary_position": "tail" if role == "TAIL" else "head",
                "item_id": group_item_id or None,
                "title": meta.get("title") or row.original_filename or "",
                "notes": meta.get("notes") or "",
                "location": meta.get("location") or (batch.metadata_json or {}).get("location", "") if batch else "",
                "box_id": meta.get("box_id") or ((batch.metadata_json or {}).get("box_id") if batch else None),
                "source_photo_id": row.id,
            }
            official_slate, qr_payload, _ = service.create_slate(db, user=current_user, payload=slate_payload)
            slate_id = official_slate.id
            row.item_id = official_slate.item_id
            row.batch_id = batch.id if batch else row.batch_id
            meta["official_slate_id"] = official_slate.id
            meta["qr_payload"] = qr_payload
            official_slate.slate_image_id = row.id
            db.add(official_slate)
        row.metadata_json = meta
        db.add(row)
        if role == "TAIL" and group_item_id and not slate_id:
            raise HTTPException(status_code=409, detail="Tail Slate could not be attached to its canonical item group")

    # Some operator-created boundary markers have no source photo. Keep their
    # authoritative Slate metadata and QR payload aligned with classification.
    for selected_slate in selected_slates:
        if classification == "PHOTO":
            if not selected_slate.slate_image_id:
                slate_meta = dict(selected_slate.metadata_json or {})
                slate_meta.update({"timeline_deleted": True, "timeline_deleted_at": slate_meta.get("timeline_deleted_at") or datetime.now(UTC).isoformat(), "timeline_deleted_by": current_user.id})
                selected_slate.metadata_json = slate_meta
                db.add(selected_slate)
            continue
        role = "TAIL" if classification == "TAIL" else "HEAD"
        slate_meta = dict(selected_slate.metadata_json or {})
        slate_meta["timeline_role"] = role
        selected_slate.metadata_json = slate_meta
        qr = dict(selected_slate.qr_payload_json or {})
        qr["boundary_position"] = "tail" if role == "TAIL" else "start"
        selected_slate.qr_payload_json = qr
        db.add(selected_slate)

    if rows:
        service.rebuild_batches_for_user(db, user_id=current_user.id)
    db.add(IntakeReconciliationEvent(user_id=current_user.id, event_type="timeline_classification_change", status="completed", details_json={"before": before, "after": {"classification": classification, "photo_ids": ids}, "scope": "selected"}))
    db.commit()
    return {"updated": len(rows), "classification": classification, "official_slate_ids": [(row.metadata_json or {}).get("official_slate_id") for row in rows if (row.metadata_json or {}).get("official_slate_id")]}


@router.delete("/timeline/assets/{asset_id}")
def delete_timeline_asset(asset_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    slate = None
    photo = None
    if str(asset_id).startswith("slate-"):
        try:
            slate_id = int(str(asset_id).removeprefix("slate-"))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Timeline Slate not found") from exc
        slate = db.execute(select(IntakeSlate).where(IntakeSlate.id == slate_id, IntakeSlate.user_id == current_user.id)).scalar_one_or_none()
        if slate is None:
            raise HTTPException(status_code=404, detail="Timeline Slate not found")
        photo = db.execute(select(IntakePhoto).where(IntakePhoto.id == slate.slate_image_id, IntakePhoto.user_id == current_user.id)).scalar_one_or_none() if slate.slate_image_id else None
    else:
        try:
            photo_id = int(asset_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Timeline image not found") from exc
        photo = db.execute(select(IntakePhoto).where(IntakePhoto.id == photo_id, IntakePhoto.user_id == current_user.id)).scalar_one_or_none()
        if photo is None:
            raise HTTPException(status_code=404, detail="Timeline image not found")
        linked_id = (photo.metadata_json or {}).get("official_slate_id")
        if linked_id and photo.id == db.execute(select(IntakeSlate.slate_image_id).where(IntakeSlate.id == int(linked_id), IntakeSlate.user_id == current_user.id)).scalar_one_or_none():
            slate = db.execute(select(IntakeSlate).where(IntakeSlate.id == int(linked_id), IntakeSlate.user_id == current_user.id)).scalar_one_or_none()

    deleted_photo_ids = []
    if photo is not None:
        metadata = dict(photo.metadata_json or {})
        if not metadata.get("timeline_deleted"):
            metadata.update({"timeline_deleted": True, "timeline_deleted_at": datetime.now(UTC).isoformat(), "timeline_deleted_by": current_user.id})
            photo.metadata_json = metadata
            photo.is_public_listing_candidate = False
            photo.is_internal_only = True
            db.add(photo)
        deleted_photo_ids.append(photo.id)
    if slate is not None:
        slate_metadata = dict(slate.metadata_json or {})
        slate_metadata.update({"timeline_deleted": True, "timeline_deleted_at": slate_metadata.get("timeline_deleted_at") or datetime.now(UTC).isoformat(), "timeline_deleted_by": current_user.id})
        slate.metadata_json = slate_metadata
        db.add(slate)
    item_ids = {str(value).strip() for value in [photo.item_id if photo else None, slate.item_id if slate else None] if str(value or "").strip()}
    db.add(IntakeReconciliationEvent(user_id=current_user.id, event_type="timeline_asset_deleted", status="completed", details_json={"asset_id": asset_id, "photo_ids": deleted_photo_ids, "slate_id": slate.id if slate else None, "item_ids": sorted(item_ids)}))
    db.flush()
    service.rebuild_batches_for_user(db, user_id=current_user.id)
    _refresh_existing_timeline_draft_media(db, user_id=current_user.id, item_ids=item_ids)
    db.commit()
    return {"deleted": True, "asset_id": asset_id, "photo_ids": deleted_photo_ids, "slate_id": slate.id if slate else None}


@router.post("/timeline/assets/bulk-delete")
def delete_timeline_assets(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Soft-delete selected timeline assets without touching unrelated assets.

    Deletion is metadata-based so listing media and provenance remain recoverable;
    both photo IDs and rendered Slate marker IDs are accepted.
    """
    raw_ids = (payload.get("asset_ids") or payload.get("photo_ids")) if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="Select at least one timeline photo")
    if len(raw_ids) > 5000:
        raise HTTPException(status_code=400, detail="Bulk deletion is limited to 5000 photos per request")
    photo_ids: set[int] = set()
    slate_ids: set[int] = set()
    try:
        for value in raw_ids:
            token = str(value)
            if token.startswith("slate-"):
                slate_ids.add(int(token.removeprefix("slate-")))
            else:
                photo_ids.add(int(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Timeline bulk deletion accepts photo or Slate IDs") from exc
    photo_ids = sorted(photo_ids)
    if any(value <= 0 for value in photo_ids):
        raise HTTPException(status_code=400, detail="Timeline photo IDs must be positive")
    rows = db.execute(
        select(IntakePhoto).where(
            IntakePhoto.user_id == current_user.id,
            IntakePhoto.id.in_(photo_ids),
        )
    ).scalars().all()
    selected_slates = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == current_user.id, IntakeSlate.id.in_(slate_ids))).scalars().all() if slate_ids else []
    found_ids = {row.id for row in rows}
    found_slate_ids = {row.id for row in selected_slates}
    skipped_ids = [value for value in raw_ids if (str(value).startswith("slate-") and int(str(value).removeprefix("slate-")) not in found_slate_ids) or (not str(value).startswith("slate-") and int(value) not in found_ids)]
    now = datetime.now(UTC).isoformat()
    item_ids: set[str] = set()
    deleted_ids: list[int] = []
    for row in rows:
        metadata = dict(row.metadata_json or {})
        if not metadata.get("timeline_deleted"):
            metadata.update({"timeline_deleted": True, "timeline_deleted_at": now, "timeline_deleted_by": current_user.id})
            row.metadata_json = metadata
            row.is_public_listing_candidate = False
            row.is_internal_only = True
            db.add(row)
        deleted_ids.append(row.id)
        if str(row.item_id or "").strip():
            item_ids.add(str(row.item_id).strip())
        linked_id = metadata.get("official_slate_id")
        if linked_id:
            slate = db.execute(select(IntakeSlate).where(IntakeSlate.id == int(linked_id), IntakeSlate.user_id == current_user.id)).scalar_one_or_none()
            if slate is not None:
                slate_meta = dict(slate.metadata_json or {})
                slate_meta.update({"timeline_deleted": True, "timeline_deleted_at": slate_meta.get("timeline_deleted_at") or now, "timeline_deleted_by": current_user.id})
                slate.metadata_json = slate_meta
                db.add(slate)
                if str(slate.item_id or "").strip():
                    item_ids.add(str(slate.item_id).strip())
    for slate in selected_slates:
        slate_meta = dict(slate.metadata_json or {})
        slate_meta.update({"timeline_deleted": True, "timeline_deleted_at": slate_meta.get("timeline_deleted_at") or now, "timeline_deleted_by": current_user.id})
        slate.metadata_json = slate_meta
        db.add(slate)
        if str(slate.item_id or "").strip():
            item_ids.add(str(slate.item_id).strip())
    db.add(IntakeReconciliationEvent(
        user_id=current_user.id,
        event_type="timeline_assets_bulk_deleted",
        status="completed",
        details_json={"photo_ids": deleted_ids, "slate_ids": sorted(found_slate_ids), "skipped_ids": skipped_ids, "item_ids": sorted(item_ids)},
    ))
    db.flush()
    service.rebuild_batches_for_user(db, user_id=current_user.id)
    _refresh_existing_timeline_draft_media(db, user_id=current_user.id, item_ids=item_ids)
    db.commit()
    return {"deleted": len(deleted_ids) + len(found_slate_ids), "photo_ids": deleted_ids, "slate_ids": sorted(found_slate_ids), "skipped": skipped_ids}


@router.post("/timeline/primary")
def set_timeline_primary(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    photo_id = int((payload or {}).get("photo_id") or 0)
    photo = db.execute(select(IntakePhoto).where(IntakePhoto.id == photo_id, IntakePhoto.user_id == current_user.id)).scalar_one_or_none()
    if photo is None or photo.is_slate:
        raise HTTPException(status_code=400, detail="Select a product photo")
    ordered = service.timeline_items(db, user_id=current_user.id, limit=None, offset=0)
    batches = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == current_user.id)).scalars().all()
    batches_by_id = {batch.id: batch for batch in batches}
    group_items = []
    for row in ordered:
        current = row["photo"]
        serialized = _serialize_photo(current, compact=False)
        batch = batches_by_id.get(current.batch_id)
        if batch and batch.item_id:
            serialized["canonical_group_id"] = batch.item_id
        current_meta = current.metadata_json if isinstance(current.metadata_json, dict) else {}
        qr_payload = current_meta.get("qr_payload") if isinstance(current_meta.get("qr_payload"), dict) else {}
        serialized["timeline_role"] = str(current_meta.get("timeline_role") or current_meta.get("classification") or qr_payload.get("boundary_position") or ("HEAD" if current.is_slate else "PHOTO")).upper()
        group_items.append({"photo": serialized})
    _assign_timeline_image_groups(group_items)
    selected_item = next((entry for entry in group_items if int(entry["photo"].get("id") or 0) == photo_id), None)
    group_id = selected_item.get("image_group_id") if selected_item else None
    if not group_id:
        raise HTTPException(status_code=409, detail="Photo is not assigned to a canonical image group yet")
    peers = [row["photo"] for row in group_items if row.get("image_group_id") == group_id and not row["photo"].get("is_slate") and row["photo"].get("id")]
    peer_ids = [int(peer["id"]) for peer in peers]
    if not peer_ids:
        raise HTTPException(status_code=409, detail="Image group has no product photos")
    peer_rows = db.execute(select(IntakePhoto).where(IntakePhoto.user_id == current_user.id, IntakePhoto.id.in_(peer_ids))).scalars().all()
    clear = bool((payload or {}).get("clear"))
    for peer in peer_rows:
        meta = dict(peer.metadata_json or {})
        meta.pop("timeline_primary", None)
        meta.pop("timeline_primary_source", None)
        meta.pop("timeline_primary_group_id", None)
        if not clear and peer.id == photo_id:
            meta.update({"timeline_primary": True, "timeline_primary_source": "MANUAL_OPERATOR", "timeline_primary_group_id": group_id})
        peer.metadata_json = meta
        db.add(peer)
    db.add(IntakeReconciliationEvent(user_id=current_user.id, event_type="timeline_primary_photo_cleared" if clear else "timeline_primary_photo_set", status="completed", details_json={"photo_id": photo_id, "image_group_id": group_id, "cleared": clear}))
    db.flush()
    group_item_id = str(group_id).removeprefix("item:") if str(group_id).startswith("item:") else None
    if group_item_id:
        _refresh_existing_timeline_draft_media(db, user_id=current_user.id, item_ids={group_item_id})
    db.commit()
    return {"photo_id": photo_id, "image_group_id": group_id, "primary": not clear, "selection_source": "AUTOMATIC" if clear else "MANUAL_OPERATOR"}

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
