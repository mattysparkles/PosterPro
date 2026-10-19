from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import Listing, ListingPrediction
from app.services.pricing_intelligence_service import PricingIntelligenceService


class PredictionService:
    def predict_sell_through(self, db: Session, listing_id: int) -> dict:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")

        pricing = PricingIntelligenceService().recommend_price(db, listing_id)
        current = pricing["current_price"] or 1
        market = pricing["market_avg_sold"] or current
        price_competitiveness = max(min((market / max(current, 1)), 1.4), 0.6)

        keyword_score = min(len(listing.tags or []), 8) / 8
        condition_score = 1.0 if (listing.condition or "").lower() in {"new", "like new"} else 0.75
        photo_score = (listing.photo_quality_score or 0.7)

        base = (0.35 * price_competitiveness) + (0.25 * keyword_score) + (0.2 * condition_score) + (0.2 * photo_score)
        probability_7d = round(max(min(base - 0.2, 0.95), 0.05), 3)
        probability_30d = round(max(min(base + 0.15, 0.99), probability_7d), 3)

        row = db.query(ListingPrediction).filter(ListingPrediction.listing_id == listing_id).one_or_none()
        if not row:
            row = ListingPrediction(listing_id=listing_id)
            db.add(row)

        row.probability_sale_7d = probability_7d
        row.probability_sale_30d = probability_30d
        row.features = {
            "price_competitiveness": round(price_competitiveness, 3),
            "keyword_score": round(keyword_score, 3),
            "condition_score": round(condition_score, 3),
            "photo_quality_score": round(photo_score, 3),
        }
        db.commit()
        db.refresh(row)

        return {
            "listing_id": listing_id,
            "probability_sale_7d": probability_7d,
            "probability_sale_30d": probability_30d,
            "features": row.features,
            "model_version": row.model_version,
        }
