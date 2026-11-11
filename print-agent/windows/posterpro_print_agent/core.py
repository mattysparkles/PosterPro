"""Platform-neutral validation and idempotency helpers for the Windows agent."""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any


PRINTER_NAME = "Sparkles SKU Printer"
PIXEL_SIZE = (464, 320)
ALLOWED_STATES = {"RENDERED", "SUBMITTING", "SPOOLER_ACCEPTED", "FAILED", "UNKNOWN", "CANCELLED"}


def validate_job(job: dict[str, Any]) -> bytes:
    """Reject anything other than a fixed-size, structured 58x40 label job."""
    if not isinstance(job, dict) or job.get("job_type") != "census_item_label":
        raise ValueError("Only Shelf Census label jobs are accepted.")
    if not isinstance(job.get("job_id"), str) or not job["job_id"]:
        raise ValueError("A stable server job ID is required.")
    if job.get("printer_name") != PRINTER_NAME:
        raise ValueError("Print destination is not the configured Sparkles SKU Printer.")
    paper = job.get("paper") or {}
    if paper != {"width_mm": 58, "height_mm": 40, "dpi_x": 203, "dpi_y": 203, "orientation": "portrait", "scaling_percent": 100}:
        raise ValueError("Unsupported paper profile; expected native 58x40 mm portrait at 203 DPI and 100%.")
    if tuple((job.get("pixel_width"), job.get("pixel_height"))) != PIXEL_SIZE:
        raise ValueError("Label raster must be exactly 464x320 dots.")
    payload = job.get("payload") or {}
    if not isinstance(payload.get("item_uuid"), str) or not isinstance(payload.get("barcode_token"), str):
        raise ValueError("Physical item identity is incomplete.")
    encoded = job.get("image_png_base64")
    if not isinstance(encoded, str) or len(encoded) > 2_000_000:
        raise ValueError("Label raster is missing or exceeds size limits.")
    try:
        png = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Label raster is not valid base64.") from exc
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Label raster is not a PNG image.")
    # PNG IHDR stores width and height as big-endian 32-bit values.
    if len(png) < 24 or (int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")) != PIXEL_SIZE:
        raise ValueError("PNG dimensions do not match the assigned label stock.")
    return png


def load_journal(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_journal(path: Path, journal: dict[str, dict[str, Any]]) -> None:
    """Durably replace local submission journal; never silently drop a prior job."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".posterpro-print-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(journal, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def may_submit(job_id: str, journal: dict[str, dict[str, Any]]) -> bool:
    """Only never-submitted jobs may reach the printer after agent restart."""
    entry = journal.get(job_id) or {}
    return entry.get("state") not in {"SUBMITTING", "SPOOLER_ACCEPTED", "UNKNOWN", "CANCELLED"}

