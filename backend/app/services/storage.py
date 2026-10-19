from pathlib import Path
import re
from uuid import uuid4
from urllib.parse import urlparse

import httpx

from app.core.config import settings


class LocalStorage:
    def __init__(self, root: str | None = None):
        configured = Path(root or settings.storage_root)
        # ``STORAGE_ROOT=./storage`` is intentionally convenient for the
        # backend service, but maintenance scripts may run from the repository
        # root.  Resolve relative storage consistently from ``backend/`` so
        # saved `/media/...` URLs always point at the directory FastAPI serves.
        self.root = configured if configured.is_absolute() else (Path(__file__).resolve().parents[2] / configured).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_from_url(self, url: str, prefix: str = "imports", suggested_basename: str | None = None) -> str:
        destination_dir = self.root / prefix
        destination_dir.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=30) as client:
            response = client.get(url)
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").lower()
            extension = _infer_extension(url, content_type)
            file_name = _build_filename(suggested_basename, extension)
            target = _next_available_path(destination_dir, file_name)
            target.write_bytes(response.content)
        return str(target)

    def save_bytes(self, data: bytes, *, extension: str = ".jpg", prefix: str = "uploads") -> str:
        destination_dir = self.root / prefix
        destination_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{uuid4()}{extension if extension.startswith('.') else f'.{extension}'}"
        target = destination_dir / file_name
        target.write_bytes(data)
        return str(target)


def _infer_extension(url: str, content_type: str) -> str:
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "gif" in content_type:
        return ".gif"
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def _build_filename(suggested_basename: str | None, extension: str) -> str:
    if not suggested_basename:
        return f"{uuid4()}{extension}"
    slug = re.sub(r"[^a-z0-9]+", "-", suggested_basename.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:90]
    if not slug:
        return f"{uuid4()}{extension}"
    # A caller that provides a curated basename (for example an Amazon Vine
    # product image) expects a stable public filename.  Collisions are handled
    # by `_next_available_path` with a readable numeric suffix instead of a
    # random public URL fragment.
    return f"{slug}{extension}"


def _next_available_path(destination_dir: Path, file_name: str) -> Path:
    target = destination_dir / file_name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    index = 2
    while True:
        candidate = destination_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1
