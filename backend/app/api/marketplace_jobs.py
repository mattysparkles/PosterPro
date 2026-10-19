from __future__ import annotations

from datetime import datetime
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, case, and_, or_
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AutomationBridgeSmokeTestResponse,
    BridgeMarketplaceAccountsEnvelope,
    BridgeMarketplaceAccountConnectRequest,
    BridgeMarketplaceAccountResponse,
    BridgeMarketplaceAccountSessionRequest,
    BridgeMarketplaceAccountUpsertRequest,
    BridgeMarketplaceConnectSessionResponse,
    CrosspostJobResponse,
    MarketplaceJobsOverviewResponse,
    CrosspostPreviewEntry,
    CrosspostQueueRequest,
    MarketplaceImportJobCreateRequest,
    MarketplaceBulkImportRequest,
    MarketplaceImportJobResponse,
)
from app.core.auth import ensure_user_owns_resource, get_current_user
from app.core.database import get_db
from app.models.enums import EbayPublishStatus, ListingStatus, MARKETPLACE_DESTINATION_VALUES, MarketplaceListingStatus, MarketplaceName
from app.models.models import IntakeNotification, IntakePhotoBatch, IntakeProviderMedia, Listing, ListingCorrectionJob, MarketplaceCrosspostJob, MarketplaceExtensionJob, MarketplaceImportJob, MarketplaceListing, MarketplacePublishAttempt, MarketplacePublishBatch, User
from app.services.active_listing_snapshot import cached_ebay_active_ids, last_ebay_active_ids, refresh_ebay_active_snapshot
from app.services.marketplace_execution import resolve_execution_mode
from app.services.marketplace_field_mapper import build_marketplace_payload
from app.services.marketplace_extension_jobs import build_extension_marketplace_payload
from app.services.marketplace_routing import MarketplaceRoutingRule, MarketplaceRoutingService
from app.services.customer_description import customer_description_is_safe
from app.services.listing_specificity import classify_listing_reviewability
from app.services.canonical_readiness import canonical_listing_readiness
from app.services.marketplace_preflight import MarketplacePreflightService, compute_payload_hash
from app.services.automation_bridge import (
    bridge_browser_submit_policy,
    connect_bridge_account,
    get_bridge_asset,
    get_bridge_connect_desktop_frame,
    get_bridge_connect_session,
    list_bridge_accounts,
    send_bridge_connect_desktop_action,
    smoke_test_automation_bridge,
    start_bridge_account_connect,
    update_bridge_account_session,
    upsert_bridge_account,
    AutomationBridgeError,
)
from app.services.bridge_desktop import issue_bridge_desktop_token
from app.workers.tasks import STALE_IMPORT_JOB_AFTER, _import_job_is_stale, process_marketplace_crosspost_job_task, process_marketplace_import_job_task
from app.workers.celery_app import celery_app

router = APIRouter()
FACEBOOK_EBAY_ROLLOUT_BATCH_ID = "facebook-ebay-rollout-7176bf87661d404987b88a19dc92d6a1"


def _json_safe(value):
    """Serialize durable execution metadata without leaking datetime objects into JSON columns."""
    return json.loads(json.dumps(value, default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item)))


class ReconcileCreateNotFoundRequest(BaseModel):
    confirmation: Literal["REMOTE_RECONCILIATION_NOT_FOUND"]
    evidence_summary: str = Field(min_length=8, max_length=500)

@router.get("/correction-jobs")
def list_correction_jobs(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.execute(select(ListingCorrectionJob).where(ListingCorrectionJob.user_id == current_user.id).order_by(ListingCorrectionJob.priority.asc(), case((ListingCorrectionJob.priority == 0, ListingCorrectionJob.created_at), else_=None).desc(), case((ListingCorrectionJob.priority != 0, ListingCorrectionJob.created_at), else_=None).asc(), case((ListingCorrectionJob.priority == 0, ListingCorrectionJob.id), else_=None).desc(), case((ListingCorrectionJob.priority != 0, ListingCorrectionJob.id), else_=None).asc())).scalars().all()
    return [{"id": r.id, "listing_id": r.listing_id, "priority": r.priority, "fields": r.fields or [], "operator_note": r.operator_note, "status": r.status, "attempt_count": r.attempt_count, "before": r.before_snapshot, "after": r.after_snapshot, "material_delta": r.material_delta, "result": r.result, "failure_reason": r.failure_reason, "requested_by": r.requested_by, "created_at": r.created_at.isoformat() if r.created_at else None, "claimed_at": r.claimed_at.isoformat() if r.claimed_at else None, "started_at": r.started_at.isoformat() if r.started_at else None, "completed_at": r.completed_at.isoformat() if r.completed_at else None, "superseded_by": r.superseded_by} for r in rows[:limit]]

@router.patch("/correction-jobs/{job_id}/priority")
def reprioritize_correction_job(job_id: int, payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = db.get(ListingCorrectionJob, job_id)
    if not job or job.user_id != current_user.id: raise HTTPException(status_code=404, detail="Correction job not found")
    if job.status != "queued": raise HTTPException(status_code=409, detail="Only queued corrections can be reprioritized")
    old = job.priority; job.priority = max(0, int(payload.get("priority", old))); meta = dict(job.result or {}); meta.setdefault("priority_history", []).append({"old": old, "new": job.priority, "changed_by": current_user.id, "changed_at": datetime.utcnow().isoformat()}); job.result = meta; db.commit()
    return {"id": job.id, "priority": job.priority}


def _enqueue_priority(task, job_id: int, priority: int | None = None):
    """Map PosterPro's numeric priority directly to Celery's broker priority.

    Celery/AMQP/Redis priority 0 is highest.  The old ``10 - priority``
    inversion put operator publish jobs at priority 9, behind the recurring
    background/retry traffic, so they could remain queued indefinitely.
    """
    normalized = max(0, min(10, int(priority if priority is not None else 1)))
    return task.apply_async(args=[job_id], priority=normalized)

class BulkRequeueRequest(BaseModel):
    statuses: list[str] = Field(default_factory=lambda: ["failed"], max_length=4)
    job_types: list[str] = Field(default_factory=lambda: ["crosspost", "import"], max_length=2)


class JobControlRequest(BaseModel):
    action: str
    job_ids: list[int] = Field(default_factory=list, max_length=500)
    job_types: list[str] = Field(default_factory=lambda: ["crosspost"], max_length=2)
    confirm_restart: bool = False


@router.post("/marketplace-jobs/control")
def control_marketplace_jobs(payload: JobControlRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Durable operator controls. Dismiss is soft-only; job records are never deleted."""
    action = str(payload.action or "").strip().lower()
    types = {str(value).strip().lower() for value in payload.job_types if str(value).strip()}
    if not types or not types.issubset({"crosspost", "import"}):
        raise HTTPException(status_code=400, detail="Select cross-post and/or import jobs.")
    settings_json = dict(current_user.settings_json or {})
    paused = bool(settings_json.get("marketplace_jobs_paused"))
    if action in {"pause_queue", "resume_queue"}:
        next_paused = action == "pause_queue"
        settings_json["marketplace_jobs_paused"] = next_paused
        current_user.settings_json = settings_json
        db.add(current_user)
        changed = []
        pending_dispatch = []
        models = []
        if "crosspost" in types: models.append(("crosspost", MarketplaceCrosspostJob, process_marketplace_crosspost_job_task))
        if "import" in types: models.append(("import", MarketplaceImportJob, process_marketplace_import_job_task))
        for job_type, model, task in models:
            rows = db.execute(select(model).where(model.user_id == current_user.id, model.status.in_(["queued", "running", "paused"] if next_paused else ["paused"]))).scalars().all()
            for job in rows:
                if next_paused and str(job.status).lower() in {"queued", "running"}:
                    if job.task_id: celery_app.control.revoke(job.task_id, terminate=False)
                    job.status = "paused"
                    job.last_error = "Paused by operator. Any provider request already in progress may still finish; no remaining destinations will be dispatched after worker acknowledgement."
                    db.add(job)
                elif not next_paused:
                    job.status = "queued"
                    job.last_error = "Resumed by operator."
                    pending_dispatch.append((job_type, job.id, task, getattr(job, "priority", 1)))
                    db.add(job)
                changed.append({"type": job_type, "id": job.id, "status": job.status})
        db.commit()
        for job_type, job_id, task, priority in pending_dispatch:
            dispatched = _enqueue_priority(task, job_id, priority)
            model = MarketplaceCrosspostJob if job_type == "crosspost" else MarketplaceImportJob
            job = db.execute(select(model).where(model.id == job_id, model.user_id == current_user.id)).scalar_one()
            job.task_id = dispatched.id
            db.add(job)
            for item in changed:
                if item["type"] == job_type and item["id"] == job_id:
                    item["task_id"] = dispatched.id
        db.commit()
        return {"action": action, "paused": next_paused, "jobs": changed, "in_flight_note": "An external provider request already in progress cannot be interrupted; running jobs stop at their next safe boundary."}

    if action not in {"cancel_selected", "pause_selected", "resume_selected", "restart_selected", "dismiss_selected"}:
        raise HTTPException(status_code=400, detail="Unsupported job control action.")
    ids = list(dict.fromkeys(int(value) for value in payload.job_ids if int(value) > 0))
    if not ids:
        raise HTTPException(status_code=400, detail="Select at least one job.")
    if action == "restart_selected" and not payload.confirm_restart:
        raise HTTPException(status_code=400, detail="Explicit restart confirmation is required.")
    changed = []
    pending_dispatch = []
    models = []
    if "crosspost" in types: models.append(("crosspost", MarketplaceCrosspostJob, process_marketplace_crosspost_job_task))
    if "import" in types: models.append(("import", MarketplaceImportJob, process_marketplace_import_job_task))
    found_ids = set()
    for job_type, model, task in models:
        rows = db.execute(select(model).where(model.user_id == current_user.id, model.id.in_(ids)).with_for_update()).scalars().all()
        for job in rows:
            found_ids.add(job.id)
            status_value = str(job.status or "").lower()
            if action == "cancel_selected":
                if status_value not in {"queued", "running"}:
                    changed.append({"type": job_type, "id": job.id, "status": status_value, "skipped": "not active"}); continue
                if job.task_id: celery_app.control.revoke(job.task_id, terminate=False)
                job.status = "canceled"
                job.last_error = "Cancellation requested by operator; any current provider request may still finish."
            elif action == "pause_selected":
                if status_value not in {"queued", "running"}:
                    changed.append({"type": job_type, "id": job.id, "status": status_value, "skipped": "not active"}); continue
                if job.task_id: celery_app.control.revoke(job.task_id, terminate=False)
                job.status = "paused"
                job.last_error = "Paused by operator. An in-flight provider request may finish; no later destinations should start after worker acknowledgement."
            elif action == "resume_selected":
                if status_value != "paused":
                    changed.append({"type": job_type, "id": job.id, "status": status_value, "skipped": "not paused"}); continue
                if paused:
                    changed.append({"type": job_type, "id": job.id, "status": status_value, "skipped": "resume the account queue first"}); continue
                job.status = "queued"
                job.last_error = "Resumed by operator; prior results are retained. Reconcile remote state if an earlier destination may have completed."
                pending_dispatch.append((job_type, job.id, task, getattr(job, "priority", 1)))
            elif action == "restart_selected":
                if status_value not in {"failed", "canceled", "paused"}:
                    changed.append({"type": job_type, "id": job.id, "status": status_value, "skipped": "only failed, canceled, or paused jobs may be restarted"}); continue
                if status_value == "completed":
                    changed.append({"type": job_type, "id": job.id, "status": status_value, "skipped": "completed operations cannot be replayed from queue controls"}); continue
                if paused:
                    changed.append({"type": job_type, "id": job.id, "status": status_value, "skipped": "queue is paused"}); continue
                if isinstance(job, MarketplaceCrosspostJob):
                    prior = dict(job.execution_plan or {}) if isinstance(job.execution_plan, dict) else {}
                    prior.setdefault("operator_control_history", []).append({"action": "restart", "at": datetime.utcnow().isoformat(), "by": current_user.id, "prior_status": status_value})
                    job.execution_plan = prior
                else:
                    preview = dict(job.normalized_preview or {}) if isinstance(job.normalized_preview, dict) else {}
                    preview.setdefault("operator_control_history", []).append({"action": "restart", "at": datetime.utcnow().isoformat(), "by": current_user.id, "prior_status": status_value})
                    job.normalized_preview = preview
                job.status = "queued"
                job.last_error = "Restarted by operator; reconcile provider state if the previous attempt may have reached a marketplace."
                pending_dispatch.append((job_type, job.id, task, getattr(job, "priority", 1)))
            else:
                if status_value not in {"completed", "failed", "canceled", "blocked", "paused", "dismissed"}:
                    changed.append({"type": job_type, "id": job.id, "status": status_value, "skipped": "active jobs cannot be dismissed"}); continue
                if isinstance(job, MarketplaceCrosspostJob):
                    prior = dict(job.execution_plan or {}) if isinstance(job.execution_plan, dict) else {}
                    prior.setdefault("operator_control_history", []).append({"action": "dismiss_from_console", "at": datetime.utcnow().isoformat(), "by": current_user.id, "prior_status": status_value})
                    job.execution_plan = prior
                else:
                    preview = dict(job.normalized_preview or {}) if isinstance(job.normalized_preview, dict) else {}
                    preview.setdefault("operator_control_history", []).append({"action": "dismiss_from_console", "at": datetime.utcnow().isoformat(), "by": current_user.id, "prior_status": status_value})
                    job.normalized_preview = preview
                job.status = "dismissed"
            db.add(job)
            changed.append({"type": job_type, "id": job.id, "status": job.status})
    if found_ids != set(ids):
        db.rollback()
        raise HTTPException(status_code=404, detail="One or more selected jobs were not found for this account/type.")
    db.commit()
    for job_type, job_id, task, priority in pending_dispatch:
        dispatched = _enqueue_priority(task, job_id, priority)
        model = MarketplaceCrosspostJob if job_type == "crosspost" else MarketplaceImportJob
        job = db.execute(select(model).where(model.id == job_id, model.user_id == current_user.id)).scalar_one()
        job.task_id = dispatched.id
        db.add(job)
        for item in changed:
            if item["type"] == job_type and item["id"] == job_id:
                item["task_id"] = dispatched.id
    db.commit()
    return {"action": action, "jobs": changed}


class MarketplaceRoutingRulesRequest(BaseModel):
    rules: list[MarketplaceRoutingRule] = Field(default_factory=list, max_length=200)


class BulkCrosspostQueueRequest(BaseModel):
    listing_ids: list[int] = Field(min_length=1, max_length=500)
    marketplaces: list[str] = Field(default_factory=list, max_length=9)
    requested_mode: str = "bulk_operator_queue"
    priority: int = Field(default=1, ge=0, le=10)
    confirm_live_ebay: bool = False
    confirmation_phrase: str | None = None


@router.get("/marketplace-routing/rules")
def get_marketplace_routing_rules(current_user: User = Depends(get_current_user)):
    settings_json = current_user.settings_json if isinstance(current_user.settings_json, dict) else {}
    stored = settings_json.get("marketplace_routing") if isinstance(settings_json.get("marketplace_routing"), dict) else {}
    return {"rules": stored.get("rules") or [], "scope": "TENANT_USER", "source": "TENANT_OVERRIDE" if stored.get("rules") else "NO_RULES"}


@router.put("/marketplace-routing/rules")
def put_marketplace_routing_rules(
    payload: MarketplaceRoutingRulesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rules = [rule.model_dump(mode="json") for rule in payload.rules]
    identifiers = [rule["id"] for rule in rules]
    if len(set(identifiers)) != len(identifiers):
        raise HTTPException(status_code=422, detail="Routing rule IDs must be unique")
    settings_json = dict(current_user.settings_json or {})
    settings_json["marketplace_routing"] = {"rules": rules, "updated_by": current_user.id, "updated_at": datetime.utcnow().isoformat()}
    current_user.settings_json = settings_json
    db.add(current_user)
    db.commit()
    return {"rules": rules, "scope": "TENANT_USER", "source": "TENANT_OVERRIDE"}


def _build_job_status_summary(rows: list[tuple[str | None, int]]) -> dict:
    summary = {
        "total": 0,
        "queued": 0,
        "running": 0,
        "failed": 0,
        "completed": 0,
        "canceled": 0,
    }
    for status_value, count in rows:
        normalized = str(status_value or "").lower()
        summary["total"] += int(count or 0)
        if normalized in summary:
            summary[normalized] += int(count or 0)
    return summary


@router.post("/marketplace-jobs/bulk-requeue")
def bulk_requeue_marketplace_jobs(
    payload: BulkRequeueRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    statuses = {str(item).strip().lower() for item in payload.statuses if str(item).strip()}
    # Replaying queued/completed work can duplicate a remote CREATE. Use the
    # explicit, selected-job control endpoint for operator-approved restarts.
    allowed = {"failed", "canceled"}
    if not statuses or not statuses.issubset(allowed):
        raise HTTPException(status_code=400, detail="Unsupported job status for bulk requeue")
    if bool((current_user.settings_json or {}).get("marketplace_jobs_paused")):
        raise HTTPException(status_code=409, detail="The marketplace queue is paused. Resume it before restarting jobs.")
    queued = []
    if "crosspost" in payload.job_types:
        rows = db.execute(select(MarketplaceCrosspostJob).where(MarketplaceCrosspostJob.user_id == current_user.id, MarketplaceCrosspostJob.status.in_(statuses))).scalars().all()
        for job in rows:
            job.status = "queued"; job.last_error = None; job.result_summary = None
            task = _enqueue_priority(process_marketplace_crosspost_job_task, job.id, job.priority); job.task_id = task.id; db.add(job); queued.append({"type":"crosspost","id":job.id,"task_id":task.id})
    if "import" in payload.job_types:
        rows = db.execute(select(MarketplaceImportJob).where(MarketplaceImportJob.user_id == current_user.id, MarketplaceImportJob.status.in_(statuses))).scalars().all()
        for job in rows:
            job.status = "queued"; job.last_error = None
            task = _enqueue_priority(process_marketplace_import_job_task, job.id, job.priority); job.task_id = task.id; db.add(job); queued.append({"type":"import","id":job.id,"task_id":task.id})
    db.commit()
    return {"queued": queued, "count": len(queued)}


def _build_system_status_summary(
    db: Session,
    *,
    user_id: int,
    import_summary: dict,
    crosspost_summary: dict,
    allow_remote_refresh: bool = True,
) -> dict:
    # Aggregate in PostgreSQL instead of materializing the entire catalog on
    # every 10-second Jobs Console refresh (which caused request timeouts).
    catalog_total = int(db.execute(select(func.count(Listing.id)).where(Listing.user_id == user_id)).scalar_one())
    catalog_sold = int(db.execute(select(func.count(Listing.id)).where(Listing.user_id == user_id, (Listing.sold_at.is_not(None) | (Listing.quantity <= 0)))).scalar_one())
    # `custom_labels` is legacy JSON (and is not portable to one SQL JSON
    # predicate across the supported PostgreSQL/SQLite test environments).
    # Fetch only that narrow column rather than full Listing objects.
    archived_rows = db.execute(
        select(Listing.id, Listing.custom_labels).where(Listing.user_id == user_id, Listing.custom_labels.is_not(None))
    ).all()
    archived_listing_ids = {
        listing_id for listing_id, labels in archived_rows
        if isinstance(labels, list) and any(str(label).lower().startswith("archived") for label in labels)
    }
    catalog_archived = len(archived_listing_ids)
    # Dashboard catalog metrics aggregate the complete tenant catalog. They
    # must not inherit the paginated Listings page size and must count a
    # canonical listing once regardless of its number of live projections.
    legacy_live = and_(
        or_(
            Listing.status == ListingStatus.PUBLISHED,
            Listing.ebay_publish_status == EbayPublishStatus.POSTED,
        ),
        Listing.ebay_listing_id.is_not(None),
        func.trim(Listing.ebay_listing_id) != "",
    )
    not_sold = and_(Listing.sold_at.is_(None), func.coalesce(Listing.quantity, 1) > 0)
    # The Jobs Console polls frequently.  For large catalogs, never block the
    # request on an eBay network call; use a fresh tenant cache when available
    # and let the explicit/full dashboard path perform remote reconciliation.
    if allow_remote_refresh:
        snapshot = refresh_ebay_active_snapshot(db, user_id)
    else:
        cached_ids = cached_ebay_active_ids(db, user_id)
        stale_ids = last_ebay_active_ids(db, user_id) if cached_ids is None else None
        snapshot = {
            "status": "REMOTE_CACHED" if cached_ids is not None else ("STALE_REMOTE_SNAPSHOT" if stale_ids is not None else "LOCAL_LAST_KNOWN"),
            "listing_ids": cached_ids if cached_ids is not None else stale_ids,
            "verified_at": None,
        }
    ebay_remote_ids = snapshot.get("listing_ids")
    legacy_ebay_listing_ids = set(db.execute(
        select(Listing.id).where(Listing.user_id == user_id, not_sold, legacy_live)
    ).scalars().all())
    if ebay_remote_ids is None:
        ebay_live_listing_ids = legacy_ebay_listing_ids
        ebay_live_count = len(legacy_ebay_listing_ids)
    else:
        ebay_live_listing_ids = set(db.execute(
            select(Listing.id).where(
                Listing.user_id == user_id,
                Listing.ebay_listing_id.in_(ebay_remote_ids),
            )
        ).scalars().all()) if ebay_remote_ids else set()
        ebay_live_count = len(ebay_remote_ids)

    active_projection_rows = db.execute(
        select(MarketplaceListing.marketplace, MarketplaceListing.listing_id)
        .join(Listing, Listing.id == MarketplaceListing.listing_id)
        .where(
            Listing.user_id == user_id,
            not_sold,
            MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
            MarketplaceListing.marketplace_listing_id.is_not(None),
            func.trim(MarketplaceListing.marketplace_listing_id) != "",
        )
    ).all()
    live_listing_ids_by_marketplace: dict[str, set[int]] = {}
    for market, listing_id in active_projection_rows:
        market_key = str(getattr(market, "value", market) or "").strip().lower()
        if market_key == MarketplaceName.ebay.value:
            continue  # eBay is based on the refreshed official active-listing result.
        if listing_id not in archived_listing_ids:
            live_listing_ids_by_marketplace.setdefault(market_key, set()).add(listing_id)
    if ebay_remote_ids is None:
        live_listing_ids_by_marketplace[MarketplaceName.ebay.value] = legacy_ebay_listing_ids
    else:
        live_listing_ids_by_marketplace[MarketplaceName.ebay.value] = ebay_live_listing_ids
    distinct_live_listing_ids = set().union(*live_listing_ids_by_marketplace.values()) if live_listing_ids_by_marketplace else set()
    live_counts_by_marketplace: dict[str, int | None] = {
        market: None for market in (
            MarketplaceName.ebay.value,
            MarketplaceName.facebook.value,
            MarketplaceName.mercari.value,
            MarketplaceName.poshmark.value,
            MarketplaceName.vinted.value,
            MarketplaceName.etsy.value,
            MarketplaceName.offerup.value,
        )
    }
    live_counts_by_marketplace.update({
        market: (len(ids) if ids else None) for market, ids in live_listing_ids_by_marketplace.items()
    })
    if snapshot.get("status") == "UNAVAILABLE":
        live_counts_by_marketplace[MarketplaceName.ebay.value] = None
        catalog_published = None
        ebay_reconciliation_candidates = None
    else:
        live_counts_by_marketplace[MarketplaceName.ebay.value] = ebay_live_count
        catalog_published = len(distinct_live_listing_ids)
        stale_ebay_filter = and_(
            Listing.user_id == user_id,
            not_sold,
            legacy_live,
            ~Listing.ebay_listing_id.in_(ebay_remote_ids) if ebay_remote_ids else True,
        )
        ebay_reconciliation_candidates = int(db.execute(
            select(func.count(func.distinct(Listing.id))).where(stale_ebay_filter)
        ).scalar_one())
    active_projection_ids_set = set().union(*live_listing_ids_by_marketplace.values()) if live_listing_ids_by_marketplace else set()
    # Match the catalog's lifecycle buckets, including current preflight and
    # specificity blockers. This query is intentionally narrow (only draft,
    # ready, or explicitly review-flagged rows) and avoids serializing photos,
    # jobs, and marketplace relations for the whole catalog.
    candidate_rows = db.execute(
        select(
            Listing.id, Listing.status, Listing.title, Listing.description,
            Listing.category_suggestion, Listing.item_specifics,
            Listing.source_metadata, Listing.marketplace_data,
            Listing.processing_state, Listing.needs_review,
            Listing.restricted_review_required, Listing.ebay_publish_status,
            Listing.ebay_listing_id, Listing.source_type,
            Listing.sold_at, Listing.quantity, Listing.custom_labels,
        ).where(
            Listing.user_id == user_id,
            or_(
                Listing.status.in_([ListingStatus.draft, ListingStatus.ready, ListingStatus.INGESTED, ListingStatus.PROCESSED]),
                Listing.needs_review.is_(True),
                Listing.restricted_review_required.is_(True),
            ),
            not_sold,
        )
    ).all()
    catalog_review = 0
    catalog_ready = 0
    catalog_drafts = 0
    for row in candidate_rows:
        if row.id in archived_listing_ids:
            continue
        status_value = str(getattr(row.status, "value", row.status) or "").strip().lower()
        ebay_status = str(getattr(row.ebay_publish_status, "value", row.ebay_publish_status) or "").strip().upper()
        if row.id in active_projection_ids_set:
            continue
        if status_value in {"failed", "error"} or ebay_status == "FAILED":
            continue
        if str(row.processing_state or "").strip().lower() in {"needs_attention", "blocked"}:
            continue
        marketplace_data = row.marketplace_data if isinstance(row.marketplace_data, dict) else {}
        source_metadata = row.source_metadata if isinstance(row.source_metadata, dict) else {}
        preflight = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else {}
        by_marketplace = preflight.get("by_marketplace") if isinstance(preflight.get("by_marketplace"), dict) else {}
        configured_targets = [str(value).strip().lower() for value in marketplace_data.get("targets") or [] if str(value).strip()]
        if not configured_targets and str(row.source_type or "").strip().lower() == "amazon_vine":
            configured_targets = ["ebay"]
        has_current_target_blocker = any(
            isinstance(by_marketplace.get(market), dict)
            and not bool(by_marketplace[market].get("stale"))
            and bool(by_marketplace[market].get("blockers"))
            for market in configured_targets
        )
        if has_current_target_blocker:
            continue
        reviewability = classify_listing_reviewability(
            title=row.title,
            description=row.description,
            category=row.category_suggestion,
            item_specifics=row.item_specifics if isinstance(row.item_specifics, dict) else {},
            source_metadata=source_metadata,
            has_images=False,
        )
        if reviewability.get("caption_like_title") or reviewability.get("bare_identifier_title"):
            continue
        explicitly_approved = bool(source_metadata.get("operator_approved_at"))
        review_flagged = bool(row.needs_review or row.restricted_review_required)
        if review_flagged and not explicitly_approved and status_value not in {"ready", "posted", "published"}:
            review_ready = any(
                isinstance(by_marketplace.get(market), dict)
                and str(by_marketplace[market].get("status") or "").strip().lower() in {"ready", "ready_with_warnings", "published", "needs_review"}
                and not by_marketplace[market].get("blockers")
                for market in ("ebay", "facebook", "mercari", "poshmark", "vinted")
            )
            if review_ready:
                catalog_review += 1
            continue
        if status_value == "ready":
            targets = [str(value).strip().lower() for value in marketplace_data.get("targets") or [] if str(value).strip()]
            approved_target = any(
                isinstance(by_marketplace.get(market), dict)
                and str(by_marketplace[market].get("status") or "").strip().lower() in {"ready", "ready_with_warnings", "published"}
                for market in targets
            )
            if explicitly_approved and approved_target:
                catalog_ready += 1
            elif not explicitly_approved:
                catalog_drafts += 1
            continue
        if status_value in {"draft", "ingested", "processed"}:
            catalog_drafts += 1
    catalog_failed = int(db.execute(
        select(func.count(func.distinct(Listing.id))).where(
            Listing.user_id == user_id,
            or_(Listing.status == ListingStatus.FAILED, Listing.ebay_publish_status == EbayPublishStatus.FAILED),
        ~Listing.id.in_(active_projection_ids_set),
            Listing.sold_at.is_(None),
        )
    ).scalar_one())

    intake_batches_rows = db.execute(
        select(IntakePhotoBatch.status, func.count(IntakePhotoBatch.id))
        .where(IntakePhotoBatch.user_id == user_id)
        .group_by(IntakePhotoBatch.status)
    ).all()
    intake_batches = {str(status or "").lower(): int(count or 0) for status, count in intake_batches_rows}
    intake_photos_rows = db.execute(
        select(IntakeProviderMedia.processing_status, func.count(IntakeProviderMedia.id))
        .where(IntakeProviderMedia.user_id == user_id)
        .group_by(IntakeProviderMedia.processing_status)
    ).all()
    intake_photos = {str(status or "").lower(): int(count or 0) for status, count in intake_photos_rows}
    unread_notifications = int(
        db.execute(
            select(func.count(IntakeNotification.id)).where(IntakeNotification.user_id == user_id, IntakeNotification.read_at.is_(None))
        ).scalar_one()
    )
    queued_jobs = int(import_summary.get("queued", 0)) + int(crosspost_summary.get("queued", 0))
    running_jobs = int(import_summary.get("running", 0)) + int(crosspost_summary.get("running", 0))
    failed_jobs = int(import_summary.get("failed", 0)) + int(crosspost_summary.get("failed", 0))
    visible_total = int(db.execute(
        select(func.count(Listing.id)).where(
            Listing.user_id == user_id,
            Listing.sold_at.is_(None),
            func.coalesce(Listing.quantity, 1) > 0,
            ~Listing.id.in_(archived_listing_ids),
        )
    ).scalar_one())
    active_batches = sum(int(intake_batches.get(status, 0)) for status in ("collecting", "ready_for_draft", "drafted"))
    processing_photos = sum(int(intake_photos.get(status, 0)) for status in ("discovered", "changed", "retry", "processing"))
    message = "No active work detected."
    if running_jobs or processing_photos:
        message = "Intake or marketplace work is currently in progress."
    elif queued_jobs:
        message = "Jobs are queued and waiting for workers."
    elif catalog_review:
        message = "Review-ready drafts are waiting for operator approval."
    elif catalog_drafts:
        message = "Drafts are still being refined automatically."
    return {
        "catalog_total": catalog_total,
        "catalog_visible": visible_total,
        "catalog_drafts": catalog_drafts,
        "catalog_review": catalog_review,
        "catalog_ready": catalog_ready,
        "catalog_published": catalog_published,
        "catalog_live": catalog_published,
        "catalog_live_by_marketplace": live_counts_by_marketplace,
        "catalog_live_verification": snapshot.get("status", "LOCAL_LAST_KNOWN"),
        "catalog_live_verified_at": snapshot.get("verified_at"),
        "catalog_ebay_reconciliation_needed": ebay_reconciliation_candidates,
        "catalog_failed": catalog_failed,
        "catalog_sold": catalog_sold,
        "catalog_archived": catalog_archived,
        "intake_batches_active": active_batches,
        "intake_batches_ready": int(intake_batches.get("ready_for_draft", 0)),
        "intake_batches_drafted": int(intake_batches.get("drafted", 0)),
        "intake_photos_processing": processing_photos,
        "intake_photos_processed": int(intake_photos.get("processed", 0)),
        "intake_photos_retry": int(intake_photos.get("retry", 0)),
        "queued_jobs": queued_jobs,
        "running_jobs": running_jobs,
        "failed_jobs": failed_jobs,
        "unread_notifications": unread_notifications,
        "status_message": message,
    }


def _crosspost_operator_note(*, failed_target_count: int, blocked_target_count: int = 0, review_required_count: int, submitted_count: int) -> str | None:
    if failed_target_count:
        return "One or more targets failed before a usable marketplace handoff was produced. Review the target outcomes and retry after fixing the failing channel."
    if blocked_target_count:
        return "One or more targets were blocked because no live marketplace identity was confirmed. Fix the channel and retry only those targets."
    if review_required_count:
        return "This assisted cross-post run produced drafts, packets, or handoff steps that still need operator review before the marketplace listing is truly live."
    if submitted_count:
        return "At least one assisted target reached marketplace submission confirmation."
    return None


def _crosspost_operator_action(*, status_value: str, failed_target_count: int, blocked_target_count: int = 0, review_required_count: int, submitted_count: int) -> str | None:
    status_value = str(status_value or "").lower()
    if status_value in {"queued", "running"}:
        return "Monitor progress; open Details for per-target execution state."
    if failed_target_count:
        return "Open Details to review failing targets, fix the channel, then Retry."
    if blocked_target_count:
        return "Open Details, fix the blocked channel, then retry only the blocked targets."
    if review_required_count:
        return "Open Details and complete the marketplace handoff/review steps for pending targets."
    if submitted_count:
        return "Verify the marketplace listing is live, then follow up any remaining targets."
    return None


def _build_crosspost_target_outcomes(job: MarketplaceCrosspostJob) -> list[dict]:
    execution_targets = ((job.execution_plan or {}).get("targets") if isinstance(job.execution_plan, dict) else None) or []
    execution_targets = [item for item in execution_targets if isinstance(item, dict)]
    execution_by_market = {
        str(item.get("marketplace") or "").strip().lower(): item for item in execution_targets
    }
    result_items = ((job.result_summary or {}).get("results") if isinstance(job.result_summary, dict) else None) or []
    result_items = [item for item in result_items if isinstance(item, dict)]

    outcomes: list[dict] = []
    for item in result_items:
        marketplace = str(item.get("marketplace") or "").strip().lower()
        execution_mode = str(item.get("execution_mode") or execution_by_market.get(marketplace, {}).get("execution_mode") or "").strip().lower() or None
        result_status = str(item.get("status") or "").strip().lower() or None
        failed = result_status == "failed"
        pending = result_status in {"browser_automation_pending", "queued", "running"}
        response = item.get("response") if isinstance(item.get("response"), dict) else {}
        listing_confirmed = bool(response.get("marketplace_listing_id") or (response.get("listing_urls") or []))
        submitted = result_status in {"submitted_to_marketplace", "published"}
        if marketplace == "facebook" and execution_mode in {"browser_assist", "hosted_browser_assist"}:
            submitted = submitted and listing_confirmed
        bridge_fetch_pending = bool(response.get("bridge_fetch_status") == "pending") and execution_mode in {"browser_assist", "hosted_browser_assist"}
        requires_review = result_status in {
            "manual_handoff_ready",
            "provider_packet_ready",
            "browser_handoff_ready",
            "draft_form_filled",
            "manual_packet_ready",
        } or bridge_fetch_pending or result_status == "browser_automation_ready" or response.get("bridge_confirmation_status") == "submitted_without_visible_listing"
        requires_review = requires_review and not pending
        # A browser/manual handoff is not a publication.  Keep the historical
        # result visible, but expose it as a blocked destination unless a
        # confirmed remote identity was captured.
        unconfirmed_assisted = execution_mode in {"browser_assist", "hosted_browser_assist", "manual_only"} and requires_review and not listing_confirmed
        handoff_without_identity = result_status in {"manual_handoff_ready", "manual_packet_ready", "browser_handoff_ready"} or response.get("bridge_confirmation_status") == "submitted_without_visible_listing"
        blocked = bool(failed or (marketplace == "facebook" and unconfirmed_assisted and handoff_without_identity))
        operator_note = None
        if failed:
            operator_note = str(item.get("error") or "The assisted marketplace execution failed before completion.")
        elif result_status == "draft_form_filled":
            operator_note = "PosterPro reached the marketplace draft form, but final submission still needs operator review."
        elif result_status in {"manual_handoff_ready", "manual_packet_ready"}:
            operator_note = "PosterPro prepared a manual handoff packet, but no remote listing was created or confirmed."
        elif result_status == "provider_packet_ready":
            operator_note = "PosterPro prepared a provider packet that still needs downstream execution."
        elif result_status == "browser_handoff_ready":
            operator_note = "PosterPro prepared a browser automation handoff that still needs execution."
        elif bridge_fetch_pending:
            operator_note = "PosterPro reached the browser-assisted handoff, but bridge polling is still pending review."
        elif pending:
            operator_note = "PosterPro is publishing this listing through the hosted browser; the app is monitoring the submission automatically."
        elif response.get("bridge_confirmation_status") == "submitted_without_visible_listing":
            operator_note = "PosterPro clicked the Facebook submit action, but no visible seller listing was captured yet."
        elif submitted:
            operator_note = "PosterPro has confirmation that the assisted flow reached marketplace submission."
        outcomes.append(
            {
                "marketplace": marketplace,
                "execution_mode": execution_mode,
                "result_status": result_status,
                "failed": failed,
                "blocked": blocked,
                "pending": pending,
                "submitted": submitted,
                "requires_review": requires_review,
                "operator_note": operator_note,
            }
        )

    if outcomes:
        return outcomes

    pending_outcomes: list[dict] = []
    for item in execution_targets:
        marketplace = str(item.get("marketplace") or "").strip().lower()
        execution_mode = str(item.get("execution_mode") or "").strip().lower() or None
        pending_outcomes.append(
            {
                "marketplace": marketplace,
                "execution_mode": execution_mode,
                "result_status": "queued" if str(job.status or "").lower() in {"queued", "running"} else None,
                "failed": False,
                "submitted": False,
                "requires_review": execution_mode in {"manual_only", "provider_assist", "browser_assist", "hosted_browser_assist"},
                "operator_note": "This target is queued for assisted cross-post execution." if str(job.status or "").lower() in {"queued", "running"} else None,
            }
        )
    return pending_outcomes


def _crosspost_destination_operations(db: Session, job: MarketplaceCrosspostJob, assisted_jobs: list[MarketplaceExtensionJob]) -> list[dict]:
    """Normalize one parent job into explicit, independently inspectable targets."""
    result_items = ((job.result_summary or {}).get("results") if isinstance(job.result_summary, dict) else None) or []
    results = {
        str(row.get("marketplace") or "").strip().lower(): row
        for row in result_items if isinstance(row, dict)
    }
    children = {str(row.marketplace or "").lower(): row for row in assisted_jobs}
    attempts = db.execute(
        select(MarketplacePublishAttempt)
        .where(MarketplacePublishAttempt.job_id == job.id)
        .order_by(MarketplacePublishAttempt.created_at.asc(), MarketplacePublishAttempt.id.asc())
    ).scalars().all()
    attempts_by_market: dict[str, list[MarketplacePublishAttempt]] = {}
    for attempt in attempts:
        attempts_by_market.setdefault(str(attempt.marketplace.value if hasattr(attempt.marketplace, "value") else attempt.marketplace).lower(), []).append(attempt)

    operations = []
    for market in dict.fromkeys([*(job.target_marketplaces or []), *results.keys(), *children.keys()]):
        name = str(market or "").strip().lower()
        if not name:
            continue
        raw = results.get(name) or {}
        response = raw.get("response") if isinstance(raw.get("response"), dict) else {}
        child = children.get(name)
        child_result = child.result if child and isinstance(child.result, dict) else {}
        execution_diagnostic = child_result.get("execution_diagnostic") if isinstance(child_result.get("execution_diagnostic"), dict) else {}
        field_results = child_result.get("field_results") if isinstance(child_result.get("field_results"), list) else []
        field_failures = [
            {
                "field": item.get("field"),
                "error_code": item.get("error_code"),
                "detail": item.get("error_detail") or item.get("error") or item.get("verified_value"),
                "stage": item.get("failure_stage") or (item.get("interaction_diagnostic") or {}).get("stage"),
            }
            for item in field_results
            if isinstance(item, dict) and item.get("required") is True and item.get("filled") is not True
        ]
        attempts_for_market = attempts_by_market.get(name, [])
        latest_attempt = attempts_for_market[-1] if attempts_for_market else None
        state = str(child.status if child else raw.get("status") or ("NOT_DISPATCHED" if not raw else "UNKNOWN")).upper()
        provider_code = response.get("error_code") or response.get("provider_error_id")
        error_code = (child.error_code if child else None) or child_result.get("error_code") or provider_code
        error_detail = (child.error_detail if child else None) or child_result.get("error_detail") or child_result.get("error") or "; ".join(
            f"{item['field']}: {item['error_code'] or item['detail'] or 'unresolved'}" for item in field_failures
        ) or ", ".join(str(value) for value in (child_result.get("missing_required_fields") or [])) or response.get("error") or response.get("provider_short_message") or (latest_attempt.raw_error if latest_attempt else None)
        remote_id = (child.external_listing_id if child else None) or response.get("marketplace_listing_id") or (latest_attempt.marketplace_listing_id if latest_attempt else None)
        remote_url = (child.external_url if child else None) or response.get("external_url") or (response.get("listing_urls") or [None])[0] or ((child_result.get("page_url") if child_result.get("submission_performed") else None))
        if name == "ebay" and (provider_code == 25019 or str(provider_code) == "25019" or error_code == "EBAY_SELLER_RESTRICTED"):
            state = "BLOCKED_EXTERNAL"
            error_code = "EBAY_SELLER_RESTRICTED"
        operations.append({
            "marketplace": name,
            "status": state,
            "operation": child.action if child else raw.get("operation") or "CREATE",
            "execution_mode": raw.get("execution_mode") or ("browser_extension" if child else "direct_api" if name == "ebay" else None),
            "extension_job_id": child.id if child else raw.get("extension_job_id"),
            "publish_attempt_id": latest_attempt.id if latest_attempt else None,
            "error_code": error_code,
            "error_detail": str(error_detail)[:2000] if error_detail else None,
            "failure_stage": child_result.get("failed_stage") or execution_diagnostic.get("failed_stage") or next((item["stage"] for item in field_failures if item.get("stage")), None) or ("REQUIRED_FIELD_VALIDATION" if field_failures else child_result.get("stage")) or response.get("failure_stage") or response.get("provider_endpoint"),
            "field_failures": field_failures,
            "missing_required_fields": child_result.get("missing_required_fields") or [],
            "submission_performed": child_result.get("submission_performed") if child else response.get("provider_call_attempted"),
            "remote_listing_id": remote_id,
            "remote_url": remote_url,
            "attempt_count": child.attempt_count if child else max(
                len(attempts_for_market), 1 if response.get("provider_call_attempted") else 0,
            ),
            "provider_error_id": response.get("provider_error_id"),
            "provider_endpoint": response.get("provider_endpoint"),
            "provider_call_attempted": response.get("provider_call_attempted"),
            "remote_outcome": response.get("remote_outcome"),
            "created_at": child.created_at if child else (latest_attempt.created_at if latest_attempt else None),
            "claimed_at": child.claimed_at if child else None,
            "started_at": child.started_at if child else (latest_attempt.started_at if latest_attempt else None),
            "last_state_at": child.last_state_at if child else None,
            "completed_at": child.completed_at if child else (latest_attempt.finished_at if latest_attempt else None),
            "updated_at": child.updated_at if child else (latest_attempt.updated_at if latest_attempt else None),
        })
    return operations


def _serialize_crosspost_job(job: MarketplaceCrosspostJob, *, compact: bool = False, operator_email: str | None = None) -> dict:
    status_value = str(job.status or "").lower()
    can_cancel = status_value in {"queued", "running"}
    can_retry = status_value in {"failed", "canceled", "paused"}
    target_outcomes = _build_crosspost_target_outcomes(job)
    review_required_count = sum(1 for item in target_outcomes if item.get("requires_review"))
    submitted_count = sum(1 for item in target_outcomes if item.get("submitted"))
    failed_target_count = sum(1 for item in target_outcomes if item.get("failed"))
    blocked_target_count = sum(1 for item in target_outcomes if item.get("blocked"))
    operator_note = _crosspost_operator_note(
        failed_target_count=failed_target_count,
        blocked_target_count=blocked_target_count,
        review_required_count=review_required_count,
        submitted_count=submitted_count,
    )
    operator_action = _crosspost_operator_action(
        status_value=status_value,
        failed_target_count=failed_target_count,
        blocked_target_count=blocked_target_count,
        review_required_count=review_required_count,
        submitted_count=submitted_count,
    )
    ui_state_tone = "default"
    if status_value in {"queued", "running"}:
        ui_state_tone = "info"
    elif failed_target_count or blocked_target_count or status_value == "failed":
        ui_state_tone = "danger"
    elif review_required_count:
        ui_state_tone = "warning"
    elif submitted_count or status_value == "completed":
        ui_state_tone = "success"

    ui_primary_action = "View details"
    if status_value in {"queued", "running"}:
        ui_primary_action = "Monitor"
    elif failed_target_count or status_value == "failed":
        ui_primary_action = "Retry"
    elif review_required_count:
        ui_primary_action = "Complete handoff"
    elif submitted_count:
        ui_primary_action = "Verify listing"

    ui_secondary_actions: list[str] = []
    if can_retry:
        ui_secondary_actions.append("Retry")
    if can_cancel:
        ui_secondary_actions.append("Cancel")
    return {
        "id": job.id,
        "user_id": job.user_id,
        "operator_email": operator_email,
        "listing_id": job.listing_id,
        "source_marketplace": job.source_marketplace,
        "target_marketplaces": job.target_marketplaces,
        "requested_mode": job.requested_mode,
        "status": job.status,
        "execution_plan": None if compact else job.execution_plan,
        "result_summary": None if compact else job.result_summary,
        "task_id": job.task_id,
        "last_error": job.last_error,
        "can_retry": can_retry,
        "can_cancel": can_cancel,
        "operator_note": operator_note,
        "operator_action": operator_action,
        "review_required_count": review_required_count,
        "submitted_count": submitted_count,
        "failed_target_count": failed_target_count,
        "blocked_target_count": blocked_target_count,
        "target_outcomes": [] if compact else target_outcomes,
        "ui_state_tone": ui_state_tone,
        "ui_primary_action": ui_primary_action,
        "ui_secondary_actions": ui_secondary_actions,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "priority": int(job.priority or 1),
        "attempt_count": int(job.attempt_count or 0),
        "next_attempt_at": job.next_attempt_at,
        "requested_by": job.requested_by,
    }


def _serialize_import_job(job: MarketplaceImportJob, *, db: Session, compact: bool = False, operator_email: str | None = None) -> dict:
    status_value = str(job.status or "").lower()
    is_stale = _import_job_is_stale(job)
    can_cancel = status_value in {"queued", "running"} and not is_stale
    can_retry = status_value in {"failed", "canceled", "paused"} or is_stale

    operator_note = None
    operator_action = None
    if is_stale:
        operator_note = (
            f"This import job has not updated in over {int(STALE_IMPORT_JOB_AFTER.total_seconds() // 60)} minutes. "
            "Use Recover to reset the stuck worker record and queue a fresh attempt."
        )
        operator_action = "Recover this stuck import job."
    elif job.source_marketplace == MarketplaceName.ebay.value and job.last_error:
        lowered = str(job.last_error).lower()
        if "reconnect ebay" in lowered or "connect ebay" in lowered:
            operator_note = "Reconnect eBay from Settings, then recover or retry this import job."
            operator_action = "Reconnect eBay from Settings, then retry or recover this import."

    normalized_preview = job.normalized_preview if isinstance(job.normalized_preview, dict) else {}
    review_listing_ids: list[int] = []
    for key in ("new_listing_ids", "reused_listing_ids", "created_listing_ids"):
        values = normalized_preview.get(key)
        if isinstance(values, list):
            for value in values:
                try:
                    listing_id = int(value)
                except (TypeError, ValueError):
                    continue
                if listing_id not in review_listing_ids:
                    review_listing_ids.append(listing_id)
    if not review_listing_ids and job.created_listing_id:
        review_listing_ids.append(job.created_listing_id)

    review_items: list[dict] = []
    if review_listing_ids and not compact:
        # Import-job serialization is also used by worker/admin views without
        # a request-scoped current_user. The durable job's owner is the trusted
        # tenant boundary here; never rely on a client-supplied user id.
        listings = db.execute(select(Listing).where(Listing.user_id == job.user_id, Listing.id.in_(review_listing_ids))).scalars().all()
        listing_by_id = {listing.id: listing for listing in listings}
        for listing_id in review_listing_ids:
            listing = listing_by_id.get(listing_id)
            if not listing:
                continue
            review_items.append(
                {
                    "listing_id": listing.id,
                    "title": listing.title,
                    "status": getattr(listing.status, "value", listing.status),
                    "needs_review": bool(listing.needs_review),
                }
            )

    review_required_count = sum(1 for item in review_items if item.get("needs_review"))
    if operator_action is None:
        if status_value in {"queued", "running"}:
            operator_action = "Monitor progress; open Details to review preview and errors."
        elif status_value == "failed":
            operator_action = "Open Details to review the error, then Retry after fixing the issue."
        elif review_required_count:
            operator_action = "Open the imported listings and complete the required review steps."
        elif status_value == "completed" and review_items:
            operator_action = "Review the imported listings."

    ui_state_tone = "default"
    if status_value in {"queued", "running"} and not is_stale:
        ui_state_tone = "info"
    elif status_value == "failed":
        ui_state_tone = "danger"
    elif is_stale or review_required_count:
        ui_state_tone = "warning"
    elif status_value == "completed":
        ui_state_tone = "success"

    if is_stale:
        ui_primary_action = "Recover"
    elif status_value in {"queued", "running"}:
        ui_primary_action = "Monitor"
    elif status_value == "failed":
        ui_primary_action = "Retry"
    elif review_required_count:
        ui_primary_action = "Review imports"
    else:
        ui_primary_action = "View details"

    ui_secondary_actions: list[str] = []
    if can_retry:
        ui_secondary_actions.append("Retry")
    if can_cancel:
        ui_secondary_actions.append("Cancel")

    return {
        "id": job.id,
        "user_id": job.user_id,
        "operator_email": operator_email,
        "source_marketplace": job.source_marketplace,
        "source_listing_reference": job.source_listing_reference,
        "import_mode": job.import_mode,
        "status": job.status,
        "payload": None if compact else job.payload,
        "normalized_preview": None if compact else job.normalized_preview,
        "created_listing_id": job.created_listing_id,
        "task_id": job.task_id,
        "last_error": job.last_error,
        "is_stale": is_stale,
        "can_retry": can_retry,
        "can_cancel": can_cancel,
        "operator_note": operator_note,
        "operator_action": operator_action,
        "review_required_count": review_required_count,
        "review_items": [] if compact else review_items,
        "ui_state_tone": ui_state_tone,
        "ui_primary_action": ui_primary_action,
        "ui_secondary_actions": ui_secondary_actions,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "priority": int(job.priority or 1),
        "attempt_count": int(job.attempt_count or 0),
        "next_attempt_at": job.next_attempt_at,
        "requested_by": job.requested_by,
    }


def _bridge_desktop_access_payload(*, user_id: int, connect_session_id: str) -> dict[str, str]:
    token, expires_at = issue_bridge_desktop_token(user_id=user_id, connect_session_id=connect_session_id)
    return {
        "token": token,
        "websocket_path": "marketplace-jobs/bridge-desktop/ws",
        "expires_at": expires_at,
    }


def _build_preview_for_marketplace(*, listing: Listing, user: User, marketplace: str) -> CrosspostPreviewEntry:
    execution_mode = resolve_execution_mode(listing=listing, user=user, marketplace=marketplace)
    payload = build_marketplace_payload(listing, marketplace)
    notes: list[str] = []
    if execution_mode != "direct_api":
        notes.append(
            "This target is not configured for direct API publishing. PosterPro will create a structured handoff plan instead of a live publish call."
        )
    if marketplace == MarketplaceName.facebook.value:
        notes.append("Facebook Marketplace remains modeled as a manual/provider/browser-assisted channel in this deployment.")
    if execution_mode == "browser_assist":
        submit_policy = bridge_browser_submit_policy()
        notes.append(str(submit_policy["policy_note"]))
    return CrosspostPreviewEntry(
        marketplace=marketplace,
        execution_mode=execution_mode,
        payload=payload,
        notes=notes,
    )


@router.get("/marketplace-routing/preview/{listing_id}")
def preview_marketplace_routing(
    listing_id: int,
    marketplaces: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    manual = [value.strip().lower() for value in marketplaces.split(",") if value.strip()] if marketplaces is not None else None
    settings_json = current_user.settings_json if isinstance(current_user.settings_json, dict) else {}
    stored = settings_json.get("marketplace_routing") if isinstance(settings_json.get("marketplace_routing"), dict) else {}
    result = MarketplaceRoutingService().resolve(listing, stored.get("rules") or [], manual_override=manual)
    return {"listing_id": listing.id, **result}


@router.get("/listings/{listing_id}/crosspost-preview", response_model=list[CrosspostPreviewEntry])
def get_crosspost_preview(
    listing_id: int,
    marketplaces: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)

    requested = [item.strip().lower() for item in (marketplaces or "").split(",") if item.strip()]
    if not requested:
        requested = list((listing.marketplace_data or {}).get("targets") or [MarketplaceName.ebay.value])

    preview: list[CrosspostPreviewEntry] = []
    for market in requested:
        if market not in MARKETPLACE_DESTINATION_VALUES:
            continue
        preview.append(_build_preview_for_marketplace(listing=listing, user=current_user, marketplace=market))
    return preview


@router.post("/listings/{listing_id}/crosspost-jobs", response_model=CrosspostJobResponse)
def queue_crosspost_job(
    listing_id: int,
    payload: CrosspostQueueRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)

    requested = [item.strip().lower() for item in (payload.marketplaces or []) if item.strip()]
    routing_decision = None
    if not requested:
        settings_json = current_user.settings_json if isinstance(current_user.settings_json, dict) else {}
        routing_settings = settings_json.get("marketplace_routing") if isinstance(settings_json.get("marketplace_routing"), dict) else {}
        routing_rules = routing_settings.get("rules") or []
        if routing_rules:
            routing_decision = MarketplaceRoutingService().resolve(listing, routing_rules)
            requested = routing_decision["marketplaces"]
        else:
            requested = list((listing.marketplace_data or {}).get("targets") or [MarketplaceName.ebay.value])
    targets = [name for name in requested if name in MARKETPLACE_DESTINATION_VALUES]
    if not targets:
        raise HTTPException(status_code=400, detail="No supported target marketplaces were requested")
    if not customer_description_is_safe(listing.description):
        raise HTTPException(status_code=422, detail="Customer description contains internal review or marketplace guidance; revise before publishing")
    # A cross-post job is an execution contract, not merely a request to try
    # every selected destination.  Resolve each destination before the job is
    # durable so a missing Facebook category (or an eBay field rejection) is
    # reported to the caller instead of becoming a worker failure later.
    preflight_service = MarketplacePreflightService()
    preflight_by_marketplace = {
        market: preflight_service.preflight_listing(db, listing, market)
        for market in targets
    }
    blocked = {
        market: result
        for market, result in preflight_by_marketplace.items()
        if (
            not isinstance(result, dict)
            or result.get("blockers")
            or str(result.get("status") or "").lower() not in {"ready", "ready_with_warnings", "published"}
        )
    }
    if blocked:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "No cross-post job was queued because one or more destinations are not publishable.",
                "destination_readiness": _json_safe(preflight_by_marketplace),
            },
        )
    approved_payload_by_marketplace = {
        market: _json_safe(build_marketplace_payload(listing, market))
        for market in targets
    }
    execution_plan = {
        "targets": [
            _build_preview_for_marketplace(listing=listing, user=current_user, marketplace=market).model_dump()
            for market in targets
        ],
        "canonical_readiness": {
            market: canonical_listing_readiness(listing, marketplace=market)
            for market in targets
        },
        "preflight_by_marketplace": _json_safe(preflight_by_marketplace),
        "approved_execution_payload_by_marketplace": approved_payload_by_marketplace,
        "preflight_payload_hash_by_marketplace": {
            market: compute_payload_hash(payload)
            for market, payload in approved_payload_by_marketplace.items()
        },
        **({"routing_decision": routing_decision} if routing_decision else {}),
    }
    job = MarketplaceCrosspostJob(
        user_id=current_user.id,
        listing_id=listing.id,
        source_marketplace=((listing.marketplace_data or {}).get("source_marketplace") if isinstance(listing.marketplace_data, dict) else None),
        target_marketplaces=targets,
        requested_mode=payload.requested_mode,
        status="queued",
        execution_plan=execution_plan,
        priority=0,
        requested_by=current_user.id,
    )
    db.add(job)
    db.flush()

    for market in targets:
        existing = (
            db.execute(
                select(MarketplaceListing)
                .where(
                    MarketplaceListing.listing_id == listing.id,
                    MarketplaceListing.marketplace == MarketplaceName(market),
                )
                .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
            )
            .scalars()
            .first()
        )
        if not existing:
            db.add(
                MarketplaceListing(
                    listing_id=listing.id,
                    marketplace=MarketplaceName(market),
                    status=MarketplaceListingStatus.PENDING,
                    raw_response={"queued_by_crosspost_job": job.id},
                )
            )

    task = _enqueue_priority(process_marketplace_crosspost_job_task, job.id, job.priority)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return _serialize_crosspost_job(job, operator_email=current_user.email)


@router.post("/marketplace-jobs/bulk-crosspost")
def queue_bulk_crosspost_jobs(
    payload: BulkCrosspostQueueRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persist one independently retryable cross-post job per selected item."""
    listing_ids = list(dict.fromkeys(int(value) for value in payload.listing_ids))
    listings = db.execute(
        select(Listing).where(Listing.user_id == current_user.id, Listing.id.in_(listing_ids))
    ).scalars().all()
    by_id = {listing.id: listing for listing in listings}
    settings_json = current_user.settings_json if isinstance(current_user.settings_json, dict) else {}
    routing_settings = settings_json.get("marketplace_routing") if isinstance(settings_json.get("marketplace_routing"), dict) else {}
    routing_rules = routing_settings.get("rules") or []

    outcomes: list[dict] = []
    prepared: list[tuple[Listing, list[str], dict, MarketplaceCrosspostJob]] = []
    for listing_id in listing_ids:
        listing = by_id.get(listing_id)
        if not listing:
            outcomes.append({"listing_id": listing_id, "status": "NOT_FOUND"})
            continue
        if listing.status not in {ListingStatus.ready, ListingStatus.posted} or listing.sold_at or int(listing.quantity or 0) <= 0:
            outcomes.append({"listing_id": listing.id, "status": "NOT_READY", "reason": "Listing must be ready, unsold, and have positive quantity."})
            continue
        if not customer_description_is_safe(listing.description):
            outcomes.append({"listing_id": listing.id, "status": "BLOCKED", "reason": "Customer description contains internal review or marketplace guidance."})
            continue

        if payload.marketplaces:
            resolution = MarketplaceRoutingService().resolve(listing, routing_rules, manual_override=payload.marketplaces)
        elif routing_rules:
            resolution = MarketplaceRoutingService().resolve(listing, routing_rules)
        else:
            resolution = {"marketplaces": MarketplaceRoutingService.normalize_markets((listing.marketplace_data or {}).get("targets") or []), "source": "LISTING_DEFAULT", "matched_rule_ids": []}
        targets = resolution["marketplaces"]
        if not targets:
            outcomes.append({"listing_id": listing.id, "status": "NO_DESTINATIONS", "routing": resolution})
            continue
        # Resolve destination readiness before creating a durable job.  A
        # channel-wide blocker (notably Facebook's unverified live capability)
        # must be shown to the operator and must not create a batch of doomed
        # jobs.  Healthy destinations remain independently queueable.
        destination_readiness = {
            market: MarketplacePreflightService().preflight_listing(db, listing, market)
            for market in targets
        }
        blocked_targets = {
            market: result for market, result in destination_readiness.items()
            if isinstance(result, dict) and result.get("blockers")
        }
        if blocked_targets:
            ready_targets = [market for market in targets if market not in blocked_targets]
            if not ready_targets:
                outcomes.append({
                    "listing_id": listing.id,
                    "status": "BLOCKED",
                    "target_marketplaces": targets,
                    "destination_readiness": destination_readiness,
                })
                continue
            targets = ready_targets
        existing_job = db.execute(
            select(MarketplaceCrosspostJob)
            .where(MarketplaceCrosspostJob.user_id == current_user.id, MarketplaceCrosspostJob.listing_id == listing.id, MarketplaceCrosspostJob.status.in_(["queued", "running"]))
            .order_by(MarketplaceCrosspostJob.updated_at.desc(), MarketplaceCrosspostJob.id.desc())
        ).scalars().first()
        if existing_job:
            outcomes.append({"listing_id": listing.id, "status": "ALREADY_QUEUED", "job_id": existing_job.id, "target_marketplaces": existing_job.target_marketplaces or []})
            continue

        approved_payload_by_marketplace = {
            market: _json_safe(build_marketplace_payload(listing, market))
            for market in targets
        }
        plan = {
            "targets": [_build_preview_for_marketplace(listing=listing, user=current_user, marketplace=market).model_dump() for market in targets],
            "routing_decision": resolution,
            "canonical_readiness": {
                market: canonical_listing_readiness(listing, marketplace=market)
                for market in targets
            },
            "destination_readiness": destination_readiness,
            "preflight_by_marketplace": _json_safe({
                market: destination_readiness[market] for market in targets
            }),
            "approved_execution_payload_by_marketplace": approved_payload_by_marketplace,
            "preflight_payload_hash_by_marketplace": {
                market: compute_payload_hash(payload)
                for market, payload in approved_payload_by_marketplace.items()
            },
            "queued_from": "bulk_crosspost",
        }
        job = MarketplaceCrosspostJob(
            user_id=current_user.id,
            listing_id=listing.id,
            source_marketplace=((listing.marketplace_data or {}).get("source_marketplace") if isinstance(listing.marketplace_data, dict) else None),
            target_marketplaces=targets,
            requested_mode=payload.requested_mode,
            status="queued",
            execution_plan=plan,
            priority=payload.priority,
            requested_by=current_user.id,
        )
        db.add(job)
        prepared.append((listing, targets, resolution, job))

    if any(MarketplaceName.ebay.value in targets for _, targets, _, _ in prepared):
        if not payload.confirm_live_ebay or str(payload.confirmation_phrase or "").strip() != "QUEUE LIVE EBAY READY LISTINGS":
            db.rollback()
            raise HTTPException(status_code=400, detail="Bulk eBay queue requires explicit live-publish confirmation.")

    for listing, targets, _resolution, job in prepared:
        db.flush()
        for market in targets:
            existing_market_row = db.execute(
                select(MarketplaceListing.id).where(
                    MarketplaceListing.listing_id == listing.id,
                    MarketplaceListing.marketplace == MarketplaceName(market),
                ).limit(1)
            ).first()
            if not existing_market_row:
                db.add(MarketplaceListing(
                    listing_id=listing.id,
                    marketplace=MarketplaceName(market),
                    status=MarketplaceListingStatus.PENDING,
                    raw_response={"queued_by_crosspost_job": job.id},
                ))
        outcomes.append({"listing_id": listing.id, "status": "QUEUED", "job_id": job.id, "target_marketplaces": targets})
    db.commit()

    # Dispatch only after every accepted job is durable. If the broker is
    # temporarily unavailable, retain queued database rows and report the
    # dispatch failure instead of losing the bulk request.
    for outcome in outcomes:
        if outcome.get("status") != "QUEUED":
            continue
        job = db.get(MarketplaceCrosspostJob, outcome["job_id"])
        try:
            task = _enqueue_priority(process_marketplace_crosspost_job_task, job.id, job.priority)
            job.task_id = task.id
            outcome["task_id"] = task.id
        except Exception as exc:
            outcome["dispatch_error"] = str(exc)[:500]
        db.add(job)
    db.commit()
    return {"queued": sum(1 for row in outcomes if row.get("status") == "QUEUED"), "requested": len(listing_ids), "results": outcomes}


@router.get("/listings/{listing_id}/crosspost-jobs", response_model=list[CrosspostJobResponse])
def list_crosspost_jobs(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    jobs = db.execute(
        select(MarketplaceCrosspostJob)
        .where(MarketplaceCrosspostJob.listing_id == listing_id)
        .order_by(MarketplaceCrosspostJob.created_at.desc())
    ).scalars().all()
    return [_serialize_crosspost_job(job, operator_email=current_user.email) for job in jobs]


@router.post("/imports/marketplaces/jobs", response_model=MarketplaceImportJobResponse)
def create_marketplace_import_job(
    payload: MarketplaceImportJobCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source_marketplace = payload.source_marketplace.strip().lower()
    job = MarketplaceImportJob(
        user_id=current_user.id,
        source_marketplace=source_marketplace,
        source_listing_reference=payload.source_listing_reference,
        import_mode=payload.import_mode,
        status="queued",
        payload=payload.payload,
        priority=0,
        requested_by=current_user.id,
    )
    db.add(job)
    db.flush()
    task = _enqueue_priority(process_marketplace_import_job_task, job.id, job.priority)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return _serialize_import_job(job, db=db, operator_email=current_user.email)


@router.post("/imports/marketplaces/bulk", include_in_schema=False)
def create_marketplace_import_job_legacy_alias(
    payload: MarketplaceBulkImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compatibility alias for deployed clients that still call the old URL.

    Imports are persisted as a single durable job; this route intentionally
    does not perform a synchronous bulk import or any marketplace mutation.
    """
    jobs = []
    skipped = []
    for marketplace in dict.fromkeys(str(value).strip().lower() for value in payload.marketplaces if str(value).strip()):
        # eBay has a direct read-only inventory import. Other marketplaces use
        # the explicitly configured browser-assist mode; neither path writes to
        # the remote marketplace.
        import_mode = "sync_history" if marketplace == "ebay" else "browser_assist"
        try:
            job_payload = MarketplaceImportJobCreateRequest(
                source_marketplace=marketplace,
                import_mode=import_mode,
                payload={"max_listings": payload.max_listings or 50},
            )
            jobs.append(create_marketplace_import_job(payload=job_payload, db=db, current_user=current_user))
        except Exception as exc:  # retain a visible outcome for each requested target
            skipped.append({"marketplace": marketplace, "reason": str(exc)})
    return {"jobs": jobs, "skipped": skipped}


@router.get("/imports/marketplaces/jobs", response_model=list[MarketplaceImportJobResponse])
def list_marketplace_import_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    jobs = db.execute(
        select(MarketplaceImportJob)
        .where(MarketplaceImportJob.user_id == current_user.id)
        .order_by(MarketplaceImportJob.created_at.desc())
    ).scalars().all()
    return [_serialize_import_job(job, db=db, operator_email=current_user.email) for job in jobs]


@router.get("/marketplace-jobs/overview", response_model=MarketplaceJobsOverviewResponse)
def get_marketplace_jobs_overview(
    # Keep the console responsive even when historical job volume is large.
    # The console is an operational view; detail routes remain available for
    # drilling into a specific job.
    limit: int = Query(default=100, ge=1, le=250),
    compact: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import_query = (
        select(MarketplaceImportJob)
        .where(MarketplaceImportJob.user_id == current_user.id)
        .order_by(MarketplaceImportJob.created_at.desc())
    )
    crosspost_query = (
        select(MarketplaceCrosspostJob)
        .where(MarketplaceCrosspostJob.user_id == current_user.id)
        .order_by(MarketplaceCrosspostJob.created_at.desc())
    )
    if limit:
        import_query = import_query.limit(limit)
        crosspost_query = crosspost_query.limit(limit)

    import_query = import_query.where(MarketplaceImportJob.status != "dismissed")
    crosspost_query = crosspost_query.where(MarketplaceCrosspostJob.status != "dismissed")
    import_jobs = db.execute(import_query).scalars().all()
    crosspost_jobs = db.execute(crosspost_query).scalars().all()
    correction_jobs = db.execute(select(ListingCorrectionJob).where(ListingCorrectionJob.user_id == current_user.id).order_by(ListingCorrectionJob.priority.asc(), case((ListingCorrectionJob.priority == 0, ListingCorrectionJob.created_at), else_=None).desc(), case((ListingCorrectionJob.priority != 0, ListingCorrectionJob.created_at), else_=None).asc()).limit(limit)).scalars().all()

    import_summary_rows = db.execute(
        select(MarketplaceImportJob.status, func.count(MarketplaceImportJob.id))
        .where(MarketplaceImportJob.user_id == current_user.id)
        .group_by(MarketplaceImportJob.status)
    ).all()
    crosspost_summary_rows = db.execute(
        select(MarketplaceCrosspostJob.status, func.count(MarketplaceCrosspostJob.id))
        .where(MarketplaceCrosspostJob.user_id == current_user.id)
        .group_by(MarketplaceCrosspostJob.status)
    ).all()
    import_summary = _build_job_status_summary(import_summary_rows)
    crosspost_summary = _build_job_status_summary(crosspost_summary_rows)

    # Compact polling is the Jobs page hot path.  Avoid running the expensive
    # dashboard-wide summary there: it may refresh the remote eBay snapshot
    # and scan the catalog, which can otherwise make auto-refresh time out.
    if compact:
        catalog_count = int(db.execute(select(func.count(Listing.id)).where(Listing.user_id == current_user.id)).scalar_one())
        system_status = _build_system_status_summary(
            db,
            user_id=current_user.id,
            import_summary=import_summary,
            crosspost_summary=crosspost_summary,
            allow_remote_refresh=catalog_count <= 250,
        )
    else:
        system_status = _build_system_status_summary(
            db,
            user_id=current_user.id,
            import_summary=import_summary,
            crosspost_summary=crosspost_summary,
        )

    return {
        "import_jobs": [_serialize_import_job(job, db=db, compact=compact, operator_email=current_user.email) for job in import_jobs],
        "crosspost_jobs": [_serialize_crosspost_job(job, compact=compact, operator_email=current_user.email) for job in crosspost_jobs],
        "correction_jobs": [{"id": j.id, "listing_id": j.listing_id, "priority": j.priority, "fields": j.fields or [], "operator_note": j.operator_note, "status": j.status, "attempt_count": j.attempt_count, "result": j.result, "material_delta": j.material_delta, "failure_reason": j.failure_reason, "created_at": j.created_at.isoformat() if j.created_at else None} for j in correction_jobs],
        "import_summary": import_summary,
        "crosspost_summary": crosspost_summary,
        "system_status": system_status,
        "queue_control": {"paused": bool((current_user.settings_json or {}).get("marketplace_jobs_paused"))},
    }


@router.post("/marketplace-crosspost-jobs/{job_id}/retry", response_model=CrosspostJobResponse)
def retry_crosspost_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = db.get(MarketplaceCrosspostJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Crosspost job not found")
    ensure_user_owns_resource(current_user, job.user_id)
    if str(job.status or "").lower() not in {"failed", "canceled", "paused"}:
        raise HTTPException(status_code=409, detail="Only failed, canceled, or paused jobs can be restarted. Completed and active operations cannot be replayed here.")
    if bool((current_user.settings_json or {}).get("marketplace_jobs_paused")):
        raise HTTPException(status_code=409, detail="The marketplace queue is paused. Resume it before restarting jobs.")
    prior_plan = dict(job.execution_plan or {}) if isinstance(job.execution_plan, dict) else {}
    prior_plan.setdefault("operator_control_history", []).append({"action": "restart", "at": datetime.utcnow().isoformat(), "by": current_user.id, "prior_status": str(job.status or "").lower()})
    job.execution_plan = prior_plan
    job.status = "queued"
    job.last_error = "Restarted by operator; prior results are retained. Reconcile remote state if the previous attempt may have reached a marketplace."
    task = _enqueue_priority(process_marketplace_crosspost_job_task, job.id, job.priority)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return _serialize_crosspost_job(job, operator_email=current_user.email)

@router.get("/marketplace-publish-batches/{batch_id}")
def get_publish_batch(batch_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    batch = db.get(MarketplacePublishBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Publish batch not found")
    ensure_user_owns_resource(current_user, batch.user_id)
    jobs = db.execute(select(MarketplaceCrosspostJob).where(MarketplaceCrosspostJob.batch_id == batch_id).order_by(MarketplaceCrosspostJob.id)).scalars().all()
    return {"batch_id": batch.id, "user_id": batch.user_id, "operation": batch.operation, "selected_count": batch.selected_count, "selected_listing_ids": batch.selected_listing_ids or [], "requested_marketplaces": batch.requested_marketplaces or [], "status": batch.status, "summary": batch.summary or {}, "created_at": batch.created_at, "updated_at": batch.updated_at, "jobs": [_serialize_crosspost_job(job, compact=True) for job in jobs]}


@router.post("/marketplace-publish-batches/{batch_id}/release-facebook-paused")
def release_facebook_rollout_paused_children(batch_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Release only the existing, never-claimed paused CREATE children in the authorized Facebook rollout."""
    if batch_id != FACEBOOK_EBAY_ROLLOUT_BATCH_ID:
        raise HTTPException(status_code=404, detail="Facebook rollout batch not found")
    batch = db.execute(select(MarketplacePublishBatch).where(MarketplacePublishBatch.id == batch_id).with_for_update()).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Facebook rollout batch not found")
    ensure_user_owns_resource(current_user, batch.user_id)
    summary = dict(batch.summary or {}) if isinstance(batch.summary, dict) else {}
    control = dict(summary.get("rollout_control") or {}) if isinstance(summary.get("rollout_control"), dict) else {}
    if control.get("state") != "PAUSED_CANARY_DESCRIPTION_GUARD":
        raise HTTPException(status_code=409, detail="Batch is not in the expected guarded-pause state")
    items = [dict(item) for item in (summary.get("items") or []) if isinstance(item, dict)]
    paused_items = [item for item in items if item.get("status") == "PAUSED"]
    if not paused_items:
        raise HTTPException(status_code=409, detail="No paused Facebook CREATE children are eligible for release")
    parent_ids = [int(item["crosspost_job_id"]) for item in paused_items if item.get("crosspost_job_id") is not None]
    parents = db.execute(select(MarketplaceCrosspostJob).where(
        MarketplaceCrosspostJob.batch_id == batch_id,
        MarketplaceCrosspostJob.user_id == batch.user_id,
        MarketplaceCrosspostJob.id.in_(parent_ids),
    ).with_for_update()).scalars().all()
    parent_by_id = {parent.id: parent for parent in parents}
    child_ids = [int(item["extension_job_id"]) for item in paused_items if item.get("extension_job_id") is not None]
    children = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.id.in_(child_ids),
        MarketplaceExtensionJob.user_id == batch.user_id,
        MarketplaceExtensionJob.marketplace == "facebook",
        MarketplaceExtensionJob.action == "CREATE",
    ).with_for_update()).scalars().all()
    child_by_id = {child.id: child for child in children}
    # Validate every row before changing any: this release is all-or-nothing.
    for item in paused_items:
        child_id = int(item.get("extension_job_id") or 0)
        parent_id = int(item.get("crosspost_job_id") or 0)
        child, parent = child_by_id.get(child_id), parent_by_id.get(parent_id)
        result = child.result if child and isinstance(child.result, dict) else {}
        if (
            child is None or parent is None or child.crosspost_job_id != parent.id
            or child.status != "FAILED" or child.error_code != "ROLLOUT_PAUSED_FOR_STABILIZATION"
            or int(child.attempt_count or 0) != 0 or child.claimed_at or child.started_at
            or result.get("submission_performed") is True
            or child.external_listing_id or child.external_url
        ):
            raise HTTPException(status_code=409, detail=f"Paused Facebook child {child_id} is not a never-submitted, unclaimed CREATE")
        remote_rows = db.execute(select(MarketplaceListing).where(
            MarketplaceListing.listing_id == child.listing_id,
            MarketplaceListing.marketplace == MarketplaceName.facebook,
        ).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())).scalars().all()
        if any(remote.marketplace_listing_id and remote.status not in {MarketplaceListingStatus.DELETED, MarketplaceListingStatus.CLOSED} for remote in remote_rows):
            raise HTTPException(status_code=409, detail=f"Listing {child.listing_id} already has a Facebook remote identity; reconcile instead of creating")

    now = datetime.utcnow()
    released_ids = []
    for item in paused_items:
        child = child_by_id[int(item["extension_job_id"])]
        parent = parent_by_id[int(item["crosspost_job_id"])]
        snapshot = dict(child.payload_snapshot or {}) if isinstance(child.payload_snapshot, dict) else {}
        history = list(snapshot.get("execution_history") or [])
        history.append({
            "status": child.status, "error_code": child.error_code,
            "error_detail": child.error_detail, "result": child.result,
            "recorded_at": now.isoformat(), "release_reason": "operator_authorized_facebook_submitted_pending_review_policy",
        })
        snapshot["execution_history"] = history[-8:]
        child.payload_snapshot = snapshot
        child.status = "QUEUED"
        child.priority = 0
        child.device_id = None
        child.lease_expires_at = None
        child.claimed_at = None
        child.started_at = None
        child.completed_at = None
        child.error_code = None
        child.error_detail = None
        child.result = None
        child.last_state_at = now
        parent.status = "queued"
        parent.last_error = None
        plan = dict(parent.execution_plan or {}) if isinstance(parent.execution_plan, dict) else {}
        resume_history = list(plan.get("resume_history") or [])
        resume_history.append({"at": now.isoformat(), "by": current_user.id, "resumed_extension_job_ids": [child.id], "reason": "operator_authorized_facebook_rollout_release"})
        plan["resume_history"] = resume_history[-8:]
        parent.execution_plan = plan
        parent.attempt_count = int(parent.attempt_count or 0) + 1
        parent.next_attempt_at = None
        parent.last_error = None
        item.update({"status": "PENDING", "error_code": None})
        released_ids.append(child.id)
        db.add_all([child, parent])
    control.update({"state": "RELEASED", "reason": "Operator authorized sequential Facebook CREATE throughput with SUBMITTED_PENDING_REVIEW for accepted Publish outcomes.", "released_at": now.isoformat(), "released_by": current_user.id, "released_child_count": len(released_ids)})
    summary.update({
        "items": items, "rollout_control": control, "paused": 0,
        "pending": sum(item.get("status") in {"PENDING", "PAUSED"} for item in items),
        "released": len(released_ids), "updated_at": now.isoformat(),
    })
    batch.summary = summary
    batch.status = "queued"
    db.add(batch)
    db.commit()
    return {"batch_id": batch.id, "status": batch.status, "released_count": len(released_ids), "extension_job_ids": released_ids, "created_at_preserved": True, "submission_performed_children_released": 0}


@router.get("/marketplace-crosspost-jobs/{job_id}", response_model=CrosspostJobResponse)
def get_crosspost_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = db.get(MarketplaceCrosspostJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Crosspost job not found")
    ensure_user_owns_resource(current_user, job.user_id)
    result = _serialize_crosspost_job(job)
    listing = db.get(Listing, job.listing_id)
    result["listing_title"] = listing.title if listing else None
    result["batch_id"] = job.batch_id
    plan = job.execution_plan if isinstance(job.execution_plan, dict) else {}
    result["preflight_status"] = plan.get("preflight_status") or (job.result_summary or {}).get("preflight_status")
    result["blocker_codes"] = [str(item.get("code")) for item in (plan.get("blockers") or []) if isinstance(item, dict) and item.get("code")]
    result["blocker_messages"] = [str(item.get("message") or item.get("detail")) for item in (plan.get("blockers") or []) if isinstance(item, dict) and (item.get("message") or item.get("detail"))]
    assisted_jobs = db.execute(
        select(MarketplaceExtensionJob)
        .where(
            MarketplaceExtensionJob.crosspost_job_id == job.id,
            MarketplaceExtensionJob.user_id == current_user.id,
        )
        .order_by(MarketplaceExtensionJob.created_at.asc(), MarketplaceExtensionJob.id.asc())
    ).scalars().all()
    result["assisted_jobs"] = [
        {
            "id": assisted.id,
            "marketplace": assisted.marketplace,
            "action": assisted.action,
            "status": assisted.status,
            "device_id": assisted.device_id,
            "attempt_count": assisted.attempt_count,
            "claimed_at": assisted.claimed_at,
            "started_at": assisted.started_at,
            "completed_at": assisted.completed_at,
            "external_listing_id": assisted.external_listing_id,
            "external_url": assisted.external_url,
            "error_code": assisted.error_code,
            "error_detail": assisted.error_detail,
            "payload": assisted.payload_snapshot,
        }
        for assisted in assisted_jobs
    ]
    # A cross-post parent is one operator action, but every marketplace is an
    # independent operation.  Return a normalized destination view so the
    # Jobs drawer does not have to reverse-engineer provider/extension payloads
    # (and, importantly, does not hide nested provider errors).
    result["source_listing_status"] = listing.status if listing else None
    result["source_price"] = listing.listing_price if listing else None
    result["destination_operations"] = _crosspost_destination_operations(
        db, job, assisted_jobs
    )
    return result


@router.post("/marketplace-crosspost-jobs/{job_id}/destinations/{marketplace}/reconcile-not-found")
def record_create_reconciliation_not_found(
    job_id: int,
    marketplace: str,
    payload: ReconcileCreateNotFoundRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persist an operator's authenticated-account NOT FOUND result before a guarded retry."""
    market = str(marketplace or "").strip().lower()
    job = db.execute(select(MarketplaceCrosspostJob).where(
        MarketplaceCrosspostJob.id == job_id,
        MarketplaceCrosspostJob.user_id == current_user.id,
    ).with_for_update()).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Crosspost job not found")
    plan = job.execution_plan if isinstance(job.execution_plan, dict) else {}
    if not bool(plan.get("operator_live_confirmed")):
        raise HTTPException(status_code=409, detail="The parent job has no durable operator live-create authorization")
    child = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.crosspost_job_id == job.id,
        MarketplaceExtensionJob.user_id == current_user.id,
        MarketplaceExtensionJob.marketplace == market,
        MarketplaceExtensionJob.action == "CREATE",
    ).order_by(MarketplaceExtensionJob.id.desc()).with_for_update()).scalars().first()
    if child is None:
        raise HTTPException(status_code=404, detail="Destination CREATE operation not found")
    result = dict(child.result or {}) if isinstance(child.result, dict) else {}
    if child.external_listing_id or child.external_url or result.get("external_listing_id") or result.get("external_url"):
        raise HTTPException(status_code=409, detail="A remote listing identity exists; reconcile by linking it instead of recording NOT FOUND")
    if market == "facebook" and result.get("submission_performed") is True:
        raise HTTPException(status_code=409, detail="This CREATE crossed the Publish boundary; a no-match seller search remains reconciliation-only and cannot authorize another Publish")
    if child.error_code != "SUBMISSION_OUTCOME_UNKNOWN":
        raise HTTPException(status_code=409, detail="This destination does not have an uncertain submitted CREATE to reconcile")
    now = datetime.utcnow()
    result["remote_reconciliation"] = {
        "status": "NOT_FOUND",
        "confirmation": payload.confirmation,
        "source": "operator_confirmed_authenticated_account_reconciliation",
        "evidence_summary": " ".join(payload.evidence_summary.split())[:500],
        "verified_by_operator": int(current_user.id),
        "verified_at": now.isoformat(),
    }
    child.result = result
    child.error_code = "REMOTE_RECONCILIATION_NOT_FOUND"
    child.error_detail = "Operator-confirmed authenticated-account reconciliation found no matching remote listing; guarded retry may proceed under the original authorization."
    child.last_state_at = now
    db.add(child)
    db.commit()
    return {"job_id": job.id, "extension_job_id": child.id, "marketplace": market, "status": "REMOTE_RECONCILIATION_NOT_FOUND", "submission_performed": True, "retry_authorized_by_original_job": True}


@router.post("/marketplace-crosspost-jobs/{job_id}/resume-unsubmitted", response_model=CrosspostJobResponse)
def resume_unsubmitted_crosspost_job(
    job_id: int,
    marketplace: str | None = Query(default=None, description="Resume only this existing destination child; omitted resumes all safe children."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume the same operator-authorized cross-post only when no CREATE could have happened."""
    job = db.execute(select(MarketplaceCrosspostJob).where(
        MarketplaceCrosspostJob.id == job_id,
        MarketplaceCrosspostJob.user_id == current_user.id,
    ).with_for_update()).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Crosspost job not found")
    listing = db.get(Listing, job.listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Source listing not found")
    children = db.execute(select(MarketplaceExtensionJob).where(
        MarketplaceExtensionJob.crosspost_job_id == job.id,
        MarketplaceExtensionJob.user_id == current_user.id,
    ).order_by(MarketplaceExtensionJob.id).with_for_update()).scalars().all()
    requested_marketplace = str(marketplace or "").strip().lower()
    if requested_marketplace:
        children = [child for child in children if str(child.marketplace or "").lower() == requested_marketplace]
    if not children:
        raise HTTPException(status_code=409, detail="No existing browser destination operation matches this resume request")

    now = datetime.utcnow()
    resumed = []
    for child in children:
        status = str(child.status or "").upper()
        result = child.result if isinstance(child.result, dict) else {}
        if child.action != "CREATE":
            continue
        reconciliation = result.get("remote_reconciliation") if isinstance(result.get("remote_reconciliation"), dict) else {}
        confirmed_not_found = (
            reconciliation.get("status") == "NOT_FOUND"
            and reconciliation.get("confirmation") == "REMOTE_RECONCILIATION_NOT_FOUND"
            and reconciliation.get("source") == "operator_confirmed_authenticated_account_reconciliation"
            and reconciliation.get("verified_by_operator") == int(current_user.id)
        )
        if child.external_listing_id or child.external_url or (child.marketplace == "facebook" and result.get("submission_performed") is True) or (child.error_code == "SUBMISSION_OUTCOME_UNKNOWN" and result.get("submission_performed") is not False and not confirmed_not_found):
            raise HTTPException(status_code=409, detail=f"{child.marketplace} has a remote or uncertain CREATE outcome; reconcile before resume")
        if status in {"COMPLETED", "SUBMITTED", "SUBMITTING", "CLAIMED", "NAVIGATING", "FORM_FILLING"}:
            raise HTTPException(status_code=409, detail=f"{child.marketplace} operation is {status}; it cannot be safely replayed")
        stale_suppressed_unsubmitted = status == "CANCELLED" and child.error_code == "STALE_JOB_SUPPRESSED" and result.get("submission_performed") is not True
        if status not in {"QUEUED", "RETRYABLE", "FAILED", "AWAITING_OPERATOR_REVIEW", "BLOCKED_EXTERNAL"} and not stale_suppressed_unsubmitted:
            continue
        # A QUEUED child is safe to refresh only if it has never been claimed.
        if status == "QUEUED" and (child.attempt_count or child.claimed_at or child.started_at):
            raise HTTPException(status_code=409, detail=f"{child.marketplace} queued operation has prior execution evidence; reconcile before resume")
        raw_snapshot = dict(child.payload_snapshot or {})
        history = list(raw_snapshot.get("execution_history") or [])
        if child.result or child.error_code or status != "QUEUED":
            history.append({
                "status": status,
                "error_code": child.error_code,
                "error_detail": child.error_detail,
                "result": result,
                "recorded_at": now.isoformat(),
            })
        refreshed_payload = build_extension_marketplace_payload(
            db, user=current_user, listing=listing, marketplace=child.marketplace,
            action=child.action, external_id=None, external_url=None,
        )
        raw_snapshot["execution_history"] = history[-8:]
        raw_snapshot["marketplace_payload"] = refreshed_payload
        child.payload_snapshot = raw_snapshot
        child.status = "QUEUED"
        child.device_id = None
        child.lease_expires_at = None
        child.claimed_at = None
        child.started_at = None
        child.completed_at = None
        child.error_code = None
        child.error_detail = None
        child.result = None
        child.last_state_at = now
        db.add(child)
        resumed.append({"marketplace": child.marketplace, "extension_job_id": child.id, "status": child.status})
    if not resumed:
        raise HTTPException(status_code=409, detail="No safe, unsubmitted browser destination operation can be resumed")

    plan = dict(job.execution_plan or {}) if isinstance(job.execution_plan, dict) else {}
    resume_history = list(plan.get("resume_history") or [])
    resume_history.append({"at": now.isoformat(), "by": current_user.id, "resumed_extension_job_ids": [item["extension_job_id"] for item in resumed]})
    plan["resume_history"] = resume_history[-8:]
    job.execution_plan = plan
    job.status = "running"
    job.attempt_count = int(job.attempt_count or 0) + 1
    job.next_attempt_at = None
    job.last_error = "Existing unsubmitted browser operations resumed; prior provider blocks are retained in destination results."
    db.add(job)
    db.commit()
    return get_crosspost_job(job_id, db=db, current_user=current_user)


@router.post("/marketplace-crosspost-jobs/{job_id}/cancel", response_model=CrosspostJobResponse)
def cancel_crosspost_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = db.get(MarketplaceCrosspostJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Crosspost job not found")
    ensure_user_owns_resource(current_user, job.user_id)
    if str(job.status).lower() in {"completed", "failed", "canceled"}:
        raise HTTPException(status_code=400, detail="Only queued or running jobs can be canceled")
    job.status = "canceled"
    if not job.last_error:
        job.last_error = "Canceled by operator"
    if job.task_id:
        celery_app.control.revoke(job.task_id, terminate=False)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/marketplace-import-jobs/{job_id}/retry", response_model=MarketplaceImportJobResponse)
def retry_import_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = db.get(MarketplaceImportJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    ensure_user_owns_resource(current_user, job.user_id)
    status_value = str(job.status or "").lower()
    is_stale = _import_job_is_stale(job)
    if status_value in {"queued", "running"} and not is_stale:
        raise HTTPException(status_code=400, detail="Only failed, completed, canceled, or stale jobs can be retried")
    if status_value == "completed":
        raise HTTPException(status_code=409, detail="Completed imports cannot be replayed from queue controls. Review the existing result or create a new import.")
    previous_status = status_value or "unknown"
    if is_stale and job.task_id:
        celery_app.control.revoke(job.task_id, terminate=False)
    preview = dict(job.normalized_preview or {}) if isinstance(job.normalized_preview, dict) else {}
    preview.setdefault("operator_control_history", []).append({"action": "restart", "at": datetime.utcnow().isoformat(), "by": current_user.id, "prior_status": previous_status})
    job.normalized_preview = preview
    job.status = "queued"
    job.last_error = (
        f"Recovered by operator from stale {previous_status} state at {datetime.utcnow().isoformat()}."
        if is_stale
        else None
    )
    # Keep the last created identity and normalized output as audit evidence.
    task = _enqueue_priority(process_marketplace_import_job_task, job.id, job.priority)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return _serialize_import_job(job, db=db, operator_email=current_user.email)


@router.get("/marketplace-import-jobs/{job_id}", response_model=MarketplaceImportJobResponse)
def get_import_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = db.get(MarketplaceImportJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    ensure_user_owns_resource(current_user, job.user_id)
    return _serialize_import_job(job, db=db, operator_email=current_user.email)


@router.post("/marketplace-import-jobs/{job_id}/cancel", response_model=MarketplaceImportJobResponse)
def cancel_import_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = db.get(MarketplaceImportJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    ensure_user_owns_resource(current_user, job.user_id)
    if str(job.status).lower() in {"completed", "failed", "canceled"} or _import_job_is_stale(job):
        raise HTTPException(status_code=400, detail="Only queued or running jobs can be canceled")
    job.status = "canceled"
    if not job.last_error:
        job.last_error = "Canceled by operator"
    if job.task_id:
        celery_app.control.revoke(job.task_id, terminate=False)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _serialize_import_job(job, db=db)


@router.post("/marketplace-jobs/bridge-smoke-test", response_model=AutomationBridgeSmokeTestResponse)
def run_bridge_smoke_test(
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can test the automation bridge")
    return smoke_test_automation_bridge()


@router.get("/marketplace-jobs/bridge-assets/{asset_id}")
def get_marketplace_bridge_asset(
    asset_id: str,
    current_user: User = Depends(get_current_user),
):
    try:
        content, content_type, content_disposition = get_bridge_asset(asset_id)
        headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
        if content_disposition:
            headers["Content-Disposition"] = content_disposition
        return Response(
            content=content,
            media_type=content_type or "application/octet-stream",
            headers=headers,
        )
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/marketplace-jobs/bridge-accounts", response_model=BridgeMarketplaceAccountsEnvelope)
def get_bridge_accounts(
    marketplace: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    try:
        data = list_bridge_accounts(marketplace=marketplace.strip().lower() if marketplace else None)
        return data
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/marketplace-jobs/bridge-accounts/{marketplace}/{account_key}", response_model=BridgeMarketplaceAccountResponse)
def save_bridge_account(
    marketplace: str,
    account_key: str,
    payload: BridgeMarketplaceAccountUpsertRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        data = upsert_bridge_account(marketplace=marketplace, account_key=account_key, payload=payload.model_dump())
        return data
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/marketplace-jobs/bridge-accounts/{marketplace}/{account_key}/session", response_model=BridgeMarketplaceAccountResponse)
def save_bridge_account_session(
    marketplace: str,
    account_key: str,
    payload: BridgeMarketplaceAccountSessionRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        data = update_bridge_account_session(marketplace=marketplace, account_key=account_key, payload=payload.model_dump())
        return data
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/marketplace-jobs/bridge-accounts/{marketplace}/{account_key}/connect", response_model=BridgeMarketplaceAccountResponse)
def connect_marketplace_bridge_account(
    marketplace: str,
    account_key: str,
    payload: BridgeMarketplaceAccountConnectRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        data = connect_bridge_account(marketplace=marketplace, account_key=account_key, payload=payload.model_dump())
        return data
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/marketplace-jobs/bridge-accounts/{marketplace}/{account_key}/connect/start", response_model=BridgeMarketplaceConnectSessionResponse)
def start_marketplace_bridge_account_connect(
    marketplace: str,
    account_key: str,
    payload: BridgeMarketplaceAccountConnectRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        data = start_bridge_account_connect(marketplace=marketplace, account_key=account_key, payload=payload.model_dump())
        data["desktop_access"] = _bridge_desktop_access_payload(
            user_id=current_user.id,
            connect_session_id=str(data.get("connect_session_id") or ""),
        )
        return data
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/marketplace-jobs/bridge-connect-sessions/{connect_session_id}", response_model=BridgeMarketplaceConnectSessionResponse)
def get_marketplace_bridge_connect_session(
    connect_session_id: str,
    current_user: User = Depends(get_current_user),
):
    try:
        data = get_bridge_connect_session(connect_session_id)
        status_value = str(data.get("status") or "").strip().lower()
        if status_value not in {"completed", "failed", "canceled"}:
            data["desktop_access"] = _bridge_desktop_access_payload(
                user_id=current_user.id,
                connect_session_id=connect_session_id,
            )
        return data
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/marketplace-jobs/bridge-connect-sessions/{connect_session_id}/desktop-frame")
def get_marketplace_bridge_connect_desktop_frame(
    connect_session_id: str,
    current_user: User = Depends(get_current_user),
):
    try:
        frame = get_bridge_connect_desktop_frame(connect_session_id)
        return Response(
            content=frame,
            media_type="image/png",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/marketplace-jobs/bridge-connect-sessions/{connect_session_id}/desktop-actions/{action}")
def run_marketplace_bridge_connect_desktop_action(
    connect_session_id: str,
    action: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
):
    try:
        return send_bridge_connect_desktop_action(
            connect_session_id=connect_session_id,
            action=action,
            payload=payload,
        )
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
