from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.auth import ensure_user_owns_resource, get_current_user
from app.core.database import get_db
from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import (
    Listing,
    ListingRevision,
    MarketplaceCrosspostJob,
    MarketplaceExtensionDevice,
    MarketplaceExtensionJob,
    MarketplaceExtensionPairingCode,
    MarketplaceListing,
    User,
)
from app.services.marketplace_extension_jobs import MarketplaceExtensionJobError, queue_extension_marketplace_action

router = APIRouter()
CURRENT_EXTENSION_VERSION = "0.2.0"
MINIMUM_EXTENSION_VERSION = "0.2.0"

ASSISTED_MARKETPLACES = {"facebook", "mercari", "poshmark", "vinted", "etsy", "offerup", "depop", "whatnot"}
JOB_STATES = {
    "CLAIMED", "NAVIGATING", "FORM_FILLING", "AWAITING_OPERATOR_REVIEW",
    "SUBMITTING", "SUBMITTED", "COMPLETED", "FAILED", "RETRYABLE", "CANCELLED",
}
TRANSITIONS = {
    "CLAIMED": {"NAVIGATING", "FAILED", "RETRYABLE", "CANCELLED"},
    "NAVIGATING": {"FORM_FILLING", "AWAITING_OPERATOR_REVIEW", "FAILED", "RETRYABLE", "CANCELLED"},
    "FORM_FILLING": {"AWAITING_OPERATOR_REVIEW", "FAILED", "RETRYABLE", "CANCELLED"},
    "AWAITING_OPERATOR_REVIEW": {"SUBMITTING", "CANCELLED", "FAILED"},
    "SUBMITTING": {"SUBMITTED", "COMPLETED", "FAILED", "RETRYABLE"},
    "SUBMITTED": {"COMPLETED", "FAILED", "RETRYABLE"},
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=UTC).isoformat() if value and value.tzinfo is None else (value.isoformat() if value else None)


class PairingCodeRequest(BaseModel):
    device_name: str = Field(default="PosterPro browser", max_length=120)


class PairRequest(BaseModel):
    pairing_code: str = Field(min_length=16, max_length=128)
    device_name: str = Field(default="PosterPro browser", max_length=120)
    browser: str | None = Field(default=None, max_length=64)
    extension_version: str | None = Field(default=None, max_length=32)


class HeartbeatRequest(BaseModel):
    browser: str | None = Field(default=None, max_length=64)
    extension_version: str | None = Field(default=None, max_length=32)


class AssistedJobRequest(BaseModel):
    marketplace: str
    action: Literal["CREATE", "UPDATE", "END", "SYNC"] = "CREATE"
    priority: int = Field(default=1, ge=0, le=10)


class JobStateRequest(BaseModel):
    status: str
    error_code: str | None = Field(default=None, max_length=100)
    error_detail: str | None = Field(default=None, max_length=2000)
    external_listing_id: str | None = Field(default=None, max_length=255)
    external_url: str | None = Field(default=None, max_length=2000)
    result: dict | None = None


def _version_tuple(value: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value or "0").split(".") if part.isdigit())
    except ValueError:
        return (0,)


def _marketplace_url_matches(marketplace: str, url: str | None) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    domains = {
        "facebook": ("facebook.com",), "mercari": ("mercari.com",),
        "poshmark": ("poshmark.com",), "vinted": ("vinted.com",),
        "etsy": ("etsy.com",), "offerup": ("offerup.com",),
        "depop": ("depop.com",), "whatnot": ("whatnot.com",),
    }.get(str(marketplace or "").lower(), ())
    return bool(host and any(host == domain or host.endswith(f".{domain}") for domain in domains))


def _require_device(authorization: str | None, db: Session) -> MarketplaceExtensionDevice:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Paired extension token required")
    device = db.execute(
        select(MarketplaceExtensionDevice).where(
            MarketplaceExtensionDevice.token_hash == _digest(token),
            MarketplaceExtensionDevice.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=401, detail="Extension device is unpaired or revoked")
    return device


def _device_payload(device: MarketplaceExtensionDevice) -> dict:
    return {
        "id": device.id,
        "device_key": device.device_key,
        "name": device.name,
        "browser": device.browser,
        "extension_version": device.extension_version,
        "current_version": CURRENT_EXTENSION_VERSION,
        "minimum_version": MINIMUM_EXTENSION_VERSION,
        "update_required": _version_tuple(device.extension_version) < _version_tuple(MINIMUM_EXTENSION_VERSION),
        "user_id": device.user_id,
        "last_seen_at": _iso(device.last_seen_at),
        "last_claim_at": _iso(device.last_claim_at),
        "revoked": bool(device.revoked_at),
    }


def _job_payload(job: MarketplaceExtensionJob) -> dict:
    return {
        "id": job.id,
        "listing_id": job.listing_id,
        "marketplace": job.marketplace,
        "action": job.action,
        "status": job.status,
        "attempt_count": job.attempt_count,
        "claimed_at": _iso(job.claimed_at),
        "started_at": _iso(job.started_at),
        "lease_expires_at": _iso(job.lease_expires_at),
        "completed_at": _iso(job.completed_at),
        "external_listing_id": job.external_listing_id,
        "external_url": job.external_url,
        "error_code": job.error_code,
        "error_detail": job.error_detail,
        "payload": job.payload_snapshot,
        "result": job.result,
    }


@router.post("/browser-extension/pairing-codes")
def create_pairing_code(
    payload: PairingCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    code = secrets.token_urlsafe(24)
    now = datetime.now(UTC).replace(tzinfo=None)
    row = MarketplaceExtensionPairingCode(
        user_id=current_user.id,
        code_hash=_digest(code),
        expires_at=now + timedelta(minutes=5),
    )
    db.add(row)
    db.commit()
    return {"pairing_code": code, "expires_at": _iso(row.expires_at), "device_name": payload.device_name}


@router.post("/browser-extension/pair")
def pair_extension(payload: PairRequest, db: Session = Depends(get_db)):
    now = datetime.now(UTC).replace(tzinfo=None)
    row = db.execute(
        select(MarketplaceExtensionPairingCode)
        .where(MarketplaceExtensionPairingCode.code_hash == _digest(payload.pairing_code))
        .with_for_update()
    ).scalar_one_or_none()
    if not row or row.consumed_at or row.expires_at <= now:
        raise HTTPException(status_code=410, detail="Pairing code is invalid, expired, or already used")
    row.consumed_at = now
    token = secrets.token_urlsafe(48)
    device = MarketplaceExtensionDevice(
        user_id=row.user_id,
        device_key=secrets.token_urlsafe(18),
        name=payload.device_name.strip() or "PosterPro browser",
        browser=payload.browser,
        extension_version=payload.extension_version,
        token_hash=_digest(token),
        last_seen_at=now,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return {"device": _device_payload(device), "device_token": token}


@router.get("/browser-extension/devices")
def list_extension_devices(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    devices = db.execute(
        select(MarketplaceExtensionDevice).where(MarketplaceExtensionDevice.user_id == current_user.id)
        .order_by(MarketplaceExtensionDevice.created_at.desc())
    ).scalars().all()
    jobs = db.execute(
        select(MarketplaceExtensionJob).where(MarketplaceExtensionJob.user_id == current_user.id)
        .order_by(MarketplaceExtensionJob.created_at.desc()).limit(100)
    ).scalars().all()
    pending_jobs = db.execute(
        select(func.count(MarketplaceExtensionJob.id)).where(
            MarketplaceExtensionJob.user_id == current_user.id,
            MarketplaceExtensionJob.status.in_(["QUEUED", "RETRYABLE"]),
            or_(
                MarketplaceExtensionJob.error_code.is_(None),
                MarketplaceExtensionJob.error_code != "SUBMISSION_OUTCOME_UNKNOWN",
            ),
        )
    ).scalar_one()
    active = [job for job in jobs if job.status in {"CLAIMED", "NAVIGATING", "FORM_FILLING", "AWAITING_OPERATOR_REVIEW", "SUBMITTING"}]
    return {
        "devices": [_device_payload(device) for device in devices],
        "pending_jobs": int(pending_jobs or 0),
        "active_jobs": [_job_payload(job) for job in active[:20]],
        "recent_failures": [_job_payload(job) for job in jobs if job.status == "FAILED"][:20],
    }


@router.delete("/browser-extension/devices/{device_id}")
def revoke_extension_device(device_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    device = db.get(MarketplaceExtensionDevice, device_id)
    if not device or device.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Extension device not found")
    device.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return {"revoked": True, "device_id": device_id}


@router.post("/browser-extension/heartbeat")
def extension_heartbeat(
    payload: HeartbeatRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _require_device(authorization, db)
    device.last_seen_at = datetime.now(UTC).replace(tzinfo=None)
    if payload.browser:
        device.browser = payload.browser[:64]
    if payload.extension_version:
        device.extension_version = payload.extension_version[:32]
    db.commit()
    return {
        "ok": True,
        "device": _device_payload(device),
        "current_version": CURRENT_EXTENSION_VERSION,
        "minimum_version": MINIMUM_EXTENSION_VERSION,
        "update_required": _version_tuple(payload.extension_version) < _version_tuple(MINIMUM_EXTENSION_VERSION),
    }


@router.post("/listings/{listing_id}/assisted-marketplace-jobs")
def create_assisted_job(
    listing_id: int,
    payload: AssistedJobRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    market = payload.marketplace.strip().lower()
    action = payload.action.upper()
    if market not in ASSISTED_MARKETPLACES:
        raise HTTPException(status_code=400, detail="Marketplace does not use this assisted transport")
    if action == "SYNC":
        raise HTTPException(status_code=422, detail="Extension status/sale polling is not supported for this marketplace")
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    try:
        job, created = queue_extension_marketplace_action(
            db,
            user_id=current_user.id,
            listing=listing,
            marketplace=market,
            action=action,
            priority=payload.priority,
        )
    except MarketplaceExtensionJobError as exc:
        status_code = 422 if exc.code == "MARKETPLACE_FIELDS_INCOMPLETE" else 409 if exc.code in {
            "EXTERNAL_IDENTITY_EXISTS", "EXTERNAL_IDENTITY_REQUIRED", "EXTERNAL_URL_REQUIRED",
            "OPERATOR_RECONCILIATION_REQUIRED",
        } else 400
        raise HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)}) from exc
    db.commit()
    db.refresh(job)
    result = _job_payload(job)
    result["deduplicated"] = not created
    return result


@router.get("/assisted-marketplace-jobs")
def list_assisted_jobs(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    jobs = db.execute(
        select(MarketplaceExtensionJob).where(MarketplaceExtensionJob.user_id == current_user.id)
        .order_by(MarketplaceExtensionJob.created_at.desc()).limit(200)
    ).scalars().all()
    return [_job_payload(job) for job in jobs]


@router.get("/assisted-marketplace-jobs/{job_id}")
def get_assisted_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = db.get(MarketplaceExtensionJob, job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Assisted marketplace job not found")
    return _job_payload(job)


@router.post("/browser-extension/jobs/claim")
def claim_extension_job(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _require_device(authorization, db)
    if _version_tuple(device.extension_version) < _version_tuple(MINIMUM_EXTENSION_VERSION):
        raise HTTPException(status_code=426, detail={"code": "EXTENSION_UPDATE_REQUIRED", "minimum_version": MINIMUM_EXTENSION_VERSION})
    now = datetime.now(UTC).replace(tzinfo=None)
    candidate = db.execute(
        select(MarketplaceExtensionJob)
        .where(
            MarketplaceExtensionJob.user_id == device.user_id,
            or_(
                MarketplaceExtensionJob.status == "QUEUED",
                and_(
                    MarketplaceExtensionJob.status == "RETRYABLE",
                    or_(
                        MarketplaceExtensionJob.error_code.is_(None),
                        MarketplaceExtensionJob.error_code != "SUBMISSION_OUTCOME_UNKNOWN",
                    ),
                ),
                (
                MarketplaceExtensionJob.status.in_(["CLAIMED", "NAVIGATING", "FORM_FILLING"])
                & MarketplaceExtensionJob.lease_expires_at.is_not(None)
                & (MarketplaceExtensionJob.lease_expires_at < now)
                ),
            ),
        )
        .order_by(MarketplaceExtensionJob.priority.asc(), MarketplaceExtensionJob.created_at.asc())
        .with_for_update(skip_locked=True)
    ).scalars().first()
    device.last_seen_at = now
    if not candidate:
        db.commit()
        return {"job": None}
    candidate.status = "CLAIMED"
    candidate.device_id = device.id
    candidate.claimed_at = now
    candidate.started_at = candidate.started_at or now
    candidate.lease_expires_at = now + timedelta(minutes=5)
    candidate.last_state_at = now
    candidate.attempt_count = int(candidate.attempt_count or 0) + 1
    device.last_claim_at = now
    db.commit()
    db.refresh(candidate)
    return {"job": _job_payload(candidate)}


@router.post("/browser-extension/jobs/{job_id}/state")
def update_extension_job_state(
    job_id: int,
    payload: JobStateRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _require_device(authorization, db)
    job = db.get(MarketplaceExtensionJob, job_id)
    if not job or job.user_id != device.user_id or job.device_id != device.id:
        raise HTTPException(status_code=404, detail="Claimed assisted job not found")
    next_status = payload.status.strip().upper()
    if next_status not in JOB_STATES or next_status not in TRANSITIONS.get(job.status, set()):
        raise HTTPException(status_code=409, detail=f"Invalid assisted job transition: {job.status} -> {next_status}")
    if payload.external_url and not _marketplace_url_matches(job.marketplace, payload.external_url):
        raise HTTPException(status_code=422, detail="Result URL does not match the job marketplace")
    if job.action in {"UPDATE", "END"}:
        original_id = job.external_listing_id or (job.payload_snapshot or {}).get("external_listing_id")
        original_url = job.external_url or (job.payload_snapshot or {}).get("external_url")
        if payload.external_listing_id and payload.external_listing_id != original_id:
            raise HTTPException(status_code=409, detail="Update/end results cannot replace the confirmed external listing ID")
        if payload.external_url and payload.external_url != original_url:
            raise HTTPException(status_code=409, detail="Update/end results cannot replace the confirmed external listing URL")
    now = datetime.now(UTC).replace(tzinfo=None)
    previous_status = str(job.status).upper()
    job.status = next_status
    job.last_state_at = now
    job.error_code = payload.error_code
    job.error_detail = payload.error_detail
    if (
        job.action == "CREATE"
        and previous_status in {"SUBMITTING", "SUBMITTED"}
        and next_status in {"RETRYABLE", "FAILED"}
    ):
        # A browser crash after the marketplace submit boundary is ambiguous:
        # automatic CREATE retry could create a duplicate. Require an operator
        # to reconcile the external account before any subsequent CREATE.
        job.error_code = "SUBMISSION_OUTCOME_UNKNOWN"
        job.error_detail = payload.error_detail or (
            "The extension lost confirmation after submission began. Check the marketplace before retrying."
        )
    if payload.external_listing_id:
        job.external_listing_id = payload.external_listing_id
    if payload.external_url:
        job.external_url = payload.external_url
    if payload.result is not None:
        job.result = payload.result
    if next_status in {"FAILED", "RETRYABLE", "CANCELLED", "COMPLETED"}:
        job.completed_at = now
        job.lease_expires_at = None
    else:
        job.lease_expires_at = now + timedelta(minutes=5)
    if next_status == "COMPLETED":
        if job.action in {"CREATE", "UPDATE"} and not (job.external_listing_id or job.external_url):
            raise HTTPException(status_code=422, detail="Completion requires an external listing ID or URL")
        if job.action in {"CREATE", "UPDATE", "END"}:
            row = db.execute(select(MarketplaceListing).where(
                MarketplaceListing.listing_id == job.listing_id,
                MarketplaceListing.marketplace == MarketplaceName(job.marketplace),
            ).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())).scalars().first()
            if row is None and job.action == "CREATE":
                row = MarketplaceListing(listing_id=job.listing_id, marketplace=MarketplaceName(job.marketplace))
            if row is not None:
                row.marketplace_listing_id = job.external_listing_id or row.marketplace_listing_id
                row.status = {
                    "CREATE": MarketplaceListingStatus.PUBLISHED,
                    "UPDATE": MarketplaceListingStatus.UPDATED,
                    "END": MarketplaceListingStatus.DELETED,
                }[job.action]
                row.raw_response = {**(row.raw_response or {}), "external_url": job.external_url, "extension_job_id": job.id, "result": payload.result}
                db.add(row)
    elif job.action in {"CREATE", "UPDATE", "END"}:
        row = db.execute(select(MarketplaceListing).where(
            MarketplaceListing.listing_id == job.listing_id,
            MarketplaceListing.marketplace == MarketplaceName(job.marketplace),
        ).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())).scalars().first()
        if row is not None:
            # Action state is not listing lifecycle. A failed revise/end must
            # not make an already-published listing appear unpublished/failed.
            if job.action == "CREATE":
                row.status = MarketplaceListingStatus.FAILED if next_status == "FAILED" else MarketplaceListingStatus.PENDING
            row.raw_response = {
                **(row.raw_response or {}),
                "extension_job_id": job.id,
                "extension_state": next_status,
                "error_code": job.error_code,
                "error_detail": job.error_detail,
                "external_url": job.external_url or (row.raw_response or {}).get("external_url"),
            }
            db.add(row)

    if job.crosspost_job_id:
        parent = db.get(MarketplaceCrosspostJob, job.crosspost_job_id)
        if parent and parent.user_id == device.user_id:
            children = db.execute(select(MarketplaceExtensionJob).where(
                MarketplaceExtensionJob.crosspost_job_id == parent.id,
            )).scalars().all()
            statuses = {str(child.status).upper() for child in children}
            if any(child.error_code == "SUBMISSION_OUTCOME_UNKNOWN" for child in children):
                parent.status = "awaiting_operator_review"
                parent.last_error = "A marketplace CREATE may have been submitted, but the extension did not confirm the outcome. Verify external identity before retrying."
            elif statuses.intersection({"QUEUED", "CLAIMED", "NAVIGATING", "FORM_FILLING", "SUBMITTING", "SUBMITTED", "RETRYABLE"}):
                parent.status = "awaiting_operator_review" if "AWAITING_OPERATOR_REVIEW" in statuses else "running"
            elif "FAILED" in statuses:
                parent.status = "failed"
            elif children and statuses.issubset({"COMPLETED", "CANCELLED"}):
                parent.status = "completed"
            parent.result_summary = {
                **(parent.result_summary or {}),
                "extension_jobs": [
                    {"id": child.id, "marketplace": child.marketplace, "action": child.action, "status": child.status, "error_code": child.error_code}
                    for child in children
                ],
            }
            revision_id = (parent.execution_plan or {}).get("revision_id")
            if revision_id:
                revision = db.get(ListingRevision, int(revision_id))
                if revision:
                    revision.status = parent.status
                    revision.sync_state = "synced" if parent.status == "completed" else "update_failed" if parent.status == "failed" else "update_queued"
                    db.add(revision)
            db.add(parent)
    device.last_seen_at = now
    db.commit()
    db.refresh(job)
    return _job_payload(job)
