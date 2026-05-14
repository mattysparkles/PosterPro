from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Listing


class PricingIntelligenceService:
    def recommend_price(
        self,
        db: Session,
        listing_id: int,
        external_comparables: list[dict] | None = None,
        estimated_value_override: float | None = None,
    ) -> dict:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")
        return self._recommend_for_listing(
            db,
            listing,
            external_comparables=external_comparables,
            estimated_value_override=estimated_value_override,
        )

    def _recommend_for_listing(
        self,
        db: Session,
        listing: Listing,
        external_comparables: list[dict] | None = None,
        estimated_value_override: float | None = None,
    ) -> dict:
        listing_id = listing.id

        historical = db.execute(
            select(Listing).where(
                Listing.user_id == listing.user_id,
                Listing.category_suggestion == listing.category_suggestion,
                Listing.sale_price.is_not(None),
            )
        ).scalars().all()

        historical_avg = (
            sum(l.sale_price for l in historical if l.sale_price is not None) / len(historical)
            if historical else (estimated_value_override or listing.estimated_value or listing.suggested_price or listing.listing_price or 25.0)
        )
        normalized_external = []
        for row in external_comparables or []:
            try:
                price = float(row.get("price"))
                if price > 0:
                    normalized_external.append(
                        {
                            "title": str(row.get("title") or "Comparable sale").strip(),
                            "price": round(price, 2),
                        }
                    )
            except (TypeError, ValueError, AttributeError):
                continue

        external_market_avg = (
            sum(row["price"] for row in normalized_external) / len(normalized_external)
            if normalized_external else None
        )
        market_comps = external_market_avg or (historical_avg * 1.05)
        listing_age_days = (listing.updated_at - listing.created_at).days if listing.updated_at and listing.created_at else 0
        age_discount = 0.95 if listing_age_days > 30 else 1.0

        recommended = round(((historical_avg * 0.6) + (market_comps * 0.4)) * age_discount, 2)
        current_price = listing.listing_price or listing.suggested_price or recommended
        delta_pct = ((current_price - recommended) / recommended) * 100 if recommended else 0

        confidence = 0.85 if len(historical) >= 5 else 0.62
        if normalized_external:
            confidence = min(0.94, confidence + 0.1)

        comparable_titles = [row["title"] for row in normalized_external[:3]] or [
            l.title or f"Historical sale #{l.id}" for l in historical[:3]
        ]
        reasoning = (
            f"Priced {abs(delta_pct):.1f}% {'above' if delta_pct > 0 else 'below'} blended sold comps; "
            f"{len(historical)} historical category sales and {len(normalized_external)} external sold comps considered."
        )

        return {
            "listing_id": listing_id,
            "recommended_price": recommended,
            "confidence": round(confidence, 2),
            "reasoning": reasoning,
            "current_price": round(current_price, 2),
            "market_avg_sold": round(market_comps, 2),
            "historical_avg_sold": round(historical_avg, 2),
            "external_market_avg_sold": round(external_market_avg, 2) if external_market_avg else None,
            "historical_comparable_count": len(historical),
            "external_comparable_count": len(normalized_external),
            "comparable_titles": comparable_titles,
            "comparables": normalized_external[:5],
            "listing_age_days": listing_age_days,
        }
