from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.schemas import PricingApplyRequest, PricingBulkRequest
from app.core.auth import ensure_user_owns_resource, get_current_user, resolve_user_scope
from app.core.database import get_db
from app.models.models import Listing, User
from app.services.alert_service import AlertService
from app.services.analytics_service import AnalyticsService
from app.services.listing_optimizer_service import ListingOptimizerService
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.pricing_research_service import STALE_PRICING_DAYS, compute_listing_quality_summary, validate_marketplace_readiness

router = APIRouter()


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
