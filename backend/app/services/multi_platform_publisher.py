from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, User
from app.services.marketplace_setup import MANUAL_WORKFLOW_READY, load_manual_marketplace_settings
from app.services.ebay_service import publish_listing_to_ebay

ASSISTED_WORKFLOW_MESSAGE = "Direct API publishing is not available for this marketplace in this deployment. Use the assisted marketplace workflow instead."


@dataclass(slots=True)
class PublishResult:
    marketplace: str
    status: MarketplaceListingStatus
    response: dict[str, Any]


class PlatformPublisher(ABC):
    marketplace: str

    @abstractmethod
    def publish(self, db: Session, listing: Listing) -> PublishResult:
        raise NotImplementedError


class EbayPublisher(PlatformPublisher):
    marketplace = MarketplaceName.ebay.value

    def publish(self, db: Session, listing: Listing) -> PublishResult:
        response = asyncio.run(publish_listing_to_ebay(listing, db))
        return PublishResult(
            marketplace=self.marketplace,
            status=MarketplaceListingStatus.PUBLISHED,
            response=response,
        )


class StubPlatformPublisher(PlatformPublisher):
    def __init__(self, marketplace: str):
        self.marketplace = marketplace

    def publish(self, db: Session, listing: Listing) -> PublishResult:  # noqa: ARG002
        return PublishResult(
            marketplace=self.marketplace,
            status=MarketplaceListingStatus.FAILED,
            response={"error": ASSISTED_WORKFLOW_MESSAGE, "status": "ASSISTED_WORKFLOW_REQUIRED"},
        )


class MultiPlatformPublisher:
    """Robust marketplace publisher with concrete eBay implementation + safe stubs."""

    def __init__(self) -> None:
        self._publishers: dict[str, PlatformPublisher] = {
            MarketplaceName.ebay.value: EbayPublisher(),
            MarketplaceName.etsy.value: StubPlatformPublisher(MarketplaceName.etsy.value),
            MarketplaceName.poshmark.value: StubPlatformPublisher(MarketplaceName.poshmark.value),
            MarketplaceName.mercari.value: StubPlatformPublisher(MarketplaceName.mercari.value),
            MarketplaceName.depop.value: StubPlatformPublisher(MarketplaceName.depop.value),
            MarketplaceName.whatnot.value: StubPlatformPublisher(MarketplaceName.whatnot.value),
            MarketplaceName.facebook.value: StubPlatformPublisher(MarketplaceName.facebook.value),
            MarketplaceName.vinted.value: StubPlatformPublisher(MarketplaceName.vinted.value),
        }

    def publish(self, db: Session, listing: Listing, marketplace: str) -> PublishResult:
        market = marketplace.lower()
        publisher = self._publishers.get(market)
        if not publisher:
            raise ValueError(f"Unsupported marketplace: {market}")
        return publisher.publish(db, listing)


multi_platform_publisher = MultiPlatformPublisher()


def upsert_marketplace_listing(
    db: Session,
    *,
    listing_id: int,
    marketplace: str,
    status: MarketplaceListingStatus,
    response: dict[str, Any] | None,
) -> MarketplaceListing:
    market = MarketplaceName(marketplace.lower())
    row = (
        db.execute(
            select(MarketplaceListing)
            .where(
                MarketplaceListing.listing_id == listing_id,
                MarketplaceListing.marketplace == market,
            )
            .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        )
        .scalars()
        .first()
    )

    if not row:
        row = MarketplaceListing(listing_id=listing_id, marketplace=market, status=status)

    row.status = status
    row.raw_response = response
    row.marketplace_listing_id = (
        (response or {}).get("listing_id")
        or (response or {}).get("marketplace_listing_id")
        or (response or {}).get("external_listing_id")
        or row.marketplace_listing_id
    )
    db.add(row)
    return row


def get_enabled_platforms(user: User | None) -> list[str]:
    configured = list((user.enabled_platforms if user else None) or [])
    normalized: list[str] = []
    for name in configured:
        lowered = str(name).lower()
        if lowered in MarketplaceName._value2member_map_ and lowered not in normalized:
            normalized.append(lowered)

    if normalized:
        return normalized

    inferred: list[str] = [MarketplaceName.ebay.value]
    if not user:
        return inferred

    manual_settings = load_manual_marketplace_settings(user)
    for marketplace, settings in manual_settings.items():
        if marketplace == MarketplaceName.ebay.value:
            continue
        if str(settings.get("workflow_state") or "").strip().lower() != MANUAL_WORKFLOW_READY:
            continue
        if not (
            str(settings.get("bridge_account_key") or "").strip()
            or str(settings.get("account_handle") or "").strip()
            or str(settings.get("display_name") or "").strip()
        ):
            continue
        if marketplace not in inferred:
            inferred.append(marketplace)

    return inferred
