from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.models import VineImageRecoveryJob

MAX_ATTEMPTS = 4


def enqueue_recovery(db: Session, *, user_id: int, asin: str, title_hint: str | None = None, listing_id: int | None = None) -> VineImageRecoveryJob:
    asin = str(asin or "").strip().upper()
    if len(asin) != 10 or not asin.isalnum():
        raise ValueError("valid ASIN required")
    insert = pg_insert if db.bind and db.bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(VineImageRecoveryJob).values(
        user_id=user_id, asin=asin, title_hint=title_hint, listing_id=listing_id,
        status="pending_authorization", attempts=0,
        page_evidence_json={"reason": "fast_lookup_has_no_confirmed_usable_gallery", "rights": "unverified"},
    ).on_conflict_do_nothing(index_elements=["user_id", "asin"])
    db.execute(statement)
    db.flush()
    job = db.execute(select(VineImageRecoveryJob).where(VineImageRecoveryJob.user_id == user_id, VineImageRecoveryJob.asin == asin)).scalar_one()
    if listing_id and not job.listing_id:
        job.listing_id = listing_id
    if title_hint and not job.title_hint:
        job.title_hint = title_hint
    if job.status in {"failed", "unavailable"} and job.attempts < MAX_ATTEMPTS:
        job.status = "pending_authorization"
        job.next_attempt_at = datetime.utcnow() + timedelta(minutes=10)
    return job


def serialize_job(job: VineImageRecoveryJob) -> dict:
    return {
        "id": job.id, "asin": job.asin, "listing_id": job.listing_id,
        "title_hint": job.title_hint, "status": job.status, "attempts": job.attempts,
        "last_error": job.last_error, "created_at": job.created_at,
        "candidates": job.candidates_json or [], "page_evidence": job.page_evidence_json or {},
    }
