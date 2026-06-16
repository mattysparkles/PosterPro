from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
    MarketplaceImportJobResponse,
)
from app.core.auth import ensure_user_owns_resource, get_current_user
from app.core.database import get_db
from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceImportJob, MarketplaceListing, User
from app.services.marketplace_execution import resolve_execution_mode
from app.services.marketplace_field_mapper import build_marketplace_payload
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


def _crosspost_operator_note(*, failed_target_count: int, review_required_count: int, submitted_count: int) -> str | None:
    if failed_target_count:
        return "One or more targets failed before a usable marketplace handoff was produced. Review the target outcomes and retry after fixing the failing channel."
    if review_required_count:
        return "This assisted cross-post run produced drafts, packets, or handoff steps that still need operator review before the marketplace listing is truly live."
    if submitted_count:
        return "At least one assisted target reached marketplace submission confirmation."
    return None


def _crosspost_operator_action(*, status_value: str, failed_target_count: int, review_required_count: int, submitted_count: int) -> str | None:
    status_value = str(status_value or "").lower()
    if status_value in {"queued", "running"}:
        return "Monitor progress; open Details for per-target execution state."
    if failed_target_count:
        return "Open Details to review failing targets, fix the channel, then Retry."
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
        submitted = result_status in {"submitted_to_marketplace", "published"}
        requires_review = result_status in {
            "manual_handoff_ready",
            "provider_packet_ready",
            "browser_handoff_ready",
            "draft_form_filled",
            "manual_packet_ready",
        }
        operator_note = None
        if failed:
            operator_note = str(item.get("error") or "The assisted marketplace execution failed before completion.")
        elif result_status == "draft_form_filled":
            operator_note = "PosterPro reached the marketplace draft form, but final submission still needs operator review."
        elif result_status in {"manual_handoff_ready", "manual_packet_ready"}:
            operator_note = "PosterPro prepared a manual handoff packet for operator completion."
        elif result_status == "provider_packet_ready":
            operator_note = "PosterPro prepared a provider packet that still needs downstream execution."
        elif result_status == "browser_handoff_ready":
            operator_note = "PosterPro prepared a browser automation handoff that still needs execution."
        elif submitted:
            operator_note = "PosterPro has confirmation that the assisted flow reached marketplace submission."
        outcomes.append(
            {
                "marketplace": marketplace,
                "execution_mode": execution_mode,
                "result_status": result_status,
                "failed": failed,
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
                "requires_review": execution_mode in {"manual_only", "provider_assist", "browser_assist"},
                "operator_note": "This target is queued for assisted cross-post execution." if str(job.status or "").lower() in {"queued", "running"} else None,
            }
        )
    return pending_outcomes


def _serialize_crosspost_job(job: MarketplaceCrosspostJob) -> dict:
    status_value = str(job.status or "").lower()
    can_cancel = status_value in {"queued", "running"}
    can_retry = status_value in {"completed", "failed", "canceled"}
    target_outcomes = _build_crosspost_target_outcomes(job)
    review_required_count = sum(1 for item in target_outcomes if item.get("requires_review"))
    submitted_count = sum(1 for item in target_outcomes if item.get("submitted"))
    failed_target_count = sum(1 for item in target_outcomes if item.get("failed"))
    operator_note = _crosspost_operator_note(
        failed_target_count=failed_target_count,
        review_required_count=review_required_count,
        submitted_count=submitted_count,
    )
    operator_action = _crosspost_operator_action(
        status_value=status_value,
        failed_target_count=failed_target_count,
        review_required_count=review_required_count,
        submitted_count=submitted_count,
    )
    ui_state_tone = "default"
    if status_value in {"queued", "running"}:
        ui_state_tone = "info"
    elif failed_target_count or status_value == "failed":
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
        "listing_id": job.listing_id,
        "source_marketplace": job.source_marketplace,
        "target_marketplaces": job.target_marketplaces,
        "requested_mode": job.requested_mode,
        "status": job.status,
        "execution_plan": job.execution_plan,
        "result_summary": job.result_summary,
        "task_id": job.task_id,
        "last_error": job.last_error,
        "can_retry": can_retry,
        "can_cancel": can_cancel,
        "operator_note": operator_note,
        "operator_action": operator_action,
        "review_required_count": review_required_count,
        "submitted_count": submitted_count,
        "failed_target_count": failed_target_count,
        "target_outcomes": target_outcomes,
        "ui_state_tone": ui_state_tone,
        "ui_primary_action": ui_primary_action,
        "ui_secondary_actions": ui_secondary_actions,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _serialize_import_job(job: MarketplaceImportJob, *, db: Session) -> dict:
    status_value = str(job.status or "").lower()
    is_stale = _import_job_is_stale(job)
    can_cancel = status_value in {"queued", "running"} and not is_stale
    can_retry = status_value in {"completed", "failed", "canceled"} or is_stale

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
    if review_listing_ids:
        listings = db.execute(select(Listing).where(Listing.id.in_(review_listing_ids))).scalars().all()
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
        "source_marketplace": job.source_marketplace,
        "source_listing_reference": job.source_listing_reference,
        "import_mode": job.import_mode,
        "status": job.status,
        "payload": job.payload,
        "normalized_preview": job.normalized_preview,
        "created_listing_id": job.created_listing_id,
        "task_id": job.task_id,
        "last_error": job.last_error,
        "is_stale": is_stale,
        "can_retry": can_retry,
        "can_cancel": can_cancel,
        "operator_note": operator_note,
        "operator_action": operator_action,
        "review_required_count": review_required_count,
        "review_items": review_items,
        "ui_state_tone": ui_state_tone,
        "ui_primary_action": ui_primary_action,
        "ui_secondary_actions": ui_secondary_actions,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
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
        if market not in MarketplaceName._value2member_map_:
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
    if not requested:
        requested = list((listing.marketplace_data or {}).get("targets") or [MarketplaceName.ebay.value])
    targets = [name for name in requested if name in MarketplaceName._value2member_map_]
    if not targets:
        raise HTTPException(status_code=400, detail="No supported target marketplaces were requested")

    execution_plan = {
        "targets": [
            _build_preview_for_marketplace(listing=listing, user=current_user, marketplace=market).model_dump()
            for market in targets
        ]
    }
    job = MarketplaceCrosspostJob(
        user_id=current_user.id,
        listing_id=listing.id,
        source_marketplace=((listing.marketplace_data or {}).get("source_marketplace") if isinstance(listing.marketplace_data, dict) else None),
        target_marketplaces=targets,
        requested_mode=payload.requested_mode,
        status="queued",
        execution_plan=execution_plan,
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

    task = process_marketplace_crosspost_job_task.delay(job.id)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return _serialize_crosspost_job(job)


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
    return [_serialize_crosspost_job(job) for job in jobs]


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
    )
    db.add(job)
    db.flush()
    task = process_marketplace_import_job_task.delay(job.id)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return _serialize_import_job(job, db=db)


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
    return [_serialize_import_job(job, db=db) for job in jobs]


@router.get("/marketplace-jobs/overview", response_model=MarketplaceJobsOverviewResponse)
def get_marketplace_jobs_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import_jobs = db.execute(
        select(MarketplaceImportJob)
        .where(MarketplaceImportJob.user_id == current_user.id)
        .order_by(MarketplaceImportJob.created_at.desc())
    ).scalars().all()
    crosspost_jobs = db.execute(
        select(MarketplaceCrosspostJob)
        .where(MarketplaceCrosspostJob.user_id == current_user.id)
        .order_by(MarketplaceCrosspostJob.created_at.desc())
    ).scalars().all()
    return {
        "import_jobs": [_serialize_import_job(job, db=db) for job in import_jobs],
        "crosspost_jobs": [_serialize_crosspost_job(job) for job in crosspost_jobs],
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
    job.status = "queued"
    job.last_error = None
    job.result_summary = None
    task = process_marketplace_crosspost_job_task.delay(job.id)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return _serialize_crosspost_job(job)


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
    return _serialize_crosspost_job(job)


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
    previous_status = status_value or "unknown"
    if is_stale and job.task_id:
        celery_app.control.revoke(job.task_id, terminate=False)
    job.status = "queued"
    job.last_error = (
        f"Recovered by operator from stale {previous_status} state at {datetime.utcnow().isoformat()}."
        if is_stale
        else None
    )
    job.created_listing_id = None
    task = process_marketplace_import_job_task.delay(job.id)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return _serialize_import_job(job, db=db)


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
    return _serialize_import_job(job, db=db)


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
