from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Listing
from app.services.pricing_intelligence_service import PricingIntelligenceService


class AlertService:
    def generate_alerts(self, db: Session, user_id: int) -> list[dict]:
        listings = db.execute(select(Listing).where(Listing.user_id == user_id)).scalars().all()
        alerts = []
        now = datetime.utcnow()

        for listing in listings:
            if listing.sale_price is not None:
                continue

            if listing.created_at < now - timedelta(days=30):
                alerts.append({"type": "stale_listing", "listing_id": listing.id, "message": "Listing is stale (>30 days)."})

            rec = PricingIntelligenceService().recommend_price(db, listing.id)
            current = rec["current_price"]
            recommended = rec["recommended_price"]
            if current > recommended * 1.15:
                alerts.append({"type": "overpriced", "listing_id": listing.id, "message": "Current price is >15% above recommended."})
            if rec["market_avg_sold"] > current * 1.2:
                alerts.append({"type": "high_demand", "listing_id": listing.id, "message": "Market comps imply stronger demand than your current strategy."})
            if rec["market_avg_sold"] < current * 0.8:
                alerts.append({"type": "market_price_drop", "listing_id": listing.id, "message": "Recent market sold average has dropped significantly."})

        return alerts
