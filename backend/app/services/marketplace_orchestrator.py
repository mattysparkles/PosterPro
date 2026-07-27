from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceListing, User
from app.services.multi_platform_publisher import get_enabled_platforms
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.marketplace_error_translation import translate_marketplace_error
from app.workers.tasks import process_marketplace_crosspost_job_task, publish_listing_to_marketplace_task, sync_sold_everywhere_task


def list_marketplaces() -> list[dict]:
    return [
        {"name": MarketplaceName.ebay.value, "supports_oauth": True},
        {"name": MarketplaceName.etsy.value, "supports_oauth": False},
        {"name": MarketplaceName.mercari.value, "supports_oauth": False},
        {"name": MarketplaceName.facebook.value, "supports_oauth": False},
        {"name": MarketplaceName.poshmark.value, "supports_oauth": False},
        {"name": MarketplaceName.depop.value, "supports_oauth": False},
        {"name": MarketplaceName.whatnot.value, "supports_oauth": False},
        {"name": MarketplaceName.vinted.value, "supports_oauth": False},
    ]


def _find_pending_marketplace_work(db: Session, listing_id: int, marketplace: str) -> dict[str, object] | None:
    market = str(marketplace or "").strip().lower()
    if not market or market not in MarketplaceName._value2member_map_:
        return None

    pending_listing = (
        db.execute(
            select(MarketplaceListing)
            .where(
                MarketplaceListing.listing_id == listing_id,
                MarketplaceListing.marketplace == MarketplaceName(market),
                MarketplaceListing.status == MarketplaceListingStatus.PENDING,
            )
            .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        )
        .scalars()
        .first()
    )
    if pending_listing:
        return {
            "kind": "marketplace_listing",
            "id": pending_listing.id,
            "status": pending_listing.status.value if hasattr(pending_listing.status, "value") else str(pending_listing.status),
            "marketplace_listing_id": pending_listing.marketplace_listing_id,
        }

    jobs = (
        db.execute(
            select(MarketplaceCrosspostJob)
            .where(MarketplaceCrosspostJob.listing_id == listing_id)
            .order_by(MarketplaceCrosspostJob.updated_at.desc(), MarketplaceCrosspostJob.id.desc())
        )
        .scalars()
        .all()
    )
    for job in jobs:
        status_value = str(job.status or "").lower()
        if status_value not in {"queued", "running"}:
            continue
        targets = {str(target or "").strip().lower() for target in (job.target_marketplaces or []) if str(target or "").strip()}
        if market in targets:
            return {
                "kind": "crosspost_job",
                "id": job.id,
                "status": status_value,
                "task_id": job.task_id,
            }
    return None


def _is_marketplace_already_published(db: Session, listing: Listing, marketplace: str) -> bool:
    market = str(marketplace or "").strip().lower()
    if market == MarketplaceName.ebay.value:
        return bool(listing.ebay_listing_id or str(listing.ebay_publish_status or "").upper() == "POSTED")

    existing = (
        db.execute(
            select(MarketplaceListing)
            .where(
                MarketplaceListing.listing_id == listing.id,
                MarketplaceListing.marketplace == MarketplaceName(market),
            )
            .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        )
        .scalars()
        .first()
    )
    return bool(existing and existing.status == MarketplaceListingStatus.PUBLISHED)


def _queue_single_marketplace_publish(
    db: Session,
    *,
    listing: Listing,
    marketplace: str,
    preflight: dict | None = None,
    skip_already_queued: bool = False,
) -> dict:
    market_key = str(marketplace or "").strip().lower()
    if market_key not in MarketplaceName._value2member_map_:
        return {"marketplace": market_key, "status": "UNSUPPORTED", "task_id": None}

    preflight_service = MarketplacePreflightService()
    preflight = preflight or preflight_service.preflight_listing(db, listing, market_key)
    blockers = preflight.get("blockers") or []
    warnings = preflight.get("warnings") or []
    if blockers:
        return {
            "marketplace": market_key,
            "status": "BLOCKED",
            "task_id": None,
            "error": "; ".join(issue.get("message") or "Publish blocked" for issue in blockers),
            "error_details": blockers,
            "warnings": warnings,
            "preflight": preflight,
        }

    if skip_already_queued:
        pending = _find_pending_marketplace_work(db, listing.id, market_key)
        if pending:
            return {
                "marketplace": market_key,
                "status": "SKIPPED_ALREADY_QUEUED",
                "task_id": None,
                "error": "Marketplace work is already pending.",
                "error_details": pending,
                "warnings": warnings,
                "preflight": preflight,
            }

    existing = (
        db.execute(
            select(MarketplaceListing)
            .where(
                MarketplaceListing.listing_id == listing.id,
                MarketplaceListing.marketplace == MarketplaceName(market_key),
            )
            .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        )
        .scalars()
        .first()
    )
    if not existing:
        existing = MarketplaceListing(
            listing_id=listing.id,
            marketplace=MarketplaceName(market_key),
            status=MarketplaceListingStatus.PENDING,
        )
        db.add(existing)
        db.flush()

    task = publish_listing_to_marketplace_task.delay(listing.id, market_key)
    return {"marketplace": market_key, "status": "QUEUED", "task_id": task.id, "warnings": warnings, "preflight": preflight}


def enqueue_crosspost_job(
    db: Session,
    *,
    listing: Listing,
    target_markets: list[str],
    requested_mode: str,
    execution_plan: dict | None = None,
) -> dict[str, object]:
    markets: list[str] = []
    seen: set[str] = set()
    for market in target_markets:
        market_key = str(market or "").strip().lower()
        if market_key and market_key in MarketplaceName._value2member_map_ and market_key not in seen:
            seen.add(market_key)
            markets.append(market_key)
    if not markets:
        return {"status": "failed", "error": "No supported target marketplaces were requested"}

    existing_job = (
        db.execute(
            select(MarketplaceCrosspostJob)
            .where(
                MarketplaceCrosspostJob.listing_id == listing.id,
                MarketplaceCrosspostJob.status.in_(["queued", "running"]),
            )
            .order_by(MarketplaceCrosspostJob.updated_at.desc(), MarketplaceCrosspostJob.id.desc())
        )
        .scalars()
        .first()
    )
    if existing_job:
        existing_targets = [str(target or "").strip().lower() for target in (existing_job.target_marketplaces or []) if str(target or "").strip()]
        missing = [market for market in markets if market not in existing_targets]
        if not missing:
            return {"status": "already_queued", "job_id": existing_job.id, "task_id": existing_job.task_id, "target_marketplaces": existing_targets}
        updated_targets = [*existing_targets, *missing]
        existing_job.target_marketplaces = updated_targets
        existing_job.execution_plan = {
            **(existing_job.execution_plan or {}),
            "target_marketplaces": updated_targets,
            "queued_from": requested_mode,
            "appended_targets": missing,
        }
        for market in missing:
            existing = (
                db.execute(
                    select(MarketplaceListing)
                    .where(
                        MarketplaceListing.listing_id == listing.id,
                        MarketplaceListing.marketplace == MarketplaceName(market),
                    )
                    .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
                )
                .scalars()
                .first()
            )
            if not existing:
                db.add(
                    MarketplaceListing(
                        listing_id=listing.id,
                        marketplace=MarketplaceName(market),
                        status=MarketplaceListingStatus.PENDING,
                        raw_response={"queued_by_crosspost_job": existing_job.id},
                    )
                )
        db.add(existing_job)
        db.commit()
        db.refresh(existing_job)
        return {"status": "queued", "job_id": existing_job.id, "task_id": existing_job.task_id, "target_marketplaces": updated_targets}

    job = MarketplaceCrosspostJob(
        user_id=listing.user_id,
        listing_id=listing.id,
        source_marketplace=((listing.marketplace_data or {}).get("source_marketplace") if isinstance(listing.marketplace_data, dict) else None),
        target_marketplaces=markets,
        requested_mode=requested_mode,
        status="queued",
        execution_plan=execution_plan or {"queued_from": requested_mode, "target_marketplaces": markets},
    )
    db.add(job)
    db.flush()

    for market in markets:
        existing = (
            db.execute(
                select(MarketplaceListing)
                .where(
                    MarketplaceListing.listing_id == listing.id,
                    MarketplaceListing.marketplace == MarketplaceName(market),
                )
                .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
            )
            .scalars()
            .first()
        )
        if not existing:
            db.add(
                MarketplaceListing(
                    listing_id=listing.id,
                    marketplace=MarketplaceName(market),
                    status=MarketplaceListingStatus.PENDING,
                    raw_response={"queued_by_crosspost_job": job.id},
                )
            )

    task = process_marketplace_crosspost_job_task.delay(job.id)
    job.task_id = task.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"status": "queued", "job_id": job.id, "task_id": task.id, "target_marketplaces": markets}


def queue_publish(db: Session, listing_id: int, marketplaces: list[str] | None) -> list[dict]:
    listing = db.get(Listing, listing_id)
    if not listing:
        raise ValueError("Listing not found")

    user = db.get(User, listing.user_id)
    targets = marketplaces or get_enabled_platforms(user)
    results: list[dict] = []

    for market in targets:
        market_key = market.lower()
        if _is_marketplace_already_published(db, listing, market_key):
            results.append({"marketplace": market_key, "status": "SKIPPED_ALREADY_PUBLISHED", "task_id": None})
            continue
        results.append(_queue_single_marketplace_publish(db, listing=listing, marketplace=market_key))

    db.commit()
    return results


def bulk_publish_ready(
    db: Session,
    listing_ids: list[int],
    marketplaces: list[str] | None,
    *,
    allow_warnings: bool = False,
    dry_run: bool = True,
    force_preflight_refresh: bool = False,
    skip_already_queued: bool = True,
) -> dict[str, object]:
    preflight_service = MarketplacePreflightService()
    target_markets: list[str] = []
    seen_markets: set[str] = set()
    for market in marketplaces or [MarketplaceName.ebay.value, MarketplaceName.facebook.value]:
        market_key = str(market or "").strip().lower()
        if market_key and market_key not in seen_markets:
            seen_markets.add(market_key)
            target_markets.append(market_key)

    listings = (
        db.execute(select(Listing).where(Listing.id.in_(listing_ids)).order_by(Listing.updated_at.desc(), Listing.id.desc()))
        .scalars()
        .all()
    )
    listing_by_id = {listing.id: listing for listing in listings}
    summary = {
        "queued": 0,
        "skipped_blocked": 0,
        "skipped_warning_requires_confirmation": 0,
        "skipped_unsupported_marketplace": 0,
        "skipped_already_queued": 0,
        "failed": 0,
        "dry_run_ready": 0,
        "dry_run_blocked": 0,
    }
    items: list[dict[str, object]] = []

    if not dry_run:
        for listing_id in listing_ids:
            listing = listing_by_id.get(listing_id)
            if not listing:
                items.append({"listing_id": listing_id, "status": "failed", "error": "Listing not found"})
                summary["failed"] += 1
                continue

            listing_item: dict[str, object] = {
                "listing_id": listing.id,
                "title": listing.title,
                "marketplaces": {},
                "ready_marketplaces": [],
                "blocked_marketplaces": [],
                "warning_marketplaces": [],
            }
            for market in target_markets:
                if market not in MarketplaceName._value2member_map_:
                    listing_item["marketplaces"][market] = {"marketplace": market, "status": "unsupported", "task_id": None, "error": f"Unsupported marketplace: {market}"}
                    summary["skipped_unsupported_marketplace"] += 1
                    continue
                preflight = None if force_preflight_refresh else preflight_service._cached_marketplace_preflight(listing, market)
                if preflight is None or force_preflight_refresh:
                    try:
                        preflight = preflight_service.preflight_listing(db, listing, market)
                        preflight_service.cache_preflight_summary(db, listing, preflight)
                    except Exception as exc:
                        translated = translate_marketplace_error(market, exc)
                        listing_item["marketplaces"][market] = {
                            "marketplace": market,
                            "status": "failed",
                            "task_id": None,
                            "error": translated["user_message"],
                            "error_details": [translated],
                            "preflight": None,
                        }
                        summary["failed"] += 1
                        continue

                blockers = preflight.get("blockers") or []
                warnings = preflight.get("warnings") or []
                status_value = str(preflight.get("status") or "").lower()
                ready_like = status_value in {"ready", "ready_with_warnings", "published"}
                blocked_like = bool(blockers)
                warnings_need_confirmation = bool(warnings) and not allow_warnings and not blocked_like

                if not ready_like and not blocked_like:
                    listing_item["blocked_marketplaces"].append(market)
                    summary["skipped_blocked"] += 1
                    summary["dry_run_blocked"] += int(dry_run)
                    result = {
                        "marketplace": market,
                        "status": "dry_run_blocked" if dry_run else "skipped_blocked",
                        "task_id": None,
                        "error": str(preflight.get("status") or "Listing is not ready to publish."),
                        "error_details": preflight.get("blockers") or [],
                        "warnings": warnings,
                        "preflight": preflight,
                    }
                elif blocked_like:
                    listing_item["blocked_marketplaces"].append(market)
                    summary["skipped_blocked"] += 1
                    summary["dry_run_blocked"] += int(dry_run)
                    result = {
                        "marketplace": market,
                        "status": "dry_run_blocked" if dry_run else "skipped_blocked",
                        "task_id": None,
                        "error": "; ".join(issue.get("message") or "Publish blocked" for issue in blockers),
                        "error_details": blockers,
                        "warnings": warnings,
                        "preflight": preflight,
                    }
                elif warnings_need_confirmation:
                    listing_item["warning_marketplaces"].append(market)
                    summary["skipped_warning_requires_confirmation"] += 1
                    result = {
                        "marketplace": market,
                        "status": "dry_run_warning_requires_confirmation" if dry_run else "skipped_warning_requires_confirmation",
                        "task_id": None,
                        "error": "Warnings present; live queue requires allow_warnings=true.",
                        "error_details": warnings,
                        "warnings": warnings,
                        "preflight": preflight,
                    }
                elif dry_run:
                    listing_item["ready_marketplaces"].append(market)
                    if warnings:
                        listing_item["warning_marketplaces"].append(market)
                    summary["dry_run_ready"] += 1
                    result = {
                        "marketplace": market,
                        "status": "dry_run_ready_with_warnings" if warnings else "dry_run_ready",
                        "task_id": None,
                        "warnings": warnings,
                        "preflight": preflight,
                    }
                else:
                    if skip_already_queued:
                        pending = _find_pending_marketplace_work(db, listing.id, market)
                        if pending:
                            summary["skipped_already_queued"] += 1
                            result = {
                                "marketplace": market,
                                "status": "skipped_already_queued",
                                "task_id": None,
                                "error": "Marketplace work is already pending.",
                                "error_details": pending,
                                "warnings": warnings,
                                "preflight": preflight,
                            }
                            listing_item["marketplaces"][market] = result
                            continue
                    queued = enqueue_crosspost_job(
                        db,
                        listing=listing,
                        target_markets=[market],
                        requested_mode="bulk_publish_ready",
                        execution_plan={"queued_from": "bulk_publish_ready", "dry_run": False, "target_marketplaces": [market], "preflight_status": preflight.get("status")},
                    )
                    result = {
                        "marketplace": market,
                        "status": str(queued.get("status") or "queued"),
                        "task_id": queued.get("task_id"),
                        "job_id": queued.get("job_id"),
                        "error": queued.get("error"),
                        "warnings": warnings,
                        "preflight": preflight,
                    }
                    if str(result.get("status") or "").lower() == "queued":
                        summary["queued"] += 1
                        listing_item["ready_marketplaces"].append(market)
                        if warnings:
                            listing_item["warning_marketplaces"].append(market)
                    elif str(result.get("status") or "").lower() == "already_queued":
                        summary["skipped_already_queued"] += 1
                    else:
                        summary["failed"] += 1

                listing_item["marketplaces"][market] = result

            items.append(listing_item)

        db.commit()
        return {
            "items": items,
            "summary": summary,
            "markets": target_markets,
            "dry_run": dry_run,
            "allow_warnings": allow_warnings,
            "skip_already_queued": skip_already_queued,
            "generated_at": datetime.now(UTC),
        }

    for listing_id in listing_ids:
        listing = listing_by_id.get(listing_id)
        if not listing:
            items.append({"listing_id": listing_id, "status": "failed", "error": "Listing not found"})
            summary["failed"] += 1
            continue

        listing_item: dict[str, object] = {
            "listing_id": listing.id,
            "title": listing.title,
            "marketplaces": {},
            "ready_marketplaces": [],
            "blocked_marketplaces": [],
            "warning_marketplaces": [],
        }

        for market in target_markets:
            if market not in MarketplaceName._value2member_map_:
                listing_item["marketplaces"][market] = {"marketplace": market, "status": "unsupported", "task_id": None, "error": f"Unsupported marketplace: {market}"}
                summary["skipped_unsupported_marketplace"] += 1
                continue

            preflight = None if force_preflight_refresh else preflight_service._cached_marketplace_preflight(listing, market)
            if preflight is None or force_preflight_refresh:
                try:
                    preflight = preflight_service.preflight_listing(db, listing, market)
                    preflight_service.cache_preflight_summary(db, listing, preflight)
                except Exception as exc:
                    translated = translate_marketplace_error(market, exc)
                    listing_item["marketplaces"][market] = {
                        "marketplace": market,
                        "status": "failed",
                        "task_id": None,
                        "error": translated["user_message"],
                        "error_details": [translated],
                        "preflight": None,
                    }
                    summary["failed"] += 1
                    continue

            blockers = preflight.get("blockers") or []
            warnings = preflight.get("warnings") or []
            status_value = str(preflight.get("status") or "").lower()
            ready_like = status_value in {"ready", "ready_with_warnings", "published"}
            blocked_like = bool(blockers)
            warnings_need_confirmation = bool(warnings) and not allow_warnings and not blocked_like

            if not ready_like and not blocked_like:
                listing_item["blocked_marketplaces"].append(market)
                summary["skipped_blocked"] += 1
                summary["dry_run_blocked"] += 1
                result = {
                    "marketplace": market,
                    "status": "dry_run_blocked",
                    "task_id": None,
                    "error": str(preflight.get("status") or "Listing is not ready to publish."),
                    "error_details": preflight.get("blockers") or [],
                    "warnings": warnings,
                    "preflight": preflight,
                }
            elif blocked_like:
                listing_item["blocked_marketplaces"].append(market)
                summary["skipped_blocked"] += 1
                summary["dry_run_blocked"] += 1
                result = {
                    "marketplace": market,
                    "status": "dry_run_blocked",
                    "task_id": None,
                    "error": "; ".join(issue.get("message") or "Publish blocked" for issue in blockers),
                    "error_details": blockers,
                    "warnings": warnings,
                    "preflight": preflight,
                }
            elif warnings_need_confirmation:
                listing_item["warning_marketplaces"].append(market)
                summary["skipped_warning_requires_confirmation"] += 1
                result = {
                    "marketplace": market,
                    "status": "dry_run_warning_requires_confirmation",
                    "task_id": None,
                    "error": "Warnings present; live queue requires allow_warnings=true.",
                    "error_details": warnings,
                    "warnings": warnings,
                    "preflight": preflight,
                }
            else:
                listing_item["ready_marketplaces"].append(market)
                if warnings:
                    listing_item["warning_marketplaces"].append(market)
                summary["dry_run_ready"] += 1
                result = {
                    "marketplace": market,
                    "status": "dry_run_ready_with_warnings" if warnings else "dry_run_ready",
                    "task_id": None,
                    "warnings": warnings,
                    "preflight": preflight,
                }

            listing_item["marketplaces"][market] = result

        items.append(listing_item)

    db.commit()
    return {
        "items": items,
        "summary": summary,
        "markets": target_markets,
        "dry_run": dry_run,
        "allow_warnings": allow_warnings,
        "skip_already_queued": skip_already_queued,
        "generated_at": datetime.now(UTC),
    }


def listing_marketplace_status(db: Session, listing_id: int) -> list[MarketplaceListing]:
    return db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing_id)).scalars().all()


def trigger_sync_sold(listing_ids: list[int] | None) -> dict:
    task = sync_sold_everywhere_task.delay(listing_ids or [])
    return {"task_id": task.id, "status": "QUEUED"}
