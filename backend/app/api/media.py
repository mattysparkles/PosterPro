"""Small, safe derivatives for fast operator-facing catalog grids."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.storage import LocalStorage


router = APIRouter()

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _media_source(public_path: str) -> tuple[Path, Path]:
    """Resolve only a served ``/media/`` path; never accept a filesystem path."""
    if not public_path.startswith("/media/"):
        raise HTTPException(status_code=400, detail="A served media path is required.")
    root = LocalStorage().root.resolve()
    relative = public_path.removeprefix("/media/").lstrip("/")
    candidate = (root / relative).resolve()
    if root not in candidate.parents or candidate.suffix.lower() not in _IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported media path.")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Media file was not found.")
    return root, candidate


@router.get("/media/thumbnail")
def media_thumbnail(
    path: str = Query(..., min_length=8, max_length=2048),
    width: int = Query(200, ge=48, le=512),
    height: int = Query(200, ge=48, le=512),
) -> FileResponse:
    """Return a cached JPEG thumbnail without changing the original asset.

    The source mtime is part of the cache key, so replacing an approved image
    naturally produces a new derivative while existing browser caches remain
    valid.  This endpoint intentionally accepts only paths already exposed by
    PosterPro's `/media` mount.
    """
    root, source = _media_source(path)
    stat = source.stat()
    key = hashlib.sha256(
        f"{source.relative_to(root).as_posix()}:{stat.st_mtime_ns}:{width}:{height}".encode("utf-8")
    ).hexdigest()
    destination = root / ".thumbnails" / key[:2] / f"{key}.jpg"
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(source) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
                image.save(destination, "JPEG", quality=78, optimize=True)
        except (OSError, UnidentifiedImageError) as exc:
            raise HTTPException(status_code=422, detail="Media cannot be rendered as an image.") from exc
    return FileResponse(
        destination,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
