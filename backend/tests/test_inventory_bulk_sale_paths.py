import os
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite:///./test_inventory_bulk_sale_paths.db"

from app.core.database import Base, SessionLocal, engine
from app.models.enums import ListingStatus
from app.models.models import BulkJob, Listing, StorageUnitBatch, User
from app.workers import tasks


Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


def seed_user_and_listing(quantity: int = 3):
    db = SessionLocal()
    user = User(email=f"bulk-sale-{uuid4()}@example.com")
    db.add(user)
    db.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Vintage poster",
        quantity=quantity,
        platform_quantities={"ebay": quantity, "mercari": quantity},
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)

    job = BulkJob(
        id=f"job-{uuid4()}",
        user_id=user.id,
        action="mark_sold",
        status="queued",
        total_items=1,
        processed_items=0,
        errors=[],
        error_count=0,
        payload={},
        filters={},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    user_id = user.id
    listing_id = listing.id
    job_id = job.id
    db.close()
    return user_id, listing_id, job_id


def test_bulk_mark_sold_sets_quantity_zero_and_timestamp():
    _user_id, listing_id, job_id = seed_user_and_listing(quantity=4)

    result = tasks.bulk_process_inventory_chunk(
        job_id=job_id,
        action="mark_sold",
        payload={"sale_price": 129.99},
        listing_ids=[listing_id],
        batch_index=0,
    )
    assert result["processed"] == 1

    db = SessionLocal()
    listing = db.get(Listing, listing_id)
    assert listing is not None
    assert listing.quantity == 0
    assert listing.platform_quantities == {"ebay": 0, "mercari": 0}
    assert listing.sale_price == 129.99
    assert listing.sold_at is not None
    db.close()


def test_storage_unit_processing_sets_inventory_and_sale_detection_metadata(monkeypatch):
    db = SessionLocal()
    user = User(email=f"storage-sale-{uuid4()}@example.com")
    db.add(user)
    db.flush()
    batch = StorageUnitBatch(user_id=user.id, storage_unit_name="Unit C", status="PROCESSING", total_items=1, processed_items=0)
    db.add(batch)
    db.flush()
    listing = Listing(
        user_id=user.id,
        batch_id=batch.id,
        status=ListingStatus.INGESTED,
        raw_photo_path="./storage/uploads/demo.jpg",
        quantity=2,
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    db.refresh(batch)

    monkeypatch.setattr(
        tasks.PhotoEnrichmentService,
        "enrich_photo",
        lambda self, _path: {
            "title": "Camera Bundle",
            "description": "Clean listing",
            "category_id": "123",
            "category_suggestion": "Cameras",
            "tags": ["camera"],
            "item_specifics": {"brand": "Nikon"},
            "estimated_value": 250.0,
        },
    )
    monkeypatch.setattr(tasks.settings, "autonomous_mode", False)
    monkeypatch.setattr(tasks.auto_price_listing, "delay", lambda *_args, **_kwargs: None)

    tasks.process_storage_unit_listing(listing.id, batch.id)

    db.refresh(listing)
    assert listing.status == ListingStatus.PROCESSED
    assert listing.platform_quantities == {"inventory": 2}
    assert listing.marketplace_data["sale_detection_ready"] is True
    assert listing.marketplace_data["ingestion_source"] == "autonomous_storage_batch"
    assert listing.last_refreshed is not None
    db.close()
