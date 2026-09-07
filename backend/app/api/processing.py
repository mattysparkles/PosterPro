from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import Listing, User
from app.services.listing_processing import ListingProcessingService
from app.workers.tasks import resume_incomplete_listings_task
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/processing", tags=["processing"])


@router.get("/health")
def processing_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = ListingProcessingService()
    backlog = service.backlog_summary(db, user_id=current_user.id)
    worker_ping = {}
    try:
        worker_ping = celery_app.control.ping(timeout=1.0) or {}
    except Exception:
        worker_ping = {}
    return {
        "backlog": backlog,
        "worker_health": {
            "worker_count": len(worker_ping) if isinstance(worker_ping, dict) else len(worker_ping or []),
            "ping": worker_ping,
        },
        "stalled": backlog.get("stalled", False),
    }


@router.get("/blockers")
def processing_blockers(
    reason: str = Query(..., min_length=1, max_length=255),
    limit: int = Query(default=100, ge=1, le=250),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return actionable rows for one blocker reason without loading the full catalog."""
    rows = db.execute(
        select(
            Listing.id, Listing.title, Listing.description, Listing.source_type,
            Listing.processing_state, Listing.processing_blocking_reason,
            Listing.processing_stage, Listing.image_urls, Listing.source_metadata,
        )
        .where(Listing.user_id == current_user.id, Listing.processing_blocking_reason == reason)
        .order_by(Listing.updated_at.desc())
        .limit(limit)
    ).all()
    items = []
    for row in rows:
        source_meta = row.source_metadata if isinstance(row.source_metadata, dict) else {}
        items.append({
            "id": row.id,
            "title": row.title,
            "description": row.description,
            "source_type": row.source_type,
            "processing_state": row.processing_state,
            "reason": row.processing_blocking_reason,
            "stage": row.processing_stage,
            "image_urls": row.image_urls if isinstance(row.image_urls, list) else [],
            "evidence": {
                "source_notes": source_meta.get("notes") or source_meta.get("voice_transcript") or source_meta.get("recovery_evidence"),
                "source_metadata": source_meta,
            },
            "next_action": "Open listing, add the missing evidence, or queue automatic repair.",
        })
    return {"reason": reason, "count": len(items), "items": items}


@router.post("/backfill")
def queue_processing_backfill(
    limit: int = Query(default=25, ge=1, le=250),
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = ListingProcessingService()
    summary = service.backlog_summary(db, user_id=current_user.id)
    if dry_run:
        result = service.resume_backlog(db, user_id=current_user.id, limit=limit, dry_run=True, worker_id="api-processing-backfill")
        return {"queued": False, "dry_run": True, "summary": summary, "result": result}
    task = resume_incomplete_listings_task.apply_async(args=[current_user.id, limit, False])
    return {
        "queued": True,
        "task_id": task.id,
        "summary": summary,
        "limit": limit,
        "dry_run": False,
    }
