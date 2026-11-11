"""Idempotent source-wide Vine pricing-blocker repair.

The operator has explicitly authorized Vine prices as-is for human review.
This module clears price-only review blockers, never changes a price, never
approves a listing, and never publishes it. Positive approved asks remain
mandatory at the final marketplace boundary.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import re

from sqlalchemy.orm import Session

from app.models.models import Listing
from app.services.canonical_readiness import canonical_listing_readiness


_PRICE_REVIEW_BLOCKERS = (
    "valid positive listing price",
    "evidence-backed pricing research",
    "unsupported estimate",
    "pricing_evidence_missing",
    "pricing_provenance_missing",
    "price is missing",
    "price_missing",
)


def _is_price_blocker(value: object) -> bool:
    text = str(value or "").lower()
    return any(marker in text for marker in _PRICE_REVIEW_BLOCKERS)


def _stored_pricing_blockers(listing: Listing) -> list[str]:
    found: list[str] = []
    summary = getattr(listing, "readiness_summary", None)
    if isinstance(summary, dict):
        found.extend(str(value) for value in summary.get("blockers") or [] if _is_price_blocker(value))
    if _is_price_blocker(getattr(listing, "processing_blocking_reason", None)):
        found.append(str(listing.processing_blocking_reason))
    if _is_price_blocker(getattr(listing, "processing_last_error", None)):
        found.append(str(listing.processing_last_error))
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    preflight = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else {}
    by_market = preflight.get("by_marketplace") if isinstance(preflight.get("by_marketplace"), dict) else {}
    for row in by_market.values():
        if not isinstance(row, dict):
            continue
        for key in ("blockers", "execution_blockers", "preparation_blockers"):
            for issue in row.get(key) or []:
                if _is_price_blocker(issue.get("message") if isinstance(issue, dict) else issue) or (
                    isinstance(issue, dict) and str(issue.get("code") or "").upper() in {
                        "PRICING_EVIDENCE_MISSING", "PRICING_PROVENANCE_MISSING",
                    }
                ):
                    found.append(str(issue.get("message") or issue.get("code") or "pricing blocker") if isinstance(issue, dict) else str(issue))
        found.extend(str(message) for message in row.get("blocker_messages") or [] if _is_price_blocker(message))
        if str(row.get("top_blocker_code") or "").upper() in {"PRICING_EVIDENCE_MISSING", "PRICING_PROVENANCE_MISSING"}:
            found.append(str(row.get("top_blocker_message") or row.get("top_blocker_code")))
    return found


def repair_verified_vine_price_readiness(db: Session, *, apply: bool = False) -> dict:
    """Clear Vine pricing blockers across all Vine rows, regardless of provenance.

    Only the operator's Vine review policy is applied here. Every monetary
    field and source record is read-only. Missing/zero price remains missing;
    the publishing adapters still reject it until an operator supplies a
    positive ask. Other content/category/provider blockers are retained.
    """
    rows = db.query(Listing).filter(Listing.source_type == "amazon_vine").all()
    changed_ids: list[int] = []
    newly_review: list[int] = []
    before_price_blocked = 0
    after_price_blocked = 0
    missing_price = 0
    positive_price = 0
    remaining_nonprice: Counter[str] = Counter()

    for listing in rows:
        original_price = getattr(listing, "listing_price", None)
        try:
            positive = original_price is not None and float(original_price) > 0
        except (TypeError, ValueError, OverflowError):
            positive = False
        positive_price += int(positive)
        missing_price += int(not positive)

        before = canonical_listing_readiness(listing)
        stored_price_blockers_before = _stored_pricing_blockers(listing)
        before_blockers = list(before.get("blocking_reasons") or []) + stored_price_blockers_before
        if any(_is_price_blocker(value) for value in before_blockers):
            before_price_blocked += 1

        changed = False
        summary = getattr(listing, "readiness_summary", None)
        if isinstance(summary, dict):
            old = list(summary.get("blockers") or [])
            new = [value for value in old if not _is_price_blocker(value)]
            if new != old:
                listing.readiness_summary = {**summary, "blockers": new}
                changed = True

        marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        preflight = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else {}
        by_market = dict(preflight.get("by_marketplace") or {}) if isinstance(preflight.get("by_marketplace"), dict) else {}
        cache_changed = False
        for destination, row in by_market.items():
            if not isinstance(row, dict):
                continue
            revised = dict(row)
            row_changed = False
            for key in ("blockers", "execution_blockers", "preparation_blockers"):
                old = list(revised.get(key) or [])
                new = [issue for issue in old if not (
                    _is_price_blocker(issue.get("message") if isinstance(issue, dict) else issue)
                    or (isinstance(issue, dict) and str(issue.get("code") or "").upper() in {
                        "PRICING_EVIDENCE_MISSING", "PRICING_PROVENANCE_MISSING",
                    })
                )]
                if new != old:
                    revised[key] = new
                    cache_changed = row_changed = True
            old_codes = list(revised.get("blocker_codes") or [])
            new_codes = [code for code in old_codes if str(code).upper() not in {"PRICING_EVIDENCE_MISSING", "PRICING_PROVENANCE_MISSING"}]
            if new_codes != old_codes:
                revised["blocker_codes"] = new_codes
                cache_changed = row_changed = True
            old_messages = list(revised.get("blocker_messages") or [])
            new_messages = [message for message in old_messages if not _is_price_blocker(message)]
            if new_messages != old_messages:
                revised["blocker_messages"] = new_messages
                cache_changed = row_changed = True
            if str(revised.get("top_blocker_code") or "").upper() in {"PRICING_EVIDENCE_MISSING", "PRICING_PROVENANCE_MISSING"} or _is_price_blocker(revised.get("top_blocker_message")):
                remaining = next((item for key in ("blockers", "execution_blockers", "preparation_blockers") for item in revised.get(key) or []), None)
                revised["top_blocker_code"] = remaining.get("code") if isinstance(remaining, dict) else None
                revised["top_blocker_message"] = (remaining.get("message") or remaining.get("code")) if isinstance(remaining, dict) else (str(remaining) if remaining else None)
                cache_changed = row_changed = True
            if row_changed:
                # This cache is recalculated only for Vine's newly accepted
                # price policy; PRICE_MISSING remains a real publish blocker.
                revised["vine_pricing_policy_recomputed_at"] = datetime.now(UTC).isoformat()
                if not any(revised.get(key) for key in ("blockers", "execution_blockers", "preparation_blockers")) and positive:
                    revised["status"] = "needs_review"
                by_market[destination] = revised
        if cache_changed:
            listing.marketplace_data = {
                **marketplace_data,
                "marketplace_preflight": {**preflight, "by_marketplace": by_market},
            }
            changed = True

        old_processing_reason = str(getattr(listing, "processing_blocking_reason", None) or "").strip()
        if old_processing_reason:
            reason_parts = [part.strip() for part in re.split(r"[;\n|]+", old_processing_reason) if part.strip()]
            kept_parts = [part for part in reason_parts if not _is_price_blocker(part)]
            if len(kept_parts) != len(reason_parts):
                listing.processing_blocking_reason = "; ".join(kept_parts) or None
                if not kept_parts:
                    listing.processing_last_error = None
                    listing.processing_error_stage = None
                changed = True
        old_last_error = str(getattr(listing, "processing_last_error", None) or "").strip()
        if old_last_error:
            error_parts = [part.strip() for part in re.split(r"[;\n|]+", old_last_error) if part.strip()]
            kept_errors = [part for part in error_parts if not _is_price_blocker(part)]
            if len(kept_errors) != len(error_parts):
                listing.processing_last_error = "; ".join(kept_errors) or None
                if not kept_errors and not str(getattr(listing, "processing_blocking_reason", None) or "").strip():
                    listing.processing_error_stage = None
                changed = True

        after = canonical_listing_readiness(listing)
        after_blockers = list(after.get("blocking_reasons") or [])
        after_prices = [value for value in after_blockers if _is_price_blocker(value)] + _stored_pricing_blockers(listing)
        if after_prices:
            after_price_blocked += 1
        for value in after_blockers:
            if not _is_price_blocker(value):
                remaining_nonprice[str(value)] += 1

        lifecycle = str(getattr(getattr(listing, "status", None), "value", getattr(listing, "status", "")) or "").lower()
        if (
            after.get("queue") == "NEEDS_REVIEW"
            and not after_prices
            and not after.get("remote_live")
            and lifecycle not in {"ready", "posted", "published", "archived", "sold"}
        ):
            if stored_price_blockers_before:
                newly_review.append(int(listing.id))
            if not listing.needs_review:
                listing.needs_review = True
                changed = True
            if str(listing.processing_state or "").lower() in {"needs_attention", "retry"} and not after_blockers:
                listing.processing_state = "complete"
                listing.processing_stage = "quality_gate"
                changed = True

        # Refuse any accidental mutation of the commercial amount.
        if getattr(listing, "listing_price", None) != original_price:
            raise RuntimeError(f"Vine price repair unexpectedly changed listing_price for row {listing.id}")
        if changed:
            changed_ids.append(int(listing.id))
            if apply:
                db.add(listing)

    if apply and changed_ids:
        db.commit()
    else:
        db.rollback()

    return {
        "mode": "applied" if apply else "dry_run",
        "vine_records_scanned": len(rows),
        "price_blocked_before": before_price_blocked,
        "price_blocked_after": after_price_blocked,
        "positive_existing_prices": positive_price,
        "missing_or_nonpositive_prices": missing_price,
        "newly_accessible_needs_review": len(newly_review),
        "changed_records": len(changed_ids) if apply else 0,
        "would_change_records": len(changed_ids) if not apply else 0,
        "newly_review_listing_ids": newly_review if apply else [],
        "remaining_nonprice_blockers": dict(remaining_nonprice.most_common()),
    }
