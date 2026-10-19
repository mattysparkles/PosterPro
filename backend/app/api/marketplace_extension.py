from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, update
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
    MarketplacePublishBatch,
    User,
)
from app.services.marketplace_extension_jobs import MarketplaceExtensionJobError, queue_extension_marketplace_action
from app.services.process_notifications import create_process_notification

router = APIRouter()
CURRENT_EXTENSION_VERSION = "0.3.70"
MINIMUM_EXTENSION_VERSION = "0.3.59"
# A deployed extension cannot reload itself.  Keep the immediately preceding
# verified CREATE runtime eligible while Chrome is reloading the .70 package;
# old unsupported builds remain blocked from live CREATE claims.
# Browser automation selectors are version-sensitive.  A live CREATE must use
# the exact reviewed manifest version; older builds may still report
# diagnostics/reconciliation, but cannot submit a commercial listing.
LIVE_CREATE_EXTENSION_VERSIONS = {CURRENT_EXTENSION_VERSION}
# Browser-assisted jobs are operator actions, not an archival replay queue.  A
# queued job older than this window is stale and must be explicitly recreated.
EXTENSION_JOB_CLAIM_WINDOW = timedelta(hours=24)


def _has_recent_explicit_resume(db: Session, child: MarketplaceExtensionJob, cutoff: datetime, now: datetime) -> bool:
    """A guarded same-child resume grants a fresh bounded claim window without rewriting created_at."""
    if not child.crosspost_job_id:
        return False
    parent = db.get(MarketplaceCrosspostJob, child.crosspost_job_id)
    plan = parent.execution_plan if parent and isinstance(parent.execution_plan, dict) else {}
    history = plan.get("resume_history") if isinstance(plan.get("resume_history"), list) else []
    for entry in reversed(history):
        if not isinstance(entry, dict) or child.id not in (entry.get("resumed_extension_job_ids") or []):
            continue
        raw_at = entry.get("at")
        if not isinstance(raw_at, str):
            continue
        try:
            resumed_at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if resumed_at.tzinfo is not None:
            resumed_at = resumed_at.astimezone(UTC).replace(tzinfo=None)
        return cutoff <= resumed_at <= now
    return False

ASSISTED_MARKETPLACES = {"facebook", "mercari", "poshmark", "vinted", "etsy", "offerup", "depop", "whatnot"}
JOB_STATES = {
    "CLAIMED", "NAVIGATING", "FORM_FILLING", "AWAITING_OPERATOR_REVIEW",
    "SUBMITTING", "SUBMITTED", "SUBMITTED_PENDING_REVIEW", "COMPLETED", "FAILED", "RETRYABLE", "CANCELLED",
    "LOGIN_REQUIRED", "FORM_DETECTED", "TESTING_FIELDS", "BLOCKED_EXTERNAL",
}
TRANSITIONS = {
    "CLAIMED": {"NAVIGATING", "FAILED", "RETRYABLE", "CANCELLED"},
    "NAVIGATING": {"FORM_DETECTED", "FORM_FILLING", "LOGIN_REQUIRED", "BLOCKED_EXTERNAL", "AWAITING_OPERATOR_REVIEW", "FAILED", "RETRYABLE", "CANCELLED"},
    "FORM_DETECTED": {"TESTING_FIELDS", "FORM_FILLING", "FAILED", "BLOCKED_EXTERNAL", "CANCELLED"},
    "TESTING_FIELDS": {"COMPLETED", "LOGIN_REQUIRED", "BLOCKED_EXTERNAL", "FAILED", "AWAITING_OPERATOR_REVIEW", "CANCELLED"},
    "FORM_FILLING": {"AWAITING_OPERATOR_REVIEW", "SUBMITTING", "LOGIN_REQUIRED", "BLOCKED_EXTERNAL", "FAILED", "RETRYABLE", "CANCELLED"},
    "AWAITING_OPERATOR_REVIEW": {"SUBMITTING", "CANCELLED", "FAILED"},
    "SUBMITTING": {"SUBMITTING", "SUBMITTED", "COMPLETED", "AWAITING_OPERATOR_REVIEW", "FAILED", "RETRYABLE"},
    "SUBMITTED": {"SUBMITTED_PENDING_REVIEW", "COMPLETED", "FAILED", "RETRYABLE"},
    "SUBMITTED_PENDING_REVIEW": {"COMPLETED", "FAILED", "BLOCKED_EXTERNAL"},
}


def _facebook_submission_pending_review(job: MarketplaceExtensionJob, device: MarketplaceExtensionDevice, prior_result: dict, payload: JobStateRequest, previous_status: str) -> bool:
    """Recognize the browser's positive submit transition without requiring an immediate URL."""
    incoming = payload.result if isinstance(payload.result, dict) else {}
    reconciliation = incoming.get("remote_reconciliation") if isinstance(incoming.get("remote_reconciliation"), dict) else {}
    prior_reconciliation = prior_result.get("remote_reconciliation") if isinstance(prior_result.get("remote_reconciliation"), dict) else {}
    if not (job.marketplace == "facebook" and job.action == "CREATE" and previous_status in {"SUBMITTING", "SUBMITTED"}):
        return False
    if payload.status.strip().upper() not in {"FAILED", "RETRYABLE"} or incoming.get("submission_performed") is not True:
        return False
    # A post-submit empty seller search can reflect review/indexing delay. It is
    # reconciliation evidence, never permission to replay a submitted CREATE.
    if payload.error_code in {"REJECTED_BY_FACEBOOK", "FACEBOOK_POLICY_REJECTION", "FACEBOOK_LISTING_REMOVED"}:
        return False
    version = str(prior_result.get("content_script_version") or "")
    background_version = str(prior_result.get("background_version") or "")
    build_id = str(prior_result.get("content_script_build_id") or "")
    expected_prefix = f"posterpro-{device.extension_version}-"
    if not version or version != str(device.extension_version or "") or background_version != version or not build_id.startswith(expected_prefix):
        return False
    return True


def _facebook_reconciliation_due_at(now: datetime, attempt: int) -> datetime:
    """10m, 30m, 1h, then bounded daily checks; submission never waits on this schedule."""
    delays = (timedelta(minutes=10), timedelta(minutes=20), timedelta(minutes=30), timedelta(hours=24))
    return now + delays[min(max(int(attempt), 0), len(delays) - 1)]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _facebook_description_fingerprint(value: str) -> tuple[str, int]:
    """Match the extension's bounded normalized-description fingerprint."""
    normalized = re.sub(r"\s+", " ", str(value or "").replace("\r", "\n")).strip()
    digest = 0x811C9DC5
    encoded = normalized.encode("utf-16-le", errors="surrogatepass")
    for offset in range(0, len(encoded), 2):
        digest ^= int.from_bytes(encoded[offset:offset + 2], "little")
        digest = (digest * 0x01000193) & 0xFFFFFFFF
    return f"fnv1a32:{digest:08x}", len(normalized)


FACEBOOK_DESCRIPTION_REPAIR_BATCH_ID = "facebook-ebay-rollout-7176bf87661d404987b88a19dc92d6a1"


def _queue_facebook_description_repair(
    db: Session, create_job: MarketplaceExtensionJob, listing: Listing,
    parent: MarketplaceCrosspostJob | None,
) -> MarketplaceExtensionJob | None:
    """Queue a deduplicated, description-only UPDATE after exact reconciliation."""
    batch = db.get(MarketplacePublishBatch, parent.batch_id) if parent and parent.batch_id else None
    if not (
        create_job.marketplace == "facebook" and create_job.action == "CREATE"
        and create_job.error_code == "FACEBOOK_DESCRIPTION_REPAIR_REQUIRED"
        and parent and parent.user_id == create_job.user_id
        and parent.batch_id == FACEBOOK_DESCRIPTION_REPAIR_BATCH_ID
        and batch and batch.user_id == create_job.user_id
        and int(listing.id) in {int(value) for value in (batch.selected_listing_ids or []) if str(value).isdigit()}
    ):
        return None
    remote_id = str(create_job.external_listing_id or "").strip()
    remote_url = str(create_job.external_url or "").strip()
    expected_url = f"https://www.facebook.com/marketplace/item/{remote_id}/"
    if not remote_id or str(remote_url).rstrip("/") != expected_url.rstrip("/"):
        return None
    row = db.execute(select(MarketplaceListing).where(
        MarketplaceListing.listing_id == listing.id,
        MarketplaceListing.marketplace == MarketplaceName.facebook,
    ).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())).scalars().first()
    raw = row.raw_response if row and isinstance(row.raw_response, dict) else {}
    if not row or row.status != MarketplaceListingStatus.PUBLISHED or row.marketplace_listing_id != remote_id:
        return None
    if str(raw.get("external_url") or "").rstrip("/") != expected_url.rstrip("/"):
        return None

    prior_repairs = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.user_id == create_job.user_id,
        MarketplaceExtensionJob.listing_id == listing.id,
        MarketplaceExtensionJob.marketplace == "facebook",
        MarketplaceExtensionJob.action == "UPDATE",
    ).order_by(MarketplaceExtensionJob.created_at.desc(), MarketplaceExtensionJob.id.desc())).scalars().all()
    for prior in prior_repairs:
        prior_payload = (prior.payload_snapshot or {}).get("marketplace_payload", {}) if isinstance(prior.payload_snapshot, dict) else {}
        if prior_payload.get("repair_scope") == "facebook_description_only":
            return prior
        if prior.status in {"QUEUED", "CLAIMED", "NAVIGATING", "FORM_FILLING", "SUBMITTING", "SUBMITTED", "AWAITING_OPERATOR_REVIEW"}:
            return None

    repair, _created = queue_extension_marketplace_action(
        db, user_id=create_job.user_id, listing=listing, marketplace="facebook",
        action="UPDATE", priority=max(5, int(create_job.priority or 0)), crosspost_job=parent,
    )
    snapshot = dict(repair.payload_snapshot or {})
    payload = dict(snapshot.get("marketplace_payload") or {})
    create_payload = (create_job.payload_snapshot or {}).get("marketplace_payload", {})
    payload.update({
        "repair_scope": "facebook_description_only",
        "description": str(create_payload.get("description") or ""),
        "repair_expected_remote_id": remote_id,
        "repair_expected_remote_url": expected_url,
        "repair_expected_title": str(create_payload.get("title") or listing.title or ""),
        "repair_expected_price": create_payload.get("price"),
    })
    snapshot["marketplace_payload"] = payload
    snapshot["description_repair"] = {
        "state": "FACEBOOK_DESCRIPTION_REPAIR_IN_PROGRESS",
        "source_create_job_id": create_job.id,
        "external_listing_id": remote_id,
        "external_url": expected_url,
    }
    repair.payload_snapshot = snapshot
    repair.external_listing_id = remote_id
    repair.external_url = expected_url
    repair.result = {**(repair.result or {}), "description_repair": snapshot["description_repair"], "description_only": True}
    db.add(repair)
    create_result = dict(create_job.result or {})
    reconciliation = dict(create_result.get("remote_reconciliation") or {})
    reconciliation.update({"description_repair_job_id": repair.id, "description_repair_state": "FACEBOOK_DESCRIPTION_REPAIR_IN_PROGRESS"})
    create_result["remote_reconciliation"] = reconciliation
    create_job.result = create_result
    row.raw_response = {**raw, "description_repair_job_id": repair.id, "description_repair_state": "FACEBOOK_DESCRIPTION_REPAIR_IN_PROGRESS"}
    db.add_all([create_job, row])
    db.flush()
    return repair


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


class MarketplaceDiagnosticRequest(BaseModel):
    device_id: int | None = None


class JobStateRequest(BaseModel):
    status: str
    error_code: str | None = Field(default=None, max_length=100)
    error_detail: str | None = Field(default=None, max_length=2000)
    external_listing_id: str | None = Field(default=None, max_length=255)
    external_url: str | None = Field(default=None, max_length=2000)
    result: dict | None = None


class JobProgressRequest(BaseModel):
    stage: str = Field(min_length=1, max_length=80)
    phase: Literal["started", "completed", "checkpoint"] = "checkpoint"
    timestamp: datetime | None = None
    extension_version: str = Field(min_length=1, max_length=32)
    content_build_id: str = Field(min_length=1, max_length=120)
    page_url: str | None = Field(default=None, max_length=500)
    detail: dict = Field(default_factory=dict)


class FacebookReconciliationResultRequest(BaseModel):
    state: Literal["FOUND", "NOT_FOUND", "AMBIGUOUS"]
    external_listing_id: str | None = Field(default=None, max_length=255)
    external_url: str | None = Field(default=None, max_length=2000)
    evidence: dict = Field(default_factory=dict)


class OperatorCompletionRequest(BaseModel):
    confirmed: bool
    external_listing_id: str | None = Field(default=None, max_length=255)
    external_url: str | None = Field(default=None, max_length=2000)


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
        "update_required": _version_tuple(device.extension_version) < _version_tuple(CURRENT_EXTENSION_VERSION),
        "user_id": device.user_id,
        "last_seen_at": _iso(device.last_seen_at),
        "last_claim_at": _iso(device.last_claim_at),
        "revoked": bool(device.revoked_at),
    }


def _job_payload(job: MarketplaceExtensionJob) -> dict:
    return {
        "id": job.id,
        "listing_id": job.listing_id,
        "device_id": job.device_id,
        "marketplace": job.marketplace,
        "action": job.action,
        "status": job.status,
        "requested_at": _iso(job.created_at),
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


def _crosspost_parent_status(parent: MarketplaceCrosspostJob, children: list[MarketplaceExtensionJob]) -> str:
    states = [str(child.status or "").upper() for child in children]
    if any(child.error_code == "SUBMISSION_OUTCOME_UNKNOWN" for child in children):
        return "awaiting_operator_review"
    if any(state in {"QUEUED", "CLAIMED", "NAVIGATING", "FORM_FILLING", "SUBMITTING", "SUBMITTED", "RETRYABLE"} for state in states):
        return "awaiting_operator_review" if "AWAITING_OPERATOR_REVIEW" in states else "running"
    direct_results = ((parent.result_summary or {}).get("results") if isinstance(parent.result_summary, dict) else None) or []
    direct_failures = [item for item in direct_results if isinstance(item, dict) and str(item.get("status") or "").lower() in {"failed", "blocked", "error"}]
    any_failures = bool(direct_failures) or any(state in {"FAILED", "BLOCKED_EXTERNAL"} for state in states)
    any_successes = any(state == "COMPLETED" for state in states) or any(
        isinstance(item, dict) and str(item.get("status") or "").lower() in {"published", "completed", "success", "updated"}
        for item in direct_results
    )
    if any_failures and any_successes:
        return "partial"
    if any_failures:
        return "failed"
    if children and any(state == "SUBMITTED_PENDING_REVIEW" for state in states) and all(
        state in {"SUBMITTED_PENDING_REVIEW", "COMPLETED", "CANCELLED"} for state in states
    ):
        return "submitted_pending_review"
    if children and all(state in {"COMPLETED", "CANCELLED"} for state in states):
        return "completed"
    return "awaiting_operator_review"


def _persist_publish_batch_child_result(db: Session, parent: MarketplaceCrosspostJob, job: MarketplaceExtensionJob, now: datetime) -> None:
    if not parent.batch_id:
        return
    batch = db.get(MarketplacePublishBatch, parent.batch_id)
    if not batch or not isinstance(batch.summary, dict):
        return
    summary = dict(batch.summary)
    items = list(summary.get("items") or [])
    result = job.result if isinstance(job.result, dict) else {}
    reconciliation = result.get("remote_reconciliation") if isinstance(result.get("remote_reconciliation"), dict) else {}
    update_payload = (job.payload_snapshot or {}).get("marketplace_payload", {}) if isinstance(job.payload_snapshot, dict) else {}
    repair_meta = (job.payload_snapshot or {}).get("description_repair", {}) if isinstance(job.payload_snapshot, dict) else {}
    repair_create_job_id = repair_meta.get("source_create_job_id") if update_payload.get("repair_scope") == "facebook_description_only" else None
    if job.action == "UPDATE" and update_payload.get("repair_scope") == "facebook_description_only" and job.status == "COMPLETED":
        item_state = "SUCCESS"
    elif job.error_code == "FACEBOOK_DESCRIPTION_REPAIR_REQUIRED":
        item_state = "DESCRIPTION_REPAIR_REQUIRED"
    elif job.status == "COMPLETED":
        if reconciliation.get("known_remote_presence") is True:
            item_state = "ALREADY_EXISTS"
        elif reconciliation.get("status") in {"FOUND", "FOUND_DESCRIPTION_REPAIR_REQUIRED"} or reconciliation.get("remote_exists_verified") is True:
            item_state = "LIVE_VERIFIED"
        else:
            item_state = "SUCCESS"
    elif job.status == "SUBMITTED_PENDING_REVIEW":
        item_state = "SUBMITTED_PENDING_REVIEW"
    elif job.error_code == "SUBMISSION_OUTCOME_UNKNOWN":
        item_state = "RECONCILIATION_REQUIRED"
    elif job.error_code == "REMOTE_RECONCILIATION_NOT_FOUND":
        item_state = "PENDING"
    elif job.status in {"FAILED", "BLOCKED_EXTERNAL"}:
        item_state = "BLOCKED"
    else:
        item_state = str(job.status or "PROCESSING").upper()
    for item in items:
        if isinstance(item, dict) and (item.get("extension_job_id") == job.id or (repair_create_job_id and item.get("extension_job_id") == repair_create_job_id)):
            item.update({
                "status": item_state, "remote_id": job.external_listing_id,
                "remote_url": job.external_url, "error_code": job.error_code,
                "reconciliation": reconciliation or None,
                "submission_performed": result.get("submission_performed") is True,
                "submitted_at": result.get("submitted_at"),
                "submission_duration_seconds": result.get("submission_duration_seconds"),
            })
            break
    summary["items"] = items
    summary["success"] = sum(item.get("status") in {"SUCCESS", "LIVE_VERIFIED", "ALREADY_EXISTS"} for item in items if isinstance(item, dict))
    summary["already_existed"] = sum(item.get("status") == "ALREADY_EXISTS" for item in items if isinstance(item, dict))
    summary["reconciliation_required"] = sum(item.get("status") == "RECONCILIATION_REQUIRED" for item in items if isinstance(item, dict))
    summary["description_repair_required"] = sum(item.get("status") == "DESCRIPTION_REPAIR_REQUIRED" for item in items if isinstance(item, dict))
    summary["blocked"] = sum(item.get("status") == "BLOCKED" for item in items if isinstance(item, dict))
    summary["pending"] = sum(item.get("status") in {"PENDING", "PAUSED"} for item in items if isinstance(item, dict))
    summary["submitted_pending_review"] = sum(item.get("status") == "SUBMITTED_PENDING_REVIEW" for item in items if isinstance(item, dict))
    summary["live_verified"] = sum(item.get("status") == "LIVE_VERIFIED" for item in items if isinstance(item, dict))
    summary["updated_at"] = _iso(now)
    submitted_items = [item for item in items if isinstance(item, dict) and item.get("submitted_at")]
    durations = sorted(float(item["submission_duration_seconds"]) for item in submitted_items if isinstance(item.get("submission_duration_seconds"), (int, float)) and item["submission_duration_seconds"] >= 0)
    summary["average_seconds_to_submit"] = round(sum(durations) / len(durations), 2) if durations else None
    summary["median_seconds_to_submit"] = round((durations[(len(durations) - 1) // 2] + durations[len(durations) // 2]) / 2, 2) if durations else None
    last_ten = sorted((item for item in submitted_items if isinstance(item.get("submission_duration_seconds"), (int, float))), key=lambda item: str(item.get("submitted_at") or ""), reverse=True)[:10]
    summary["last_10_average_seconds_to_submit"] = round(sum(float(item["submission_duration_seconds"]) for item in last_ten) / len(last_ten), 2) if last_ten else None
    remaining_to_submit = summary["pending"]
    summary["estimated_seconds_to_submit_remaining"] = round(summary["last_10_average_seconds_to_submit"] * remaining_to_submit, 2) if summary["last_10_average_seconds_to_submit"] is not None else None
    batch.summary = summary
    db.add(batch)


def _diagnostic_payload(db: Session, job: MarketplaceExtensionJob) -> dict:
    payload = _job_payload(job)
    payload["current_version"] = CURRENT_EXTENSION_VERSION
    payload["minimum_version"] = MINIMUM_EXTENSION_VERSION
    device = db.get(MarketplaceExtensionDevice, job.device_id) if job.device_id else None
    payload["device"] = ({
        "id": device.id,
        "name": _safe_structural_text(device.name, 48) or f"Device {device.id}",
        "browser": _safe_structural_text(device.browser, 24) or "unknown",
        "extension_version": device.extension_version,
        "last_seen_at": _iso(device.last_seen_at),
    } if device else None)
    return payload


def _safe_structural_text(value, limit: int = 100) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = " ".join(value.split())[:limit]
    if not candidate or "@" in candidate or re.search(
        r"(?i)(password|cookie|token|bearer|secret|session|\b[A-F0-9]{24,}\b|\b\d{3}[- .]?\d{3}[- .]?\d{4}\b)",
        candidate,
    ):
        return None
    return candidate if re.fullmatch(r"[\w .,:/()&+’'’-]+", candidate, flags=re.UNICODE) else None


def _safe_execution_diagnostic(value: dict | None, marketplace: str) -> dict | None:
    if not isinstance(value, dict):
        return None
    safe: dict = {}
    for key in ("stage", "last_successful_stage", "error_code"):
        candidate = value.get(key)
        if isinstance(candidate, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", candidate):
            safe[key] = candidate
    for key in ("background_version",):
        candidate = value.get(key)
        if isinstance(candidate, str) and re.fullmatch(r"\d+(?:\.\d+){1,3}", candidate):
            safe[key] = candidate
    for key in ("started_at", "failed_at", "identity_verified_at"):
        candidate = value.get(key)
        if isinstance(candidate, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T[^\s]{1,40}", candidate):
            safe[key] = candidate[:64]
    tab_id = value.get("tab_id")
    if isinstance(tab_id, int) and not isinstance(tab_id, bool) and 0 < tab_id < 2**31:
        safe["tab_id"] = tab_id
    for key in ("requested_url", "page_url"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            origin = candidate.get("origin")
            path = candidate.get("path")
            if isinstance(origin, str) and _marketplace_url_matches(marketplace, origin):
                parsed = urlsplit(origin)
                safe_path = path if isinstance(path, str) and path.startswith("/") else "/"
                safe[key] = {"origin": f"{parsed.scheme}://{parsed.netloc}", "path": safe_path.split("?", 1)[0].split("#", 1)[0][:240]}
    if isinstance(value.get("document_ready_state"), str) and value["document_ready_state"] in {"loading", "interactive", "complete"}:
        safe["document_ready_state"] = value["document_ready_state"]
    if isinstance(value.get("identity_attempts"), int):
        safe["identity_attempts"] = max(0, min(value["identity_attempts"], 4))
    if isinstance(value.get("error_name"), str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value["error_name"]):
        safe["error_name"] = value["error_name"]
    if message := _safe_structural_text(value.get("error_message"), 600):
        safe["error_message"] = message
    identity = value.get("identity")
    if isinstance(identity, dict):
        safe["identity"] = {
            key: text for key in ("extension_version", "content_script_version", "content_script_build_id")
            if (text := _safe_structural_text(identity.get(key), 100))
        }
        for key in ("content_script_injected_at", "page_loaded_at"):
            candidate = identity.get(key)
            if isinstance(candidate, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T[^\s]{1,40}", candidate):
                safe["identity"][key] = candidate[:64]
    events = value.get("events")
    if isinstance(events, list):
        safe_events = []
        for event in events[-20:]:
            if not isinstance(event, dict):
                continue
            item = {}
            for key in ("stage", "status", "error_name"):
                candidate = event.get(key)
                valid = (
                    isinstance(candidate, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", candidate)
                    if key == "stage" else
                    isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_ -]{0,79}", candidate)
                )
                if valid:
                    item[key] = candidate
            for key in ("at", "completed_at"):
                candidate = event.get(key)
                if isinstance(candidate, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T[^\s]{1,40}", candidate):
                    item[key] = candidate[:64]
            for key in ("tab_id",):
                candidate = event.get(key)
                if isinstance(candidate, int) and not isinstance(candidate, bool) and 0 < candidate < 2**31:
                    item[key] = candidate
            for key in ("page_url", "requested_url"):
                candidate = event.get(key)
                if isinstance(candidate, dict):
                    origin = candidate.get("origin")
                    if isinstance(origin, str) and _marketplace_url_matches(marketplace, origin):
                        parsed = urlsplit(origin)
                        path = candidate.get("path") if isinstance(candidate.get("path"), str) else "/"
                        item[key] = {"origin": f"{parsed.scheme}://{parsed.netloc}", "path": path.split("?", 1)[0].split("#", 1)[0][:240]}
            if message := _safe_structural_text(event.get("error_message"), 600):
                item["error_message"] = message
            if item:
                safe_events.append(item)
        safe["events"] = safe_events
    return safe or None


def _safe_selector_diagnostic(value, field_name: str) -> dict | None:
    if not isinstance(value, dict) or str(value.get("field") or "") != field_name:
        return None
    safe = {"field": field_name}
    path = value.get("page_path")
    if isinstance(path, str) and path.startswith("/"):
        safe_path = path.split("?", 1)[0].split("#", 1)[0]
        segments = [segment for segment in safe_path.split("/") if segment]
        safe["page_path"] = "/" + "/".join(":id" if (segment.isdigit() or len(segment) > 48 or re.search(r"(?i)(token|secret|session|auth|key)", segment) or re.fullmatch(r"[a-z0-9_-]{24,}", segment, re.I)) else segment for segment in segments[:12])
    selectors = value.get("selectors_tried")
    if isinstance(selectors, list):
        safe["selectors_tried"] = [item[:140] for item in selectors[:8] if isinstance(item, str) and len(item) <= 140 and re.fullmatch(r"[A-Za-z0-9_#.[\] ='\"():->,*+-]+", item)]
    controls = value.get("controls")
    safe_controls = []
    field_aliases = {
        "category": ("category", "department", "item type"), "condition": ("condition",), "availability": ("availability", "quantity"),
        "department": ("department",), "subcategory": ("subcategory", "category"), "shipping": ("shipping", "delivery", "parcel"),
        "shipping_payer": ("shipping", "delivery", "payer"), "shipping_method": ("shipping", "delivery", "method"), "package": ("package", "parcel", "weight"),
    }
    if isinstance(controls, list):
        for control in controls[:8]:
            if not isinstance(control, dict):
                continue
            item = {}
            tag = str(control.get("tag") or "").lower()
            role = str(control.get("role") or "").lower()
            if tag in {"input", "textarea", "select", "button", "div", "span", "label"}:
                item["tag"] = tag
            if role in {"textbox", "combobox", "button", "option", "listbox", "radio", "checkbox", "switch"}:
                item["role"] = role
            attributes = {}
            for key, limit in (("aria_label", 80), ("name", 64), ("placeholder", 80), ("nearby_label", 80)):
                text = _safe_structural_text(control.get(key), limit)
                if text:
                    attributes[key] = text
            label_hint = " ".join(attributes.values()).lower()
            associated = any(alias in label_hint for alias in field_aliases.get(field_name, (field_name,)))
            if associated:
                item.update(attributes)
            options = control.get("option_labels")
            if isinstance(options, list) and associated:
                item["option_labels"] = [text for option in options[:12] if (text := _safe_structural_text(option, 80))]
            if item:
                safe_controls.append(item)
    safe["controls"] = safe_controls
    hints = value.get("expected_label_hints")
    if isinstance(hints, list):
        safe["expected_label_hints"] = [text for hint in hints[:8] if (text := _safe_structural_text(hint, 80))]
    visible_options = value.get("visible_option_labels")
    if isinstance(visible_options, list):
        safe["visible_option_labels"] = [text for option in visible_options[:20] if (text := _safe_structural_text(option, 90))]
    if field_name in {"category", "condition", "shipping"}:
        for key in ("stage", "label", "field_container", "trigger_clicked", "selected_option", "post_selection_field_text", "visible_selected_breadcrumb"):
            candidate = value.get(key)
            if key == "stage" and isinstance(candidate, str) and re.fullmatch(r"[A-Z_]{3,64}", candidate):
                safe[key] = candidate
            elif key != "stage" and (text := _safe_structural_text(candidate, 180)):
                safe[key] = text
        for key in ("dom_menu_count_before", "dom_menu_count_after"):
            candidate = value.get(key)
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                safe[key] = max(0, min(candidate, 500))
        for key in ("dialog_visible_before", "dialog_visible_after", "selected_state"):
            if isinstance(value.get(key), bool):
                safe[key] = value[key]
        structure = value.get("clicked_element_structure")
        if isinstance(structure, dict):
            safe_structure = {}
            for part in ("element", "actionable_ancestor"):
                candidate = structure.get(part)
                if isinstance(candidate, dict):
                    safe_node = {}
                    tag = candidate.get("tag")
                    if isinstance(tag, str) and tag.lower() in {"button", "div", "span", "a", "li", "label", "input"}:
                        safe_node["tag"] = tag.lower()
                    for source_key, target_key, limit in (("role", "role", 32), ("aria_selected", "aria_selected", 20), ("aria_checked", "aria_checked", 20), ("tabindex", "tabindex", 8), ("data_testid", "data_testid", 80), ("href", "href", 120), ("text", "text", 160)):
                        candidate_text = candidate.get(source_key)
                        if isinstance(candidate_text, str) and source_key != "href":
                            candidate_text = _safe_structural_text(candidate_text, limit)
                        elif isinstance(candidate_text, str):
                            candidate_text = candidate_text if candidate_text.startswith("/") and not re.search(r"(?i)(token|secret|session|auth|key)", candidate_text) else None
                        if candidate_text:
                            safe_node[target_key] = candidate_text
                    if safe_node:
                        safe_structure[part] = safe_node
            if safe_structure:
                safe["clicked_element_structure"] = safe_structure
        selected_input = value.get("selected_input")
        if isinstance(selected_input, dict):
            safe_input = {}
            if isinstance(selected_input.get("checked"), bool):
                safe_input["checked"] = selected_input["checked"]
            for key in ("value", "aria_label", "card_text"):
                if text := _safe_structural_text(selected_input.get(key), 140):
                    safe_input[key] = text
            if safe_input:
                safe["selected_input"] = safe_input
        for key in ("first_visible_options", "category_path", "dialog_or_menu_role"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                safe[key] = [text for part in candidate[:20] if (text := _safe_structural_text(str(part), 100))]
        for key in ("condition_before", "condition_after"):
            candidate = value.get(key)
            if isinstance(candidate, dict):
                entry = {}
                if isinstance(candidate.get("checked"), bool):
                    entry["checked"] = candidate["checked"]
                for subkey in ("value", "selected_state"):
                    if text := _safe_structural_text(candidate.get(subkey), 80):
                        entry[subkey] = text
                choices = candidate.get("choices")
                if isinstance(choices, list):
                    entry["choices"] = [{"value": _safe_structural_text(choice.get("value"), 80), "checked": choice.get("checked"), "label": _safe_structural_text(choice.get("label"), 140)} for choice in choices[:12] if isinstance(choice, dict) and isinstance(choice.get("checked"), bool)]
                safe[key] = entry
        for key in ("delivery_before", "delivery_after"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                safe[key] = [{"value": _safe_structural_text(row.get("value"), 80), "checked": row.get("checked"), "label": _safe_structural_text(row.get("label"), 140)} for row in candidate[:12] if isinstance(row, dict) and isinstance(row.get("checked"), bool)]
    elif field_name == "location":
        stage = value.get("stage")
        if isinstance(stage, str) and re.fullmatch(r"[A-Z_]{3,64}", stage):
            safe["stage"] = stage
        if isinstance(value.get("next_clicked"), bool):
            safe["next_clicked"] = value["next_clicked"]
        for key in ("location_requirement_satisfied", "location_value_verified"):
            if isinstance(value.get(key), bool):
                safe[key] = value[key]
        if value.get("verified_mode") in {"FACEBOOK_VISIBLE_LOCATION", "FACEBOOK_LOCATION_INPUT", "FACEBOOK_LOCATION_PICKER", "FACEBOOK_INHERITED_LOCATION"}:
            safe["verified_mode"] = value["verified_mode"]
        stages = value.get("stages")
        if isinstance(stages, list):
            safe_stages = []
            for item in stages[:16]:
                if not isinstance(item, dict):
                    continue
                safe_item = {}
                if isinstance(item.get("phase"), str) and re.fullmatch(r"[A-Z_]{3,48}", item["phase"]):
                    safe_item["phase"] = item["phase"]
                if isinstance(item.get("step"), int) and not isinstance(item["step"], bool):
                    safe_item["step"] = max(0, min(item["step"], 4))
                path = item.get("page_path")
                if isinstance(path, str) and path.startswith("/"):
                    safe_item["page_path"] = path.split("?", 1)[0].split("#", 1)[0][:180]
                for key in ("headings", "button_labels", "location_related_labels", "final_publish_labels"):
                    values = item.get(key)
                    if isinstance(values, list):
                        safe_item[key] = [text for candidate in values[:24] if (text := _safe_structural_text(candidate, 140))]
                buttons = item.get("buttons")
                if isinstance(buttons, list):
                    safe_buttons = []
                    for button in buttons[:24]:
                        if not isinstance(button, dict):
                            continue
                        label = _safe_structural_text(button.get("label"), 100)
                        if label:
                            safe_buttons.append({"label": label, "enabled": bool(button.get("enabled"))})
                    safe_item["buttons"] = safe_buttons
                for key in ("final_publish_boundary", "final_publish_control_present", "location_validation_error", "user_presence_challenge"):
                    if isinstance(item.get(key), bool):
                        safe_item[key] = item[key]
                for key in ("semantic_controls", "field_labels", "selected_controls"):
                    values = item.get(key)
                    if isinstance(values, list):
                        rows = []
                        for row in values[:24]:
                            if not isinstance(row, dict):
                                continue
                            safe_row = {}
                            for name, limit in (("role", 32), ("label", 100), ("type", 24), ("placeholder", 80), ("aria_label", 80), ("expanded", 12), ("selected", 12)):
                                if text := _safe_structural_text(row.get(name), limit):
                                    safe_row[name] = text
                            for name in ("checked", "disabled", "required"):
                                if isinstance(row.get(name), bool):
                                    safe_row[name] = row[name]
                            if safe_row:
                                rows.append(safe_row)
                        safe_item[key] = rows
                safe_stages.append(safe_item)
            safe["stages"] = safe_stages
    return safe


def _safe_diagnostic_result(value: dict | None, marketplace: str) -> dict | None:
    """Persist only structured, non-sensitive capability facts from the extension."""
    if not isinstance(value, dict):
        return None
    safe: dict = {"marketplace": marketplace, "action": "DIAGNOSTIC", "submission_performed": False}
    for key in ("error_code",):
        candidate = value.get(key)
        if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", candidate):
            safe[key] = candidate
    if message := _safe_structural_text(value.get("error_detail"), 1800):
        safe["error_detail"] = message
    execution_diagnostic = _safe_execution_diagnostic(value.get("execution_diagnostic"), marketplace)
    if execution_diagnostic:
        safe["execution_diagnostic"] = execution_diagnostic
    for key in ("background_version", "extension_version", "content_script_version"):
        candidate = value.get(key)
        if isinstance(candidate, str) and re.fullmatch(r"\d+(?:\.\d+){1,3}", candidate):
            safe[key] = candidate
    for key in ("location_requirement_satisfied", "location_value_verified"):
        if isinstance(value.get(key), bool):
            safe[key] = value[key]
    if value.get("location_verified_mode") in {"FACEBOOK_VISIBLE_LOCATION", "FACEBOOK_LOCATION_INPUT", "FACEBOOK_LOCATION_PICKER", "FACEBOOK_INHERITED_LOCATION"}:
        safe["location_verified_mode"] = value["location_verified_mode"]
    candidate = value.get("content_script_build_id")
    if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", candidate):
        safe["content_script_build_id"] = candidate
    for key in ("content_script_injected_at", "page_loaded_at"):
        candidate = value.get(key)
        if isinstance(candidate, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T[^\s]{1,40}", candidate):
            safe[key] = candidate[:64]
    allowed_enums = {
        "login_state": {"LOGGED_IN", "LOGIN_REQUIRED", "CHECKPOINT_REQUIRED", "CAPTCHA_REQUIRED", "ACCOUNT_RESTRICTION", "UNKNOWN"},
        "page_state": {"FORM_AVAILABLE", "FORM_CHANGED", "FORM_HYDRATION_TIMEOUT", "MARKETPLACE_FORM_CHANGED", "LOGIN_REQUIRED", "CHECKPOINT_REQUIRED", "CAPTCHA_REQUIRED", "ACCOUNT_RESTRICTION", "UNKNOWN"},
        "stage": {"LOGIN_REQUIRED", "CHECKPOINT_REQUIRED", "CAPTCHA_REQUIRED", "ACCOUNT_RESTRICTION", "FORM_HYDRATION_TIMEOUT", "MARKETPLACE_FORM_CHANGED", "UNKNOWN", "FORM_DETECTED", "TESTING_FIELDS", "COMPLETED", "FAILED"},
    }
    for key, choices in allowed_enums.items():
        candidate = str(value.get(key) or "").upper()
        if candidate in choices:
            safe[key] = candidate
    if isinstance(value.get("operator_review_required"), bool):
        safe["operator_review_required"] = value["operator_review_required"]
    for key in ("error_code", "selector_strategy"):
        candidate = str(value.get(key) or "")
        if key == "error_code" and candidate and all(char.isalnum() or char in "_-" for char in candidate):
            safe[key] = candidate[:100]
        elif key == "selector_strategy" and candidate == "central_marketplace_map_then_semantic_labels":
            safe[key] = candidate
    page_url = value.get("page_url")
    if page_url and _marketplace_url_matches(marketplace, str(page_url)):
        parsed = urlsplit(str(page_url))
        safe["page_url"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path[:500], "", ""))
    readiness = value.get("form_readiness")
    if isinstance(readiness, dict):
        safe_readiness = {}
        if isinstance(readiness.get("form_ready_wait_started"), bool):
            safe_readiness["form_ready_wait_started"] = readiness["form_ready_wait_started"]
        if isinstance(readiness.get("form_ready_wait_ms"), int) and not isinstance(readiness.get("form_ready_wait_ms"), bool):
            safe_readiness["form_ready_wait_ms"] = max(0, min(readiness["form_ready_wait_ms"], 15000))
        anchors = readiness.get("form_ready_anchors")
        if isinstance(anchors, dict):
            safe_anchors = {key: anchors[key] for key in ("title", "price", "description", "photos", "visible_form") if isinstance(anchors.get(key), bool)}
            if isinstance(anchors.get("core_anchor_count"), int) and not isinstance(anchors.get("core_anchor_count"), bool):
                safe_anchors["core_anchor_count"] = max(0, min(anchors["core_anchor_count"], 10))
            safe_readiness["form_ready_anchors"] = safe_anchors
        safe["form_readiness"] = safe_readiness
    evidence = value.get("page_evidence")
    if isinstance(evidence, dict):
        safe_evidence = {}
        current_url = evidence.get("current_url")
        if isinstance(current_url, str) and _marketplace_url_matches(marketplace, current_url):
            parsed = urlsplit(current_url)
            safe_evidence["current_url"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path[:240], "", ""))
        for key, limit in (("page_title", 160), ("body_text_summary", 360)):
            if text := _safe_structural_text(evidence.get(key), limit):
                safe_evidence[key] = text
        if isinstance(evidence.get("document_ready_state"), str) and evidence.get("document_ready_state") in {"loading", "interactive", "complete"}:
            safe_evidence["document_ready_state"] = evidence["document_ready_state"]
        for key in ("loading_indicators", "captcha_or_checkpoint_text", "sell_shell_visible"):
            if isinstance(evidence.get(key), bool):
                safe_evidence[key] = evidence[key]
        if isinstance(evidence.get("login_state"), str) and evidence.get("login_state") in {"LOGGED_IN", "LOGIN_REQUIRED", "CHECKPOINT_REQUIRED", "CAPTCHA_REQUIRED", "ACCOUNT_RESTRICTION", "UNKNOWN"}:
            safe_evidence["login_state"] = evidence["login_state"]
        headings = evidence.get("visible_headings")
        if isinstance(headings, list):
            safe_evidence["visible_headings"] = [text for item in headings[:8] if (text := _safe_structural_text(item, 100))]
        counts = evidence.get("counts")
        if isinstance(counts, dict):
            safe_evidence["counts"] = {key: max(0, min(counts[key], 500)) for key in ("inputs", "textareas", "buttons", "file_inputs", "visible_forms") if isinstance(counts.get(key), int) and not isinstance(counts.get(key), bool)}
        anchors = evidence.get("form_anchors")
        if isinstance(anchors, dict):
            safe_evidence["form_anchors"] = {key: anchors[key] for key in ("title", "price", "description", "photos", "visible_form") if isinstance(anchors.get(key), bool)}
            if isinstance(anchors.get("core_anchor_count"), int) and not isinstance(anchors.get("core_anchor_count"), bool):
                safe_evidence["form_anchors"]["core_anchor_count"] = max(0, min(anchors["core_anchor_count"], 10))
        safe["page_evidence"] = safe_evidence
    for key in ("uploaded_image_count",):
        try:
            safe[key] = max(0, min(30, int(value.get(key) or 0)))
        except (TypeError, ValueError):
            pass
    fields = value.get("field_results")
    if isinstance(fields, list):
        safe_fields = []
        for row in fields[:40]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("field") or "")[:64]
            if not name or not all(char.isalnum() or char == "_" for char in name):
                continue
            item = {"field": name}
            for flag in ("required", "detected", "attempted", "filled"):
                if isinstance(row.get(flag), bool):
                    item[flag] = row[flag]
            verified = row.get("verified_value")
            if isinstance(verified, (str, int, float)):
                # This value is the extension's entered/mapped value, never page content.
                verified_text = str(verified)[:160]
                secret_shape = re.search(r"(?i)(password|cookie|session.?token|access.?token|refresh.?token|bearer\s+|sk-[a-z0-9_-]{12,}|ya29\.[a-z0-9._-]{12,}|https?://|[?&][a-z0-9_-]+=|@[a-z0-9.-]+)", verified_text)
                item["verified_value"] = None if secret_shape else verified_text
            error_code = str(row.get("error_code") or "")
            if error_code and all(char.isalnum() or char in "_-" for char in error_code):
                item["error_code"] = error_code[:100]
            if item.get("filled") is not True:
                selector_diagnostic = _safe_selector_diagnostic(row.get("selector_diagnostic"), name)
                if selector_diagnostic:
                    item["selector_diagnostic"] = selector_diagnostic
            interaction_diagnostic = _safe_selector_diagnostic(
                {"field": name, **row["interaction_diagnostic"]}, name
            ) if name in {"category", "condition", "shipping", "location"} and isinstance(row.get("interaction_diagnostic"), dict) else None
            if interaction_diagnostic:
                interaction_diagnostic.pop("field", None)
                item["interaction_diagnostic"] = interaction_diagnostic
            safe_fields.append(item)
        safe["field_results"] = safe_fields
    missing = value.get("missing_required_fields")
    if isinstance(missing, list):
        safe["missing_required_fields"] = [str(item)[:64] for item in missing[:40] if all(char.isalnum() or char in "_-" for char in str(item))]
    safe["form_detected"] = safe.get("page_state") == "FORM_AVAILABLE"
    required_results = [field for field in safe.get("field_results", []) if field.get("required") is True]
    missing_required = list(safe.get("missing_required_fields") or [])
    # Some extension versions reported a failed field only in field_results and
    # omitted missing_required_fields. Preserve a deterministic explanation
    # instead of turning a visible form-fill into an unexplained dashboard
    # failure.
    for field in required_results:
        name = field.get("field")
        if name and field.get("filled") is not True and name not in missing_required:
            missing_required.append(name)
    safe["missing_required_fields"] = missing_required[:40]
    safe["verification_stage"] = (
        "FORM_OPENED_FIELD_CHECK_INCOMPLETE" if safe["form_detected"] and missing_required
        else "FORM_OPENED" if safe["form_detected"]
        else "FORM_NOT_VERIFIED"
    )
    safe["capability_ready"] = bool(
        safe.get("login_state") == "LOGGED_IN"
        and safe.get("background_version") == CURRENT_EXTENSION_VERSION
        and safe.get("content_script_version") == CURRENT_EXTENSION_VERSION
        and safe["form_detected"]
        and required_results
        and all(field.get("detected") is True and field.get("attempted") is True and field.get("filled") is True for field in required_results)
        and not missing_required
    )
    return safe


def _diagnostic_external_block_detail(*, marketplace: str, result: dict | None, supplied: str | None = None) -> str:
    """Turn an extension transport failure into an actionable operator message.

    BLOCKED_EXTERNAL is deliberately used when PosterPro cannot verify the live
    marketplace page.  An opaque UNKNOWN error made this look like a listing
    failure even though no submission was attempted.
    """
    generic_external = {
        "the marketplace requires attention before posterpro can inspect its listing form.",
        "the marketplace requires an external security or account step.",
    }
    if supplied and supplied.strip().lower() not in generic_external:
        return supplied[:2000]
    safe = result if isinstance(result, dict) else {}
    market = str(marketplace or "marketplace").capitalize()
    login_state = str(safe.get("login_state") or "UNKNOWN").upper()
    page_state = str(safe.get("page_state") or "UNKNOWN").upper()
    if login_state in {"LOGIN_REQUIRED", "CHECKPOINT_REQUIRED", "CAPTCHA_REQUIRED", "ACCOUNT_RESTRICTION"}:
        return f"{market} is not ready in the paired browser ({login_state.replace('_', ' ').lower()}). Open {market} in that Chrome profile, resolve the account prompt, then run the diagnostic again. No listing was submitted."
    if login_state == "UNKNOWN" and page_state == "UNKNOWN":
        return f"PosterPro could not see a live {market} page or create form in the paired browser. Open {market} in the paired Chrome profile, sign in, leave the create form open, and run the diagnostic again. No listing was submitted."
    if not bool(safe.get("form_detected")):
        return f"PosterPro reached the paired browser but could not verify the {market} create form. Open the {market} create flow in that browser and run the diagnostic again. No listing was submitted."
    missing = safe.get("missing_required_fields") or []
    if missing:
        fields = ", ".join(str(item) for item in missing[:12])
        return f"The {market} form was visible, but required fields could not be verified: {fields}. No listing was submitted."
    return f"The {market} browser capability test was blocked before a verified create form was available. Run the diagnostic again after opening the marketplace in the paired browser. No listing was submitted."


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
    # A fresh install/re-pair for the same browser profile supersedes the
    # previous installation.  Leaving the old token active makes the
    # dashboard show multiple "connected" browsers and allows a stale
    # extension to claim new work.  Retire only the matching browser/profile
    # (other intentionally named devices remain available).
    device_name = payload.device_name.strip() or "PosterPro browser"
    browser = (payload.browser or "").strip() or None
    stale_query = select(MarketplaceExtensionDevice).where(
        MarketplaceExtensionDevice.user_id == row.user_id,
        MarketplaceExtensionDevice.revoked_at.is_(None),
        func.lower(MarketplaceExtensionDevice.name) == device_name.lower(),
    )
    if browser:
        # Very old extension builds did not report a browser name. Treat an
        # unknown browser on the same default profile as stale as well.
        stale_query = stale_query.where(
            or_(
                func.lower(MarketplaceExtensionDevice.browser) == browser.lower(),
                MarketplaceExtensionDevice.browser.is_(None),
            )
        )
    stale_devices = db.execute(stale_query).scalars().all()
    stale_device_ids = [device.id for device in stale_devices if device.id]
    for stale_device in stale_devices:
        stale_device.revoked_at = now
    if stale_device_ids:
        # Do not strand work on a token that has just been retired.  Queued or
        # leased work returns to the normal queue and can be claimed by the
        # newly paired extension.
        db.execute(
            update(MarketplaceExtensionJob)
            .where(
                MarketplaceExtensionJob.user_id == row.user_id,
                MarketplaceExtensionJob.device_id.in_(stale_device_ids),
                MarketplaceExtensionJob.status.in_(["CLAIMED", "NAVIGATING", "FORM_DETECTED", "TESTING_FIELDS", "FORM_FILLING", "RETRYABLE"]),
            )
            .values(
                device_id=None,
                status="RETRYABLE",
                lease_expires_at=None,
                claimed_at=None,
                error_code="STALE_EXTENSION_REPLACED",
                error_detail="The browser extension was replaced by a newer paired installation.",
            )
        )
    device = MarketplaceExtensionDevice(
        user_id=row.user_id,
        device_key=secrets.token_urlsafe(18),
        name=device_name,
        browser=browser,
        extension_version=payload.extension_version,
        token_hash=_digest(token),
        last_seen_at=now,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return {
        "device": _device_payload(device),
        "device_token": token,
        "replaced_device_ids": stale_device_ids,
        "stale_connections_removed": len(stale_device_ids),
    }


@router.get("/browser-extension/devices")
def list_extension_devices(
    current_device_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    devices = db.execute(
        select(MarketplaceExtensionDevice).where(MarketplaceExtensionDevice.user_id == current_user.id)
        .order_by(MarketplaceExtensionDevice.created_at.desc())
    ).scalars().all()
    jobs = db.execute(
        select(MarketplaceExtensionJob).where(MarketplaceExtensionJob.user_id == current_user.id)
        .order_by(MarketplaceExtensionJob.created_at.desc()).limit(100)
    ).scalars().all()
    now = datetime.now(UTC).replace(tzinfo=None)
    recent_cutoff = now - EXTENSION_JOB_CLAIM_WINDOW
    pending_jobs = db.execute(
        select(func.count(MarketplaceExtensionJob.id)).where(
            MarketplaceExtensionJob.user_id == current_user.id,
            MarketplaceExtensionJob.created_at >= recent_cutoff,
            MarketplaceExtensionJob.status.in_(["QUEUED", "RETRYABLE"]),
            or_(
                MarketplaceExtensionJob.error_code.is_(None),
                MarketplaceExtensionJob.error_code != "SUBMISSION_OUTCOME_UNKNOWN",
            ),
        )
    ).scalar_one()
    active = [job for job in jobs if job.status in {"CLAIMED", "NAVIGATING", "FORM_FILLING", "AWAITING_OPERATOR_REVIEW", "SUBMITTING"}]
    online_devices = [device for device in devices if device.last_seen_at and (now - device.last_seen_at).total_seconds() <= 120 and not device.revoked_at]
    latest = max(devices, key=lambda device: (device.last_seen_at or datetime.min, device.id or 0), default=None)
    latest_online = max(online_devices, key=lambda device: (device.last_seen_at or datetime.min, device.id or 0), default=None)
    compatible = bool(latest_online and _version_tuple(latest_online.extension_version) >= _version_tuple(MINIMUM_EXTENSION_VERSION))
    correlated = next((device for device in devices if current_device_id and device.id == current_device_id), None)
    correlated_online = bool(correlated and correlated in online_devices and not correlated.revoked_at)
    correlated_compatible = bool(correlated_online and _version_tuple(correlated.extension_version) >= _version_tuple(MINIMUM_EXTENSION_VERSION))
    return {
        "devices": [_device_payload(device) for device in devices],
        "current_version": CURRENT_EXTENSION_VERSION,
        "minimum_version": MINIMUM_EXTENSION_VERSION,
        "pending_jobs": int(pending_jobs or 0),
        "active_jobs": [_job_payload(job) for job in active[:20]],
        "recent_failures": [_job_payload(job) for job in jobs if job.status == "FAILED"][:20],
        "diagnostics": {
            "page_extension_detected": None,
            "page_bridge_active": None,
            "registered_devices": len(devices),
            "online_registered_devices": len(online_devices),
            "detected_version": latest_online.extension_version if latest_online else None,
            "minimum_version": MINIMUM_EXTENSION_VERSION,
            "device_registration": bool(latest_online and not latest_online.revoked_at),
            "device_token_present": bool(latest_online and latest_online.token_hash),
            "heartbeat_last_seen": _iso(latest_online.last_seen_at) if latest_online else None,
            "current_browser_recognized": correlated_online if current_device_id else None,
            "current_device_id": correlated.id if correlated else None,
            "current_user_matched": bool(correlated and correlated.user_id == current_user.id) if current_device_id else bool(latest_online and latest_online.user_id == current_user.id),
            "tenant_match": "NOT_MODELED_DEVICE_USER_ONLY",
            "job_transport_ready": correlated_compatible if current_device_id else bool(latest_online and compatible),
            "transport_requirements": {"registered": correlated is not None if current_device_id else bool(latest_online), "compatible_version": correlated_compatible if current_device_id else compatible, "fresh_heartbeat": correlated_online if current_device_id else bool(latest_online), "revoked": bool(correlated and correlated.revoked_at) if current_device_id else bool(latest_online and latest_online.revoked_at)},
        },
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
        "update_required": _version_tuple(payload.extension_version) < _version_tuple(CURRENT_EXTENSION_VERSION),
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


@router.post("/browser-extension/diagnostics/{marketplace}")
def create_marketplace_diagnostic(
    marketplace: str,
    payload: MarketplaceDiagnosticRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue a synthetic, non-submitting live-form capability test."""
    market = marketplace.strip().lower()
    if market not in {"facebook", "mercari", "poshmark", "vinted", "offerup"}:
        raise HTTPException(status_code=422, detail="A live form diagnostic is not available for this marketplace")
    if market == "mercari":
        raise HTTPException(status_code=409, detail={
            "code": "MERCARI_AUTHORIZED_PROVIDER_REQUIRED",
            "message": "Mercari's current policy prohibits automated interfaces not provided by Mercari. PosterPro will not inspect or automate the live Mercari form; use the manual listing handoff until an authorized provider interface is available.",
            "policy_url": "https://www.mercari.com/us/help_center/topics/listing/policies/prohibited-conduct/",
        })
    if market == "facebook":
        from app.services.onboarding_service import facebook_location_is_confirmed

        if not facebook_location_is_confirmed(current_user):
            raise HTTPException(status_code=409, detail={
                "code": "FACEBOOK_LOCATION_CONFIRMATION_REQUIRED",
                "message": "Confirm Facebook Marketplace's listing location in guided setup before running the safe form diagnostic.",
                "onboarding_step": "facebook_location",
            })
    now = datetime.now(UTC).replace(tzinfo=None)
    device_stmt = select(MarketplaceExtensionDevice).where(
        MarketplaceExtensionDevice.user_id == current_user.id,
        MarketplaceExtensionDevice.revoked_at.is_(None),
    )
    if payload and payload.device_id:
        device_stmt = device_stmt.where(MarketplaceExtensionDevice.id == payload.device_id)
    device = db.execute(device_stmt.order_by(MarketplaceExtensionDevice.last_seen_at.desc().nullslast())).scalars().first()
    if not device:
        raise HTTPException(status_code=409, detail={"code": "EXTENSION_REQUIRED", "message": "Pair the PosterPro extension before starting a browser form test."})
    if not device.last_seen_at or device.last_seen_at < now - timedelta(minutes=3):
        raise HTTPException(status_code=409, detail={"code": "EXTENSION_OFFLINE", "message": "Open PosterPro in the browser where the extension is installed. The browser must check in before a real form test can start."})
    if str(device.extension_version or "") not in LIVE_CREATE_EXTENSION_VERSIONS:
        raise HTTPException(status_code=426, detail={"code": "EXTENSION_UPDATE_REQUIRED", "minimum_version": CURRENT_EXTENSION_VERSION, "message": "Update the paired extension before running a live-form diagnostic."})

    from app.services.marketplace_extension_jobs import _create_url
    from app.services.marketplace_setup import load_manual_marketplace_settings

    shipping_mode = {"facebook": "local pickup", "mercari": "shipping", "vinted": "small parcel", "offerup": "local pickup"}.get(market, "shipping")
    synthetic = {
        "marketplace": market,
        "diagnostic": True,
        "title": "PosterPro Safe Form Test Household Item - Do Not Publish",
        "description": "This is a temporary PosterPro household-item form test. It is not a real product and must not be submitted.",
        "price": 1,
        "category": "Household",
        "department": "Home",
        "subcategory": "Household",
        "condition": "Used - good",
        "availability": "in stock",
        "quantity": 1,
        "brand": "PosterPro Test",
        "size": "M",
        # Facebook location is operator-specific. Never synthesize a ZIP or
        # silently publish using a guessed location.
        "location": load_manual_marketplace_settings(current_user).get(market, {}).get("default_location", ""),
        "delivery_method": shipping_mode,
        "shipping": {"mode": shipping_mode, "local_pickup_enabled": market in {"facebook", "offerup"}, "shipping_payer": "buyer", "shipping_method": "standard", "parcel_weight": "1 lb", "parcel_size": "small"},
        "image_urls": [],
        "start_url": _create_url(market),
    }
    # A diagnostic is a point-in-time probe. Repeated clicks must not build a
    # FIFO of synthetic tests behind one another; keep the newest queued probe
    # and leave completed/history records intact.
    db.execute(
        update(MarketplaceExtensionJob)
        .where(
            MarketplaceExtensionJob.user_id == current_user.id,
            MarketplaceExtensionJob.marketplace == market,
            MarketplaceExtensionJob.action == "DIAGNOSTIC",
            MarketplaceExtensionJob.status == "QUEUED",
        )
        .values(
            status="CANCELLED",
            error_code="SUPERSEDED_BY_NEW_DIAGNOSTIC",
            error_detail="A newer diagnostic request replaced this queued probe.",
            completed_at=now,
            last_state_at=now,
        )
    )
    job = MarketplaceExtensionJob(
        user_id=current_user.id,
        listing_id=None,
        marketplace=market,
        action="DIAGNOSTIC",
        status="QUEUED",
        priority=0,
        payload_version=1,
        payload_snapshot={"version": 1, "diagnostic": True, "marketplace_payload": synthetic},
        last_state_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_payload(job)


@router.get("/browser-extension/diagnostics/latest/{marketplace}")
def get_latest_marketplace_diagnostic(marketplace: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    market = marketplace.strip().lower()
    job = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.user_id == current_user.id,
        MarketplaceExtensionJob.marketplace == market,
        MarketplaceExtensionJob.action == "DIAGNOSTIC",
    ).order_by(MarketplaceExtensionJob.created_at.desc(), MarketplaceExtensionJob.id.desc()).limit(1)).scalars().first()
    return _diagnostic_payload(db, job) if job else {"status": "NOT_RUN", "marketplace": market, "current_version": CURRENT_EXTENSION_VERSION, "minimum_version": MINIMUM_EXTENSION_VERSION}


@router.get("/browser-extension/diagnostics/history/{marketplace}")
def list_marketplace_diagnostic_history(marketplace: str, limit: int = 5, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    market = marketplace.strip().lower()
    if market not in {"facebook", "mercari", "poshmark", "vinted", "offerup"}:
        raise HTTPException(status_code=422, detail="A live form diagnostic is not available for this marketplace")
    bounded_limit = max(1, min(int(limit), 10))
    jobs = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.user_id == current_user.id,
        MarketplaceExtensionJob.marketplace == market,
        MarketplaceExtensionJob.action == "DIAGNOSTIC",
    ).order_by(MarketplaceExtensionJob.created_at.desc(), MarketplaceExtensionJob.id.desc()).limit(bounded_limit)).scalars().all()
    return [_diagnostic_payload(db, job) for job in jobs]


@router.get("/browser-extension/diagnostics/{job_id}")
def get_marketplace_diagnostic(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.id == job_id,
        MarketplaceExtensionJob.user_id == current_user.id,
        MarketplaceExtensionJob.action == "DIAGNOSTIC",
    )).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Marketplace diagnostic not found")
    return _diagnostic_payload(db, job)


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


@router.post("/assisted-marketplace-jobs/{job_id}/confirm-result")
def confirm_assisted_job_result(
    job_id: int,
    payload: OperatorCompletionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record a human-confirmed marketplace action from PosterPro Jobs."""
    job = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.id == job_id,
        MarketplaceExtensionJob.user_id == current_user.id,
    ).with_for_update()).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Assisted marketplace job not found")
    if job.status != "AWAITING_OPERATOR_REVIEW":
        raise HTTPException(status_code=409, detail=f"Job is not awaiting operator review ({job.status})")
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required")
    external_id = str(payload.external_listing_id or "").strip() or None
    external_url = str(payload.external_url or "").strip() or None
    if external_url and not _marketplace_url_matches(job.marketplace, external_url):
        raise HTTPException(status_code=422, detail="Result URL does not match the job marketplace")
    old_id = job.external_listing_id or (job.payload_snapshot or {}).get("external_listing_id")
    old_url = job.external_url or (job.payload_snapshot or {}).get("external_url")
    if job.action in {"UPDATE", "END"}:
        if not old_id and not old_url:
            raise HTTPException(status_code=409, detail="Existing marketplace identity is required for update/end")
        if external_id and external_id != old_id:
            raise HTTPException(status_code=409, detail="Update/end cannot replace the confirmed external listing ID")
        if external_url and external_url != old_url:
            raise HTTPException(status_code=409, detail="Update/end cannot replace the confirmed external listing URL")
        external_id, external_url = old_id, old_url
    elif job.action == "CREATE" and not (external_id or external_url):
        raise HTTPException(status_code=422, detail="A confirmed external listing ID or URL is required")

    now = datetime.now(UTC).replace(tzinfo=None)
    job.status = "COMPLETED"
    job.completed_at = now
    job.last_state_at = now
    job.lease_expires_at = None
    job.external_listing_id = external_id
    job.external_url = external_url
    job.result = {
        **(job.result or {}),
        "operator_confirmed": True,
        "completion_source": "posterpro_jobs",
        "confirmed_at": _iso(now),
    }
    row = db.execute(select(MarketplaceListing).where(
        MarketplaceListing.listing_id == job.listing_id,
        MarketplaceListing.marketplace == MarketplaceName(job.marketplace),
    ).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())).scalars().first()
    if row is None and job.action == "CREATE":
        row = MarketplaceListing(listing_id=job.listing_id, marketplace=MarketplaceName(job.marketplace))
    if row is not None:
        row.marketplace_listing_id = external_id or row.marketplace_listing_id
        row.status = {
            "CREATE": MarketplaceListingStatus.PUBLISHED,
            "UPDATE": MarketplaceListingStatus.UPDATED,
            "END": MarketplaceListingStatus.DELETED,
        }[job.action]
        row.raw_response = {
            **(row.raw_response or {}),
            "external_url": external_url,
            "extension_job_id": job.id,
            "result": job.result,
        }
        db.add(row)
    if job.crosspost_job_id:
        parent = db.get(MarketplaceCrosspostJob, job.crosspost_job_id)
        if parent and parent.user_id == current_user.id:
            children = db.execute(select(MarketplaceExtensionJob).where(
                MarketplaceExtensionJob.crosspost_job_id == parent.id,
            )).scalars().all()
            parent.status = _crosspost_parent_status(parent, children)
            parent.result_summary = {
                **(parent.result_summary or {}),
                "extension_jobs": [
                    {"id": child.id, "marketplace": child.marketplace, "action": child.action, "status": child.status, "error_code": child.error_code}
                    for child in children
                ],
            }
            db.add(parent)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_payload(job)


@router.get("/browser-extension/jobs/{job_id}/status")
def get_extension_job_status(
    job_id: int,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _require_device(authorization, db)
    job = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.id == job_id,
        MarketplaceExtensionJob.user_id == device.user_id,
        MarketplaceExtensionJob.device_id == device.id,
    )).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Claimed assisted job not found")
    now = datetime.now(UTC).replace(tzinfo=None)
    if (
        str(job.status).upper() == "AWAITING_OPERATOR_REVIEW"
        and job.created_at
        and job.created_at < now - EXTENSION_JOB_CLAIM_WINDOW
    ):
        # An abandoned browser form must not permanently block diagnostics or
        # newer operator work on the same extension device.
        job.status = "CANCELLED"
        job.error_code = "STALE_REVIEW_SUPPRESSED"
        job.error_detail = "This browser-assisted form was left open too long and was closed automatically. Start a new action if needed."
        job.completed_at = now
        job.last_state_at = now
        job.lease_expires_at = None
        db.commit()
    return {"job_id": job.id, "status": job.status, "completed_at": _iso(job.completed_at)}


@router.post("/browser-extension/facebook-reconciliation/claim")
def claim_facebook_reconciliation(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Claim one read-only reconciliation task before ordinary CREATE work."""
    device = _require_device(authorization, db)
    if _version_tuple(device.extension_version) < _version_tuple(MINIMUM_EXTENSION_VERSION):
        raise HTTPException(status_code=426, detail={"code": "EXTENSION_UPDATE_REQUIRED", "minimum_version": MINIMUM_EXTENSION_VERSION})
    now = datetime.now(UTC).replace(tzinfo=None)
    candidates = db.execute(
        select(MarketplaceExtensionJob)
        .where(
            MarketplaceExtensionJob.user_id == device.user_id,
            MarketplaceExtensionJob.marketplace == "facebook",
            MarketplaceExtensionJob.action == "CREATE",
            MarketplaceExtensionJob.status.in_(["FAILED", "AWAITING_OPERATOR_REVIEW", "SUBMITTED_PENDING_REVIEW"]),
        )
        .order_by(MarketplaceExtensionJob.created_at.asc(), MarketplaceExtensionJob.id.asc())
        .with_for_update(skip_locked=True)
        .limit(100)
    ).scalars().all()
    for job in candidates:
        result = dict(job.result) if isinstance(job.result, dict) else {}
        known_remote = result.get("derived_classification") == "REMOTE_PRESENT_RECONCILIATION_REQUIRED" \
            or (isinstance(result.get("operator_remote_presence"), dict) and result["operator_remote_presence"].get("confirmed") is True)
        pending_review = str(job.status or "").upper() == "SUBMITTED_PENDING_REVIEW"
        if job.error_code != "SUBMISSION_OUTCOME_UNKNOWN" and not known_remote and not pending_review:
            continue
        reconciliation = result.get("remote_reconciliation") if isinstance(result.get("remote_reconciliation"), dict) else {}
        state = str(reconciliation.get("status") or "").upper()
        if state in {"FOUND", "NOT_FOUND", "REMOTE_FOUND", "REMOTE_NOT_FOUND"}:
            continue
        if state == "AMBIGUOUS" and not pending_review:
            continue
        if pending_review:
            due = reconciliation.get("next_reconciliation_at")
            if due:
                try:
                    due_at = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
                    if due_at.tzinfo is not None:
                        due_at = due_at.astimezone(UTC).replace(tzinfo=None)
                    if due_at > now:
                        continue
                except ValueError:
                    pass
        lease_until = reconciliation.get("lease_expires_at")
        if state == "IN_PROGRESS" and lease_until:
            try:
                parsed_lease = datetime.fromisoformat(str(lease_until).replace("Z", "+00:00"))
                if parsed_lease.tzinfo is not None:
                    parsed_lease = parsed_lease.astimezone(UTC).replace(tzinfo=None)
                if parsed_lease > now:
                    continue
            except ValueError:
                pass
        attempts = int(reconciliation.get("claim_count") or 0)
        if attempts >= 30:
            continue
        # Identity emitted by the old first-link matcher is only a candidate.
        # Preserve it as evidence, but clear it from trusted columns until this
        # authenticated read-only pass independently matches title and price.
        if not known_remote and result.get("submission_performed") is True and (job.external_listing_id or job.external_url):
            result.setdefault("untrusted_submit_identity", {
                "external_listing_id": job.external_listing_id,
                "external_url": job.external_url,
                "reason": "LEGACY_FIRST_LINK_IDENTITY_REQUIRES_TITLE_PRICE_RECONCILIATION",
            })
            job.external_listing_id = None
            job.external_url = None
        if result.get("submission_performed") is True and not pending_review:
            job.status = "SUBMITTED_PENDING_REVIEW"
            job.error_code = None
            job.error_detail = None
            job.completed_at = now
            pending_review = True
        reconciliation = {
            **reconciliation,
            "status": "IN_PROGRESS",
            "claim_count": attempts + 1,
            "known_remote_presence": bool(known_remote),
            "claimed_by_device_id": device.id,
            "claimed_at": _iso(now),
            "lease_expires_at": _iso(now + timedelta(minutes=5)),
        }
        result["remote_reconciliation"] = reconciliation
        job.result = result
        job.device_id = device.id
        job.lease_expires_at = now + timedelta(minutes=5)
        job.last_state_at = now
        device.last_seen_at = now
        db.add(job)
        db.commit()
        db.refresh(job)
        task = _job_payload(job)
        task["reconciliation_only"] = True
        task["known_remote_presence"] = bool(known_remote)
        return {"task": task}
    device.last_seen_at = now
    db.commit()
    return {"task": None}


@router.post("/browser-extension/facebook-reconciliation/{job_id}/result")
def record_facebook_reconciliation_result(
    job_id: int,
    payload: FacebookReconciliationResultRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Persist authenticated-account evidence without replaying the prior CREATE."""
    device = _require_device(authorization, db)
    job = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.id == job_id,
        MarketplaceExtensionJob.user_id == device.user_id,
        MarketplaceExtensionJob.device_id == device.id,
        MarketplaceExtensionJob.marketplace == "facebook",
        MarketplaceExtensionJob.action == "CREATE",
    ).with_for_update()).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Facebook reconciliation task not found for this paired device")
    current_result = dict(job.result) if isinstance(job.result, dict) else {}
    recon = current_result.get("remote_reconciliation") if isinstance(current_result.get("remote_reconciliation"), dict) else {}
    if recon.get("status") != "IN_PROGRESS" or recon.get("claimed_by_device_id") != device.id:
        raise HTTPException(status_code=409, detail="Facebook reconciliation claim is not active for this device")
    if job.lease_expires_at is None or job.lease_expires_at <= datetime.now(UTC).replace(tzinfo=None):
        raise HTTPException(status_code=409, detail="Facebook reconciliation lease expired; claim it again before recording results")
    evidence = dict(payload.evidence or {})
    source = str(evidence.get("source") or "")
    if source not in {"authenticated_navigation_result", "authenticated_facebook_seller_listings"}:
        raise HTTPException(status_code=422, detail="Reconciliation evidence must come from the authenticated Facebook listing page")
    if not _marketplace_url_matches("facebook", str(evidence.get("page_url") or "")):
        raise HTTPException(status_code=422, detail="Reconciliation evidence page is not on Facebook")
    snapshot = (job.payload_snapshot or {}).get("marketplace_payload") if isinstance(job.payload_snapshot, dict) else {}
    expected_title = str((snapshot or {}).get("title") or "").strip()
    price_value = re.sub(r"[^0-9.]", "", str((snapshot or {}).get("price") or "0"))
    try:
        expected_price = float(price_value or 0)
    except ValueError:
        expected_price = 0.0
    now = datetime.now(UTC).replace(tzinfo=None)
    if payload.state == "FOUND":
        remote_id = str(payload.external_listing_id or "").strip()
        remote_url = str(payload.external_url or "").strip()
        if (not remote_id or not remote_url or evidence.get("authenticated_account_context") is not True
            or evidence.get("exact_title_match") is not True or evidence.get("exact_price_match") is not True):
            raise HTTPException(status_code=422, detail="A found listing requires an ID/URL and exact title/price evidence")
        if not expected_title or str(evidence.get("observed_title") or "").strip().casefold() != expected_title.casefold():
            raise HTTPException(status_code=422, detail="Observed listing title does not exactly match the authorized source listing")
        try:
            expected_price_text = f"{expected_price:.2f}"
        except (TypeError, ValueError):
            expected_price_text = ""
        if str(evidence.get("observed_price") or "").replace("$", "").strip() != expected_price_text:
            raise HTTPException(status_code=422, detail="Observed listing price does not match the authorized source listing")
        if not _marketplace_url_matches("facebook", remote_url):
            raise HTTPException(status_code=422, detail="Remote listing URL is not on Facebook")
        parsed = urlsplit(remote_url)
        match = re.match(r"^/marketplace/item/([^/?#]+)/?$", parsed.path, re.I)
        if not match or match.group(1) != remote_id:
            raise HTTPException(status_code=422, detail="Remote Facebook ID must match the item URL")
        canonical_url = urlunsplit(("https", "www.facebook.com", f"/marketplace/item/{remote_id}/", "", ""))
        expected_description = str((snapshot or {}).get("description") or "")
        expected_description_hash, expected_description_length = _facebook_description_fingerprint(expected_description)
        description_proof = evidence.get("description_verification") if isinstance(evidence.get("description_verification"), dict) else {}
        remote_description_verified = bool(
            expected_description_length > 0
            and description_proof.get("matches") is True
            and description_proof.get("expected_hash") == expected_description_hash
            and description_proof.get("observed_hash") == expected_description_hash
            and description_proof.get("expected_length") == expected_description_length
            and description_proof.get("observed_length") == expected_description_length
        )
        description_status = str(description_proof.get("status") or "").upper()
        description_absence_proven = (
            description_status in {"NOT_VERIFIED", "NOT_PRESENT"}
            and source == "authenticated_navigation_result"
        )
        description_repair_required = (
            expected_description_length > 0
            and not remote_description_verified
            and description_absence_proven
        )
        description_still_pending = expected_description_length > 0 and not remote_description_verified and not description_absence_proven
        reconciliation = {
            **recon,
            "status": "FOUND_DESCRIPTION_REPAIR_REQUIRED" if description_repair_required else "FOUND_CONTENT_PENDING" if description_still_pending else "FOUND",
            "confirmation": "REMOTE_FOUND_DESCRIPTION_REPAIR_REQUIRED" if description_repair_required else "REMOTE_FOUND_CONTENT_PENDING" if description_still_pending else "REMOTE_RECONCILIATION_FOUND",
            "source": source, "external_listing_id": remote_id, "external_url": canonical_url,
            "evidence": evidence, "verified_by_device_id": device.id, "verified_at": _iso(now),
            "remote_exists_verified": True, "remote_description_verified": remote_description_verified,
            "description_expected_hash": expected_description_hash if expected_description_length else None,
            "description_expected_length": expected_description_length,
            "reconciled_at": _iso(now),
        }
        current_result.update({
            "submission_performed": True,
            "remote_reconciliation": reconciliation,
            "external_listing_id": remote_id,
            "external_url": canonical_url,
        })
        job.external_listing_id = remote_id
        job.external_url = canonical_url
        job.status = "AWAITING_OPERATOR_REVIEW" if description_repair_required else "SUBMITTED_PENDING_REVIEW" if description_still_pending else "COMPLETED"
        job.error_code = "FACEBOOK_DESCRIPTION_REPAIR_REQUIRED" if description_repair_required else None
        job.error_detail = (
            "Facebook listing identity is verified, but the authenticated listing page did not prove the canonical description is present. Preserve this remote listing and repair its description; do not CREATE again."
            if description_repair_required else None
        )
        job.completed_at = None if description_repair_required else now
    elif payload.state == "NOT_FOUND":
        if recon.get("known_remote_presence") is True or (result_known_remote := current_result.get("derived_classification")) == "REMOTE_PRESENT_RECONCILIATION_REQUIRED":
            raise HTTPException(status_code=409, detail="Operator-confirmed remote presence cannot be overturned by a non-exhaustive absence result")
        search_complete = (
            evidence.get("authenticated_account_context") is True
            and evidence.get("exact_search_used") is True
            and evidence.get("exact_title_search") is True
            and evidence.get("account_search_complete") is True
            and evidence.get("empty_state_verified") is True
            and evidence.get("result_count") == 0
            and str(evidence.get("searched_title") or "").strip().casefold() == expected_title.casefold()
        )
        if not search_complete:
            raise HTTPException(status_code=422, detail="REMOTE_NOT_FOUND requires a completed exact-title search with an authenticated empty state")
        reconciliation = {
            **recon, "status": "NOT_FOUND", "confirmation": "REMOTE_RECONCILIATION_NOT_FOUND",
            "source": source, "evidence": evidence, "verified_by_device_id": device.id,
            "verified_at": _iso(now), "safe_same_child_retry": False,
        }
        current_result.update({"submission_performed": True, "remote_reconciliation": reconciliation})
        # Even a complete empty seller page may lag an accepted submission in
        # Facebook review. Preserve the evidence, schedule another read-only
        # check, and never make a post-submit CREATE retry available.
        job.status = "SUBMITTED_PENDING_REVIEW"
        job.error_code = None
        job.error_detail = None
        reconciliation["last_observation_status"] = "NOT_FOUND"
        reconciliation["status"] = "PENDING_REVIEW"
        reconciliation["reconciliation_attempt"] = int(recon.get("claim_count") or 1)
        reconciliation["next_reconciliation_at"] = _iso(_facebook_reconciliation_due_at(now, reconciliation["reconciliation_attempt"]))
        current_result["remote_reconciliation"] = reconciliation
        job.completed_at = now
        job.completed_at = now
    else:
        pending_review = str(job.status or "").upper() == "SUBMITTED_PENDING_REVIEW"
        attempt_number = int(recon.get("reconciliation_attempt") or recon.get("claim_count") or 1)
        reconciliation = {
            **recon, "status": "PENDING_REVIEW" if pending_review else "AMBIGUOUS",
            "confirmation": "FACEBOOK_REVIEW_PENDING" if pending_review else "REMOTE_RECONCILIATION_AMBIGUOUS",
            "last_observation_status": str(payload.state or "AMBIGUOUS").upper() if pending_review else None,
            "source": source, "evidence": evidence, "verified_by_device_id": device.id,
            "verified_at": _iso(now), "safe_same_child_retry": False,
            "reconciliation_attempt": attempt_number,
        }
        if pending_review:
            reconciliation["next_reconciliation_at"] = _iso(_facebook_reconciliation_due_at(now, attempt_number))
        current_result.update({"submission_performed": True, "remote_reconciliation": reconciliation})
        job.status = "SUBMITTED_PENDING_REVIEW" if pending_review else "FAILED"
        job.error_code = None if pending_review else "SUBMISSION_OUTCOME_UNKNOWN"
        job.error_detail = None if pending_review else "Authenticated Facebook state could not uniquely prove presence or complete absence; no retry is permitted."
        job.completed_at = now
    job.result = current_result
    job.lease_expires_at = None
    job.last_state_at = now
    device.last_seen_at = now
    db.add(job)
    if payload.state == "FOUND":
        row = db.execute(select(MarketplaceListing).where(
            MarketplaceListing.listing_id == job.listing_id,
            MarketplaceListing.marketplace == MarketplaceName.facebook,
        ).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())).scalars().first()
        if row is None:
            row = MarketplaceListing(listing_id=job.listing_id, marketplace=MarketplaceName.facebook)
        row.marketplace_listing_id = job.external_listing_id
        row.status = MarketplaceListingStatus.PUBLISHED
        row.raw_response = {**(row.raw_response or {}), "external_url": job.external_url, "extension_job_id": job.id, "result": current_result}
        db.add(row)
    parent = db.get(MarketplaceCrosspostJob, job.crosspost_job_id) if job.crosspost_job_id else None
    if payload.state == "FOUND" and description_repair_required and parent:
        source_listing = db.get(Listing, job.listing_id)
        if source_listing:
            repair_job = _queue_facebook_description_repair(db, job, source_listing, parent)
            if repair_job:
                reconciliation = dict((job.result or {}).get("remote_reconciliation") or {})
                reconciliation.update({
                    "description_repair_job_id": repair_job.id,
                    "description_repair_state": "FACEBOOK_DESCRIPTION_REPAIR_IN_PROGRESS",
                })
                job.result = {**(job.result or {}), "remote_reconciliation": reconciliation}
                db.add(job)
    if parent and parent.user_id == device.user_id:
        children = db.execute(select(MarketplaceExtensionJob).where(MarketplaceExtensionJob.crosspost_job_id == parent.id)).scalars().all()
        parent.status = _crosspost_parent_status(parent, children)
        parent.last_error = None if payload.state == "FOUND" or job.status == "SUBMITTED_PENDING_REVIEW" else job.error_detail
        parent.result_summary = {
            **(parent.result_summary or {}),
            "extension_jobs": [{"id": child.id, "marketplace": child.marketplace, "action": child.action, "status": child.status, "error_code": child.error_code} for child in children],
        }
        db.add(parent)
        _persist_publish_batch_child_result(db, parent, job, now)
    db.commit()
    db.refresh(job)
    return {"job_id": job.id, "listing_id": job.listing_id, "state": payload.state, "status": job.status, "external_listing_id": job.external_listing_id, "external_url": job.external_url, "error_code": job.error_code, "remote_exists_verified": payload.state == "FOUND", "safe_same_child_retry": False, "submission_performed": current_result.get("submission_performed")}


@router.post("/browser-extension/jobs/claim")
def claim_extension_job(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    device = _require_device(authorization, db)
    if _version_tuple(device.extension_version) < _version_tuple(MINIMUM_EXTENSION_VERSION):
        raise HTTPException(status_code=426, detail={"code": "EXTENSION_UPDATE_REQUIRED", "minimum_version": MINIMUM_EXTENSION_VERSION})
    now = datetime.now(UTC).replace(tzinfo=None)
    # Older installations may still have queued Mercari automation records.
    # Suppress them before claim so the extension cannot execute an action
    # that is not supported by an authorized Mercari interface.
    unauthorized_mercari_jobs = db.execute(
        select(MarketplaceExtensionJob).where(
            MarketplaceExtensionJob.user_id == device.user_id,
            MarketplaceExtensionJob.marketplace == "mercari",
            MarketplaceExtensionJob.action != "DIAGNOSTIC",
            MarketplaceExtensionJob.status.in_(["QUEUED", "RETRYABLE"]),
        ).with_for_update(skip_locked=True)
    ).scalars().all()
    for mercari_job in unauthorized_mercari_jobs:
        mercari_job.status = "BLOCKED_EXTERNAL"
        mercari_job.error_code = "MERCARI_AUTHORIZED_PROVIDER_REQUIRED"
        mercari_job.error_detail = "Mercari's current policy prohibits automated interfaces not provided by Mercari. No submission was performed by PosterPro; use the manual listing handoff."
        mercari_job.completed_at = now
        mercari_job.last_state_at = now
        mercari_job.lease_expires_at = None
        db.add(mercari_job)
    recent_cutoff = now - EXTENSION_JOB_CLAIM_WINDOW
    # Prevent a newly-heartbeating extension from replaying an old assisted
    # publish batch.  These records remain auditable, but a fresh operator
    # action is required to create a new job.
    stale_statuses = ["QUEUED", "RETRYABLE", "CLAIMED", "NAVIGATING", "FORM_FILLING", "AWAITING_OPERATOR_REVIEW", "SUBMITTING", "SUBMITTED"]
    stale_rows = db.execute(
        select(MarketplaceExtensionJob)
        .where(
            MarketplaceExtensionJob.user_id == device.user_id,
            MarketplaceExtensionJob.status.in_(stale_statuses),
            MarketplaceExtensionJob.created_at < recent_cutoff,
        )
        .order_by(MarketplaceExtensionJob.created_at.asc())
        .limit(1000)
    ).scalars().all()
    explicitly_resumed_stale_ids: set[int] = set()
    for stale_job in stale_rows:
        if _has_recent_explicit_resume(db, stale_job, recent_cutoff, now):
            explicitly_resumed_stale_ids.add(stale_job.id)
            continue
        stale_job.status = "CANCELLED"
        stale_job.error_code = "STALE_JOB_SUPPRESSED"
        stale_job.error_detail = "Older browser-assisted job suppressed; start a new assisted action to run it."
        stale_job.completed_at = now
        stale_job.last_state_at = now
        stale_job.lease_expires_at = None
        db.add(stale_job)
    candidate_query = select(MarketplaceExtensionJob)
    candidate_query = candidate_query.where(MarketplaceExtensionJob.marketplace != "mercari")
    if str(device.extension_version or "") not in LIVE_CREATE_EXTENSION_VERSIONS:
        # Unreviewed selector builds may perform diagnostics only. They must
        # not claim any commercial mutation, including a CREATE for an
        # assisted marketplace other than Facebook/Mercari.
        candidate_query = candidate_query.where(MarketplaceExtensionJob.action == "DIAGNOSTIC")
    candidate = db.execute(
        candidate_query
        .where(
            MarketplaceExtensionJob.user_id == device.user_id,
            or_(
                MarketplaceExtensionJob.created_at >= recent_cutoff,
                MarketplaceExtensionJob.id.in_(explicitly_resumed_stale_ids),
            ),
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


@router.post("/browser-extension/jobs/{job_id}/lease")
def renew_extension_job_lease(
    job_id: int,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Keep an actively executing job leased without exposing claim controls."""
    device = _require_device(authorization, db)
    job = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.id == job_id,
        MarketplaceExtensionJob.user_id == device.user_id,
        MarketplaceExtensionJob.device_id == device.id,
    ).with_for_update()).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Claimed assisted job not found")
    if job.status not in {"CLAIMED", "NAVIGATING", "FORM_FILLING", "SUBMITTING", "SUBMITTED"}:
        raise HTTPException(status_code=409, detail=f"Job lease cannot be renewed while {job.status}")
    now = datetime.now(UTC).replace(tzinfo=None)
    if job.lease_expires_at is None or job.lease_expires_at <= now:
        raise HTTPException(status_code=409, detail="Job lease has expired; claim recovery belongs to the queue")
    job.lease_expires_at = now + timedelta(minutes=5)
    device.last_seen_at = now
    db.commit()
    return {"ok": True, "job_id": job.id, "lease_expires_at": _iso(job.lease_expires_at)}


@router.post("/browser-extension/jobs/{job_id}/progress")
def report_extension_job_progress(
    job_id: int,
    payload: JobProgressRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Persist bounded, non-sensitive browser progress without changing job state."""
    device = _require_device(authorization, db)
    job = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.id == job_id,
        MarketplaceExtensionJob.user_id == device.user_id,
        MarketplaceExtensionJob.device_id == device.id,
    ).with_for_update()).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Claimed assisted job not found")
    if job.marketplace != "facebook" or job.action != "CREATE" or job.status != "FORM_FILLING":
        raise HTTPException(status_code=409, detail="Progress checkpoints are accepted only during Facebook CREATE form filling")
    if payload.extension_version != device.extension_version:
        raise HTTPException(status_code=409, detail="Progress extension version does not match the paired device")
    expected_build = {
        "0.3.65": "posterpro-0.3.65-facebook-form-progress-watchdog",
        "0.3.66": "posterpro-0.3.66-facebook-clean-create-context",
        "0.3.67": "posterpro-0.3.67-facebook-description-mutation-trace",
        "0.3.68": "posterpro-0.3.68-facebook-submit-identity-binding",
        "0.3.69": "posterpro-0.3.69-facebook-create-context-manager",
        "0.3.70": "posterpro-0.3.70-facebook-background-queue-focus-safety",
    }.get(payload.extension_version)
    if payload.content_build_id != expected_build:
        raise HTTPException(status_code=409, detail="Progress content build does not match the approved form-progress build")
    allowed_stages = {
        "FORM_FILLING_STARTED", "CLEAN_FORM_CHECK_STARTED", "CLEAN_FORM_VERIFIED",
        "IMAGE_DOWNLOAD_STARTED", "IMAGE_DOWNLOAD_COMPLETE",
        "FACEBOOK_FORM_READY", "IMAGE_UPLOAD_STARTED", "IMAGE_UPLOAD_COMPLETE",
        "TITLE_SET", "PRICE_SET", "CATEGORY_RESOLUTION_STARTED", "CATEGORY_SET",
        "CONDITION_SET", "DESCRIPTION_SET", "LOCATION_SET", "OFFERS_DISABLED",
        "REQUIRED_FIELDS_VERIFIED", "FACEBOOK_FORM_BLOCKED", "ADVANCING_STAGE", "ADVANCING_TO_SUBMIT", "READY_TO_SUBMIT",
    }
    if payload.stage not in allowed_stages:
        raise HTTPException(status_code=422, detail="Unrecognized Facebook form progress stage")
    page_url = None
    if payload.page_url:
        parsed = urlsplit(payload.page_url)
        if parsed.scheme != "https" or parsed.hostname not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
            raise HTTPException(status_code=422, detail="Progress page URL must be a Facebook HTTPS URL")
        page_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path[:240], "", ""))
    safe_detail = {}
    for key, value in list(payload.detail.items())[:8]:
        if key not in {"field", "index", "count", "error_code", "duration_ms", "wait_ms", "required_fields_remaining"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe_detail[key] = str(value)[:120] if isinstance(value, str) else value
    now = datetime.now(UTC).replace(tzinfo=None)
    client_at = payload.timestamp
    if client_at and client_at.tzinfo is not None:
        client_at = client_at.astimezone(UTC).replace(tzinfo=None)
    # Trust server receive time for watchdog calculations; retain client time
    # only as supporting timing evidence.
    result = dict(job.result) if isinstance(job.result, dict) else {}
    previous = result.get("form_progress") if isinstance(result.get("form_progress"), dict) else {}
    history = list(previous.get("history") or [])
    received_at = _iso(now)
    checkpoint = {
        "stage": payload.stage,
        "phase": payload.phase,
        "received_at": received_at,
        "client_timestamp": _iso(client_at) if client_at else None,
        "page_url": page_url,
        "detail": safe_detail,
    }
    prior_received = previous.get("last_progress_at")
    if prior_received:
        try:
            checkpoint["seconds_since_previous"] = max(0, round((now - datetime.fromisoformat(prior_received.replace("Z", "+00:00")).replace(tzinfo=None)).total_seconds(), 3))
        except (ValueError, TypeError):
            pass
    history.append(checkpoint)
    history = history[-80:]
    durations = dict(previous.get("stage_durations") or {})
    if payload.phase == "started":
        durations[payload.stage] = {"started_at": received_at, "completed_at": None, "duration_seconds": None}
    elif payload.phase == "completed" and isinstance(durations.get(payload.stage), dict):
        stage_entry = dict(durations[payload.stage])
        stage_entry["completed_at"] = received_at
        try:
            started = datetime.fromisoformat(str(stage_entry["started_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
            stage_entry["duration_seconds"] = round((now - started).total_seconds(), 3)
        except (ValueError, TypeError, KeyError):
            stage_entry["duration_seconds"] = None
        durations[payload.stage] = stage_entry
    result["form_progress"] = {
        "last_progress_stage": payload.stage,
        "last_progress_phase": payload.phase,
        "last_progress_at": received_at,
        "last_progress_client_timestamp": _iso(client_at) if client_at else None,
        "page_url": page_url,
        "detail": safe_detail,
        "history": history,
        "stage_durations": durations,
    }
    job.result = result
    device.last_seen_at = now
    db.commit()
    return {"ok": True, "job_id": job.id, "status": job.status, "last_progress_stage": payload.stage, "last_progress_at": received_at}


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
    if (
        job.marketplace == "facebook" and job.action == "CREATE"
        and str(job.status).upper() in {"SUBMITTING", "SUBMITTED", "SUBMITTED_PENDING_REVIEW"}
        and next_status == "RETRYABLE"
        and isinstance(payload.result, dict)
        and payload.result.get("safe_pre_submit_retry") is True
    ):
        raise HTTPException(status_code=409, detail="A Facebook CREATE that reached SUBMITTING is never eligible for pre-submit retry")
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
    previous_last_state_at = job.last_state_at
    prior_result = dict(job.result) if isinstance(job.result, dict) else {}
    pending_review_submission = _facebook_submission_pending_review(job, device, prior_result, payload, previous_status)
    effective_status = "SUBMITTED_PENDING_REVIEW" if pending_review_submission else next_status
    safe_facebook_prefill_retry = bool(
        job.marketplace == "facebook" and job.action == "CREATE" and previous_status == "FORM_FILLING"
        and next_status == "RETRYABLE" and isinstance(payload.result, dict)
        and payload.result.get("safe_pre_submit_retry") is True
        and prior_result.get("submission_performed") is not True
        and not job.external_listing_id and not job.external_url
    )
    retry_exhausted = safe_facebook_prefill_retry and int(job.attempt_count or 0) >= 2
    if retry_exhausted:
        effective_status = "FAILED"
    elif safe_facebook_prefill_retry:
        # Let the already-released cohort continue first; a single bounded
        # same-child retry remains eligible after ordinary queued work.
        job.priority = 10
    job.status = effective_status
    job.last_state_at = now
    job.error_code = payload.error_code
    job.error_detail = payload.error_detail
    if retry_exhausted:
        job.error_code = "FACEBOOK_FORM_STALL_RETRY_EXHAUSTED"
        job.error_detail = payload.error_detail or "The bounded same-child pre-submit retry was exhausted. No submission occurred."
    explicit_no_submit_result = (
        isinstance(payload.result, dict)
        and payload.result.get("submission_performed") is False
    )
    automated_facebook_not_found = False
    if (
        job.action == "CREATE"
        and job.marketplace == "facebook"
        and previous_status in {"SUBMITTING", "SUBMITTED"}
        and next_status == "FAILED"
        and isinstance(payload.result, dict)
    ):
        remote_reconciliation = payload.result.get("remote_reconciliation")
        evidence = remote_reconciliation.get("evidence") if isinstance(remote_reconciliation, dict) else None
        snapshot = (job.payload_snapshot or {}).get("marketplace_payload") if isinstance(job.payload_snapshot, dict) else {}
        expected_title = str((snapshot or {}).get("title") or "").strip()
        automated_facebook_not_found = bool(
            payload.error_code == "REMOTE_RECONCILIATION_NOT_FOUND"
            and isinstance(remote_reconciliation, dict)
            and remote_reconciliation.get("status") == "NOT_FOUND"
            and remote_reconciliation.get("confirmation") == "REMOTE_RECONCILIATION_NOT_FOUND"
            and remote_reconciliation.get("source") == "authenticated_facebook_seller_listings"
            and isinstance(evidence, dict)
            and evidence.get("authenticated_account_context") is True
            and evidence.get("exact_search_used") is True
            and evidence.get("exact_title_search") is True
            and evidence.get("account_search_complete") is True
            and evidence.get("empty_state_verified") is True
            and evidence.get("result_count") == 0
            and str(evidence.get("searched_title") or "").strip().casefold() == expected_title.casefold()
            and _marketplace_url_matches("facebook", str(evidence.get("page_url") or ""))
            and urlsplit(str(evidence.get("page_url") or "")).path.rstrip("/").lower() == "/marketplace/you/selling"
        )
        if automated_facebook_not_found:
            history = (job.payload_snapshot or {}).get("execution_history") if isinstance(job.payload_snapshot, dict) else []
            prior_not_found_count = sum(
                1 for entry in (history or [])
                if isinstance(entry, dict)
                and isinstance(entry.get("result"), dict)
                and isinstance(entry["result"].get("remote_reconciliation"), dict)
                and entry["result"]["remote_reconciliation"].get("status") == "NOT_FOUND"
                and entry["result"]["remote_reconciliation"].get("source") == "authenticated_facebook_seller_listings"
                and entry["result"]["remote_reconciliation"].get("safe_same_child_retry") is True
            )
            safe_retry = False
            payload.result["remote_reconciliation"].update({
                "verified_by_device_id": device.id,
                "verified_at": _iso(now),
                "safe_same_child_retry": False,
            })
            job.error_code = None
            job.error_detail = None
    if (
        job.action == "CREATE"
        and previous_status in {"SUBMITTING", "SUBMITTED"}
        and next_status in {"RETRYABLE", "FAILED"}
        and not explicit_no_submit_result
        and not automated_facebook_not_found
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
        incoming_result = _safe_diagnostic_result(payload.result, job.marketplace) if job.action == "DIAGNOSTIC" else dict(payload.result) if isinstance(payload.result, dict) else payload.result
        if isinstance(incoming_result, dict) and isinstance(prior_result.get("form_progress"), dict):
            incoming_result.setdefault("form_progress", prior_result["form_progress"])
        if safe_facebook_prefill_retry and isinstance(incoming_result, dict):
            incoming_result["safe_pre_submit_retry"] = not retry_exhausted
            incoming_result["retry_exhausted"] = retry_exhausted
        if job.marketplace == "facebook" and job.action == "CREATE" and isinstance(incoming_result, dict) and incoming_result.get("submission_performed") is True:
            submitted_at = prior_result.get("submitted_at")
            if not submitted_at:
                submitted_at = _iso(previous_last_state_at if previous_status == "SUBMITTED" and previous_last_state_at else now)
            incoming_result["submitted_at"] = submitted_at
            if "submission_duration_seconds" not in incoming_result and job.started_at:
                try:
                    submitted_dt = datetime.fromisoformat(str(submitted_at).replace("Z", "+00:00"))
                    if submitted_dt.tzinfo is not None:
                        submitted_dt = submitted_dt.astimezone(UTC).replace(tzinfo=None)
                    incoming_result["submission_duration_seconds"] = max(0, round((submitted_dt - job.started_at).total_seconds(), 2))
                except ValueError:
                    pass
        if pending_review_submission and isinstance(incoming_result, dict):
            # The browser reports SUBMITTED before it performs the optional
            # read-only seller lookup. Preserve the identity/submit evidence
            # from that prior transition when the later response omits it.
            incoming_result.setdefault("submit_transition_evidence", {
                key: prior_result.get(key) for key in (
                    "submission_performed", "confirmation", "page_url", "background_version",
                    "extension_version", "content_script_version", "content_script_build_id",
                ) if prior_result.get(key) is not None
            })
            incoming_result["submission_state"] = "SUBMITTED_PENDING_REVIEW"
            old_reconciliation = incoming_result.get("remote_reconciliation")
            if not isinstance(old_reconciliation, dict):
                old_reconciliation = {}
            incoming_result["remote_reconciliation"] = {
                **old_reconciliation,
                "status": "PENDING_REVIEW",
                "last_observation_status": old_reconciliation.get("status"),
                "known_remote_presence": False,
                "safe_same_child_retry": False,
                "next_reconciliation_at": _iso(_facebook_reconciliation_due_at(now, 0)),
                "reconciliation_attempt": int(old_reconciliation.get("reconciliation_attempt") or 0),
            }
        job.result = incoming_result
        if job.action == "DIAGNOSTIC":
            safe_missing = (job.result or {}).get("missing_required_fields") or [
                field.get("field") for field in (job.result or {}).get("field_results", [])
                if field.get("required") is True and field.get("filled") is not True and field.get("field")
            ]
            if safe_missing:
                job.error_detail = ", ".join(safe_missing)[:2000]
            elif payload.error_detail:
                job.error_detail = payload.error_detail[:2000]
            if next_status == "COMPLETED" and (job.result or {}).get("capability_ready") is not True:
                # A diagnostic is only complete when it establishes that the
                # current browser can perform every required field action.
                # Keeping an incomplete probe as COMPLETED hid the exact
                # Mercari form failures behind a misleading green state.
                # This is diagnostic state only: it never represents a
                # marketplace listing or a submitted commercial action.
                job.status = "BLOCKED_EXTERNAL"
                # `result` is deliberately sanitized and may not retain an
                # incoming provider error code.  Keep the explicit state
                # payload code so the operator sees the actual remediation
                # target (for example REQUIRED_FIELDS_UNRESOLVED).
                job.error_code = payload.error_code or (job.result or {}).get("error_code") or "DIAGNOSTIC_CAPABILITY_INCOMPLETE"
            if next_status == "COMPLETED" and (
                (job.result or {}).get("background_version") != CURRENT_EXTENSION_VERSION
                or (job.result or {}).get("content_script_version") != CURRENT_EXTENSION_VERSION
            ):
                job.status = "FAILED"
                job.error_code = "STALE_CONTENT_SCRIPT_RELOAD_REQUIRED"
                job.error_detail = "The diagnostic did not prove that the current content-script build ran. No capability result was accepted."
            if next_status == "BLOCKED_EXTERNAL":
                job.error_code = (job.result or {}).get("error_code") or "EXTERNAL_MARKETPLACE_UNAVAILABLE"
                job.error_detail = _diagnostic_external_block_detail(
                    marketplace=job.marketplace,
                    result=job.result,
                    supplied=payload.error_detail,
                )
    elif job.action == "DIAGNOSTIC" and next_status == "BLOCKED_EXTERNAL":
        job.error_code = payload.error_code or "EXTERNAL_MARKETPLACE_UNAVAILABLE"
        job.error_detail = _diagnostic_external_block_detail(
            marketplace=job.marketplace,
            result=job.result,
            supplied=payload.error_detail,
        )
    if pending_review_submission:
        job.error_code = None
        job.error_detail = None
        job.completed_at = now
        job.lease_expires_at = None
    elif next_status in {"FAILED", "RETRYABLE", "CANCELLED", "COMPLETED", "BLOCKED_EXTERNAL", "LOGIN_REQUIRED", "SUBMITTED_PENDING_REVIEW"}:
        job.completed_at = now
        job.lease_expires_at = None
    else:
        job.lease_expires_at = now + timedelta(minutes=5)
    if next_status == "COMPLETED":
        if job.action in {"CREATE", "UPDATE"} and not (job.external_listing_id or job.external_url):
            raise HTTPException(status_code=422, detail="Completion requires an external listing ID or URL")
        if job.action == "CREATE" and job.marketplace == "facebook":
            if not job.external_listing_id or not job.external_url:
                raise HTTPException(status_code=422, detail="Facebook CREATE completion requires both a remote listing ID and URL")
            path_match = re.match(r"^/marketplace/item/([^/?#]+)/?$", urlsplit(job.external_url).path, re.I)
            if not path_match or path_match.group(1) != str(job.external_listing_id):
                raise HTTPException(status_code=422, detail="Facebook completion URL must identify the same remote listing ID")
            payload_snapshot = (job.payload_snapshot or {}).get("marketplace_payload") if isinstance(job.payload_snapshot, dict) else {}
            expected_description = str((payload_snapshot or {}).get("description") or "")
            expected_hash, expected_length = _facebook_description_fingerprint(expected_description)
            completion_result = payload.result if isinstance(payload.result, dict) else (job.result if isinstance(job.result, dict) else {})
            local_description = completion_result.get("description_verification") if isinstance(completion_result.get("description_verification"), dict) else {}
            remote_description = completion_result.get("remote_description_verification") if isinstance(completion_result.get("remote_description_verification"), dict) else {}
            if expected_length and not (
                local_description.get("matches") is True
                and local_description.get("expected_hash") == expected_hash
                and local_description.get("observed_hash") == expected_hash
                and local_description.get("expected_length") == expected_length
                and local_description.get("observed_length") == expected_length
                and remote_description.get("matches") is True
                and remote_description.get("expected_hash") == expected_hash
                and remote_description.get("observed_hash") == expected_hash
                and remote_description.get("expected_length") == expected_length
                and remote_description.get("observed_length") == expected_length
            ):
                raise HTTPException(status_code=422, detail="Facebook CREATE cannot complete until canonical description is verified both before submit and on the remote listing")
        if job.action == "UPDATE" and job.marketplace == "facebook":
            payload_snapshot = (job.payload_snapshot or {}).get("marketplace_payload") if isinstance(job.payload_snapshot, dict) else {}
            if isinstance(payload_snapshot, dict) and payload_snapshot.get("repair_scope") == "facebook_description_only":
                expected_id = str(payload_snapshot.get("repair_expected_remote_id") or "")
                expected_url = str(payload_snapshot.get("repair_expected_remote_url") or "")
                expected_title = str(payload_snapshot.get("repair_expected_title") or "")
                expected_price = payload_snapshot.get("repair_expected_price")
                expected_hash, expected_length = _facebook_description_fingerprint(str(payload_snapshot.get("description") or ""))
                result = payload.result if isinstance(payload.result, dict) else (job.result if isinstance(job.result, dict) else {})
                proof = result.get("remote_description_verification") if isinstance(result.get("remote_description_verification"), dict) else {}
                observed = result.get("post_save_reconciliation") if isinstance(result.get("post_save_reconciliation"), dict) else {}
                observed_title = " ".join(str(observed.get("observed_title") or "").split()).casefold()
                expected_title_normalized = " ".join(expected_title.split()).casefold()
                observed_price_text = re.sub(r"[^0-9.]", "", str(observed.get("observed_price") or ""))
                try:
                    observed_price_matches = bool(expected_price is not None and observed_price_text and float(observed_price_text) == float(expected_price))
                except (TypeError, ValueError):
                    observed_price_matches = False
                if (
                    not expected_id or not expected_url or not expected_title or not expected_length
                    or job.external_listing_id != expected_id or str(job.external_url or "").rstrip("/") != expected_url.rstrip("/")
                    or str(result.get("external_listing_id") or "") != expected_id
                    or str(result.get("external_url") or "").rstrip("/") != expected_url.rstrip("/")
                    or result.get("description_only") is not True or result.get("save_activation_count") != 1
                    or result.get("title_unchanged") is not True or result.get("price_unchanged") is not True
                    or observed.get("state") != "FOUND" or observed.get("exact_title_match") is not True
                    or observed.get("exact_price_match") is not True or observed_title != expected_title_normalized
                    or not observed_price_matches
                    or proof.get("matches") is not True or proof.get("expected_hash") != expected_hash
                    or proof.get("observed_hash") != expected_hash or proof.get("expected_length") != expected_length
                    or proof.get("observed_length") != expected_length
                ):
                    raise HTTPException(status_code=422, detail="Facebook description repair cannot complete until the same remote identity, unchanged title/price, and canonical description are verified after one Save")
                published_row = db.execute(select(MarketplaceListing).where(
                    MarketplaceListing.listing_id == job.listing_id,
                    MarketplaceListing.marketplace == MarketplaceName.facebook,
                ).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())).scalars().first()
                if (
                    not published_row or published_row.status != MarketplaceListingStatus.PUBLISHED
                    or published_row.marketplace_listing_id != expected_id
                    or str((published_row.raw_response or {}).get("external_url") or "").rstrip("/") != expected_url.rstrip("/")
                ):
                    raise HTTPException(status_code=409, detail="Facebook description repair target is no longer the same persisted PUBLISHED listing")
        if job.action in {"CREATE", "UPDATE", "END"}:
            row = db.execute(select(MarketplaceListing).where(
                MarketplaceListing.listing_id == job.listing_id,
                MarketplaceListing.marketplace == MarketplaceName(job.marketplace),
            ).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())).scalars().first()
            if row is None and job.action == "CREATE":
                row = MarketplaceListing(listing_id=job.listing_id, marketplace=MarketplaceName(job.marketplace))
            if row is not None:
                row.marketplace_listing_id = job.external_listing_id or row.marketplace_listing_id
                row_payload = (job.payload_snapshot or {}).get("marketplace_payload", {}) if isinstance(job.payload_snapshot, dict) else {}
                is_description_repair = job.action == "UPDATE" and job.marketplace == "facebook" and row_payload.get("repair_scope") == "facebook_description_only"
                row.status = MarketplaceListingStatus.PUBLISHED if is_description_repair else {
                    "CREATE": MarketplaceListingStatus.PUBLISHED,
                    "UPDATE": MarketplaceListingStatus.UPDATED,
                    "END": MarketplaceListingStatus.DELETED,
                }[job.action]
                row.raw_response = {**(row.raw_response or {}), "external_url": job.external_url, "extension_job_id": job.id, "result": payload.result,
                    **({"description_repair_state": "FACEBOOK_DESCRIPTION_REPAIR_SUCCESS"} if is_description_repair else {})}
                db.add(row)
                if is_description_repair:
                    repair_meta = (job.payload_snapshot or {}).get("description_repair", {})
                    source_create_id = repair_meta.get("source_create_job_id")
                    source_create = db.get(MarketplaceExtensionJob, int(source_create_id)) if source_create_id else None
                    if source_create and source_create.user_id == job.user_id and source_create.action == "CREATE" and source_create.marketplace == "facebook":
                        source_result = dict(source_create.result or {})
                        remote_reconciliation = dict(source_result.get("remote_reconciliation") or {})
                        remote_reconciliation.update({
                            "status": "FOUND", "remote_description_verified": True,
                            "description_repair_state": "FACEBOOK_DESCRIPTION_REPAIR_SUCCESS",
                            "description_repair_job_id": job.id,
                            "description_verification": (payload.result or {}).get("remote_description_verification"),
                        })
                        source_result["remote_reconciliation"] = remote_reconciliation
                        source_create.result = source_result
                        source_create.status = "COMPLETED"
                        source_create.error_code = None
                        source_create.error_detail = None
                        source_create.completed_at = now
                        source_create.external_listing_id = expected_id
                        source_create.external_url = expected_url
                        db.add(source_create)
    elif job.action in {"CREATE", "UPDATE", "END"}:
        row = db.execute(select(MarketplaceListing).where(
            MarketplaceListing.listing_id == job.listing_id,
            MarketplaceListing.marketplace == MarketplaceName(job.marketplace),
        ).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())).scalars().first()
        if row is not None:
            # Action state is not listing lifecycle. A failed revise/end must
            # not make an already-published listing appear unpublished/failed.
            if job.action == "CREATE":
                remote_present_for_repair = (
                    job.marketplace == "facebook"
                    and next_status == "AWAITING_OPERATOR_REVIEW"
                    and bool(job.external_listing_id and job.external_url)
                    and isinstance(payload.result, dict)
                    and payload.result.get("remote_exists_verified") is True
                )
                row.status = MarketplaceListingStatus.PUBLISHED if remote_present_for_repair else (
                    MarketplaceListingStatus.PENDING
                    if job.status in {"SUBMITTING", "SUBMITTED", "SUBMITTED_PENDING_REVIEW"}
                    else MarketplaceListingStatus.FAILED if next_status == "FAILED" else MarketplaceListingStatus.PENDING
                )
            row.raw_response = {
                **(row.raw_response or {}),
                "extension_job_id": job.id,
                "extension_state": job.status,
                "submission_state": "SUBMITTED_PENDING_REVIEW" if job.status == "SUBMITTED_PENDING_REVIEW" else None,
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
            parent.status = _crosspost_parent_status(parent, children)
            if any(child.error_code == "SUBMISSION_OUTCOME_UNKNOWN" for child in children):
                parent.last_error = "A marketplace CREATE may have been submitted, but the extension did not confirm the outcome. Verify external identity before retrying."
            elif parent.status == "failed":
                if job.action == "END":
                    listing = db.get(Listing, parent.listing_id)
                    create_process_notification(
                        db,
                        user_id=parent.user_id,
                        title="URGENT: SOLD ITEM MAY STILL BE LISTED",
                        message=f"PosterPro could not finish removing the {job.marketplace} copy of {(listing.title if listing else 'this item')}. Open the marketplace listing and retry or confirm removal.",
                        notification_type="marketplace_delist_failed",
                        href=f"/listings/{parent.listing_id}",
                        metadata_json={"listing_id": parent.listing_id, "marketplace": job.marketplace, "job_id": parent.id, "extension_job_id": job.id, "external_listing_id": job.external_listing_id, "error_code": job.error_code},
                    )
            parent.result_summary = {
                **(parent.result_summary or {}),
                "extension_jobs": [
                    {"id": child.id, "marketplace": child.marketplace, "action": child.action, "status": child.status, "error_code": child.error_code}
                    for child in children
                ],
            }
            if job.marketplace == "facebook" and job.action in {"CREATE", "UPDATE"}:
                _persist_publish_batch_child_result(db, parent, job, now)
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
