from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, User
from app.services.ebay_service import revise_ebay_listing
from app.services.marketplace_mutations import apply_marketplace_operation

LIVE_EBAY_REPRICE_CONFIRMATION_PHRASE = "APPLY LIVE EBAY PRICE CHANGES"
OPERATOR_TIMEZONE = ZoneInfo("America/New_York")

# A single declarative registry keeps the Dashboard/operator surface honest
# about which structured actions are available.  The registry is metadata only;
# execution still goes through the normal scoped mutation and marketplace job
# services rather than arbitrary SQL or agent-specific business logic.
OPERATOR_TOOL_REGISTRY = (
    ("search_listings", "query", "preview"),
    ("filter_listings", "query", "preview"),
    ("get_listing", "query", "preview"),
    ("get_marketplace_states", "query", "preview"),
    ("get_remote_identities", "query", "preview"),
    ("update_price", "mutation", "queued"),
    ("update_shipping", "mutation", "queued"),
    ("update_title", "mutation", "queued"),
    ("update_description", "mutation", "queued"),
    ("update_quantity", "mutation", "queued"),
    ("update_condition", "mutation", "queued"),
    ("update_category", "mutation", "queued"),
    ("update_marketplace_override", "mutation", "queued"),
    ("publish_listing", "mutation", "confirmation_required"),
    ("crosspost_listing", "mutation", "confirmation_required"),
    ("end_listing", "mutation", "confirmation_required"),
    ("reconcile_listing", "query", "preview"),
    ("query_sales", "query", "preview"),
    ("query_analytics", "query", "preview"),
    ("move_inventory", "mutation", "queued"),
    ("set_location", "mutation", "queued"),
    ("regenerate_description", "mutation", "queued"),
    ("reprice_from_comps", "mutation", "confirmation_required"),
)


def get_operator_tool_registry() -> list[dict[str, str]]:
    return [{"name": name, "kind": kind, "execution": execution} for name, kind, execution in OPERATOR_TOOL_REGISTRY]

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
    maximum_age_days: int | None = None
    minimum_price: float | None = None
    maximum_price: float | None = None
    posted_on_dates: list[date] | None = None
    unsupported_constraints: list[str] | None = None
    price_filter_mode: str = "all"
    weekday: int | None = None
    odd_remote_id: bool = False
    shipping_flat: float | None = None


@dataclass
class StructuredOperation:
    """One independently targeted operation from a compound request."""
    items: list[int]
    marketplaces: list[str]
    field: str
    action: str
    value: Any = None


class OperatorCommandService:
    def _cross_market_calendar_reprice_preview(self, db: Session, *, user: User, prompt: str) -> dict[str, Any] | None:
        """Build the exact two-market calendar reprice preview from remote facts.

        This intentionally supports only the explicitly requested shape.  It
        does not fall back to local creation timestamps: publication time and
        the two marketplace prices must be present in each persisted remote
        response before a listing can match.
        """
        text = " ".join(str(prompt or "").lower().split())
        required = (
            "ebay" in text and "facebook" in text
            and "monday" in text
            and bool(re.search(r"odd(?:-numbered)? day of (?:the )?month", text))
            and bool(re.search(r"(?:above|over|greater than)\s*\$?50(?:\.0+)?", text))
            and bool(re.search(r"ebay.{0,100}(?:by|price by)\s*5\s*%", text))
            and bool(re.search(r"facebook.{0,100}(?:by|price by)\s*3\s*%", text))
        )
        if not required:
            return None

        rows = db.execute(
            select(MarketplaceListing).join(Listing, MarketplaceListing.listing_id == Listing.id).where(
                Listing.user_id == user.id,
                MarketplaceListing.marketplace.in_([MarketplaceName.ebay, MarketplaceName.facebook]),
                MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
            ).order_by(MarketplaceListing.id.desc())
        ).scalars().all()
        by_listing: dict[int, dict[str, MarketplaceListing]] = {}
        for row in rows:
            market = str(row.marketplace.value if hasattr(row.marketplace, "value") else row.marketplace).lower()
            if market not in {"ebay", "facebook"} or row.listing_id in by_listing and market in by_listing[row.listing_id]:
                continue
            by_listing.setdefault(row.listing_id, {})[market] = row

        matches: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for listing_id, markets in by_listing.items():
            if set(markets) != {"ebay", "facebook"}:
                continue
            listing = db.get(Listing, listing_id)
            ebay_row, facebook_row = markets["ebay"], markets["facebook"]
            published_at = self._remote_published_at(ebay_row)
            ebay_price = self._remote_price(ebay_row)
            facebook_price = self._remote_price(facebook_row)
            reasons: list[str] = []
            if published_at is None:
                reasons.append("eBay remote publication timestamp is unavailable")
            if ebay_price is None:
                reasons.append("eBay remote current price is unavailable")
            if facebook_price is None:
                reasons.append("Facebook remote current price is unavailable")
            if reasons:
                skipped.append({"listing_id": listing_id, "reasons": reasons})
                continue
            local_published = published_at.astimezone(OPERATOR_TIMEZONE)
            if local_published.weekday() != 0 or local_published.day % 2 != 1 or ebay_price <= 50:
                continue
            matches.append({
                "listing_id": listing_id,
                "title": listing.title if listing else None,
                "publication_timestamp": published_at.isoformat(),
                "publication_timezone": "America/New_York",
                "ebay": {
                    "marketplace_listing_id": ebay_row.marketplace_listing_id,
                    "current_price": ebay_price,
                    "proposed_price": round(ebay_price * 0.95, 2),
                },
                "facebook": {
                    "marketplace_listing_id": facebook_row.marketplace_listing_id,
                    "current_price": facebook_price,
                    "proposed_price": round(facebook_price * 0.97, 2),
                },
                "profitability": {
                    "status": "REQUIRES_CURRENT_FEE_AND_SHIPPING_EVIDENCE",
                    "message": "No profitability guarantee is made from listing prices alone.",
                },
                "why_matched": "Active on eBay and Facebook; eBay remote publication time is Monday on an odd calendar day; eBay remote price exceeds $50.",
            })
        return {
            "prompt": prompt,
            "parsed": True,
            "command_type": "cross_market_calendar_reprice_preview",
            "dry_run": True,
            "apply_live": False,
            "requires_confirmation": True,
            "confirmation_phrase": "APPLY CROSS-MARKET PRICE CHANGES",
            "timezone": "America/New_York",
            "message": "Preview only. No marketplace price was changed.",
            "summary": {"eligible_count": len(matches), "skipped_missing_remote_evidence": len(skipped)},
            "listings": matches,
            "skipped": skipped,
        }

    @staticmethod
    def _remote_published_at(row: MarketplaceListing) -> datetime | None:
        raw = row.raw_response if isinstance(row.raw_response, dict) else {}
        for key in ("published_at", "publishedAt", "listing_start_time", "listingStartTime"):
            value = raw.get(key)
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        return None

    @staticmethod
    def _remote_price(row: MarketplaceListing) -> float | None:
        raw = row.raw_response if isinstance(row.raw_response, dict) else {}
        for key in ("current_price", "currentPrice", "price", "listing_price", "listingPrice"):
            try:
                value = float(raw.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                return round(value, 2)
        return None

    def parse_operation_plan_with_diagnostics(self, prompt: str) -> tuple[list[StructuredOperation], list[str]]:
        """Return parsed operations plus clauses that were not understood.

        Compound commands are safety-sensitive: silently dropping one clause
        can make an apparently precise request execute only part of the user's
        intent.  Callers can therefore require the operator to clarify before
        any mutation is previewed or applied.
        """
        text = " ".join(str(prompt or "").lower().split())
        if not text:
            return [], []
        clauses = re.split(r"\s*(?:;|\n|\.\s+(?=(?:for\s+items?|give\s+items?|end\s+items?|remove\s+items?|lower\s+items?|reduce\s+items?)))\s*", text)
        plan: list[StructuredOperation] = []
        unsupported: list[str] = []
        for clause in clauses:
            before = len(plan)
            # Reuse the established parser for one clause so parsing behavior
            # remains identical to the legacy public method.
            parsed = self._parse_operation_clause(clause)
            if parsed:
                plan.extend(parsed)
            elif clause.strip() and not self._is_explicit_noop_clause(clause):
                unsupported.append(clause.strip())
            if len(plan) == before and not clause.strip():
                continue
        return plan, unsupported

    @staticmethod
    def _is_explicit_noop_clause(clause: str) -> bool:
        """Recognize explicit preservation instructions as intentional no-ops."""
        normalized = " ".join(str(clause or "").lower().split())
        return bool(re.search(r"\b(?:leave|keep|do not change|don't change|no changes?)\b", normalized)) and bool(
            re.search(r"\b(?:unchanged|alone|as[- ]is|other|everything else)\b", normalized)
        )

    def _parse_operation_clause(self, clause: str) -> list[StructuredOperation]:
        """Parse one clause; kept separate so diagnostics can be lossless."""
        item_match = re.search(r"(?:items?|listings?)\s+(.+?)(?=\s+(?:by|with|at|on|free|make|price|reduce|lower|end|remove|delist)\b|$)", clause)
        if not item_match:
            item_match = re.search(r"^(?:give|end|remove|delist)\s+(.+?)(?=\s+(?:on|free|with)\b|$)", clause)
        if not item_match:
            return []
        items = [int(value) for value in re.findall(r"\d+", item_match.group(1))]
        markets = [name for name in ("ebay", "facebook", "mercari", "vinted", "poshmark", "etsy", "offerup") if name in clause]
        if "all marketplaces" in clause or "every marketplace" in clause:
            markets = ["all"]
        if "canonical" in clause and not markets:
            markets = ["canonical"]
        if not items or not markets:
            return []
        if re.search(r"(?:regenerate|rewrite|refresh).{0,40}\bdescription(?:s)?\b", clause):
            return [StructuredOperation(items, markets, "description", "regenerate", None)]
        if re.search(r"(?:set|change|make|update).{0,30}\bprice(?:s)?\b.{0,12}(?:to|at)\s*\$?\d+(?:\.\d{1,2})?", clause):
            amount = re.search(r"(?:to|at)\s*\$?(\d+(?:\.\d{1,2})?)", clause)
            return [StructuredOperation(items, markets, "price", "set", round(float(amount.group(1)), 2))] if amount else []
        if re.search(r"(?:raise|increase|increas(?:e|ing)|higher).{0,30}\d+(?:\.\d+)?\s*%", clause):
            percent = re.search(r"(\d+(?:\.\d+)?)\s*%", clause)
            return [StructuredOperation(items, markets, "price", "percentage_change", float(percent.group(1)))] if percent else []
        if re.search(r"(?:lower|reduce|decrease|drop|cut).{0,30}\d+(?:\.\d+)?\s*%", clause):
            percent = re.search(r"(\d+(?:\.\d+)?)\s*%", clause)
            return [StructuredOperation(items, markets, "price", "percentage_change", -float(percent.group(1)))] if percent else []
        if re.search(r"(?:free|no[- ]cost)\s+(?:ebay\s+)?shipping", clause):
            return [StructuredOperation(items, markets, "shipping", "set", "free")]
        if re.search(r"\bend\b|\bremove\b|\bdelist\b", clause):
            return [StructuredOperation(items, markets, "listing", "end", None)]
        return []

    def parse_operation_plan(self, prompt: str) -> list[StructuredOperation]:
        """Parse safe, explicit compound mutations for preview generation.

        This deliberately returns a plan only; execution still goes through
        the scoped mutation/publish services and explicit confirmation gates.
        Unsupported clauses are ignored rather than guessed.
        """
        plan, _unsupported = self.parse_operation_plan_with_diagnostics(prompt)
        return plan

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

        minimum_age_days, maximum_age_days = self._extract_age_bounds(normalized)
        minimum_price, maximum_price = self._extract_price_bounds(normalized)
        posted_on_dates = self._extract_posted_dates(normalized)
        unsupported_constraints = self._unsupported_reprice_constraints(normalized)
        # A mutation must never broaden to "all listings" because a requested
        # predicate (dates, history, variants, or scheduling) was not modeled.
        if unsupported_constraints:
            return None
        price_filter_mode = "all"
        if minimum_age_days is not None or maximum_age_days is not None or posted_on_dates:
            price_filter_mode = "minimum_age_days"
        if minimum_price is not None or maximum_price is not None:
            price_filter_mode = "minimum_price"

        command_type = "ebay_reprice_all"
        if minimum_age_days is not None or maximum_age_days is not None or posted_on_dates:
            command_type = "ebay_reprice_by_listing_age"
        if (minimum_price is not None or maximum_price is not None) and command_type == "ebay_reprice_all":
            command_type = "ebay_reprice_by_listing_price"
        elif (minimum_price is not None or maximum_price is not None) and command_type == "ebay_reprice_by_listing_age":
            command_type = "ebay_reprice_by_listing_age_and_price"

        return ParsedOperatorCommand(
            command_type=command_type,
            marketplace="ebay" if "ebay" in normalized or "published" in normalized or "live" in normalized else "ebay",
            percent=float(percent),
            minimum_age_days=int(minimum_age_days) if minimum_age_days is not None else None,
            maximum_age_days=int(maximum_age_days) if maximum_age_days is not None else None,
            minimum_price=float(minimum_price) if minimum_price is not None else None,
            maximum_price=float(maximum_price) if maximum_price is not None else None,
            posted_on_dates=posted_on_dates,
            price_filter_mode=price_filter_mode,
            weekday=self._extract_weekday(normalized),
            odd_remote_id=bool(re.search(r"\b(?:item|listing) number\b.{0,20}\bodd\b", normalized)),
            shipping_flat=self._extract_flat_shipping(normalized),
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
        cross_market_preview = self._cross_market_calendar_reprice_preview(db, user=user, prompt=prompt)
        if cross_market_preview is not None:
            return cross_market_preview
        unsupported_filters = self._unsupported_reprice_constraints(" ".join(str(prompt or "").lower().split()))
        if unsupported_filters:
            return {
                "prompt": prompt,
                "parsed": False,
                "command_type": "unsupported_reprice_filter",
                "dry_run": True,
                "apply_live": False,
                "requires_confirmation": False,
                "unsupported_constraints": unsupported_filters,
                "message": "No changes were made. PosterPro cannot verify or safely execute these requested filters yet.",
                "summary": {"eligible_count": 0},
                "listings": [],
            }
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
        recent_cutoff = None
        if parsed.minimum_age_days is not None:
            cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=parsed.minimum_age_days)
        if parsed.maximum_age_days is not None:
            recent_cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=parsed.maximum_age_days)
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

            if parsed.posted_on_dates:
                if not posted_at or posted_at.date() not in parsed.posted_on_dates:
                    continue
            if parsed.maximum_age_days is not None:
                # "posted less than N days ago" means newer than the cutoff,
                # the inverse of "older than N days".
                if not posted_at or not recent_cutoff or posted_at <= recent_cutoff:
                    continue

            if parsed.weekday is not None and (not posted_at or posted_at.weekday() != parsed.weekday):
                continue
            if parsed.odd_remote_id:
                remote_id = str(marketplace_row.marketplace_listing_id or "").strip()
                if not remote_id.isdigit() or int(remote_id) % 2 == 0:
                    continue

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
            if parsed.maximum_price is not None and current_price >= parsed.maximum_price:
                continue

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
                    "shipping_flat": parsed.shipping_flat,
                }
            )

        summary = {
            "marketplace": parsed.marketplace,
            "percent": parsed.percent,
            "minimum_age_days": parsed.minimum_age_days,
            "maximum_age_days": parsed.maximum_age_days,
            "minimum_price": parsed.minimum_price,
            "maximum_price": parsed.maximum_price,
            "posted_on_dates": [value.isoformat() for value in (parsed.posted_on_dates or [])],
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
        if parsed.maximum_age_days is not None:
            threshold_label = f"live eBay listings posted within the last {parsed.maximum_age_days} days"
        if parsed.posted_on_dates:
            threshold_label = "live eBay listings posted on " + ", ".join(value.isoformat() for value in parsed.posted_on_dates)
        if parsed.minimum_price is not None or parsed.maximum_price is not None:
            price_label = ""
            if parsed.minimum_price is not None:
                price_label += f" priced above ${parsed.minimum_price:.2f}"
            if parsed.maximum_price is not None:
                price_label += f" priced below ${parsed.maximum_price:.2f}"
            threshold_label += price_label

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
                f"Preview ready. {len(preview_rows)} {threshold_label} are eligible for a {parsed.percent:.15g}% price drop."
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
                # Keep the remote destination change explicit while retaining
                # compatibility with the canonical price used by the eBay
                # adapter. Other marketplace overrides remain untouched.
                apply_marketplace_operation(listing, marketplaces=["ebay"], field="price", value=row["new_price"])
                if parsed.shipping_flat is not None:
                    apply_marketplace_operation(listing, marketplaces=["ebay"], field="shipping", value={"shipping_method": "flat", "shipping_price": parsed.shipping_flat, "free_shipping": False})
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
        if re.search(r"\bpi\s*percent\b", normalized_prompt):
            return math.pi
        return None

    def _extract_weekday(self, normalized_prompt: str) -> int | None:
        for name, value in (("monday", 0), ("tuesday", 1), ("wednesday", 2), ("thursday", 3), ("friday", 4), ("saturday", 5), ("sunday", 6)):
            # Operators commonly omit "on" in phrases such as "posted
            # Tuesday"; support both forms without broad substring matches.
            if re.search(rf"(?:\bon\s+|posted\s+){name}\b", normalized_prompt):
                return value
        return None

    def _extract_flat_shipping(self, normalized_prompt: str) -> float | None:
        match = re.search(r"(?:change|set|make).{0,40}\b(?:flat )?shipping\b.{0,20}\$([0-9]+(?:\.[0-9]{1,2})?)", normalized_prompt)
        if not match:
            match = re.search(r"\$([0-9]+(?:\.[0-9]{1,2})?)\s+flat shipping", normalized_prompt)
        return float(match.group(1)) if match else None

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

    def _extract_age_bounds(self, normalized_prompt: str) -> tuple[int | None, int | None]:
        age = self._extract_age_days(normalized_prompt)
        if age is None:
            return None, None
        recent = re.search(r"\b(?:less than|under|within|in the last|last)\s+(?:the\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:day|days|week|weeks)\b", normalized_prompt)
        if recent:
            return None, age
        return age, None

    def _extract_posted_dates(self, normalized_prompt: str) -> list[date] | None:
        if not re.search(r"\b(?:posted|listed|published)\b", normalized_prompt):
            return None
        found: list[date] = []
        for month, day, year in re.findall(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", normalized_prompt):
            try:
                found.append(date(int(year), int(month), int(day)))
            except ValueError:
                return None
        return list(dict.fromkeys(found)) or None

    def _extract_price_bounds(self, normalized_prompt: str) -> tuple[float | None, float | None]:
        minimum = self._extract_minimum_price(normalized_prompt)
        maximum_match = re.search(r"\b(?:under|below|less than|cheaper than)\s+\$?(\d+(?:\.\d{1,2})?)(?!\s*(?:day|days|week|weeks)\b)", normalized_prompt)
        maximum = float(maximum_match.group(1)) if maximum_match else None
        return minimum, maximum

    def _unsupported_reprice_constraints(self, normalized_prompt: str) -> list[str]:
        unsupported: list[str] = []
        # Do not silently reinterpret the master cross-marketplace example as
        # a simpler eBay-only price drop. These predicates require a separate
        # publication-date field, identity join, per-market rounding, and two
        # approval-gated remote revisions.
        if re.search(r"\bodd(?:-numbered)?\s+day(?:s)?\s+of\s+(?:the\s+)?month\b", normalized_prompt):
            unsupported.append("odd calendar-day publication filtering (not yet available in the safe reprice tool)")
        if re.search(r"\b(?:also|and)\s+(?:published|listed)\s+(?:to|on)\s+facebook\b", normalized_prompt):
            unsupported.append("cross-marketplace publication matching (not yet available in the safe reprice tool)")
        if "facebook" in normalized_prompt and "ebay" in normalized_prompt and len(re.findall(r"\d+(?:\.\d+)?\s*(?:%|percent)", normalized_prompt)) > 1:
            unsupported.append("distinct per-marketplace price changes (not yet available in the safe reprice tool)")
        if re.search(r"\b(?:never|hasn't|has not|without ever)\b.{0,35}\b(?:price reduction|price drop|discount|markdown|reduced)\b", normalized_prompt):
            unsupported.append("price-reduction history (the current catalog does not provide complete historical price-change evidence)")
        if re.search(r"\b(?:variant|variation|size|color|colour|medium|red)\b", normalized_prompt):
            unsupported.append("variant-specific pricing (variant-level sale pricing is not implemented by this command)")
        if re.search(r"\b(?:for|during|until|next)\s+(?:\d+\s+)?(?:hours?|days?)\b|\b(?:full moon|next full moon|schedule|scheduled)\b", normalized_prompt):
            unsupported.append("time-limited or scheduled pricing (no safe scheduler is available for this command)")
        if re.search(r"\b(?:posted|listed|published)\b.{0,30}\b\d{1,2}[/-]\d{1,2}[/-](?!\d{4}\b)\d+\b", normalized_prompt):
            unsupported.append("date format (use a valid date such as 10/14/2026)")
        return unsupported

    def _extract_minimum_price(self, normalized_prompt: str) -> float | None:
        price_match = re.search(
            r"(?:over|above|more than|greater than|higher than|priced above|priced over|at least)\s+\$?(\d+(?:\.\d+)?)(?!\s*(?:day|days|week|weeks)\b)",
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
