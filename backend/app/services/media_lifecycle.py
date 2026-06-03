from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Image, Listing


def _resolve_local_media_path(media_ref: str) -> Path | None:
    raw = str(media_ref or "").strip()
    if not raw:
        return None
    storage_root = Path(settings.storage_root).resolve()
    if raw.startswith("/media/"):
        return (storage_root / raw.replace("/media/", "", 1)).resolve()
    if raw.startswith("storage/"):
        return (storage_root / raw.replace("storage/", "", 1)).resolve()
    if raw.startswith("./storage/"):
        return (storage_root / raw.replace("./storage/", "", 1)).resolve()
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    return None


def purge_listing_media(db: Session, listing: Listing, *, clear_references: bool = True) -> dict:
    paths = [str(value).strip() for value in (listing.image_urls or []) if str(value).strip()]
    deleted_files = 0
    missing_files = 0
    deleted_image_rows = 0
    preserved_image_rows = 0

    for media_ref in paths:
        resolved = _resolve_local_media_path(media_ref)
        if resolved is None:
            continue

        image_row = db.execute(select(Image).where(Image.local_path == str(resolved))).scalar_one_or_none()
        if image_row is not None:
            db.delete(image_row)
            deleted_image_rows += 1
        else:
            preserved_image_rows += 1

        if resolved.exists():
            try:
                resolved.unlink()
                deleted_files += 1
            except OSError:
                missing_files += 1
        else:
            missing_files += 1

    if clear_references:
        listing.image_urls = []
        db.add(listing)

    return {
        "deleted_files": deleted_files,
        "missing_files": missing_files,
        "deleted_image_rows": deleted_image_rows,
        "preserved_image_rows": preserved_image_rows,
    }
