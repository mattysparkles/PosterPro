from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.connectors.registry import get_connector
from app.core.config import settings
from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, Sale, User

logger = logging.getLogger(__name__)


class SaleDetectionService:
    DEFAULT_MARKETPLACES = [
        MarketplaceName.ebay.value,
        MarketplaceName.poshmark.value,
        MarketplaceName.mercari.value,
        MarketplaceName.depop.value,
        MarketplaceName.whatnot.value,
        MarketplaceName.vinted.value,
    ]

    def get_enabled_marketplaces(self, user: User) -> list[str]:
        configured = user.sale_detection_platforms or self.DEFAULT_MARKETPLACES
        return [m for m in configured if m in MarketplaceName._value2member_map_]

    def _parse_sold_at(self, raw_value: str | None) -> datetime:
        if not raw_value:
            return datetime.now(UTC).replace(tzinfo=None)
        try:
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            logger.warning("Invalid sold_at format from marketplace", extra={"sold_at": raw_value})
            return datetime.now(UTC).replace(tzinfo=None)

    def _already_processed(self, db: Session, marketplace: str, order_id: str | None, listing_id: str | None) -> bool:
        if not order_id and not listing_id:
            return False
        filters = [Sale.platform == MarketplaceName(marketplace)]
        if order_id:
            filters.append(Sale.marketplace_order_id == order_id)
        if listing_id:
            filters.append(Sale.marketplace_listing_id == listing_id)
        existing = db.execute(select(Sale.id).where(and_(*filters))).first()
        return existing is not None

    def _find_listing(self, db: Session, user_id: int, event: dict) -> Listing | None:
        listing_key = str(event.get("marketplace_listing_id") or "")
        if listing_key:
            listing = db.execute(
                select(Listing).where(
                    Listing.user_id == user_id,
                    func.coalesce(Listing.ebay_listing_id, "") == listing_key,
                )
            ).scalar_one_or_none()
            if listing:
                return listing
            marketplace_listing = db.execute(
                select(MarketplaceListing)
                .where(MarketplaceListing.marketplace_listing_id == listing_key)
                .order_by(MarketplaceListing.id.desc())
            ).scalar_one_or_none()
            if marketplace_listing:
                return db.get(Listing, marketplace_listing.listing_id)
        return None

    def _record_sale(self, db: Session, user_id: int, listing: Listing | None, event: dict) -> Sale:
        sale = Sale(
            user_id=user_id,
            listing_id=listing.id if listing else None,
            platform=MarketplaceName(event["marketplace"]),
            marketplace_order_id=event.get("marketplace_order_id"),
            marketplace_listing_id=event.get("marketplace_listing_id"),
            quantity=max(1, int(event.get("quantity") or 1)),
            amount=float(event["amount"]) if event.get("amount") is not None else None,
            currency=event.get("currency") or "USD",
            sold_at=self._parse_sold_at(event.get("sold_at")),
            status="DETECTED",
            details=event.get("raw") or event,
        )
        db.add(sale)
        return sale

    async def _fanout_quantity_adjustment(self, db: Session, listing: Listing, sold_platform: str, quantity_sold: int, dry_run: bool) -> dict:
        new_quantity = max(0, int(listing.quantity or 0) - quantity_sold)
        platform_quantities = dict(listing.platform_quantities or {})
        outcomes: dict[str, dict] = {}

        for row in listing.marketplace_listings:
            market = row.marketplace.value
            if market == sold_platform:
                continue

            connector = get_connector(market)
            if new_quantity <= 0:
                action = "delist"
                row.status = MarketplaceListingStatus.DELETED
                if dry_run:
                    response = {"status": "DRY_RUN", "action": action}
                else:
                    response = await connector.delete(listing)
            else:
                action = "quantity_adjust"
                row.status = MarketplaceListingStatus.UPDATED
                platform_quantities[market] = new_quantity
                if dry_run:
                    response = {"status": "DRY_RUN", "action": action, "quantity": new_quantity}
                else:
                    response = await connector.update(listing)

            row.raw_response = {
                **(row.raw_response or {}),
                "sale_detection": {
                    "action": action,
                    "new_quantity": new_quantity,
                    "dry_run": dry_run,
                    "executed_at": datetime.now(UTC).isoformat(),
                    "response": response,
                },
            }
            outcomes[market] = {"action": action, "response": response}
            db.add(row)

        listing.quantity = new_quantity
        listing.platform_quantities = platform_quantities
        db.add(listing)
        return outcomes

    def poll_user_sales(self, db: Session, user: User, *, dry_run: bool = True, lookback_minutes: int = 30) -> dict:
        since = (datetime.now(UTC) - timedelta(minutes=lookback_minutes)).isoformat()
        enabled = self.get_enabled_marketplaces(user)
        logger.info("Starting sale polling", extra={"user_id": user.id, "marketplaces": enabled, "dry_run": dry_run})

        events: list[dict] = []
        for marketplace in enabled:
            connector = get_connector(marketplace)
            try:
                polled = asyncio.run(connector.poll_sales(user.id, since=since))
                logger.info(
                    "Marketplace poll completed",
                    extra={"user_id": user.id, "marketplace": marketplace, "events": len(polled)},
                )
                events.extend(polled)
            except Exception as exc:
                logger.exception(
                    "Marketplace sale polling failed",
                    extra={"user_id": user.id, "marketplace": marketplace, "error": str(exc)},
                )

        detected = 0
        adjusted = 0
        for event in events:
            if event.get("status") == "stub":
                logger.info("Stubbed marketplace sale poll", extra=event)
                continue
            platform = str(event.get("marketplace") or "").lower()
            if platform not in MarketplaceName._value2member_map_:
                continue
            if self._already_processed(db, platform, event.get("marketplace_order_id"), event.get("marketplace_listing_id")):
                continue

            listing = self._find_listing(db, user.id, event)
            sale = self._record_sale(db, user.id, listing, event)
            detected += 1
            logger.info(
                "Sale detected",
                extra={
                    "sale_id": sale.id,
                    "user_id": user.id,
                    "platform": platform,
                    "listing_id": listing.id if listing else None,
                    "order_id": event.get("marketplace_order_id"),
                },
            )

            if listing:
                outcome = asyncio.run(
                    self._fanout_quantity_adjustment(
                        db,
                        listing,
                        sold_platform=platform,
                        quantity_sold=max(1, int(event.get("quantity") or 1)),
                        dry_run=dry_run,
                    )
                )
                adjusted += len(outcome)
                sale.status = "DRY_RUN" if dry_run else "SYNCED"
                sale.details = {**(sale.details or {}), "fanout": outcome}
            else:
                sale.status = "UNMATCHED"

            db.add(sale)

        db.commit()
        return {
            "user_id": user.id,
            "dry_run": dry_run,
            "marketplaces_polled": enabled,
            "events_seen": len(events),
            "sales_detected": detected,
            "adjustments_triggered": adjusted,
        }

    def poll_all_users(self, db: Session, *, dry_run: bool = True, lookback_minutes: int = 30) -> dict:
        users = db.execute(select(User)).scalars().all()
        results = [self.poll_user_sales(db, user, dry_run=dry_run, lookback_minutes=lookback_minutes) for user in users]
        return {"processed_users": len(results), "results": results, "dry_run": dry_run}
