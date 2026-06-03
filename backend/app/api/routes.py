from datetime import datetime
import io
import json
import zipfile

import httpx

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.schemas import (
    BatchStorageUnitUrlRequest,
    BulkListingApproveRequest,
    BulkListingApproveResponse,
    GooglePhotosImportRequest,
    GooglePhotosWatchRequest,
    ListingApprovalResponse,
    ListingCreateRequest,
    ListingGenerateRequest,
    ListingResponse,
    ListingTemplateApplyRequest,
    ListingTemplateCreateRequest,
    ListingTemplateResponse,
    ListingUpdateRequest,
    PhotoEditRequest,
    PhotoEditResponse,
    StorageUnitBatchResponse,
)
from app.core.auth import ensure_user_owns_resource, get_current_user, resolve_user_scope
from app.core.config import settings
from app.core.database import get_db
from app.models.models import (
    AutomatedOfferLog,
    Cluster,
    EbayOfferHistory,
    Image,
    Listing,
    ListingABTestVariant,
    ListingPrediction,
    ListingTemplate,
    MarketplaceCrosspostJob,
    MarketplaceImportJob,
    MarketplaceListing,
    ProductMediaCache,
    Sale,
    StorageUnitBatch,
    User,
    VineImportItem,
)
from app.services.ebay import EbayService
from app.services.embedding import fake_clip_embedding
from app.services.google_photos import GooglePhotosService
from app.services.image_pipeline import ImagePipelineService
from app.services.inventory_service import InventorySafetyError, InventoryService
from app.services.listing_ai import ListingAIService
from app.services.media_lifecycle import purge_listing_media
from app.services.listing_workspace import normalize_marketplace_data
from app.services.marketplace_orchestrator import queue_publish
from app.services.profit_service import ProfitService
from app.services.storage import LocalStorage
from app.services.pricing_service import PricingService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.photo_editor import PhotoEditorService
from app.services.listing_templates_service import listing_template_service
from app.services.amazon_media import AmazonProductMediaProvider
from app.services.amazon_product_discovery import AmazonProductDiscoveryService
from app.services.automation_bridge import AutomationBridgeError, submit_bridge_job, wait_for_bridge_job
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

_DEFAULT_WORKFLOW_PREFERENCES = {
    "review_before_publish": True,
    "auto_publish_after_approval": False,
    "bulk_approval_enabled": True,
    "listing_preview_mode": "marketplace",
}

_GOOGLE_PHOTOS_WATCH_KEY = "google_photos_watch"


def _workflow_preferences(user: User | None) -> dict:
    if not user:
        return dict(_DEFAULT_WORKFLOW_PREFERENCES)
    settings_json = user.settings_json or {}
    raw = settings_json.get("workflow_preferences")
    stored = raw if isinstance(raw, dict) else {}
    return {
        "review_before_publish": bool(stored.get("review_before_publish", _DEFAULT_WORKFLOW_PREFERENCES["review_before_publish"])),
        "auto_publish_after_approval": bool(stored.get("auto_publish_after_approval", _DEFAULT_WORKFLOW_PREFERENCES["auto_publish_after_approval"])),
        "bulk_approval_enabled": bool(stored.get("bulk_approval_enabled", _DEFAULT_WORKFLOW_PREFERENCES["bulk_approval_enabled"])),
        "listing_preview_mode": str(stored.get("listing_preview_mode") or _DEFAULT_WORKFLOW_PREFERENCES["listing_preview_mode"]),
    }


def _delete_listing_for_user(db: Session, *, listing: Listing, current_user: User) -> dict:
    ensure_user_owns_resource(current_user, listing.user_id)
    media_cleanup = purge_listing_media(db, listing)

    db.execute(delete(MarketplaceListing).where(MarketplaceListing.listing_id == listing.id))
    db.execute(delete(MarketplaceCrosspostJob).where(MarketplaceCrosspostJob.listing_id == listing.id))
    db.execute(delete(ListingPrediction).where(ListingPrediction.listing_id == listing.id))
    db.execute(delete(ListingABTestVariant).where(ListingABTestVariant.listing_id == listing.id))
    db.execute(update(Sale).where(Sale.listing_id == listing.id).values(listing_id=None))
    db.execute(update(EbayOfferHistory).where(EbayOfferHistory.listing_id == listing.id).values(listing_id=None))
    db.execute(update(AutomatedOfferLog).where(AutomatedOfferLog.listing_id == listing.id).values(listing_id=None))
    db.execute(update(MarketplaceImportJob).where(MarketplaceImportJob.created_listing_id == listing.id).values(created_listing_id=None))
    db.execute(
        update(VineImportItem)
        .where((VineImportItem.listing_id == listing.id) | (VineImportItem.inventory_item_id == listing.id))
        .values(listing_id=None, inventory_item_id=None)
    )

    db.delete(listing)
    db.commit()
    return {
        "listing_id": listing.id,
        "deleted": True,
        "media_cleanup": media_cleanup,
    }



def _approve_listing_for_user(db: Session, *, listing: Listing, current_user: User) -> dict:
    marketplace_data = dict(listing.marketplace_data or {})
    current_targets = marketplace_data.get("targets")
    targets = [str(value).strip().lower() for value in (current_targets or []) if str(value).strip()]
    for target in ("ebay", "facebook"):
        if target not in targets:
            targets.append(target)

    listing.status = ListingStatus.ready
    listing.needs_review = False
    listing.marketplace_data = normalize_marketplace_data(
        {
            **marketplace_data,
            "targets": targets,
            "crosspost_mode": str(marketplace_data.get("crosspost_mode") or "approval_required"),
        }
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)

    preferences = _workflow_preferences(current_user)
    results: list[dict] = []
    if preferences.get("auto_publish_after_approval"):
        results = queue_publish(db, listing.id, targets)
        db.refresh(listing)
    return {
        "listing": listing,
        "auto_publish_after_approval": bool(preferences.get("auto_publish_after_approval")),
        "results": results,
    }


def _to_public_image_url(path: str) -> str:
    storage_root = Path(settings.storage_root).resolve()
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(storage_root)
        return f"/media/{relative.as_posix()}"
    except ValueError:
        return path


def _google_photos_watch_settings(user: User | None) -> dict:
    settings_json = (user.settings_json or {}) if user else {}
    raw = settings_json.get(_GOOGLE_PHOTOS_WATCH_KEY) if isinstance(settings_json, dict) else {}
    value = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(value.get("enabled", False)),
        "auto_enrich": bool(value.get("auto_enrich", True)),
        "album_url": str(value.get("album_url") or "").strip(),
        "last_synced_at": value.get("last_synced_at"),
        "last_imported_count": int(value.get("last_imported_count") or 0),
        "last_error": str(value.get("last_error") or "").strip() or None,
    }


def _save_google_photos_watch_settings(*, user: User, db: Session, settings_payload: dict) -> dict:
    current_settings = dict(user.settings_json or {})
    current_settings[_GOOGLE_PHOTOS_WATCH_KEY] = settings_payload
    user.settings_json = current_settings
    db.add(user)
    db.commit()
    db.refresh(user)
    return _google_photos_watch_settings(user)


def _import_google_photos_album(
    *,
    album_url: str,
    db: Session,
    current_user: User,
    user_scope: int,
    auto_enrich: bool = True,
) -> dict:
    photo_service = GooglePhotosService()
    storage = LocalStorage()
    pipeline = ImagePipelineService()
    urls = photo_service.extract_image_urls(str(album_url))

    existing_urls = {
        str(value)
        for (value,) in db.execute(
            select(Image.source_url).where(
                Image.user_id == user_scope,
                Image.source_url.in_(urls),
            )
        ).all()
        if value
    }
    new_urls = [url for url in urls if url not in existing_urls]
    created_listing_ids: list[int] = []
    skipped_urls: list[str] = []

    for url in new_urls:
        try:
            local = storage.save_from_url(url, prefix="google-photos")
            processed = pipeline.process(local)
            embedding = fake_clip_embedding(processed)
            image = Image(user_id=user_scope, source_url=url, local_path=processed, embedding=embedding)
            db.add(image)
            db.flush()

            listing = Listing(
                user_id=user_scope,
                status=ListingStatus.draft,
                title="Google Photos intake draft",
                description="Generated from monitored Google Photos album intake.",
                image_urls=[_to_public_image_url(processed)],
                source_type="google_photos_album",
                source_metadata={
                    "album_url": str(album_url),
                    "source_image_url": url,
                    "intake_source": "google_photos_watch",
                    "image_id": image.id,
                },
                needs_review=True,
                marketplace_data=normalize_marketplace_data({"targets": ["ebay", "facebook"], "crosspost_mode": "approval_required"}),
            )
            db.add(listing)
            db.flush()
            created_listing_ids.append(listing.id)
        except Exception:
            skipped_urls.append(url)
            continue

    db.commit()
    task_id = None
    if created_listing_ids:
        task = process_photo_batch.delay(created_listing_ids)
        task_id = task.id
    return {
        "scanned": len(urls),
        "new_items": len(new_urls),
        "created_listing_ids": created_listing_ids,
        "task_id": task_id,
        "auto_enrich": bool(auto_enrich),
        "skipped_urls": skipped_urls,
    }


def _is_archived_vine_listing(listing: Listing) -> bool:
    labels = {str(label).strip().lower() for label in (listing.custom_labels or [])}
    return "archived_vine" in labels or str(listing.status).lower() == "rejected"


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
    crosspost_enabled: bool | None = None


@router.get("/listing-templates", response_model=list[ListingTemplateResponse])
def get_listing_templates(
    user_id: int | None = None,
    category_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    return listing_template_service.list_templates(db, user_id=scoped_user_id, category_id=category_id)


@router.post("/listing-templates", response_model=ListingTemplateResponse)
def create_listing_template(
    payload: ListingTemplateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return listing_template_service.create_template(
        db,
        user_id=resolve_user_scope(current_user, payload.user_id),
        name=payload.name,
        category_id=payload.category_id,
        is_category_default=payload.is_category_default,
        fields=payload.fields,
    )


@router.post("/listings/{listing_id}/apply-template", response_model=ListingResponse)
def apply_template_to_listing(listing_id: int, payload: ListingTemplateApplyRequest, db: Session = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    template = db.get(ListingTemplate, payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.user_id != listing.user_id:
        raise HTTPException(status_code=403, detail="Template does not belong to listing owner")
    return listing_template_service.apply_template(db, listing, template)


@router.post("/import/google-photos")
def import_google_photos(
    payload: GooglePhotosImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, payload.user_id)
    result = _import_google_photos_album(
        album_url=str(payload.album_url),
        db=db,
        current_user=current_user,
        user_scope=scoped_user_id,
        auto_enrich=True,
    )
    if result["created_listing_ids"]:
        cluster_task = cluster_images_task.delay(scoped_user_id)
        result["cluster_task_id"] = cluster_task.id
    return result


@router.get("/import/google-photos/watch")
def get_google_photos_watch_settings(
    current_user: User = Depends(get_current_user),
):
    return _google_photos_watch_settings(current_user)


@router.put("/import/google-photos/watch")
def update_google_photos_watch_settings(
    payload: GooglePhotosWatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    saved = _save_google_photos_watch_settings(
        user=current_user,
        db=db,
        settings_payload={
            "enabled": bool(payload.enabled),
            "auto_enrich": bool(payload.auto_enrich),
            "album_url": str(payload.album_url),
            "last_synced_at": _google_photos_watch_settings(current_user).get("last_synced_at"),
            "last_imported_count": _google_photos_watch_settings(current_user).get("last_imported_count", 0),
            "last_error": None,
        },
    )
    return saved


@router.post("/import/google-photos/watch/run")
def run_google_photos_watch(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    watch = _google_photos_watch_settings(current_user)
    album_url = str(watch.get("album_url") or "").strip()
    if not album_url:
        raise HTTPException(status_code=400, detail="No Google Photos album URL configured.")
    try:
        result = _import_google_photos_album(
            album_url=album_url,
            db=db,
            current_user=current_user,
            user_scope=current_user.id,
            auto_enrich=bool(watch.get("auto_enrich", True)),
        )
        updated = _save_google_photos_watch_settings(
            user=current_user,
            db=db,
            settings_payload={
                **watch,
                "last_synced_at": datetime.utcnow().isoformat(),
                "last_imported_count": int(result.get("new_items") or 0),
                "last_error": None,
            },
        )
        return {"watch": updated, "result": result}
    except Exception as exc:
        _save_google_photos_watch_settings(
            user=current_user,
            db=db,
            settings_payload={
                **watch,
                "last_error": str(exc),
            },
        )
        raise


@router.get("/clusters")
def get_clusters(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    clusters = db.execute(select(Cluster).where(Cluster.user_id == current_user.id)).scalars().all()
    return [{"id": c.id, "title_hint": c.title_hint, "image_count": len(c.images)} for c in clusters]


@router.get("/listings", response_model=list[ListingResponse])
def get_listings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.execute(select(Listing).where(Listing.user_id == current_user.id).order_by(Listing.updated_at.desc())).scalars().all()


@router.post("/listings/vine/backfill-images")
def backfill_vine_listing_images(
    include_archived: bool = False,
    force_refresh: bool = True,
    strict_match: bool = True,
    use_bridge_session: bool = True,
    only_missing_images: bool = False,
    limit: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    def _bridge_capture_for_asin(target_asin: str, target_name: str | None) -> ProductMediaCache | None:
        nonlocal bridge_refetched, bridge_failed
        try:
            bridge_submission = submit_bridge_job(
                job_type="import",
                execution_mode="browser_assist",
                payload={
                    "source_marketplace": "amazon",
                    "asin": target_asin,
                    "asins": [target_asin],
                    "payload": {
                        "asin": target_asin,
                        "product_name": target_name,
                    },
                },
            )
            bridge_job_id = str((((bridge_submission or {}).get("bridge_response") or {}).get("job_id") or "")).strip()
            if not bridge_job_id:
                bridge_failed += 1
                return None
            bridge_completion = wait_for_bridge_job(job_id=bridge_job_id, timeout_seconds=45, poll_interval_seconds=1.0)
            if str(bridge_completion.get("status") or "").lower() != "completed":
                bridge_failed += 1
                return None
            captured = ((bridge_completion.get("result") or {}).get("imported_listings") or [])
            first = captured[0] if captured and isinstance(captured[0], dict) else {}
            captured_urls = [str(url).strip() for url in (first.get("image_urls") or []) if str(url).strip()]
            if not captured_urls:
                bridge_failed += 1
                return None
            provider.cache_gallery_from_remote_urls(
                asin=target_asin,
                image_urls=captured_urls,
                title_hint=target_name,
                source_provider="bridge_browser",
            )
            cache_row = db.execute(select(ProductMediaCache).where(ProductMediaCache.asin == target_asin)).scalar_one_or_none()
            bridge_refetched += 1
            return cache_row
        except (AutomationBridgeError, Exception):
            bridge_failed += 1
            return None

    listings = db.execute(
        select(Listing).where(
            Listing.user_id == current_user.id,
            Listing.source_type == "amazon_vine",
        )
    ).scalars().all()
    updated = 0
    discovered = 0
    missing_asin = 0
    no_cache = 0
    already_present = 0
    bridge_refetched = 0
    bridge_failed = 0
    provider = AmazonProductMediaProvider(db, owner_user_id=current_user.id)
    discovery = AmazonProductDiscoveryService(provider)
    processed = 0

    for listing in listings:
        if not include_archived and _is_archived_vine_listing(listing):
            continue
        has_images = bool(listing.image_urls and len(listing.image_urls))
        if only_missing_images and has_images:
            continue
        if has_images and not force_refresh:
            already_present += 1
            continue
        if limit is not None and processed >= max(0, limit):
            break
        processed += 1
        source_metadata = dict(listing.source_metadata or {})
        item = (
            db.execute(
                select(VineImportItem)
                .where(
                    (VineImportItem.listing_id == listing.id) | (VineImportItem.inventory_item_id == listing.id)
                )
                .order_by(VineImportItem.updated_at.desc(), VineImportItem.id.desc())
            )
            .scalars()
            .first()
        )
        # Trust the source Vine ASIN first; avoid prior derived "amazon_match_asin" drift.
        asin = str((item.asin if item and item.asin else source_metadata.get("asin") or "")).strip().upper()
        product_name = str(listing.title or source_metadata.get("product_name") or "").strip() or None
        manual_url = (
            str(source_metadata.get("manual_amazon_url") or source_metadata.get("item_url") or "").strip()
            or (str(item.manual_amazon_url).strip() if item and item.manual_amazon_url else None)
        )

        if asin or manual_url:
            try:
                result = (
                    discovery.discover_for_vine_item(asin=asin or None, product_name=product_name, manual_url=manual_url)
                    if strict_match
                    else discovery.discover_for_item(asin=asin or None, product_name=product_name, manual_url=manual_url)
                )
                resolved_asin = str(result.get("asin") or asin or "").strip().upper()
                if resolved_asin and resolved_asin != asin:
                    asin = resolved_asin
                resolved_asin = str(result.get("asin") or asin or "").strip().upper()
                # In strict mode, do not accept mismatched ASINs.
                if strict_match and asin and resolved_asin and resolved_asin != asin:
                    result = {}
                if result.get("image_status") in {"cached", "fetched"}:
                    discovered += 1
            except Exception:
                pass

        if not asin:
            if force_refresh:
                listing.image_urls = []
                labels = set(listing.custom_labels or [])
                labels.add("needs_photos")
                listing.custom_labels = sorted(labels)
                db.add(listing)
            missing_asin += 1
            continue
        cache = db.execute(select(ProductMediaCache).where(ProductMediaCache.asin == asin)).scalar_one_or_none()
        if cache is None:
            if use_bridge_session:
                cache = _bridge_capture_for_asin(asin, product_name)
            if cache is None:
                if force_refresh:
                    listing.image_urls = []
                    labels = set(listing.custom_labels or [])
                    labels.add("needs_photos")
                    listing.custom_labels = sorted(labels)
                    db.add(listing)
                no_cache += 1
                continue
        gallery_urls = [str(url) for url in (cache.gallery_image_urls_json or []) if str(url).strip()]
        if gallery_urls:
            listing.image_urls = gallery_urls
            source_meta = dict(listing.source_metadata or {})
            source_meta["asin"] = asin
            source_meta["product_name"] = product_name
            listing.source_metadata = source_meta
            for asset_id in (cache.local_asset_ids_json or []):
                image_row = db.get(Image, int(asset_id))
                if image_row is None:
                    continue
                image_meta = dict(image_row.image_metadata or {})
                image_meta.update(
                    {
                        "source": "amazon_vine",
                        "asin": asin,
                        "listing_id": listing.id,
                        "product_name": product_name,
                    }
                )
                image_row.image_metadata = image_meta
                db.add(image_row)
            db.add(listing)
            updated += 1
            continue
        if cache.primary_image_url:
            listing.image_urls = [cache.primary_image_url]
            source_meta = dict(listing.source_metadata or {})
            source_meta["asin"] = asin
            source_meta["product_name"] = product_name
            listing.source_metadata = source_meta
            db.add(listing)
            updated += 1
            continue
        if use_bridge_session:
            cache = _bridge_capture_for_asin(asin, product_name)
            if cache is not None:
                refreshed_gallery = [str(url) for url in (cache.gallery_image_urls_json or []) if str(url).strip()]
                if refreshed_gallery:
                    listing.image_urls = refreshed_gallery
                    source_meta = dict(listing.source_metadata or {})
                    source_meta["asin"] = asin
                    source_meta["product_name"] = product_name
                    listing.source_metadata = source_meta
                    db.add(listing)
                    updated += 1
                    continue
                if cache.primary_image_url:
                    listing.image_urls = [cache.primary_image_url]
                    source_meta = dict(listing.source_metadata or {})
                    source_meta["asin"] = asin
                    source_meta["product_name"] = product_name
                    listing.source_metadata = source_meta
                    db.add(listing)
                    updated += 1
                    continue
        if force_refresh:
            listing.image_urls = []
            labels = set(listing.custom_labels or [])
            labels.add("needs_photos")
            listing.custom_labels = sorted(labels)
            db.add(listing)
        no_cache += 1

    db.commit()
    return {
        "updated": updated,
        "discovered": discovered,
        "already_present": already_present,
        "missing_asin": missing_asin,
        "no_cache": no_cache,
        "bridge_refetched": bridge_refetched,
        "bridge_failed": bridge_failed,
        "total_vine_listings": len(listings),
        "processed": processed,
        "include_archived": include_archived,
        "force_refresh": force_refresh,
        "strict_match": strict_match,
    }


@router.get("/listings/{listing_id}", response_model=ListingResponse)
def get_listing(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    return listing


@router.post("/listings", response_model=ListingResponse)
def create_listing(
    payload: ListingCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = Listing(
        user_id=current_user.id,
        status=ListingStatus(payload.status) if payload.status else ListingStatus.draft,
        image_urls=payload.image_urls or [],
        raw_photo_path=payload.raw_photo_path,
        storage_unit_name=payload.storage_unit_name,
        title=payload.title,
        description=payload.description,
        category_id=payload.category_id,
        category_suggestion=payload.category_suggestion,
        item_specifics=payload.item_specifics or {},
        tags=payload.tags or [],
        estimated_value=payload.estimated_value,
        start_price=payload.start_price,
        buy_it_now_price=payload.buy_it_now_price,
        min_acceptable_offer=payload.min_acceptable_offer,
        suggested_price=payload.suggested_price,
        listing_price=payload.listing_price,
        purchase_cost=payload.purchase_cost,
        fees_estimated=payload.fees_estimated,
        fees_actual=payload.fees_actual,
        shipping_cost=payload.shipping_cost,
        sale_price=payload.sale_price,
        condition=payload.condition,
        photo_quality_score=payload.photo_quality_score,
        quantity=payload.quantity or 1,
        platform_quantities=payload.platform_quantities or {},
        custom_labels=payload.custom_labels or [],
        last_refreshed=payload.last_refreshed,
        source_type=payload.source_type or "manual",
        source_metadata=payload.source_metadata or {},
        marketplace_data=normalize_marketplace_data(payload.marketplace_data),
        needs_review=payload.needs_review if payload.needs_review is not None else True,
        restricted_review_required=bool(payload.restricted_review_required),
        restricted_reasons=payload.restricted_reasons or [],
        detected_category_guess=payload.detected_category_guess,
        marketplace_allowed_status=payload.marketplace_allowed_status,
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return listing


@router.patch("/listings/{listing_id}", response_model=ListingResponse)
def update_listing(
    listing_id: int,
    payload: ListingUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    direct_updates = payload.model_dump(
        exclude_none=True,
        exclude={"quantity", "platform_quantities", "custom_labels", "marketplace_data"},
    )
    if "status" in direct_updates:
        direct_updates["status"] = ListingStatus(direct_updates["status"])
    for key, value in direct_updates.items():
        setattr(listing, key, value)
    if payload.marketplace_data is not None:
        listing.marketplace_data = normalize_marketplace_data(payload.marketplace_data)
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


@router.post("/listings/{listing_id}/approve", response_model=ListingApprovalResponse)
def approve_listing(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve a listing and optionally queue marketplace publish jobs.

    This endpoint is the server-side source of truth for approval transitions.
    It normalizes targets to include the priority channels (`ebay`, `facebook`),
    marks the listing as ready, clears review-required state, and then queues
    publish jobs when the operator workflow preference allows auto publish.
    """
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    return _approve_listing_for_user(db, listing=listing, current_user=current_user)


@router.post("/listings/approve-bulk", response_model=BulkListingApproveResponse)
def approve_listings_bulk(
    payload: BulkListingApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    approvals: list[dict] = []
    for listing_id in payload.listing_ids:
        listing = db.get(Listing, listing_id)
        if not listing:
            continue
        ensure_user_owns_resource(current_user, listing.user_id)
        approvals.append(_approve_listing_for_user(db, listing=listing, current_user=current_user))
    return {"approvals": approvals}


@router.delete("/listings/{listing_id}")
def delete_listing(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return _delete_listing_for_user(db, listing=listing, current_user=current_user)


@router.post("/listings/delete-bulk")
def delete_listings_bulk(
    payload: BulkListingApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deleted: list[dict] = []
    for listing_id in payload.listing_ids:
        listing = db.get(Listing, listing_id)
        if not listing:
            continue
        deleted.append(_delete_listing_for_user(db, listing=listing, current_user=current_user))
    return {"deleted": deleted, "deleted_count": len(deleted)}


@router.post("/listings/{listing_id}/photo-tools", response_model=PhotoEditResponse)
async def process_listing_photo(
    listing_id: int,
    edits: str = Form(default="{}"),
    remove_background: bool = Form(default=False),
    source_image: str | None = Form(default=None),
    photo: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)

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
    user_id: int | None = Form(None),
    storage_unit_name: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not photos:
        raise HTTPException(status_code=400, detail="No photos uploaded")

    storage = LocalStorage()
    listing_ids: list[int] = []
    uploads: list[str] = []

    scoped_user_id = resolve_user_scope(current_user, user_id)
    for photo in photos:
        content = await photo.read()
        if not content:
            continue
        suffix = Path(photo.filename or "").suffix or ".jpg"
        raw_path = storage.save_bytes(content, extension=suffix, prefix="uploads")
        listing = Listing(
            user_id=scoped_user_id,
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
    user_id: int | None = Form(None),
    storage_unit_name: str | None = Form(default=None),
    overnight_mode: bool = Form(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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

    batch = _create_storage_batch(
        db,
        resolve_user_scope(current_user, user_id),
        storage_unit_name,
        overnight_mode,
        photo_paths,
    )
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
def ingest_storage_unit_urls(
    payload: BatchStorageUnitUrlRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    storage = LocalStorage()
    photo_paths = [storage.save_from_url(str(url), prefix="batch_uploads") for url in payload.image_urls]
    if not photo_paths:
        raise HTTPException(status_code=400, detail="No valid image URLs received")
    batch = _create_storage_batch(
        db,
        resolve_user_scope(current_user, payload.user_id),
        payload.storage_unit_name,
        payload.overnight_mode,
        photo_paths,
    )
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
def list_storage_unit_batches(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.execute(
        select(StorageUnitBatch)
        .where(StorageUnitBatch.user_id == current_user.id)
        .order_by(StorageUnitBatch.id.desc())
    ).scalars().all()


@router.get("/batch/storage-unit/{batch_id}", response_model=StorageUnitBatchResponse)
def get_storage_unit_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    batch = db.get(StorageUnitBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    ensure_user_owns_resource(current_user, batch.user_id)
    return batch


@router.post("/batch/storage-unit/{batch_id}/run-overnight", response_model=StorageUnitBatchResponse)
def run_storage_unit_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    batch = db.get(StorageUnitBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    ensure_user_owns_resource(current_user, batch.user_id)
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
        "autonomous_crosspost_enabled": settings.autonomous_crosspost_enabled,
    }


@router.post("/config/toggle-autonomous")
def toggle_autonomous_mode(payload: AutonomousToggleRequest | None = None):
    if payload and payload.enabled is not None:
        settings.autonomous_mode = payload.enabled
    else:
        settings.autonomous_mode = not settings.autonomous_mode

    if payload and payload.crosspost_enabled is not None:
        settings.autonomous_crosspost_enabled = payload.crosspost_enabled

    return {
        "autonomous_mode": settings.autonomous_mode,
        "autonomous_dry_run": settings.autonomous_dry_run,
        "autonomous_crosspost_enabled": settings.autonomous_crosspost_enabled,
    }




@router.get("/listings/{listing_id}/pricing")
def get_listing_pricing(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    return PricingService().get_pricing(db, listing_id)


@router.post("/listings/{listing_id}/generate", response_model=ListingResponse)
def generate_listing(
    listing_id: int,
    payload: ListingGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)

    ai = ListingAIService()
    ebay = EbayService()

    generated = ai.generate(
        {
            "title_hint": listing.cluster.title_hint if listing.cluster else None,
            "source_type": listing.source_type,
            "image_count": len(listing.image_urls or []),
            "storage_unit_name": listing.storage_unit_name,
            "existing_specifics": listing.item_specifics or {},
            "existing_condition": listing.condition,
            "custom_labels": listing.custom_labels or [],
        }
    )
    price_data = ebay.enrich_price(generated["title"], payload.barcode)
    pricing_analysis = PricingIntelligenceService().recommend_price(
        db,
        listing_id,
        external_comparables=price_data.get("comparables") or [],
        estimated_value_override=generated.get("estimated_value"),
    )

    marketplace_data = dict(listing.marketplace_data or {})
    marketplace_data["ai_draft"] = {
        "draft_quality": generated.get("draft_quality"),
        "missing_information": generated.get("missing_information") or [],
        "photo_notes": generated.get("photo_notes") or [],
        "research_queries": generated.get("research_queries") or [],
        "generation_source": generated.get("generation_source"),
        "model_used": generated.get("model_used"),
    }
    marketplace_data["pricing_analysis"] = pricing_analysis

    source_metadata = dict(listing.source_metadata or {})
    source_metadata["listing_intelligence"] = {
        "title": generated.get("title"),
        "category_suggestion": generated.get("category_suggestion"),
        "condition": generated.get("condition"),
        "item_specifics": generated.get("item_specifics") or {},
        "tags": generated.get("tags") or [],
        "estimated_value": generated.get("estimated_value"),
        "missing_information": generated.get("missing_information") or [],
        "photo_notes": generated.get("photo_notes") or [],
        "research_queries": generated.get("research_queries") or [],
        "draft_quality": generated.get("draft_quality"),
        "generation_source": generated.get("generation_source"),
        "model_used": generated.get("model_used"),
    }

    listing.title = generated["title"]
    listing.description = generated["description"]
    listing.category_suggestion = generated["category_suggestion"]
    listing.condition = generated.get("condition") or listing.condition
    listing.item_specifics = generated.get("item_specifics") or listing.item_specifics
    listing.tags = generated["tags"]
    listing.estimated_value = generated.get("estimated_value") or listing.estimated_value
    listing.suggested_price = pricing_analysis["recommended_price"]
    listing.listing_price = pricing_analysis["recommended_price"]
    listing.marketplace_data = marketplace_data
    listing.source_metadata = source_metadata
    listing.needs_review = True
    listing.status = "ready"
    db.commit()
    db.refresh(listing)
    return listing


@router.get("/listings/{listing_id}/intelligence")
def get_listing_intelligence(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)

    pricing_analysis = (listing.marketplace_data or {}).get("pricing_analysis")
    if not pricing_analysis:
        pricing_analysis = PricingIntelligenceService().recommend_price(db, listing_id)

    intelligence = (listing.source_metadata or {}).get("listing_intelligence") or {}
    draft_meta = (listing.marketplace_data or {}).get("ai_draft") or {}
    readiness = {
        "needs_review": bool(listing.needs_review or listing.restricted_review_required),
        "missing_information_count": len(intelligence.get("missing_information") or []),
        "ready_for_publish": bool(
            listing.status == "ready"
            and not listing.restricted_review_required
            and bool(listing.title)
            and bool(listing.description)
        ),
    }

    return {
        "listing_id": listing.id,
        "intelligence": intelligence,
        "draft_meta": draft_meta,
        "pricing_analysis": pricing_analysis,
        "readiness": readiness,
    }
