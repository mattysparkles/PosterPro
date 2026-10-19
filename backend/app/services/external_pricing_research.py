"""Shared, read-only external pricing research persistence.

This service records provider evidence and an analysis only. It never changes a
listing's customer-facing price or readiness state.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.models import Listing
from app.services.ebay_comparable_research import search_ebay_active_comparables
from app.services.pricing_intelligence_service import PricingIntelligenceService


async def refresh_listing_external_pricing(db: Session, listing: Listing) -> dict[str, Any]:
    research = await search_ebay_active_comparables(listing)
    analysis = PricingIntelligenceService().recommend_price(
        db, int(listing.id), external_comparables=research.get("comparables") or [],
    )
    listing = db.get(Listing, listing.id) or listing
    marketplace_data = dict(listing.marketplace_data or {})
    saved = dict(marketplace_data.get("pricing_analysis") or analysis)
    saved.update({
        "external_research_status": research.get("status"),
        "external_research_source": research.get("source"),
        "external_research_query": research.get("query"),
        "external_research_date": research.get("researched_at"),
        "external_active_asking_count": research.get("active_asking_count", 0),
        "external_sold_comparable_count": research.get("sold_comparable_count", 0),
        "external_research_warning": research.get("message"),
    })
    marketplace_data["pricing_analysis"] = saved
    listing.marketplace_data = marketplace_data
    db.add(listing)
    db.commit()
    return saved
