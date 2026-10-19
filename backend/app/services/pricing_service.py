from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.models import Listing
from app.services.pricing_research_service import PricingResearchService


class PricingServiceError(RuntimeError):
    """Raised when pricing research cannot be completed safely."""


class PricingService:
    """Expose evidence-backed prices without silently changing a seller ask.

    Older versions filled missing values with category heuristics and an LLM
    estimate, then wrote those numbers into buy-it-now fields. Research is now
    the sole source of recommendations; the operator applies a result through
    the explicit pricing review workflow.
    """

    def __init__(self, model: str | None = None):
        # Kept as a compatibility argument for existing callers. An LLM is not
        # a substitute for market evidence and is deliberately not invoked.
        self.model = model

    def generate_pricing(self, db: Session, listing_id: int) -> dict[str, Any]:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")

        existing_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        existing = existing_data.get("pricing_analysis") if isinstance(existing_data.get("pricing_analysis"), dict) else {}
        if existing.get("recommendation_eligible") is True and not existing.get("stale") and existing.get("recommended_price"):
            research = existing
        else:
            research = PricingResearchService().build_research(
                db,
                listing,
                ignore_unproven_listing_price=True,
            )

        eligible = research.get("recommendation_eligible") is True and bool(research.get("recommended_price"))
        listing.marketplace_data = {
            **existing_data,
            "pricing_analysis": research,
        }
        db.add(listing)
        db.commit()
        db.refresh(listing)

        if not eligible:
            return {
                "listing_id": listing.id,
                "status": "research_required",
                "recommendation_eligible": False,
                "start_price": None,
                "buy_it_now_price": None,
                "min_acceptable_offer": None,
                "blocker": research.get("publication_price_blocker") or "Comparable/source price evidence or a documented operator-entered price is required.",
                "pricing_analysis": research,
            }

        return {
            "listing_id": listing.id,
            "status": "recommendation_ready_for_operator_review",
            "recommendation_eligible": True,
            "start_price": research.get("quick_sale_price"),
            "buy_it_now_price": research.get("recommended_price"),
            "min_acceptable_offer": research.get("floor_price"),
            "pricing_analysis": research,
            "applied": False,
        }

    def get_pricing(self, db: Session, listing_id: int) -> dict[str, Any]:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")
        data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        analysis = data.get("pricing_analysis") if isinstance(data.get("pricing_analysis"), dict) else {}
        eligible = analysis.get("recommendation_eligible") is True
        return {
            "listing_id": listing.id,
            "start_price": analysis.get("quick_sale_price") if eligible else None,
            "buy_it_now_price": analysis.get("recommended_price") if eligible else None,
            "min_acceptable_offer": analysis.get("floor_price") if eligible else None,
            "estimated_value": None,
            "recommendation_eligible": eligible,
            "pricing_source": analysis.get("provenance") or "unresearched",
            "blocker": None if eligible else analysis.get("publication_price_blocker") or "Pricing research is required.",
            "pricing_analysis": analysis,
        }

    def adjust_price_based_on_comps(self, db: Session, listing_id: int) -> dict[str, Any]:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")
        return {
            "listing_id": listing_id,
            "status": "review_required",
            "message": "Pricing evidence can be refreshed, but this operation does not change an asking price. Review and apply a supported recommendation explicitly.",
        }
