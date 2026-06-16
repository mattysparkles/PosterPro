from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import Listing
from app.services.pricing_research_service import PricingResearchService


class PricingIntelligenceService:
    def __init__(self) -> None:
        self.research = PricingResearchService()

    def recommend_price(
        self,
        db: Session,
        listing_id: int,
        external_comparables: list[dict] | None = None,
        estimated_value_override: float | None = None,
        preserve_manual_override: bool = True,
    ) -> dict:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")
        result = self.research.build_research(
            db,
            listing,
            external_comparables=external_comparables,
            estimated_value_override=estimated_value_override,
            preserve_manual_override=preserve_manual_override,
        )
        marketplace_data = dict(listing.marketplace_data or {})
        marketplace_data["pricing_analysis"] = result
        listing.marketplace_data = marketplace_data
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return result
