from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from datetime import UTC, datetime
from sqlalchemy.orm import Session

from app.api.schemas import PricingApplyRequest, PricingBulkRequest
from app.core.auth import ensure_user_owns_resource, get_current_user, resolve_user_scope
from app.core.database import get_db
from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, ListingRevision, MarketplaceCrosspostJob, MarketplaceListing, User
from app.services.alert_service import AlertService
from app.services.analytics_service import AnalyticsService
from app.services.listing_optimizer_service import ListingOptimizerService
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.pricing_research_service import STALE_PRICING_DAYS, compute_listing_quality_summary, validate_marketplace_readiness

router = APIRouter()


class PricingDecisionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=32)


@router.get("/analytics/overview")
def analytics_overview(
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return AnalyticsService().compute_overview(db, resolve_user_scope(current_user, user_id))


@router.get("/analytics/dashboard")
def analytics_dashboard(
    user_id: int | None = Query(None),
    days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return AnalyticsService().dashboard(db, resolve_user_scope(current_user, user_id), days=days)


@router.get("/analytics/listings/{listing_id}")
def analytics_listing(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    try:
        return AnalyticsService().listing_detail(db, listing_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pricing/recommendations/{listing_id}")
def pricing_recommendation(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    try:
        return PricingIntelligenceService().recommend_price(db, listing_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/pricing/recommendations/{listing_id}/refresh")
def refresh_pricing_recommendation(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    return PricingIntelligenceService().recommend_price(db, listing_id)


@router.post("/pricing/recommendations/{listing_id}/apply")
def apply_pricing_recommendation(
    listing_id: int,
    payload: PricingApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    pricing = PricingIntelligenceService().recommend_price(db, listing_id)
    strategy = str(payload.strategy or "recommended").strip().lower()
    if strategy == "quick_sale":
        price = pricing.get("quick_sale_price")
    elif strategy == "floor":
        price = pricing.get("floor_price")
    elif strategy == "stretch":
        price = pricing.get("stretch_price")
    else:
        price = pricing.get("recommended_price")
    listing.suggested_price = price
    listing.listing_price = price
    marketplace_data = dict(listing.marketplace_data or {})
    if payload.override_reason:
        marketplace_data["manual_price_override_reason"] = payload.override_reason
    listing.marketplace_data = marketplace_data
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return {
        "listing_id": listing.id,
        "applied_price": price,
        "strategy": strategy,
        "pricing_analysis": pricing,
    }


@router.post("/pricing/recommendations/{listing_id}/decision")
def decide_underpricing(
    listing_id: int,
    payload: PricingDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record one of the three operator decisions for strong sold-comp risk."""
    action = payload.action.strip().lower()
    if action not in {"leave_price_as_is", "auto_fix_price", "pause_listing"}:
        raise HTTPException(status_code=422, detail="Choose leave_price_as_is, auto_fix_price, or pause_listing.")
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    pricing = PricingIntelligenceService().recommend_price(db, listing_id)
    risk = pricing.get("underpricing_risk") if isinstance(pricing.get("underpricing_risk"), dict) else {}
    if risk.get("level") not in {"POTENTIAL", "SEVERE"} or not risk.get("evidence_signature"):
        raise HTTPException(status_code=409, detail="There is no current high-confidence underpricing alert to resolve.")

    marketplace_data = dict(listing.marketplace_data or {})
    active_rows = db.query(MarketplaceListing).filter(
        MarketplaceListing.listing_id == listing.id,
        MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
    ).all()
    active = {str(row.marketplace.value if hasattr(row.marketplace, "value") else row.marketplace).lower(): row for row in active_rows if row.marketplace_listing_id}
    result: dict = {"listing_id": listing.id, "action": action, "active_marketplaces": sorted(active), "jobs": [], "manual_end_required": []}

    if action == "leave_price_as_is":
        marketplace_data["pricing_underpricing_acknowledgement"] = {
            "evidence_signature": risk["evidence_signature"],
            "acknowledged_at": datetime.now(UTC).isoformat(),
            "acknowledged_by": current_user.id,
            "price": risk.get("current_price"),
            "sold_median": risk.get("sold_median"),
        }
        marketplace_data["manual_price_override"] = risk.get("current_price")
        marketplace_data["manual_price_override_reason"] = "Operator intentionally acknowledged the high-confidence pricing warning."
        db.add(listing); listing.marketplace_data = marketplace_data; db.commit()
        result["status"] = "ACKNOWLEDGED"
        result["price_preserved"] = risk.get("current_price")
        return result

    if action == "auto_fix_price":
        old_price = listing.listing_price or listing.suggested_price
        new_price = pricing.get("recommended_price")
        if not new_price or float(new_price) <= 0:
            raise HTTPException(status_code=409, detail="A safe recommended price is not available.")
        listing.listing_price = float(new_price)
        listing.suggested_price = float(new_price)
        marketplace_data.pop("pricing_review_pause", None)
        marketplace_data.pop("pricing_underpricing_acknowledgement", None)
        marketplace_data["manual_price_override_reason"] = None
        listing.marketplace_data = marketplace_data
        targets = [market for market in sorted(active) if market == "ebay" and bool(listing.ebay_listing_id) or market != "ebay"]
        revision_no = int(marketplace_data.get("posterpro_revision") or 0) + 1
        changed = {"listing_price": {"before": old_price, "after": float(new_price)}}
        revision = ListingRevision(listing_id=listing.id, user_id=current_user.id, revision=revision_no, operation="pricing_auto_fix", changed_fields=changed, marketplaces_targeted=targets, sync_state="update_queued" if targets else "local", status="queued" if targets else "recorded")
        db.add(revision); db.flush()
        for market in targets:
            identity = active[market].marketplace_listing_id
            job = MarketplaceCrosspostJob(
                user_id=current_user.id,
                listing_id=listing.id,
                source_marketplace="posterpro",
                target_marketplaces=[market],
                requested_mode="pricing_auto_fix_update",
                status="queued",
                priority=0,
                requested_by=current_user.id,
                execution_plan={"operation": "update", "revision": revision_no, "revision_id": revision.id, "external_listing_id": identity, "changed_fields": changed},
            )
            db.add(job); db.flush()
            result["jobs"].append({"job_id": job.id, "marketplace": market, "status": "queued", "external_listing_id": identity})
        marketplace_data["posterpro_revision"] = revision_no
        marketplace_data["sync_state"] = "update_queued" if targets else "local"
        listing.marketplace_data = marketplace_data
        db.add(listing); db.commit()
        for item in result["jobs"]:
            try:
                from app.api.marketplace_jobs import _enqueue_priority
                from app.workers.tasks import process_marketplace_crosspost_job_task
                task = _enqueue_priority(process_marketplace_crosspost_job_task, item["job_id"], 0)
                db.query(MarketplaceCrosspostJob).filter(MarketplaceCrosspostJob.id == item["job_id"]).update({"task_id": task.id})
                item["task_id"] = task.id
            except Exception as exc:
                # The durable job remains visible/retryable if the task broker is unavailable.
                item["dispatch_error"] = type(exc).__name__
        db.commit()
        result.update({"status": "PRICE_UPDATED", "old_price": old_price, "applied_price": float(new_price), "sync_state": marketplace_data["sync_state"]})
        return result

    marketplace_data["pricing_review_pause"] = {"active": True, "reason": "High-confidence underpricing review", "set_at": datetime.now(UTC).isoformat(), "evidence_signature": risk["evidence_signature"]}
    if not active:
        listing.status = ListingStatus.draft
    listing.marketplace_data = marketplace_data
    db.add(listing); db.commit()
    for market, row in active.items():
        if market == MarketplaceName.ebay.value:
            result["manual_end_required"].append({"marketplace": market, "external_listing_id": row.marketplace_listing_id, "reason": "This deployment has no safe direct eBay end-listing operation; the current offer remains live until ended in eBay."})
            continue
        try:
            from app.services.marketplace_extension_jobs import queue_extension_marketplace_action
            job, created = queue_extension_marketplace_action(db, user_id=current_user.id, listing=listing, marketplace=market, action="END", priority=0)
            result["jobs"].append({"job_id": job.id, "marketplace": market, "status": job.status, "action": "END", "deduplicated": not created, "external_listing_id": job.external_listing_id})
        except Exception as exc:
            result["manual_end_required"].append({"marketplace": market, "external_listing_id": row.marketplace_listing_id, "reason": str(exc)[:240]})
    db.commit()
    result.update({"status": "PAUSED_FOR_REVIEW", "publishing_blocked": True})
    return result


@router.post("/pricing/recommendations/bulk")
def bulk_pricing_action(
    payload: PricingBulkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    results: list[dict] = []
    action = str(payload.action or "refresh").strip().lower()
    for listing_id in payload.listing_ids:
        listing = db.get(Listing, listing_id)
        if not listing:
            continue
        ensure_user_owns_resource(current_user, listing.user_id)
        if action == "add_manual_comp":
            marketplace_data = dict(listing.marketplace_data or {})
            manual = marketplace_data.get("pricing_manual_comps")
            if not isinstance(manual, list):
                manual = []
            if payload.manual_comp:
                manual.append(payload.manual_comp)
            marketplace_data["pricing_manual_comps"] = manual
            listing.marketplace_data = marketplace_data
            db.add(listing)
            db.commit()
            results.append({"listing_id": listing.id, "status": "updated_manual_comp"})
            continue

        pricing = PricingIntelligenceService().recommend_price(db, listing_id)
        if action == "apply_quick_sale":
            listing.suggested_price = pricing.get("quick_sale_price")
            listing.listing_price = pricing.get("quick_sale_price")
        elif action == "apply_recommended":
            listing.suggested_price = pricing.get("recommended_price")
            listing.listing_price = pricing.get("recommended_price")
        elif action == "apply_floor":
            listing.suggested_price = pricing.get("floor_price")
            listing.listing_price = pricing.get("floor_price")
        db.add(listing)
        db.commit()
        results.append({"listing_id": listing.id, "status": action, "pricing_analysis": pricing})
    return {"results": results}


@router.get("/listings/{listing_id}/readiness")
def get_listing_readiness(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    pricing = ((listing.marketplace_data or {}).get("pricing_analysis") or {}) if isinstance(listing.marketplace_data, dict) else {}
    return {
        "listing_id": listing.id,
        "quality_summary": compute_listing_quality_summary(listing, pricing_analysis=pricing),
        "ebay_blockers": validate_marketplace_readiness(listing=listing, marketplace="ebay", pricing_analysis=pricing),
        "facebook_blockers": validate_marketplace_readiness(listing=listing, marketplace="facebook", pricing_analysis=pricing),
        "stale_pricing_days": STALE_PRICING_DAYS,
    }


@router.post("/listings/{listing_id}/optimize")
def optimize_listing(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    try:
        return ListingOptimizerService().optimize_listing(db, listing_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/predictions/{listing_id}")
def get_prediction(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    try:
        return PredictionService().predict_sell_through(db, listing_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/alerts")
def get_alerts(
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"alerts": AlertService().generate_alerts(db, resolve_user_scope(current_user, user_id))}
