#!/usr/bin/env python3
"""Repair unpublished eBay draft shipping tiers and refresh eBay preflight.

Dry-run by default. Apply in bounded, resumable batches with --apply and
--after-id; this script never calls any publish/approval endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from collections import Counter
from pathlib import Path

from dotenv import dotenv_values

for key, value in dotenv_values("/opt/apps/posterpro/repo/backend/.env").items():
    if value is not None:
        os.environ.setdefault(key, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import Listing
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.shipping_profitability_policy import ebay_fulfillment_policy_for_price
from app.services.ebay_service import _ebay_effective_price, _listing_ebay_policy_settings, validate_cached_ebay_fulfillment_policy


parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true", help="Apply a bounded metadata repair and fresh preflight")
parser.add_argument("--limit", type=int, default=25, help="Maximum rows per invocation (1-100)")
parser.add_argument("--after-id", type=int, default=0, help="Resume after this listing ID")
parser.add_argument("--max-batches", type=int, default=1, help="Maximum committed batches per invocation")
parser.add_argument("--source-types", default="", help="Optional comma-separated source_type scope; default is all")
args = parser.parse_args()
limit = min(100, max(1, args.limit))


def _price(listing: Listing) -> float | None:
    try:
        amount = float(_ebay_effective_price(listing))
    except (TypeError, ValueError, OverflowError):
        return None
    return amount if amount > 0 and amount < float("inf") else None


def _publication_blocked(listing: Listing) -> bool:
    status = str(getattr(listing.status, "value", listing.status) or "").lower()
    ebay_status = str(getattr(listing.ebay_publish_status, "value", listing.ebay_publish_status) or "").lower()
    if status in {"sold", "archived", "cancelled", "canceled", "posted", "published", "active"}:
        return True
    if ebay_status in {"posted", "published", "posting", "queued", "unknown", "remote_state_unknown"}:
        return True
    if listing.ebay_listing_id:
        return True
    for remote in listing.marketplace_listings or []:
        remote_status = str(getattr(remote.status, "value", remote.status) or "").lower()
        if remote_status in {"published", "active"} or getattr(remote, "marketplace_listing_id", None):
            return True
    return False


def _shipping_values(price: float) -> dict:
    under = price < 25
    return {
        "mode": "flat" if under else "included",
        "shipping_charge_mode": "flat" if under else "included",
        "shipping_policy": "flat_rate" if under else "free_shipping",
        "shipping_setup_required": False,
        "shipping_setup_reason": None,
        "shipping_policy_blocker": None,
        "shipping_measurements_required": False,
        "buyer_pays_shipping": under,
        "buyer_shipping_charge": 8.49 if under else 0.0,
        "shipping_cost": 8.49 if under else 0.0,
        "free_shipping": not under,
        "domestic_service": "USPSParcel",
        "shipping_service": "USPSParcel",
        "fulfillment_policy_id": ebay_fulfillment_policy_for_price(price),
        "shipping_policy_threshold": 25.0,
        "shipping_policy_enforced": True,
    }


shipping_only_blockers = {
    "eBay listing policies are missing or invalid.",
    "Package weight is an estimate, not a verified packed measurement.",
    "eBay rejected the shipping or package details.",
}


db = SessionLocal()
service = MarketplacePreflightService()
try:
    source_types = [value.strip() for value in str(args.source_types or "").split(",") if value.strip()]
    statement = select(Listing).where(Listing.user_id == 2, Listing.id > args.after_id)
    if source_types:
        statement = statement.where(Listing.source_type.in_(source_types))
    rows = db.scalars(statement.order_by(Listing.id)).all()
    candidates = []
    totals = Counter()
    for listing in rows:
        price = _price(listing)
        if price is None or _publication_blocked(listing):
            continue
        process_state = str(listing.processing_state or "").lower()
        if process_state not in {"complete", "needs_attention"} or listing.restricted_review_required:
            continue
        tier = "under_25" if price < 25 else "at_or_above_25"
        totals[f"drafts_{tier}"] += 1
        expected = ebay_fulfillment_policy_for_price(price)
        data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        shipping = data.get("shipping") if isinstance(data.get("shipping"), dict) else {}
        profile = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
        overrides = data.get("marketplace_overrides") if isinstance(data.get("marketplace_overrides"), dict) else {}
        ebay = overrides.get("ebay") if isinstance(overrides.get("ebay"), dict) else {}
        ebay_shipping = ebay.get("shipping") if isinstance(ebay.get("shipping"), dict) else {}
        current_id = next((str(value) for value in (
            ebay_shipping.get("fulfillment_policy_id"),
            ebay.get("fulfillment_policy_id"),
            shipping.get("fulfillment_policy_id"),
            shipping.get("shipping_policy_id"),
            profile.get("fulfillment_policy_id"),
            profile.get("shipping_policy_id"),
        ) if value), "")
        preflight = ((data.get("marketplace_preflight") or {}).get("by_marketplace") or {}).get("ebay") or {}
        validation = (preflight.get("policy_summary") or {}).get("fulfillment_policy_validation") or {}
        preflight_matches = bool(validation.get("valid") and validation.get("policy_id") == expected)
        if not preflight_matches and current_id == expected:
            direct = validate_cached_ebay_fulfillment_policy(
                _listing_ebay_policy_settings(db, listing),
                sale_price=price,
                shipping_profile=profile,
            )
            checked_at = preflight.get("last_checked_at")
            try:
                checked = datetime.fromisoformat(str(checked_at or "").replace("Z", "+00:00"))
                if checked.tzinfo is not None:
                    checked = checked.astimezone(UTC).replace(tzinfo=None)
                updated = listing.updated_at
                if updated and updated.tzinfo is not None:
                    updated = updated.astimezone(UTC).replace(tzinfo=None)
                recent_preflight = datetime.now(UTC).replace(tzinfo=None) - checked <= timedelta(hours=24) and (not updated or abs((updated - checked).total_seconds()) <= 5)
            except (TypeError, ValueError):
                recent_preflight = False
            preflight_matches = bool(direct.get("valid") and direct.get("policy_id") == expected and recent_preflight)
        marker = any(
            value is True or str(value or "").strip().lower() == "setup_required"
            for value in (shipping.get("shipping_setup_required"), shipping.get("shipping_charge_mode"), profile.get("shipping_setup_required"), profile.get("shipping_charge_mode"), profile.get("shipping_policy"), profile.get("mode"))
        )
        if current_id != expected:
            totals["incorrect_shipping_policy"] += 1
        stale_shipping_blocker = str(listing.processing_blocking_reason or "").strip() in shipping_only_blockers
        if current_id != expected or marker or not preflight_matches or stale_shipping_blocker:
            candidates.append((listing, price, expected, current_id))

    batch_cap = min(40, max(1, args.max_batches))
    process_limit = limit * batch_cap
    print(json.dumps({"mode": "apply" if args.apply else "dry_run", "limit_per_batch": limit, "max_batches": batch_cap, "after_id": args.after_id, "eligible_tier_counts_after_cursor": dict(totals), "candidate_count": len(candidates), "candidate_ids": [row[0].id for row in candidates[:process_limit]]}, indent=2))
    if not args.apply:
        raise SystemExit(0)

    result_counts = Counter()
    blocker_counts = Counter()
    results = []
    batch_results = []
    for index, (listing, price, expected, old_id) in enumerate(candidates[:process_limit], start=1):
        values = _shipping_values(price)
        tier = "under_25" if price < 25 else "at_or_above_25"
        result_counts[f"repaired_{tier}"] += int(old_id != expected)
        profile = dict(listing.shipping_profile or {})
        profile.update(values)
        profile.pop("shipping_estimate_blocked", None)
        listing.shipping_profile = profile

        data = dict(listing.marketplace_data or {})
        shipping = dict(data.get("shipping") or {})
        shipping.update(values)
        data["shipping"] = shipping
        overrides = dict(data.get("marketplace_overrides") or {})
        ebay = dict(overrides.get("ebay") or {})
        ebay_shipping = dict(ebay.get("shipping") or {})
        ebay_shipping.update({
            "fulfillment_policy_id": expected,
            "fulfillment_policy_name": "PosterPro Buyer Paid Flat $8.49 Under $25" if price < 25 else "PosterPro Fulfillment 1780738916",
        })
        ebay["shipping"] = ebay_shipping
        overrides["ebay"] = ebay
        data["marketplace_overrides"] = overrides
        preflight = dict(data.get("marketplace_preflight") or {})
        by_market = dict(preflight.get("by_marketplace") or {})
        by_market.pop("ebay", None)
        preflight["by_marketplace"] = by_market
        data["marketplace_preflight"] = preflight
        listing.marketplace_data = data
        if str(listing.processing_blocking_reason or "").strip() in shipping_only_blockers:
            listing.processing_blocking_reason = None
            if str(listing.processing_state or "").lower() == "needs_attention":
                listing.processing_state = "complete"
            result_counts["cleared_stale_shipping_blocker"] += 1
        db.flush()

        direct_policy_validation = validate_cached_ebay_fulfillment_policy(
            _listing_ebay_policy_settings(db, listing),
            sale_price=price,
            shipping_profile=profile,
        )
        preflight_result = service.preflight_listing(db, listing, "ebay", allow_live_metadata_refresh=False)
        if not isinstance(preflight_result.get("policy_summary"), dict):
            preflight_result["policy_summary"] = {}
        if not (preflight_result["policy_summary"].get("fulfillment_policy_validation") or {}).get("valid"):
            preflight_result["policy_summary"].update({
                "fulfillment_policy_id": expected,
                "fulfillment_policy_validation": direct_policy_validation,
            })
        service.cache_preflight_summary(db, listing, preflight_result)
        passed = preflight_result.get("status") in {"ready", "ready_with_warnings"} and not preflight_result.get("blockers")
        validation = ((preflight_result.get("policy_summary") or {}).get("fulfillment_policy_validation") or {})
        repair_data = dict(listing.marketplace_data or {})
        repair_data["shipping_policy_repair"] = {
            "fulfillment_policy_id": expected,
            "verified_at": datetime.now(UTC).isoformat(),
            "preflight_status": preflight_result.get("status"),
            "policy_validated": bool(validation.get("valid") or direct_policy_validation.get("valid")),
            "buyer_charge": next((service_row.get("charge") for service_row in validation.get("services", []) if service_row.get("free_shipping") is False), 0.0 if validation.get("free_shipping") else None),
            "free_shipping": validation.get("free_shipping"),
        }
        listing.marketplace_data = repair_data
        if passed:
            result_counts[f"passed_{tier}"] += 1
            if str(getattr(listing.status, "value", listing.status) or "").lower() != "ready":
                listing.needs_review = True
                result_counts["restored_to_needs_review"] += 1
        else:
            result_counts[f"blocked_{tier}"] += 1
        results.append({
            "listing_id": listing.id,
            "price": price,
            "tier": tier,
            "policy_id": expected,
            "preflight_status": preflight_result.get("status"),
            "passed": passed,
            "buyer_charge": next((service.get("charge") for service in validation.get("services", []) if service.get("free_shipping") is False), 0.0 if validation.get("free_shipping") else None),
            "free_shipping": validation.get("free_shipping"),
            "blockers": [issue.get("code") or issue.get("message") for issue in preflight_result.get("blockers", [])],
        })
        batch_results.append(results[-1])
        blocker_counts.update(str(code) for code in results[-1]["blockers"])
        if index % limit == 0:
            db.commit()
            batch_results = []
    db.commit()
    audit_path = Path(__file__).resolve().parents[2] / "ops" / "shipping-repair" / "results.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, default=str) + "\n")
    print(json.dumps({"processed": len(results), "batch_result": dict(result_counts), "blocker_counts": dict(blocker_counts), "last_processed_id": results[-1]["listing_id"] if results else args.after_id, "sample_results": results[:5]}, indent=2, default=str))
except SystemExit:
    raise
except Exception:
    db.rollback()
    raise
finally:
    db.close()
