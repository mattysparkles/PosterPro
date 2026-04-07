from datetime import datetime

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import GooglePhotosImportRequest, ListingGenerateRequest, ListingResponse, ListingUpdateRequest
from app.core.database import get_db
from app.models.models import Cluster, Image, Listing
from app.services.ebay import EbayService
from app.services.embedding import fake_clip_embedding
from app.services.google_photos import GooglePhotosService
from app.services.image_pipeline import ImagePipelineService
from app.services.listing_ai import ListingAIService
from app.services.profit_service import ProfitService
from app.services.storage import LocalStorage
from app.models.enums import ListingStatus
from app.workers.tasks import cluster_images_task, process_photo_batch

router = APIRouter()


@router.post("/import/google-photos")
def import_google_photos(payload: GooglePhotosImportRequest, db: Session = Depends(get_db)):
    photo_service = GooglePhotosService()
    storage = LocalStorage()
    pipeline = ImagePipelineService()

    urls = photo_service.extract_image_urls(str(payload.album_url))
    created = []
    for url in urls:
        local = storage.save_from_url(url)
        processed = pipeline.process(local)
        embedding = fake_clip_embedding(processed)
        image = Image(user_id=payload.user_id, source_url=url, local_path=processed, embedding=embedding)
        db.add(image)
        created.append(url)
    db.commit()

    task = cluster_images_task.delay(payload.user_id)
    return {"imported": len(created), "task_id": task.id}


@router.get("/clusters")
def get_clusters(db: Session = Depends(get_db)):
    clusters = db.execute(select(Cluster)).scalars().all()
    return [{"id": c.id, "title_hint": c.title_hint, "image_count": len(c.images)} for c in clusters]


@router.get("/listings", response_model=list[ListingResponse])
def get_listings(db: Session = Depends(get_db)):
    return db.execute(select(Listing)).scalars().all()


@router.patch("/listings/{listing_id}", response_model=ListingResponse)
def update_listing(listing_id: int, payload: ListingUpdateRequest, db: Session = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(listing, key, value)
    if payload.sale_price is not None:
        listing.sold_at = datetime.utcnow()
        ProfitService().update_profit_on_sale_event(listing, "ebay")
    db.commit()
    db.refresh(listing)
    return listing


@router.post("/ingest/photos")
async def ingest_photos(
    photos: list[UploadFile] = File(...),
    user_id: int = Form(1),
    storage_unit_name: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not photos:
        raise HTTPException(status_code=400, detail="No photos uploaded")

    storage = LocalStorage()
    listing_ids: list[int] = []
    uploads: list[str] = []

    for photo in photos:
        content = await photo.read()
        if not content:
            continue
        suffix = Path(photo.filename or "").suffix or ".jpg"
        raw_path = storage.save_bytes(content, extension=suffix, prefix="uploads")
        listing = Listing(
            user_id=user_id,
            cluster_id=None,
            status=ListingStatus.INGESTED,
            image_urls=[raw_path],
            raw_photo_path=raw_path,
            storage_unit_name=storage_unit_name,
        )
        db.add(listing)
        db.flush()
        listing_ids.append(listing.id)
        uploads.append(raw_path)

    if not listing_ids:
        raise HTTPException(status_code=400, detail="No valid photo payloads received")

    db.commit()
    task = process_photo_batch.delay(listing_ids)
    return {"created_listings": listing_ids, "uploaded_paths": uploads, "task_id": task.id}


@router.post("/listings/{listing_id}/generate", response_model=ListingResponse)
def generate_listing(listing_id: int, payload: ListingGenerateRequest, db: Session = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    ai = ListingAIService()
    ebay = EbayService()

    generated = ai.generate({"title_hint": listing.cluster.title_hint if listing.cluster else None})
    price_data = ebay.enrich_price(generated["title"], payload.barcode)

    listing.title = generated["title"]
    listing.description = generated["description"]
    listing.category_suggestion = generated["category_suggestion"]
    listing.tags = generated["tags"]
    listing.suggested_price = price_data["suggested_price"]
    listing.listing_price = price_data["suggested_price"]
    listing.status = "ready"
    db.commit()
    db.refresh(listing)
    return listing
