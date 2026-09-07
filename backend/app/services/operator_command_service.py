from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, User
from app.services.ebay_service import revise_ebay_listing

LIVE_EBAY_REPRICE_CONFIRMATION_PHRASE = "APPLY LIVE EBAY PRICE CHANGES"

_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
}


@dataclass
class ParsedOperatorCommand:
    command_type: str
    marketplace: str
    percent: float
    minimum_age_days: int | None = None
    minimum_price: float | None = None
    price_filter_mode: str = "all"


class OperatorCommandService:
    def _build_bulk_action_signature(self, parsed: ParsedOperatorCommand) -> str:
        minimum_age = "none" if parsed.minimum_age_days is None else str(int(parsed.minimum_age_days))
        minimum_price = "none" if parsed.minimum_price is None else f"{float(parsed.minimum_price):.2f}"
        return f"{parsed.command_type}:{parsed.marketplace}:{parsed.price_filter_mode}:{float(parsed.percent):.4f}:{minimum_age}:{minimum_price}"

    def _listing_has_bulk_action(self, listing: Listing, signature: str) -> bool:
        marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        operator_actions = marketplace_data.get("operator_bulk_actions") if isinstance(marketplace_data.get("operator_bulk_actions"), dict) else {}
        applied_actions = operator_actions.get("applied") if isinstance(operator_actions.get("applied"), list) else []
        return any(
            isinstance(action, dict) and str(action.get("signature") or "").strip() == signature
            for action in applied_actions
        )

    def parse_prompt(self, prompt: str) -> ParsedOperatorCommand | None:
        normalized = " ".join(str(prompt or "").strip().lower().split())
        if not normalized:
            return None
        if not any(token in normalized for token in ("lower", "reduce", "decrease", "drop", "cut", "trim", "markdown", "discount")):
            return None
        if not any(token in normalized for token in ("price", "prices", "listing", "listings", "item", "items")):
            return None

        percent = self._extract_percent(normalized)
        if percent is None or percent <= 0:
            return None

        minimum_age_days = self._extract_age_days(normalized)
        minimum_price = self._extract_minimum_price(normalized)
        price_filter_mode = "all"
        if minimum_age_days is not None and minimum_age_days > 0:
            price_filter_mode = "minimum_age_days"
        elif minimum_price is not None and minimum_price > 0:
            price_filter_mode = "minimum_price"

        command_type = "ebay_reprice_all"
        if minimum_age_days is not None and minimum_age_days > 0:
            command_type = "ebay_reprice_by_listing_age"
        elif minimum_price is not None and minimum_price > 0:
            command_type = "ebay_reprice_by_listing_price"

        return ParsedOperatorCommand(
            command_type=command_type,
            marketplace="ebay" if "ebay" in normalized or "published" in normalized or "live" in normalized else "ebay",
            percent=float(percent),
            minimum_age_days=int(minimum_age_days) if minimum_age_days is not None else None,
            minimum_price=float(minimum_price) if minimum_price is not None else None,
            price_filter_mode=price_filter_mode,
        )

    async def handle_prompt(
        self,
        db: Session,
        *,
        user: User,
        prompt: str,
        dry_run: bool = True,
        apply_live: bool = False,
        confirm_live_apply: bool = False,
        confirmation_phrase: str | None = None,
    ) -> dict[str, Any]:
        parsed = self.parse_prompt(prompt)
        if not parsed:
            return {
                "prompt": prompt,
                "parsed": False,
                "command_type": "unsupported",
                "dry_run": True,
                "apply_live": False,
                "requires_confirmation": False,
                "confirmation_phrase": None,
                "message": "Prompt not recognized yet. Current supported command: lower live eBay prices by a percentage when listings are older than a time threshold.",
                "summary": {},
                "listings": [],
            }

        return await self._run_ebay_reprice_command(
            db,
            user=user,
            prompt=prompt,
            parsed=parsed,
            dry_run=dry_run,
            apply_live=apply_live,
            confirm_live_apply=confirm_live_apply,
            confirmation_phrase=confirmation_phrase,
        )

    async def _run_ebay_reprice_command(
        self,
        db: Session,
        *,
        user: User,
        prompt: str,
        parsed: ParsedOperatorCommand,
        dry_run: bool,
        apply_live: bool,
        confirm_live_apply: bool,
        confirmation_phrase: str | None,
    ) -> dict[str, Any]:
        live_rows = db.execute(
            select(MarketplaceListing)
            .where(
                MarketplaceListing.marketplace == MarketplaceName.ebay,
                MarketplaceListing.status.in_(
                    [
                        MarketplaceListingStatus.PUBLISHED,
                        MarketplaceListingStatus.UPDATED,
                    ]
                ),
            )
            .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        ).scalars().all()

        latest_marketplace_row_by_listing: dict[int, MarketplaceListing] = {}
        for row in live_rows:
            if row.listing_id not in latest_marketplace_row_by_listing:
                latest_marketplace_row_by_listing[row.listing_id] = row

        live_listing_ids = list(latest_marketplace_row_by_listing.keys())
        listings = (
            db.execute(
                select(Listing).where(
                    Listing.user_id == user.id,
                    Listing.id.in_(live_listing_ids),
                    Listing.sale_price.is_(None),
                    Listing.sold_at.is_(None),
                )
            )
            .scalars()
            .all()
            if live_listing_ids
            else []
        )
        listing_by_id = {listing.id: listing for listing in listings}

        cutoff = None
        if parsed.minimum_age_days is not None:
            cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=parsed.minimum_age_days)
        preview_rows: list[dict[str, Any]] = []
        eligible_listing_ids: list[int] = []
        skipped_already_applied = 0
        total_live = 0
        older_than_threshold = 0
        above_price_threshold = 0
        skipped_without_price = 0
        signature = self._build_bulk_action_signature(parsed)

        for listing_id, marketplace_row in latest_marketplace_row_by_listing.items():
            listing = listing_by_id.get(listing_id)
            if not listing:
                continue
            total_live += 1
            if self._listing_has_bulk_action(listing, signature):
                skipped_already_applied += 1
                continue
            posted_at = marketplace_row.created_at or marketplace_row.updated_at or listing.updated_at or listing.created_at

            current_price = self._coerce_price(listing.listing_price or listing.suggested_price or listing.buy_it_now_price)
            if current_price is None:
                skipped_without_price += 1
                continue

            if parsed.minimum_age_days is not None:
                if not posted_at or not cutoff or posted_at > cutoff:
                    continue
                older_than_threshold += 1

            if parsed.minimum_price is not None:
                if current_price <= parsed.minimum_price:
                    continue
                above_price_threshold += 1

            new_price = round(max(0.99, current_price * (1 - (parsed.percent / 100.0))), 2)
            if math.isclose(new_price, current_price, abs_tol=0.009):
                continue

            eligible_listing_ids.append(listing.id)
            preview_rows.append(
                {
                    "listing_id": listing.id,
                    "title": listing.title,
                    "current_price": current_price,
                    "new_price": new_price,
                    "posted_at": posted_at,
                    "ebay_listing_id": listing.ebay_listing_id,
                    "status": "preview" if dry_run or not apply_live else "queued",
                    "message": None,
                }
            )

        summary = {
            "marketplace": parsed.marketplace,
            "percent": parsed.percent,
            "minimum_age_days": parsed.minimum_age_days,
            "minimum_price": parsed.minimum_price,
            "price_filter_mode": parsed.price_filter_mode,
            "total_live_ebay_listings": total_live,
            "older_than_threshold": older_than_threshold,
            "above_price_threshold": above_price_threshold,
            "already_applied_count": skipped_already_applied,
            "eligible_count": len(preview_rows),
            "skipped_without_price": skipped_without_price,
            "updated_count": 0,
            "failed_count": 0,
        }

        threshold_label = "all live eBay listings"
        if parsed.minimum_age_days is not None:
            threshold_label = f"live eBay listings posted more than {parsed.minimum_age_days} days ago"
        elif parsed.minimum_price is not None:
            threshold_label = f"live eBay listings priced above ${parsed.minimum_price:.2f}"

        response = {
            "prompt": prompt,
            "parsed": True,
            "command_type": parsed.command_type,
            "dry_run": bool(dry_run or not apply_live),
            "apply_live": bool(apply_live),
            "requires_confirmation": True,
            "confirmation_mode": "checkbox",
            "confirmation_phrase": LIVE_EBAY_REPRICE_CONFIRMATION_PHRASE,
            "message": None,
            "summary": summary,
            "listings": preview_rows[:100],
        }

        if dry_run or not apply_live:
            response["message"] = (
                f"Preview ready. {len(preview_rows)} {threshold_label} are eligible for a {parsed.percent:.0f}% price drop."
            )
            return response

        if not confirm_live_apply and str(confirmation_phrase or "").strip() != LIVE_EBAY_REPRICE_CONFIRMATION_PHRASE:
            response["message"] = (
                "Live eBay repricing requires confirmation."
            )
            return response

        updated_count = 0
        failed_count = 0
        live_rows_by_listing = {row["listing_id"]: row for row in preview_rows}
        for listing_id in eligible_listing_ids:
            listing = db.get(Listing, listing_id)
            if not listing:
                failed_count += 1
                row = live_rows_by_listing.get(listing_id)
                if row:
                    row["status"] = "failed"
                    row["message"] = "Listing no longer exists."
                continue
            row = live_rows_by_listing.get(listing_id)
            try:
                listing.listing_price = row["new_price"]
                listing.suggested_price = row["new_price"]
                marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
                operator_bulk_actions = marketplace_data.get("operator_bulk_actions") if isinstance(marketplace_data.get("operator_bulk_actions"), dict) else {}
                applied_actions = list(operator_bulk_actions.get("applied") or [])
                applied_actions = [
                    action for action in applied_actions
                    if not (isinstance(action, dict) and str(action.get("signature") or "").strip() == signature)
                ]
                applied_actions.append(
                    {
                        "signature": signature,
                        "command_type": parsed.command_type,
                        "marketplace": parsed.marketplace,
                        "percent": parsed.percent,
                        "minimum_age_days": parsed.minimum_age_days,
                        "minimum_price": parsed.minimum_price,
                        "previous_price": row["current_price"],
                        "new_price": row["new_price"],
                        "applied_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                        "applied_by_user_id": user.id,
                    }
                )
                marketplace_data["operator_bulk_actions"] = {
                    **operator_bulk_actions,
                    "applied": applied_actions[-25:],
                }
                listing.marketplace_data = marketplace_data
                db.add(listing)
                await revise_ebay_listing(listing, db)
                updated_count += 1
                if row:
                    row["status"] = "updated"
                    row["message"] = "Live eBay price updated."
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                failed_count += 1
                if row:
                    row["status"] = "failed"
                    row["message"] = str(exc)

        response["dry_run"] = False
        response["summary"]["updated_count"] = updated_count
        response["summary"]["failed_count"] = failed_count
        response["summary"]["skipped_already_applied"] = skipped_already_applied
        response["message"] = f"Applied live eBay repricing to {updated_count} listings."
        return response

    def _extract_percent(self, normalized_prompt: str) -> float | None:
        percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%", normalized_prompt)
        if percent_match:
            return float(percent_match.group(1))
        digit_percent_match = re.search(r"(\d+(?:\.\d+)?)\s+percent", normalized_prompt)
        if digit_percent_match:
            return float(digit_percent_match.group(1))
        percent_word_match = re.search(r"(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|twenty|thirty)\s+percent", normalized_prompt)
        if percent_word_match:
            return float(_NUMBER_WORDS[percent_word_match.group(1)])
        return None

    def _extract_age_days(self, normalized_prompt: str) -> int | None:
        if not any(token in normalized_prompt for token in ("day", "days", "week", "weeks", "month", "months", "older than", "more than", "posted for", "listed for")):
            return None
        day_match = re.search(r"(\d+)\s*(day|days|week|weeks)", normalized_prompt)
        if day_match:
            value = int(day_match.group(1))
            unit = day_match.group(2)
            return value * 7 if unit.startswith("week") else value

        month_match = re.search(r"(\d+)\s*(month|months)", normalized_prompt)
        if month_match:
            return int(month_match.group(1)) * 30

        word_match = re.search(r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(day|days|week|weeks)", normalized_prompt)
        if word_match:
            value = int(_NUMBER_WORDS[word_match.group(1)])
            unit = word_match.group(2)
            return value * 7 if unit.startswith("week") else value

        word_month_match = re.search(r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(month|months)", normalized_prompt)
        if word_month_match:
            return int(_NUMBER_WORDS[word_month_match.group(1)]) * 30
        return None

    def _extract_minimum_price(self, normalized_prompt: str) -> float | None:
        price_match = re.search(
            r"(?:over|above|more than|greater than|higher than|priced above|priced over|at least)\s+\$?(\d+(?:\.\d+)?)",
            normalized_prompt,
        )
        if price_match:
            return float(price_match.group(1))
        threshold_match = re.search(r"(?:price|priced|listing price|item price|cost)\s*(?:is\s*)?(?:over|above|more than|greater than)\s+\$?(\d+(?:\.\d+)?)", normalized_prompt)
        if threshold_match:
            return float(threshold_match.group(1))
        return None

    def _coerce_price(self, value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return round(parsed, 2)
