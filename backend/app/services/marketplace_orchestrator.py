from __future__ import annotations

from datetime import UTC, datetime
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MARKETPLACE_DESTINATION_VALUES, ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceListing, MarketplacePublishBatch, User
from app.services.multi_platform_publisher import get_enabled_platforms
from app.services.marketplace_preflight import MarketplacePreflightService, compute_payload_hash
from app.services.marketplace_field_mapper import build_marketplace_payload
from app.services.canonical_readiness import canonical_listing_readiness
from app.services.marketplace_error_translation import translate_marketplace_error
from app.workers.tasks import process_marketplace_crosspost_job_task, publish_listing_to_marketplace_task, sync_sold_everywhere_task


def _json_safe(value):
    """Convert preflight/execution metadata to JSON before storing it in JSON columns."""
    return json.loads(json.dumps(value, default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item)))


def _current_pipeline_readiness_block(db: Session, listing: Listing, marketplace: str) -> dict | None:
    """Return a destination blocker for rows produced by the current pipeline.

    Old imported records may not have source/processing metadata and continue
    through their established preflight compatibility path. New intake rows do
    carry both fields, so queueing cannot bypass canonical readiness.
    """
    if not str(getattr(listing, "source_type", "") or "").strip() or not str(getattr(listing, "processing_state", "") or "").strip():
        return None
    evaluation = MarketplacePreflightService().evaluate_publishability(db, listing, marketplace)
    if evaluation.get("publishable"):
        return None
    return evaluation.get("canonical") or {
        "publishable": False,
        "blocking_reasons": [issue.get("message") for issue in (evaluation.get("blockers") or [])],
    }


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
        {"name": MarketplaceName.offerup.value, "supports_oauth": False},
    ]


def _find_pending_marketplace_work(db: Session, listing_id: int, marketplace: str) -> dict[str, object] | None:
    market = str(marketplace or "").strip().lower()
    if not market or market not in MARKETPLACE_DESTINATION_VALUES:
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
    if market_key not in MARKETPLACE_DESTINATION_VALUES:
        return {"marketplace": market_key, "status": "UNSUPPORTED", "task_id": None}

    preflight_service = MarketplacePreflightService()
    preflight = preflight or preflight_service.preflight_listing(db, listing, market_key)
    blockers = preflight.get("blockers") or []
    warnings = preflight.get("warnings") or []
    # Publish must honor the same processing/readiness contract as the queue.
    # Legacy rows without a processing_state remain governed by marketplace
    # preflight; explicit in-flight/blocked states may never be queued merely
    # because a provider preflight happened to return no field errors.
    processing_state = str(getattr(listing, "processing_state", "") or "").strip().lower()
    if processing_state and processing_state not in {"complete", "completed", "ready"}:
        canonical = canonical_listing_readiness(listing, marketplace=market_key)
        blockers = [*blockers, *(canonical.get("blocking_reasons") or []), "Listing processing is not complete"]
    if blockers:
        blocker_messages = [
            str(issue.get("message") or issue.get("code") or "Publish blocked") if isinstance(issue, dict) else str(issue)
            for issue in blockers
        ]
        return {
            "marketplace": market_key,
            "status": "BLOCKED",
            "task_id": None,
            "error": "; ".join(blocker_messages),
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
    batch_id: str | None = None,
) -> dict[str, object]:
    markets: list[str] = []
    seen: set[str] = set()
    for market in target_markets:
        market_key = str(market or "").strip().lower()
        if market_key and market_key in MARKETPLACE_DESTINATION_VALUES and market_key not in seen:
            seen.add(market_key)
            markets.append(market_key)
    if not markets:
        return {"status": "failed", "error": "No supported target marketplaces were requested"}

    # A queued job must carry a destination-specific publish contract.  Older
    # callers used one unscoped `preflight`/payload field, which made a
    # multi-market job capable of applying eBay readiness to Facebook.  Fill
    # missing contracts here as the last shared queue guard; no caller can
    # create a doomed job merely by bypassing a UI endpoint.
    execution_plan = dict(execution_plan or {})
    supplied_preflights = execution_plan.get("preflight_by_marketplace")
    supplied_payloads = execution_plan.get("approved_execution_payload_by_marketplace")
    supplied_hashes = execution_plan.get("preflight_payload_hash_by_marketplace")
    preflight_by_marketplace = dict(supplied_preflights) if isinstance(supplied_preflights, dict) else {}
    payload_by_marketplace = dict(supplied_payloads) if isinstance(supplied_payloads, dict) else {}
    hash_by_marketplace = dict(supplied_hashes) if isinstance(supplied_hashes, dict) else {}
    if len(markets) == 1:
        market = markets[0]
        if market not in preflight_by_marketplace and isinstance(execution_plan.get("preflight"), dict):
            preflight_by_marketplace[market] = execution_plan["preflight"]
        if market not in payload_by_marketplace and isinstance(execution_plan.get("approved_execution_payload"), dict):
            payload_by_marketplace[market] = execution_plan["approved_execution_payload"]
        if market not in hash_by_marketplace and execution_plan.get("preflight_payload_hash"):
            hash_by_marketplace[market] = execution_plan["preflight_payload_hash"]

    preflight_service = MarketplacePreflightService()
    blocked_destinations: dict[str, dict] = {}
    for market in markets:
        preflight = preflight_by_marketplace.get(market)
        if not isinstance(preflight, dict) or not preflight.get("status"):
            preflight = preflight_service.preflight_listing(db, listing, market)
        preflight_by_marketplace[market] = preflight
        if preflight.get("blockers") or str(preflight.get("status") or "").lower() not in {"ready", "ready_with_warnings", "published"}:
            blocked_destinations[market] = _json_safe(preflight)
            continue
        payload = payload_by_marketplace.get(market)
        if not isinstance(payload, dict):
            payload = build_marketplace_payload(listing, market)
        payload_by_marketplace[market] = payload
        hash_by_marketplace[market] = hash_by_marketplace.get(market) or compute_payload_hash(payload)
    if blocked_destinations:
        return {
            "status": "blocked",
            "error": "No cross-post job was queued because one or more destinations are not publishable.",
            "destination_readiness": _json_safe(preflight_by_marketplace),
            "blocked_destinations": blocked_destinations,
            "target_marketplaces": markets,
        }

    execution_plan.update({
        "target_marketplaces": markets,
        "preflight_by_marketplace": _json_safe(preflight_by_marketplace),
        "approved_execution_payload_by_marketplace": _json_safe(payload_by_marketplace),
        "preflight_payload_hash_by_marketplace": _json_safe(hash_by_marketplace),
    })
    # Preflight objects contain timezone-aware datetimes (for example generated_at
    # and checked_at).  SQLAlchemy's JSON serializer rejects those at flush time.
    execution_plan = _json_safe(execution_plan)

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
            return {"status": "already_queued", "job_id": existing_job.id, "task_id": existing_job.task_id, "target_marketplaces": existing_targets, "marketplace": markets[0] if len(markets) == 1 else None}
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
        if batch_id:
            recompute_publish_batch(db, batch_id)
        db.commit()
        db.refresh(existing_job)
        return {"status": "queued", "job_id": existing_job.id, "task_id": existing_job.task_id, "target_marketplaces": updated_targets, "marketplace": markets[0] if len(markets) == 1 else None}

    job = MarketplaceCrosspostJob(
        user_id=listing.user_id,
        listing_id=listing.id,
        source_marketplace=((listing.marketplace_data or {}).get("source_marketplace") if isinstance(listing.marketplace_data, dict) else None),
        target_marketplaces=markets,
        requested_mode=requested_mode,
        status="queued",
        # Live publication is revenue-critical.  Keep it ahead of ordinary
        # imports/maintenance work unless a future caller explicitly adds a
        # lower-priority queue API.
        priority=0,
        execution_plan=execution_plan or {"queued_from": requested_mode, "target_marketplaces": markets},
        batch_id=batch_id,
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

    # Celery broker priority 0 is highest, matching PosterPro's priority
    # contract (operator publish work uses 0).
    task = process_marketplace_crosspost_job_task.apply_async(
        args=[job.id], priority=max(0, min(10, int(getattr(job, "priority", 1) or 1)))
    )
    job.task_id = task.id
    db.add(job)
    if batch_id:
        recompute_publish_batch(db, batch_id)
    db.commit()
    db.refresh(job)
    return {"status": "queued", "job_id": job.id, "task_id": task.id, "target_marketplaces": markets, "marketplace": markets[0] if len(markets) == 1 else None}


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
    owner_user_id: int | None = None,
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
    if not listing_ids or not listings or len({listing.user_id for listing in listings}) != 1 or len(listings) != len(set(listing_ids)) or (owner_user_id is not None and any(listing.user_id != owner_user_id for listing in listings)):
        raise ValueError("Bulk publish requires a non-empty selection of listings owned by one tenant")
    listing_by_id = {listing.id: listing for listing in listings}
    owner_id = listings[0].user_id
    batch_id = f"bulk-{uuid.uuid4().hex}"
    db.add(MarketplacePublishBatch(
        id=batch_id,
        user_id=owner_id,
        selected_count=len(listing_ids),
        selected_listing_ids=list(listing_ids),
        requested_marketplaces=target_markets,
        status="preflighting",
    ))
    summary = {
        "selected": len(listing_ids),
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
                "batch_id": batch_id,
                "marketplaces": {},
                "ready_marketplaces": [],
                "blocked_marketplaces": [],
                "warning_marketplaces": [],
            }
            for market in target_markets:
                if market not in MARKETPLACE_DESTINATION_VALUES:
                    listing_item["marketplaces"][market] = {"marketplace": market, "status": "unsupported", "task_id": None, "error": f"Unsupported marketplace: {market}"}
                    summary["skipped_unsupported_marketplace"] += 1
                    continue
                canonical_block = _current_pipeline_readiness_block(db, listing, market)
                if canonical_block:
                    listing_item["blocked_marketplaces"].append(market)
                    summary["skipped_blocked"] += 1
                    result = {"marketplace": market, "status": "skipped_blocked", "task_id": None, "error": "Listing is not ready to publish.", "error_details": canonical_block.get("blocking_reasons") or [], "canonical_readiness": canonical_block}
                    listing_item["marketplaces"][market] = result
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
                        execution_plan={"queued_from": "bulk_publish_ready", "bulk_batch_id": batch_id, "selected_listing_ids": list(listing_ids), "dry_run": False, "target_marketplaces": [market], "preflight_status": preflight.get("status")},
                        batch_id=batch_id,
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
            "batch_id": batch_id,
            "selected_listing_ids": list(listing_ids),
            "selected_count": len(listing_ids),
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
            "batch_id": batch_id,
            "marketplaces": {},
            "ready_marketplaces": [],
            "blocked_marketplaces": [],
            "warning_marketplaces": [],
        }

        for market in target_markets:
            if market not in MARKETPLACE_DESTINATION_VALUES:
                listing_item["marketplaces"][market] = {"marketplace": market, "status": "unsupported", "task_id": None, "error": f"Unsupported marketplace: {market}"}
                summary["skipped_unsupported_marketplace"] += 1
                continue

            canonical_block = _current_pipeline_readiness_block(db, listing, market)
            if canonical_block:
                listing_item["blocked_marketplaces"].append(market)
                summary["skipped_blocked"] += 1
                result = {"marketplace": market, "status": "dry_run_blocked" if dry_run else "skipped_blocked", "task_id": None, "error": "Listing is not ready to publish.", "error_details": canonical_block.get("blocking_reasons") or [], "canonical_readiness": canonical_block}
                listing_item["marketplaces"][market] = result
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
        "batch_id": batch_id,
        "selected_listing_ids": list(listing_ids),
        "selected_count": len(listing_ids),
        "items": items,
        "summary": summary,
        "markets": target_markets,
        "dry_run": dry_run,
        "allow_warnings": allow_warnings,
        "skip_already_queued": skip_already_queued,
        "generated_at": datetime.now(UTC),
    }


def recompute_publish_batch(db: Session, batch_id: str) -> MarketplacePublishBatch | None:
    """Refresh a durable batch summary from its child jobs without side effects."""
    batch = db.get(MarketplacePublishBatch, batch_id)
    if not batch:
        return None
    jobs = db.execute(select(MarketplaceCrosspostJob).where(MarketplaceCrosspostJob.batch_id == batch_id)).scalars().all()
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "blocked": 0, "canceled": 0, "skipped": 0}
    by_marketplace: dict[str, dict[str, int]] = {}
    for job in jobs:
        status = str(job.status or "").lower()
        normalized = {
            "processing": "running",
            "in_progress": "running",
            "retrying": "queued",
            "needs_review": "blocked",
            "completed_with_errors": "failed",
            "cancelled": "canceled",
            "already_published": "skipped",
            "already_queued": "skipped",
        }.get(status, status)
        if normalized in counts:
            counts[normalized] += 1
        elif status in {"already_published", "already_queued"}:
            counts["skipped"] += 1
        for market in (job.target_marketplaces or []):
            market_key = str(market or "").strip().lower()
            if not market_key:
                continue
            market_counts = by_marketplace.setdefault(market_key, {key: 0 for key in counts})
            if normalized in market_counts:
                market_counts[normalized] += 1
    selected = int(batch.selected_count or 0)
    if not jobs:
        state = batch.status or "preflighting"
    elif counts["failed"] and counts["completed"]:
        state = "partially_completed"
    elif counts["blocked"] and (counts["completed"] or counts["failed"]):
        state = "partially_failed"
    elif counts["failed"]:
        state = "failed"
    elif counts["completed"] and counts["completed"] == len(jobs):
        state = "completed"
    elif counts["running"]:
        state = "running"
    elif counts["queued"]:
        state = "queued"
    else:
        state = "blocked"
    if str(batch.operation or "") == "unpublish_and_archive" and batch.selected_listing_ids:
        archive_states = []
        for selected_id in batch.selected_listing_ids:
            row = db.get(Listing, int(selected_id))
            data = row.marketplace_data if row and isinstance(row.marketplace_data, dict) else {}
            archive_states.append(str((data.get("archive_state") or {}).get("status") or "REMOTE_STATE_UNKNOWN"))
        if archive_states and all(value == "ARCHIVED" for value in archive_states):
            state = "completed"
        elif any(value == "UNPUBLISHING" for value in archive_states):
            state = "running"
        elif any(value in {"PARTIALLY_UNPUBLISHED", "REMOTE_STATE_UNKNOWN", "ARCHIVE_FAILED"} for value in archive_states):
            state = "partially_failed"
    batch.status = state
    requested_markets = [str(m).strip().lower() for m in (batch.requested_marketplaces or []) if str(m).strip()]
    expected_destinations = selected * len(set(requested_markets))
    batch.summary = {
        **(batch.summary or {}),
        **counts,
        "child_job_count": len(jobs),
        "selected_count": selected,
        "requested_marketplaces": requested_markets,
        "expected_destination_operations": expected_destinations,
        "actual_destination_operations": sum(len(job.target_marketplaces or []) for job in jobs),
        "missing_destination_operations": max(0, expected_destinations - sum(len(job.target_marketplaces or []) for job in jobs)),
        "by_marketplace": by_marketplace,
    }
    if str(batch.operation or "") == "unpublish_and_archive":
        states = (batch.summary or {}).get("listing_states") or {}
        batch.summary["remote_state_unknown_count"] = sum(value == "REMOTE_STATE_UNKNOWN" for value in states.values())
        batch.summary["partial_count"] = sum(value == "PARTIALLY_UNPUBLISHED" for value in states.values())
        batch.summary["archived_listings"] = sum(value == "ARCHIVED" for value in states.values())
    db.add(batch)
    return batch


def finalize_unpublish_archive_listing(db: Session, batch_id: str, listing_id: int) -> dict:
    """Aggregate every destination END outcome before changing local archive state."""
    batch = db.get(MarketplacePublishBatch, batch_id)
    listing = db.get(Listing, listing_id)
    if not batch or not listing:
        return {"status": "missing"}
    if listing.sold_at or str(getattr(listing.status, "value", listing.status) or "").lower() in {"sold", "closed"}:
        return {"status": "sold"}
    jobs = db.execute(
        select(MarketplaceCrosspostJob).where(
            MarketplaceCrosspostJob.batch_id == batch_id,
            MarketplaceCrosspostJob.listing_id == listing_id,
        )
    ).scalars().all()
    # Only provider-semantic terminal states prove that the remote identity is
    # no longer live.  Generic worker/job completion is deliberately excluded.
    confirmed = {"ended", "already_ended", "closed", "not_found", "withdrawn", "deactivated", "verified_not_live"}
    outcomes: list[dict] = []
    for job in jobs:
        market = str((job.target_marketplaces or [None])[0] or "").lower()
        result = ((job.result_summary or {}).get("results") or [{}])[0]
        result_status = str(result.get("status") or "").lower()
        job_status = str(job.status or "").lower()
        if result_status in confirmed:
            state = "CONFIRMED_NOT_LIVE"
        elif job_status in {"queued", "running"}:
            state = "RUNNING"
        elif job_status == "blocked" and any("REMOTE_STATE_UNKNOWN" in str(value) for value in (job.execution_plan or {}).values()):
            state = "REMOTE_STATE_UNKNOWN"
        elif job_status in {"failed", "blocked"}:
            state = "FAILED"
        else:
            state = "REMOTE_STATE_UNKNOWN"
        outcomes.append({"marketplace": market, "state": state, "job_id": job.id, "result": result, "remote_id": (job.execution_plan or {}).get("external_listing_id")})
    archive_data = dict(listing.marketplace_data or {}) if isinstance(listing.marketplace_data, dict) else {}
    if not outcomes:
        state = "REMOTE_STATE_UNKNOWN"
    elif all(item["state"] == "CONFIRMED_NOT_LIVE" for item in outcomes):
        state = "ARCHIVED"
    elif any(item["state"] == "REMOTE_STATE_UNKNOWN" for item in outcomes):
        state = "REMOTE_STATE_UNKNOWN"
    elif any(item["state"] == "RUNNING" for item in outcomes):
        state = "UNPUBLISHING"
    elif any(item["state"] == "CONFIRMED_NOT_LIVE" for item in outcomes):
        state = "PARTIALLY_UNPUBLISHED"
    else:
        state = "ARCHIVE_FAILED"
    archive_data["archive_state"] = {"status": state, "operation": batch_id, "destinations": outcomes}
    listing.marketplace_data = archive_data
    if state == "ARCHIVED":
        listing.custom_labels = list(dict.fromkeys([*(listing.custom_labels or []), "archived_vine"]))
        listing.status = ListingStatus.archived
        listing.needs_review = False
    else:
        listing.processing_state = state.lower()
        listing.needs_review = False
    db.add(listing)
    if str(batch.operation or "") == "unpublish_and_archive":
        selected = list(batch.selected_listing_ids or [])
        states = {}
        for selected_id in selected:
            row = db.get(Listing, int(selected_id))
            data = row.marketplace_data if row and isinstance(row.marketplace_data, dict) else {}
            states[int(selected_id)] = str((data.get("archive_state") or {}).get("status") or "REMOTE_STATE_UNKNOWN")
        batch.summary = {**(batch.summary or {}), "archived_listings": sum(value == "ARCHIVED" for value in states.values()), "partially_unpublished": sum(value == "PARTIALLY_UNPUBLISHED" for value in states.values()), "remote_state_unknown": sum(value == "REMOTE_STATE_UNKNOWN" for value in states.values()), "archive_failed": sum(value == "ARCHIVE_FAILED" for value in states.values()), "unpublishing": sum(value == "UNPUBLISHING" for value in states.values()), "listing_states": states}
        if states and all(value == "ARCHIVED" for value in states.values()):
            batch.status = "completed"
        elif any(value in {"UNPUBLISHING"} for value in states.values()):
            batch.status = "running"
        elif any(value in {"PARTIALLY_UNPUBLISHED", "REMOTE_STATE_UNKNOWN", "ARCHIVE_FAILED"} for value in states.values()):
            batch.status = "partially_failed"
        db.add(batch)
    return {"status": state, "destinations": outcomes}


def listing_marketplace_status(db: Session, listing_id: int) -> list[MarketplaceListing]:
    return db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing_id)).scalars().all()


def trigger_sync_sold(listing_ids: list[int] | None) -> dict:
    task = sync_sold_everywhere_task.delay(listing_ids or [])
    return {"task_id": task.id, "status": "QUEUED"}
