from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
from datetime import datetime, timedelta, UTC
from pathlib import Path

from celery import chord, group
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import (
    BulkJob,
    Cluster,
    Image,
    Listing,
    MarketplaceAccount,
    MarketplaceCrosspostJob,
    MarketplaceImportJob,
    MarketplaceListing,
    StorageUnitBatch,
    User,
)
from app.services.listing_workspace import normalize_marketplace_data
from app.services.marketplace_execution import resolve_execution_mode
from app.services.marketplace_field_mapper import build_marketplace_payload, normalize_import_payload
from app.services.automation_bridge import submit_bridge_job, wait_for_bridge_job, get_bridge_asset, AutomationBridgeError
from app.services.secondary_marketplace_execution import execute_secondary_marketplace_path
from app.services.inventory_service import InventorySafetyError, InventoryService
from app.services.storage import LocalStorage
from app.services.analytics_service import AnalyticsService
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.photo_enrichment import PhotoEnrichmentService
from app.services.pricing_service import PricingService
from app.services.multi_platform_publisher import get_enabled_platforms, multi_platform_publisher, upsert_marketplace_listing
from app.services.offer_service import OfferService
from app.services.sale_detection_service import SaleDetectionService
from app.workers.celery_app import celery_app
from app.services.clustering import cluster_embeddings
from app.services.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

sale_detection_service = SaleDetectionService()
inventory_service = InventoryService()


def _crosspost_job_canceled(db, job_id: int) -> bool:
    job = db.get(MarketplaceCrosspostJob, job_id)
    return bool(job and str(job.status).lower() == "canceled")


def _import_job_canceled(db, job_id: int) -> bool:
    job = db.get(MarketplaceImportJob, job_id)
    return bool(job and str(job.status).lower() == "canceled")


def _best_effort_localize_import_images(image_urls: list[str] | None) -> tuple[list[str], list[str]]:
    urls = [str(url).strip() for url in (image_urls or []) if str(url).strip()]
    if not urls:
        return [], []

    storage = LocalStorage()
    localized: list[str] = []
    failures: list[str] = []
    for url in urls:
        if not url.lower().startswith(("http://", "https://")):
            if url not in localized:
                localized.append(url)
            continue
        try:
            saved = storage.save_from_url(url, prefix="marketplace_imports")
        except Exception:
            failures.append(url)
            saved = url
        if saved not in localized:
            localized.append(saved)
    return localized, failures


def _disposition_filename(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None
    match = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip() or None


def _bridge_asset_extension(file_name: str | None, content_type: str | None) -> str:
    if file_name:
        suffix = Path(file_name).suffix
        if suffix:
            return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed == ".jpe":
        return ".jpg"
    return guessed or ".jpg"


def _best_effort_localize_bridge_assets(image_assets: list[dict[str, Any]] | None) -> tuple[list[str], list[str]]:
    assets = [item for item in (image_assets or []) if isinstance(item, dict)]
    if not assets:
        return [], []

    storage = LocalStorage()
    localized: list[str] = []
    failures: list[str] = []
    for asset in assets:
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id:
            continue
        try:
            content, content_type, content_disposition = get_bridge_asset(asset_id)
            file_name = str(asset.get("file_name") or "").strip() or _disposition_filename(content_disposition)
            saved = storage.save_bytes(
                content,
                extension=_bridge_asset_extension(file_name, content_type or str(asset.get("content_type") or "")),
                prefix="marketplace_imports",
            )
            localized.append(saved)
        except Exception:
            failures.append(asset_id)
    return localized, failures


def _find_existing_imported_listing(*, db, user_id: int, source_marketplace: str, source_listing_reference: str | None) -> Listing | None:
    reference = str(source_listing_reference or "").strip()
    if not reference:
        return None

    candidates = db.execute(
        select(Listing).where(
            Listing.user_id == user_id,
            Listing.source_type == f"{source_marketplace}_import",
        )
    ).scalars().all()
    for candidate in candidates:
        source_metadata = candidate.source_metadata or {}
        raw_payload = source_metadata.get("raw_payload") if isinstance(source_metadata, dict) else {}
        known_references = {
            reference,
            str(source_metadata.get("source_listing_reference") or "").strip(),
            str(raw_payload.get("source_listing_reference") or "").strip() if isinstance(raw_payload, dict) else "",
            str(raw_payload.get("source_url") or "").strip() if isinstance(raw_payload, dict) else "",
        }
        known_references.discard("")
        if reference in known_references:
            return candidate
    return None


def _create_imported_listing(
    *,
    db,
    user_id: int,
    source_marketplace: str,
    import_job_id: int,
    import_mode: str,
    source_listing_reference: str | None,
    raw_payload: dict,
    normalized: dict,
) -> tuple[Listing, bool]:
    raw_payload = raw_payload if isinstance(raw_payload, dict) else {}
    bridge_asset_images, bridge_asset_failures = _best_effort_localize_bridge_assets(raw_payload.get("image_assets"))
    localized_images = bridge_asset_images
    image_import_failures = bridge_asset_failures
    if not localized_images:
        localized_images, image_import_failures = _best_effort_localize_import_images(normalized.get("image_urls") or [])
    normalized_reference = str(source_listing_reference or "").strip() or None
    source_metadata = {
        "import_job_id": import_job_id,
        "source_marketplace": source_marketplace,
        "source_listing_reference": normalized_reference,
        "import_mode": import_mode,
        "raw_payload": raw_payload,
        "original_image_urls": normalized.get("image_urls") or [],
    }
    if raw_payload.get("image_assets"):
        source_metadata["bridge_image_assets"] = raw_payload.get("image_assets")
    if image_import_failures:
        source_metadata["image_import_failures"] = image_import_failures

    existing_listing = _find_existing_imported_listing(
        db=db,
        user_id=user_id,
        source_marketplace=source_marketplace,
        source_listing_reference=normalized_reference
        or (raw_payload.get("source_listing_reference") if isinstance(raw_payload, dict) else None)
        or (raw_payload.get("source_url") if isinstance(raw_payload, dict) else None),
    )
    default_targets = [MarketplaceName.ebay.value]
    if source_marketplace.strip().lower() == MarketplaceName.amazon.value:
        default_targets.append(MarketplaceName.amazon.value)
    marketplace_data = normalize_marketplace_data(
        {
            "source_marketplace": source_marketplace,
            "manual_entry": False,
            "targets": default_targets,
        }
    )
    if existing_listing:
        if not existing_listing.title:
            existing_listing.title = normalized.get("title") or None
        if not existing_listing.description:
            existing_listing.description = normalized.get("description") or None
        if not existing_listing.category_id:
            existing_listing.category_id = normalized.get("category_id") or None
        if not existing_listing.condition:
            existing_listing.condition = normalized.get("condition") or None
        if existing_listing.listing_price is None:
            existing_listing.listing_price = normalized.get("listing_price")
        if not existing_listing.image_urls and localized_images:
            existing_listing.image_urls = localized_images
        if not existing_listing.item_specifics:
            existing_listing.item_specifics = normalized.get("item_specifics") or {}
        if not existing_listing.tags:
            existing_listing.tags = normalized.get("tags") or []
        if not existing_listing.quantity:
            existing_listing.quantity = int(normalized.get("quantity") or 1)
        existing_listing.source_metadata = source_metadata
        existing_listing.marketplace_data = existing_listing.marketplace_data or marketplace_data
        existing_listing.needs_review = True
        db.add(existing_listing)
        db.flush()
        return existing_listing, False

    listing = Listing(
        user_id=user_id,
        status=ListingStatus.draft,
        title=normalized.get("title") or None,
        description=normalized.get("description") or None,
        category_id=normalized.get("category_id") or None,
        condition=normalized.get("condition") or None,
        listing_price=normalized.get("listing_price"),
        quantity=int(normalized.get("quantity") or 1),
        image_urls=localized_images,
        item_specifics=normalized.get("item_specifics") or {},
        tags=normalized.get("tags") or [],
        source_type=f"{source_marketplace}_import",
        source_metadata=source_metadata,
        marketplace_data=marketplace_data,
        needs_review=True,
    )
    db.add(listing)
    db.flush()
    return listing, True


def _extract_end_time_iso(marketplace_data: dict | None) -> str | None:
    if not marketplace_data:
        return None
    publish = marketplace_data.get("publish") or {}
    return (
        publish.get("endTime")
        or publish.get("listingEndDate")
        or publish.get("listingEndTime")
        or marketplace_data.get("ebay_end_time")
    )


def _extract_quantity(marketplace_data: dict | None) -> int | None:
    if not marketplace_data:
        return None
    item = marketplace_data.get("item") or {}
    availability = item.get("availability") or {}
    ship_to = availability.get("shipToLocationAvailability") or {}
    quantity = ship_to.get("quantity", marketplace_data.get("quantity"))
    if quantity is None:
        return None
    try:
        return int(quantity)
    except (TypeError, ValueError):
        return None


def _get_user_relist_min_price(user_id: int) -> float:
    if not settings.auto_relist_user_rules_json:
        return settings.auto_relist_min_price
    try:
        rule_map = json.loads(settings.auto_relist_user_rules_json)
        if not isinstance(rule_map, dict):
            return settings.auto_relist_min_price
        user_value = rule_map.get(str(user_id)) or rule_map.get(user_id)
        return float(user_value) if user_value is not None else settings.auto_relist_min_price
    except Exception:
        logger.warning("Invalid auto_relist_user_rules_json value; using default threshold.")
        return settings.auto_relist_min_price


@celery_app.task(name="cluster_images")
def cluster_images_task(user_id: int) -> dict:
    with SessionLocal() as db:
        images = db.execute(select(Image).where(Image.user_id == user_id)).scalars().all()
        groups = cluster_embeddings([(img.id, img.embedding or []) for img in images if img.embedding])
        result = {}
        for _, image_ids in groups.items():
            cluster = Cluster(user_id=user_id)
            db.add(cluster)
            db.flush()
            for image_id in image_ids:
                image = next(i for i in images if i.id == image_id)
                image.cluster_id = cluster.id
            listing = Listing(user_id=user_id, cluster_id=cluster.id, status="draft")
            db.add(listing)
            result[str(cluster.id)] = image_ids
        db.commit()
        return result


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=5,
    name="publish_listing_to_marketplace",
)
def publish_listing_to_marketplace_task(self, listing_id: int, marketplace: str) -> dict:
    with SessionLocal() as db:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")
        user = db.get(User, listing.user_id)
        if not user:
            raise ValueError("User not found")

        try:
            rate_limiter.acquire(marketplace)
            execution_mode = resolve_execution_mode(listing=listing, user=user, marketplace=marketplace)
            if execution_mode == "direct_api":
                result = multi_platform_publisher.publish(db, listing, marketplace)
                upsert_marketplace_listing(
                    db,
                    listing_id=listing_id,
                    marketplace=marketplace,
                    status=result.status,
                    response=result.response,
                )
                response_status = result.status.value
                response_payload = result.response
            else:
                response_payload = execute_secondary_marketplace_path(
                    listing=listing,
                    marketplace=marketplace,
                    execution_mode=execution_mode,
                )
                upsert_marketplace_listing(
                    db,
                    listing_id=listing_id,
                    marketplace=marketplace,
                    status=MarketplaceListingStatus.PENDING,
                    response=response_payload,
                )
                response_status = MarketplaceListingStatus.PENDING.value
            db.commit()
            return {
                "marketplace": marketplace,
                "execution_mode": execution_mode,
                "status": response_status,
                "response": response_payload,
            }
        except Exception as exc:
            upsert_marketplace_listing(
                db,
                listing_id=listing_id,
                marketplace=marketplace,
                status=MarketplaceListingStatus.FAILED,
                response={"error": str(exc)},
            )
            db.commit()
            raise


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="process_marketplace_crosspost_job",
)
def process_marketplace_crosspost_job_task(self, job_id: int) -> dict:
    with SessionLocal() as db:
        job = db.get(MarketplaceCrosspostJob, job_id)
        if not job:
            raise ValueError("Crosspost job not found")
        listing = db.get(Listing, job.listing_id)
        if not listing:
            job.status = "failed"
            job.last_error = "Listing not found"
            db.add(job)
            db.commit()
            raise ValueError("Listing not found")
        user = db.get(User, job.user_id)
        if not user:
            job.status = "failed"
            job.last_error = "User not found"
            db.add(job)
            db.commit()
            raise ValueError("User not found")

        if str(job.status).lower() == "canceled":
            return {"job_id": job_id, "status": "canceled"}

        job.status = "running"
        db.add(job)
        db.commit()

        results: list[dict] = []
        targets = job.target_marketplaces or []
        for market in targets:
            db.expire_all()
            if _crosspost_job_canceled(db, job_id):
                return {"job_id": job_id, "status": "canceled", "results": results}
            execution_mode = resolve_execution_mode(listing=listing, user=user, marketplace=market)
            payload = build_marketplace_payload(listing, market)
            if execution_mode == "direct_api":
                result = multi_platform_publisher.publish(db, listing, market)
                upsert_marketplace_listing(
                    db,
                    listing_id=listing.id,
                    marketplace=market,
                    status=result.status,
                    response=result.response,
                )
                results.append(
                    {
                        "marketplace": market,
                        "execution_mode": execution_mode,
                        "status": result.status.value,
                        "response": result.response,
                    }
                )
            else:
                response = execute_secondary_marketplace_path(
                    listing=listing,
                    marketplace=market,
                    execution_mode=execution_mode,
                )
                upsert_marketplace_listing(
                    db,
                    listing_id=listing.id,
                    marketplace=market,
                    status=MarketplaceListingStatus.PENDING,
                    response=response,
                )
                results.append(
                    {
                        "marketplace": market,
                        "execution_mode": execution_mode,
                        "status": "planned",
                        "response": response,
                    }
                )

        db.expire_all()
        if _crosspost_job_canceled(db, job_id):
            return {"job_id": job_id, "status": "canceled", "results": results}
        job.status = "completed"
        job.result_summary = {"results": results}
        db.add(job)
        db.commit()
        return {"job_id": job_id, "results": results}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="process_marketplace_import_job",
)
def process_marketplace_import_job_task(self, job_id: int) -> dict:
    with SessionLocal() as db:
        job = db.get(MarketplaceImportJob, job_id)
        if not job:
            raise ValueError("Import job not found")
        user = db.get(User, job.user_id)
        if not user:
            job.status = "failed"
            job.last_error = "User not found"
            db.add(job)
            db.commit()
            raise ValueError("User not found")

        if str(job.status).lower() == "canceled":
            return {"job_id": job_id, "status": "canceled"}

        job.status = "running"
        db.add(job)
        db.commit()

        normalized = normalize_import_payload(
            source_marketplace=job.source_marketplace,
            payload=job.payload or {},
        )
        imported_payloads: list[dict] = []
        bridge_result: dict | None = None
        bridge_completion: dict | None = None
        db.expire_all()
        if _import_job_canceled(db, job_id):
            return {"job_id": job_id, "status": "canceled"}
        if job.import_mode in {"provider_assist", "browser_assist"}:
            try:
                bridge_result = submit_bridge_job(
                    job_type="import",
                    execution_mode=job.import_mode,
                    payload={
                        "source_marketplace": job.source_marketplace,
                        "source_listing_reference": job.source_listing_reference,
                        "max_listings": (job.payload or {}).get("max_listings"),
                        "payload": job.payload or {},
                        "normalized_preview": normalized,
                    },
                )
            except AutomationBridgeError as exc:
                job.status = "failed"
                job.last_error = str(exc)
                job.normalized_preview = normalized
                db.add(job)
                db.commit()
                raise
            else:
                bridge_job = ((bridge_result.get("bridge_response") or {}) if isinstance(bridge_result, dict) else {})
                bridge_job_id = str(bridge_job.get("job_id") or "").strip()
                if bridge_job_id:
                    bridge_completion = wait_for_bridge_job(job_id=bridge_job_id, timeout_seconds=180, poll_interval_seconds=1.0)
                    completion_status = str(bridge_completion.get("status") or "").strip().lower()
                    if completion_status != "completed":
                        job.status = "failed"
                        job.last_error = str(bridge_completion.get("error") or f"Bridge import job finished with status '{completion_status}'")
                        job.normalized_preview = {
                            "bridge_submission": bridge_result,
                            "bridge_completion": bridge_completion,
                            "normalized_preview": normalized,
                        }
                        db.add(job)
                        db.commit()
                        raise AutomationBridgeError(job.last_error)
                    bridge_payload = bridge_completion.get("result") or {}
                    imported_payloads = [
                        item for item in (bridge_payload.get("imported_listings") or []) if isinstance(item, dict)
                    ]
                    if imported_payloads:
                        normalized = {
                            **normalized,
                            "bridge_submission": bridge_result,
                            "bridge_completion": bridge_completion,
                            "imported_listing_count": len(imported_payloads),
                        }
                    else:
                        normalized = {
                            **normalized,
                            "bridge_submission": bridge_result,
                            "bridge_completion": bridge_completion,
                        }
                else:
                    normalized = {
                        **normalized,
                        "bridge_submission": bridge_result,
                    }
        created_listing_ids: list[int] = []
        reused_listing_ids: list[int] = []
        if imported_payloads:
            for imported_payload in imported_payloads:
                normalized_item = normalize_import_payload(
                    source_marketplace=job.source_marketplace,
                    payload=imported_payload,
                )
                listing, created = _create_imported_listing(
                    db=db,
                    user_id=user.id,
                    source_marketplace=job.source_marketplace,
                    import_job_id=job.id,
                    import_mode=job.import_mode,
                    source_listing_reference=imported_payload.get("source_listing_reference") or imported_payload.get("source_url") or job.source_listing_reference,
                    raw_payload=imported_payload,
                    normalized=normalized_item,
                )
                if created:
                    created_listing_ids.append(listing.id)
                else:
                    reused_listing_ids.append(listing.id)
            resolved_listing_ids = [*created_listing_ids, *reused_listing_ids]
            job.created_listing_id = resolved_listing_ids[0] if resolved_listing_ids else None
            job.normalized_preview = {
                **(normalized if isinstance(normalized, dict) else {}),
                "created_listing_ids": resolved_listing_ids,
                "new_listing_ids": created_listing_ids,
                "reused_listing_ids": reused_listing_ids,
            }
            job.status = "completed"
            db.add(job)
            db.commit()
            return {
                "job_id": job.id,
                "created_listing_ids": resolved_listing_ids,
                "new_listing_ids": created_listing_ids,
                "reused_listing_ids": reused_listing_ids,
            }

        job.normalized_preview = normalized
        db.expire_all()
        if _import_job_canceled(db, job_id):
            db.add(job)
            db.commit()
            return {"job_id": job_id, "status": "canceled", "normalized_preview": normalized}

        listing, created = _create_imported_listing(
            db=db,
            user_id=user.id,
            source_marketplace=job.source_marketplace,
            import_job_id=job.id,
            import_mode=job.import_mode,
            source_listing_reference=job.source_listing_reference,
            raw_payload=job.payload or {},
            normalized=normalized,
        )

        job.created_listing_id = listing.id
        job.normalized_preview = {
            **(normalized if isinstance(normalized, dict) else {}),
            "created_listing_ids": [listing.id],
            "new_listing_ids": [listing.id] if created else [],
            "reused_listing_ids": [] if created else [listing.id],
        }
        job.status = "completed"
        db.add(job)
        db.commit()
        return {
            "job_id": job.id,
            "created_listing_id": listing.id,
            "new_listing_ids": [listing.id] if created else [],
            "reused_listing_ids": [] if created else [listing.id],
        }


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
    name="sync_sold_everywhere",
)
def sync_sold_everywhere_task(self, listing_ids: list[int]) -> dict:
    # Placeholder orchestration hook: in production this would look at order webhooks and marketplace statuses.
    processed = []
    with SessionLocal() as db:
        query = select(Listing)
        if listing_ids:
            query = query.where(Listing.id.in_(listing_ids))
        listings = db.execute(query).scalars().all()
        for listing in listings:
            rows = db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing.id)).scalars().all()
            for row in rows:
                if row.status == MarketplaceListingStatus.PUBLISHED:
                    row.status = MarketplaceListingStatus.UPDATED
                    row.raw_response = {**(row.raw_response or {}), "sold_sync": "queued"}
                    db.add(row)
            processed.append(listing.id)
        db.commit()

    return {"processed_listing_ids": processed, "count": len(processed)}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="poll_for_sales",
)
def poll_for_sales_task(self, dry_run: bool | None = None) -> dict:
    if not settings.sale_detection_enabled:
        logger.info("Sale detection polling disabled by config")
        return {"disabled": True, "processed_users": 0}

    resolved_dry_run = settings.sale_detection_dry_run if dry_run is None else dry_run
    with SessionLocal() as db:
        logger.info("Sale detection polling task started", extra={"dry_run": resolved_dry_run})
        result = sale_detection_service.poll_all_users(
            db,
            dry_run=resolved_dry_run,
            lookback_minutes=max(10, settings.sale_detection_poll_minutes + 5),
        )
        logger.info("Sale detection polling task completed", extra=result)
        return result


@celery_app.task(name="recompute_daily_analytics")
def recompute_daily_analytics_task(user_id: int = 1) -> dict:
    with SessionLocal() as db:
        stat = AnalyticsService().store_daily_stats(db, user_id)
        return {"user_id": user_id, "stat_date": str(stat.stat_date)}


@celery_app.task(name="refresh_pricing_recommendations")
def refresh_pricing_recommendations_task(user_id: int = 1) -> dict:
    with SessionLocal() as db:
        listings = db.execute(select(Listing).where(Listing.user_id == user_id)).scalars().all()
        recommendations = [PricingIntelligenceService().recommend_price(db, l.id) for l in listings]
        return {"user_id": user_id, "count": len(recommendations)}


@celery_app.task(name="refresh_listing_predictions")
def refresh_listing_predictions_task(user_id: int = 1) -> dict:
    with SessionLocal() as db:
        listings = db.execute(select(Listing).where(Listing.user_id == user_id)).scalars().all()
        predictions = [PredictionService().predict_sell_through(db, l.id) for l in listings]
        return {"user_id": user_id, "count": len(predictions)}


@celery_app.task(name="flag_stale_listings")
def flag_stale_listings_task() -> dict:
    stale_count = 0
    with SessionLocal() as db:
        listings = db.execute(select(Listing)).scalars().all()
        stale_cutoff = datetime.utcnow() - timedelta(days=inventory_service.STALE_AFTER_DAYS)
        for listing in listings:
            is_stale = listing.last_refreshed is None or listing.last_refreshed < stale_cutoff
            listing.stale_flag = is_stale
            stale_count += 1 if is_stale else 0
            db.add(listing)
        db.commit()
    return {"processed": len(listings), "stale": stale_count}


@celery_app.task(name="bulk_process_inventory_chunk")
def bulk_process_inventory_chunk(job_id: str, action: str, payload: dict, listing_ids: list[int], batch_index: int = 0) -> dict:
    processed = 0
    errors: list[dict] = []
    with SessionLocal() as db:
        job = db.get(BulkJob, job_id)
        if not job:
            return {"processed": 0, "errors": [{"message": "Bulk job not found"}]}
        job.status = "running"
        db.add(job)
        listings = db.execute(select(Listing).where(Listing.id.in_(listing_ids))).scalars().all()
        for listing in listings:
            try:
                if action == "edit":
                    inventory_service.update_listing_inventory(
                        listing,
                        quantity=payload.get("quantity"),
                        platform_quantities=payload.get("platform_quantities"),
                    )
                elif action == "delist":
                    inventory_service.update_listing_inventory(listing, delist=True)
                elif action == "relist":
                    inventory_service.update_listing_inventory(listing, relist=True)
                elif action == "label":
                    inventory_service.update_listing_inventory(
                        listing,
                        labels_to_add=payload.get("add_labels"),
                        labels_to_remove=payload.get("remove_labels"),
                    )
                elif action == "mark_sold":
                    inventory_service.update_listing_inventory(
                        listing,
                        mark_sold=True,
                        sale_price=payload.get("sale_price"),
                    )
                elif action in {"refresh", "autobump"}:
                    listing.last_refreshed = datetime.utcnow()
                    listing.stale_flag = False
                else:
                    raise ValueError(f"Unsupported action: {action}")
                if payload.get("marketplaces"):
                    data = listing.marketplace_data or {}
                    data["scheduled_refresh"] = {
                        "marketplaces": payload.get("marketplaces"),
                        "requested_at": datetime.utcnow().isoformat(),
                    }
                    listing.marketplace_data = data
                db.add(listing)
                processed += 1
            except (InventorySafetyError, ValueError) as exc:
                errors.append({"listing_id": listing.id, "error": str(exc)})

        db.commit()
        job.processed_items = min(job.total_items, (job.processed_items or 0) + processed + len(errors))
        existing_errors = job.errors or []
        job.errors = [*existing_errors, *errors]
        job.error_count = len(job.errors or [])
        db.add(job)
        db.commit()

    return {"processed": processed, "errors": errors, "batch_index": batch_index}


@celery_app.task(name="bulk_finalize_job")
def bulk_finalize_job(results: list[dict], job_id: str) -> dict:
    with SessionLocal() as db:
        job = db.get(BulkJob, job_id)
        if not job:
            return {"job_id": job_id, "status": "missing"}
        total_errors = sum(len(result.get("errors") or []) for result in (results or []))
        job.status = "completed_with_errors" if total_errors else "completed"
        job.error_count = total_errors
        job.processed_items = job.total_items
        db.add(job)
        db.commit()
        return {
            "job_id": job_id,
            "status": job.status,
            "processed_items": job.processed_items,
            "total_items": job.total_items,
            "errors": total_errors,
        }




@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=5,
    name="auto_price_listing",
)
def auto_price_listing(self, listing_id: int) -> dict:
    with SessionLocal() as db:
        logger.info("Auto pricing start", extra={"listing_id": listing_id})
        result = PricingService().generate_pricing(db, listing_id)
        return {"listing_id": listing_id, **result}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=5,
    name="autonomous_publish",
)
def autonomous_publish(self, listing_id: int, dry_run: bool | None = None) -> dict:
    with SessionLocal() as db:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")
        if listing.status != ListingStatus.PROCESSED:
            raise ValueError(f"Listing must be PROCESSED before autonomous publish (got {listing.status})")

        resolved_dry_run = settings.autonomous_dry_run if dry_run is None else dry_run
        logger.info(
            "Autonomous publish start",
            extra={"listing_id": listing_id, "dry_run": resolved_dry_run, "status": listing.status.value},
        )
        pricing = PricingService().generate_pricing(db, listing_id)
        logger.info("Autonomous publish pricing complete", extra={"listing_id": listing_id, "pricing": pricing})

        if resolved_dry_run:
            existing_data = listing.marketplace_data or {}
            listing.marketplace_data = {
                **existing_data,
                "autonomous": {
                    "trigger": "auto",
                    "dry_run": True,
                    "pricing": pricing,
                    "executed_at": datetime.now(UTC).isoformat(),
                },
            }
            db.add(listing)
            db.commit()
            logger.info("Autonomous publish dry-run complete", extra={"listing_id": listing_id})
            return {"listing_id": listing_id, "status": "DRY_RUN", "pricing": pricing}

        try:
            ebay_result = multi_platform_publisher.publish(db, listing, MarketplaceName.ebay.value).response
            listing.status = ListingStatus.PUBLISHED
            user = db.get(User, listing.user_id)
            enabled_platforms = get_enabled_platforms(user)
            crosspost_targets = [market for market in enabled_platforms if market != MarketplaceName.ebay.value] if settings.autonomous_crosspost_enabled else []
            crosspost_group = None
            if crosspost_targets:
                crosspost_group = group(
                    publish_listing_to_marketplace_task.s(listing.id, market) for market in crosspost_targets
                ).apply_async()
            listing.marketplace_data = {
                **(listing.marketplace_data or {}),
                "autonomous": {
                    "trigger": "auto",
                    "dry_run": False,
                    "pricing": pricing,
                    "published_at": datetime.now(UTC).isoformat(),
                    "crosspost_targets": crosspost_targets,
                    "crosspost_group_id": crosspost_group.id if crosspost_group else None,
                },
            }
            db.add(listing)
            db.commit()
            logger.info(
                "Autonomous publish complete",
                extra={
                    "listing_id": listing_id,
                    "status": listing.status.value,
                    "ebay_listing_id": listing.ebay_listing_id,
                },
            )
            return {
                "listing_id": listing_id,
                "status": "PUBLISHED",
                "ebay": ebay_result,
                "pricing": pricing,
                "crossposts": {
                    "targets": crosspost_targets,
                    "group_id": crosspost_group.id if crosspost_group else None,
                },
            }
        except Exception as exc:
            listing.status = ListingStatus.FAILED
            db.add(listing)
            db.commit()
            logger.exception("Autonomous publish failed", extra={"listing_id": listing_id, "error": str(exc)})
            raise


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="adjust_active_listing_prices",
)
def adjust_active_listing_prices(self) -> dict:
    adjusted = 0
    with SessionLocal() as db:
        active_listings = db.execute(
            select(Listing).where(
                Listing.status.in_(["PROCESSED", "ready"]),
                Listing.sale_price.is_(None),
            )
        ).scalars().all()

        service = PricingService()
        for listing in active_listings:
            service.adjust_price_based_on_comps(db, listing.id)
            adjusted += 1

    return {"adjusted": adjusted}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="monitor_and_relist",
)
def monitor_and_relist(self) -> dict:
    if not settings.auto_relist_enabled:
        logger.info("Auto relist disabled by config.")
        return {"checked": 0, "relisted": 0, "skipped": 0, "disabled": True}

    checked = 0
    relisted = 0
    skipped = 0
    now = datetime.now(UTC)

    with SessionLocal() as db:
        candidates = db.execute(select(Listing).where(Listing.status == ListingStatus.PUBLISHED)).scalars().all()
        for listing in candidates:
            checked += 1
            data = listing.marketplace_data or {}
            end_time_iso = _extract_end_time_iso(data)
            quantity = _extract_quantity(data)
            listing_price = float(listing.listing_price or listing.buy_it_now_price or listing.suggested_price or 0)
            min_price = _get_user_relist_min_price(listing.user_id)

            should_monitor = False
            if end_time_iso:
                try:
                    end_time = datetime.fromisoformat(end_time_iso.replace("Z", "+00:00"))
                    should_monitor = end_time <= now
                except ValueError:
                    logger.warning("Unable to parse listing end time.", extra={"listing_id": listing.id, "value": end_time_iso})
            if quantity is not None and quantity <= 0:
                should_monitor = True

            if not should_monitor:
                continue

            if listing_price <= min_price:
                skipped += 1
                logger.info(
                    "Auto-relist skipped due to relist rule threshold.",
                    extra={"listing_id": listing.id, "listing_price": listing_price, "min_price": min_price},
                )
                continue

            logger.info(
                "Auto-relisting listing.",
                extra={"listing_id": listing.id, "ebay_listing_id": listing.ebay_listing_id, "price": listing_price},
            )
            try:
                result = asyncio.run(publish_listing_to_ebay(listing, db, relist=True))
                relisted += 1
                logger.info("Auto-relist successful.", extra={"listing_id": listing.id, "result": result})
            except Exception as exc:
                skipped += 1
                logger.exception("Auto-relist failed.", extra={"listing_id": listing.id, "error": str(exc)})

    return {"checked": checked, "relisted": relisted, "skipped": skipped}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="process_photo_batch",
)
def process_photo_batch(self, listing_ids: list[int]) -> dict:
    service = PhotoEnrichmentService()
    processed = 0
    failed = 0

    with SessionLocal() as db:
        for listing_id in listing_ids:
            listing = db.get(Listing, listing_id)
            if not listing:
                logger.warning("Photo batch listing not found", extra={"listing_id": listing_id})
                failed += 1
                continue
            if not listing.raw_photo_path:
                listing.status = "FAILED"
                db.add(listing)
                failed += 1
                continue

            try:
                logger.info("Photo enrichment start", extra={"listing_id": listing.id, "photo_path": listing.raw_photo_path})
                enriched = service.enrich_photo(listing.raw_photo_path)
                listing.title = enriched.get("title") or listing.title
                listing.description = enriched.get("description") or listing.description
                listing.category_id = enriched.get("category_id")
                listing.category_suggestion = enriched.get("category_suggestion")
                listing.tags = enriched.get("tags")
                listing.item_specifics = enriched.get("item_specifics")
                listing.estimated_value = enriched.get("estimated_value")
                listing.status = "PROCESSED"
                listing.last_refreshed = datetime.utcnow()
                listing.platform_quantities = listing.platform_quantities or {"inventory": max(1, int(listing.quantity or 1))}
                listing.marketplace_data = {
                    **(listing.marketplace_data or {}),
                    "ingestion_source": "autonomous_photo_batch",
                    "sale_detection_ready": True,
                }
                db.add(listing)
                processed += 1
                logger.info("Photo enrichment complete", extra={"listing_id": listing.id, "status": listing.status})
                if settings.autonomous_mode:
                    logger.info(
                        "Queueing autonomous publish from photo pipeline",
                        extra={"listing_id": listing.id, "dry_run": settings.autonomous_dry_run},
                    )
                    autonomous_publish.delay(listing.id, dry_run=settings.autonomous_dry_run)
                else:
                    auto_price_listing.delay(listing.id)
            except Exception as exc:
                listing.status = "FAILED"
                db.add(listing)
                failed += 1
                logger.exception("Photo enrichment failed", extra={"listing_id": listing.id, "error": str(exc)})
        db.commit()

    return {"processed": processed, "failed": failed, "total": len(listing_ids)}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="process_storage_unit_listing",
)
def process_storage_unit_listing(self, listing_id: int, batch_id: int) -> dict:
    service = PhotoEnrichmentService()
    with SessionLocal() as db:
        listing = db.get(Listing, listing_id)
        batch = db.get(StorageUnitBatch, batch_id)
        if not listing or not batch:
            raise ValueError("Listing or batch not found")
        if not listing.raw_photo_path:
            listing.status = ListingStatus.FAILED
            batch.processed_items += 1
            db.add_all([listing, batch])
            db.commit()
            return {"listing_id": listing_id, "status": "FAILED"}
        try:
            enriched = service.enrich_photo(listing.raw_photo_path)
            listing.title = enriched.get("title") or listing.title
            listing.description = enriched.get("description") or listing.description
            listing.category_id = enriched.get("category_id")
            listing.category_suggestion = enriched.get("category_suggestion")
            listing.tags = enriched.get("tags")
            listing.item_specifics = enriched.get("item_specifics")
            listing.estimated_value = enriched.get("estimated_value")
            listing.status = ListingStatus.PROCESSED
            listing.last_refreshed = datetime.utcnow()
            listing.platform_quantities = listing.platform_quantities or {"inventory": max(1, int(listing.quantity or 1))}
            listing.marketplace_data = {
                **(listing.marketplace_data or {}),
                "ingestion_source": "autonomous_storage_batch",
                "sale_detection_ready": True,
            }
            if settings.autonomous_mode:
                autonomous_publish.delay(listing.id, dry_run=settings.autonomous_dry_run)
            else:
                auto_price_listing.delay(listing.id)
            batch.processed_items += 1
            db.add_all([listing, batch])
            db.commit()
            return {"listing_id": listing_id, "status": "PROCESSED"}
        except Exception as exc:
            listing.status = ListingStatus.FAILED
            batch.processed_items += 1
            db.add_all([listing, batch])
            db.commit()
            logger.exception("Storage unit listing processing failed", extra={"listing_id": listing_id, "error": str(exc)})
            raise


@celery_app.task(name="finalize_storage_unit_batch")
def finalize_storage_unit_batch(results: list[dict], batch_id: int) -> dict:
    with SessionLocal() as db:
        batch = db.get(StorageUnitBatch, batch_id)
        if not batch:
            return {"batch_id": batch_id, "status": "MISSING"}
        failed_count = sum(1 for result in results if result.get("status") == "FAILED")
        batch.status = "FAILED" if failed_count else "COMPLETED"
        db.add(batch)
        db.commit()
        return {"batch_id": batch_id, "status": batch.status, "processed": batch.processed_items, "total": batch.total_items}


def enqueue_storage_unit_batch_pipeline(batch_id: int, listing_ids: list[int]):
    workflow = chord(
        group(process_storage_unit_listing.s(listing_id, batch_id) for listing_id in listing_ids),
        finalize_storage_unit_batch.s(batch_id),
    )
    return workflow.apply_async()


@celery_app.task(name="process_overnight_storage_batches")
def process_overnight_storage_batches() -> dict:
    started = 0
    with SessionLocal() as db:
        batches = db.execute(
            select(StorageUnitBatch).where(
                StorageUnitBatch.overnight_mode.is_(True),
                StorageUnitBatch.status.in_(["INGESTED", "QUEUED"]),
            )
        ).scalars().all()
        for batch in batches:
            listing_ids = [listing.id for listing in batch.listings]
            if not listing_ids:
                continue
            batch.status = "PROCESSING"
            async_result = enqueue_storage_unit_batch_pipeline(batch.id, listing_ids)
            batch.pipeline_task_id = async_result.id
            db.add(batch)
            started += 1
        db.commit()
    return {"started": started}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=5,
    name="process_incoming_offers",
)
def process_incoming_offers(self) -> dict:
    checked_accounts = 0
    offers_checked = 0
    accepted = 0
    rejected = 0
    skipped = 0

    with SessionLocal() as db:
        accounts = db.execute(
            select(MarketplaceAccount).where(MarketplaceAccount.marketplace == MarketplaceName.ebay)
        ).scalars().all()
        service = OfferService()
        for account in accounts:
            checked_accounts += 1
            result = service.process_incoming_offers(db, account)
            offers_checked += result["checked"]
            accepted += result["accepted"]
            rejected += result["rejected"]
            skipped += result["skipped"]

    return {
        "accounts_checked": checked_accounts,
        "offers_checked": offers_checked,
        "accepted": accepted,
        "rejected": rejected,
        "skipped": skipped,
    }


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=3,
    name="send_personalized_offers",
)
def send_personalized_offers_task(self) -> dict:
    checked_accounts = 0
    sent = 0
    skipped = 0
    candidates = 0
    with SessionLocal() as db:
        accounts = db.execute(
            select(MarketplaceAccount).where(MarketplaceAccount.marketplace == MarketplaceName.ebay)
        ).scalars().all()
        service = OfferService()
        for account in accounts:
            checked_accounts += 1
            result = service.send_personalized_offers(db, account, force=False)
            candidates += result["candidates"]
            sent += result["sent"]
            skipped += result["skipped"]
    return {"accounts_checked": checked_accounts, "candidates": candidates, "sent": sent, "skipped": skipped}
