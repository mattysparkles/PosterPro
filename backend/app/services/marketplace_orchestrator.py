from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, User
from app.services.marketplace_publisher import get_enabled_platforms
from app.workers.tasks import publish_listing_to_marketplace_task, sync_sold_everywhere_task


def list_marketplaces() -> list[dict]:
    return [
        {"name": MarketplaceName.ebay.value, "supports_oauth": True},
        {"name": MarketplaceName.etsy.value, "supports_oauth": False},
        {"name": MarketplaceName.mercari.value, "supports_oauth": False},
        {"name": MarketplaceName.facebook.value, "supports_oauth": False},
    ]


def queue_publish(db: Session, listing_id: int, marketplaces: list[str] | None) -> list[dict]:
    listing = db.get(Listing, listing_id)
    if not listing:
        raise ValueError("Listing not found")

    user = db.get(User, listing.user_id)
    targets = marketplaces or get_enabled_platforms(user)
    results: list[dict] = []

    for market in targets:
        market_key = market.lower()
        if market_key not in MarketplaceName._value2member_map_:
            results.append({"marketplace": market_key, "status": "UNSUPPORTED", "task_id": None})
            continue

        existing = db.execute(
            select(MarketplaceListing).where(
                MarketplaceListing.listing_id == listing_id,
                MarketplaceListing.marketplace == MarketplaceName(market_key),
            )
        ).scalar_one_or_none()
        if not existing:
            existing = MarketplaceListing(
                listing_id=listing_id,
                marketplace=MarketplaceName(market_key),
                status=MarketplaceListingStatus.PENDING,
            )
            db.add(existing)
            db.flush()

        task = publish_listing_to_marketplace_task.delay(listing_id, market_key)
        results.append({"marketplace": market_key, "status": "QUEUED", "task_id": task.id})

    db.commit()
    return results


def listing_marketplace_status(db: Session, listing_id: int) -> list[MarketplaceListing]:
    return db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing_id)).scalars().all()


def trigger_sync_sold(listing_ids: list[int] | None) -> dict:
    task = sync_sold_everywhere_task.delay(listing_ids or [])
    return {"task_id": task.id, "status": "QUEUED"}
