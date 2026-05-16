from __future__ import annotations

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
    connect_bridge_account,
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
from app.workers.tasks import process_marketplace_crosspost_job_task, process_marketplace_import_job_task
from app.workers.celery_app import celery_app

router = APIRouter()


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
        existing = db.execute(
            select(MarketplaceListing).where(
                MarketplaceListing.listing_id == listing.id,
                MarketplaceListing.marketplace == MarketplaceName(market),
            )
        ).scalar_one_or_none()
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
    return job


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
    return db.execute(
        select(MarketplaceCrosspostJob)
        .where(MarketplaceCrosspostJob.listing_id == listing_id)
        .order_by(MarketplaceCrosspostJob.created_at.desc())
    ).scalars().all()


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
    return job


@router.get("/imports/marketplaces/jobs", response_model=list[MarketplaceImportJobResponse])
def list_marketplace_import_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.execute(
        select(MarketplaceImportJob)
        .where(MarketplaceImportJob.user_id == current_user.id)
        .order_by(MarketplaceImportJob.created_at.desc())
    ).scalars().all()


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
        "import_jobs": import_jobs,
        "crosspost_jobs": crosspost_jobs,
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
    return job


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
    return job


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
    job.status = "queued"
    job.last_error = None
    job.created_listing_id = None
    task = process_marketplace_import_job_task.delay(job.id)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


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
    return job


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


@router.post("/marketplace-jobs/bridge-smoke-test", response_model=AutomationBridgeSmokeTestResponse)
def run_bridge_smoke_test(
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can test the automation bridge")
    return smoke_test_automation_bridge()


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
