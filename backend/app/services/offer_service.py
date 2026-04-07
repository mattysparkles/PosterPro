from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import AutomatedOfferLog, EbayOfferHistory, Listing, MarketplaceAccount, OfferAutomationRule
from app.services.ebay_service import accept_best_offer, get_incoming_best_offers, reject_best_offer


@dataclass(slots=True)
class OfferDecision:
    decision: str
    reason: str


class OfferService:
    DEFAULT_RULES = {
        "auto_send_to_new_watchers": True,
        "discount_percent": 10.0,
        "minimum_listing_price": 25.0,
        "exclude_listing_ids": [],
        "message_template": "Thanks for watching! Here is a limited-time offer just for you.",
    }

    def evaluate_offer(self, listing: Listing | None, offer: dict[str, Any]) -> OfferDecision:
        offer_amount = self._extract_offer_amount(offer)
        if offer_amount is None:
            return OfferDecision(decision="reject", reason="Unable to parse offer amount")

        if not listing:
            return OfferDecision(decision="reject", reason="No local listing mapped to eBay listing")

        min_acceptable = float(listing.min_acceptable_offer or 0)
        if min_acceptable and offer_amount >= min_acceptable:
            return OfferDecision(decision="accept", reason=f"Offer >= min_acceptable_offer ({min_acceptable:.2f})")

        rules = (listing.marketplace_data or {}).get("offer_auto_rules") or {}
        accept_ratio = rules.get("accept_over_buy_it_now_ratio")
        buy_it_now = float(listing.buy_it_now_price or listing.listing_price or listing.suggested_price or 0)
        if accept_ratio is not None and buy_it_now > 0:
            threshold = buy_it_now * float(accept_ratio)
            if offer_amount >= threshold:
                return OfferDecision(
                    decision="accept",
                    reason=f"Offer >= {float(accept_ratio) * 100:.1f}% of Buy It Now ({threshold:.2f})",
                )

        return OfferDecision(decision="reject", reason="Offer below configured auto-accept thresholds")

    def process_incoming_offers(self, db: Session, account: MarketplaceAccount) -> dict[str, int]:
        offers = asyncio.run(get_incoming_best_offers(account))
        accepted = 0
        rejected = 0
        skipped = 0

        for offer in offers:
            offer_id = str(offer.get("offerId") or "")
            if not offer_id:
                skipped += 1
                continue

            existing = db.execute(select(EbayOfferHistory).where(EbayOfferHistory.ebay_offer_id == offer_id)).scalar_one_or_none()
            if existing:
                skipped += 1
                continue

            ebay_listing_id = str(offer.get("listingId") or offer.get("itemId") or "") or None
            listing = None
            if ebay_listing_id:
                listing = db.execute(select(Listing).where(Listing.ebay_listing_id == ebay_listing_id)).scalar_one_or_none()

            decision = self.evaluate_offer(listing, offer)

            if decision.decision == "accept":
                api_response = asyncio.run(accept_best_offer(account, offer_id))
                accepted += 1
            else:
                api_response = asyncio.run(reject_best_offer(account, offer_id, decision.reason))
                rejected += 1

            history = EbayOfferHistory(
                listing_id=listing.id if listing else None,
                user_id=account.user_id,
                ebay_offer_id=offer_id,
                ebay_listing_id=ebay_listing_id,
                buyer_username=(offer.get("buyer") or {}).get("username"),
                offered_amount=self._extract_offer_amount(offer),
                currency=self._extract_currency(offer),
                offer_status=str(offer.get("status") or "PENDING"),
                decision=decision.decision,
                decision_reason=decision.reason,
                raw_payload={"offer": offer, "decision_api_response": api_response},
                decided_at=datetime.now(UTC).replace(tzinfo=None),
            )
            db.add(history)

        db.commit()
        return {"checked": len(offers), "accepted": accepted, "rejected": rejected, "skipped": skipped}

    def get_or_create_rule(self, db: Session, user_id: int) -> OfferAutomationRule:
        rule = db.execute(select(OfferAutomationRule).where(OfferAutomationRule.user_id == user_id)).scalar_one_or_none()
        if rule:
            return rule
        rule = OfferAutomationRule(user_id=user_id, is_enabled=False, rules=self.DEFAULT_RULES)
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule

    def update_rules(self, db: Session, user_id: int, is_enabled: bool, rules: dict[str, Any]) -> OfferAutomationRule:
        record = self.get_or_create_rule(db, user_id)
        merged_rules = {**self.DEFAULT_RULES, **(record.rules or {}), **(rules or {})}
        merged_rules["exclude_listing_ids"] = [int(item) for item in (merged_rules.get("exclude_listing_ids") or [])]
        record.is_enabled = bool(is_enabled)
        record.rules = merged_rules
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    def send_personalized_offers(self, db: Session, account: MarketplaceAccount, force: bool = False) -> dict[str, int]:
        rule_record = self.get_or_create_rule(db, account.user_id)
        if not rule_record.is_enabled and not force:
            return {"candidates": 0, "sent": 0, "skipped": 0, "disabled": 1}

        rules = {**self.DEFAULT_RULES, **(rule_record.rules or {})}
        exclude_listing_ids = {int(item) for item in (rules.get("exclude_listing_ids") or [])}
        discount_percent = max(1.0, min(float(rules.get("discount_percent") or 10.0), 80.0))
        minimum_listing_price = max(0.0, float(rules.get("minimum_listing_price") or 0.0))
        auto_watchers_only = bool(rules.get("auto_send_to_new_watchers", True))

        listings = db.execute(select(Listing).where(Listing.user_id == account.user_id)).scalars().all()
        sent = 0
        skipped = 0
        candidates = 0
        for listing in listings:
            if listing.id in exclude_listing_ids:
                skipped += 1
                continue

            listing_price = float(listing.buy_it_now_price or listing.listing_price or listing.suggested_price or 0.0)
            if listing_price < minimum_listing_price:
                skipped += 1
                continue

            watcher_data = (listing.marketplace_data or {}).get("watchers") or {}
            watchers = int(watcher_data.get("count") or 0)
            new_watchers = int(watcher_data.get("new_since_last_run") or watchers)
            if auto_watchers_only and new_watchers <= 0:
                skipped += 1
                continue

            candidates += 1
            offer_price = round(listing_price * (1 - discount_percent / 100), 2)
            payload = {
                "listing_id": listing.id,
                "ebay_listing_id": listing.ebay_listing_id,
                "watchers": watchers,
                "new_watchers": new_watchers,
                "discount_percent": discount_percent,
                "offer_price": offer_price,
                "message": rules.get("message_template"),
            }
            db.add(
                AutomatedOfferLog(
                    user_id=account.user_id,
                    listing_id=listing.id,
                    platform=account.marketplace.value,
                    watcher_count=watchers,
                    offer_percent=discount_percent,
                    offer_price=offer_price,
                    status="SENT",
                    details=payload,
                    sent_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            updated_marketplace_data = {**(listing.marketplace_data or {})}
            updated_marketplace_data["watchers"] = {**watcher_data, "new_since_last_run": 0}
            updated_marketplace_data["last_auto_offer"] = payload
            listing.marketplace_data = updated_marketplace_data
            db.add(listing)
            sent += 1

        db.commit()
        return {"candidates": candidates, "sent": sent, "skipped": skipped, "disabled": 0}

    @staticmethod
    def _extract_offer_amount(offer: dict[str, Any]) -> float | None:
        amount = (
            (offer.get("price") or {}).get("value")
            or (offer.get("offeredAmount") or {}).get("value")
            or offer.get("amount")
        )
        try:
            return float(amount) if amount is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_currency(offer: dict[str, Any]) -> str | None:
        return (
            (offer.get("price") or {}).get("currency")
            or (offer.get("offeredAmount") or {}).get("currency")
            or offer.get("currency")
        )
