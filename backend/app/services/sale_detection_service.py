from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
import re

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.connectors.registry import get_connector
from app.core.config import settings
from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.enums import ListingStatus
from app.models.models import Listing, MarketplaceListing, Sale, User
from app.services.media_lifecycle import purge_listing_media
from app.services.marketplace_extension_jobs import MarketplaceExtensionJobError, queue_extension_marketplace_action
from app.services.profit_service import ProfitService
from app.services.rate_limiter import rate_limiter
from app.services.process_notifications import create_process_notification

logger = logging.getLogger(__name__)


def _normalize_match_text(value: str | None) -> str:
    raw = str(value or "").lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", raw)
    return " ".join(cleaned.split())


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

    def _already_processed(self, db: Session, user_id: int, marketplace: str, order_id: str | None, listing_id: str | None) -> bool:
        if not order_id and not listing_id:
            return False
        filters = [Sale.user_id == user_id, Sale.platform == MarketplaceName(marketplace)]
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
                .join(Listing, Listing.id == MarketplaceListing.listing_id)
                .where(
                    Listing.user_id == user_id,
                    MarketplaceListing.marketplace_listing_id == listing_key,
                )
                .order_by(MarketplaceListing.id.desc())
            ).scalar_one_or_none()
            if marketplace_listing:
                return db.get(Listing, marketplace_listing.listing_id)

        raw_payload = event.get("raw") if isinstance(event.get("raw"), dict) else {}
        item_payload = raw_payload.get("item") if isinstance(raw_payload.get("item"), dict) else {}
        order_payload = raw_payload.get("order") if isinstance(raw_payload.get("order"), dict) else {}
        line_items = order_payload.get("lineItems") if isinstance(order_payload.get("lineItems"), list) else []
        first_line_item = line_items[0] if line_items and isinstance(line_items[0], dict) else {}
        title = (
            item_payload.get("title")
            or first_line_item.get("title")
            or event.get("title")
        )
        normalized_title = _normalize_match_text(title)
        amount = event.get("amount")
        try:
            target_amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            target_amount = None
        if not normalized_title:
            return None

        candidates = db.execute(select(Listing).where(Listing.user_id == user_id)).scalars().all()
        exact_matches: list[Listing] = []
        for candidate in candidates:
            if candidate.sold_at or int(candidate.quantity or 0) <= 0:
                continue
            candidate_title = _normalize_match_text(candidate.title)
            if not candidate_title or candidate_title != normalized_title:
                continue
            if target_amount is not None:
                candidate_amount = candidate.listing_price or candidate.suggested_price or candidate.estimated_value
                if candidate_amount is not None and abs(float(candidate_amount) - target_amount) > 25:
                    continue
            exact_matches.append(candidate)
        if len(exact_matches) == 1:
            return exact_matches[0]
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
            fees_actual=(listing.fees_actual if listing else None),
            shipping_cost=(listing.shipping_cost if listing else None),
            promotional_fees=float((listing.marketplace_data or {}).get("promotional_fees") or 0) if listing else None,
            marketplace_fees=(ProfitService().estimate_fees_by_marketplace(listing, event["marketplace"]) if listing else None),
            profit=(listing.profit if listing else None),
            roi_percentage=(listing.roi_percentage if listing else None),
            sold_at=self._parse_sold_at(event.get("sold_at")),
            status="DETECTED",
            details=event.get("raw") or event,
        )
        db.add(sale)
        return sale

    def _sold_sync_preferences(self, user: User) -> dict:
        raw = (user.settings_json or {}).get("sold_sync_preferences")
        stored = raw if isinstance(raw, dict) else {}
        return {
            "sold_out_delist_everywhere": bool(stored.get("sold_out_delist_everywhere", True)),
            "out_of_stock_delist_everywhere": bool(stored.get("out_of_stock_delist_everywhere", False)),
            "remove_media_on_sold_out": bool(stored.get("remove_media_on_sold_out", False)),
        }

    async def _fanout_quantity_adjustment(
        self,
        db: Session,
        listing: Listing,
        user: User,
        sold_platform: str,
        quantity_sold: int,
        dry_run: bool,
        sale_amount: float | None = None,
    ) -> dict:
        new_quantity = max(0, int(listing.quantity or 0) - quantity_sold)
        platform_quantities = dict(listing.platform_quantities or {})
        outcomes: dict[str, dict] = {}
        prefs = self._sold_sync_preferences(user)
        sold_out = new_quantity <= 0
        target_markets = {row.marketplace.value for row in listing.marketplace_listings}

        for row in listing.marketplace_listings:
            market = row.marketplace.value
            if market == sold_platform:
                action = "sold_on_marketplace" if sold_out else "quantity_adjust"
                response = {"status": "DRY_RUN", "action": action, "quantity": new_quantity} if dry_run else {"status": "RECORDED_SOLD_SOURCE" if sold_out else "UPDATED_SOURCE", "action": action, "quantity": new_quantity}
                if not dry_run:
                    row.status = MarketplaceListingStatus.SOLD if sold_out else MarketplaceListingStatus.UPDATED
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
                    db.add(row)
                outcomes[market] = {"action": action, "response": response}
                continue

            if row.status in {MarketplaceListingStatus.SOLD, MarketplaceListingStatus.DELETED, MarketplaceListingStatus.CLOSED}:
                outcomes[market] = {"action": "already_inactive", "response": {"status": row.status.value}}
                continue

            action = "delist" if sold_out and prefs["sold_out_delist_everywhere"] else "quantity_adjust"
            if dry_run:
                response = {"status": "DRY_RUN", "action": action, "quantity": new_quantity}
            else:
                connector = get_connector(market)
                rate_limiter.acquire(market)
                if action == "delist":
                    capabilities = connector.get_capabilities()
                    if capabilities.get("supports_extension_end"):
                        try:
                            extension_job, created = queue_extension_marketplace_action(
                                db,
                                user_id=int(listing.user_id),
                                listing=listing,
                                marketplace=market,
                                action="END",
                                priority=0,
                            )
                            response = {
                                "status": "QUEUED_FOR_OPERATOR_REVIEW",
                                "action": "END",
                                "extension_job_id": extension_job.id,
                                "deduplicated": not created,
                                "external_listing_id": extension_job.external_listing_id,
                                "external_url": extension_job.external_url,
                            }
                        except MarketplaceExtensionJobError as exc:
                            response = {"status": "NOT_ENQUEUED", "error_code": exc.code, "error": str(exc)}
                    else:
                        response = {"status": "UNSUPPORTED_ACTION", "action": "END", "marketplace": market}
                else:
                    response = {"status": "UNSUPPORTED_ACTION", "action": "UPDATE_QUANTITY", "marketplace": market}
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
                db.add(row)
            platform_quantities[market] = new_quantity
            outcomes[market] = {"action": action, "response": response}

        if not dry_run:
            listing.quantity = new_quantity
            for market in target_markets:
                platform_quantities[market] = new_quantity
            listing.platform_quantities = platform_quantities
            if sold_out:
                listing.sold_at = datetime.now(UTC).replace(tzinfo=None)
                if sale_amount is not None:
                    listing.sale_price = float(sale_amount)
                if listing.status in {ListingStatus.ready, ListingStatus.draft, ListingStatus.posted}:
                    listing.status = ListingStatus.posted
                current_labels = {str(label).strip() for label in (listing.custom_labels or []) if str(label).strip()}
                current_labels.add("archived_sold")
                listing.custom_labels = sorted(current_labels)
                archived_state = dict(listing.marketplace_data or {})
                archived_state["archive_state"] = {
                    "status": "sold",
                    "sold_platform": sold_platform,
                    "sold_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                    "quantity_sold": quantity_sold,
                    "dry_run": dry_run,
                }
                archived_state["sale_reconciliation"] = {
                    "sold_platform": sold_platform,
                    "quantity_sold": max(1, int(quantity_sold or 1)),
                    "new_quantity": new_quantity,
                    "dry_run": dry_run,
                    "adjusted_marketplaces": sorted(outcomes.keys()),
                    "executed_at": datetime.now(UTC).isoformat(),
                }
                listing.marketplace_data = archived_state
            if sold_out and prefs["remove_media_on_sold_out"]:
                cleanup = purge_listing_media(db, listing, clear_references=True)
                outcomes["media_cleanup"] = cleanup
            db.add(listing)
        return outcomes

    def poll_user_sales(self, db: Session, user: User, *, dry_run: bool = True, lookback_minutes: int = 30) -> dict:
        since = (datetime.now(UTC) - timedelta(minutes=lookback_minutes)).isoformat()
        requested = self.get_enabled_marketplaces(user)
        connectors = {marketplace: get_connector(marketplace) for marketplace in requested}
        enabled = [
            marketplace for marketplace in requested
            if connectors[marketplace].get_capabilities().get("supports_sale_polling", False)
        ]
        skipped = [marketplace for marketplace in requested if marketplace not in enabled]
        logger.info("Starting sale polling", extra={"user_id": user.id, "marketplaces": enabled, "skipped": skipped, "dry_run": dry_run})

        events: list[dict] = []
        for marketplace in enabled:
            connector = connectors[marketplace]
            try:
                rate_limiter.acquire(marketplace)
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
        seen_event_keys: set[tuple[str, str, str]] = set()
        for event in events:
            if event.get("status") == "stub":
                logger.info(
                    "Stubbed marketplace sale poll",
                    extra={"user_id": user.id, "event_payload": event},
                )
                continue
            platform = str(event.get("marketplace") or "").lower()
            if platform not in MarketplaceName._value2member_map_:
                continue
            event_key = (
                platform,
                str(event.get("marketplace_order_id") or "").strip(),
                str(event.get("marketplace_listing_id") or "").strip(),
            )
            if event_key in seen_event_keys:
                continue
            seen_event_keys.add(event_key)
            if self._already_processed(db, user.id, platform, event.get("marketplace_order_id"), event.get("marketplace_listing_id")):
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
                        user,
                        sold_platform=platform,
                        quantity_sold=max(1, int(event.get("quantity") or 1)),
                        dry_run=dry_run,
                        sale_amount=float(event["amount"]) if event.get("amount") is not None else None,
                    )
                )
                adjusted += len(outcome)
                sale.status = "DRY_RUN" if dry_run else "SYNCED"
                sale.details = {**(sale.details or {}), "fanout": outcome}
                if not dry_run:
                    create_process_notification(
                        db,
                        user_id=user.id,
                        title="SOLD - NEEDS SHIPPING",
                        message=f"{listing.title or 'Listing'} sold on {platform} for ${event.get('amount') or '—'}. Order {event.get('marketplace_order_id') or 'unknown'}.",
                        notification_type="sale_needs_shipping",
                        href=f"/sales?sale={sale.id}",
                        metadata_json={"sale_id": sale.id, "order_id": event.get("marketplace_order_id"), "marketplace": platform, "shipping_state": "NEEDS_SHIPPING"},
                    )
            else:
                sale.status = "UNMATCHED"

            db.add(sale)

        db.commit()
        return {
            "user_id": user.id,
            "dry_run": dry_run,
            "marketplaces_requested": requested,
            "marketplaces_polled": enabled,
            "marketplaces_skipped": skipped,
            "events_seen": len(events),
            "sales_detected": detected,
            "adjustments_triggered": adjusted,
        }

    def reconcile_unmatched_sale(
        self,
        db: Session,
        user: User,
        sale: Sale,
        *,
        listing_id: int | None = None,
        dry_run: bool = False,
    ) -> dict:
        if sale.user_id != user.id:
            raise ValueError("Sale does not belong to the supplied user.")
        if sale.listing_id:
            return {"status": "already_matched", "listing_id": sale.listing_id}

        listing = db.get(Listing, listing_id) if listing_id else None
        if listing_id and (not listing or listing.user_id != user.id):
            raise ValueError("Listing not found for reconciliation.")

        event = {
            "marketplace": sale.platform.value,
            "marketplace_order_id": sale.marketplace_order_id,
            "marketplace_listing_id": sale.marketplace_listing_id,
            "quantity": sale.quantity,
            "amount": sale.amount,
            "currency": sale.currency,
            "sold_at": sale.sold_at.isoformat() if sale.sold_at else None,
            "raw": sale.details or {},
            "title": ((sale.details or {}).get("item") or {}).get("title"),
        }
        if not listing:
            listing = self._find_listing(db, user.id, event)
        if not listing:
            return {"status": "unmatched"}

        outcome = asyncio.run(
            self._fanout_quantity_adjustment(
                db,
                listing,
                user,
                sold_platform=sale.platform.value,
                quantity_sold=max(1, int(sale.quantity or 1)),
                dry_run=dry_run,
                sale_amount=sale.amount,
            )
        )
        sale.listing_id = listing.id
        sale.status = "DRY_RUN" if dry_run else "SYNCED"
        sale.details = {**(sale.details or {}), "reconciled": {"listing_id": listing.id, "fanout": outcome, "dry_run": dry_run}}
        db.add(sale)
        db.commit()
        return {"status": sale.status, "listing_id": listing.id, "fanout": outcome}

    def poll_all_users(self, db: Session, *, dry_run: bool = True, lookback_minutes: int = 30) -> dict:
        users = db.execute(select(User)).scalars().all()
        results = [self.poll_user_sales(db, user, dry_run=dry_run, lookback_minutes=lookback_minutes) for user in users]
        return {"processed_users": len(results), "results": results, "dry_run": dry_run}
