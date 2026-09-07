from datetime import date, datetime
import io
import json
import zipfile

import httpx

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import asc, and_, case, delete, desc, exists, func, not_, or_, select, update, cast, String
from sqlalchemy.orm import Session
from sqlalchemy.orm import selectinload

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
    ListingRevision,
    ListingCorrectionJob,
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
from app.services.ebay_service import revise_ebay_listing
from app.services.embedding import fake_clip_embedding
from app.services.google_photos import GooglePhotosService
from app.services.image_pipeline import ImagePipelineService
from app.services.inventory_service import InventorySafetyError, InventoryService
from app.services.intake_slate import IntakeSlateService
from app.services.listing_ai import ListingAIService
from app.services.listing_provenance import mark_manual_field_provenance
from app.services.listing_review import (
    derive_condition_data,
    derive_shipping_profile,
    normalize_listing_images,
    summarize_listing_readiness,
    sync_listing_review_state,
)
from app.services.listing_specificity import GENERIC_CAPTION_TITLES, classify_listing_reviewability
from app.services.media_lifecycle import purge_listing_media
from app.services.listing_workspace import normalize_marketplace_data
from app.services.marketplace_orchestrator import enqueue_crosspost_job, queue_publish
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.operator_command_service import OperatorCommandService
from app.services.profit_service import ProfitService
from app.services.process_notifications import create_process_notification
from app.services.storage import LocalStorage
from app.services.pricing_service import PricingService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.pricing_research_service import compute_listing_quality_summary
from app.services.photo_editor import PhotoEditorService
from app.services.listing_templates_service import listing_template_service
from app.services.amazon_media import AmazonProductMediaProvider
from app.services.amazon_product_discovery import AmazonProductDiscoveryService
from app.services.automation_bridge import AutomationBridgeError, submit_bridge_job, wait_for_bridge_job
from app.services.vine_import_service import VineImportService
from app.models.enums import EbayPublishStatus, ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.workers.tasks import (
    cluster_images_task,
    enqueue_storage_unit_batch_pipeline,
    process_overnight_storage_batches,
    process_photo_batch,
    process_marketplace_crosspost_job_task,
    process_listing_correction_jobs_task,
)

router = APIRouter()
inventory_service = InventoryService()
photo_editor_service = PhotoEditorService()
operator_command_service = OperatorCommandService()
intake_slate_service = IntakeSlateService()
vine_import_service = VineImportService()


def _is_merged_recovery_child(listing: Listing) -> bool:
    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    return bool(recovery.get("merged_into_recovery_item_id") or recovery.get("merged_into_recovery_group_id"))


def _listing_bucket_expression():
    custom_labels_text = func.coalesce(func.lower(cast(Listing.custom_labels, String)), "")
    generic_caption = func.lower(func.trim(cast(Listing.title, String))).in_(
        sorted(GENERIC_CAPTION_TITLES)
    )
    return case(
        (or_(Listing.sold_at.is_not(None), Listing.quantity <= 0), "sold"),
        (custom_labels_text.contains("archived_vine"), "archived"),
        (or_(Listing.processing_state == "needs_attention", Listing.processing_state == "blocked"), "needs_attention"),
        (generic_caption, "needs_attention"),
        (or_(Listing.status == ListingStatus.FAILED, Listing.ebay_publish_status == "FAILED"), "failed"),
        (or_(Listing.status == ListingStatus.PUBLISHED, Listing.ebay_publish_status == "POSTED", Listing.ebay_listing_id.is_not(None)), "published"),
        (or_(Listing.restricted_review_required.is_(True), Listing.needs_review.is_(True)), "review"),
        (and_(
            Listing.status == ListingStatus.ready,
            Listing.source_metadata["operator_approved_at"].as_string().is_not(None),
        ), "ready"),
        else_="drafts",
    )


def _listing_bucket(listing: Listing) -> str:
    labels = {str(value).strip().lower() for value in (listing.custom_labels or [])}
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    explicitly_approved = bool(source_metadata.get("operator_approved_at"))
    reviewability = classify_listing_reviewability(
        title=listing.title,
        description=listing.description,
        category=listing.category_suggestion,
        item_specifics=listing.item_specifics if isinstance(listing.item_specifics, dict) else {},
        source_metadata=source_metadata,
        has_images=bool(listing.image_urls or []),
    )
    if listing.sold_at is not None or int(listing.quantity or 1) <= 0:
        return "sold"
    if {"archived_vine", "archived_sold"} & labels:
        return "archived"
    if str(listing.ebay_publish_status or "").upper() == "POSTED" or bool(listing.ebay_listing_id):
        return "published"
    if str(listing.processing_state or "").strip().lower() in {"needs_attention", "blocked"}:
        return "needs_attention"
    if reviewability.get("caption_like_title") or reviewability.get("bare_identifier_title"):
        return "needs_attention"
    if str(listing.status).lower() == "error" or str(listing.ebay_publish_status or "").upper() == "FAILED":
        return "failed"
    if not bool(listing.image_urls or []):
        return "drafts"
    if (listing.restricted_review_required or listing.needs_review) and not explicitly_approved:
        return "review"
    if listing.status == ListingStatus.ready:
        preflight_state = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data, dict) else {}
        by_marketplace = preflight_state.get("by_marketplace") if isinstance(preflight_state, dict) else {}
        target_markets = [str(value).strip().lower() for value in marketplace_data.get("targets") or [] if str(value).strip()]
        approved_target = any(
            isinstance(by_marketplace, dict)
            and str((by_marketplace.get(market) or {}).get("status") or "").strip().lower() in {"ready", "ready_with_warnings", "published"}
            for market in target_markets
        )
        return "ready" if explicitly_approved and approved_target else "drafts"
    return "drafts"


def _listing_marketplace_names(listing: Listing) -> set[str]:
    names: set[str] = set()
    if listing.ebay_publish_status or listing.ebay_listing_id:
        names.add("ebay")
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    for value in marketplace_data.get("targets") or []:
        name = str(value or "").strip().lower()
        if name:
            names.add(name)
    for row in getattr(listing, "marketplace_listings", None) or []:
        name = str(getattr(row, "marketplace", "") or getattr(row, "marketplace_name", "") or "").strip().lower()
        if name:
            names.add(name)
    return names


def _is_vine_source(listing: Listing) -> bool:
    source = str(listing.source_type or "").strip().lower()
    hint = ""
    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    for key in ("source", "ingest_source", "marketplace_source"):
        value = str(source_metadata.get(key) or "").strip().lower()
        if value:
            hint = value
            break
    return "vine" in source or "vine" in hint


def _matches_marketplace_filter(listing: Listing, marketplace: str | None) -> bool:
    normalized = str(marketplace or "").strip().lower()
    if not normalized or normalized == "all":
        return True
    return normalized in _listing_marketplace_names(listing)


def _matches_readiness_filter(listing: Listing, filter_value: str | None) -> bool:
    normalized = str(filter_value or "").strip().lower()
    if not normalized or normalized == "all":
        return True
    summary = summarize_listing_readiness(
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
    condition_data = listing.condition_data if isinstance(listing.condition_data, dict) else {}
    shipping_profile = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
    pricing = (listing.marketplace_data or {}).get("pricing_analysis") if isinstance(listing.marketplace_data, dict) else {}
    quality = listing.quality_summary if isinstance(listing.quality_summary, dict) else {}
    preflight_root = (listing.marketplace_data or {}).get("marketplace_preflight") if isinstance(listing.marketplace_data, dict) else {}
    by_marketplace = preflight_root.get("by_marketplace") if isinstance(preflight_root, dict) else {}
    ebay_preflight = by_marketplace.get("ebay") if isinstance(by_marketplace, dict) else None
    facebook_preflight = by_marketplace.get("facebook") if isinstance(by_marketplace, dict) else None

    def _preflight_ready(summary_row: dict | None) -> bool:
        return bool(summary_row and str(summary_row.get("status") or "").lower() in {"ready", "ready_with_warnings", "published"})

    def _preflight_blocked(summary_row: dict | None, tokens: tuple[str, ...]) -> bool:
        if not summary_row:
            return False
        return any(any(token in str(code) for token in tokens) for code in (summary_row.get("blocker_codes") or []))

    if normalized == "missing_photos":
        return bool(summary.get("images_missing") or summary.get("manual_photo_needed"))
    if normalized == "reference_only":
        return bool(summary.get("reference_image_count")) and not summary.get("actual_image_count")
    if normalized == "missing_weight":
        return not shipping_profile.get("package_weight")
    if normalized == "missing_dimensions":
        package_dimensions = shipping_profile.get("package_dimensions") if isinstance(shipping_profile.get("package_dimensions"), dict) else {}
        return not any(package_dimensions.get(key) for key in ("length", "width", "height"))
    if normalized == "missing_condition":
        return bool(condition_data.get("operator_review_required")) or not listing.condition
    if normalized == "missing_category":
        return not (listing.category_id or listing.category_suggestion)
    if normalized == "missing_price":
        return not (listing.listing_price or listing.suggested_price)
    if normalized == "weak_pricing":
        return float(pricing.get("price_confidence") or pricing.get("confidence") or 0) < 0.45
    if normalized == "stale_pricing":
        return bool(pricing.get("stale"))
    if normalized == "ready_for_ebay":
        return bool(quality.get("ready_for_ebay"))
    if normalized == "ready_for_facebook":
        return bool(quality.get("ready_for_facebook"))
    if normalized == "high_confidence_ready":
        return bool(quality.get("ready_for_publish_queue")) and float(pricing.get("price_confidence") or pricing.get("confidence") or 0) >= 0.7
    if normalized == "likely_low_value":
        recommended = float(pricing.get("recommended_price") or listing.listing_price or 0)
        return recommended > 0 and recommended <= 20
    if normalized == "oversize_low_margin":
        recommended = float(pricing.get("recommended_price") or listing.listing_price or 0)
        return bool((shipping_profile.get("oversize") or shipping_profile.get("local_pickup_recommended")) and recommended <= 40)
    if normalized == "ebay_ready":
        return _preflight_ready(ebay_preflight)
    if normalized == "ebay_blocked":
        return bool(ebay_preflight and (str(ebay_preflight.get("status") or "").lower() == "blocked" or (ebay_preflight.get("blocker_count") or 0) > 0))
    if normalized == "ebay_warning_only":
        return bool(ebay_preflight and (str(ebay_preflight.get("status") or "").lower() == "ready_with_warnings" or (not (ebay_preflight.get("blocker_count") or 0) and (ebay_preflight.get("warning_count") or 0) > 0)))
    if normalized == "ebay_missing_category":
        return bool(not ebay_preflight or any("category" in str(field).lower() for field in (ebay_preflight.get("missing_fields") or [])))
    if normalized == "ebay_missing_aspects":
        return bool(ebay_preflight and any(code == "EBAY_REQUIRED_ASPECT_MISSING" for code in (ebay_preflight.get("blocker_codes") or [])))
    if normalized == "ebay_missing_policies":
        return bool(ebay_preflight and any("POLICY" in str(code) for code in (ebay_preflight.get("blocker_codes") or [])))
    if normalized == "ebay_missing_shipping":
        return bool(ebay_preflight and _preflight_blocked(ebay_preflight, ("SHIPPING", "WEIGHT", "DIMENSIONS")))
    if normalized == "ebay_missing_photos":
        return bool(ebay_preflight and _preflight_blocked(ebay_preflight, ("PHOTOS", "IMAGE")))
    if normalized == "facebook_ready":
        return _preflight_ready(facebook_preflight)
    if normalized == "facebook_blocked":
        return bool(facebook_preflight and (str(facebook_preflight.get("status") or "").lower() == "blocked" or (facebook_preflight.get("blocker_count") or 0) > 0))
    if normalized == "facebook_warning_only":
        return bool(facebook_preflight and (str(facebook_preflight.get("status") or "").lower() == "ready_with_warnings" or (not (facebook_preflight.get("blocker_count") or 0) and (facebook_preflight.get("warning_count") or 0) > 0)))
    if normalized == "facebook_missing_photos":
        return bool(facebook_preflight and _preflight_blocked(facebook_preflight, ("PHOTOS", "IMAGE")))
    if normalized == "facebook_missing_price":
        return bool(facebook_preflight and _preflight_blocked(facebook_preflight, ("PRICE",)))
    if normalized == "facebook_missing_category":
        return bool(facebook_preflight and _preflight_blocked(facebook_preflight, ("CATEGORY",)))
    if normalized == "ready_except_shipping":
        return bool((_preflight_ready(ebay_preflight) or _preflight_ready(facebook_preflight)) and (_preflight_blocked(ebay_preflight, ("SHIPPING", "WEIGHT", "DIMENSIONS")) or _preflight_blocked(facebook_preflight, ("SHIPPING", "WEIGHT", "DIMENSIONS"))))
    if normalized == "ready_except_photos":
        return bool((_preflight_ready(ebay_preflight) or _preflight_ready(facebook_preflight)) and (_preflight_blocked(ebay_preflight, ("PHOTOS", "IMAGE")) or _preflight_blocked(facebook_preflight, ("PHOTOS", "IMAGE"))))
    if normalized == "ready_except_policies":
        return bool((_preflight_ready(ebay_preflight) or _preflight_ready(facebook_preflight)) and (_preflight_blocked(ebay_preflight, ("POLICY",)) or _preflight_blocked(facebook_preflight, ("POLICY",))))
    return True


def _listing_visibility_filter(normalized_queue: str | None):
    labels = func.coalesce(func.lower(cast(Listing.custom_labels, String)), "")
    sold = or_(Listing.sold_at.is_not(None), Listing.quantity <= 0)
    archived = or_(labels.contains("archived_vine"), labels.contains("archived_sold"))
    recovery = Listing.source_metadata["recovery"]
    merged_child = or_(
        recovery["merged_into_recovery_item_id"].as_string().is_not(None),
        recovery["merged_into_recovery_group_id"].as_string().is_not(None),
    )
    normalized = str(normalized_queue or "").strip().lower()
    if normalized == "sold":
        return and_(sold, not_(merged_child))
    if normalized == "archived":
        return and_(archived, not_(sold), not_(merged_child))
    return and_(not_(sold), not_(archived), not_(merged_child))

_DEFAULT_WORKFLOW_PREFERENCES = {
    "review_before_publish": True,
    "auto_publish_after_approval": False,
    "bulk_approval_enabled": True,
    "listing_preview_mode": "marketplace",
    "default_preview_marketplace": "ebay",
    "shipping_price_threshold": 10.0,
    "shipping_under_threshold_mode": "buyer_pays_shipping",
    "shipping_at_or_above_threshold_mode": "free_shipping",
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
        "shipping_price_threshold": float(stored.get("shipping_price_threshold", _DEFAULT_WORKFLOW_PREFERENCES["shipping_price_threshold"])),
        "shipping_under_threshold_mode": str(stored.get("shipping_under_threshold_mode") or _DEFAULT_WORKFLOW_PREFERENCES["shipping_under_threshold_mode"]),
        "shipping_at_or_above_threshold_mode": str(stored.get("shipping_at_or_above_threshold_mode") or _DEFAULT_WORKFLOW_PREFERENCES["shipping_at_or_above_threshold_mode"]),
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


def _serialize_listing_summary(listing: Listing) -> dict:
    sync_listing_review_state(listing=listing)
    base = ListingResponse.model_validate(listing).model_dump()
    base.pop("marketplace_statuses", None)
    base.pop("latest_publish_attempt", None)
    return base


def _serialize_public_storefront_listing(listing: Listing) -> dict:
    marketplace_rows = []
    for row in sorted(
        listing.marketplace_listings or [],
        key=lambda item: (
            item.updated_at.isoformat() if item.updated_at else "",
            item.id or 0,
        ),
        reverse=True,
    ):
        status = str(row.status.value if hasattr(row.status, "value") else row.status or "").strip().upper()
        if status not in {"PUBLISHED", "UPDATED"}:
            continue
        marketplace_rows.append(row)

    public_images = normalize_listing_images(
        listing_images=listing.listing_images,
        image_urls=listing.image_urls,
        source_url=(listing.source_metadata or {}).get("source_image_url") if isinstance(listing.source_metadata, dict) else None,
        source_page_url=(listing.source_metadata or {}).get("amazon_source_page_url") if isinstance(listing.source_metadata, dict) else None,
        source_platform=listing.source_type or "storefront",
        default_is_reference=False,
        approved=True,
    )
    public_images = [image for image in public_images if image.get("operator_state") != "rejected"]
    public_image_urls = [
        _to_public_image_url(image.get("storage_path"))
        for image in public_images
        if str(image.get("storage_path") or "").strip()
    ]
    primary_image = next((image for image in public_images if image.get("role") == "primary" and image.get("storage_path")), None)
    thumbnail_path = (
        primary_image.get("storage_path")
        if primary_image
        else next((image.get("storage_path") for image in public_images if image.get("storage_path")), None)
    )

    marketplace_names = sorted({
        str(row.marketplace.value if hasattr(row.marketplace, "value") else row.marketplace).strip().lower()
        for row in marketplace_rows
        if str(row.marketplace.value if hasattr(row.marketplace, "value") else row.marketplace).strip()
    })
    if listing.ebay_listing_id or str(listing.ebay_publish_status or "").upper() == EbayPublishStatus.POSTED.value:
        marketplace_names = sorted({*marketplace_names, "ebay"})
    marketplace_urls: dict[str, str] = {}
    for row in marketplace_rows:
        marketplace = str(row.marketplace.value if hasattr(row.marketplace, "value") else row.marketplace).strip().lower()
        if not marketplace or marketplace in marketplace_urls:
            continue
        raw_response = row.raw_response if isinstance(row.raw_response, dict) else {}
        url = str(raw_response.get("listing_url") or raw_response.get("url") or "").strip()
        if marketplace == "ebay" and not url and listing.ebay_listing_id:
            url = f"https://www.ebay.com/itm/{listing.ebay_listing_id}"
        if url:
            marketplace_urls[marketplace] = url

    if listing.ebay_listing_id and "ebay" not in marketplace_urls:
        marketplace_urls["ebay"] = f"https://www.ebay.com/itm/{listing.ebay_listing_id}"

    return {
        "id": listing.id,
        "title": listing.title or listing.suggested_title or f"Listing #{listing.id}",
        "description": listing.description or "",
        "price": float(listing.listing_price or listing.suggested_price or 0.0),
        "quantity": int(listing.quantity or 1),
        "condition": listing.condition,
        "category_id": listing.category_id,
        "category_suggestion": listing.category_suggestion,
        "thumbnail_url": _to_public_image_url(thumbnail_path) if thumbnail_path else "",
        "image_urls": public_image_urls or [_to_public_image_url(path) for path in (listing.image_urls or []) if str(path or "").strip()],
        "marketplaces": marketplace_names,
        "marketplace_urls": marketplace_urls,
        "updated_at": listing.updated_at.isoformat() if listing.updated_at else None,
        "created_at": listing.created_at.isoformat() if listing.created_at else None,
        "sku": getattr(listing, "sku", None),
        "listing_url": marketplace_urls.get("ebay") or next(iter(marketplace_urls.values()), ""),
    }


@router.get("/public/storefront/listings")
def get_public_storefront_listings(
    page: int | None = Query(default=1, ge=1),
    page_size: int | None = Query(default=25, ge=1, le=250),
    search: str | None = Query(default=None, max_length=200),
    sort_by: str | None = Query(default="updated", max_length=32),
    sort_dir: str | None = Query(default="desc", max_length=4),
    db: Session = Depends(get_db),
):
    filters = []
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(or_(Listing.title.ilike(pattern), Listing.description.ilike(pattern)))

    published_filters = or_(
        Listing.status == ListingStatus.PUBLISHED,
        Listing.ebay_publish_status == EbayPublishStatus.POSTED,
        Listing.ebay_listing_id.is_not(None),
        exists(
            select(1).where(
                and_(
                    MarketplaceListing.listing_id == Listing.id,
                    MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
                )
            )
        ),
    )
    visibility_filter = _listing_visibility_filter("published")
    statement = (
        select(Listing)
        .options(selectinload(Listing.marketplace_listings))
        .where(*filters, published_filters, visibility_filter)
    )
    sort_map = {
        "updated": Listing.updated_at,
        "created": Listing.created_at,
        "price": Listing.listing_price,
        "title": Listing.title,
        "source": Listing.source_type,
    }
    resolved_sort_by = str(sort_by or "updated").strip().lower()
    sort_column = sort_map.get(resolved_sort_by, Listing.updated_at)
    resolved_sort_dir = str(sort_dir or "desc").strip().lower()
    sort_expr = asc(sort_column) if resolved_sort_dir == "asc" else desc(sort_column)

    resolved_page_size = page_size or 25
    resolved_page = page or 1
    rows = db.execute(statement.order_by(sort_expr, desc(Listing.updated_at))).scalars().all()
    visible_rows = []
    for listing in rows:
        public_listing = _serialize_public_storefront_listing(listing)
        if public_listing.get("thumbnail_url") or public_listing.get("image_urls"):
            visible_rows.append(public_listing)
    total = len(visible_rows)
    start = (resolved_page - 1) * resolved_page_size
    end = start + resolved_page_size
    page_rows = visible_rows[start:end]
    return {
        "items": page_rows,
        "total": total,
        "page": resolved_page,
        "page_size": resolved_page_size,
        "total_pages": max(1, (total + resolved_page_size - 1) // resolved_page_size),
    }


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
    before = {field: getattr(listing, field, None) for field in ("title", "description", "listing_price", "suggested_price", "quantity", "condition", "category_id", "category_suggestion", "item_specifics", "image_urls", "marketplace_data", "platform_quantities", "custom_labels")}
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

    if blockers_by_market:
        blocker_lines: list[str] = []
        for market, blockers in blockers_by_market.items():
            fix_hint = next(
                (
                    str(blocker.get("fix_hint") or blocker.get("message") or "").strip()
                    for blocker in blockers
                    if str(blocker.get("fix_hint") or blocker.get("message") or "").strip()
                ),
                None,
            )
            blocker_lines.append(f"{market}: {fix_hint or (blockers[0].get('message') if blockers else 'preflight blocked')}")
        create_process_notification(
            db,
            user_id=current_user.id,
            notification_type="listing_approval_blocked",
            title=f"Listing #{listing.id} blocked by marketplace preflight",
            message="\n".join(blocker_lines[:5]),
            href=f"/listings/{listing.id}",
            metadata_json={
                "listing_id": listing.id,
                "approval_blockers": blockers_by_market,
                "targets": targets,
            },
        )

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
    return "archived_vine" in labels


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
    confirm_live_apply: bool = False
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


@router.post("/import/google-photos/watch/rebuild-bad-drafts")
def rebuild_bad_google_photos_drafts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = intake_slate_service.purge_and_regenerate_bad_google_photos_drafts(
            db,
            user=None if getattr(current_user, "is_admin", False) else current_user,
            rebuild_user=current_user,
        )
        return {
            "purged_count": int(result.get("purged_count") or 0),
            "purged_listing_ids": result.get("purged_listing_ids") or [],
            "preserved_count": int(result.get("preserved_count") or 0),
            "rebuild_result": result.get("rebuild_result") or {},
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
    marketplace: str | None = Query(default=None, max_length=32),
    readiness: str | None = Query(default=None, max_length=32),
    queue: str | None = Query(default=None, max_length=32),
    search: str | None = Query(default=None, max_length=200),
    sort_by: str | None = Query(default="updated", max_length=32),
    sort_dir: str | None = Query(default="desc", max_length=4),
    summary_only: bool = Query(default=False),
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
    if not summary_only:
        statement = statement.options(
            selectinload(Listing.marketplace_listings),
            selectinload(Listing.publish_attempts),
        )

    def _bucket(listing: Listing) -> str:
        """Resolve catalog queues before pagination so totals remain honest."""
        labels = {str(value).strip().lower() for value in (listing.custom_labels or [])}
        if listing.sold_at is not None or int(listing.quantity or 1) <= 0:
            return "sold"
        if {"archived_vine", "archived_sold"} & labels:
            return "archived"
        if str(listing.status).lower() == "error" or str(listing.ebay_publish_status or "").upper() == "FAILED":
            return "failed"
        if str(listing.ebay_publish_status or "").upper() == "POSTED" or bool(listing.ebay_listing_id):
            return "published"
        source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
        is_recovery = str(listing.source_type or "") == "media_inventory_recovery"
        explicitly_approved = bool(source_metadata.get("operator_approved_at"))
        if is_recovery and listing.status == ListingStatus.draft:
            return "review"
        if listing.restricted_review_required or listing.needs_review:
            return "review"
        if listing.status == ListingStatus.draft:
            return "drafts"
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
    # The frontend's public tab key is `attention`; normalize it to the
    # canonical lifecycle bucket used by the API and database.
    if normalized_queue == "attention":
        normalized_queue = "needs_attention"
    queue_filters = []
    if normalized_queue and normalized_queue != "all":
        if normalized_queue == "sold":
            queue_filters.append(or_(Listing.sold_at.is_not(None), Listing.quantity <= 0))
        elif normalized_queue == "archived":
            queue_filters.append(and_(
                or_(
                    func.coalesce(func.lower(cast(Listing.custom_labels, String)), "").contains("archived_vine"),
                    func.coalesce(func.lower(cast(Listing.custom_labels, String)), "").contains("archived_sold"),
                ),
                not_(or_(Listing.sold_at.is_not(None), Listing.quantity <= 0)),
            ))
        elif normalized_queue == "drafts":
            queue_filters.append(and_(Listing.status == ListingStatus.draft, Listing.needs_review.is_(False), Listing.restricted_review_required.is_(False)))
        elif normalized_queue == "failed":
            queue_filters.append(or_(Listing.status == ListingStatus.FAILED, Listing.ebay_publish_status == "FAILED"))
        elif normalized_queue == "needs_attention":
            # Attention is strictly an unpublished repair queue.  A listing
            # that has already gone live belongs in Published even if an old
            # processing blocker was left behind on the row.
            queue_filters.append(and_(
                or_(Listing.processing_state == "needs_attention", Listing.processing_state == "blocked"),
                Listing.status != ListingStatus.PUBLISHED,
                or_(Listing.ebay_publish_status.is_(None), Listing.ebay_publish_status != "POSTED"),
                Listing.ebay_listing_id.is_(None),
            ))
        elif normalized_queue == "published":
            queue_filters.append(or_(
                Listing.status == ListingStatus.PUBLISHED,
                Listing.ebay_publish_status == "POSTED",
                Listing.ebay_listing_id.is_not(None),
            ))
        elif normalized_queue == "ready":
            queue_filters.append(and_(Listing.status == ListingStatus.ready, Listing.source_metadata["operator_approved_at"].as_string().is_not(None)))
        elif normalized_queue == "review":
            # Needs Review is an approval queue, not a holding area for
            # unpublished/photo-less or already-published records.
            queue_filters.append(
                and_(
                    or_(Listing.needs_review.is_(True), Listing.restricted_review_required.is_(True)),
                    Listing.sold_at.is_(None),
                    Listing.status != ListingStatus.PUBLISHED,
                    Listing.ebay_listing_id.is_(None),
                    or_(Listing.ebay_publish_status.is_(None), Listing.ebay_publish_status != "POSTED"),
                    Listing.image_urls.is_not(None),
                )
            )

    if queue_filters:
        statement = statement.where(*queue_filters)
    visibility_filter = _listing_visibility_filter(normalized_queue)
    statement = statement.where(visibility_filter)
    all_filters = [*filters, *queue_filters, visibility_filter]
    normalized_marketplace = str(marketplace or "").strip().lower()
    normalized_readiness = str(readiness or "").strip().lower()
    needs_python_filtering = (
        (normalized_marketplace and normalized_marketplace != "all")
        or (normalized_readiness and normalized_readiness != "all")
        or normalized_queue in {"drafts", "ready", "review", "needs_attention", "published", "failed"}
    )

    sort_map = {
        "updated": Listing.updated_at,
        "created": Listing.created_at,
        "price": Listing.listing_price,
        "source": Listing.source_type,
        "status": Listing.status,
        "title": Listing.title,
    }
    resolved_sort_by = str(sort_by or "updated").strip().lower()
    sort_column = sort_map.get(resolved_sort_by, Listing.updated_at)
    resolved_sort_dir = str(sort_dir or "desc").strip().lower()
    sort_expr = asc(sort_column) if resolved_sort_dir == "asc" else desc(sort_column)

    if page is None and page_size is None and not needs_python_filtering:
        rows = db.execute(statement.order_by(desc(Listing.updated_at))).scalars().all()
        serializer = _serialize_listing_summary if summary_only else _serialize_listing_response
        return [serializer(listing) for listing in rows]

    resolved_page = page or 1
    resolved_page_size = page_size or 25
    if needs_python_filtering:
        rows = db.execute(
            statement.order_by(sort_expr, desc(Listing.updated_at))
        ).scalars().all()
        rows = [
            listing
            for listing in rows
            if _matches_marketplace_filter(listing, normalized_marketplace)
            and _matches_readiness_filter(listing, normalized_readiness)
            and (
                normalized_queue in {"", "all"}
                or _listing_bucket(listing) == normalized_queue
            )
        ]
        total = len(rows)
        bucket_counts: dict[str, int] = {}
        for listing in rows:
            bucket = _listing_bucket(listing)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        bucket_counts["all"] = total
        bucket_counts["attention"] = bucket_counts.get("needs_attention", 0)
        bucket_counts["vine"] = sum(1 for listing in rows if _is_vine_source(listing))
        start = (resolved_page - 1) * resolved_page_size
        rows = rows[start : start + resolved_page_size]
    else:
        total = int(db.execute(select(func.count()).select_from(Listing).where(*all_filters)).scalar_one())
        bucket_counts_rows = db.execute(
            select(_listing_bucket_expression().label("bucket"), func.count())
            .select_from(Listing)
            .where(*all_filters)
            .group_by("bucket")
        ).all()
        bucket_counts = {str(bucket): int(count) for bucket, count in bucket_counts_rows}
        bucket_counts["all"] = total
        bucket_counts["attention"] = bucket_counts.get("needs_attention", 0)
        bucket_counts["vine"] = int(
            db.execute(
                select(func.count())
                .select_from(Listing)
                .where(*all_filters, or_(Listing.source_type == "amazon_vine", Listing.source_type.ilike("%vine%")))
            ).scalar_one()
        )
        rows = db.execute(
            statement.order_by(sort_expr, desc(Listing.updated_at))
            .offset((resolved_page - 1) * resolved_page_size)
            .limit(resolved_page_size)
        ).scalars().all()
    serializer = _serialize_listing_summary if summary_only else _serialize_listing_response
    return {
        "items": [serializer(listing) for listing in rows],
        "total": total,
        "page": resolved_page,
        "page_size": resolved_page_size,
        "total_pages": max(1, (total + resolved_page_size - 1) // resolved_page_size),
        "bucket_counts": bucket_counts,
    }


@router.post("/listings/vine/backfill-images")
def backfill_vine_listing_images(
    include_archived: bool = False,
    force_refresh: bool = True,
    strict_match: bool = True,
    use_bridge_session: bool = True,
    only_missing_images: bool = False,
    since_order_date: date | None = None,
    limit: int | None = None,
    listing_ids: list[int] | None = Query(default=None),
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

    query = select(Listing).where(
        Listing.user_id == current_user.id,
        Listing.source_type == "amazon_vine",
    )
    if listing_ids:
        query = query.where(Listing.id.in_(listing_ids))
    listings = db.execute(query).scalars().all()
    if since_order_date is not None:
        listings = [
            listing
            for listing in listings
            if any(
                item.order_date and item.order_date >= since_order_date
                for item in db.execute(
                    select(VineImportItem).where(
                        (VineImportItem.listing_id == listing.id) | (VineImportItem.inventory_item_id == listing.id)
                    )
                ).scalars().all()
            )
        ]
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
        "listing_ids": listing_ids or [],
    }


@router.post("/listings/vine/refresh-metadata")
def refresh_vine_listing_metadata(
    include_archived: bool = False,
    since_order_date: date | None = None,
    limit: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listings = db.execute(
        select(Listing).where(
            Listing.user_id == current_user.id,
            Listing.source_type == "amazon_vine",
        )
    ).scalars().all()
    if not include_archived:
        listings = [listing for listing in listings if not _is_archived_vine_listing(listing)]
    listing_ids = [listing.id for listing in listings]
    result = service.refresh_vine_listing_metadata(
        db,
        user_id=current_user.id,
        listing_ids=listing_ids or None,
        since_order_date=since_order_date,
        limit=limit,
    )
    return {
        **result,
        "listing_ids": listing_ids[: max(0, limit)] if limit is not None else listing_ids,
        "total_vine_listings": len(listings),
        "include_archived": include_archived,
        "since_order_date": since_order_date.isoformat() if since_order_date else None,
    }


@router.post("/listings/vine/repair-all-images")
def repair_all_vine_listing_images(
    include_archived: bool = False,
    force_refresh: bool = True,
    use_bridge_session: bool = True,
    only_missing_images: bool = True,
    limit: int | None = None,
    chunk_size: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = vine_import_service.repair_all_vine_listing_images(
        db,
        user_id=current_user.id,
        include_archived=include_archived,
        force_refresh=force_refresh,
        use_bridge_session=use_bridge_session,
        only_missing_images=only_missing_images,
        limit=limit,
        chunk_size=chunk_size,
    )
    return {
        **result,
        "include_archived": include_archived,
        "force_refresh": force_refresh,
        "use_bridge_session": use_bridge_session,
        "only_missing_images": only_missing_images,
        "limit": limit,
        "chunk_size": chunk_size,
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
    metadata = dict(listing.source_metadata or {})
    metadata["correction_job_id"] = correction.id
    listing.source_metadata = metadata
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
    before = {field: getattr(listing, field, None) for field in ("title", "description", "listing_price", "suggested_price", "quantity", "condition", "category_id", "category_suggestion", "item_specifics", "image_urls", "marketplace_data", "platform_quantities", "custom_labels")}
    direct_updates = payload.model_dump(
        exclude_none=True,
        exclude={"quantity", "platform_quantities", "custom_labels", "marketplace_data"},
    )
    if "status" in direct_updates:
        direct_updates["status"] = ListingStatus(direct_updates["status"])
    for key, value in direct_updates.items():
        setattr(listing, key, value)
    manual_fields = [field for field in ("title", "description", "category_suggestion", "item_specifics", "condition", "estimated_value", "suggested_price", "listing_price", "buy_it_now_price") if field in direct_updates]
    if manual_fields:
        source_metadata = mark_manual_field_provenance(dict(listing.source_metadata or {}), manual_fields)
        listing.source_metadata = source_metadata
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
    changed_fields = [field for field in before if before[field] != getattr(listing, field, None)]
    if changed_fields or payload.quantity is not None or payload.platform_quantities is not None:
        md = dict(listing.marketplace_data or {})
        revision = int(md.get("posterpro_revision") or 0) + 1
        md["posterpro_revision"] = revision
        active_exists = db.scalar(select(exists().where(and_(MarketplaceListing.listing_id == listing.id, MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED])))))
        md["sync_state"] = "local_changes_not_published" if active_exists else md.get("sync_state", "local")
        listing.marketplace_data = md
        db.add(ListingRevision(listing_id=listing.id, user_id=current_user.id, revision=revision, operation="save", changed_fields={f: {"before": before[f], "after": getattr(listing, f, None)} for f in changed_fields}, marketplaces_targeted=[], sync_state=md.get("sync_state", "local"), status="recorded"))
    db.commit()
    db.refresh(listing)
    return _serialize_listing_response(listing)


@router.post("/listings/{listing_id}/save-publish-changes")
async def save_publish_listing_changes(
    listing_id: int,
    payload: ListingUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persist a manual edit and queue marketplace updates for this revision."""
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    before = {field: getattr(listing, field, None) for field in ("title", "description", "listing_price", "suggested_price", "quantity", "condition", "category_id", "category_suggestion", "item_specifics", "image_urls", "marketplace_data", "platform_quantities", "custom_labels")}
    direct_updates = payload.model_dump(exclude_none=True, exclude={"quantity", "platform_quantities", "custom_labels", "marketplace_data"})
    if "status" in direct_updates:
        direct_updates["status"] = ListingStatus(direct_updates["status"])
    for key, value in direct_updates.items():
        setattr(listing, key, value)
    if payload.marketplace_data is not None:
        listing.marketplace_data = normalize_marketplace_data(payload.marketplace_data)
    try:
        inventory_service.update_listing_inventory(listing, quantity=payload.quantity, platform_quantities=payload.platform_quantities, labels_to_add=payload.custom_labels)
    except InventorySafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    manual_fields = [field for field in before if before[field] != getattr(listing, field, None)]
    if manual_fields:
        listing.source_metadata = mark_manual_field_provenance(dict(listing.source_metadata or {}), manual_fields)
    active_rows = db.execute(select(MarketplaceListing.marketplace, MarketplaceListing.marketplace_listing_id).where(MarketplaceListing.listing_id == listing.id, MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]))).all()
    active = {str(value.value if hasattr(value, "value") else value).lower(): identity for value, identity in active_rows if identity}
    targets = list(active.keys())
    md_before = dict(before.get("marketplace_data") or {})
    revision = int(md_before.get("posterpro_revision") or 0) + 1
    changed_set = {field: {"before": before[field], "after": getattr(listing, field, None)} for field in manual_fields}
    # Idempotent rapid double-submit: reuse the most recent identical manual
    # save/publish revision instead of creating duplicate jobs.
    recent_revision = db.execute(select(ListingRevision).where(ListingRevision.listing_id == listing.id, ListingRevision.user_id == current_user.id, ListingRevision.operation == "save_publish").order_by(ListingRevision.id.desc()).limit(1)).scalar_one_or_none()
    if recent_revision and recent_revision.changed_fields == changed_set:
        existing_jobs = db.execute(select(MarketplaceCrosspostJob.id, MarketplaceCrosspostJob.target_marketplaces, MarketplaceCrosspostJob.task_id).where(MarketplaceCrosspostJob.listing_id == listing.id, MarketplaceCrosspostJob.execution_plan["revision_id"].as_integer() == recent_revision.id)).all()
        return {"listing": _serialize_listing_response(listing), "changed_fields": manual_fields, "jobs": [{"job_id": j.id, "marketplace": (j.target_marketplaces or [None])[0], "task_id": j.task_id} for j in existing_jobs], "sync_state": (listing.marketplace_data or {}).get("sync_state", "synced"), "deduplicated": True}
    revision_row = ListingRevision(listing_id=listing.id, user_id=current_user.id, revision=revision, operation="save_publish", changed_fields=changed_set, marketplaces_targeted=targets, marketplace_results={}, sync_state="update_queued" if targets else "synced", status="queued")
    db.add(revision_row); db.flush()
    db.commit(); db.refresh(listing)
    jobs = []
    for market in targets:
        job = MarketplaceCrosspostJob(user_id=current_user.id, listing_id=listing.id, source_marketplace="posterpro", target_marketplaces=[market], requested_mode="manual_update", status="queued", priority=0, requested_by=current_user.id, execution_plan={"operation": "update", "revision": revision, "revision_id": revision_row.id, "external_listing_id": active.get(market), "changed_fields": changed_set})
        db.add(job); db.flush()
        from app.api.marketplace_jobs import _enqueue_priority
        task = _enqueue_priority(process_marketplace_crosspost_job_task, job.id, job.priority); job.task_id = task.id; jobs.append({"job_id": job.id, "marketplace": market, "task_id": task.id})
    md = dict(listing.marketplace_data or {}); md["posterpro_revision"] = revision; md["sync_state"] = "update_queued" if jobs else "synced"; listing.marketplace_data = md
    db.commit(); db.refresh(listing)
    return {"listing": _serialize_listing_response(listing), "changed_fields": manual_fields, "jobs": jobs, "sync_state": md["sync_state"]}


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
        confirm_live_apply=payload.confirm_live_apply,
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


@router.get("/listings/{listing_id}/revisions")
def get_listing_revisions(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    rows = db.execute(select(ListingRevision).where(ListingRevision.listing_id == listing_id).order_by(desc(ListingRevision.revision), desc(ListingRevision.created_at))).scalars().all()
    return [{"id": row.id, "revision": row.revision, "operation": row.operation, "changed_fields": row.changed_fields or {}, "marketplaces_targeted": row.marketplaces_targeted or [], "marketplace_results": row.marketplace_results or {}, "sync_state": row.sync_state, "status": row.status, "reason": row.reason, "requested_by": row.user_id, "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows]


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
    priority = max(0, int(payload.priority or 0))
    history.append({"fields": list(dict.fromkeys(payload.fields or [])), "note": (payload.note or "").strip() or None, "priority": priority, "source": "MANUAL_CORRECTION", "requested_by": current_user.id, "requested_at": datetime.utcnow().isoformat(), "status": "QUEUED"})
    metadata["operator_revision_requests"] = history[-20:]
    metadata["rework_state"] = "queued_for_ai_revision"
    metadata["correction_priority"] = priority
    metadata["correction_requested_at"] = datetime.utcnow().isoformat()
    metadata["correction_status"] = "QUEUED - DRAFTING PAUSED" if getattr(settings, "drafting_paused", False) else "QUEUED"
    # Durable queue record; worker claim ordering is priority ASC, with newest priority-0 first.
    pending = db.execute(select(ListingCorrectionJob).where(ListingCorrectionJob.listing_id == listing.id, ListingCorrectionJob.status == "queued")).scalars().all()
    for old in pending:
        old_fields = set(old.fields or []); new_fields = set(payload.fields or [])
        if old_fields & new_fields:
            old.status = "superseded"
    correction = ListingCorrectionJob(user_id=current_user.id, listing_id=listing.id, requested_by=current_user.id, priority=priority, fields=list(dict.fromkeys(payload.fields or [])), operator_note=(payload.note or "").strip() or None, before_snapshot={"title": listing.title, "description": listing.description, "category_id": listing.category_id, "category_suggestion": listing.category_suggestion, "item_specifics": listing.item_specifics, "listing_price": listing.listing_price, "condition": listing.condition, "image_urls": listing.image_urls})
    db.add(correction)
    listing.source_metadata = metadata
    listing.status = "draft"
    listing.needs_review = False
    db.add(listing)
    db.commit()
    process_listing_correction_jobs_task.delay(limit=1)
    db.refresh(listing)
    metadata = dict(listing.source_metadata or {})
    metadata["correction_job_id"] = correction.id
    listing.source_metadata = metadata
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
