from datetime import datetime
import io
import json
import zipfile

import httpx

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.schemas import (
    BatchStorageUnitUrlRequest,
    BulkListingApproveRequest,
    BulkListingApproveResponse,
    GooglePhotosImportRequest,
    ListingPhotoActionRequest,
    ListingPhotoSetPrimaryRequest,
    GooglePhotosWatchRequest,
    ListingApprovalResponse,
    ListingCreateRequest,
    ListingGenerateRequest,
    ListingRevisionRequest,
    ListingApproveQueueRequest,
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
    MarketplacePublishAttempt,
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
from app.services.intake_slate import IntakeSlateService
from app.services.listing_ai import ListingAIService
from app.services.listing_review import (
    derive_condition_data,
    derive_shipping_profile,
    normalize_listing_images,
    summarize_listing_readiness,
    sync_listing_review_state,
)
from app.services.media_lifecycle import purge_listing_media
from app.services.listing_workspace import normalize_marketplace_data
from app.services.marketplace_orchestrator import enqueue_crosspost_job, queue_publish
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.operator_command_service import OperatorCommandService
from app.services.profit_service import ProfitService
from app.services.storage import LocalStorage
from app.services.pricing_service import PricingService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.pricing_research_service import compute_listing_quality_summary
from app.services.photo_editor import PhotoEditorService
from app.services.listing_templates_service import listing_template_service
from app.services.amazon_media import AmazonProductMediaProvider
from app.services.amazon_product_discovery import AmazonProductDiscoveryService
from app.services.automation_bridge import AutomationBridgeError, submit_bridge_job, wait_for_bridge_job
from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.workers.tasks import (
    cluster_images_task,
    enqueue_storage_unit_batch_pipeline,
    process_overnight_storage_batches,
    process_photo_batch,
    process_marketplace_crosspost_job_task,
)

router = APIRouter()
inventory_service = InventoryService()
photo_editor_service = PhotoEditorService()
operator_command_service = OperatorCommandService()
intake_slate_service = IntakeSlateService()

_DEFAULT_WORKFLOW_PREFERENCES = {
    "review_before_publish": True,
    "auto_publish_after_approval": False,
    "bulk_approval_enabled": True,
    "listing_preview_mode": "marketplace",
    "default_preview_marketplace": "ebay",
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
        "default_preview_marketplace": str(stored.get("default_preview_marketplace") or _DEFAULT_WORKFLOW_PREFERENCES["default_preview_marketplace"]),
    }


def _approval_preflight_status(listing: Listing) -> dict:
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    preflight_state = marketplace_data.get("marketplace_preflight")
    by_marketplace = preflight_state.get("by_marketplace") if isinstance(preflight_state, dict) else {}
    approved_markets: list[str] = []
    blockers: dict[str, list[dict]] = {}
    warnings: dict[str, list[dict]] = {}
    ready_markets: list[str] = []

    for market in [str(value).strip().lower() for value in (marketplace_data.get("targets") or []) if str(value).strip()]:
        cached = by_marketplace.get(market) if isinstance(by_marketplace, dict) else None
        if not isinstance(cached, dict) or cached.get("status") in {None, "", "stale"}:
            continue
        status = str(cached.get("status") or "").strip().lower()
        if status in {"ready", "ready_with_warnings", "published"}:
            approved_markets.append(market)
            ready_markets.append(market)
        elif status == "blocked":
            blockers[market] = [item for item in (cached.get("blockers") or []) if isinstance(item, dict)]
        if cached.get("warnings"):
            warnings[market] = [item for item in (cached.get("warnings") or []) if isinstance(item, dict)]

    return {
        "approved_markets": approved_markets,
        "ready_markets": ready_markets,
        "blockers": blockers,
        "warnings": warnings,
        "has_blockers": bool(blockers),
        "has_targets": bool(approved_markets or blockers or warnings),
    }


def _serialize_listing_response(listing: Listing) -> dict:
    sync_listing_review_state(listing=listing)
    base = ListingResponse.model_validate(listing).model_dump()
    latest_rows_by_marketplace: dict[str, MarketplaceListing] = {}
    for row in sorted(
        listing.marketplace_listings or [],
        key=lambda item: (
            item.updated_at.isoformat() if item.updated_at else "",
            item.id or 0,
        ),
        reverse=True,
    ):
        key = row.marketplace.value
        if key in latest_rows_by_marketplace:
            continue
        latest_rows_by_marketplace[key] = row

    base["marketplace_statuses"] = [
        {
            "marketplace": row.marketplace.value,
            "status": row.status.value if hasattr(row.status, "value") else str(row.status),
            "marketplace_listing_id": row.marketplace_listing_id,
            "raw_response": row.raw_response,
        }
        for row in latest_rows_by_marketplace.values()
    ]
    base["readiness_summary"] = summarize_listing_readiness(
        listing_images=listing.listing_images,
        condition_data=listing.condition_data,
        shipping_profile=listing.shipping_profile,
        listing={
            "category_id": listing.category_id,
            "category_suggestion": listing.category_suggestion,
            "listing_price": listing.listing_price,
            "suggested_price": listing.suggested_price,
        },
    )
    pricing_analysis = ((listing.marketplace_data or {}).get("pricing_analysis") or {}) if isinstance(listing.marketplace_data, dict) else {}
    base["quality_summary"] = compute_listing_quality_summary(listing, pricing_analysis=pricing_analysis)
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    base["marketplace_preflight_summary"] = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else None
    latest_attempt = None
    if listing.publish_attempts:
        attempts = [attempt for attempt in listing.publish_attempts if isinstance(attempt, MarketplacePublishAttempt)]
        attempts = sorted(
            attempts,
            key=lambda attempt: (
                attempt.updated_at.isoformat() if attempt.updated_at else "",
                attempt.id or 0,
            ),
            reverse=True,
        )
        latest_attempt = attempts[0] if attempts else None
    base["latest_publish_attempt"] = (
        {
            "id": latest_attempt.id,
            "listing_id": latest_attempt.listing_id,
            "marketplace": latest_attempt.marketplace.value if hasattr(latest_attempt.marketplace, "value") else str(latest_attempt.marketplace),
            "started_at": latest_attempt.started_at,
            "finished_at": latest_attempt.finished_at,
            "dry_run": latest_attempt.dry_run,
            "preflight_status": latest_attempt.preflight_status,
            "payload_snapshot": latest_attempt.payload_snapshot,
            "payload_hash": latest_attempt.payload_hash,
            "inventory_item_sku": latest_attempt.inventory_item_sku,
            "offer_id": latest_attempt.offer_id,
            "marketplace_listing_id": latest_attempt.marketplace_listing_id,
            "marketplace_status": latest_attempt.marketplace_status,
            "translated_error": latest_attempt.translated_error,
            "raw_error": latest_attempt.raw_error,
            "retryable": latest_attempt.retryable,
            "retry_count": latest_attempt.retry_count,
            "previous_attempt_id": latest_attempt.previous_attempt_id,
            "job_id": latest_attempt.job_id,
            "task_id": latest_attempt.task_id,
        }
        if latest_attempt
        else None
    )
    return base


def _apply_listing_review_defaults(listing: Listing) -> None:
    listing.listing_images = normalize_listing_images(
        listing_images=listing.listing_images,
        image_urls=listing.image_urls,
        source_url=(listing.source_metadata or {}).get("source_image_url") if isinstance(listing.source_metadata, dict) else None,
        source_page_url=(listing.source_metadata or {}).get("amazon_source_page_url") if isinstance(listing.source_metadata, dict) else None,
        source_platform=listing.source_type or "upload",
        default_is_reference=bool(
            listing.source_type in {"amazon_vine", "google_photos_album"}
            or str((listing.source_metadata or {}).get("source_marketplace") or "").strip()
        ),
        approved=listing.source_type in {"upload", "storage_batch", "google_photos_album"},
    )
    listing.image_urls = [item["storage_path"] for item in (listing.listing_images or []) if item.get("operator_state") != "rejected"]
    listing.condition_data = derive_condition_data(
        listing={"condition": listing.condition, "source_type": listing.source_type},
        source_type=listing.source_type,
        source_metadata=listing.source_metadata,
        existing=listing.condition_data,
    )
    listing.shipping_profile = derive_shipping_profile(
        listing={"title": listing.title, "description": listing.description},
        item_specifics=listing.item_specifics,
        existing=listing.shipping_profile,
    )


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



def _approve_listing_for_user(
    db: Session,
    *,
    listing: Listing,
    current_user: User,
    queue_automatically: bool | None = None,
) -> dict:
    marketplace_data = dict(listing.marketplace_data or {})
    current_targets = marketplace_data.get("targets")
    targets = [str(value).strip().lower() for value in (current_targets or []) if str(value).strip()]
    for target in ("ebay", "facebook"):
        if target not in targets:
            targets.append(target)
    listing.marketplace_data = normalize_marketplace_data(
        {
            **marketplace_data,
            "targets": targets,
            "crosspost_mode": str(marketplace_data.get("crosspost_mode") or "approval_required"),
        }
    )
    preflight_service = MarketplacePreflightService()
    preflight_results: dict[str, dict] = {}
    blockers_by_market: dict[str, list[dict]] = {}
    ready_markets: list[str] = []
    warning_markets: list[str] = []
    for market in targets:
        preflight = preflight_service.preflight_listing(db, listing, market)
        preflight_service.cache_preflight_summary(db, listing, preflight)
        preflight_results[market] = preflight
        blockers = [item for item in (preflight.get("blockers") or []) if isinstance(item, dict)]
        warnings = [item for item in (preflight.get("warnings") or []) if isinstance(item, dict)]
        if blockers:
            blockers_by_market[market] = blockers
        else:
            ready_markets.append(market)
        if warnings:
            warning_markets.append(market)

    publish_ready = bool(targets) and not blockers_by_market and all(
        str(preflight_results.get(market, {}).get("status") or "").strip().lower() in {"ready", "ready_with_warnings", "published"}
        for market in targets
    )

    source_metadata = dict(listing.source_metadata or {})
    source_metadata["operator_approved_at"] = datetime.utcnow().isoformat() if publish_ready else None
    source_metadata["operator_approved_by_user_id"] = current_user.id if publish_ready else None
    source_metadata["approval_attempted_at"] = datetime.utcnow().isoformat()
    if blockers_by_market:
        source_metadata["approval_blockers"] = blockers_by_market
    listing.source_metadata = source_metadata

    if publish_ready:
        listing.status = ListingStatus.ready
        listing.needs_review = False
    else:
        listing.status = ListingStatus.draft
        listing.needs_review = False
    listing.marketplace_data = normalize_marketplace_data(
        {
            **(listing.marketplace_data or {}),
            "targets": targets,
            "crosspost_mode": str(marketplace_data.get("crosspost_mode") or "approval_required"),
            "approval_ready_markets": ready_markets,
            "approval_warning_markets": warning_markets,
            "approval_blockers": blockers_by_market,
            "approval_publishable": publish_ready,
        }
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)

    preferences = _workflow_preferences(current_user)
    results: list[dict] = []
    should_auto_queue = (preferences.get("auto_publish_after_approval") if queue_automatically is None else queue_automatically) and publish_ready
    if should_auto_queue:
        results = queue_publish(db, listing.id, targets)
        db.refresh(listing)
    return {
        "listing": _serialize_listing_response(listing),
        "auto_publish_after_approval": bool(preferences.get("auto_publish_after_approval")),
        "approval_publishable": publish_ready,
        "approval_blockers": blockers_by_market,
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


_UPLOAD_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_UPLOAD_ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _validate_photo_upload(*, upload: UploadFile, content: bytes) -> str:
    if not content:
        raise HTTPException(status_code=400, detail=f"{upload.filename or 'photo'} is empty.")
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in _UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"{upload.filename or 'photo'} has an unsupported file extension.")
    content_type = str(upload.content_type or "").lower().strip()
    if content_type not in _UPLOAD_ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail=f"{upload.filename or 'photo'} has an unsupported MIME type.")
    return ".jpg" if suffix == ".jpeg" else suffix


def _normalized_listing_photo_response(listing: Listing) -> dict:
    sync_listing_review_state(listing=listing)
    return {
        "listing": _serialize_listing_response(listing),
        "photo_summary": summarize_listing_readiness(
            listing_images=listing.listing_images,
            condition_data=listing.condition_data,
            shipping_profile=listing.shipping_profile,
            listing={
                "category_id": listing.category_id,
                "category_suggestion": listing.category_suggestion,
                "listing_price": listing.listing_price,
                "suggested_price": listing.suggested_price,
            },
        ),
    }


def _mutate_listing_images(
    *,
    listing: Listing,
    matcher,
    mutator,
) -> bool:
    images = normalize_listing_images(
        listing_images=listing.listing_images,
        image_urls=listing.image_urls,
        source_url=(listing.source_metadata or {}).get("source_image_url") if isinstance(listing.source_metadata, dict) else None,
        source_page_url=(listing.source_metadata or {}).get("amazon_source_page_url") if isinstance(listing.source_metadata, dict) else None,
        source_platform=listing.source_type or "upload",
        default_is_reference=bool(
            str(listing.source_type or "").strip().lower() in {"amazon_vine", "google_photos_album"}
            or str((listing.source_metadata or {}).get("source_marketplace") or "").strip()
        ),
        approved=str(listing.source_type or "").strip().lower() in {"upload", "storage_batch"},
    )
    changed = False
    next_images: list[dict] = []
    for image in images:
        next_image = dict(image)
        if matcher(next_image):
            mutator(next_image)
            changed = True
        next_images.append(next_image)
    if changed:
        listing.listing_images = normalize_listing_images(listing_images=next_images)
        listing.image_urls = [item["storage_path"] for item in (listing.listing_images or []) if item.get("operator_state") != "rejected"]
    return changed


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
                listing_images=[
                    {
                        "storage_path": _to_public_image_url(processed),
                        "source_url": url,
                        "source_page_url": str(album_url),
                        "source_platform": "google_photos",
                        "role": "primary",
                        "confidence": 0.86,
                        "operator_state": "suggested",
                        "display_order": 0,
                        "is_reference": False,
                        "label": "Google Photos intake",
                    }
                ],
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
            _apply_listing_review_defaults(listing)
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
            listing_images=[
                {
                    "storage_path": raw_path,
                    "source_platform": "upload",
                    "role": "primary",
                    "confidence": 1.0,
                    "operator_state": "approved",
                    "display_order": 0,
                    "is_reference": False,
                    "label": "Batch intake photo",
                }
            ],
            raw_photo_path=raw_path,
            storage_unit_name=storage_unit_name,
            source_type="storage_batch",
        )
        _apply_listing_review_defaults(listing)
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


class DashboardOperatorCommandRequest(BaseModel):
    prompt: str
    dry_run: bool = True
    apply_live: bool = False
    confirmation_phrase: str | None = None


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
    settings_payload = intake_slate_service.settings_for_user(current_user)
    return {
        "enabled": bool(settings_payload.get("enabled")),
        "auto_enrich": bool(settings_payload.get("auto_draft_listing", True)),
        "album_url": str(settings_payload.get("album_url") or "").strip(),
        "last_synced_at": settings_payload.get("last_synced_at"),
        "last_imported_count": int(settings_payload.get("last_imported_count") or 0),
        "last_error": str(settings_payload.get("last_error") or "").strip() or None,
    }


@router.put("/import/google-photos/watch")
def update_google_photos_watch_settings(
    payload: GooglePhotosWatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current = intake_slate_service.settings_for_user(current_user)
    saved = intake_slate_service.save_settings(
        db=db,
        user=current_user,
        payload={
            **current,
            "enabled": bool(payload.enabled),
            "album_url": str(payload.album_url),
            "auto_draft_listing": bool(payload.auto_enrich),
            "last_error": None,
        },
    )
    return {
        "enabled": bool(saved.get("enabled")),
        "auto_enrich": bool(saved.get("auto_draft_listing", True)),
        "album_url": str(saved.get("album_url") or "").strip(),
        "last_synced_at": saved.get("last_synced_at"),
        "last_imported_count": int(saved.get("last_imported_count") or 0),
        "last_error": str(saved.get("last_error") or "").strip() or None,
    }


@router.post("/import/google-photos/watch/run")
def run_google_photos_watch(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = intake_slate_service.monitor_google_album(db, user=current_user)
        updated = intake_slate_service.settings_for_user(current_user)
        return {
            "watch": {
                "enabled": bool(updated.get("enabled")),
                "auto_enrich": bool(updated.get("auto_draft_listing", True)),
                "album_url": str(updated.get("album_url") or "").strip(),
                "last_synced_at": updated.get("last_synced_at"),
                "last_imported_count": int(updated.get("last_imported_count") or 0),
                "last_error": str(updated.get("last_error") or "").strip() or None,
            },
            "result": {
                "scanned": int(result.get("scanned") or 0),
                "new_items": int(result.get("imported") or 0),
                "slates_detected": int(result.get("slates_detected") or 0),
                "assigned_photos": int(result.get("assigned_photos") or 0),
                "drafts_created": int(result.get("drafts_created") or 0),
                "duplicates": int(result.get("duplicates") or 0),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/clusters")
def get_clusters(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    clusters = db.execute(select(Cluster).where(Cluster.user_id == current_user.id)).scalars().all()
    return [{"id": c.id, "title_hint": c.title_hint, "image_count": len(c.images)} for c in clusters]


@router.get("/listings")
def get_listings(
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=250),
    source_type: str | None = Query(default=None),
    queue: str | None = Query(default=None, max_length=32),
    search: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the operator catalog.

    The legacy unpaged response is retained for integrations that have not
    opted into paging.  The web workspace always sends both page parameters so
    it never blocks on serializing an entire recovery/import history.
    """
    filters = []
    normalized_source = str(source_type or "").strip().lower()
    if normalized_source and normalized_source != "all":
        filters.append(Listing.source_type == normalized_source)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(or_(Listing.title.ilike(pattern), Listing.description.ilike(pattern)))
    statement = select(Listing).where(*filters)

    def _bucket(listing: Listing) -> str:
        """Resolve catalog queues before pagination so totals remain honest."""
        labels = {str(value).strip().lower() for value in (listing.custom_labels or [])}
        if listing.sold_at is not None or int(listing.quantity or 1) <= 0:
            return "sold"
        if listing.status == ListingStatus.rejected or {"archived_vine", "archived_sold"} & labels:
            return "archived"
        if listing.status == ListingStatus.draft:
            return "drafts"
        if str(listing.status).lower() == "error" or str(listing.ebay_publish_status or "").upper() == "FAILED":
            return "failed"
        if str(listing.ebay_publish_status or "").upper() == "POSTED" or bool(listing.ebay_listing_id):
            return "published"
        source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
        is_recovery = str(listing.source_type or "") == "media_inventory_recovery"
        explicitly_approved = bool(source_metadata.get("operator_approved_at"))
        if is_recovery and not explicitly_approved:
            return "drafts"
        if listing.restricted_review_required or listing.needs_review:
            return "review"
        if listing.status == ListingStatus.ready:
            preflight_state = (listing.marketplace_data or {}).get("marketplace_preflight") if isinstance(listing.marketplace_data, dict) else {}
            by_marketplace = preflight_state.get("by_marketplace") if isinstance(preflight_state, dict) else {}
            target_markets = [str(value).strip().lower() for value in (listing.marketplace_data or {}).get("targets") or [] if str(value).strip()]
            approved_target = any(
                isinstance(by_marketplace, dict)
                and str((by_marketplace.get(market) or {}).get("status") or "").strip().lower() in {"ready", "ready_with_warnings", "published"}
                for market in target_markets
            )
            return "ready" if explicitly_approved and approved_target else "drafts"
        return "review"

    normalized_queue = str(queue or "").strip().lower()
    if page is None and page_size is None:
        rows = db.execute(statement.order_by(Listing.updated_at.desc())).scalars().all()
        if normalized_queue and normalized_queue != "all":
            rows = [row for row in rows if _bucket(row) == normalized_queue]
        return [_serialize_listing_response(listing) for listing in rows]

    resolved_page = page or 1
    resolved_page_size = page_size or 25
    if normalized_queue and normalized_queue != "all":
        matching_rows = [
            row for row in db.execute(statement.order_by(Listing.updated_at.desc())).scalars().all()
            if _bucket(row) == normalized_queue
        ]
        total = len(matching_rows)
        start = (resolved_page - 1) * resolved_page_size
        rows = matching_rows[start:start + resolved_page_size]
    else:
        total = int(db.execute(select(func.count()).select_from(Listing).where(*filters)).scalar_one())
        rows = db.execute(
            statement.order_by(Listing.updated_at.desc())
            .offset((resolved_page - 1) * resolved_page_size)
            .limit(resolved_page_size)
        ).scalars().all()
    return {
        "items": [_serialize_listing_response(listing) for listing in rows],
        "total": total,
        "page": resolved_page,
        "page_size": resolved_page_size,
        "total_pages": max(1, (total + resolved_page_size - 1) // resolved_page_size),
    }


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

    def _trusted_amazon_listing_images(image_urls: list[str], *, source_page_url: str | None, asin: str | None, product_name: str | None) -> list[dict]:
        cleaned_urls = [str(url).strip() for url in image_urls if str(url).strip()]
        return normalize_listing_images(
            listing_images=[
                {
                    "storage_path": url,
                    "source_page_url": source_page_url,
                    "source_platform": "amazon",
                    "operator_state": "approved",
                    "is_reference": False,
                    "metadata": {
                        "source": "amazon_vine",
                        "asin": asin,
                        "product_name": product_name,
                        "trusted_catalog_match": True,
                    },
                }
                for url in cleaned_urls
            ],
            source_page_url=source_page_url,
            source_platform="amazon",
            default_is_reference=False,
            approved=True,
        )
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
        # Preserve the exact source-page provenance when attaching a recovered
        # gallery.  The old compatibility endpoint referenced `item_url`
        # below without defining it, which aborted successful image attachment
        # after the browser had already found product media.
        item_url = (
            str(item.item_url).strip() if item and item.item_url else None
        ) or str(source_metadata.get("item_url") or source_metadata.get("amazon_source_page_url") or "").strip() or None

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
            listing.listing_images = _trusted_amazon_listing_images(
                gallery_urls,
                source_page_url=item_url,
                asin=asin,
                product_name=product_name,
            )
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
            listing.listing_images = _trusted_amazon_listing_images(
                [cache.primary_image_url],
                source_page_url=item_url,
                asin=asin,
                product_name=product_name,
            )
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
                    listing.listing_images = _trusted_amazon_listing_images(
                        refreshed_gallery,
                        source_page_url=item_url,
                        asin=asin,
                        product_name=product_name,
                    )
                    source_meta = dict(listing.source_metadata or {})
                    source_meta["asin"] = asin
                    source_meta["product_name"] = product_name
                    listing.source_metadata = source_meta
                    db.add(listing)
                    updated += 1
                    continue
                if cache.primary_image_url:
                    listing.image_urls = [cache.primary_image_url]
                    listing.listing_images = _trusted_amazon_listing_images(
                        [cache.primary_image_url],
                        source_page_url=item_url,
                        asin=asin,
                        product_name=product_name,
                    )
                    source_meta = dict(listing.source_metadata or {})
                    source_meta["asin"] = asin
                    source_meta["product_name"] = product_name
                    listing.source_metadata = source_meta
                    db.add(listing)
                    updated += 1
                    continue
        if force_refresh:
            listing.image_urls = []
            listing.listing_images = []
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
    return _serialize_listing_response(listing)


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
        listing_images=payload.listing_images or [],
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
        condition_data=payload.condition_data or {},
        photo_quality_score=payload.photo_quality_score,
        quantity=payload.quantity or 1,
        platform_quantities=payload.platform_quantities or {},
        custom_labels=payload.custom_labels or [],
        last_refreshed=payload.last_refreshed,
        source_type=payload.source_type or "manual",
        source_metadata=payload.source_metadata or {},
        shipping_profile=payload.shipping_profile or {},
        marketplace_data=normalize_marketplace_data(payload.marketplace_data),
        needs_review=payload.needs_review if payload.needs_review is not None else True,
        restricted_review_required=bool(payload.restricted_review_required),
        restricted_reasons=payload.restricted_reasons or [],
        detected_category_guess=payload.detected_category_guess,
        marketplace_allowed_status=payload.marketplace_allowed_status,
    )
    _apply_listing_review_defaults(listing)
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return _serialize_listing_response(listing)


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
    _apply_listing_review_defaults(listing)
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
    return _serialize_listing_response(listing)


@router.post("/listings/approve-and-queue")
def approve_and_queue_listings(
    payload: ListingApproveQueueRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    targets = [str(value).strip().lower() for value in (payload.marketplaces or ["ebay"]) if str(value).strip()]
    if "ebay" in targets and (not payload.confirm_live_publish or str(payload.confirmation_phrase or "").strip() != "QUEUE LIVE EBAY READY LISTINGS"):
        raise HTTPException(status_code=400, detail="Live eBay queue requires explicit confirmation.")
    results = []
    for listing_id in list(dict.fromkeys(payload.listing_ids or [])):
        listing = db.get(Listing, listing_id)
        if not listing:
            results.append({"listing_id": listing_id, "status": "missing"})
            continue
        ensure_user_owns_resource(current_user, listing.user_id)
        source_metadata = dict(listing.source_metadata or {})
        source_metadata["approval_attempted_at"] = datetime.utcnow().isoformat()
        source_metadata["operator_approved_by_user_id"] = current_user.id
        listing.source_metadata = source_metadata
        listing.marketplace_data = normalize_marketplace_data(
            {
                **(listing.marketplace_data or {}),
                "targets": targets,
                "crosspost_mode": str((listing.marketplace_data or {}).get("crosspost_mode") or "approval_required"),
                "approval_requested": True,
                "approval_publishable": False,
            }
        )
        db.add(listing)
        db.commit()
        result = enqueue_crosspost_job(
            db,
            listing=listing,
            target_markets=targets,
            requested_mode="operator_approved_live_queue",
            execution_plan={"operator_live_confirmed": True, "queued_from": "approve_and_queue", "targets": targets},
        )
        results.append({"listing_id": listing.id, "status": result.get("status"), "results": [], "job_id": result.get("job_id"), "task_id": result.get("task_id"), "error": result.get("error")})
    return {"results": results}


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
    listing.listing_images = [
        *(listing.listing_images or []),
        {
            "storage_path": saved_path,
            "source_platform": "upload",
            "role": "alternate_angle" if (listing.listing_images or listing.image_urls) else "primary",
            "confidence": 1.0,
            "operator_state": "approved",
            "display_order": len(listing.listing_images or []),
            "is_reference": False,
            "label": "Edited photo",
            "metadata": {
                "filter_name": parsed.filter_name,
                "remove_background": bool(remove_background),
            },
        },
    ]
    _apply_listing_review_defaults(listing)
    db.add(listing)
    db.commit()
    db.refresh(listing)

    return PhotoEditResponse(
        image_url=_to_public_image_url(saved_path),
        image_urls=[_to_public_image_url(path) for path in (listing.image_urls or [])],
    )


@router.post("/listings/{listing_id}/photos/upload")
async def upload_listing_photos(
    listing_id: int,
    photos: list[UploadFile] = File(...),
    role: str | None = Form(default=None),
    note: str | None = Form(default=None),
    source: str = Form(default="actual_upload"),
    operator_state: str = Form(default="suggested"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    if not photos:
        raise HTTPException(status_code=400, detail="No photos uploaded")

    normalized_state = str(operator_state or "suggested").strip().lower()
    if normalized_state not in {"suggested", "approved"}:
        raise HTTPException(status_code=400, detail="operator_state must be suggested or approved")

    normalized_role = str(role or "").strip().lower() or None
    normalized_source = str(source or "actual_upload").strip().lower() or "actual_upload"
    storage = LocalStorage()
    base_images = normalize_listing_images(
        listing_images=listing.listing_images,
        image_urls=listing.image_urls,
        source_url=(listing.source_metadata or {}).get("source_image_url") if isinstance(listing.source_metadata, dict) else None,
        source_page_url=(listing.source_metadata or {}).get("amazon_source_page_url") if isinstance(listing.source_metadata, dict) else None,
        source_platform=listing.source_type or "upload",
        default_is_reference=False,
        approved=False,
    )
    next_images = list(base_images)
    uploaded_paths: list[str] = []
    start_order = len(next_images)

    for offset, photo in enumerate(photos):
        content = await photo.read()
        suffix = _validate_photo_upload(upload=photo, content=content)
        saved_path = storage.save_bytes(content, extension=suffix, prefix=f"listing-photos/{listing.user_id}/{listing.id}")
        uploaded_paths.append(saved_path)
        next_images.append(
            {
                "storage_path": saved_path,
                "source_platform": normalized_source,
                "role": normalized_role or ("primary" if not next_images else "alternate_angle"),
                "confidence": 1.0,
                "operator_state": normalized_state,
                "display_order": start_order + offset,
                "is_reference": False,
                "label": "Actual item photo upload",
                "metadata": {
                    "original_filename": str(photo.filename or "").strip() or None,
                    "content_type": str(photo.content_type or "").strip() or None,
                    "note": str(note or "").strip() or None,
                },
            }
        )

    listing.listing_images = normalize_listing_images(listing_images=next_images)
    listing.image_urls = [item["storage_path"] for item in (listing.listing_images or []) if item.get("operator_state") != "rejected"]
    db.add(listing)
    db.commit()
    db.refresh(listing)
    response = _normalized_listing_photo_response(listing)
    response["uploaded_paths"] = [_to_public_image_url(path) for path in uploaded_paths]
    return response


@router.post("/listings/{listing_id}/photos/approve")
def approve_listing_photos(
    listing_id: int,
    payload: ListingPhotoActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    target_paths = {str(path).strip() for path in (payload.storage_paths or []) if str(path).strip()}
    if not target_paths:
        raise HTTPException(status_code=400, detail="No photo storage paths provided")

    changed = _mutate_listing_images(
        listing=listing,
        matcher=lambda image: str(image.get("storage_path") or "").strip() in target_paths,
        mutator=lambda image: image.update({
            "operator_state": "approved",
            "operator_approved": True,
            "operator_rejected": False,
            "is_reference": False,
        }),
    )
    if not changed:
        raise HTTPException(status_code=404, detail="No matching listing photos found")
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return _normalized_listing_photo_response(listing)


@router.post("/listings/{listing_id}/photos/reject")
def reject_listing_photos(
    listing_id: int,
    payload: ListingPhotoActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    target_paths = {str(path).strip() for path in (payload.storage_paths or []) if str(path).strip()}
    if not target_paths:
        raise HTTPException(status_code=400, detail="No photo storage paths provided")

    changed = _mutate_listing_images(
        listing=listing,
        matcher=lambda image: str(image.get("storage_path") or "").strip() in target_paths,
        mutator=lambda image: image.update({
            "operator_state": "rejected",
            "operator_approved": False,
            "operator_rejected": True,
        }),
    )
    if not changed:
        raise HTTPException(status_code=404, detail="No matching listing photos found")
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return _normalized_listing_photo_response(listing)


@router.post("/listings/{listing_id}/photos/set-primary")
def set_listing_photo_primary(
    listing_id: int,
    payload: ListingPhotoSetPrimaryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    target = str(payload.storage_path or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="storage_path is required")

    images = normalize_listing_images(
        listing_images=listing.listing_images,
        image_urls=listing.image_urls,
        source_url=(listing.source_metadata or {}).get("source_image_url") if isinstance(listing.source_metadata, dict) else None,
        source_page_url=(listing.source_metadata or {}).get("amazon_source_page_url") if isinstance(listing.source_metadata, dict) else None,
        source_platform=listing.source_type or "upload",
        default_is_reference=bool(
            str(listing.source_type or "").strip().lower() in {"amazon_vine", "google_photos_album"}
            or str((listing.source_metadata or {}).get("source_marketplace") or "").strip()
        ),
        approved=str(listing.source_type or "").strip().lower() in {"upload", "storage_batch"},
    )
    matched = any(str(image.get("storage_path") or "").strip() == target for image in images)
    if not matched:
        raise HTTPException(status_code=404, detail="No matching listing photo found")
    reordered = [image for image in images if str(image.get("storage_path") or "").strip() == target]
    reordered.extend(image for image in images if str(image.get("storage_path") or "").strip() != target)
    listing.listing_images = normalize_listing_images(listing_images=reordered)
    listing.image_urls = [item["storage_path"] for item in (listing.listing_images or []) if item.get("operator_state") != "rejected"]
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return _normalized_listing_photo_response(listing)


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
            listing_images=[
                {
                    "storage_path": raw_path,
                    "source_platform": "upload",
                    "role": "primary",
                    "confidence": 1.0,
                    "operator_state": "approved",
                    "display_order": 0,
                    "is_reference": False,
                    "label": "Loose upload",
                }
            ],
            raw_photo_path=raw_path,
            storage_unit_name=storage_unit_name,
            source_type="upload",
        )
        _apply_listing_review_defaults(listing)
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


@router.post("/dashboard/operator-command")
async def run_dashboard_operator_command(
    payload: DashboardOperatorCommandRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await operator_command_service.handle_prompt(
        db,
        user=current_user,
        prompt=payload.prompt,
        dry_run=payload.dry_run,
        apply_live=payload.apply_live,
        confirmation_phrase=payload.confirmation_phrase,
    )




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


@router.post("/listings/{listing_id}/request-revision", response_model=ListingResponse)
def request_listing_revision(
    listing_id: int,
    payload: ListingRevisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    metadata = dict(listing.source_metadata or {})
    history = list(metadata.get("operator_revision_requests") or [])
    history.append({"fields": list(dict.fromkeys(payload.fields or [])), "note": (payload.note or "").strip() or None, "requested_at": datetime.utcnow().isoformat()})
    metadata["operator_revision_requests"] = history[-20:]
    metadata["rework_state"] = "queued_for_ai_revision"
    listing.source_metadata = metadata
    listing.status = "draft"
    listing.needs_review = False
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return _serialize_listing_response(listing)


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
    # Generation is a submission for operator review, never an approval.
    listing.needs_review = True
    listing.status = "PROCESSED"
    _apply_listing_review_defaults(listing)
    db.commit()
    db.refresh(listing)
    return _serialize_listing_response(listing)


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
    readiness_summary = summarize_listing_readiness(
        listing_images=listing.listing_images,
        condition_data=listing.condition_data,
        shipping_profile=listing.shipping_profile,
        listing={
            "category_id": listing.category_id,
            "category_suggestion": listing.category_suggestion,
            "listing_price": listing.listing_price,
            "suggested_price": listing.suggested_price,
        },
    )
    readiness = {
        "needs_review": bool(listing.needs_review or listing.restricted_review_required),
        "missing_information_count": len(intelligence.get("missing_information") or []),
        "ready_for_publish": bool(
            listing.status == "ready"
            and not listing.restricted_review_required
            and bool(listing.title)
            and bool(listing.description)
            and not readiness_summary.get("blocked_for_publish")
        ),
        "review_summary": readiness_summary,
        "quality_summary": compute_listing_quality_summary(listing, pricing_analysis=pricing_analysis),
    }

    return {
        "listing_id": listing.id,
        "intelligence": intelligence,
        "draft_meta": draft_meta,
        "pricing_analysis": pricing_analysis,
        "readiness": readiness,
    }
