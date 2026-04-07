from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import EbayOfferHistory, Listing, MarketplaceAccount
from app.services.ebay_service import accept_best_offer, get_incoming_best_offers, reject_best_offer


@dataclass(slots=True)
class OfferDecision:
    decision: str
    reason: str


class OfferService:
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
