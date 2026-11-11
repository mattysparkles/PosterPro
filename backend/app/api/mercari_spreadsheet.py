"""Operator-owned Mercari spreadsheet preparation, never remote submission."""
from __future__ import annotations

import json
import hashlib
import uuid
from datetime import UTC, datetime
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import Listing, User
from app.services.mercari_spreadsheet_export import fill_official_workbook, inventory_candidates
from app.services.active_listing_snapshot import refresh_ebay_active_snapshot

router = APIRouter(prefix="/mercari/spreadsheet", tags=["mercari-spreadsheet"])


@router.get("/readiness")
def readiness(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    snapshot = refresh_ebay_active_snapshot(db, current_user.id)
    active_ids = snapshot.get("listing_ids") if isinstance(snapshot.get("listing_ids"), set) else set()
    rows = inventory_candidates(db, current_user.id, active_ids)
    counts = {key: sum(key in row["blockers"] for row in rows) for key in ("category", "shipping", "required_information")}
    return {"items": rows, "ebay_snapshot": {"status": snapshot.get("status"), "verified_at": snapshot.get("verified_at"), "source": "official eBay active-listings read" if snapshot.get("status") == "REMOTE_VERIFIED" else "last verified eBay snapshot"}, "summary": {"source_items": len(rows), "export_ready": sum(row["status"] == "export_ready" for row in rows),
            "needs_repair": sum(row["status"] != "export_ready" for row in rows),
            "missing_category": counts["category"], "missing_shipping": counts["shipping"], "missing_required_information": counts["required_information"]}}


@router.post("/export")
async def export(template: UploadFile = File(...), listing_ids: str = Form(...),
                 db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not (template.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="Upload Mercari's official .xlsx listing template. PosterPro preserves its worksheets and column layout.")
    try:
        selected = list(dict.fromkeys(int(value) for value in json.loads(listing_ids) if str(value).isdigit()))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="listing_ids must be a JSON array of PosterPro listing IDs.") from exc
    if not selected or len(selected) > 200:
        raise HTTPException(status_code=422, detail="Select between 1 and 200 listings per export batch.")
    snapshot = refresh_ebay_active_snapshot(db, current_user.id)
    active_ids = snapshot.get("listing_ids") if isinstance(snapshot.get("listing_ids"), set) else set()
    if not active_ids:
        raise HTTPException(status_code=409, detail="No verified eBay active-listing snapshot is available. Refresh eBay reconciliation before exporting its inventory.")
    candidates = {row["listing_id"]: row for row in inventory_candidates(db, current_user.id, active_ids)}
    unknown = [listing_id for listing_id in selected if listing_id not in candidates]
    blocked = {listing_id: candidates[listing_id]["blockers"] for listing_id in selected if listing_id in candidates and candidates[listing_id]["status"] != "export_ready"}
    if unknown or blocked:
        raise HTTPException(status_code=409, detail={"message": "Only export-ready inventory can be included. Resolve the listed items in Quick Repair.", "unknown_ids": unknown, "blocked": blocked})
    listings = db.execute(select(Listing).where(Listing.user_id == current_user.id, Listing.id.in_(selected))).scalars().all()
    by_id = {row.id: row for row in listings}
    ordered = [by_id[item_id] for item_id in selected if item_id in by_id]
    if len(ordered) != len(selected):
        raise HTTPException(status_code=404, detail="One or more selected listings were not found for this seller.")
    raw = await template.read()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Template must be smaller than 15 MB.")
    try:
        output, manifest = fill_official_workbook(raw, ordered)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    batch_id = str(uuid.uuid4())
    exported_at = datetime.now(UTC).isoformat()
    template_fingerprint = hashlib.sha256(raw).hexdigest()
    row_map = {item["posterpro_listing_id"]: item for item in manifest["row_map"]}
    for listing in ordered:
        metadata = dict(listing.source_metadata or {}) if isinstance(listing.source_metadata, dict) else {}
        exports = dict(metadata.get("marketplace_exports") or {}) if isinstance(metadata.get("marketplace_exports"), dict) else {}
        mercari_exports = list(exports.get("mercari") or [])
        mercari_exports.append({"batch_id": batch_id, "status": "EXPORTED", "exported_at": exported_at,
                                "worksheet_row": row_map[listing.id]["row"], "template_sha256": template_fingerprint})
        exports["mercari"] = mercari_exports[-20:]
        metadata["marketplace_exports"] = exports
        listing.source_metadata = metadata
        db.add(listing)
    db.commit()
    headers = {"Content-Disposition": "attachment; filename=posterpro-mercari-import-ready.xlsx",
               "X-PosterPro-Exported-Rows": str(manifest["exported_count"]),
               "X-PosterPro-Export-Batch": batch_id,
               "X-PosterPro-Listing-IDs": ",".join(str(item["posterpro_listing_id"]) for item in manifest["row_map"])}
    return StreamingResponse(BytesIO(output), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)
