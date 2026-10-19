from datetime import date, datetime, timedelta
import io
import json
import zipfile
import uuid

import httpx

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import asc, and_, case, delete, desc, exists, func, not_, or_, select, update, cast, String, Numeric
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
    UnpublishAndArchiveRequest,
    ListingResponse,
    ListingTemplateApplyRequest,
    ListingTemplateCreateRequest,
    ListingTemplateResponse,
    ListingUpdateRequest,
    PhotoEditRequest,
    PhotoEditResponse,
    StorageUnitBatchResponse,
)
from app.core.auth import ensure_user_owns_resource, get_current_user, is_effective_admin, resolve_user_scope
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
    MarketplaceAccount,
    MarketplacePublishAttempt,
    MarketplaceMetadataCache,
    MarketplacePublishBatch,
    AIDailyBudget,
    AIRequestReservation,
    AIProviderCircuit,
    ProductMediaCache,
    Sale,
    StorageUnitBatch,
    User,
    VineImportItem,
)
from app.services.ebay import EbayService
from app.services.ebay_service import revise_ebay_listing, get_category_tree
from app.services.category_rules import is_source_noise_category, resolve_taxonomy_leaf
from app.services.embedding import fake_clip_embedding
from app.services.google_photos import GooglePhotosService
from app.services.image_pipeline import ImagePipelineService
from app.services.inventory_service import InventorySafetyError, InventoryService
from app.services.intake_slate import IntakeSlateService
from app.services.listing_ai import ListingAIService
from app.services.listing_provenance import is_human_owned_field, mark_manual_field_provenance
from app.services.listing_review import (
    derive_condition_data,
    derive_shipping_profile,
    normalize_listing_images,
    summarize_listing_readiness,
    sync_listing_review_state,
)
from app.services.shipping_profitability_policy import normalize_shipping_policy
from app.services.listing_specificity import GENERIC_CAPTION_TITLES, classify_listing_reviewability
from app.services.ai_guard import utc_period
from app.services.canonical_readiness import canonical_listing_readiness, resolve_authoritative_listing_price
from app.services.marketplace_mutations import SUPPORTED_MARKETS, apply_marketplace_operation
from app.services.media_lifecycle import purge_listing_media
from app.services.listing_workspace import normalize_marketplace_data
from app.services.marketplace_orchestrator import enqueue_crosspost_job, queue_publish, _json_safe
from app.services.marketplace_field_mapper import build_marketplace_payload
from app.services.marketplace_preflight import MarketplacePreflightService, compute_payload_hash
from app.services.operator_command_service import OperatorCommandService, get_operator_tool_registry
from app.services.profit_service import ProfitService
from app.services.ebay_fee_service import calculate_expected_ebay_fee
from app.services.process_notifications import create_process_notification
from app.services.storage import LocalStorage
from app.services.pricing_service import PricingService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.pricing_research_service import compute_listing_quality_summary
from app.services.active_listing_snapshot import cached_ebay_active_ids, last_ebay_active_ids
from app.services.photo_editor import PhotoEditorService
from app.services.listing_templates_service import listing_template_service
from app.services.amazon_media import AmazonProductMediaProvider
from app.services.amazon_product_discovery import AmazonProductDiscoveryService
from app.services.automation_bridge import AutomationBridgeError, submit_bridge_job, wait_for_bridge_job
from app.services.vine_import_service import VineImportService
from app.models.enums import EbayPublishStatus, ListingStatus, MARKETPLACE_DESTINATION_VALUES, MarketplaceListingStatus, MarketplaceName
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


def _queue_agent_marketplace_updates(
    db: Session,
    *,
    user: User,
    changes: list[dict],
    operation_label: str = "agent_mutation",
) -> list[dict]:
    """Queue exact-identity updates for agent mutations.

    The agent and the listing editor must share the same remote-update contract:
    a local destination override is only a desired state until a durable update
    job has attempted the exact known marketplace identity.  Canonical-only or
    unpublished destinations remain local-only and are reported as such.
    """
    grouped: dict[tuple[int, str], dict[str, dict]] = {}
    for change in changes or []:
        try:
            listing_id = int(change.get("listing_id"))
        except (TypeError, ValueError):
            continue
        market = str(change.get("marketplace") or "").strip().lower()
        if market not in SUPPORTED_MARKETS:
            continue
        grouped.setdefault((listing_id, market), {})[str(change.get("field") or "field")] = {
            "before": change.get("before"),
            "after": change.get("after"),
        }

    results: list[dict] = []
    from app.api.marketplace_jobs import _enqueue_priority

    for (listing_id, market), changed_fields in grouped.items():
        active = db.execute(
            select(MarketplaceListing).where(
                MarketplaceListing.listing_id == listing_id,
                MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
                MarketplaceListing.marketplace_listing_id.is_not(None),
                func.lower(cast(MarketplaceListing.marketplace, String)) == market,
            ).order_by(MarketplaceListing.id.desc())
        ).scalars().first()
        if not active or not str(active.marketplace_listing_id or "").strip():
            results.append({"listing_id": listing_id, "marketplace": market, "status": "saved_locally", "message": "No active remote listing identity was found."})
            continue
        pending = db.execute(
            select(MarketplaceCrosspostJob).where(
                MarketplaceCrosspostJob.user_id == user.id,
                MarketplaceCrosspostJob.listing_id == listing_id,
                MarketplaceCrosspostJob.status.in_(["queued", "running"]),
            ).order_by(MarketplaceCrosspostJob.id.desc())
        ).scalars().all()
        existing = next(
            (
                job for job in pending
                if market in [str(value).strip().lower() for value in (job.target_marketplaces or [])]
                and str((job.execution_plan or {}).get("operation") or "").lower() == "update"
            ),
            None,
        )
        if existing:
            results.append({"listing_id": listing_id, "marketplace": market, "status": "already_queued", "job_id": existing.id, "external_listing_id": active.marketplace_listing_id})
            continue
        revision = ListingRevision(
            listing_id=listing_id,
            user_id=user.id,
            revision=int((db.get(Listing, listing_id).marketplace_data or {}).get("posterpro_revision") or 0) + 1,
            operation=operation_label,
            changed_fields=changed_fields,
            marketplaces_targeted=[market],
            marketplace_results={},
            sync_state="update_queued",
            status="queued",
        )
        db.add(revision)
        db.flush()
        job = MarketplaceCrosspostJob(
            user_id=user.id,
            listing_id=listing_id,
            source_marketplace="posterpro",
            target_marketplaces=[market],
            requested_mode=operation_label,
            status="queued",
            priority=0,
            requested_by=user.id,
                execution_plan={
                "operation": "update",
                "revision_id": revision.id,
                "external_listing_id": str(active.marketplace_listing_id),
                "changed_fields": changed_fields,
            },
        )
        db.add(job)
        db.flush()
        try:
            task = _enqueue_priority(process_marketplace_crosspost_job_task, job.id, job.priority)
            job.task_id = task.id
        except Exception as exc:  # queue failure remains visible and retryable
            results.append({"listing_id": listing_id, "marketplace": market, "status": "queued", "job_id": job.id, "dispatch_error": str(exc)})
        else:
            results.append({"listing_id": listing_id, "marketplace": market, "status": "queued", "job_id": job.id, "task_id": job.task_id, "external_listing_id": active.marketplace_listing_id})
    return results


def _is_merged_recovery_child(listing: Listing) -> bool:
    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    return bool(recovery.get("merged_into_recovery_item_id") or recovery.get("merged_into_recovery_group_id"))


def _listing_bucket_expression():
    custom_labels_text = func.coalesce(func.lower(cast(Listing.custom_labels, String)), "")
    archive_state = func.lower(func.coalesce(Listing.marketplace_data["archive_state"]["status"].as_string(), ""))
    incomplete_archive_state = archive_state.in_(["remote_state_unknown", "partially_unpublished", "unpublishing", "archive_failed"])
    processing_state_text = func.lower(func.coalesce(Listing.processing_state, ""))
    blocking_text = func.lower(func.coalesce(Listing.processing_blocking_reason, ""))
    generic_caption = func.lower(func.trim(cast(Listing.title, String))).in_(
        sorted(GENERIC_CAPTION_TITLES)
    )
    confirmed_ebay_identity = and_(
        Listing.ebay_listing_id.is_not(None),
        func.trim(Listing.ebay_listing_id) != "",
        or_(Listing.status == ListingStatus.PUBLISHED, Listing.ebay_publish_status == "POSTED"),
    )
    confirmed_marketplace_projection = exists(select(1).where(and_(
        MarketplaceListing.listing_id == Listing.id,
        MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
        MarketplaceListing.marketplace_listing_id.is_not(None),
        func.trim(MarketplaceListing.marketplace_listing_id) != "",
    )))
    return case(
        (or_(Listing.sold_at.is_not(None), Listing.quantity <= 0), "sold"),
        (and_(or_(custom_labels_text.contains("archived_vine"), custom_labels_text.contains("archived_ebay")), not_(incomplete_archive_state)), "archived"),
        (or_(confirmed_ebay_identity, confirmed_marketplace_projection), "published"),
        # An existing external listing is authoritative for queue visibility;
        # a later failed revise must not make an already-live item disappear
        # from Published. The failed job remains visible in Jobs/details.
        (or_(processing_state_text.in_(["publisher_path_unsupported", "publisher_unsupported"]), blocking_text.contains("publisher_path_unsupported")), "publisher_unsupported"),
        (or_(processing_state_text.in_(["remote_state_unknown", "reconciliation_required"]), blocking_text.contains("remote_state_unknown"), blocking_text.contains("reconciliation_required")), "reconciliation"),
        (or_(processing_state_text.in_(["policy_blocked", "compliance_blocked"]), blocking_text.contains("policy review required"), blocking_text.contains("hazmat"), blocking_text.contains("policy_or_compliance")), "policy_blocked"),
        (processing_state_text.in_(["retry", "provider_retry", "transient_provider_failure"]), "provider_retry"),
        (or_(Listing.status == ListingStatus.FAILED, Listing.ebay_publish_status == "FAILED"), "failed"),
        # A stale blocker string can survive after a repair has queued the
        # row for machine work.  Do not count that denormalized text alone as
        # Needs Attention; the current lifecycle state is the discriminator.
        (and_(Listing.processing_state.in_(["needs_attention", "blocked"]), Listing.needs_review.is_(False)), "needs_attention"),
        (and_(Listing.processing_state.in_(["processing", "pending", "queued", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"]), Listing.needs_review.is_(False), Listing.restricted_review_required.is_(False)), "processing"),
        (generic_caption, "needs_attention"),
        (or_(Listing.restricted_review_required.is_(True), Listing.needs_review.is_(True)), "review"),
        (and_(
            Listing.status == ListingStatus.ready,
            Listing.source_metadata["operator_approved_at"].as_string().is_not(None),
        ), "ready"),
        else_="drafts",
    )


def _vine_image_enrichment_warning(entry: dict | None, listing: Listing) -> bool:
    """Cached image-enrichment warning is non-blocking for reviewable Vine drafts."""
    if str(listing.source_type or '').strip().lower() != 'amazon_vine' or not isinstance(entry, dict):
        return False
    text = ' '.join(str(entry.get(key) or '') for key in ('code', 'message', 'reason')).lower()
    return 'no usable amazon/product image' in text


def _compute_listing_bucket(listing: Listing, remote_ebay_active_ids: set[str] | None = None) -> str:
    labels = {str(value).strip().lower() for value in (listing.custom_labels or [])}
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    preflight = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else {}
    by_marketplace = preflight.get("by_marketplace") if isinstance(preflight.get("by_marketplace"), dict) else {}
    configured_targets = {
        str(value).strip().lower()
        for value in (marketplace_data.get("targets") or [])
        if str(value).strip()
    }
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
    # A fresh/last-verified eBay active snapshot is authoritative. An old
    # local archive label must not hide a listing that is demonstrably still
    # live on eBay from the Published queue/count.
    if remote_ebay_active_ids is not None and str(listing.ebay_listing_id or "").strip() in remote_ebay_active_ids:
        return "published"
    if {"archived_vine", "archived_sold"} & labels:
        return "archived"
    has_live_projection = any(
        (remote_ebay_active_ids is None or str(getattr(row, "marketplace", "")).lower() not in {"ebay", "marketplacename.ebay"})
        and (
            getattr(row, "status", None) in {MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED}
            or str(getattr(row, "status", "")).upper() in {"PUBLISHED", "UPDATED"}
        )
        and bool(str(getattr(row, "marketplace_listing_id", "") or "").strip())
        for row in (getattr(listing, "marketplace_listings", None) or [])
    )
    normalized_status = str(getattr(listing.status, "value", listing.status) or "").strip().lower()
    has_ebay_identity = bool(str(listing.ebay_listing_id or "").strip())
    ebay_status = str(getattr(listing.ebay_publish_status, "value", listing.ebay_publish_status) or "").strip().upper()
    has_confirmed_ebay_identity = (
        str(listing.ebay_listing_id or "").strip() in remote_ebay_active_ids
        if remote_ebay_active_ids is not None
        else has_ebay_identity and (normalized_status == "published" or ebay_status == "POSTED")
    )
    if has_confirmed_ebay_identity or has_live_projection:
        return "published"
    # Needs Attention is reserved for facts that genuinely require an
    # operator decision.  Capability, reconciliation, policy and scheduled
    # provider work each have a distinct queue so they cannot be mistaken for
    # a product-data correction request.
    processing_state = str(getattr(listing, "processing_state", "") or "").strip().lower()
    blocking_text = " ".join(str(value or "") for value in (
        getattr(listing, "processing_blocking_reason", None),
        getattr(listing, "processing_last_error", None),
    )).lower()
    if processing_state in {"publisher_path_unsupported", "publisher_unsupported"} or "publisher_path_unsupported" in blocking_text:
        return "publisher_unsupported"
    if processing_state in {"remote_state_unknown", "reconciliation_required"} or any(token in blocking_text for token in ("remote_state_unknown", "reconciliation_required", "reconcile remote")):
        return "reconciliation"
    preflight_text = " ".join(
        str(value or "")
        for row in (by_marketplace.values() if isinstance(by_marketplace, dict) else [])
        if isinstance(row, dict)
        for value in (row.get("blockers") or [])
        for value in ((value.get("code"), value.get("message")) if isinstance(value, dict) else (value,))
    ).lower()
    policy_text = f"{blocking_text} {preflight_text}"
    if processing_state in {"policy_blocked", "compliance_blocked"} or any(token in policy_text for token in ("policy review required", "hazmat", "compliance block", "policy_or_compliance")):
        return "policy_blocked"
    if processing_state in {"retry", "provider_retry", "transient_provider_failure"}:
        return "provider_retry"
    if listing.status == ListingStatus.FAILED or normalized_status in {"failed", "error"} or str(listing.ebay_publish_status or "").upper() == "FAILED":
        return "failed"
    # The readiness service is the canonical queue contract.  Keep the
    # legacy fallbacks below only for records too old to have enough fields;
    # otherwise queue filtering and serialized detail must make the same
    # decision for Needs Review vs Needs Attention.
    canonical = canonical_listing_readiness(listing)
    canonical_queue = str(canonical.get("queue") or "").upper()
    # Legacy non-Vine recovery drafts may not carry a marketplace category
    # record, but a current successful destination preflight is still the
    # authoritative evidence that they are ready for operator review.  Keep
    # this compatibility path scoped away from Vine, whose stricter category
    # and publishability contract is enforced above and in canonical readiness.
    if (
        str(listing.source_type or "").strip().lower() != "amazon_vine"
        and listing.needs_review
        and resolve_authoritative_listing_price(listing) is not None
        and reviewability.get("reviewability_class") == "pass"
        and not (canonical.get("blocking_reasons") or [])
        and not any(
            isinstance(row, dict) and row.get("blockers")
            for row in by_marketplace.values()
        )
        and any(
            isinstance(row, dict)
            and str(row.get("status") or "").lower() in {"ready", "ready_with_warnings", "published", "needs_review"}
            for row in by_marketplace.values()
        )
    ):
        return "review"
    # Legacy/imported rows may carry the durable review flag while their
    # processing metadata is absent.  Preserve that explicit operator queue
    # only when canonical readiness has no blockers; otherwise the canonical
    # blocker remains authoritative and the row stays out of Needs Review.
    if (
        (listing.needs_review or listing.restricted_review_required)
        and canonical.get("review_required")
        and (canonical.get("publishable") or canonical.get("destination_publishable"))
        and not (canonical.get("blocking_reasons") or [])
    ):
        return "review"
    if canonical_queue == "NEEDS_ATTENTION":
        return "needs_attention"
    if canonical_queue == "PROCESSING":
        return "processing"
    if canonical_queue == "NEEDS_REVIEW":
        return "review"
    if canonical_queue == "DRAFTS":
        return "drafts"
    if canonical_queue == "READY":
        return "ready" if explicitly_approved else "drafts"
    # Persisted preflight is authoritative even when an older worker did not
    # set processing_state. Keep blocked rows in the repair queue instead of
    # allowing them to masquerade as Needs Review.
    if any(
        isinstance(by_marketplace, dict)
        and isinstance(by_marketplace.get(market), dict)
        and not bool((by_marketplace.get(market) or {}).get("stale"))
        and any(not _vine_image_enrichment_warning(blocker, listing) for blocker in ((by_marketplace.get(market) or {}).get("blockers") or []))
        for market in configured_targets
    ):
        return "needs_attention"
    if (
        str(listing.processing_state or "").strip().lower() in {"needs_attention", "blocked"}
        and (str(getattr(listing, "processing_blocking_reason", None) or "").strip() or not listing.needs_review)
        and "marketplace publish was blocked" not in str(getattr(listing, "processing_blocking_reason", None) or "").lower()
    ):
        return "needs_attention"
    if reviewability.get("caption_like_title") or reviewability.get("bare_identifier_title"):
        return "needs_attention"
    if (listing.restricted_review_required or listing.needs_review) and not explicitly_approved and str(listing.status).lower() not in {"ready", "posted", "published"}:
        # Needs Review is strictly the publishable approval queue. A row that
        # still has a blocker belongs in Needs Attention so it cannot be
        # accidentally approved as if it were ready.
        preflight = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data, dict) else {}
        by_marketplace = preflight.get("by_marketplace") if isinstance(preflight, dict) else {}
        # `needs_review` is a valid preflight outcome when the marketplace
        # quality service has no hard blockers but still expects a person to
        # inspect/approve the draft. The exact blocker check above remains
        # authoritative; status labels alone must not strand clean Vine drafts
        # in Needs Attention.
        ready = any(
            isinstance(by_marketplace, dict)
            and str((by_marketplace.get(market) or {}).get("status") or "").lower() in {"ready", "ready_with_warnings", "published", "needs_review"}
            and not (by_marketplace.get(market) or {}).get("blockers")
            for market in ("ebay", "facebook", "mercari", "poshmark", "vinted")
        )
        return "review" if ready else "needs_attention"
    if listing.status == ListingStatus.ready:
        preflight_state = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data, dict) else {}
        by_marketplace = preflight_state.get("by_marketplace") if isinstance(preflight_state, dict) else {}
        target_markets = [str(value).strip().lower() for value in marketplace_data.get("targets") or [] if str(value).strip()]
        approved_target = any(
            isinstance(by_marketplace, dict)
            and str((by_marketplace.get(market) or {}).get("status") or "").strip().lower() in {"ready", "ready_with_warnings", "published"}
            for market in target_markets
        )
        if explicitly_approved and approved_target:
            return "ready"
        # An approved row whose fresh readiness has blockers must not remain
        # in Ready: surface it in the operator repair queue instead.
        return "needs_attention" if explicitly_approved else "drafts"
    if not bool(listing.image_urls or []):
        return "drafts"
    return "drafts"


_LISTING_BUCKET_CACHE: dict[int, tuple[tuple, str]] = {}


def _listing_bucket(listing: Listing, remote_ebay_active_ids: set[str] | None = None) -> str:
    updated_at = getattr(listing, "updated_at", None)
    listing_id = getattr(listing, "id", None)
    if updated_at is None or not listing_id:
        return _compute_listing_bucket(listing, remote_ebay_active_ids)
    projections = tuple(
        (
            str(getattr(row, "marketplace", "")),
            str(getattr(row, "status", "")),
            str(getattr(row, "marketplace_listing_id", "") or ""),
        )
        for row in (getattr(listing, "marketplace_listings", None) or [])
    )
    external_id = str(getattr(listing, "ebay_listing_id", "") or "").strip()
    cache_key = (
        updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at),
        str(getattr(listing.status, "value", listing.status) or ""),
        str(getattr(listing, "processing_state", "") or ""),
        bool(getattr(listing, "needs_review", False)),
        bool(getattr(listing, "restricted_review_required", False)),
        external_id,
        str(getattr(listing, "ebay_publish_status", "") or ""),
        external_id in remote_ebay_active_ids if remote_ebay_active_ids is not None else None,
        projections,
    )
    cached = _LISTING_BUCKET_CACHE.get(int(listing_id))
    if cached and cached[0] == cache_key:
        return cached[1]
    bucket = _compute_listing_bucket(listing, remote_ebay_active_ids)
    if len(_LISTING_BUCKET_CACHE) >= 5000:
        _LISTING_BUCKET_CACHE.clear()
    _LISTING_BUCKET_CACHE[int(listing_id)] = (cache_key, bucket)
    return bucket


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
    archive_state = func.lower(func.coalesce(Listing.marketplace_data["archive_state"]["status"].as_string(), ""))
    incomplete_archive_state = archive_state.in_(["remote_state_unknown", "partially_unpublished", "unpublishing", "archive_failed"])
    sold = or_(Listing.sold_at.is_not(None), func.coalesce(Listing.quantity, 1) <= 0)
    archived = and_(or_(labels.contains("archived_vine"), labels.contains("archived_ebay"), labels.contains("archived_sold")), not_(incomplete_archive_state))
    recovery = Listing.source_metadata["recovery"]
    merged_child = func.coalesce(or_(
        recovery["merged_into_recovery_item_id"].as_string().is_not(None),
        recovery["merged_into_recovery_group_id"].as_string().is_not(None),
    ), False)
    normalized = str(normalized_queue or "").strip().lower()
    if normalized == "sold":
        return and_(sold, not_(merged_child))
    if normalized == "archived":
        return and_(archived, not_(sold), not_(merged_child))
    # SQLite and older PostgreSQL rows may have NULL recovery metadata; JSON
    # null comparisons otherwise make the entire visibility predicate NULL.
    # Merged recovery children are projections of their winning parent and
    # must not appear as standalone catalog rows in any queue.
    return and_(not_(sold), not_(archived), not_(merged_child))

_DEFAULT_WORKFLOW_PREFERENCES = {
    "review_before_publish": True,
    "auto_publish_after_approval": False,
    "bulk_approval_enabled": True,
    "listing_preview_mode": "marketplace",
    "default_preview_marketplace": "ebay",
    "shipping_price_threshold": 25.0,
    "shipping_under_threshold_mode": "buyer_pays_shipping",
    "shipping_at_or_above_threshold_mode": "preserve_existing",
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
        **normalize_shipping_policy(stored),
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


def _serialize_listing_response(listing: Listing, seller_config: dict | None = None) -> dict:
    sync_listing_review_state(listing=listing)
    base = ListingResponse.model_validate(listing).model_dump()
    # Never surface scraped policy/navigation fragments as if they were a
    # marketplace taxonomy.  Keep the persisted source evidence untouched, but
    # give the operator an honest, actionable display value until taxonomy
    # resolution supplies a verified category ID.
    raw_category_hint = str(base.get("category_suggestion") or "").strip()
    if not str(base.get("category_id") or "").strip() and is_source_noise_category(raw_category_hint):
        base["category_suggestion"] = "Marketplace category needs resolution"
        base["category_resolution_status"] = "NEEDS_RESOLUTION"
    else:
        base["category_resolution_status"] = "VERIFIED" if str(base.get("category_id") or "").strip() else "UNRESOLVED"
    # The catalog card and its queue filter must share one readiness decision.
    # Frontend-only recomputation can be incomplete because summary responses
    # intentionally omit the heavyweight quality/readiness summaries.
    base["queue_bucket"] = _listing_bucket(listing)
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
            "raw_response": _safe_marketplace_response(row.raw_response),
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
    base["canonical_readiness"] = canonical_listing_readiness(listing)
    canonical = base["canonical_readiness"] if isinstance(base["canonical_readiness"], dict) else {}
    base["attention_reasons"] = [str(reason) for reason in (canonical.get("blocking_reasons") or []) if str(reason).strip()]
    base["processing_stage"] = listing.processing_stage
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    base["description_regeneration_requests"] = list(marketplace_data.get("description_regeneration_requests") or [])[-10:]
    pricing_analysis = ((listing.marketplace_data or {}).get("pricing_analysis") or {}) if isinstance(listing.marketplace_data, dict) else {}
    base["quality_summary"] = compute_listing_quality_summary(listing, pricing_analysis=pricing_analysis)
    base["marketplace_preflight_summary"] = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else None
    preflight_root = base["marketplace_preflight_summary"] or {}
    preflight_by_market = preflight_root.get("by_marketplace") if isinstance(preflight_root, dict) else {}
    preflight_by_market = preflight_by_market if isinstance(preflight_by_market, dict) else {}
    def _preflight_reason(value: dict) -> str:
        for key in ("blockers", "warnings"):
            for issue in value.get(key) or []:
                if isinstance(issue, dict) and str(issue.get("message") or "").strip():
                    return str(issue["message"]).strip()
        return ""
    base["catalog_category"] = {
        "label": str(base.get("category_suggestion") or base.get("detected_category_guess") or "Category not assigned"),
        "id": base.get("category_id"),
        "status": base.get("category_resolution_status"),
        "marketplace": None,
        "taxonomy_tree_id": None,
        "full_path": None,
    }
    category_source = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    category_validation = category_source.get("category_validation") if isinstance(category_source.get("category_validation"), dict) else {}
    validated_id = str(category_validation.get("category_id") or "")
    category_id = str(listing.category_id or "")
    if validated_id and validated_id == category_id:
        marketplace = str(category_source.get("taxonomy_marketplace") or category_validation.get("marketplace") or "").upper()
        tree_id = str(category_source.get("taxonomy_tree_id") or category_validation.get("tree_id") or "")
        identity_consistent = (marketplace, tree_id) in {("EBAY_US", "0"), ("EBAY_MOTORS_US", "100")}
        validation_status = str(category_validation.get("status") or "").lower()
        base["catalog_category"] = {
            "label": str(category_validation.get("category_name") or listing.category_suggestion or category_id),
            "id": category_id,
            "status": "VERIFIED" if identity_consistent and validation_status == "valid_local" else "REVALIDATION_REQUIRED",
            "marketplace": marketplace or None,
            "taxonomy_tree_id": tree_id or None,
            "full_path": category_validation.get("full_path") or category_source.get("category_path"),
            "leaf": category_validation.get("leaf"),
        }
    base["catalog_preflight"] = {
        market: {
            "status": str((value or {}).get("status") or "not_checked"),
            "reason": _preflight_reason(value),
        }
        for market, value in preflight_by_market.items()
        if isinstance(value, dict)
    }
    financial_snapshot = _listing_financial_snapshot(listing, seller_config=seller_config)
    base["catalog_shipping"] = financial_snapshot["shipping"]
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
            "payload_snapshot": _safe_marketplace_response(latest_attempt.payload_snapshot),
            "payload_hash": latest_attempt.payload_hash,
            "inventory_item_sku": latest_attempt.inventory_item_sku,
            "offer_id": latest_attempt.offer_id,
            "marketplace_listing_id": latest_attempt.marketplace_listing_id,
            "marketplace_status": latest_attempt.marketplace_status,
            "translated_error": latest_attempt.translated_error,
            "raw_error": _safe_marketplace_response(latest_attempt.raw_error),
            "retryable": latest_attempt.retryable,
            "retry_count": latest_attempt.retry_count,
            "previous_attempt_id": latest_attempt.previous_attempt_id,
            "job_id": latest_attempt.job_id,
            "task_id": latest_attempt.task_id,
        }
        if latest_attempt
        else None
    )
    base["listing_financials"] = financial_snapshot
    return base


def _listing_financial_snapshot(listing: Listing, seller_config: dict | None = None) -> dict:
    """Compact, honest row-level economics for the catalog workspace.

    These are estimates until a marketplace returns an actual settlement.  We
    keep listing/insertion fees separate from sold-item/final-value fees so the
    catalog never presents an asking-price estimate as a confirmed sale cost.
    """
    try:
        price = float(listing.listing_price or listing.suggested_price or 0)
    except (TypeError, ValueError):
        price = 0.0
    sold = bool(listing.sold_at or int(listing.quantity or 1) <= 0)
    shipping = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
    mode = str(shipping.get("shipping_charge_mode") or "calculated").strip().lower()
    free_shipping = bool(shipping.get("free_shipping")) or mode in {"included", "free", "free_shipping"}
    exact_shipping = listing.shipping_cost if listing.shipping_cost is not None else None
    try:
        exact_shipping = float(exact_shipping) if exact_shipping is not None else None
    except (TypeError, ValueError):
        exact_shipping = None
    # Legacy imports frequently stored 0.00 when the buyer was offered free
    # shipping.  That is not the operator's carrier cost, so only a positive
    # recorded quote is authoritative; zero/null falls through to estimation.
    if exact_shipping is not None and exact_shipping > 0:
        shipping_min = shipping_max = round(exact_shipping, 2)
        shipping_basis = "recorded operator cost"
    elif mode in {"flat", "flat_fee"} and not free_shipping:
        try:
            flat = float(shipping.get("shipping_flat_cost") or shipping.get("flat_rate") or 0)
        except (TypeError, ValueError):
            flat = 0.0
        shipping_min = shipping_max = round(max(0.0, flat), 2)
        shipping_basis = "flat fee" if flat else "estimate"
    else:
        quote = ProfitService().estimate_shipping_quote(listing)
        shipping_min = quote["min"]
        shipping_max = quote["max"]
        shipping_basis = quote["basis"]
    handling_days = shipping.get("handling_time_days")
    if handling_days in (None, ""):
        handling_days = ((listing.marketplace_data or {}).get("shipping") or {}).get("handling_time_days") if isinstance(listing.marketplace_data, dict) else None
    try:
        handling_days = max(1, int(handling_days or 1))
    except (TypeError, ValueError):
        handling_days = 1

    rules = {
        **ProfitService.MARKETPLACE_FEE_RULES,
        # Reference estimates for channels without a synchronized account
        # policy. These are intentionally labeled as estimates in the UI.
        "vinted": {"final_value_pct": 0.0, "fixed": 0.0},
        "offerup": {"final_value_pct": 0.0, "fixed": 0.0},
        "poshmark": {"final_value_pct": 0.20, "fixed": 0.0},
        "etsy": {"final_value_pct": 0.065, "fixed": 0.20},
        "depop": {"final_value_pct": 0.10, "fixed": 0.0},
        "whatnot": {"final_value_pct": 0.08, "fixed": 0.0},
    }
    listing_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    targets = [str(value).strip().lower() for value in (listing_data.get("targets") or []) if str(value).strip()]
    # Show the complete fee reference matrix even before a channel is
    # connected/selected.  The UI uses `configured` to gray those channels.
    markets = ["ebay", "facebook", "mercari", "vinted", "offerup", "poshmark", "etsy", "depop", "whatnot"]
    configured_markets = set(targets)
    if listing.ebay_listing_id:
        configured_markets.add("ebay")
    tax_data = listing_data.get("sales_tax") if isinstance(listing_data.get("sales_tax"), dict) else {}
    tax_raw = tax_data.get("amount", listing_data.get("sales_tax_amount"))
    try:
        tax_amount = max(0.0, float(tax_raw)) if tax_raw not in (None, "") else None
    except (TypeError, ValueError):
        tax_amount = None
    marketplace_costs = {}
    for market in markets:
        if market == "ebay":
            ebay_result = calculate_expected_ebay_fee(listing, seller_config)
            marketplace_costs[market] = {
                "fee_percent": ebay_result["effectiveRate"],
                "fee_basis": "eBay final value fee base: item price + buyer-paid shipping + estimated sales tax",
                "fixed_fee": ebay_result["orderFee"],
                "fee_amount": ebay_result["totalExpectedFee"],
                "listing_fee": 0.0,
                "sold_item_fee": ebay_result["totalExpectedFee"],
                "estimated_total_fee": ebay_result["totalExpectedFee"],
                "free_listing_allowance": 250,
                "insertion_fee_basis": "eBay 250 free listings/month allowance; usage sync pending",
                "configured": market in configured_markets,
                "rule_id": ebay_result["ruleId"],
                "rule_label": ebay_result["ruleLabel"],
                "fee_base": ebay_result["feeBase"],
                "percentage_fee": ebay_result["percentageFee"],
                "order_fee": ebay_result["orderFee"],
                "seller_plan": ebay_result["sellerPlan"],
                "calculation_type": ebay_result["calculationType"],
                "tax_amount": ebay_result["taxAmount"],
                "fee_rule_version": ebay_result["version"],
            }
            continue
        rule = rules.get(market)
        if rule:
            percent = float(rule["final_value_pct"])
            fixed = float(rule["fixed"])
            transaction_base = price + (tax_amount or 0.0)
            sold_fee = round((transaction_base * percent) + fixed, 2) if transaction_base > 0 else 0.0
            # eBay's monthly allowance is represented explicitly.  A live
            # usage sync can replace this with the remaining allowance; never
            # invent a paid insertion fee when usage is unknown.
            insertion_fee = 0.0 if market == "ebay" else 0.0
            marketplace_costs[market] = {
                "fee_percent": round(percent * 100, 3),
                "fee_basis": "current configured estimate; marketplace/category policy may vary",
                "fixed_fee": round(fixed, 2),
                "fee_amount": sold_fee,
                "listing_fee": insertion_fee,
                "sold_item_fee": sold_fee,
                "estimated_total_fee": round(insertion_fee + sold_fee, 2),
                "free_listing_allowance": 250 if market == "ebay" else None,
                "insertion_fee_basis": "eBay 250 free listings/month allowance; usage sync pending" if market == "ebay" else "not configured",
                "configured": market in configured_markets,
            }
        else:
            marketplace_costs[market] = {
                "fee_percent": None, "fixed_fee": None, "listing_fee": None,
                "fee_amount": None, "sold_item_fee": None, "estimated_total_fee": None,
                "free_listing_allowance": None, "insertion_fee_basis": "marketplace fee policy not configured",
                "configured": market in configured_markets,
            }
    ebay = marketplace_costs.get("ebay") or {}
    return {
        "item_value": round(price, 2) if price > 0 else None,
        "sold": sold,
        "shipping": {
            "mode": "free" if free_shipping else mode,
            "min": shipping_min,
            "max": shipping_max,
            "basis": shipping_basis,
            "buyer_pays": 0.0 if free_shipping else None,
            "operator_pays": True,
            "handling_time_days": handling_days,
            "service": str((ProfitService().estimate_shipping_quote(listing).get("service") or "usps_ground_advantage")).replace("_", " ").title(),
        },
        "marketplace_costs": marketplace_costs,
        "expected_ebay_fee": calculate_expected_ebay_fee(listing, seller_config),
        "sales_tax_collected": round(tax_amount, 2) if tax_amount is not None else None,
        "listing_fee_total": round(float(ebay.get("listing_fee") or 0), 2),
        "sold_item_fee_total": round(float(ebay.get("sold_item_fee") or 0), 2),
        "net_before_shipping": round(price - float(ebay.get("listing_fee") or 0) - float(ebay.get("sold_item_fee") or 0), 2) if price > 0 else None,
        "net_after_shipping_min": round(price - float(ebay.get("listing_fee") or 0) - float(ebay.get("sold_item_fee") or 0) - shipping_max, 2) if price > 0 else None,
        "net_after_shipping_max": round(price - float(ebay.get("listing_fee") or 0) - float(ebay.get("sold_item_fee") or 0) - shipping_min, 2) if price > 0 else None,
    }


def _safe_marketplace_response(value):
    """Expose operational response context without leaking credentials/secrets."""
    secret_keys = {"token", "access_token", "refresh_token", "client_secret", "secret", "password", "cookie", "authorization", "auth_header", "device_token", "device_secret"}
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if str(key).strip().lower() in secret_keys or any(marker in str(key).strip().lower() for marker in ("token", "secret", "password", "cookie")) else _safe_marketplace_response(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_marketplace_response(item) for item in value]
    return value


def _serialize_listing_summary(listing: Listing) -> dict:
    sync_listing_review_state(listing=listing)
    base = ListingResponse.model_validate(listing).model_dump()
    base["queue_bucket"] = _listing_bucket(listing)
    base["canonical_readiness"] = canonical_listing_readiness(listing)
    canonical = base["canonical_readiness"] if isinstance(base["canonical_readiness"], dict) else {}
    base["attention_reasons"] = [str(reason) for reason in (canonical.get("blocking_reasons") or []) if str(reason).strip()]
    base["processing_stage"] = listing.processing_stage
    # The paginated Listings page uses the summary serializer.  Keep the
    # at-a-glance fields available on that path too; previously only the full
    # detail serializer populated catalog_category, so valid Vine categories
    # rendered as "not assigned" in the actual review grid.
    raw_category = str(base.get("category_suggestion") or "").strip()
    category_label = raw_category or str(base.get("detected_category_guess") or "Category not assigned")
    base["catalog_category"] = {
        "label": category_label,
        "id": base.get("category_id"),
        "status": "VERIFIED" if str(base.get("category_id") or "").strip() else "UNRESOLVED",
    }
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    preflight_root = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else {}
    preflight_by_market = preflight_root.get("by_marketplace") if isinstance(preflight_root.get("by_marketplace"), dict) else {}
    base["catalog_preflight"] = {
        market: {"status": str((value or {}).get("status") or "not_checked"), "reason": ""}
        for market, value in preflight_by_market.items() if isinstance(value, dict)
    }
    financial_snapshot = _listing_financial_snapshot(listing)
    base["catalog_shipping"] = financial_snapshot["shipping"]
    base["listing_financials"] = financial_snapshot
    base.pop("marketplace_statuses", None)
    base.pop("latest_publish_attempt", None)
    # Catalog cards need a compact projection. Full source facts, per-market
    # copy, editor fields and historical snapshots belong to the one-listing
    # detail request, not every row in a 25-row page.
    base.pop("description", None)
    base.pop("canonical_description", None)
    base.pop("marketplace_descriptions", None)
    base.pop("item_specifics", None)
    for key in ("condition_data", "shipping_profile"):
        base.pop(key, None)

    source = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    base["source_metadata"] = {
        key: ([source[key][0]] if isinstance(source.get(key), list) and source[key] else source[key])
        for key in ("image_urls", "original_image_urls", "primary_image_url", "source_image_url", "operator_approved_at")
        if key in source
    }
    raw_images = base.get("listing_images") if isinstance(base.get("listing_images"), list) else []
    approved_images = [image for image in raw_images if isinstance(image, dict) and image.get("operator_state") != "rejected"]
    chosen_image = (
        next((image for image in approved_images if image.get("role") == "primary" and not image.get("is_reference")), None)
        or next((image for image in approved_images if not image.get("is_reference")), None)
        or next((image for image in approved_images if image.get("role") == "primary"), None)
        or (approved_images[0] if approved_images else None)
    )
    image_fields = ("id", "storage_path", "url", "role", "operator_state", "is_reference", "source_platform", "sort_order", "position")
    base["listing_images"] = [
        {key: chosen_image.get(key) for key in image_fields if key in chosen_image}
    ] if chosen_image else []
    image_urls = base.get("image_urls") if isinstance(base.get("image_urls"), list) else []
    all_image_urls = [
        *image_urls,
        *(source.get("image_urls") if isinstance(source.get("image_urls"), list) else []),
        *(source.get("original_image_urls") if isinstance(source.get("original_image_urls"), list) else []),
    ]
    base["listing_image_count"] = len(approved_images) or len({str(value) for value in all_image_urls if value})
    if image_urls:
        base["image_urls"] = image_urls[:1]

    raw_marketplace = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    compact_marketplace: dict = {}
    for key in ("targets", "error", "archive_state", "management_capability"):
        if key in raw_marketplace:
            compact_marketplace[key] = raw_marketplace[key]
    pricing = raw_marketplace.get("pricing_analysis")
    if isinstance(pricing, dict):
        compact_marketplace["pricing_analysis"] = {
            key: pricing[key]
            for key in ("price_confidence", "confidence", "stale", "method", "provenance", "status", "placeholder")
            if key in pricing
        }
    preflight = raw_marketplace.get("marketplace_preflight")
    if isinstance(preflight, dict):
        by_market = preflight.get("by_marketplace") if isinstance(preflight.get("by_marketplace"), dict) else {}
        def compact_issues(issues):
            compact = []
            for issue in (issues or [])[:3]:
                if isinstance(issue, dict):
                    compact.append({field: issue[field] for field in ("code", "message", "retryable") if field in issue})
                else:
                    compact.append(str(issue)[:240])
            return compact

        compact_marketplace["marketplace_preflight"] = {
            "by_marketplace": {
                market: {
                    "status": value.get("status"),
                    "stale": value.get("stale"),
                    "last_checked_at": value.get("last_checked_at"),
                    "blockers": compact_issues(value.get("blockers")),
                    "warnings": compact_issues(value.get("warnings")),
                }
                for market, value in by_market.items() if isinstance(value, dict)
            }
        }
    base["marketplace_data"] = compact_marketplace
    base["marketplace_preflight_summary"] = compact_marketplace.get("marketplace_preflight")

    financials = base.get("listing_financials") if isinstance(base.get("listing_financials"), dict) else {}
    costs = financials.get("marketplace_costs") if isinstance(financials.get("marketplace_costs"), dict) else {}
    cost_fields = ("fee_percent", "fixed_fee", "fee_amount", "fee_base", "rule_label", "fee_basis", "configured", "listing_fee")
    base["listing_financials"] = {
        "marketplace_costs": {
            market: {field: cost[field] for field in cost_fields if field in cost}
            for market, cost in costs.items() if isinstance(cost, dict)
        },
        "sales_tax_collected": financials.get("sales_tax_collected"),
    }

    for key in ("readiness_summary", "canonical_readiness"):
        value = base.get(key)
        if isinstance(value, dict):
            base[key] = {
                field: value[field]
                for field in ("queue", "publishable", "destination_publishable", "review_required", "blocking_reasons", "blockers", "warnings", "status", "actual_image_count")
                if field in value
            }
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
    # This pre-tenant endpoint historically exposed published rows across every
    # account. Keep the path as a clear migration response, never as a global
    # catalog API. Public catalog access now requires /public/stores/{slug}.
    raise HTTPException(
        status_code=410,
        detail="This catalog link is retired. Use the store owner's tenant-specific public store link.",
    )


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
    before = {field: getattr(listing, field, None) for field in ("title", "description", "canonical_description", "marketplace_descriptions", "listing_price", "suggested_price", "quantity", "condition", "category_id", "category_suggestion", "item_specifics", "image_urls", "marketplace_data", "platform_quantities", "custom_labels")}
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
    requested_marketplaces: list[str] | None = None,
) -> dict:
    marketplace_data = dict(listing.marketplace_data or {})
    current_targets = marketplace_data.get("targets")
    selected_targets = requested_marketplaces if requested_marketplaces is not None else current_targets
    targets = [str(value).strip().lower() for value in (selected_targets or ["ebay"]) if str(value).strip()]
    # Stored cross-post targets describe preparation metadata.  They are not
    # an instruction to block an eBay approval/publish attempt on Facebook (or
    # any other channel).  Only explicitly requested destinations participate
    # in this approval decision.
    targets = list(dict.fromkeys(targets)) or ["ebay"]
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
        evaluation = preflight_service.evaluate_publishability(db, listing, market, refresh=True)
        preflight = evaluation["preflight"]
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
    state = str(((listing.marketplace_data or {}).get("archive_state") or {}).get("status") or "").upper() if isinstance(listing.marketplace_data, dict) else ""
    if state in {"REMOTE_STATE_UNKNOWN", "PARTIALLY_UNPUBLISHED", "UNPUBLISHING", "ARCHIVE_FAILED"}:
        return False
    return "archived_vine" in labels and (not state or state == "ARCHIVED")


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


class MarketplaceOperationRequest(BaseModel):
    marketplaces: list[str]
    field: str
    action: str = "set"
    value: object | None = None


class MarketplaceOperationPreviewRequest(MarketplaceOperationRequest):
    """Explicit preview contract for field- and destination-scoped edits."""


class MarketplaceDescriptionUpdateRequest(BaseModel):
    description: str
    provenance: str = "operator_edited"


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
def apply_template_to_listing(
    listing_id: int,
    payload: ListingTemplateApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    template = db.get(ListingTemplate, payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    ensure_user_owns_resource(current_user, template.user_id)
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
            "archived_count": int(result.get("archived_count") or 0),
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
    # Combined status filters are comma-separated (for example published,
    # review,attention,processing).  32 characters truncated valid unions and
    # caused the catalog request to fail validation before querying.
    # Accept arbitrary comma-separated input at the transport boundary. The
    # normalized parser below limits matching to known queue buckets; a short
    # Pydantic max_length here incorrectly rejected valid multi-select views.
    queue: str | None = Query(default=None),
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
    # Every normal catalog request is tenant-scoped. Platform-wide views must
    # use an explicit admin endpoint; never infer that from a missing query arg.
    filters = [Listing.user_id == current_user.id]
    account_policy_settings = (current_user.settings_json or {}).get("ebay_marketplace_policy_settings") if isinstance(current_user.settings_json, dict) else {}
    seller_config = account_policy_settings if isinstance(account_policy_settings, dict) else {}
    normalized_queue = str(queue or "").strip().lower()
    if normalized_queue == "attention":
        normalized_queue = "needs_attention"
    queue_aliases = {
        "attention": "needs_attention",
        "needs_attention": "needs_attention",
        "draft": "drafts",
        "drafts": "drafts",
        "needs_review": "review",
        "review": "review",
    }
    queue_values = {
        queue_aliases.get(value, value)
        for value in normalized_queue.split(",")
        if value and value != "all"
    }
    multi_queue = len(queue_values) > 1
    # The active eBay snapshot is shared metadata and can be expensive to
    # refresh.  Catalog/search/review requests do not need it; only the
    # Published queue requires remote-identity reconciliation.
    remote_ebay_active_ids = None
    # A combined view containing Published still needs the authoritative
    # active-eBay snapshot; otherwise published rows are classified using
    # incomplete local identities and the aggregate can exclude most live
    # inventory.
    if "published" in queue_values:
        # A stale remote snapshot is still authoritative for the last verified
        # live count. Falling back to every local ebay_listing_id inflated the
        # dashboard/catalog (e.g. 213 local identities vs 186 confirmed live).
        remote_ebay_active_ids = cached_ebay_active_ids(db, current_user.id)
        if remote_ebay_active_ids is None:
            remote_ebay_active_ids = last_ebay_active_ids(db, current_user.id)
    normalized_source = str(source_type or "").strip().lower()
    if normalized_source and normalized_source != "all":
        if (
            normalized_source == "amazon_vine"
            and "published" in queue_values
            and remote_ebay_active_ids is not None
        ):
            # The operator's eBay account contains Vine-published inventory
            # that was historically imported as
            # ``ebay_history_reconciliation`` when the original local
            # identity could not be matched.  For the confirmed Published
            # view, the remote eBay identity is authoritative: include those
            # rows in the Vine inventory rather than reporting an incomplete
            # 114-row subset.  Other queues retain strict source filtering.
            filters.append(
                or_(
                    Listing.source_type == normalized_source,
                    Listing.ebay_listing_id.in_(remote_ebay_active_ids) if remote_ebay_active_ids else Listing.id.in_([-1]),
                )
            )
        else:
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
        ebay_identity = str(listing.ebay_listing_id or "").strip()
        if remote_ebay_active_ids is not None:
            ebay_live = ebay_identity in remote_ebay_active_ids
        else:
            ebay_live = str(listing.ebay_publish_status or "").upper() == "POSTED" or bool(ebay_identity)
        if ebay_live:
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

    def _published_remote_price(listing: Listing) -> float | None:
        """Return a price from the verified eBay sync for this exact identity."""
        data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        sync = data.get("ebay_sync") if isinstance(data.get("ebay_sync"), dict) else {}
        identity = str(listing.ebay_listing_id or "").strip()
        if not identity or str(sync.get("ebay_listing_id") or "").strip() != identity:
            return None
        try:
            price = float(sync.get("remote_price"))
        except (TypeError, ValueError):
            return None
        return round(price, 2) if price > 0 else None

    def _aggregate_price(listing: Listing, sold_view: bool = False, published_view: bool = False) -> float | None:
        """Return the effective operator-facing value for the filtered set.

        This deliberately uses the current listing price (or a documented
        suggested-price fallback), never acquisition/ETV/cost fields.  Sold
        views prefer the confirmed sale amount.  Explicit placeholder/fallback
        pricing is excluded rather than silently presented as real value.
        Quantity is intentionally not multiplied: PosterPro listings represent
        one physical item unless a future aggregate-specific quantity contract
        is introduced.
        """
        if published_view:
            # Confirmed live eBay prices are authoritative. Legacy Vine ETV
            # provenance describes the price's history, not whether a current
            # remotely-live listing has a valid asking price.
            remote_price = _published_remote_price(listing)
            if remote_price is not None:
                return remote_price
            for value in (listing.listing_price, listing.suggested_price):
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if numeric > 0:
                    return round(numeric, 2)
            return None

        pricing = listing.marketplace_data.get("pricing_analysis") if isinstance(listing.marketplace_data, dict) else None
        if isinstance(pricing, dict):
            provenance = " ".join(
                str(pricing.get(key) or "")
                for key in ("method", "source", "provenance", "strategy", "status")
            ).lower()
            etv_provenance = str(pricing.get("provenance") or "").strip().lower()
            try:
                listing_price_positive = float(listing.listing_price or 0) > 0
            except (TypeError, ValueError, OverflowError):
                listing_price_positive = False
            valid_vine_etv_ask = (
                str(listing.source_type or "").strip().lower() == "amazon_vine"
                and etv_provenance in {"vine_etv_fallback", "vine_estimated_tax_value_fallback"}
                and listing_price_positive
            )
            if pricing.get("placeholder") is True or pricing.get("fallback_without_approval") is True or any(
                token in provenance
                for token in ("placeholder", "default_price", "fallback_without_approval", "temporary price", "unresearched")
            ) or ("etv" in provenance and not valid_vine_etv_ask):
                return None
        if sold_view and listing.sale_price is not None:
            try:
                sale_price = float(listing.sale_price)
                if sale_price > 0:
                    return round(sale_price, 2)
            except (TypeError, ValueError):
                pass
        return resolve_authoritative_listing_price(listing)

    def _aggregate_sql(aggregate_filters: list, sold_view: bool = False, published_view: bool = False) -> dict:
        """Calculate catalog value using one database-side aggregate query.

        This intentionally operates on scalar columns and the compact JSON
        provenance marker only; it never hydrates Listing ORM objects.  The
        same tenant/search/visibility predicates are supplied by the caller.
        """
        provenance_text = func.coalesce(cast(Listing.marketplace_data, String), "")
        effective_price = (
            func.coalesce(Listing.sale_price, Listing.listing_price, Listing.suggested_price)
            if sold_view
            else func.coalesce(Listing.listing_price, Listing.suggested_price)
        )
        # A Vine row commonly retains ETV/ASIN provenance next to its current
        # canonical selling price.  That provenance is evidence, not proof that
        # the current price is a placeholder.  Only an explicit placeholder
        # marker or an explicitly unapproved fallback excludes a positive
        # value.  Broad substring checks (especially ``etv``) previously
        # classified hundreds of legitimate Vine prices as missing.
        placeholder = or_(
            provenance_text.ilike('%"placeholder": true%'),
            provenance_text.ilike('%"fallback_without_approval": true%'),
            provenance_text.ilike('%"price_state": "placeholder"%'),
            provenance_text.ilike('%"provenance": "placeholder"%'),
        )
        # A confirmed published eBay listing necessarily had a real sale
        # price when it was created.  Legacy Vine metadata may still contain
        # ETV/fallback provenance alongside that valid listing price; that
        # metadata must not make a live listing appear to have a missing
        # price.  Placeholder filtering remains active for unpublished queues.
        valid_price = and_(effective_price.is_not(None), effective_price > 0) if published_view else and_(effective_price.is_not(None), effective_price > 0, not_(placeholder))
        result = db.execute(
            select(
                func.count(Listing.id).label("count"),
                func.coalesce(func.sum(case((valid_price, effective_price), else_=0.0)), 0.0).label("total"),
                func.avg(case((valid_price, effective_price), else_=None)).label("average"),
                func.sum(case((valid_price, 1), else_=0)).label("priced"),
                func.sum(case((valid_price, 0), else_=1)).label("missing"),
                # eBay insertion/listing fees are zero while the account's
                # 250 monthly free-listing allowance is available.  Confirmed
                # or estimated final-value fees apply only to sold rows.
                func.coalesce(func.sum(case((and_(valid_price, Listing.sold_at.is_not(None)), effective_price * 0.1325 + 0.30), else_=0.0)), 0.0).label("sold_item_fees"),
                func.coalesce(func.sum(case((valid_price, effective_price * 0.1325 + 0.30), else_=0.0)), 0.0).label("expected_fee_ebay"),
                func.coalesce(func.sum(case((valid_price, effective_price * 0.05), else_=0.0)), 0.0).label("expected_fee_facebook"),
                func.coalesce(func.sum(case((valid_price, effective_price * 0.10), else_=0.0)), 0.0).label("expected_fee_mercari"),
            ).select_from(Listing).where(*aggregate_filters)
        ).one()
        shipping_text = func.coalesce(cast(Listing.shipping_profile, String), "")
        free_shipping = or_(shipping_text.ilike('%"free_shipping": true%'), shipping_text.ilike('%"shipping_charge_mode": "included"%'))
        # Source imports may store human-readable values such as
        # ``3 lb (estimated)``. Strip units before the SQL cast so one legacy
        # row cannot make the entire Listings catalog fail with HTTP 500.
        def numeric_json_value(value, fallback):
            # SQLite's test dialect has no regexp_replace; its fixtures use
            # numeric values, so a direct cast is the portable fallback.
            if db.bind is not None and db.bind.dialect.name == "sqlite":
                return cast(func.coalesce(value, fallback), Numeric)
            cleaned = func.nullif(func.regexp_replace(value, "[^0-9.]", "", "g"), "")
            return cast(func.coalesce(cleaned, fallback), Numeric)

        package_weight = numeric_json_value(
            func.coalesce(
                Listing.shipping_profile["package_weight"].as_string(),
                Listing.shipping_profile["item_weight"].as_string(),
                "1",
            ),
            "1",
        )
        package_length = numeric_json_value(Listing.shipping_profile["package_dimensions"]["length"].as_string(), "0")
        package_width = numeric_json_value(Listing.shipping_profile["package_dimensions"]["width"].as_string(), "0")
        package_height = numeric_json_value(Listing.shipping_profile["package_dimensions"]["height"].as_string(), "0")
        dimensional_weight = (package_length * package_width * package_height) / 139.0
        billable_weight = case((dimensional_weight > package_weight, dimensional_weight), else_=package_weight)
        # Buyer-free postage still costs the operator. This SQL-side estimate
        # mirrors ProfitService's carrier-rate brackets and uses the stored
        # packaged weight rather than returning a misleading zero.
        carrier_rate = case(
            (billable_weight <= 1, 6.25),
            (billable_weight <= 2, 7.40),
            (billable_weight <= 3, 8.55),
            (billable_weight <= 5, 10.85),
            else_=14.50,
        )
        service_text = func.lower(func.coalesce(cast(Listing.shipping_profile, String), ""))
        provider_rate = case(
            (service_text.ilike("%dhl%"), carrier_rate * 1.55),
            (service_text.ilike("%fedex%"), carrier_rate * 1.38),
            (service_text.ilike("%ups%"), carrier_rate * 1.34),
            (service_text.ilike("%ontrac%"), carrier_rate * 1.22),
            (service_text.ilike("%priority%"), carrier_rate * 1.45),
            (service_text.ilike("%ground advantage%"), carrier_rate),
            (service_text.ilike("%usps%"), carrier_rate),
            else_=carrier_rate * 1.20,
        )
        shipping_base = case(
            (and_(Listing.shipping_cost.is_not(None), Listing.shipping_cost > 0), Listing.shipping_cost),
            (free_shipping, provider_rate),
            # Calculated/unknown buyer postage still has an operator carrier
            # cost.  Use the selected-service estimate rather than price
            # heuristics or a misleading zero.
            else_=provider_rate,
        )
        shipping_result = db.execute(
            select(
                func.coalesce(func.sum(case((valid_price, shipping_base * 0.75), else_=0.0)), 0.0).label("shipping_min"),
                func.coalesce(func.sum(case((valid_price, shipping_base * 1.25), else_=0.0)), 0.0).label("shipping_max"),
            ).select_from(Listing).where(*aggregate_filters)
        ).one()
        total_value = round(float(result.total or 0), 2)
        sold_item_fees = round(float(result.sold_item_fees or 0), 2)
        expected_fee_totals = [float(result.expected_fee_ebay or 0), float(result.expected_fee_facebook or 0), float(result.expected_fee_mercari or 0)]
        expected_fee_min = round(min(expected_fee_totals), 2)
        expected_fee_max = round(max(expected_fee_totals), 2)
        return {
            "aggregate_value": total_value,
            "average_value": round(float(result.average), 2) if result.average is not None else 0.0,
            "priced_count": int(result.priced or 0),
            "price_source_counts": None,
            "missing_price_count": int(result.missing or 0),
            "shipping_min_total": round(float(shipping_result.shipping_min or 0), 2),
            "shipping_max_total": round(float(shipping_result.shipping_max or 0), 2),
            "listing_fee_total": 0.0,
            "sold_item_fee_total": sold_item_fees,
            "expected_sold_fee_min_total": expected_fee_min,
            "expected_sold_fee_max_total": expected_fee_max,
            "net_before_shipping_total": round(total_value - expected_fee_max, 2),
            "net_after_shipping_min_total": round(total_value - expected_fee_max - float(shipping_result.shipping_max or 0), 2),
            "net_after_shipping_max_total": round(total_value - expected_fee_min - float(shipping_result.shipping_min or 0), 2),
        }

    # The frontend's public tab key is `attention`; it was normalized above.
    queue_filters = []
    if normalized_queue and normalized_queue != "all" and not multi_queue:
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
        elif normalized_queue == "processing":
            queue_filters.append(Listing.processing_state.in_(["processing", "pending", "queued", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"]))
        elif normalized_queue == "needs_attention":
            # Detailed preflight blockers live in JSON and are evaluated by
            # _listing_bucket below; avoid a SQL predicate that would hide
            # legacy rows whose processing_state was never populated.
            pass
        elif normalized_queue == "published":
            if remote_ebay_active_ids is None:
                ebay_live_filter = and_(
                    Listing.ebay_listing_id.is_not(None),
                    func.trim(Listing.ebay_listing_id) != "",
                    or_(Listing.status == ListingStatus.PUBLISHED, Listing.ebay_publish_status == "POSTED"),
                )
            else:
                ebay_live_filter = Listing.ebay_listing_id.in_(remote_ebay_active_ids) if remote_ebay_active_ids else Listing.id.in_([-1])
                # The Published tab is the confirmed eBay inventory view. Do
                # not let an unrelated Facebook/Mercari projection inflate or
                # distort the eBay count and value summary.
            queue_filters.append(ebay_live_filter)
        elif normalized_queue == "ready":
            queue_filters.append(and_(Listing.status == ListingStatus.ready, Listing.source_metadata["operator_approved_at"].as_string().is_not(None), not_(exists(select(1).where(
                and_(
                    MarketplaceListing.listing_id == Listing.id,
                    MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
                    MarketplaceListing.marketplace_listing_id.is_not(None),
                    func.trim(MarketplaceListing.marketplace_listing_id) != "",
                )
            )))))
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
                )
            )

    if queue_filters:
        # Lifecycle buckets are derived by canonical_listing_readiness below.
        # Do not pre-filter them with legacy denormalized flags (for example,
        # ``status=draft`` or ``needs_review``), or a row whose durable state
        # was repaired by the readiness service can never reach the Python
        # classifier. Sold/archived visibility remains SQL-scoped above.
        if normalized_queue in {"drafts", "processing", "needs_attention", "review", "ready"}:
            queue_filters = []

    # Multiple status buttons are additive.  The canonical Python classifier
    # is the same classifier used by the row serializer and the frontend; use
    # it for the union instead of a second SQL approximation that can disagree
    # with readiness (and leak Processing rows into a Review + Attention view).
    # The single-queue path remains SQL/paginated where its predicate is exact.
    if multi_queue:
        queue_filters = []

    if normalized_queue == "review" and not multi_queue:
        # Keep canonical readiness authoritative, but avoid running it against
        # every archived, published, or actively-processing row before the
        # first review page can render. This is a conservative candidate
        # superset: SQL-classified review rows, explicit review flags, and
        # ready rows that may have stale legacy flags.
        statement = statement.where(or_(
            Listing.needs_review.is_(True),
            Listing.restricted_review_required.is_(True),
            Listing.status == ListingStatus.ready,
        ))

    if queue_filters:
        statement = statement.where(*queue_filters)
    if normalized_queue == "published" and not multi_queue:
        # A remote eBay snapshot is authoritative for live status. Preserve a
        # remotely-live row even if a stale local archive label remains on it.
        visibility_filter = and_(
            not_(or_(Listing.sold_at.is_not(None), func.coalesce(Listing.quantity, 1) <= 0)),
            not_(func.coalesce(or_(
                Listing.source_metadata["recovery"]["merged_into_recovery_item_id"].as_string().is_not(None),
                Listing.source_metadata["recovery"]["merged_into_recovery_group_id"].as_string().is_not(None),
            ), False)),
        )
    else:
        visibility_filter = _listing_visibility_filter(normalized_queue if not multi_queue else "all")
    statement = statement.where(visibility_filter)
    all_filters = [*filters, *queue_filters, visibility_filter]
    normalized_marketplace = str(marketplace or "").strip().lower()
    normalized_readiness = str(readiness or "").strip().lower()
    needs_python_filtering = (
        # The lifecycle classifier includes canonical readiness, retries,
        # stale-blocker cleanup and capability/policy routing.  A SQL-only
        # aggregate cannot approximate those rules without producing badge
        # counts that disagree with the exact Needs Attention tab.
        (normalized_queue in {"", "all"} and (page is not None or page_size is not None))
        or
        (normalized_marketplace and normalized_marketplace != "all")
        or (normalized_readiness and normalized_readiness != "all")
        or normalized_queue in {"drafts", "ready", "review", "needs_attention", "published", "failed", "processing"}
        or normalized_queue in {"publisher_unsupported", "reconciliation", "policy_blocked", "provider_retry"}
        or multi_queue
        or remote_ebay_active_ids is not None
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
        return [serializer(listing, seller_config=seller_config) if not summary_only else serializer(listing) for listing in rows]

    resolved_page = page or 1
    resolved_page_size = page_size or 25
    if needs_python_filtering:
        rows = db.execute(
            statement.order_by(sort_expr, desc(Listing.updated_at))
        ).scalars().all()
        filtered_rows = []
        resolved_buckets: dict[int, str] = {}
        for listing in rows:
            if not _matches_marketplace_filter(listing, normalized_marketplace):
                continue
            if not _matches_readiness_filter(listing, normalized_readiness):
                continue
            bucket_value = _listing_bucket(listing, remote_ebay_active_ids)
            resolved_buckets[listing.id] = bucket_value
            # Multi-select status filters are an additive union. Apply the
            # same canonical bucket classifier used for row serialization so
            # selecting (for example) Review + Attention never leaks
            # Processing rows into the result.
            if queue_values and bucket_value not in queue_values:
                continue
            filtered_rows.append(listing)
        rows = filtered_rows
        total = len(rows)
        bucket_counts: dict[str, int] = {}
        for listing in rows:
            bucket = resolved_buckets[listing.id]
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        bucket_counts["all"] = total
        bucket_counts["attention"] = bucket_counts.get("needs_attention", 0)
        bucket_counts["vine"] = sum(1 for listing in rows if _is_vine_source(listing))
        # The canonical classifier is still authoritative for which rows the
        # catalog returns. Reuse only its scalar IDs for a precise SQL
        # aggregate; do not hydrate another ORM collection for value math.
        aggregate_filters = [Listing.id.in_([listing.id for listing in rows])]
        aggregate_rows_for_metrics = list(rows)
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
        aggregate_filters = [*filters, *queue_filters, visibility_filter]
        if normalized_marketplace and normalized_marketplace != "all":
            aggregate_filters.append(cast(Listing.marketplace_data, String).ilike(f'%"{normalized_marketplace}"%'))
        if normalized_readiness and normalized_readiness != "all":
            if normalized_readiness == "missing_price":
                aggregate_filters.append(or_(Listing.listing_price.is_(None), Listing.listing_price <= 0))
            elif normalized_readiness == "missing_category":
                aggregate_filters.append(and_(Listing.category_id.is_(None), Listing.category_suggestion.is_(None)))
    if needs_python_filtering:
        # The filtered rows are already hydrated for the canonical queue
        # classifier.  Reusing them for the small catalog summary avoids a
        # second pair of expensive JSON/regexp aggregate scans that previously
        # made the review catalog appear to hang on larger imports.
        sold_view = "sold" in queue_values
        published_view = "published" in queue_values
        review_view = "review" in queue_values and not published_view
        values = [
            _aggregate_price(row, sold_view=sold_view, published_view=published_view)
            for row in aggregate_rows_for_metrics
        ]
        valid_values = [value for value in values if value is not None]
        aggregate_total = round(sum(valid_values), 2)
        price_source_counts = {"verified_remote": 0, "local_published_fallback": 0, "missing": 0}
        if published_view:
            for row, value in zip(aggregate_rows_for_metrics, values):
                if value is None:
                    price_source_counts["missing"] += 1
                elif _published_remote_price(row) is not None:
                    price_source_counts["verified_remote"] += 1
                else:
                    price_source_counts["local_published_fallback"] += 1
        aggregate_summary = {
            "aggregate_value": aggregate_total,
            "average_value": round(aggregate_total / len(valid_values), 2) if valid_values else None,
            "priced_count": len(valid_values),
            "price_source_counts": price_source_counts if published_view else None,
            "missing_price_count": len(values) - len(valid_values),
            "shipping_min_total": 0.0,
            "shipping_max_total": 0.0,
            "listing_fee_total": 0.0,
            "sold_item_fee_total": round(sum(value * 0.1325 + 0.30 for row, value in zip(aggregate_rows_for_metrics, values) if value is not None and row.sold_at is not None), 2),
            "expected_sold_fee_min_total": round(aggregate_total * 0.05, 2),
            "expected_sold_fee_max_total": round(aggregate_total * 0.1325 + (0.30 * len(valid_values)), 2),
            "net_before_shipping_total": round(aggregate_total - (aggregate_total * 0.1325 + (0.30 * len(valid_values))), 2),
            "net_after_shipping_min_total": round(aggregate_total - (aggregate_total * 0.1325 + (0.30 * len(valid_values))), 2),
            "net_after_shipping_max_total": round(aggregate_total - (aggregate_total * 0.05), 2),
        }
        if review_view:
            # Needs Review can target different marketplaces and its rows do
            # not yet have a destination-specific fee/shipping calculation.
            # Do not present generic percentages or zero postage as known
            # values; the gross asking-price aggregate above remains exact.
            aggregate_summary.update({
                "shipping_min_total": None,
                "shipping_max_total": None,
                "listing_fee_total": None,
                "sold_item_fee_total": None,
                "expected_sold_fee_min_total": None,
                "expected_sold_fee_max_total": None,
                "net_before_shipping_total": None,
                "net_after_shipping_min_total": None,
                "net_after_shipping_max_total": None,
            })
        if published_view:
            # The Published queue is the confirmed-live eBay population.
            # Estimate each item's fees with the shared category-aware eBay
            # fee service and operator postage with the shared shipping model.
            # Missing/incomplete calculations remain null so the UI can show
            # Unknown instead of asserting a zero fee or postage cost.
            from types import SimpleNamespace

            fee_values = []
            shipping_min_values = []
            shipping_max_values = []
            for row, price in zip(aggregate_rows_for_metrics, values):
                if price is None:
                    continue
                estimate_listing = SimpleNamespace(
                    sale_price=None,
                    listing_price=price,
                    suggested_price=price,
                    category_id=row.category_id,
                    category_suggestion=row.category_suggestion,
                    detected_category_guess=row.detected_category_guess,
                    title=row.title,
                    shipping_profile=row.shipping_profile,
                    marketplace_data=row.marketplace_data,
                )
                try:
                    fee = float(calculate_expected_ebay_fee(estimate_listing, seller_config)["totalExpectedFee"])
                    quote = ProfitService().estimate_shipping_quote(estimate_listing)
                    shipping_min = float(quote["min"])
                    shipping_max = float(quote["max"])
                    if fee < 0 or shipping_min < 0 or shipping_max < shipping_min:
                        raise ValueError("invalid financial estimate")
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    continue
                fee_values.append(fee)
                shipping_min_values.append(shipping_min)
                shipping_max_values.append(shipping_max)
            complete_estimates = len(fee_values) == len(aggregate_rows_for_metrics) == len(valid_values)
            fees = round(sum(fee_values), 2) if complete_estimates else None
            shipping_min = round(sum(shipping_min_values), 2) if complete_estimates else None
            shipping_max = round(sum(shipping_max_values), 2) if complete_estimates else None
            aggregate_summary.update({
                "shipping_min_total": shipping_min,
                "shipping_max_total": shipping_max,
                "expected_sold_fee_min_total": fees,
                "expected_sold_fee_max_total": fees,
                "net_before_shipping_total": round(aggregate_total - fees, 2) if fees is not None else None,
                "net_after_shipping_min_total": round(aggregate_total - fees - shipping_max, 2) if fees is not None and shipping_max is not None else None,
                "net_after_shipping_max_total": round(aggregate_total - fees - shipping_min, 2) if fees is not None and shipping_min is not None else None,
            })
    else:
        aggregate_summary = _aggregate_sql(
            aggregate_filters,
            sold_view="sold" in queue_values,
            published_view="published" in queue_values,
        )
    serializer = _serialize_listing_summary if summary_only else _serialize_listing_response
    serialized_items = [serializer(listing, seller_config=seller_config) if not summary_only else serializer(listing) for listing in rows]
    if remote_ebay_active_ids is not None:
        for listing, serialized in zip(rows, serialized_items):
            serialized["queue_bucket"] = _listing_bucket(listing, remote_ebay_active_ids)
    return {
        "items": serialized_items,
        "total": total,
        "page": resolved_page,
        "page_size": resolved_page_size,
        "total_pages": max(1, (total + resolved_page_size - 1) // resolved_page_size),
        "bucket_counts": bucket_counts,
        "aggregate_value": aggregate_summary["aggregate_value"],
        "average_value": aggregate_summary["average_value"],
        "priced_count": aggregate_summary.get("priced_count", total),
        "price_source_counts": aggregate_summary.get("price_source_counts"),
        "missing_price_count": aggregate_summary["missing_price_count"],
        "shipping_min_total": aggregate_summary["shipping_min_total"],
        "shipping_max_total": aggregate_summary["shipping_max_total"],
        "listing_fee_total": aggregate_summary["listing_fee_total"],
        "sold_item_fee_total": aggregate_summary["sold_item_fee_total"],
        "expected_sold_fee_min_total": aggregate_summary["expected_sold_fee_min_total"],
        "expected_sold_fee_max_total": aggregate_summary["expected_sold_fee_max_total"],
        "net_before_shipping_total": aggregate_summary["net_before_shipping_total"],
        "net_after_shipping_min_total": aggregate_summary["net_after_shipping_min_total"],
        "net_after_shipping_max_total": aggregate_summary["net_after_shipping_max_total"],
    }


# Backward-compatible alias for older browser bundles and integrations.  The
# canonical catalog endpoint is /listings; keeping this alias prevents a stale
# tab from rendering an empty catalog while it refreshes to the current bundle.
router.add_api_route("/listings/paged", get_listings, methods=["GET"], include_in_schema=False)


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

@router.get("/listings/media-retention")
def list_media_retention_queue(
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the tenant-scoped sold-media retention queue.

    This is intentionally read-only.  It lets the operator see what is queued,
    what is due, and what was purged without exposing another tenant's paths or
    allowing an accidental destructive action from the listing page.
    """
    query = select(Listing).where(
        Listing.user_id == current_user.id,
        Listing.sold_at.is_not(None),
        Listing.quantity <= 0,
    ).order_by(Listing.sold_at.asc(), Listing.id.asc()).limit(limit)
    rows = db.scalars(query).all()
    now = datetime.utcnow()
    items = []
    for listing in rows:
        retention = dict((listing.marketplace_data or {}).get("media_retention") or {})
        state = str(retention.get("status") or "not_queued")
        if status and state != str(status).strip():
            continue
        purge_after = retention.get("purge_after")
        items.append({
            "listing_id": listing.id,
            "title": listing.title,
            "sold_at": listing.sold_at.isoformat() if listing.sold_at else None,
            "status": state,
            "queued_at": retention.get("queued_at"),
            "purge_after": purge_after,
            "purge_due": bool(purge_after and str(purge_after) <= now.isoformat()),
            "purged_at": retention.get("purged_at"),
            "purge_mode": retention.get("purge_mode"),
            "image_count": len(listing.image_urls or []),
            "remote_identities": {
                "ebay": listing.ebay_listing_id,
                "marketplaces": [
                    {"marketplace": str(row.marketplace), "remote_id": row.marketplace_listing_id, "status": str(row.status)}
                    for row in (listing.marketplace_listings or [])
                ],
            },
        })
    return {"items": items, "total": len(items), "as_of": now.isoformat()}


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
        canonical_description=payload.canonical_description or payload.description,
        marketplace_descriptions=payload.marketplace_descriptions or {},
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
    before = {field: getattr(listing, field, None) for field in ("title", "description", "canonical_description", "marketplace_descriptions", "listing_price", "suggested_price", "quantity", "condition", "category_id", "category_suggestion", "item_specifics", "image_urls", "marketplace_data", "platform_quantities", "custom_labels")}
    direct_updates = payload.model_dump(
        exclude_none=True,
        exclude={"quantity", "platform_quantities", "custom_labels", "marketplace_data", "marketplace_descriptions", "target_marketplaces"},
    )
    if "description" in direct_updates and "canonical_description" not in direct_updates:
        direct_updates["canonical_description"] = direct_updates["description"]
    if "status" in direct_updates:
        direct_updates["status"] = ListingStatus(direct_updates["status"])
    for key, value in direct_updates.items():
        setattr(listing, key, value)
    if payload.marketplace_descriptions is not None:
        variants = dict(listing.marketplace_descriptions or {}) if isinstance(listing.marketplace_descriptions, dict) else {}
        provenance = dict(variants.get("_provenance") or {}) if isinstance(variants.get("_provenance"), dict) else {}
        for market, text in payload.marketplace_descriptions.items():
            market_key = str(market).strip().lower()
            if market_key in {"ebay", "facebook", "mercari", "poshmark", "vinted", "etsy", "offerup"} and str(text or "").strip():
                variants[market_key] = str(text).strip()
                provenance[market_key] = "operator_edited"
        listing.marketplace_descriptions = variants
        marketplace_data = dict(listing.marketplace_data or {})
        marketplace_data["marketplace_description_provenance"] = {
            **(marketplace_data.get("marketplace_description_provenance") or {}),
            **provenance,
        }
        listing.marketplace_data = marketplace_data
    # Any material listing edit invalidates cached marketplace readiness. A
    # stale preflight was allowing corrected drafts to keep failing (or hiding
    # the new blocker set) until an unrelated refresh happened.
    if set(direct_updates) & {"title", "description", "category_id", "category_suggestion", "item_specifics", "condition", "listing_price", "image_urls", "quantity"}:
        marketplace_data = dict(listing.marketplace_data or {})
        marketplace_data.pop("marketplace_preflight", None)
        listing.marketplace_data = marketplace_data
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


@router.post("/listings/{listing_id}/refresh-readiness")
def refresh_listing_readiness(
    listing_id: int,
    marketplace: str = Query("ebay"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-evaluate the shared publishability contract after an operator repair."""
    market = str(marketplace or "").strip().lower()
    if market not in {"ebay", "facebook", "mercari", "etsy", "poshmark", "depop", "vinted", "offerup"}:
        raise HTTPException(status_code=400, detail="Unsupported marketplace")
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)

    evaluation = MarketplacePreflightService().evaluate_publishability(db, listing, market, refresh=True)
    canonical = evaluation.get("canonical") if isinstance(evaluation.get("canonical"), dict) else canonical_listing_readiness(listing, marketplace=market)
    publishable = bool(evaluation.get("publishable") and canonical.get("publishable"))
    blockers = list(evaluation.get("blockers") or [])
    if publishable:
        listing.needs_review = True
        listing.processing_state = "complete"
        listing.processing_stage = "completed"
        listing.processing_blocking_reason = None
        listing.processing_error_stage = None
        listing.readiness_summary = dict(canonical)
    else:
        listing.needs_review = False
        reason = next((str(item.get("message") or item.get("user_message") or item.get("code") or "").strip() for item in blockers if isinstance(item, dict) and (item.get("message") or item.get("user_message") or item.get("code"))), None)
        if not reason:
            reason = next((str(value).strip() for value in (canonical.get("blocking_reasons") or []) if str(value).strip()), None)
        if reason:
            listing.processing_blocking_reason = reason
        listing.readiness_summary = dict(canonical)
    db.add(listing)
    db.commit()
    return {
        "listing_id": listing.id,
        "marketplace": market,
        "publishable": publishable,
        "queue": "needs_review" if publishable else "needs_attention",
        "preflight_status": (evaluation.get("preflight") or {}).get("status"),
        "blockers": blockers,
        "warnings": list(evaluation.get("warnings") or []),
        "canonical": canonical,
    }


@router.patch("/listings/{listing_id}/marketplace-descriptions/{marketplace}", response_model=ListingResponse)
def update_marketplace_description(
    listing_id: int,
    marketplace: str,
    payload: MarketplaceDescriptionUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit one destination description without changing canonical copy.

    Explicit operator edits are provenance-marked so later enrichment can
    regenerate only generated variants and leave human copy untouched.
    """
    market = str(marketplace or "").strip().lower()
    if market not in {"ebay", "facebook", "mercari", "poshmark", "vinted", "etsy", "offerup"}:
        raise HTTPException(status_code=422, detail="Unsupported marketplace")
    text = str(payload.description or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Description cannot be empty")
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    variants = dict(listing.marketplace_descriptions or {}) if isinstance(listing.marketplace_descriptions, dict) else {}
    variants[market] = text
    provenance = dict(variants.get("_provenance") or {}) if isinstance(variants.get("_provenance"), dict) else {}
    provenance[market] = "operator_edited" if payload.provenance != "generated" else "generated"
    variants["_provenance"] = provenance
    listing.marketplace_descriptions = variants
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return _serialize_listing_response(listing)


@router.post("/listings/{listing_id}/marketplace-operation", response_model=ListingResponse)
def apply_listing_marketplace_operation(
    listing_id: int,
    payload: MarketplaceOperationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    from app.services.marketplace_mutations import apply_marketplace_operation
    try:
        apply_marketplace_operation(listing, marketplaces=payload.marketplaces, field=payload.field, action=payload.action, value=payload.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return _serialize_listing_response(listing)


@router.post("/listings/{listing_id}/marketplace-operation/preview")
def preview_listing_marketplace_operation(
    listing_id: int,
    payload: MarketplaceOperationPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a non-mutating diff before a marketplace-scoped edit is applied."""
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    from app.services.marketplace_mutations import apply_marketplace_operation_plan
    try:
        result = apply_marketplace_operation_plan(
            [{"listing_ids": [listing_id], "marketplaces": payload.marketplaces, "field": payload.field, "action": payload.action, "value": payload.value}],
            {listing_id: listing},
            preview_only=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"listing_id": listing_id, "preview": True, "changes": result["changes"], "untouched_markets": result.get("untouched_markets", [])}


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
    before = {field: getattr(listing, field, None) for field in ("title", "description", "canonical_description", "marketplace_descriptions", "listing_price", "suggested_price", "quantity", "condition", "category_id", "category_suggestion", "item_specifics", "image_urls", "marketplace_data", "platform_quantities", "custom_labels")}
    direct_updates = payload.model_dump(exclude_none=True, exclude={"quantity", "platform_quantities", "custom_labels", "marketplace_data", "marketplace_descriptions", "target_marketplaces"})
    if "description" in direct_updates and "canonical_description" not in direct_updates:
        direct_updates["canonical_description"] = direct_updates["description"]
    if "status" in direct_updates:
        direct_updates["status"] = ListingStatus(direct_updates["status"])
    for key, value in direct_updates.items():
        setattr(listing, key, value)
    if payload.marketplace_descriptions is not None:
        variants = dict(listing.marketplace_descriptions or {}) if isinstance(listing.marketplace_descriptions, dict) else {}
        provenance = dict(variants.get("_provenance") or {}) if isinstance(variants.get("_provenance"), dict) else {}
        for market, text in payload.marketplace_descriptions.items():
            market_key = str(market).strip().lower()
            if market_key in {"ebay", "facebook", "mercari", "poshmark", "vinted", "etsy", "offerup"} and str(text or "").strip():
                variants[market_key] = str(text).strip()
                provenance[market_key] = "operator_edited"
        listing.marketplace_descriptions = variants
        marketplace_data = dict(listing.marketplace_data or {})
        marketplace_data["marketplace_description_provenance"] = {
            **(marketplace_data.get("marketplace_description_provenance") or {}),
            **provenance,
        }
        listing.marketplace_data = marketplace_data
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
    requested_targets = payload.target_marketplaces
    if requested_targets is None:
        targets = list(active.keys())
    else:
        targets = []
        for raw_market in requested_targets:
            market = str(raw_market or "").strip().lower()
            if not market or market in targets:
                continue
            if market not in active:
                raise HTTPException(status_code=422, detail=f"{market or 'Marketplace'} is not an active remote listing for this item.")
            targets.append(market)
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
    remote_sync = dict(md.get("remote_sync") or {}) if isinstance(md.get("remote_sync"), dict) else {}
    for market in targets:
        prior = dict(remote_sync.get(market) or {}) if isinstance(remote_sync.get(market), dict) else {}
        prior_state = str(prior.get("state") or "").lower()
        remote_sync[market] = {
            **prior,
            # A very fast worker can finish between job enqueue and this
            # response commit. Never overwrite a confirmed/blocked terminal
            # result with a stale queued marker.
            "state": prior_state if prior_state in {"confirmed", "blocked"} else "queued",
            "desired": {field: change.get("after") for field, change in changed_set.items()},
            "remote_id": active.get(market),
            "revision": revision,
        }
    md["remote_sync"] = remote_sync
    listing.marketplace_data = md
    db.commit(); db.refresh(listing)
    return {"listing": _serialize_listing_response(listing), "changed_fields": manual_fields, "jobs": jobs, "sync_state": md["sync_state"]}


@router.post("/listings/approve-and-queue")
def approve_and_queue_listings(
    payload: ListingApproveQueueRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    preflight_service = MarketplacePreflightService()
    targets = [str(value).strip().lower() for value in (payload.marketplaces or ["ebay"]) if str(value).strip()]
    if "ebay" in targets and (not payload.confirm_live_publish or str(payload.confirmation_phrase or "").strip() != "QUEUE LIVE EBAY READY LISTINGS"):
        raise HTTPException(status_code=400, detail="Live eBay queue requires explicit confirmation.")
    selected_ids = list(dict.fromkeys(payload.listing_ids or []))
    if not selected_ids:
        raise HTTPException(status_code=400, detail="At least one listing must be selected.")
    selected_rows = [db.get(Listing, listing_id) for listing_id in selected_ids]
    owned_rows = [row for row in selected_rows if row is not None]
    if any(row.user_id != current_user.id for row in owned_rows):
        raise HTTPException(status_code=403, detail="Bulk publish selection contains a listing outside the current account.")
    batch_id = f"approve-{uuid.uuid4().hex}"
    batch = MarketplacePublishBatch(
        id=batch_id,
        user_id=current_user.id,
        operation="approve_and_queue",
        selected_count=len(selected_ids),
        selected_listing_ids=selected_ids,
        requested_marketplaces=targets,
        status="preflighting",
        summary={"selected": len(selected_ids), "queued": 0, "blocked": 0, "missing": 0, "already_published": 0, "items": []},
    )
    db.add(batch)
    db.flush()
    results = []

    def record_blocked_destination(listing, market, preflight, blockers):
        """Persist a destination outcome even when no worker may be dispatched."""
        job = MarketplaceCrosspostJob(
            user_id=current_user.id,
            listing_id=listing.id,
            source_marketplace="posterpro",
            target_marketplaces=[market],
            requested_mode="operator_approved_live_queue",
            status="blocked",
            priority=0,
            requested_by=current_user.id,
            batch_id=batch_id,
            execution_plan={
                "operator_live_confirmed": True,
                "queued_from": "approve_and_queue",
                "targets": [market],
                "bulk_batch_id": batch_id,
                "selected_listing_ids": selected_ids,
                "preflight": _json_safe(preflight),
                    "preflight_payload_hash": compute_payload_hash(build_marketplace_payload(listing, market)),
                "blockers": _json_safe(blockers),
                "dispatch": "not_dispatched_blocked_preflight",
            },
            result_summary={"results": [{"marketplace": market, "status": "blocked", "response": {"error": _json_safe(blockers)}}]},
            last_error="; ".join(str(item.get("message") or item.get("code") or "Publish blocked") for item in blockers),
        )
        db.add(job)
        db.flush()
        return {
            "status": "blocked",
            "job_id": job.id,
            "marketplace": market,
            "target_marketplaces": [market],
            "error": "; ".join(str(item.get("message") or item.get("code") or "Publish blocked") for item in blockers),
        }
    for listing_id in selected_ids:
        listing = db.get(Listing, listing_id)
        if not listing:
            results.append({"listing_id": listing_id, "status": "missing"})
            batch.summary["missing"] = int(batch.summary.get("missing", 0)) + 1
            continue
        ensure_user_owns_resource(current_user, listing.user_id)
        # Evaluate each explicitly selected destination independently. A
        # connection/session blocker on one marketplace must not prevent a
        # ready destination (for example eBay) from being queued.
        preflight_by_market: dict[str, dict] = {}
        blockers_by_market: dict[str, list[dict]] = {}
        preparation_blockers_by_market: dict[str, list[dict]] = {}
        ready_targets: list[str] = []
        for market in targets:
            if market not in MARKETPLACE_DESTINATION_VALUES:
                blockers_by_market.setdefault(market, []).append({
                    "code": "UNSUPPORTED_MARKETPLACE",
                    "message": f"Unsupported marketplace: {market}",
                    "retryable": False,
                })
                continue
            try:
                evaluation = preflight_service.evaluate_publishability(db, listing, market, refresh=True)
                preflight = evaluation["preflight"]
            except Exception as exc:
                preflight = {"marketplace": market, "status": "blocked", "blockers": [{"code": "PREFLIGHT_ERROR", "message": str(exc), "retryable": True}]}
            preflight_by_market[market] = preflight
            blockers = [item for item in (preflight.get("blockers") or []) if isinstance(item, dict)]
            preparation_blockers = [item for item in (preflight.get("preparation_blockers") or []) if isinstance(item, dict)]
            # Older/error-shaped preflight responses do not include the
            # preparation/execution split. Treat those as destination data
            # blockers unless the response explicitly identifies a connection
            # blocker; never silently mark an errored destination ready.
            if (
                blockers
                and not preparation_blockers
                and not (preflight.get("connection_blockers") or [])
                and str(preflight.get("preparation_status") or "").lower() not in {"ready", "ready_with_warnings", "published"}
            ):
                preparation_blockers = blockers
            if preparation_blockers:
                preparation_blockers_by_market[market] = preparation_blockers
            if blockers:
                blockers_by_market[market] = blockers or [{
                    "code": "PREFLIGHT_NOT_READY",
                    "message": f"{market} preflight is {preflight.get('status') or 'not ready'}.",
                    "retryable": True,
                }]
            preparation_status = str(preflight.get("preparation_status") or preflight.get("status") or "").lower()
            if not preparation_blockers and not blockers and preparation_status in {"ready", "ready_with_warnings", "published"}:
                ready_targets.append(market)

        source_metadata = dict(listing.source_metadata or {})
        source_metadata["approval_attempted_at"] = datetime.utcnow().isoformat()
        source_metadata["operator_approved_by_user_id"] = current_user.id
        if blockers_by_market:
            source_metadata["approval_blockers"] = blockers_by_market
        else:
            source_metadata["operator_approved_at"] = datetime.utcnow().isoformat()
        listing.source_metadata = source_metadata
        md = normalize_marketplace_data(
            {
                **(listing.marketplace_data or {}),
                "targets": targets,
                "crosspost_mode": str((listing.marketplace_data or {}).get("crosspost_mode") or "approval_required"),
                "approval_requested": True,
                "approval_publishable": bool(ready_targets),
                "approval_blockers": blockers_by_market,
                "marketplace_preflight": {
                    **((listing.marketplace_data or {}).get("marketplace_preflight") or {}),
                    "by_marketplace": {
                        **(((listing.marketplace_data or {}).get("marketplace_preflight") or {}).get("by_marketplace") or {}),
                        **preflight_by_market,
                    },
                },
            }
        )
        listing.marketplace_data = md
        if preparation_blockers_by_market and not ready_targets:
            listing.status = ListingStatus.draft
            listing.needs_review = False
            listing.restricted_review_required = True
            listing.restricted_reasons = [
                f"{market}: {issue.get('message') or issue.get('code') or 'Publish blocked'}"
                for market, issues in preparation_blockers_by_market.items()
                for issue in issues
            ]
            listing.processing_state = "needs_attention"
            listing.processing_blocking_reason = "; ".join(listing.restricted_reasons[:3])
            db.add(listing)
            for market in targets:
                record_blocked_destination(listing, market, preflight_by_market.get(market) or {}, blockers_by_market.get(market) or preparation_blockers_by_market.get(market) or [{"code": "PREFLIGHT_NOT_READY", "message": f"{market} preflight did not pass."}])
            db.commit()
            results.append({
                "listing_id": listing.id,
                "status": "blocked",
                "results": [
                    {"marketplace": market, "status": "blocked", "error": blockers_by_market.get(market)}
                    for market in targets
                ],
                "error": "; ".join(listing.restricted_reasons[:5]),
            })
            batch.summary["blocked"] = int(batch.summary.get("blocked", 0)) + 1
            continue

        listing.status = ListingStatus.ready
        listing.needs_review = False
        listing.restricted_review_required = False
        listing.restricted_reasons = []
        db.add(listing)
        db.commit()
        destination_results = []
        for market in ready_targets:
            approved_payload = build_marketplace_payload(listing, market)
            destination_results.append(enqueue_crosspost_job(
                db,
                listing=listing,
                target_markets=[market],
                requested_mode="operator_approved_live_queue",
                execution_plan={
                    "operator_live_confirmed": True,
                    "queued_from": "approve_and_queue",
                    "targets": [market],
                    "bulk_batch_id": batch_id,
                    "selected_listing_ids": selected_ids,
                    # Destination-keyed fields are required even for a
                    # one-destination child job so future job aggregation
                    # cannot accidentally reuse a payload across markets.
                    "preflight_by_marketplace": {market: _json_safe(preflight_by_market.get(market) or {})},
                    "approved_execution_payload_by_marketplace": {market: _json_safe(approved_payload)},
                    "preflight_payload_hash_by_marketplace": {market: compute_payload_hash(approved_payload)},
                },
                batch_id=batch_id,
            ))
        for market in targets:
            if market not in ready_targets:
                destination_results.append(record_blocked_destination(listing, market, preflight_by_market.get(market) or {}, blockers_by_market.get(market) or [{"code": "UNSUPPORTED_MARKETPLACE", "message": f"Unsupported marketplace: {market}"}]))
        result = destination_results[0] if destination_results else {"status": "ready", "target_marketplaces": []}
        destination_by_market = {
            str(item.get("marketplace") or "").lower(): item
            for item in destination_results
            if isinstance(item, dict)
        }
        results.append({
            "listing_id": listing.id,
            "status": result.get("status"),
            "results": [{"marketplace": market, "status": "queued" if market in ready_targets else "blocked", "error": blockers_by_market.get(market), "job_id": (destination_by_market.get(market) or {}).get("job_id")} for market in targets],
            "job_id": result.get("job_id"),
            "task_id": result.get("task_id"),
            "error": result.get("error"),
        })
        if ready_targets:
            batch.summary["queued"] = int(batch.summary.get("queued", 0)) + 1
        else:
            batch.summary["blocked"] = int(batch.summary.get("blocked", 0)) + 1
        batch.summary.setdefault("items", []).append({"listing_id": listing.id, "title": listing.title, "status": result.get("status"), "ready_destinations": ready_targets, "blockers": blockers_by_market})
    batch.status = "partially_queued" if batch.summary.get("queued") and batch.summary.get("blocked") else ("queued" if batch.summary.get("queued") else "blocked")
    batch.summary = dict(batch.summary)
    db.add(batch)
    db.commit()
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
    return _approve_listing_for_user(db, listing=listing, current_user=current_user, requested_marketplaces=["ebay"])


@router.post("/listings/approve-bulk", response_model=BulkListingApproveResponse)
def approve_listings_bulk(
    payload: BulkListingApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    selected_ids = list(dict.fromkeys(payload.listing_ids or []))
    if not selected_ids:
        raise HTTPException(status_code=400, detail="At least one listing must be selected.")
    selected_rows = [db.get(Listing, listing_id) for listing_id in selected_ids]
    if any(row is None for row in selected_rows):
        raise HTTPException(status_code=404, detail="Bulk approval selection contains a missing listing.")
    if any(row.user_id != current_user.id for row in selected_rows if row is not None):
        raise HTTPException(status_code=403, detail="Bulk approval selection contains a listing outside the current account.")
    batch_id = f"approve-{uuid.uuid4().hex}"
    batch = MarketplacePublishBatch(
        id=batch_id,
        user_id=current_user.id,
        operation="approve_bulk",
        selected_count=len(selected_ids),
        selected_listing_ids=selected_ids,
        requested_marketplaces=["ebay"],
        status="preflighting",
        summary={"selected": len(selected_ids), "queued": 0, "blocked": 0, "items": []},
    )
    db.add(batch)
    db.commit()
    approvals: list[dict] = []
    for listing_id in selected_ids:
        listing = db.get(Listing, listing_id)
        if not listing:
            continue
        ensure_user_owns_resource(current_user, listing.user_id)
        approval = _approve_listing_for_user(db, listing=listing, current_user=current_user, requested_marketplaces=["ebay"])
        approvals.append(approval)
        blocked = bool(approval.get("approval_blockers"))
        batch.summary = {
            **(batch.summary or {}),
            "queued": int((batch.summary or {}).get("queued", 0)) + int(bool(approval.get("results"))),
            "blocked": int((batch.summary or {}).get("blocked", 0)) + int(blocked),
            "items": [*(batch.summary or {}).get("items", []), {"listing_id": listing.id, "status": "blocked" if blocked else "approved", "blockers": approval.get("approval_blockers") or {}}],
        }
        db.add(batch)
    summary = batch.summary or {}
    batch.status = "partially_queued" if summary.get("queued") and summary.get("blocked") else ("queued" if summary.get("queued") else "blocked")
    db.add(batch)
    db.commit()
    return {"approvals": approvals, "batch_id": batch_id, "selected_count": len(selected_ids), "summary": batch.summary}


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


@router.post("/listings/unpublish-and-archive")
def unpublish_and_archive_listings(
    payload: UnpublishAndArchiveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """End active remote offers, then archive the exact selected records.

    This is deliberately separate from the local-only archive mutation.  Remote
    END jobs are durable and destination-scoped; a listing is archived only
    after every known active destination is confirmed not-live.
    """
    selected_ids = list(dict.fromkeys(int(value) for value in (payload.listing_ids or [])))
    if not selected_ids:
        raise HTTPException(status_code=400, detail="At least one listing must be selected.")
    rows = [db.get(Listing, listing_id) for listing_id in selected_ids]
    if any(row is None for row in rows):
        raise HTTPException(status_code=404, detail="One or more selected listings were not found.")
    for row in rows:
        ensure_user_owns_resource(current_user, row.user_id)

    requested = [str(value or "").strip().lower() for value in (payload.marketplaces or []) if str(value or "").strip()]
    requested = list(dict.fromkeys(requested))
    unsupported = sorted(set(requested) - set(MARKETPLACE_DESTINATION_VALUES))
    if unsupported:
        raise HTTPException(status_code=422, detail=f"Unsupported marketplace(s): {', '.join(unsupported)}")

    batch_id = f"unpublish-archive-{uuid.uuid4().hex}"
    batch = MarketplacePublishBatch(
        id=batch_id,
        user_id=current_user.id,
        operation="unpublish_and_archive",
        selected_count=len(selected_ids),
        selected_listing_ids=selected_ids,
        requested_marketplaces=requested,
        status="preflighting",
        summary={"selected": len(selected_ids), "queued": 0, "blocked": 0, "archived": 0, "sold": 0, "no_remote_identity": 0, "items": []},
    )
    db.add(batch)
    db.flush()
    results: list[dict] = []

    for listing in rows:
        listing_status = str(listing.status.value if hasattr(listing.status, "value") else listing.status or "").lower()
        if listing.sold_at or listing_status in {"sold", "closed"}:
            results.append({"listing_id": listing.id, "status": "sold", "marketplaces": []})
            batch.summary["sold"] = int(batch.summary.get("sold", 0)) + 1
            continue
        all_marketplace_rows = db.execute(
            select(MarketplaceListing).where(MarketplaceListing.listing_id == listing.id).order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        ).scalars().all()
        active_by_market = {}
        for row in all_marketplace_rows:
            market_key = str(row.marketplace.value if hasattr(row.marketplace, "value") else row.marketplace).lower()
            # Historical sync rows may have lost marketplace_listing_id while
            # retaining the authoritative remote identity in raw_response.
            # Recover that identity before classifying the destination as
            # unknown; absence in one column is not proof of absence remotely.
            recovered_id = str(row.marketplace_listing_id or "").strip()
            raw = row.raw_response if isinstance(row.raw_response, dict) else {}
            remote = raw.get("remote") if isinstance(raw.get("remote"), dict) else {}
            source_ids = remote.get("source_identifiers") if isinstance(remote.get("source_identifiers"), dict) else {}
            for candidate in (
                raw.get("ebay_listing_id"), raw.get("marketplace_listing_id"),
                source_ids.get("ebay_listing_id"), source_ids.get("marketplace_listing_id"),
                (raw.get("remote") or {}).get("ebay_listing_id") if isinstance(raw.get("remote"), dict) else None,
            ):
                if not recovered_id and str(candidate or "").strip():
                    recovered_id = str(candidate).strip()
                    break
            if market_key not in active_by_market and recovered_id:
                active_by_market[market_key] = row
                if not row.marketplace_listing_id:
                    active_by_market[market_key] = {"marketplace_listing_id": recovered_id, "raw_response": raw, "recovered": True}
        # A historical END/CREATE job can retain an identity even when a
        # marketplace row was lost or later compacted. Keep that evidence in
        # the reconciliation universe rather than treating it as absent.
        prior_jobs = db.execute(select(MarketplaceCrosspostJob).where(MarketplaceCrosspostJob.listing_id == listing.id)).scalars().all()
        for prior in prior_jobs:
            plan = prior.execution_plan if isinstance(prior.execution_plan, dict) else {}
            market_key = str((prior.target_marketplaces or [plan.get("marketplace")])[0] or "").lower()
            remote_id = str(plan.get("external_listing_id") or "").strip()
            if market_key and remote_id and market_key not in active_by_market:
                active_by_market[market_key] = {"marketplace_listing_id": remote_id, "raw_response": {"historical_job_id": prior.id}, "historical": True}
        markets = requested or sorted(active_by_market)
        destinations: list[dict] = []
        for market in markets:
            remote_row = active_by_market.get(market)
            remote_id = str((remote_row.get("marketplace_listing_id") if isinstance(remote_row, dict) else getattr(remote_row, "marketplace_listing_id", None)) or "").strip() if remote_row else ""
            if not remote_row or not remote_id:
                unknown_job = MarketplaceCrosspostJob(
                    user_id=current_user.id,
                    listing_id=listing.id,
                    source_marketplace="posterpro",
                    target_marketplaces=[market],
                    requested_mode="unpublish_and_archive",
                    status="blocked",
                    priority=0,
                    requested_by=current_user.id,
                    batch_id=batch_id,
                    execution_plan={"operation": "end", "marketplace": market, "unpublish_and_archive": True, "archive_batch_id": batch_id, "remote_state": "REMOTE_STATE_UNKNOWN", "blocker_code": "REMOTE_STATE_UNKNOWN"},
                    result_summary={"results": [{"marketplace": market, "status": "REMOTE_STATE_UNKNOWN", "response": {"code": "REMOTE_STATE_UNKNOWN", "message": "No durable remote identity was found; reconciliation is required before archive."}}]},
                    last_error="No durable remote identity was found; reconciliation is required before archive.",
                )
                db.add(unknown_job); db.flush()
                destinations.append({"marketplace": market, "status": "remote_state_unknown", "job_id": unknown_job.id})
                batch.summary["blocked"] = int(batch.summary.get("blocked", 0)) + 1
                continue
            if market == "ebay" and not str(listing.ebay_listing_id or "").strip():
                # Preserve a recovered historical identity so the exact END
                # adapter can enforce identity matching without guessing.
                listing.ebay_listing_id = remote_id
                db.add(listing)
            existing = next(
                (
                    prior for prior in prior_jobs
                    if str((prior.execution_plan or {}).get("operation") or "").lower() == "end"
                    and str((prior.execution_plan or {}).get("marketplace") or ((prior.target_marketplaces or [""])[0])).lower() == market
                    and str((prior.execution_plan or {}).get("external_listing_id") or "").strip() == remote_id
                    and prior.id != None
                ),
                None,
            )
            if existing:
                prior_result = ((existing.result_summary or {}).get("results") or [{}])[0]
                prior_status = str(prior_result.get("status") or "").lower()
                if existing.status in {"queued", "running"}:
                    destinations.append({"marketplace": market, "status": existing.status, "job_id": existing.id, "remote_id": remote_id, "reused_from_job_id": existing.id})
                    batch.summary["queued"] = int(batch.summary.get("queued", 0)) + 1
                    continue
                if prior_status in {"ended", "already_ended", "closed", "not_found", "completed", "success"}:
                    reused = MarketplaceCrosspostJob(
                        user_id=current_user.id, listing_id=listing.id, source_marketplace="posterpro", target_marketplaces=[market], requested_mode="unpublish_and_archive", status="completed", priority=0, requested_by=current_user.id, batch_id=batch_id,
                        execution_plan={"operation": "end", "marketplace": market, "external_listing_id": remote_id, "unpublish_and_archive": True, "archive_batch_id": batch_id, "reused_from_job_id": existing.id},
                        result_summary={"results": [{**prior_result, "reused_from_job_id": existing.id}]},
                    )
                    db.add(reused); db.flush()
                    destinations.append({"marketplace": market, "status": "already_not_live", "job_id": reused.id, "remote_id": remote_id, "reused_from_job_id": existing.id})
                    continue
                # A failed END is the one prior outcome that may be retried.
                # Keep the old job as history and link the new attempt below;
                # never duplicate queued/running or confirmed-not-live work.
                if str(existing.status or "").lower() != "failed":
                    destinations.append({"marketplace": market, "status": "remote_state_unknown", "job_id": existing.id, "remote_id": remote_id, "reused_from_job_id": existing.id})
                    continue
            job = MarketplaceCrosspostJob(
                user_id=current_user.id,
                listing_id=listing.id,
                source_marketplace="posterpro",
                target_marketplaces=[market],
                requested_mode="unpublish_and_archive",
                status="queued",
                priority=0,
                requested_by=current_user.id,
                batch_id=batch_id,
                execution_plan={
                    "operation": "end",
                    "marketplace": market,
                    "external_listing_id": remote_id,
                    "unpublish_and_archive": True,
                    "archive_batch_id": batch_id,
                    "initial_remote_state": "LIVE",
                    "retry_of_job_id": existing.id if existing and existing.status == "failed" else None,
                    "remote_url": ((remote_row.get("raw_response") or {}).get("listing_url") if isinstance(remote_row, dict) else ((remote_row.raw_response or {}).get("listing_url") if isinstance(remote_row.raw_response, dict) else None)),
                },
            )
            db.add(job)
            db.flush()
            from app.api.marketplace_jobs import _enqueue_priority
            try:
                task = _enqueue_priority(process_marketplace_crosspost_job_task, job.id, job.priority)
                job.task_id = task.id
                destinations.append({"marketplace": market, "status": "queued", "job_id": job.id, "remote_id": remote_id})
                batch.summary["queued"] = int(batch.summary.get("queued", 0)) + 1
            except Exception as exc:
                job.status = "failed"
                job.last_error = f"Could not dispatch end operation: {exc}"
                destinations.append({"marketplace": market, "status": "failed", "job_id": job.id, "remote_id": remote_id, "error": str(exc)})
                batch.summary["blocked"] = int(batch.summary.get("blocked", 0)) + 1
            db.add(job)
        # Every selected listing must pass through the aggregate finalizer,
        # including listings with no durable remote identity.  Missing identity
        # is an unknown remote state, never proof that it is safe to archive.
        from app.services.marketplace_orchestrator import finalize_unpublish_archive_listing
        final_state = finalize_unpublish_archive_listing(db, batch_id, listing.id)
        results.append({"listing_id": listing.id, "title": listing.title, "status": final_state.get("status", "REMOTE_STATE_UNKNOWN"), "destinations": destinations})
        batch.summary.setdefault("items", []).append({"listing_id": listing.id, "title": listing.title, "destinations": destinations})

    # Recompute from the durable child jobs and per-listing aggregate states so
    # a mixed queued/unknown/failed batch is not flattened to "queued".
    from app.services.marketplace_orchestrator import recompute_publish_batch
    recompute_publish_batch(db, batch_id)
    batch.summary = {**(batch.summary or {}), "selected_count": len(selected_ids)}
    db.add(batch)
    db.commit()
    return {"batch_id": batch_id, "selected": len(selected_ids), "results": results, "summary": batch.summary}


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
def get_autonomous_config(current_user: User = Depends(get_current_user)):
    return {
        "autonomous_mode": settings.autonomous_mode,
        "autonomous_dry_run": settings.autonomous_dry_run,
        "autonomous_crosspost_enabled": settings.autonomous_crosspost_enabled,
    }


@router.post("/config/toggle-autonomous")
def toggle_autonomous_mode(
    payload: AutonomousToggleRequest | None = None,
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin or not is_effective_admin(current_user):
        raise HTTPException(status_code=403, detail="Only an active platform admin can change automation settings")
    if payload and payload.enabled is not None:
        settings.autonomous_mode = payload.enabled
    else:
        settings.autonomous_mode = not settings.autonomous_mode

    if payload and payload.crosspost_enabled is not None:
        settings.autonomous_crosspost_enabled = payload.crosspost_enabled

    from app.api.auth import _write_env_overrides
    _write_env_overrides({
        "AUTONOMOUS_MODE": "true" if settings.autonomous_mode else "false",
        "AUTONOMOUS_CROSSPOST_ENABLED": "true" if settings.autonomous_crosspost_enabled else "false",
    })

    return {
        "autonomous_mode": settings.autonomous_mode,
        "autonomous_dry_run": settings.autonomous_dry_run,
        "autonomous_crosspost_enabled": settings.autonomous_crosspost_enabled,
    }


@router.get("/ai/usage-budget")
def get_ai_usage_budget(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Expose the real UTC complimentary-token governor state to operators."""
    period, start, end = utc_period()
    rows = db.execute(select(AIDailyBudget).where(AIDailyBudget.budget_date == period)).scalars().all()
    by_pool = {row.pool: row for row in rows}
    pools = []
    for name, quota, ceiling in (
        ("large", settings.ai_large_daily_entitlement, settings.ai_large_daily_safe_ceiling),
        ("small", settings.ai_mini_daily_entitlement, settings.ai_mini_daily_safe_ceiling),
    ):
        row = by_pool.get(name) or by_pool.get("mini" if name == "small" else name)
        used = int(row.actual_tokens or 0) if row else 0
        reserved = int(row.reserved_tokens or 0) if row else 0
        waiting = int(db.scalar(select(func.count(AIRequestReservation.id)).where(
            AIRequestReservation.budget_date == period,
            AIRequestReservation.pool.in_([name, "mini"] if name == "small" else [name]),
            AIRequestReservation.state == "WAITING_FOR_DAILY_AI_RESET",
        )) or 0)
        pools.append({
            "pool": name,
            "quota": int(quota),
            "safe_ceiling": int(ceiling),
            "used": used,
            "reserved": reserved,
            "remaining": max(0, int(ceiling) - used - reserved),
            "waiting": waiting,
            "reset_at_utc": end.isoformat(),
        })
    circuit = db.execute(select(AIProviderCircuit).where(AIProviderCircuit.id == 1)).scalar_one_or_none()
    return {
        "period_start_utc": start.isoformat(),
        "period_end_utc": end.isoformat(),
        "pools": pools,
        "paid_openai_spend_allowed": bool(settings.paid_openai_spend_allowed),
        "complimentary_traffic_eligible": bool(settings.openai_complimentary_traffic_eligible),
        "provider_circuit": {
            "state": circuit.state if circuit else "CLOSED",
            "reason": circuit.reason if circuit else None,
            "retry_after": circuit.retry_after.isoformat() if circuit and circuit.retry_after else None,
        },
        "usage_api": {"available": False, "reason": "OpenAI Usage API permission is not configured for this key"},
    }


@router.post("/dashboard/operator-command")
async def run_dashboard_operator_command(
    payload: DashboardOperatorCommandRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Compound requests are returned as a safe, human-readable preview plan;
    # execution remains behind the existing confirmation and scoped mutation
    # services. This prevents a multi-market request from becoming a global
    # update by accident.
    operation_plan, unsupported_clauses = operator_command_service.parse_operation_plan_with_diagnostics(payload.prompt)
    if unsupported_clauses and operation_plan:
        # Never execute a partial interpretation of a compound request.  The
        # user gets the understood operations and the exact clauses requiring
        # clarification, with no side effects or misleading preview.
        return {
            "prompt": payload.prompt,
            "parsed": False,
            "command_type": "compound_operation_plan",
            "dry_run": True,
            "apply_live": False,
            "requires_confirmation": False,
            "message": "PosterPro understood only part of that request. Clarify the highlighted clause(s) before applying changes.",
            "unsupported_clauses": unsupported_clauses,
            "operations": [
                {"items": operation.items, "marketplaces": operation.marketplaces, "field": operation.field, "action": operation.action, "value": operation.value}
                for operation in operation_plan
            ],
            "changes": [],
        }
    if operation_plan:
        from app.services.marketplace_mutations import apply_marketplace_operation_plan
        preview_ids = sorted({listing_id for operation in operation_plan for listing_id in operation.items})
        preview_listings = {listing_id: db.get(Listing, listing_id) for listing_id in preview_ids}
        if any(not listing or listing.user_id != current_user.id for listing in preview_listings.values()):
            raise HTTPException(status_code=404, detail="One or more listings were not found in this account.")
        preview_payload = [{"listing_ids": operation.items, "marketplaces": operation.marketplaces, "field": operation.field, "action": operation.action, "value": operation.value} for operation in operation_plan]
        try:
            preview_result = apply_marketplace_operation_plan(preview_payload, preview_listings, preview_only=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        serialized_operations = [
            {
                "items": operation.items,
                "marketplaces": operation.marketplaces,
                "field": operation.field,
                "action": operation.action,
                "value": operation.value,
                "untouched_markets": sorted(SUPPORTED_MARKETS - set(operation.marketplaces)) if "all" not in operation.marketplaces else [],
            }
            for operation in operation_plan
        ]
        if payload.apply_live and not payload.dry_run:
            if not payload.confirm_live_apply or str(payload.confirmation_phrase or '').strip() != 'APPLY COMPOUND OPERATIONS':
                return {
                    "prompt": payload.prompt, "parsed": True, "command_type": "compound_operation_plan",
                    "dry_run": True, "apply_live": False, "requires_confirmation": True,
                    "confirmation_phrase": "APPLY COMPOUND OPERATIONS",
                    "message": "Live compound changes require the exact confirmation phrase.",
                    "operations": serialized_operations,
                    "changes": preview_result["changes"],
                }
            end_operations = [operation for operation in operation_plan if operation.action == 'end' or operation.field == 'listing']
            regular_operations = [operation for operation in operation_plan if operation not in end_operations]
            # Validate every non-end mutation before creating any durable end
            # jobs.  End jobs are dispatched asynchronously, so allowing a
            # later malformed field operation to fail would otherwise leave a
            # compound request partially applied despite the preview having
            # shown one coherent plan.
            if regular_operations:
                regular_listing_ids = sorted({listing_id for operation in regular_operations for listing_id in operation.items})
                regular_listings = {listing_id: db.get(Listing, listing_id) for listing_id in regular_listing_ids}
                regular_payload = [
                    {"listing_ids": operation.items, "marketplaces": operation.marketplaces, "field": operation.field, "action": operation.action, "value": operation.value}
                    for operation in regular_operations
                ]
                try:
                    apply_marketplace_operation_plan(regular_payload, regular_listings, preview_only=True)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            if end_operations:
                # End operations are queued as durable, destination-scoped
                # jobs.  The worker performs exact remote-identity checks and
                # uses the direct API or browser transport as appropriate.
                results = []
                for operation in end_operations:
                    for listing_id in operation.items:
                        listing = db.get(Listing, listing_id)
                        active_rows = db.execute(select(MarketplaceListing).where(
                            MarketplaceListing.listing_id == listing_id,
                            MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
                        )).scalars().all()
                        active = {str(row.marketplace.value if hasattr(row.marketplace, 'value') else row.marketplace).lower(): row for row in active_rows if row.marketplace_listing_id}
                        for market in operation.marketplaces:
                            market_key = str(market).strip().lower()
                            row = active.get(market_key)
                            if not row:
                                results.append({"listing_id": listing_id, "marketplace": market_key, "status": "not_queued", "message": "No active remote listing identity was found."})
                                continue
                            pending = db.execute(select(MarketplaceCrosspostJob).where(
                                MarketplaceCrosspostJob.user_id == current_user.id,
                                MarketplaceCrosspostJob.listing_id == listing_id,
                                MarketplaceCrosspostJob.status.in_(["queued", "running"]),
                            ).order_by(MarketplaceCrosspostJob.id.desc())).scalars().all()
                            existing = next((job for job in pending if market_key in (job.target_marketplaces or []) and str((job.execution_plan or {}).get("operation") or "").lower() == "end"), None)
                            if existing:
                                results.append({"listing_id": listing_id, "marketplace": market_key, "status": "already_queued", "job_id": existing.id})
                                continue
                            job = MarketplaceCrosspostJob(
                                user_id=current_user.id, listing_id=listing_id, source_marketplace="posterpro",
                                target_marketplaces=[market_key], requested_mode="operator_end", status="queued", priority=0,
                                requested_by=current_user.id,
                                execution_plan={"operation": "end", "external_listing_id": str(row.marketplace_listing_id), "reason": "operator_compound_plan"},
                            )
                            db.add(job); db.flush()
                            try:
                                task = _enqueue_priority(process_marketplace_crosspost_job_task, job.id, 0)
                                job.task_id = task.id
                            except Exception as exc:
                                results.append({"listing_id": listing_id, "marketplace": market_key, "status": "queued", "job_id": job.id, "dispatch_error": type(exc).__name__})
                            else:
                                results.append({"listing_id": listing_id, "marketplace": market_key, "status": "queued", "job_id": job.id, "task_id": task.id})
                db.commit()
                # A compound request may include both destination end actions
                # and ordinary field edits. Apply the latter through the same
                # scoped mutation service instead of silently dropping them.
                if regular_operations:
                    listing_ids = sorted({listing_id for operation in regular_operations for listing_id in operation.items})
                    listings = {listing_id: db.get(Listing, listing_id) for listing_id in listing_ids}
                    plan_payload = [{"listing_ids": operation.items, "marketplaces": operation.marketplaces, "field": operation.field, "action": operation.action, "value": operation.value} for operation in regular_operations]
                    try:
                        applied = apply_marketplace_operation_plan(plan_payload, listings)
                        db.add_all(listings.values())
                        update_results = _queue_agent_marketplace_updates(db, user=current_user, changes=applied["changes"])
                        db.commit()
                        results.extend({"listing_id": change["listing_id"], "status": "updated", "change": change} for change in applied["changes"])
                        results.extend(update_results)
                    except ValueError as exc:
                        db.rollback()
                        raise HTTPException(status_code=422, detail=str(exc)) from exc
            else:
                listing_ids = sorted({listing_id for operation in operation_plan for listing_id in operation.items})
                listings = {listing_id: db.get(Listing, listing_id) for listing_id in listing_ids}
                if any(not listing or listing.user_id != current_user.id for listing in listings.values()):
                    raise HTTPException(status_code=404, detail="One or more listings were not found in this account.")
                plan_payload = [{"listing_ids": operation.items, "marketplaces": operation.marketplaces, "field": operation.field, "action": operation.action, "value": operation.value} for operation in operation_plan]
                try:
                    applied = apply_marketplace_operation_plan(plan_payload, listings)
                    db.add_all(listings.values())
                    update_results = _queue_agent_marketplace_updates(db, user=current_user, changes=applied["changes"])
                    db.commit()
                    results = [{"listing_id": change["listing_id"], "status": "updated", "change": change} for change in applied["changes"]]
                    results.extend(update_results)
                except ValueError as exc:
                    db.rollback()
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {"prompt": payload.prompt, "parsed": True, "command_type": "compound_operation_plan", "dry_run": False, "apply_live": True, "requires_confirmation": True, "message": "Compound operation completed with per-item results.", "operations": serialized_operations, "results": results}
        return {
            "prompt": payload.prompt,
            "parsed": True,
            "command_type": "compound_operation_plan",
            "dry_run": True,
            "apply_live": False,
            "requires_confirmation": True,
            "message": "Preview ready. Confirm each targeted field and marketplace before execution.",
            "operations": serialized_operations,
            "changes": preview_result["changes"],
        }
    return await operator_command_service.handle_prompt(
        db,
        user=current_user,
        prompt=payload.prompt,
        dry_run=payload.dry_run,
        apply_live=payload.apply_live,
        confirm_live_apply=payload.confirm_live_apply,
        confirmation_phrase=payload.confirmation_phrase,
    )


@router.get("/dashboard/operator-tools")
def dashboard_operator_tools(current_user: User = Depends(get_current_user)):
    """Expose the structured operator capability contract to the Dashboard.

    This is intentionally metadata-only.  It gives the UI and diagnostics one
    truthful registry while all mutations remain behind the normal scoped
    services and confirmation/job guards.
    """
    return {"tools": get_operator_tool_registry()}




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
    overlapping = []
    for old in pending:
        old_fields = set(old.fields or []); new_fields = set(payload.fields or [])
        if old_fields & new_fields:
            old.status = "superseded"; overlapping.append(old)
    correction = ListingCorrectionJob(user_id=current_user.id, listing_id=listing.id, requested_by=current_user.id, priority=priority, fields=list(dict.fromkeys(payload.fields or [])), operator_note=(payload.note or "").strip() or None, before_snapshot={"title": listing.title, "description": listing.description, "category_id": listing.category_id, "category_suggestion": listing.category_suggestion, "item_specifics": listing.item_specifics, "listing_price": listing.listing_price, "condition": listing.condition, "image_urls": listing.image_urls})
    db.add(correction)
    db.flush()
    for old in overlapping:
        old.superseded_by = correction.id
    listing.source_metadata = metadata
    listing.status = "draft"
    listing.needs_review = False
    # A queued correction is not an approval/review result. Clear stale review
    # flags so it remains in Drafts until the worker records its real outcome.
    listing.restricted_review_required = False
    db.add(listing)
    db.commit()
    # Persistence of the request is authoritative.  A transient broker outage
    # must not turn a successfully-created correction into a misleading 500;
    # the durable worker/beat sweep can claim the queued row later.
    try:
        process_listing_correction_jobs_task.delay(limit=1)
    except Exception as exc:
        metadata = dict(listing.source_metadata or {})
        metadata["correction_enqueue_error"] = str(exc)[:240]
        metadata["correction_enqueue_error_at"] = datetime.utcnow().isoformat()
        listing.source_metadata = metadata
        db.add(listing)
        db.commit()
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
            "title_hint": listing.cluster.title_hint if listing.cluster else listing.title,
            "source_type": listing.source_type,
            "image_count": len(listing.image_urls or []),
            "storage_unit_name": listing.storage_unit_name,
            "existing_specifics": listing.item_specifics or {},
            "existing_condition": listing.condition,
            "custom_labels": listing.custom_labels or [],
            "source_metadata": listing.source_metadata if isinstance(listing.source_metadata, dict) else {},
            "marketplace_targets": (listing.marketplace_data or {}).get("targets", ["ebay", "facebook"]) if isinstance(listing.marketplace_data, dict) else ["ebay", "facebook"],
        },
        db=db,
        user_id=current_user.id,
        listing_id=listing.id,
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

    # Persist destination-specific generated copy without collapsing it back
    # into the canonical master description.  Human-edited variants remain
    # authoritative and are never replaced by a background regeneration.
    generated_variants = generated.get("marketplace_drafts") if isinstance(generated.get("marketplace_drafts"), dict) else {}
    existing_variants = dict(listing.marketplace_descriptions or {}) if isinstance(listing.marketplace_descriptions, dict) else {}
    variant_provenance = dict((listing.marketplace_data or {}).get("marketplace_description_provenance") or {})
    for market, draft in generated_variants.items():
        if not isinstance(draft, dict) or not str(draft.get("description") or "").strip():
            continue
        if is_human_owned_field(source_metadata, f"marketplace_descriptions.{market}") or variant_provenance.get(market) == "operator_edited":
            continue
        existing_variants[str(market).lower()] = str(draft["description"]).strip()
        variant_provenance[str(market).lower()] = "generated"
    if existing_variants:
        listing.marketplace_descriptions = existing_variants
    if variant_provenance:
        marketplace_data["marketplace_description_provenance"] = variant_provenance

    # Explicit generation may refresh machine-owned fields, but it must never
    # overwrite an operator correction.  Human provenance is field-scoped and
    # remains authoritative across later regeneration requests.
    if not is_human_owned_field(source_metadata, "title"):
        listing.title = generated["title"]
    if not is_human_owned_field(source_metadata, "description"):
        listing.description = generated["description"]
        listing.canonical_description = generated["description"]
    if not is_human_owned_field(source_metadata, "category_suggestion"):
        listing.category_suggestion = generated["category_suggestion"]
    if generated.get("condition") and not is_human_owned_field(source_metadata, "condition"):
        listing.condition = generated["condition"]
    if generated.get("item_specifics") and not is_human_owned_field(source_metadata, "item_specifics"):
        listing.item_specifics = generated["item_specifics"]
    if not is_human_owned_field(source_metadata, "tags"):
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
    canonical = canonical_listing_readiness(listing, marketplace="ebay")
    readiness = {
        "needs_review": bool(listing.needs_review or listing.restricted_review_required),
        "missing_information_count": len(intelligence.get("missing_information") or []),
        # Publish controls and queue diagnostics must use the same canonical
        # result.  Previously this endpoint had a separate, weaker predicate
        # that could expose a publishable action while the queue classified a
        # genuine blocker as Needs Attention.
        "ready_for_publish": bool(listing.status == "ready" and not listing.restricted_review_required and canonical.get("publishable")),
        "review_summary": readiness_summary,
        "quality_summary": compute_listing_quality_summary(listing, pricing_analysis=pricing_analysis),
        "canonical": canonical,
    }

    return {
        "listing_id": listing.id,
        "intelligence": intelligence,
        "draft_meta": draft_meta,
        "pricing_analysis": pricing_analysis,
        "readiness": readiness,
    }


@router.post("/listings/{listing_id}/resolve-category")
async def resolve_listing_category(
    listing_id: int,
    marketplace: str = Query("ebay"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resolve one listing against the real marketplace taxonomy.

    This is intentionally bounded to one listing and read-only taxonomy
    lookups. It never invents IDs and leaves the listing unchanged when
    confidence is insufficient.
    """
    listing = db.get(Listing, listing_id)
    if not listing or (listing.user_id != current_user.id and not is_effective_admin(current_user)):
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(marketplace).lower() != "ebay":
        raise HTTPException(status_code=400, detail="Taxonomy resolver currently supports eBay only")
    account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == listing.user_id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        ).limit(1)
    ).scalar_one_or_none()
    if not account or not account.access_token:
        raise HTTPException(status_code=409, detail="Connect eBay before resolving marketplace taxonomy")
    tree_cache_key = "ebay:EBAY_US:category_tree"
    tree_cache = db.execute(select(MarketplaceMetadataCache).where(MarketplaceMetadataCache.marketplace == "ebay", MarketplaceMetadataCache.cache_key == tree_cache_key).order_by(MarketplaceMetadataCache.updated_at.desc()).limit(1)).scalar_one_or_none()
    if tree_cache and isinstance(tree_cache.payload, dict) and tree_cache.payload:
        tree = tree_cache.payload
    else:
        try:
            tree = await get_category_tree(account.access_token)
        except Exception as exc:
            if tree_cache and isinstance(tree_cache.payload, dict):
                tree = tree_cache.payload
            else:
                raise HTTPException(status_code=503, detail=f"eBay category taxonomy is temporarily unavailable: {exc}") from exc
        row = tree_cache or MarketplaceMetadataCache(marketplace="ebay", cache_key=tree_cache_key)
        row.payload = tree
        row.source_version = "ebay_taxonomy_full_tree"
        row.expires_at = datetime.utcnow() + timedelta(days=30)
        db.add(row)
        db.commit()
    source = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    facts = source.get("amazon_product_facts") if isinstance(source.get("amazon_product_facts"), dict) else {}
    result = resolve_taxonomy_leaf(
        tree,
        listing.title,
        listing.category_suggestion,
        facts.get("product_type"),
        facts.get("brand"),
        facts.get("model"),
        facts.get("feature_bullets"),
    )
    if not result:
        return {"resolved": False, "listing_id": listing.id, "reason": "No sufficiently confident taxonomy leaf match"}
    listing.category_id = result["category_id"]
    listing.category_suggestion = result["category_path"]
    db.add(listing)
    db.commit()
    return {"resolved": True, "listing_id": listing.id, **result}
