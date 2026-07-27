from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import IntakePhoto, IntakePhotoBatch, Listing, User


class AlertService:
    def generate_alerts(self, db: Session, user_id: int) -> list[dict]:
        # Alerts are a read-only operator aid.  They must not trigger a
        # full-catalog pricing recalculation (which used to commit once per
        # listing and could exhaust the web process connection pool).
        listings = db.execute(
            select(Listing)
            .where(Listing.user_id == user_id)
            .order_by(Listing.updated_at.desc())
            .limit(250)
        ).scalars().all()
        user = db.get(User, user_id)
        alerts = []
        now = datetime.utcnow()

        for listing in listings:
            if listing.sale_price is not None:
                continue

            if listing.created_at < now - timedelta(days=30):
                alerts.append({"type": "stale_listing", "listing_id": listing.id, "message": "Listing is stale (>30 days)."})

            rec = (listing.marketplace_data or {}).get("pricing_analysis") or {}
            current = rec.get("current_price")
            recommended = rec.get("recommended_price")
            market_avg_sold = rec.get("market_avg_sold")

            if current is None or recommended is None:
                continue

            if current > recommended * 1.15:
                alerts.append({"type": "overpriced", "listing_id": listing.id, "message": "Current price is >15% above recommended."})
            if market_avg_sold is not None and market_avg_sold > current * 1.2:
                alerts.append({"type": "high_demand", "listing_id": listing.id, "message": "Market comps imply stronger demand than your current strategy."})
            if market_avg_sold is not None and market_avg_sold < current * 0.8:
                alerts.append({"type": "market_price_drop", "listing_id": listing.id, "message": "Recent market sold average has dropped significantly."})

        intake_settings = {}
        if user and isinstance(user.settings_json, dict):
            intake_settings = (user.settings_json or {}).get("intake_settings") or {}
            if not isinstance(intake_settings, dict):
                intake_settings = {}
        if intake_settings.get("enabled") and (intake_settings.get("album_url") or intake_settings.get("folder_id")):
            unassigned_count = db.execute(
                select(IntakePhoto.id).where(
                    IntakePhoto.user_id == user_id,
                    IntakePhoto.batch_id.is_(None),
                    IntakePhoto.is_slate.is_(False),
                )
            ).scalars().all()
            if unassigned_count:
                alerts.append(
                    {
                        "type": "intake_unassigned_photos",
                        "title": "Intake photos need grouping",
                        "message": f"{len(unassigned_count)} intake photo(s) imported without a matched head slate boundary.",
                        "href": "/intake/queue",
                    }
                )
            ready_batches = db.execute(
                select(IntakePhotoBatch).where(
                    IntakePhotoBatch.user_id == user_id,
                    IntakePhotoBatch.status.in_(("ready_for_draft", "drafted")),
                )
            ).scalars().all()
            review_batches = [batch for batch in ready_batches if batch.draft_listing_id]
            if review_batches:
                alerts.append(
                    {
                        "type": "intake_review_ready",
                        "title": "New intake drafts need review",
                        "message": f"{len(review_batches)} intake draft batch(es) are ready in the listings review queue.",
                        "href": "/listings?tab=review",
                    }
                )
            undrafted_ready = [batch for batch in ready_batches if not batch.draft_listing_id]
            if undrafted_ready:
                alerts.append(
                    {
                        "type": "intake_batches_ready",
                        "title": "Intake batches ready for draft generation",
                        "message": f"{len(undrafted_ready)} intake batch(es) have photos and slate data but still need draft generation.",
                        "href": "/intake/queue",
                    }
                )

        return alerts
