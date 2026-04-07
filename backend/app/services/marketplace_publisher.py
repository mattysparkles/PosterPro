from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, User
from app.services.ebay_service import publish_listing_to_ebay

TODO_MESSAGE = "TODO – API keys coming"


@dataclass(slots=True)
class PublishResult:
    marketplace: str
    status: MarketplaceListingStatus
    response: dict[str, Any]


class MarketplacePublisher:
    """Generic publishing service with concrete eBay integration + platform stubs."""

    STUBBED_MARKETPLACES = {
        MarketplaceName.etsy.value,
        MarketplaceName.mercari.value,
        MarketplaceName.facebook.value,
    }

    def publish(self, db: Session, listing: Listing, marketplace: str) -> PublishResult:
        market = marketplace.lower()
        if market == MarketplaceName.ebay.value:
            response = asyncio.run(publish_listing_to_ebay(listing, db))
            return PublishResult(marketplace=market, status=MarketplaceListingStatus.PUBLISHED, response=response)

        if market in self.STUBBED_MARKETPLACES:
            return PublishResult(
                marketplace=market,
                status=MarketplaceListingStatus.FAILED,
                response={"error": TODO_MESSAGE, "status": "TODO"},
            )

        raise ValueError(f"Unsupported marketplace: {market}")


marketplace_publisher = MarketplacePublisher()


def upsert_marketplace_listing(
    db: Session,
    *,
    listing_id: int,
    marketplace: str,
    status: MarketplaceListingStatus,
    response: dict[str, Any] | None,
) -> MarketplaceListing:
    market = MarketplaceName(marketplace.lower())
    row = db.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.listing_id == listing_id,
            MarketplaceListing.marketplace == market,
        )
    ).scalar_one_or_none()

    if not row:
        row = MarketplaceListing(listing_id=listing_id, marketplace=market, status=status)

    row.status = status
    row.raw_response = response
    row.marketplace_listing_id = (
        (response or {}).get("listing_id")
        or (response or {}).get("external_listing_id")
        or row.marketplace_listing_id
    )
    db.add(row)
    return row


def get_enabled_platforms(user: User | None) -> list[str]:
    configured = (user.enabled_platforms if user else None) or [MarketplaceName.ebay.value]
    normalized: list[str] = []
    for name in configured:
        lowered = str(name).lower()
        if lowered in MarketplaceName._value2member_map_ and lowered not in normalized:
            normalized.append(lowered)
    return normalized or [MarketplaceName.ebay.value]
