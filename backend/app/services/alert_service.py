from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import IntakePhoto, IntakePhotoBatch, Listing, MarketplaceListing, User


class AlertService:
    def generate_alerts(self, db: Session, user_id: int) -> list[dict]:
        # Alerts are a read-only operator aid.  They must not trigger a
        # full-catalog pricing recalculation (which used to commit once per
        # listing and could exhaust the web process connection pool).
        active_projection_rows = db.execute(
            select(MarketplaceListing.listing_id, MarketplaceListing.marketplace, MarketplaceListing.marketplace_listing_id)
            .join(Listing, Listing.id == MarketplaceListing.listing_id)
            .where(
                Listing.user_id == user_id,
                MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
                MarketplaceListing.marketplace_listing_id.is_not(None),
                func.trim(MarketplaceListing.marketplace_listing_id) != "",
            )
        ).all()
        live_listing_ids = {
            int(listing_id) for listing_id, market, _external_id in active_projection_rows
            if getattr(market, "value", market) != MarketplaceName.ebay.value
        }
        # eBay's old local PUBLISHED marker is not proof of a live listing. Use
        # only the fresh, read-only remote identity snapshot if available.
        try:
            from app.services.active_listing_snapshot import cached_ebay_active_ids

            remote_ebay_ids = cached_ebay_active_ids(db, user_id)
        except Exception:
            remote_ebay_ids = None
        if remote_ebay_ids:
            live_listing_ids.update(
                listing.id for listing in db.execute(select(Listing).where(
                    Listing.user_id == user_id,
                    Listing.ebay_listing_id.in_(remote_ebay_ids),
                )).scalars().all()
            )

        stale_cutoff = datetime.utcnow() - timedelta(days=30)
        stale_count = 0
        if live_listing_ids:
            stale_count = int(db.execute(select(func.count(Listing.id)).where(
                Listing.user_id == user_id,
                Listing.id.in_(live_listing_ids),
                Listing.sale_price.is_(None),
                Listing.created_at < stale_cutoff,
            )).scalar_one() or 0)

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

        if stale_count:
            alerts.append({
                "type": "stale_listing",
                "title": "Older active listings",
                "count": stale_count,
                "message": f"{stale_count} confirmed active listing(s) have been live for more than 30 days.",
                "href": "/inventory?stale=true",
            })
        return alerts
