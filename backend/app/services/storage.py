from pathlib import Path
from uuid import uuid4

import httpx

from app.core.config import settings


class LocalStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.storage_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_from_url(self, url: str, prefix: str = "imports") -> str:
        destination_dir = self.root / prefix
        destination_dir.mkdir(parents=True, exist_ok=True)
        extension = ".jpg"
        file_name = f"{uuid4()}{extension}"
        target = destination_dir / file_name
        with httpx.Client(timeout=30) as client:
            response = client.get(url)
            response.raise_for_status()
            target.write_bytes(response.content)
        return str(target)

    def save_bytes(self, data: bytes, *, extension: str = ".jpg", prefix: str = "uploads") -> str:
        destination_dir = self.root / prefix
        destination_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{uuid4()}{extension if extension.startswith('.') else f'.{extension}'}"
        target = destination_dir / file_name
        target.write_bytes(data)
        return str(target)
