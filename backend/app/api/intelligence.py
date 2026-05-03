from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import ensure_user_owns_resource, get_current_user, resolve_user_scope
from app.core.database import get_db
from app.models.models import Listing, User
from app.services.alert_service import AlertService
from app.services.analytics_service import AnalyticsService
from app.services.listing_optimizer_service import ListingOptimizerService
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService

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
