from pathlib import Path
import re
from uuid import uuid4
from urllib.parse import urlparse

import httpx

from app.core.config import settings


class LocalStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.storage_root)
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
            target = destination_dir / file_name
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
    return f"{slug}-{uuid4().hex[:8]}{extension}"
