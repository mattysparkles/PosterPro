"""Durable, destination-specific automatic preflight scheduling."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import SessionLocal
from app.models.models import Listing
from app.services.marketplace_field_mapper import build_marketplace_payload
from app.services.marketplace_preflight import compute_payload_hash

PREFLIGHT_QUEUE_TTL = timedelta(hours=24)


def publication_payload_hash(listing: Listing, marketplace: str) -> str | None:
    return compute_payload_hash(build_marketplace_payload(listing, marketplace))


def invalidate_marketplace_preflight(listing: Listing, marketplace: str) -> None:
    """Invalidate only the changed destination's cached result."""
    market = str(marketplace or "").strip().lower()
    data = dict(listing.marketplace_data or {}) if isinstance(listing.marketplace_data, dict) else {}
    root = data.get("marketplace_preflight") if isinstance(data.get("marketplace_preflight"), dict) else {}
    rows = dict(root.get("by_marketplace") or {}) if isinstance(root.get("by_marketplace"), dict) else {}
    rows.pop(market, None)
    if rows:
        data["marketplace_preflight"] = {**root, "by_marketplace": rows}
    else:
        data.pop("marketplace_preflight", None)
    listing.marketplace_data = data


def queue_listing_marketplace_preflight(listing_id: int, marketplace: str, *, reason: str) -> dict[str, Any]:
    """Persist queue state before dispatch; identical queued work is coalesced."""
    market = str(marketplace or "").strip().lower()
    if market not in {"ebay", "facebook", "mercari", "etsy", "poshmark", "depop", "vinted", "offerup"}:
        return {"queued": False, "status": "unsupported_marketplace"}
    db = SessionLocal()
    try:
        listing = db.get(Listing, int(listing_id))
        if listing is None:
            return {"queued": False, "status": "listing_missing"}
        fingerprint = publication_payload_hash(listing, market)
        if not fingerprint:
            return {"queued": False, "status": "payload_unavailable"}
        data = dict(listing.marketplace_data or {}) if isinstance(listing.marketplace_data, dict) else {}
        state = data.get("marketplace_preflight_jobs") if isinstance(data.get("marketplace_preflight_jobs"), dict) else {}
        jobs = dict(state.get("by_marketplace") or {}) if isinstance(state.get("by_marketplace"), dict) else {}
        previous = jobs.get(market) if isinstance(jobs.get(market), dict) else {}
        if previous.get("fingerprint") == fingerprint and previous.get("status") in {"queued", "running"}:
            return {"queued": False, "status": previous.get("status"), "deduplicated": True}
        cached_root = data.get("marketplace_preflight") if isinstance(data.get("marketplace_preflight"), dict) else {}
        cached_rows = cached_root.get("by_marketplace") if isinstance(cached_root.get("by_marketplace"), dict) else {}
        cached = cached_rows.get(market) if isinstance(cached_rows.get(market), dict) else {}
        checked = None
        try:
            checked = datetime.fromisoformat(str(cached.get("last_checked_at") or "").replace("Z", "+00:00"))
            if checked.tzinfo is not None:
                checked = checked.astimezone(UTC).replace(tzinfo=None)
        except (TypeError, ValueError):
            pass
        current_cache = (
            bool(cached.get("execution_payload_hash"))
            and cached.get("execution_payload_hash") == fingerprint
            and checked is not None
            and datetime.now(UTC).replace(tzinfo=None) - checked <= PREFLIGHT_QUEUE_TTL
        )
        if current_cache:
            return {"queued": False, "status": "already_current"}
        now = datetime.now(UTC).isoformat()
        jobs[market] = {
            "status": "queued", "fingerprint": fingerprint,
            "reason": str(reason or "publication_inputs_changed")[:120],
            "queued_at": now, "attempts": int(previous.get("attempts") or 0),
        }
        data["marketplace_preflight_jobs"] = {**state, "by_marketplace": jobs}
        listing.marketplace_data = data
        db.commit()
    finally:
        db.close()

    from app.workers.tasks import refresh_listing_marketplace_preflight_task
    try:
        refresh_listing_marketplace_preflight_task.apply_async(
            args=[int(listing_id), market, fingerprint],
            kwargs={"reason": str(reason or "publication_inputs_changed")[:120]},
            queue="posterpro",
            task_id=f"preflight-{int(listing_id)}-{market}-{fingerprint[:24]}",
        )
        return {"queued": True, "status": "queued", "fingerprint": fingerprint}
    except Exception as exc:
        db = SessionLocal()
        try:
            listing = db.get(Listing, int(listing_id))
            if listing:
                data = dict(listing.marketplace_data or {})
                state = data.get("marketplace_preflight_jobs") if isinstance(data.get("marketplace_preflight_jobs"), dict) else {}
                jobs = dict(state.get("by_marketplace") or {})
                row = dict(jobs.get(market) or {})
                if row.get("fingerprint") == fingerprint:
                    row.update({"status": "dispatch_failed", "error": f"{type(exc).__name__}: {str(exc)[:160]}", "failed_at": datetime.now(UTC).isoformat()})
                    jobs[market] = row
                    data["marketplace_preflight_jobs"] = {**state, "by_marketplace": jobs}
                    listing.marketplace_data = data
                    db.commit()
        finally:
            db.close()
        return {"queued": False, "status": "dispatch_failed", "error": str(exc)[:160]}
