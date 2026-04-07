from __future__ import annotations

import logging

from sqlalchemy import select

from app.connectors.registry import get_connector
from app.core.database import SessionLocal
from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Cluster, Image, Listing, MarketplaceListing
from app.services.analytics_service import AnalyticsService
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.photo_enrichment import PhotoEnrichmentService
from app.services.pricing_service import PricingService
from app.workers.celery_app import celery_app
from app.services.clustering import cluster_embeddings

logger = logging.getLogger(__name__)


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

        record = db.execute(
            select(MarketplaceListing).where(
                MarketplaceListing.listing_id == listing_id,
                MarketplaceListing.marketplace == MarketplaceName(marketplace),
            )
        ).scalar_one_or_none()
        if not record:
            record = MarketplaceListing(
                listing_id=listing_id,
                marketplace=MarketplaceName(marketplace),
                status=MarketplaceListingStatus.DRAFT,
            )
            db.add(record)

        connector = get_connector(marketplace)

        try:
            response = __import__("asyncio").run(connector.publish(listing))
            record.marketplace_listing_id = response.get("listing_id") or response.get("external_listing_id")
            record.raw_response = response
            record.status = MarketplaceListingStatus.PUBLISHED
            db.add(record)
            db.commit()
            return {"marketplace": marketplace, "status": "PUBLISHED", "response": response}
        except Exception as exc:
            record.status = MarketplaceListingStatus.FAILED
            record.raw_response = {"error": str(exc)}
            db.add(record)
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
                auto_price_listing.delay(listing.id)
            except Exception as exc:
                listing.status = "FAILED"
                db.add(listing)
                failed += 1
                logger.exception("Photo enrichment failed", extra={"listing_id": listing.id, "error": str(exc)})
        db.commit()

    return {"processed": processed, "failed": failed, "total": len(listing_ids)}
