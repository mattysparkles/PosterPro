from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.alert_service import AlertService
from app.services.analytics_service import AnalyticsService
from app.services.listing_optimizer_service import ListingOptimizerService
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService

router = APIRouter()


@router.get("/analytics/overview")
def analytics_overview(user_id: int = Query(1), db: Session = Depends(get_db)):
    return AnalyticsService().compute_overview(db, user_id)


@router.get("/analytics/listings/{listing_id}")
def analytics_listing(listing_id: int, db: Session = Depends(get_db)):
    try:
        return AnalyticsService().listing_detail(db, listing_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pricing/recommendations/{listing_id}")
def pricing_recommendation(listing_id: int, db: Session = Depends(get_db)):
    try:
        return PricingIntelligenceService().recommend_price(db, listing_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/listings/{listing_id}/optimize")
def optimize_listing(listing_id: int, db: Session = Depends(get_db)):
    try:
        return ListingOptimizerService().optimize_listing(db, listing_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/predictions/{listing_id}")
def get_prediction(listing_id: int, db: Session = Depends(get_db)):
    try:
        return PredictionService().predict_sell_through(db, listing_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/alerts")
def get_alerts(user_id: int = Query(1), db: Session = Depends(get_db)):
    return {"alerts": AlertService().generate_alerts(db, user_id)}
