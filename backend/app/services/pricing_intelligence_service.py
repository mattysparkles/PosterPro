from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import IntakeNotification, Listing
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
        risk = result.get("underpricing_risk") if isinstance(result.get("underpricing_risk"), dict) else {}
        if risk.get("level") == "SEVERE" and risk.get("evidence_signature"):
            notices = db.query(IntakeNotification).filter(
                IntakeNotification.user_id == listing.user_id,
                IntakeNotification.notification_type == "pricing_underpricing_severe",
            ).order_by(IntakeNotification.id.desc()).limit(50).all()
            already_notified = any(
                isinstance(row.metadata_json, dict)
                and row.metadata_json.get("listing_id") == listing.id
                and row.metadata_json.get("evidence_signature") == risk["evidence_signature"]
                for row in notices
            )
            if not already_notified:
                from app.services.process_notifications import create_process_notification

                create_process_notification(
                    db,
                    user_id=listing.user_id,
                    title=f"Pricing alert: item {listing.id} may be underpriced",
                    message=(
                        f"Current price ${risk.get('current_price')} is substantially below the "
                        f"${risk.get('sold_median')} median of {risk.get('sold_comparable_count')} "
                        "relevant sold comparables. Review the evidence before publishing or changing a live price."
                    ),
                    notification_type="pricing_underpricing_severe",
                    href="/listings",
                    metadata_json={"listing_id": listing.id, "evidence_signature": risk["evidence_signature"], "severity": "SEVERE"},
                )
        db.commit()
        db.refresh(listing)
        return result
