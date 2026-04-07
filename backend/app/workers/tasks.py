from __future__ import annotations

from sqlalchemy import select

from app.connectors.registry import get_connector
from app.core.database import SessionLocal
from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Cluster, Image, Listing, MarketplaceListing
from app.workers.celery_app import celery_app
from app.services.clustering import cluster_embeddings


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
