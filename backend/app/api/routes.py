from datetime import datetime
import io
import json
import zipfile

import httpx

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    BatchStorageUnitUrlRequest,
    GooglePhotosImportRequest,
    ListingGenerateRequest,
    ListingResponse,
    ListingUpdateRequest,
    PhotoEditRequest,
    PhotoEditResponse,
    StorageUnitBatchResponse,
)
from app.core.config import settings
from app.core.database import get_db
from app.models.models import Cluster, Image, Listing, StorageUnitBatch
from app.services.ebay import EbayService
from app.services.embedding import fake_clip_embedding
from app.services.google_photos import GooglePhotosService
from app.services.image_pipeline import ImagePipelineService
from app.services.inventory_service import InventorySafetyError, InventoryService
from app.services.listing_ai import ListingAIService
from app.services.profit_service import ProfitService
from app.services.storage import LocalStorage
from app.services.pricing_service import PricingService
from app.services.photo_editor import PhotoEditorService
from app.models.enums import ListingStatus
from app.workers.tasks import (
    cluster_images_task,
    enqueue_storage_unit_batch_pipeline,
    process_overnight_storage_batches,
    process_photo_batch,
)

router = APIRouter()
inventory_service = InventoryService()
photo_editor_service = PhotoEditorService()



def _to_public_image_url(path: str) -> str:
    storage_root = Path(settings.storage_root).resolve()
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(storage_root)
        return f"/media/{relative.as_posix()}"
    except ValueError:
        return path


def _create_storage_batch(
    db: Session,
    user_id: int,
    storage_unit_name: str | None,
    overnight_mode: bool,
    photo_paths: list[str],
) -> StorageUnitBatch:
    batch = StorageUnitBatch(
        user_id=user_id,
        storage_unit_name=storage_unit_name,
        status="INGESTED",
        overnight_mode=overnight_mode,
        total_items=len(photo_paths),
        processed_items=0,
    )
    db.add(batch)
    db.flush()
    for raw_path in photo_paths:
        listing = Listing(
            user_id=user_id,
            batch_id=batch.id,
            cluster_id=None,
            status=ListingStatus.INGESTED,
            image_urls=[raw_path],
            raw_photo_path=raw_path,
            storage_unit_name=storage_unit_name,
        )
        db.add(listing)
    return batch


def _start_batch_pipeline(db: Session, batch: StorageUnitBatch) -> str | None:
    listing_ids = [listing.id for listing in batch.listings]
    if not listing_ids:
        return None
    async_result = enqueue_storage_unit_batch_pipeline(batch.id, listing_ids)
    batch.status = "PROCESSING"
    batch.pipeline_task_id = async_result.id
    db.add(batch)
    return async_result.id


class AutonomousToggleRequest(BaseModel):
    enabled: bool | None = None


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
    for key, value in payload.model_dump(exclude_none=True, exclude={"quantity", "platform_quantities", "custom_labels"}).items():
        setattr(listing, key, value)
    try:
        inventory_service.update_listing_inventory(
            listing,
            quantity=payload.quantity,
            platform_quantities=payload.platform_quantities,
            labels_to_add=payload.custom_labels,
        )
    except InventorySafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.sale_price is not None:
        listing.sold_at = datetime.utcnow()
        ProfitService().update_profit_on_sale_event(listing, "ebay")
    db.commit()
    db.refresh(listing)
    return listing


@router.post("/listings/{listing_id}/photo-tools", response_model=PhotoEditResponse)
async def process_listing_photo(
    listing_id: int,
    edits: str = Form(default="{}"),
    remove_background: bool = Form(default=False),
    source_image: str | None = Form(default=None),
    photo: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    candidates = [source_image, *((listing.image_urls or [])), listing.raw_photo_path]
    preferred_source = next((item for item in candidates if item), None)
    upload_bytes = await photo.read() if photo else None

    try:
        parsed = PhotoEditRequest.model_validate_json(edits)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid edits payload: {exc}") from exc

    try:
        image = photo_editor_service.load_image(source_image=preferred_source, upload_bytes=upload_bytes)
        if remove_background:
            image = photo_editor_service.remove_background(image)
        image = photo_editor_service.apply_edits(
            image,
            brightness=parsed.brightness,
            contrast=parsed.contrast,
            filter_name=parsed.filter_name,
            crop_x=parsed.crop_x,
            crop_y=parsed.crop_y,
            crop_width=parsed.crop_width,
            crop_height=parsed.crop_height,
        )
        saved_path = photo_editor_service.save_image(image, transparent=remove_background)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Background removal failed: {exc}") from exc

    listing.image_urls = [*(listing.image_urls or []), saved_path]
    db.add(listing)
    db.commit()
    db.refresh(listing)

    return PhotoEditResponse(
        image_url=_to_public_image_url(saved_path),
        image_urls=[_to_public_image_url(path) for path in (listing.image_urls or [])],
    )


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


@router.post("/batch/storage-unit", response_model=StorageUnitBatchResponse)
async def ingest_storage_unit_batch(
    zip_file: UploadFile | None = File(default=None),
    image_urls: str | None = Form(default=None),
    user_id: int = Form(1),
    storage_unit_name: str | None = Form(default=None),
    overnight_mode: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    if not zip_file and not image_urls:
        raise HTTPException(status_code=400, detail="Provide either zip_file or image_urls")
    if zip_file and image_urls:
        raise HTTPException(status_code=400, detail="Provide zip_file or image_urls, not both")

    storage = LocalStorage()
    photo_paths: list[str] = []

    if zip_file:
        payload = await zip_file.read()
        if not payload:
            raise HTTPException(status_code=400, detail="Uploaded zip file is empty")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    suffix = Path(member.filename).suffix.lower()
                    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                        continue
                    file_bytes = archive.read(member.filename)
                    if not file_bytes:
                        continue
                    photo_paths.append(storage.save_bytes(file_bytes, extension=suffix, prefix="batch_uploads"))
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="Invalid zip file") from exc
    else:
        try:
            decoded = json.loads(image_urls or "[]")
            if not isinstance(decoded, list):
                raise ValueError("image_urls must be a list")
            for url in decoded:
                photo_paths.append(storage.save_from_url(str(url), prefix="batch_uploads"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid image_urls payload: {exc}") from exc

    if not photo_paths:
        raise HTTPException(status_code=400, detail="No valid images found in payload")

    batch = _create_storage_batch(db, user_id, storage_unit_name, overnight_mode, photo_paths)
    db.commit()
    db.refresh(batch)
    if not overnight_mode:
        task_id = _start_batch_pipeline(db, batch)
        db.commit()
        db.refresh(batch)
        batch.pipeline_task_id = task_id
    elif overnight_mode:
        batch.status = "QUEUED"
        db.add(batch)
        db.commit()
        db.refresh(batch)
    return batch


@router.post("/batch/storage-unit/from-urls", response_model=StorageUnitBatchResponse)
def ingest_storage_unit_urls(payload: BatchStorageUnitUrlRequest, db: Session = Depends(get_db)):
    storage = LocalStorage()
    photo_paths = [storage.save_from_url(str(url), prefix="batch_uploads") for url in payload.image_urls]
    if not photo_paths:
        raise HTTPException(status_code=400, detail="No valid image URLs received")
    batch = _create_storage_batch(db, payload.user_id, payload.storage_unit_name, payload.overnight_mode, photo_paths)
    db.commit()
    db.refresh(batch)
    if payload.overnight_mode:
        batch.status = "QUEUED"
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch
    _start_batch_pipeline(db, batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/batch/storage-unit", response_model=list[StorageUnitBatchResponse])
def list_storage_unit_batches(db: Session = Depends(get_db)):
    return db.execute(select(StorageUnitBatch).order_by(StorageUnitBatch.id.desc())).scalars().all()


@router.get("/batch/storage-unit/{batch_id}", response_model=StorageUnitBatchResponse)
def get_storage_unit_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(StorageUnitBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.post("/batch/storage-unit/{batch_id}/run-overnight", response_model=StorageUnitBatchResponse)
def run_storage_unit_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(StorageUnitBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.status not in {"QUEUED", "INGESTED"}:
        raise HTTPException(status_code=400, detail=f"Batch is not runnable from status {batch.status}")
    _start_batch_pipeline(db, batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.post("/batch/storage-unit/run-overnight")
def run_all_overnight_batches():
    task = process_overnight_storage_batches.delay()
    return {"task_id": task.id, "status": "QUEUED"}


@router.get("/config/autonomous")
def get_autonomous_config():
    return {
        "autonomous_mode": settings.autonomous_mode,
        "autonomous_dry_run": settings.autonomous_dry_run,
    }


@router.post("/config/toggle-autonomous")
def toggle_autonomous_mode(payload: AutonomousToggleRequest | None = None):
    if payload and payload.enabled is not None:
        settings.autonomous_mode = payload.enabled
    else:
        settings.autonomous_mode = not settings.autonomous_mode
    return {
        "autonomous_mode": settings.autonomous_mode,
        "autonomous_dry_run": settings.autonomous_dry_run,
    }




@router.get("/listings/{listing_id}/pricing")
def get_listing_pricing(listing_id: int, db: Session = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return PricingService().get_pricing(db, listing_id)


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
