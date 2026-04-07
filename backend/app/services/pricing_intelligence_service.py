from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Listing


class PricingIntelligenceService:
    def recommend_price(self, db: Session, listing_id: int) -> dict:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")

        historical = db.execute(
            select(Listing).where(
                Listing.user_id == listing.user_id,
                Listing.category_suggestion == listing.category_suggestion,
                Listing.sale_price.is_not(None),
            )
        ).scalars().all()

        historical_avg = (
            sum(l.sale_price for l in historical if l.sale_price is not None) / len(historical)
            if historical else (listing.suggested_price or listing.listing_price or 25.0)
        )

        market_comps = historical_avg * 1.05  # placeholder for eBay sold comps feed
        listing_age_days = (listing.updated_at - listing.created_at).days if listing.updated_at and listing.created_at else 0
        age_discount = 0.95 if listing_age_days > 30 else 1.0

        recommended = round(((historical_avg * 0.6) + (market_comps * 0.4)) * age_discount, 2)
        current_price = listing.listing_price or listing.suggested_price or recommended
        delta_pct = ((current_price - recommended) / recommended) * 100 if recommended else 0

        confidence = 0.85 if len(historical) >= 5 else 0.62
        reasoning = (
            f"Priced {abs(delta_pct):.1f}% {'above' if delta_pct > 0 else 'below'} blended sold comps; "
            f"{len(historical)} historical category sales considered."
        )

        return {
            "listing_id": listing_id,
            "recommended_price": recommended,
            "confidence": round(confidence, 2),
            "reasoning": reasoning,
            "current_price": round(current_price, 2),
            "market_avg_sold": round(market_comps, 2),
            "historical_avg_sold": round(historical_avg, 2),
            "listing_age_days": listing_age_days,
        }
