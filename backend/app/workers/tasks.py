from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, UTC

from celery import group
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Cluster, Image, Listing, MarketplaceAccount, MarketplaceListing, User
from app.services.analytics_service import AnalyticsService
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.photo_enrichment import PhotoEnrichmentService
from app.services.pricing_service import PricingService
from app.services.marketplace_publisher import get_enabled_platforms, marketplace_publisher, upsert_marketplace_listing
from app.services.offer_service import OfferService
from app.workers.celery_app import celery_app
from app.services.clustering import cluster_embeddings

logger = logging.getLogger(__name__)


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

        try:
            result = marketplace_publisher.publish(db, listing, marketplace)
            upsert_marketplace_listing(
                db,
                listing_id=listing_id,
                marketplace=marketplace,
                status=result.status,
                response=result.response,
            )
            db.commit()
            return {"marketplace": marketplace, "status": result.status.value, "response": result.response}
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
            ebay_result = marketplace_publisher.publish(db, listing, MarketplaceName.ebay.value).response
            listing.status = ListingStatus.PUBLISHED
            user = db.get(User, listing.user_id)
            enabled_platforms = get_enabled_platforms(user)
            crosspost_targets = [market for market in enabled_platforms if market != MarketplaceName.ebay.value]
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
