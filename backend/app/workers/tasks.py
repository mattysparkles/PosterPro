from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import re
import shutil
from datetime import date, datetime, timedelta, UTC
from pathlib import Path
from typing import Any

from celery import chord, group
from sqlalchemy import Text, and_, cast, func, not_, or_, select, update as sql_update, case, text

from app.core.config import reload_settings, settings
from app.core.database import SessionLocal
from app.core.config import settings
from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import (
    BulkJob,
    Cluster,
    Image,
    Listing,
    ListingRevision,
    ListingCorrectionJob,
    MarketplaceAccount,
    MarketplaceCrosspostJob,
    MarketplaceImportJob,
    MarketplaceListing,
    VineImportItem,
    StorageUnitBatch,
    IntakeSlate,
    User,
)
from app.services.listing_workspace import normalize_marketplace_data
from app.services.listing_ai import ListingAIService
from app.services.listing_provenance import is_human_owned_field
from app.services.category_rules import suggest_category_from_text
from app.services.listing_review import derive_condition_data, derive_shipping_profile, normalize_listing_images
from app.services.listing_processing import ListingProcessingService
from app.services.marketplace_execution import has_online_compatible_extension, resolve_execution_mode
from app.services.marketplace_field_mapper import build_marketplace_payload, normalize_import_payload
from app.services.marketplace_preflight import MarketplacePreflightService, compute_payload_hash
from app.services.marketplace_extension_jobs import MarketplaceExtensionJobError, queue_extension_marketplace_action
from app.services.automation_bridge import submit_bridge_job, wait_for_bridge_job, get_bridge_asset, get_bridge_job, AutomationBridgeError
from app.services.secondary_marketplace_execution import execute_secondary_marketplace_path
from app.services.inventory_service import InventorySafetyError, InventoryService
from app.services.storage import LocalStorage
from app.services.analytics_service import AnalyticsService
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.external_pricing_research import refresh_listing_external_pricing
from app.services.photo_enrichment import PhotoEnrichmentService
from app.services.pricing_service import PricingService
from app.services.multi_platform_publisher import get_enabled_platforms, multi_platform_publisher, upsert_marketplace_listing
from app.services.offer_service import OfferService
from app.services.sale_detection_service import SaleDetectionService
from app.services.media_lifecycle import purge_listing_media
from app.services.process_notifications import create_process_notification
from app.services.intake_slate import IntakeSlateService
from app.services.ai_guard import release_waiting_for_reset, recover_stale_reservations
from app.services.vine_import_service import VineImportService, VINE_IMAGE_BACKFILL_CUTOFF
from app.services.canonical_readiness import canonical_listing_readiness
from app.services.ebay_service import EbayIntegrationError, EbaySellerRestrictedError, end_ebay_listing, get_active_ebay_listings, sync_ebay_active_listings, hydrate_ebay_history_media, revise_ebay_listing
from app.services.ebay_taxonomy import resolve_local_category, category_by_id, semantic_guard_flags
from app.workers.celery_app import celery_app
from app.services.clustering import cluster_embeddings
from app.services.rate_limiter import rate_limiter
from scripts.repair_recovery_draft_copy import _needs_category_refresh, _product_listing_description

logger = logging.getLogger(__name__)


def _preserve_existing_description(listing: Listing) -> bool:
    """Never let legacy enrichment replace operator/Codex-owned copy.

    The photo/storage workers predate the durable listing-processing service and
    used to assign enrichment text unconditionally.  That made a restarted
    worker destructive.  Provenance is the authority; a non-empty description
    without an explicit machine-owned marker is also preserved conservatively.
    """
    metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    if is_human_owned_field(metadata, "description"):
        return True
    recovery = metadata.get("recovery") if isinstance(metadata.get("recovery"), dict) else {}
    provenance = str((recovery.get("field_provenance") or {}).get("description") or "").strip().lower()
    if provenance in {"codex", "codex_recovery", "operator", "human", "human_operator", "manual"}:
        return True
    return bool(str(listing.description or "").strip()) and provenance not in {"photo_enrichment", "machine", "ai"}

STALE_IMPORT_JOB_AFTER = timedelta(minutes=20)
# Browser-assisted publication waits are bounded to ten minutes. A running
# publish job is never automatically replayed after its worker disappears:
# the provider may already have accepted CREATE. After this grace period we
# surface it as failed/reconciliation-required instead of risking a duplicate.
# Queued jobs can still be re-dispatched; the worker's atomic queued->running
# claim prevents multiple broker messages from executing the same child.
STALE_CROSSPOST_JOB_AFTER = timedelta(minutes=20)


def _ebay_crosspost_error_payload(error: EbayIntegrationError) -> dict[str, Any]:
    provider_response = getattr(error, "provider_response", None)
    provider_errors = provider_response.get("errors") if isinstance(provider_response, dict) else None
    provider_error = next((row for row in provider_errors or [] if isinstance(row, dict)), {})
    error_id = str(provider_error.get("errorId") or "").strip()
    restricted = isinstance(error, EbaySellerRestrictedError) or error_id == "25019"
    code = "EBAY_SELLER_RESTRICTED" if restricted else (f"EBAY_{error_id}" if error_id else "EBAY_PROVIDER_ERROR")
    endpoint = str(getattr(error, "endpoint", None) or "").strip()
    method = str(getattr(error, "method", None) or "").strip().upper()
    http_status = getattr(error, "http_status", None)
    attempted = bool(getattr(error, "provider_call_attempted", False))
    provider_rejected = bool(provider_response.get("provider_rejected")) or str(provider_response.get("ack") or "").lower() == "failure" if isinstance(provider_response, dict) else False
    # A denied publishOffer call explicitly means no listing was published,
    # even though the preceding inventory item/offer objects may exist.
    remote_outcome = "NOT_PUBLISHED" if (endpoint.endswith("/publish") and http_status and int(http_status) >= 400) or provider_rejected else (
        "REMOTE_STATE_UNKNOWN" if attempted else "NO_PROVIDER_CALL"
    )
    translated = {
        "error_code": code,
        "error": str(error)[:4000],
        "provider_call_attempted": attempted,
        "provider_endpoint": endpoint or None,
        "provider_method": method or None,
        "http_status": http_status,
        "provider_error_id": error_id or None,
        "provider_short_message": str(provider_error.get("message") or "")[:1000] or None,
        "provider_response": provider_response,
        "request_summary": getattr(error, "request_summary", None),
        "remote_outcome": remote_outcome,
        "retryable": False,
    }
    return _json_safe(translated)

sale_detection_service = SaleDetectionService()
inventory_service = InventoryService()


@celery_app.task(name="render_intake_slate_artwork", bind=True, max_retries=3)
def render_intake_slate_artwork_task(self, slate_id: int, user_id: int) -> dict[str, Any]:
    """Render canonical Slate and box-label artwork without blocking timeline edits."""
    with SessionLocal() as db:
        slate = db.execute(
            select(IntakeSlate).where(
                IntakeSlate.id == int(slate_id),
                IntakeSlate.user_id == int(user_id),
            )
        ).scalar_one_or_none()
        if slate is None:
            return {"slate_id": int(slate_id), "status": "missing"}
        metadata = dict(slate.metadata_json or {})
        qr_payload = dict(slate.qr_payload_json or {})
        service = IntakeSlateService()
        try:
            rendered = service.render_slate_preview_asset(
                qr_payload, item_id=slate.item_id, session_id=slate.session_id
            )
            label = service.render_label_preview_asset(
                qr_payload, item_id=slate.item_id, session_id=slate.session_id
            )
            generated_at = datetime.now(UTC).isoformat()
            metadata["rendered_slate"] = {
                "storage_path": rendered.get("storage_path"),
                "local_path": rendered.get("local_path"),
                "generated_from": "canonical_intake_slate_and_qr",
                "generated_at": generated_at,
            }
            metadata["rendered_slate_url"] = rendered.get("storage_path")
            metadata["rendered_slate_status"] = "ready"
            metadata["rendered_label"] = {
                "storage_path": label.get("storage_path"),
                "local_path": label.get("local_path"),
                "generated_from": "canonical_intake_slate_and_qr",
                "generated_at": generated_at,
            }
            metadata["rendered_label_status"] = "ready"
            slate.metadata_json = metadata
            db.add(slate)
            db.commit()
            return {"slate_id": slate.id, "status": "ready"}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("Canonical Slate artwork rendering failed", extra={"slate_id": slate_id})
            if int(self.request.retries or 0) < 3:
                raise self.retry(exc=exc, countdown=min(60, 2 ** (int(self.request.retries or 0) + 1)))
            return {"slate_id": int(slate_id), "status": "failed", "error": str(exc)[:240]}


@celery_app.task(name="build_vine_batch_drafts", bind=True, max_retries=2)
def build_vine_batch_drafts_task(self, batch_id: int, user_id: int) -> dict[str, Any]:
    """Build one Vine batch outside the upload request.

    Amazon page/media enrichment is inherently long-running and may require
    bounded retries. Keeping it in the durable worker prevents an HTTP upload
    request from timing out halfway through a batch and leaving the operator
    with a parsed-but-never-built import.
    """
    db = SessionLocal()
    try:
        from app.models.models import VineImportBatch

        batch = db.execute(
            select(VineImportBatch)
            .where(VineImportBatch.id == int(batch_id))
            .with_for_update()
        ).scalar_one_or_none()
        if batch is None or int(batch.user_id) != int(user_id):
            return {"batch_id": int(batch_id), "status": "missing"}
        stats = dict(batch.stats_json or {})
        current_status = str(stats.get("auto_build_status") or "").lower()
        # Celery delivery is at-least-once, and the upload UI historically
        # queued the same batch a second time. A serialized claim makes both
        # duplicate requests and broker redelivery harmless.
        if current_status in {"processing", "complete"}:
            return {"batch_id": int(batch_id), "status": current_status, "already_claimed": True}
        stats["auto_build_status"] = "processing"
        batch.stats_json = stats
        db.add(batch)
        db.commit()
        result = VineImportService().auto_build_batch_drafts(
            db,
            batch=batch,
            item_ids=None,
            new_only=True,
            include_cancelled=True,
        )
        batch = db.get(VineImportBatch, int(batch_id))
        if batch is not None:
            stats = dict(batch.stats_json or {})
            stats["auto_build_status"] = "complete"
            batch.stats_json = stats
            db.add(batch)
            db.commit()
        return {"batch_id": int(batch_id), "status": "complete", "result": result}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        batch = db.get(VineImportBatch, int(batch_id))
        if batch is not None:
            stats = dict(batch.stats_json or {})
            stats["auto_build_status"] = "failed"
            stats["auto_build_error"] = str(exc)
            batch.stats_json = stats
            db.add(batch)
            db.commit()
        logger.exception("Vine batch draft worker failed", extra={"batch_id": batch_id, "user_id": user_id})
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()

@celery_app.task(name="process_listing_correction_jobs")
def process_listing_correction_jobs_task(limit: int = 10) -> dict[str, Any]:
    """Claim manual corrections in durable priority order and record material deltas."""
    db = SessionLocal(); processed = []
    try:
        for _ in range(max(1, min(limit, 50))):
            q = select(ListingCorrectionJob).where(ListingCorrectionJob.status == "queued").order_by(
                ListingCorrectionJob.priority.asc(),
                case((ListingCorrectionJob.priority == 0, ListingCorrectionJob.created_at), else_=None).desc(),
                case((ListingCorrectionJob.priority != 0, ListingCorrectionJob.created_at), else_=None).asc(),
                case((ListingCorrectionJob.priority == 0, ListingCorrectionJob.id), else_=None).desc(),
                case((ListingCorrectionJob.priority != 0, ListingCorrectionJob.id), else_=None).asc(),
            ).with_for_update(skip_locked=True).limit(1)
            job = db.execute(q).scalars().first()
            if not job: break
            job.status = "processing"; job.claimed_at = datetime.now(UTC); job.started_at = datetime.now(UTC); job.attempt_count = (job.attempt_count or 0) + 1
            listing = db.get(Listing, job.listing_id)
            before = dict(job.before_snapshot or {})
            changed = {}
            if listing and getattr(listing, "user_id", None) == getattr(job, "user_id", None):
                listing.source_metadata = {**dict(listing.source_metadata or {}), "correction_status": "PROCESSING", "correction_job_id": job.id}
                fields = {str(f).lower() for f in (job.fields or [])}
                category_blocked = False
                generated = None
                source_meta = dict(listing.source_metadata or {})
                # Vine drafts persist normalized facts under several historical
                # keys. Feed the actual stored product facts to the capability,
                # never the operator note as customer content.
                evidence = (
                    source_meta.get("amazon_evidence")
                    or source_meta.get("amazon_product_facts")
                    or source_meta.get("amazon_product")
                    or source_meta.get("source_evidence")
                    or {}
                )
                if fields & {"title", "description", "identity", "condition"}:
                    # ListingAI's bounded fallback and prompt builder consume
                    # source facts through source_metadata. Keep the explicit
                    # source_evidence alias for diagnostics, but also provide
                    # the facts under the capability's canonical input key so
                    # Vine corrections cannot silently regenerate generic copy.
                    generation_metadata = evidence
                    if isinstance(evidence, dict) and "recovery" not in evidence:
                        # Vine imports store Amazon facts directly under
                        # amazon_product_facts; expose that shape to the
                        # evidence-aware fallback identity extractor too.
                        generation_metadata = {**evidence, "recovery": {"identity": evidence}}
                    generated = ListingAIService().generate({"title_hint": listing.title, "source_type": listing.source_type, "image_count": len(listing.image_urls or []), "existing_specifics": listing.item_specifics or {}, "existing_condition": listing.condition, "source_metadata": generation_metadata, "source_evidence": evidence, "operator_instruction": job.operator_note}, db=db, user_id=job.user_id, listing_id=listing.id)
                if generated and "title" in fields and generated.get("title"):
                    value = str(generated["title"])[:80]
                    if value != before.get("title"): listing.title = value; changed["title"] = {"before": before.get("title"), "after": value}
                if "description" in fields:
                    # A provider/model may legitimately return the existing
                    # copy (or no copy at all).  Vine corrections must still
                    # make a deterministic, evidence-backed material change;
                    # otherwise the queue reports NO_PROGRESS and the draft is
                    # sent back to review with no explanation.
                    candidate_description = generated.get("description") if generated else None
                    # Vine copy must be rebuilt from the persisted product
                    # record on an explicit description correction, even when
                    # a prior fallback happens to resemble the current text.
                    # Preserve a substantive provider-generated rewrite.  The
                    # deterministic evidence fallback is only for providers
                    # that return no usable copy; overwriting a valid result
                    # made correction jobs appear to do nothing.
                    if not candidate_description and isinstance(evidence, dict):
                        from app.services.listing_ai import build_listing_description
                        candidate_description = build_listing_description(
                            title=str(evidence.get("title") or evidence.get("product_name") or listing.title or "Item"),
                            item_specifics=listing.item_specifics or {},
                            source_label="Amazon/Vine product facts",
                            source_metadata={"recovery": {"identity": evidence}},
                        )
                    from app.services.customer_description import sanitize_customer_description
                    safe, _removed = sanitize_customer_description(candidate_description or "")
                    if safe and safe != before.get("description"):
                        listing.description = safe
                        changed["description"] = {"before": before.get("description"), "after": safe, "validation": "evidence_backed_rewrite"}
                if "condition" in fields and str(listing.source_type or "").lower() == "amazon_vine":
                    # Vine inventory is received as new; operator correction
                    # cannot turn it into an AI-inferred used/open-box state.
                    previous_condition = listing.condition
                    listing.condition = "New"
                    condition_data = dict(listing.condition_data or {})
                    condition_data.update({"condition_bucket": "new_in_box", "new_in_box": True, "open_box": False, "used": False, "condition_source": "VINE_POLICY"})
                    listing.condition_data = condition_data
                    changed["condition"] = {"before": previous_condition, "after": "New", "validation": "VINE_CONDITION_NEW"}
                if "category" in fields:
                    evidence_title = evidence.get("title") or evidence.get("amazon_title") or evidence.get("product_name") if isinstance(evidence, dict) else None
                    evidence_type = evidence.get("product_type") or evidence.get("category") if isinstance(evidence, dict) else None
                    semantic_terms = [
                        evidence_type,
                        listing.category_suggestion,
                        job.operator_note,
                        *((evidence.get("features") or []) if isinstance(evidence, dict) and isinstance(evidence.get("features"), list) else []),
                    ]
                    ranked = resolve_local_category(
                        db,
                        str(evidence_title or listing.title or ""),
                        [str(value) for value in semantic_terms if str(value or "").strip()],
                        limit=10,
                    )
                    ranked = [
                        {
                            **row,
                            "category_id": str(row.get("category_id") or ""),
                            "category_name": row.get("category_name"),
                            "breadcrumb": row.get("full_path"),
                            "taxonomy_tree_id": str(row.get("category_tree_id") or "0"),
                            "marketplace": row.get("marketplace"),
                            "publishable": bool(row.get("leaf")),
                        }
                        for row in ranked
                        if bool(row.get("leaf")) and not semantic_guard_flags(str(listing.title or ""), str(row.get("full_path") or ""))
                    ]
                    current = category_by_id(
                        db,
                        str(listing.category_id or ""),
                        str(source_meta.get("taxonomy_tree_id") or "0"),
                        str(source_meta.get("taxonomy_marketplace") or "EBAY_US"),
                    ) if listing.category_id else None
                    current_valid = bool(current and current.get("leaf") and not semantic_guard_flags(str(listing.title or ""), str(current.get("full_path") or "")))
                    top_score = int((ranked[0] if ranked else {}).get("score") or 0)
                    next_score = int((ranked[1] if len(ranked) > 1 else {}).get("score") or -1)
                    if current_valid:
                        chosen = {
                            "category_id": str(current["category_id"]),
                            "category_name": current["category_name"],
                            "breadcrumb": current["full_path"],
                            "taxonomy_tree_id": str(current["category_tree_id"]),
                            "marketplace": current["marketplace"],
                        }
                        ranked = [chosen, *[row for row in ranked if str(row.get("category_id")) != str(current["category_id"])]]
                    elif ranked and top_score >= 12 and (len(ranked) == 1 or top_score - next_score >= 3):
                        chosen = ranked[0]
                    else:
                        chosen = None
                    if chosen:
                        cid = str(chosen["category_id"])
                        path = chosen.get("breadcrumb") or chosen.get("category_name")
                        listing.category_id = cid
                        listing.category_suggestion = chosen.get("category_name") or listing.category_suggestion
                        category_validation = {
                            "status": "VALID_EBAY_MOTORS_US" if str(chosen.get("taxonomy_tree_id")) == "100" else "VALID_EBAY_US",
                            "marketplace": chosen.get("marketplace") or ("EBAY_MOTORS_US" if str(chosen.get("taxonomy_tree_id")) == "100" else "EBAY_US"),
                            "tree_id": str(chosen.get("taxonomy_tree_id") or "0"),
                            "category_id": cid,
                            "category_name": chosen.get("category_name"),
                            "full_path": path,
                            "leaf": True,
                            "source": "local_synchronized_taxonomy",
                            "checked_at": datetime.now(UTC).isoformat(),
                        }
                        listing.source_metadata = {
                            **source_meta,
                            "category_path": path,
                            "category_name": chosen.get("category_name"),
                            "taxonomy_tree_id": str(chosen.get("taxonomy_tree_id") or "0"),
                            "taxonomy_marketplace": category_validation["marketplace"],
                            "category_validation": category_validation,
                            "leaf_verified": True,
                            "publishable": True,
                            "category_provenance": "LOCAL_TAXONOMY_CORRECTION",
                            "category_candidates": ranked,
                        }
                        changed["category"] = {
                            "before": {"category_id": before.get("category_id"), "path": source_meta.get("category_path")},
                            "after": {"category_id": cid, "path": path},
                            "validation": "local_taxonomy_leaf_verified",
                        }
                    else:
                        category_blocked = True
                        job.result = {
                            **(job.result if isinstance(job.result, dict) else {}),
                            "category_state": "NEEDS_OPERATOR_REVIEW",
                            "category_candidates": ranked[:10],
                            "taxonomy_source": "local_synchronized_trees",
                            "reason": "No local leaf candidate met the semantic score and separation threshold.",
                        }
                job.after_snapshot = {"title": listing.title, "description": listing.description, "category_id": listing.category_id, "category_suggestion": listing.category_suggestion, "item_specifics": listing.item_specifics, "listing_price": listing.listing_price, "condition": listing.condition, "image_urls": listing.image_urls}
                # Do not leave a pre-correction eBay readiness decision cached
                # after the worker has changed any publish-facing field.
                if changed:
                    marketplace_data = dict(listing.marketplace_data or {})
                    marketplace_data.pop("marketplace_preflight", None)
                    listing.marketplace_data = marketplace_data
                after_values = job.after_snapshot or {}
                keymap = {"category": "category_id", "item specifics": "item_specifics", "aspects": "item_specifics", "price": "listing_price", "images": "image_urls"}
                category_state = (job.result or {}).get("category_state")
                field_results = []
                for f in fields:
                    key = keymap.get(f, f); entry = changed.get(f) or {}; unresolved = f == "category" and category_state in {"CATEGORY_FIXED_ASPECTS_MISSING", "CATEGORY_NON_LEAF", "CATEGORY_LOOKUP_FAILED", "NEEDS_OPERATOR_REVIEW"}
                    field_results.append({"field": f, "before": entry.get("before", before.get(key)), "after": entry.get("after", after_values.get(key)), "capability_used": "ListingAI" if f in {"title","description","identity"} else "taxonomy/readiness", "evidence_used": bool(evidence), "validation_before": before.get(f"{key}_validation"), "validation_after": entry.get("validation") or category_state or ("changed" if f in changed else "unchanged"), "material_change": f in changed and not unresolved, "decision": "ASPECT_REQUIRED" if unresolved and category_state == "CATEGORY_FIXED_ASPECTS_MISSING" else ("NEEDS_REVIEW" if unresolved else ("COMPLETE" if f in changed else "NO_PROGRESS"))})
                complete = bool(fields) and fields.issubset(changed.keys()) and not category_blocked
                prior_result = dict(job.result or {}); prior_result.update({"fields_requested": list(fields), "fields_changed": list(changed), "field_results": field_results, "material_change": bool(changed), "decision": "COMPLETE" if complete else ("PARTIAL" if changed else "NO_PROGRESS")}); job.material_delta = changed; job.result = prior_result
                job.status = "completed" if complete else "needs_review"; job.failure_reason = None if complete else (job.failure_reason or "Requested corrections remain unresolved")
                listing.source_metadata = {**dict(listing.source_metadata or {}), "correction_status": "COMPLETE" if complete else "NEEDS REVIEW", "correction_job_id": job.id}
            elif listing:
                # Never let a globally ordered queue cross a tenant boundary.
                job.status = "blocked"; job.failure_reason = "Listing owner does not match correction job tenant"
            else:
                job.status = "blocked"; job.failure_reason = "Listing not found"
            job.completed_at = datetime.now(UTC); db.commit(); processed.append({"job_id": job.id, "status": job.status, "material_change": bool(changed)})
        return {"processed": processed}
    finally: db.close()


@celery_app.task(name="resume_waiting_ai_work")
def resume_waiting_ai_work_task(limit: int = 100) -> dict[str, int]:
    """UTC reset maintenance; release bounded waiting work and stale claims."""
    db = SessionLocal()
    try:
        released = release_waiting_for_reset(db, limit=limit)
        recovered = recover_stale_reservations(db, limit=limit)
        return {"released": released, "recovered": recovered}
    finally:
        db.close()


def _json_safe(value: Any) -> Any:
    """Make external/preflight payloads safe for JSON persistence."""
    return json.loads(json.dumps(value, default=str))


@celery_app.task(name="process_intake_reconciliation_jobs")
def process_intake_reconciliation_jobs_task(user_id: int | None = None, limit: int = 25) -> dict[str, Any]:
    """Durably resume intake reconciliation after monitor or worker interruption."""
    db = SessionLocal()
    try:
        results = IntakeSlateService().process_reconciliation_jobs(
            db,
            user_id=user_id,
            worker_id="celery-intake-reconciliation",
            limit=limit,
        )
        return {"processed": len(results), "results": results}
    finally:
        db.close()


@celery_app.task(name="queue_due_intake_syncs")
def queue_due_intake_syncs_task() -> dict[str, Any]:
    """Lightweight scheduler that only enqueues per-user intake sync work.

    The heavy Google Photos scan and reconciliation run in the existing
    drain_intake_provider_media task.
    """
    db = SessionLocal()
    queued_user_ids: list[int] = []
    try:
        service = IntakeSlateService()
        users = db.execute(select(User).order_by(User.id.asc())).scalars().all()
        now = datetime.now(UTC)
        for user in users:
            settings_payload = service.settings_for_user(user)
            if not settings_payload.get("enabled"):
                continue
            source_url = str(settings_payload.get("album_url") or settings_payload.get("folder_id") or "").strip()
            if not source_url:
                continue
            last_synced = service._parse_datetime(settings_payload.get("last_synced_at"))  # noqa: SLF001
            poll_seconds = max(60, int(settings_payload.get("poll_interval_seconds") or 300))
            if last_synced and (now - last_synced.astimezone(UTC)).total_seconds() < poll_seconds:
                continue
            drain_intake_provider_media_task.apply_async(args=[user.id], countdown=1)
            queued_user_ids.append(int(user.id))
        return {"queued": len(queued_user_ids), "queued_user_ids": queued_user_ids}
    finally:
        db.close()


@celery_app.task(name="reconcile_retroactive_intake_boundaries")
def reconcile_retroactive_intake_boundaries_task(user_id: int | None = None) -> dict[str, Any]:
    """Apply durable timeline-repair boundaries without a browser session.

    The grouping service is idempotent and versioned, so Beat can safely run
    this after a failed rendered-slate upload or a later operator correction.
    Drafts created here enter the normal autonomous inventory queue.
    """
    db = SessionLocal()
    try:
        service = IntakeSlateService()
        query = select(User).order_by(User.id.asc())
        if user_id is not None:
            query = query.where(User.id == int(user_id))
        results: list[dict[str, Any]] = []
        for user in db.execute(query).scalars().all():
            if not service.settings_for_user(user).get("enabled"):
                continue
            result = service.reconcile_pending_retroactive_boundaries(db, user_id=user.id)
            # Include a draft-only convergence pass in durable task output;
            # otherwise a successful post-boundary draft handoff is invisible
            # to operations because there are no newly pending boundaries.
            if result.get("pending") or result.get("drafts_created"):
                results.append({"user_id": user.id, **result})
        return {"users_reconciled": len(results), "results": results}
    finally:
        db.close()


@celery_app.task(name="repair_recent_vine_images", bind=True, max_retries=None)
def repair_recent_vine_images_task(self, user_id: int | None = None, chunk_size: int = 25) -> dict[str, Any]:
    """Keep retrying recent Vine image repair until the backlog is cleared.

    The task is intentionally chunked and re-runnable.  It only targets Vine
    rows ordered on or after the backfill cutoff, so archived older inventory
    is not reprocessed by the automatic loop.
    """
    db = SessionLocal()
    try:
        service = VineImportService()
        target_users: list[int]
        if user_id is not None:
            target_users = [int(user_id)]
        else:
            rows = db.execute(
                select(VineImportItem.user_id).join(
                    Listing,
                    (VineImportItem.listing_id == Listing.id) | (VineImportItem.inventory_item_id == Listing.id),
                ).where(
                    Listing.source_type == "amazon_vine",
                    VineImportItem.order_date.is_not(None),
                    VineImportItem.order_date >= VINE_IMAGE_BACKFILL_CUTOFF,
                ).distinct()
            ).all()
            target_users = [int(row[0]) for row in rows if row and row[0] is not None]

        totals = {
            "updated": 0,
            "removed_unsafe": 0,
            "already_present": 0,
            "missing_asin": 0,
            "no_cache": 0,
            "bridge_refetched": 0,
            "bridge_failed": 0,
            "processed": 0,
            "target_users": target_users,
            "chunk_size": max(1, int(chunk_size or 25)),
            "cutoff": VINE_IMAGE_BACKFILL_CUTOFF.isoformat(),
        }

        total_backlog = 0
        for target_user_id in target_users:
            targets = service.list_recent_vine_listing_ids(
                db,
                user_id=target_user_id,
                since_order_date=VINE_IMAGE_BACKFILL_CUTOFF,
                include_archived=False,
                only_missing_images=True,
                limit=max(1, int(chunk_size or 25)),
            )
            if not targets:
                continue
            result = service.repair_vine_listing_images(
                db,
                user_id=target_user_id,
                listing_ids=targets,
                include_archived=False,
                force_refresh=True,
                use_bridge_session=True,
                only_missing_images=True,
                limit=None,
            )
            for key in ("updated", "removed_unsafe", "already_present", "missing_asin", "no_cache", "bridge_refetched", "bridge_failed", "processed"):
                totals[key] += int(result.get(key) or 0)
            total_backlog += service.count_recent_vine_image_backlog(
                db,
                user_id=target_user_id,
                since_order_date=VINE_IMAGE_BACKFILL_CUTOFF,
                include_archived=False,
            )

        totals["remaining_backlog"] = total_backlog
        return totals
    finally:
        db.close()


@celery_app.task(name="repair_vine_listing_quality")
def repair_vine_listing_quality_task(user_id: int | None = None, chunk_size: int = 50) -> dict[str, Any]:
    """Continuously reconcile every active Vine listing from durable evidence.

    This is deliberately separate from image repair: it regenerates descriptions,
    prices, categories, condition and item specifics for rows that previously
    landed in Attention/Failed, without creating marketplace listings.
    """
    db = SessionLocal()
    try:
        service = VineImportService()
        users = [int(user_id)] if user_id is not None else [int(row[0]) for row in db.execute(select(Listing.user_id).where(Listing.source_type == "amazon_vine").distinct()).all()]
        totals = {"users": users, "updated": 0, "missing_facts": 0}
        for uid in users:
            # Keep the scheduled convergence loop bounded, while still
            # revalidating legacy rows that were promoted to Needs Review by
            # an older worker before the canonical quality gate existed.  A
            # stale review row is eligible once per hour; the refresh updates
            # its timestamp, so clean rows do not churn on every beat tick.
            recheck_before = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
            current_vine_window = select(VineImportItem.id).where(
                or_(VineImportItem.listing_id == Listing.id, VineImportItem.inventory_item_id == Listing.id),
                VineImportItem.order_date.is_not(None),
                VineImportItem.order_date >= date(2026, 8, 25),
            ).exists()
            ids = [
                int(row[0])
                for row in db.execute(
                    select(Listing.id).where(
                        Listing.user_id == uid,
                        Listing.source_type == "amazon_vine",
                        Listing.sold_at.is_(None),
                        Listing.quantity > 0,
                        Listing.status != ListingStatus.archived,
                        not_(cast(Listing.custom_labels, Text).ilike("%archived_vine%")),
                        not_(cast(Listing.custom_labels, Text).ilike("%archived_sold%")),
                        # Revenue recovery is intentionally scoped to the
                        # current Vine inventory window. Historical unarchived
                        # rows must not consume this worker's bounded repair
                        # capacity until the post-2026-08-25 sellable set is
                        # complete.
                        current_vine_window,
                        or_(
                            Listing.processing_state.in_(["needs_attention", "queued", "processing", "failed"]),
                            and_(
                                Listing.processing_state == "retry",
                                or_(Listing.processing_next_retry_at.is_(None), Listing.processing_next_retry_at <= datetime.now(UTC).replace(tzinfo=None)),
                            ),
                            Listing.needs_review.is_(False),
                            and_(
                                Listing.needs_review.is_(True),
                                Listing.processing_state.in_(["complete", "completed", "processed"]),
                                Listing.updated_at < recheck_before,
                                Listing.ebay_listing_id.is_(None),
                                Listing.status != ListingStatus.PUBLISHED,
                            ),
                            # Materialize destination descriptions for older
                            # Vine rows that predate the marketplace variant
                            # column.  This is deterministic and preserves
                            # operator-edited variants inside the mapper.
                            or_(
                                Listing.marketplace_descriptions.is_(None),
                                # The production column is JSONB while the
                                # compatibility ORM model is JSON. Comparing
                                # directly to ``{}`` generates ``jsonb = json``
                                # and aborts the scheduled Vine repair task.
                                cast(Listing.marketplace_descriptions, Text) == "{}",
                            ),
                        ),
                    ).order_by(
                        case((current_vine_window, 0), else_=1),
                        Listing.updated_at.asc(),
                        Listing.id.asc(),
                    ).limit(max(1, int(chunk_size or 50)))
                ).all()
            ]
            for offset in range(0, len(ids), max(1, int(chunk_size or 50))):
                result = service.refresh_vine_listing_metadata(db, user_id=uid, listing_ids=ids[offset:offset + max(1, int(chunk_size or 50))])
                totals["updated"] += int(result.get("updated") or 0)
                totals["missing_facts"] += int(result.get("missing_facts") or 0)
                # Some historical Vine rows have no surviving provenance row
                # (the listing itself is still valid).  Normalize their
                # lifecycle state here so they cannot remain indefinitely in
                # the transient queued bucket after quality reconciliation.
                for listing in db.scalars(select(Listing).where(Listing.id.in_(ids[offset:offset + max(1, int(chunk_size or 50))]), Listing.user_id == uid, Listing.source_type == "amazon_vine")):
                    quantity_source = str((listing.marketplace_data or {}).get("quantity_source") or "").lower() if isinstance(listing.marketplace_data, dict) else ""
                    if int(listing.quantity or 0) <= 0 and quantity_source not in {"manual", "operator"}:
                        listing.quantity = 1
                    # Derive lifecycle from canonical readiness instead of
                    # forcing every refreshed row into review. Live remote
                    # identities are never rewritten by this repair worker.
                    if not _apply_vine_quality_lifecycle(listing):
                        continue
                db.commit()
        return totals
    finally:
        db.close()


@celery_app.task(name="audit_published_vine_quality")
def audit_published_vine_quality_task(user_id: int | None = None, limit: int = 200) -> dict[str, Any]:
    """Audit live Vine copies without erasing remote identity or publishing.

    Published rows need the same quality scrutiny as drafts, but an audit must
    never downgrade a confirmed remote listing or silently rewrite operator
    fields.  It records findings, removes only exact duplicate local image
    references, and schedules provider-backed repair when copy/category work is
    incomplete.  A later destination-scoped update can use the stored findings.
    """
    db = SessionLocal()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        query = select(Listing).where(
            Listing.source_type == "amazon_vine",
            Listing.ebay_listing_id.is_not(None),
            Listing.sold_at.is_(None),
            Listing.quantity > 0,
            Listing.status != ListingStatus.archived,
            not_(func.coalesce(cast(Listing.custom_labels, Text), "").ilike("%archived_vine%")),
            not_(func.coalesce(cast(Listing.custom_labels, Text), "").ilike("%archived_sold%")),
        ).order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, int(limit or 200)))
        if user_id is not None:
            query = query.where(Listing.user_id == int(user_id))
        rows = db.scalars(query).all()
        result = {"audited": 0, "scheduled": 0, "deduped_images": 0, "weak_descriptions": 0, "category_findings": 0, "rows": []}
        for listing in rows:
            result["audited"] += 1
            urls = [str(value).strip() for value in (listing.image_urls or []) if str(value).strip()]
            unique_urls = list(dict.fromkeys(urls))
            image_rows = [value for value in (listing.listing_images or []) if isinstance(value, dict)]
            seen_image_keys: set[str] = set()
            unique_image_rows = []
            duplicate_count = 0
            for image in image_rows:
                key = str(image.get("source_photo_id") or image.get("intake_photo_id") or image.get("storage_path") or image.get("url") or image.get("path") or "").strip()
                if key and key in seen_image_keys:
                    duplicate_count += 1
                    continue
                if key:
                    seen_image_keys.add(key)
                unique_image_rows.append(image)
            if duplicate_count:
                listing.image_urls = unique_urls
                listing.listing_images = unique_image_rows
                result["deduped_images"] += duplicate_count

            description = str(listing.canonical_description or listing.description or "").strip()
            weak_description = len(description.split()) < 35
            noisy_category = not str(listing.category_id or "").strip() or str(listing.category_suggestion or "").lower().startswith(("other > needs category", "amazon >", "details >", "free ", "complete protect", "2-year protection", "30-day refund"))
            findings = []
            if weak_description:
                findings.append("description_rewrite_required")
                result["weak_descriptions"] += 1
            if noisy_category:
                findings.append("category_validation_required")
                result["category_findings"] += 1
            if duplicate_count:
                findings.append("duplicate_local_images_removed")
            source = dict(listing.source_metadata or {})
            audit = dict(source.get("published_quality_audit") or {})
            audit.update({"audited_at": now.isoformat(), "findings": findings, "remote_identity": str(listing.ebay_listing_id), "duplicate_image_count": duplicate_count})
            source["published_quality_audit"] = audit
            listing.source_metadata = source
            if weak_description or noisy_category:
                listing.processing_state = "retry"
                listing.processing_stage = "description_generation" if weak_description else "category_resolution"
                listing.processing_blocking_reason = "; ".join(findings)
                listing.processing_error_stage = "published_quality_audit"
                listing.processing_next_retry_at = now + timedelta(minutes=15)
                listing.needs_review = False
                result["scheduled"] += 1
            db.add(listing)
            result["rows"].append({"listing_id": listing.id, "remote_id": listing.ebay_listing_id, "findings": findings, "scheduled": bool(weak_description or noisy_category)})
        db.commit()
        return result
    finally:
        db.close()


def _prefer_specific_google_category(title: str, ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Break only taxonomy ties settled by explicit product-type evidence."""
    normalized = str(title or "").casefold()
    if "learning thermostat" in normalized:
        exact = [
            row for row in ranked
            if row.get("leaf")
            and "programmable thermostats" in str(row.get("full_path") or row.get("category_name") or "").casefold()
            and "non-programmable" not in str(row.get("full_path") or row.get("category_name") or "").casefold()
        ]
        if len(exact) == 1:
            # The explicit “Learning” product designation rules out the
            # otherwise token-identical Non-programmable Thermostats leaf.
            # Keep unrelated alternatives so the normal confidence threshold
            # still applies.
            chosen = exact[0]
            competitors = [
                row for row in ranked
                if "non-programmable thermostats" not in str(row.get("full_path") or row.get("category_name") or "").casefold()
            ]
            return [chosen, *[row for row in competitors if row is not chosen]]
    if "electric pressure washer" in normalized:
        exact = [
            row for row in ranked
            if row.get("leaf")
            and str(row.get("category_name") or "").casefold() == "electric pressure washers"
        ]
        if len(exact) == 1:
            # Explicit electric product type disambiguates the broad Pressure
            # Washers leaf and diesel/gas sibling leaves without fuzzy guessing.
            chosen = exact[0]
            competitors = [
                row for row in ranked
                if str(row.get("category_name") or "").casefold() not in {
                    "pressure washers", "diesel pressure washers", "gas pressure washers"
                }
            ]
            return [chosen, *[row for row in competitors if row is not chosen]]
    if "electric paint sprayer" in normalized:
        exact = [
            row for row in ranked
            if row.get("leaf")
            and str(row.get("category_name") or "").casefold() == "paint sprayers"
            and "business & industrial" not in str(row.get("full_path") or "").casefold()
        ]
        if len(exact) == 1:
            chosen = exact[0]
            competitors = [
                row for row in ranked
                if "paint sprayers & striping machines" not in str(row.get("category_name") or "").casefold()
            ]
            return [chosen, *[row for row in competitors if row is not chosen]]
    return ranked


def _invalidate_category_preflight(listing: Listing, reason: str = "category_resolution_changed") -> None:
    marketplace_data = dict(listing.marketplace_data or {})
    existing_preflight = marketplace_data.get("marketplace_preflight")
    if not isinstance(existing_preflight, dict):
        return
    stale_preflight = dict(existing_preflight)
    stale_preflight["stale"] = True
    stale_preflight["stale_reason"] = reason
    by_marketplace = stale_preflight.get("by_marketplace")
    if isinstance(by_marketplace, dict):
        stale_preflight["by_marketplace"] = {
            key: ({**value, "stale": True} if isinstance(value, dict) else value)
            for key, value in by_marketplace.items()
        }
    marketplace_data["marketplace_preflight"] = stale_preflight
    listing.marketplace_data = marketplace_data


@celery_app.task(name="repair_google_listing_categories")
def repair_google_listing_categories_task(user_id: int | None = None, limit: int = 10) -> dict[str, Any]:
    """Resolve Google/Slate draft categories from synchronized local eBay trees.

    This scheduled repair is deliberately offline/cache-only. It must not
    probe the shared eBay Taxonomy API once per listing; explicit taxonomy
    synchronization is a separate bounded operator action.
    """
    db = SessionLocal()
    advisory_lock = False
    try:
        # Celery may run several scheduled copies concurrently.  eBay's
        # taxonomy endpoint is account/provider scoped, so separate worker
        # processes must not all probe it after the first 429.  PostgreSQL's
        # session advisory lock gives this task one cross-process lane while
        # leaving the rows themselves untouched and resumable.
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            advisory_lock = bool(db.execute(text("SELECT pg_try_advisory_lock(7265149031)")).scalar())
            if not advisory_lock:
                return {"processed": 0, "selected": 0, "updated": 0, "failures": [], "skipped": "taxonomy_repair_already_running"}
        query = select(Listing).where(
            Listing.source_type == "intake_head_slate",
            or_(
                Listing.category_id.is_(None),
                and_(
                    Listing.category_id == "11808",
                    not_(Listing.title.ilike("%sealer%")),
                    not_(Listing.title.ilike("%sealing machine%")),
                ),
            ),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
            Listing.processing_state != "archived_duplicate",
            Listing.status != ListingStatus.archived,
            or_(Listing.processing_stage.is_(None), Listing.processing_stage != "grouping_review"),
            or_(
                Listing.processing_stage.is_(None),
                Listing.processing_stage != "category_review",
                Listing.processing_error_stage.is_(None),
                Listing.processing_error_stage != "category",
            ),
        ).order_by(Listing.created_at.desc(), Listing.id.desc()).limit(max(100, min(max(1, int(limit or 3)) * 100, 2000)))
        if user_id is not None:
            query = query.where(Listing.user_id == int(user_id))
        candidate_rows = db.execute(query).scalars().all()
        # Keep the lifecycle guard in Python rather than a JSON cast/NOT EXISTS
        # predicate: SQLite and PostgreSQL serialize JSON labels differently,
        # and a false-negative here silently starves the repair queue.
        candidate_ids = [int(row.id) for row in candidate_rows]
        active_remote_ids = set(db.scalars(
            select(MarketplaceListing.listing_id).where(
                MarketplaceListing.listing_id.in_(candidate_ids or [-1]),
                MarketplaceListing.marketplace_listing_id.is_not(None),
                or_(
                    MarketplaceListing.status.is_(None),
                    MarketplaceListing.status.not_in([MarketplaceListingStatus.DELETED, MarketplaceListingStatus.CLOSED]),
                ),
            )
        ).all())
        candidate_rows = [
            row for row in candidate_rows
            if not (set(str(label).casefold() for label in (row.custom_labels or [])) & {"archived_vine", "archived_sold"})
            and int(row.id) not in active_remote_ids
        ]
        now = datetime.now(UTC)

        def category_evidence_hash(listing: Listing) -> str:
            metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
            recovery = metadata.get("recovery") if isinstance(metadata.get("recovery"), dict) else {}
            identity_payload = recovery.get("image_identity_v1") if isinstance(recovery.get("image_identity_v1"), dict) else {}
            identity = identity_payload.get("identity") if isinstance(identity_payload.get("identity"), dict) else identity_payload
            source_facts = metadata.get("source_facts") if isinstance(metadata.get("source_facts"), dict) else {}
            evidence = {
                "title": listing.title,
                "category_suggestion": listing.category_suggestion,
                "specifics": listing.item_specifics if isinstance(listing.item_specifics, dict) else {},
                "identity": identity,
                "source_identity": source_facts.get("identity"),
                "source_category": source_facts.get("category"),
                # Re-run unchanged rows when the deterministic resolver rules
                # are upgraded; otherwise a six-hour suppression window can
                # keep a row stuck with a stale ambiguous result.
                "resolver_version": "local-taxonomy-2026-10-02-v5",
            }
            encoded = json.dumps(evidence, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
            return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

        rows = []
        skipped_recent = 0
        for candidate in candidate_rows:
            metadata = candidate.source_metadata if isinstance(candidate.source_metadata, dict) else {}
            prior_attempt = metadata.get("category_resolution_attempt") if isinstance(metadata.get("category_resolution_attempt"), dict) else {}
            attempted_at = None
            try:
                attempted_at = datetime.fromisoformat(str(prior_attempt.get("attempted_at") or "").replace("Z", "+00:00"))
                if attempted_at.tzinfo is None:
                    attempted_at = attempted_at.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                attempted_at = None
            if (
                prior_attempt.get("evidence_hash") == category_evidence_hash(candidate)
                and attempted_at is not None
                and attempted_at >= now - timedelta(hours=6)
            ):
                skipped_recent += 1
                continue
            rows.append(candidate)
            if len(rows) >= max(1, int(limit or 3)):
                break
        updated = 0
        failures: list[dict[str, Any]] = []
        attempted = 0
        for listing in rows:
            attempted += 1
            evidence_hash = category_evidence_hash(listing)
            try:
                recovery = (listing.source_metadata or {}).get("recovery") if isinstance(listing.source_metadata, dict) else {}
                recovery = recovery if isinstance(recovery, dict) else {}
                identity_payload = recovery.get("image_identity_v1") if isinstance(recovery.get("image_identity_v1"), dict) else {}
                identity = identity_payload.get("identity") if isinstance(identity_payload.get("identity"), dict) else identity_payload
                specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
                generic_category_labels = {"automotive", "automotive parts", "automotive accessories", "electronics", "other"}
                semantic_terms = [
                    listing.category_suggestion if str(listing.category_suggestion or "").strip().casefold() not in generic_category_labels else "",
                    identity.get("product_type") if isinstance(identity, dict) else "",
                    identity.get("category") if isinstance(identity, dict) else "",
                    identity.get("title") if isinstance(identity, dict) else "",
                    identity.get("brand") if isinstance(identity, dict) else "",
                    identity.get("model") if isinstance(identity, dict) else "",
                    identity.get("mpn") if isinstance(identity, dict) else "",
                    specifics.get("Type"), specifics.get("Model"),
                ]
                ranked = resolve_local_category(
                    db,
                    str(listing.title or ""),
                    [str(value) for value in semantic_terms if str(value or "").strip()],
                    limit=10,
                )
                ranked = [row for row in ranked if row.get("leaf") and not semantic_guard_flags(str(listing.title or ""), str(row.get("full_path") or ""))]
                ranked = _prefer_specific_google_category(str(listing.title or ""), ranked)
                top_score = int((ranked[0] if ranked else {}).get("score") or 0)
                next_score = int((ranked[1] if len(ranked) > 1 else {}).get("score") or -1)
                if not ranked or not ranked[0].get("category_id") or top_score < 12 or (len(ranked) > 1 and top_score - next_score < 3):
                    source_metadata = dict(listing.source_metadata or {})
                    source_metadata["category_resolution_attempt"] = {
                        "status": "ambiguous_local_taxonomy_match",
                        "evidence_hash": evidence_hash,
                        "attempted_at": now.isoformat(),
                        "top_score": top_score,
                    }
                    old_category_id = str(listing.category_id or "").strip()
                    if old_category_id == "11808":
                        old_category = category_by_id(db, old_category_id, "0", "EBAY_US") or {}
                        history = list(source_metadata.get("category_history") or [])
                        history.append({
                            "category_id": old_category_id,
                            "category_name": old_category.get("category_name"),
                            "full_path": old_category.get("full_path"),
                            "status": "rejected_semantic_mismatch",
                            "reason": "Generic Automotive fallback resolved to an unrelated local leaf",
                            "recorded_at": datetime.now(UTC).isoformat(),
                        })
                        source_metadata["category_history"] = history[-20:]
                        source_metadata["category_validation"] = {
                            "status": "INVALID_SEMANTIC_MISMATCH",
                            "category_id": old_category_id,
                            "category_name": old_category.get("category_name"),
                            "full_path": old_category.get("full_path"),
                            "checked_at": datetime.now(UTC).isoformat(),
                            "source": "local_synchronized_taxonomy",
                        }
                        listing.category_id = None
                        listing.source_metadata = source_metadata
                        listing.needs_review = False
                        listing.processing_state = "needs_attention"
                        listing.processing_stage = "category_review"
                        listing.processing_error_stage = "category"
                        listing.processing_blocking_reason = (
                            "The previous Automotive fallback points to eBay's Sealers & Sealing Machines leaf; "
                            "the local taxonomy search could not safely identify a matching leaf from this item's evidence."
                        )
                        listing.processing_next_retry_at = None
                        updated += 1
                    listing.source_metadata = source_metadata
                    db.add(listing)
                    failures.append({"listing_id": listing.id, "reason": "ambiguous_local_taxonomy_match", "top_score": top_score, "candidates": ranked[:5]})
                    continue
                chosen = ranked[0]
                cid = str(chosen["category_id"])
                tree_id = str(chosen.get("category_tree_id") or "0")
                marketplace = str(chosen.get("marketplace") or ("EBAY_MOTORS_US" if tree_id == "100" else "EBAY_US"))
                path = str(chosen.get("full_path") or chosen.get("category_name") or "")
                source_metadata = dict(listing.source_metadata or {})
                source_metadata.update({
                    "category_path": path,
                    "category_name": chosen.get("category_name"),
                    "taxonomy_tree_id": tree_id,
                    "taxonomy_marketplace": marketplace,
                    "leaf_verified": True,
                    "publishable": True,
                    "category_provenance": "LOCAL_SYNCHRONIZED_TAXONOMY",
                    "category_candidates": ranked[:10],
                    "category_validation": {
                        "status": "VALID_EBAY_MOTORS_US" if tree_id == "100" else "VALID_EBAY_US",
                        "marketplace": marketplace,
                        "tree_id": tree_id,
                        "tree_version": str(chosen.get("category_tree_version") or ""),
                        "category_id": cid,
                        "category_name": chosen.get("category_name"),
                        "full_path": path,
                        "leaf": True,
                        "source": "local_synchronized_taxonomy",
                        "checked_at": datetime.now(UTC).isoformat(),
                    },
                    "category_resolution_attempt": {
                        "status": "resolved",
                        "evidence_hash": evidence_hash,
                        "attempted_at": now.isoformat(),
                    },
                })
                category_changed = str(listing.category_id or "").strip() != cid
                listing.category_id = cid
                if category_changed:
                    _invalidate_category_preflight(listing)
                listing.category_suggestion = str(chosen.get("category_name") or listing.category_suggestion or "").strip()
                listing.source_metadata = source_metadata
                listing.processing_state = "queued"
                listing.processing_stage = "quality_validation"
                listing.processing_blocking_reason = None
                listing.processing_next_retry_at = None
                listing.needs_review = False
                updated += 1
            except Exception as exc:  # noqa: BLE001
                source_metadata = dict(listing.source_metadata or {})
                source_metadata["category_resolution_attempt"] = {
                    "status": "error",
                    "evidence_hash": evidence_hash,
                    "attempted_at": now.isoformat(),
                    "error": type(exc).__name__,
                }
                listing.source_metadata = source_metadata
                db.add(listing)
                failures.append({"listing_id": listing.id, "reason": type(exc).__name__, "message": str(exc)[:240]})
        # Earlier resolver passes could persist a valid category ID and local
        # validation, then a later quality pass leave only the old generic
        # category blocker on the listing.  Those rows are no longer category
        # candidates (their ID is non-null), so explicitly send only this
        # stale-blocker case back through quality validation.  Never touch
        # archived rows or records with a live remote identity.
        stale_category_rows = db.execute(
            select(Listing).where(
                Listing.source_type == "intake_head_slate",
                Listing.processing_blocking_reason.ilike("%A validated marketplace category ID is required before this Google intake draft can reach review%"),
                Listing.category_id.is_not(None),
                Listing.ebay_listing_id.is_(None),
                Listing.sold_at.is_(None),
                Listing.status != ListingStatus.archived,
            ).order_by(Listing.created_at.desc(), Listing.id.desc()).limit(100)
        ).scalars().all()
        stale_ids = [int(row.id) for row in stale_category_rows]
        stale_remote_ids = set(db.scalars(
            select(MarketplaceListing.listing_id).where(
                MarketplaceListing.listing_id.in_(stale_ids or [-1]),
                MarketplaceListing.marketplace_listing_id.is_not(None),
                or_(
                    MarketplaceListing.status.is_(None),
                    MarketplaceListing.status.not_in([MarketplaceListingStatus.DELETED, MarketplaceListingStatus.CLOSED]),
                ),
            )
        ).all())
        stale_category_requeued = 0
        stale_category_message = "a validated marketplace category id is required before this google intake draft can reach review"
        for listing in stale_category_rows:
            labels = {str(label).casefold() for label in (listing.custom_labels or [])}
            validation = (listing.source_metadata or {}).get("category_validation") if isinstance(listing.source_metadata, dict) else {}
            validation = validation if isinstance(validation, dict) else {}
            locally_valid = (
                str(validation.get("status") or "").lower() in {"valid_local", "valid_ebay_us", "valid_ebay_motors_us"}
                and str(validation.get("category_id") or "").strip() == str(listing.category_id or "").strip()
                and bool(str(validation.get("full_path") or "").strip())
            )
            if (
                not locally_valid
                or int(listing.id) in stale_remote_ids
                or labels.intersection({"archived_vine", "archived_sold"})
            ):
                continue
            remaining = [
                part.strip() for part in str(listing.processing_blocking_reason or "").split(";")
                if part.strip() and stale_category_message not in part.strip().lower()
            ]
            if remaining:
                continue
            _invalidate_category_preflight(listing, "category_resolution_revalidated")
            listing.processing_state = "queued"
            listing.processing_stage = "quality_validation"
            listing.processing_blocking_reason = None
            listing.processing_error_stage = None
            listing.processing_next_retry_at = None
            listing.needs_review = False
            db.add(listing)
            stale_category_requeued += 1
        db.commit()
        return {"processed": attempted, "selected": len(rows), "scanned": len(candidate_rows), "skipped_recent_unchanged": skipped_recent, "updated": updated, "stale_valid_category_requeued": stale_category_requeued, "failures": failures, "taxonomy_source": "local_synchronized_trees"}
    finally:
        if advisory_lock:
            try:
                db.execute(text("SELECT pg_advisory_unlock(7265149031)"))
            except Exception:
                pass
        db.close()


def _apply_vine_quality_lifecycle(listing: Listing) -> bool:
    """Apply canonical queue state to one Vine listing.

    Returns False when a live marketplace identity makes the row immutable for
    this repair pass. Keeping this decision pure and item-scoped prevents one
    bad draft from poisoning the rest of a scheduled batch.
    """
    remote_live = bool(str(listing.ebay_listing_id or '').strip()) or any(
        str(getattr(row, 'marketplace_listing_id', '') or '').strip()
        and str(getattr(getattr(row, 'status', None), 'value', getattr(row, 'status', None)) or '').upper() in {'PUBLISHED', 'UPDATED'}
        for row in (getattr(listing, 'marketplace_listings', None) or [])
    )
    if remote_live:
        # A confirmed remote identity is published state even if an older
        # repair pass left a stale local Attention/Needs Review flag behind.
        # Normalize only the local lifecycle fields; never rewrite the remote
        # identity or destination payload here.
        listing.processing_state = 'complete'
        listing.needs_review = False
        listing.processing_blocking_reason = None
        listing.processing_error_stage = None
        return False
    # Vine restriction findings are operator/policy review, not unresolved
    # machine taxonomy work. Keep them out of Needs Review and do not churn
    # the same item through category retries indefinitely.
    if bool(getattr(listing, "restricted_review_required", False)):
        reasons = getattr(listing, "restricted_reasons", None) or []
        reason_text = ", ".join(str(reason).replace("-", " ") for reason in reasons if str(reason).strip())
        listing.processing_state = "needs_attention"
        listing.needs_review = False
        listing.processing_blocking_reason = (
            f"Marketplace policy review required: {reason_text or 'restricted item'}; "
            "confirm each selected marketplace permits this product before assigning a category or publishing."
        )
        listing.processing_error_stage = "policy_review"
        return True
    transient_reasons = {
        'image_retrying', 'image_enrichment_pending', 'category_lookup_pending',
        'ai_timeout_retryable', 'enrichment_provider_timeout',
    }
    original_processing_blocker = getattr(listing, 'processing_blocking_reason', None)
    if str(original_processing_blocker or '').strip().lower() in transient_reasons:
        # Retryable provider work is not a terminal blocker. Exclude the
        # marker while evaluating the durable listing fields, then restore it
        # only if another genuine blocker is found.
        listing.processing_blocking_reason = None
        if str(getattr(listing, 'processing_state', '') or '').lower() in {'needs_attention', 'processing', 'queued', 'enriching'}:
            listing.processing_state = 'complete'
    readiness = canonical_listing_readiness(listing)
    # Legacy rows may retain only the old aggregate preflight marker after the
    # canonical contract separates destination blockers. Keep worker truth as
    # scheduled retry until a destination-specific preflight has been stored,
    # without reintroducing that marker into canonical product quality.
    if "preflight" in str(original_processing_blocker or "").lower() and not (listing.marketplace_data or {}).get("marketplace_preflight"):
        listing.processing_state = 'retry'
        listing.processing_attempt_count = int(getattr(listing, "processing_attempt_count", 0) or 0) + 1
        listing.processing_next_retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10)
        listing.processing_blocking_reason = None
        listing.processing_error_stage = 'preflight'
        listing.needs_review = False
        return True
    # Needs Review means the selected destination has passed a fresh preflight.
    # Product completeness alone is not enough: otherwise Motors rows (or rows
    # with no preflight result at all) can be promoted into a queue that implies
    # an operator click will publish successfully.
    blockers = [
        str(reason).strip() for reason in (readiness.get('canonical_blocking_reasons') or [])
        if str(reason).strip() and str(reason).strip().lower() not in transient_reasons
    ]
    if blockers:
        blocker_text = " ".join(blockers).lower()
        retryable_markers = (
            "missing image", "no images", "primary image", "category", "taxonomy",
            "type is required", "source prose", "source-page", "description", "ai rewrite",
            "product-specific enrichment", "pricing research", "every selected marketplace",
            "current preflight", "preflight",
        )
        # AI provider availability is a recoverable system condition.  Do not
        # convert it into a human Attention state just because older category
        # or image attempts exhausted the shared counter; the scheduled AI
        # worker must keep retrying until the provider succeeds.
        ai_rewrite_retry = "ai rewrite" in blocker_text
        preflight_retry = any(marker in blocker_text for marker in ("every selected marketplace", "current preflight", "preflight"))
        if any(marker in blocker_text for marker in retryable_markers) and (preflight_retry or ai_rewrite_retry or int(getattr(listing, "processing_attempt_count", 0) or 0) < 4):
            listing.processing_state = 'retry'
            listing.processing_attempt_count = int(getattr(listing, "processing_attempt_count", 0) or 0) + 1
            listing.processing_stage = (
                'image_enrichment' if any(token in blocker_text for token in ('image', 'photo'))
                else 'description_generation' if any(token in blocker_text for token in ('description', 'source prose', 'source-page'))
                else 'category_resolution' if any(token in blocker_text for token in ('category', 'taxonomy', 'type is required'))
                else 'pricing_research' if 'pricing' in blocker_text
                else 'quality_validation'
            )
            attempt = int(getattr(listing, "processing_attempt_count", 0) or 0)
            listing.processing_next_retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
                minutes=min(60, max(5, 2 ** min(attempt, 6)))
            )
            listing.processing_blocking_reason = None
            listing.processing_error_stage = 'quality_validation'
        else:
            listing.processing_state = 'needs_attention'
            listing.processing_blocking_reason = blockers[0]
            listing.processing_error_stage = listing.processing_error_stage or 'quality_validation'
        listing.needs_review = False
    else:
        destination_blockers = [
            str(reason).strip() for reason in (readiness.get('destination_blocking_reasons') or [])
            if str(reason).strip()
        ]
        persisted_capability_blocker = (
            'ebay_motors_trading_api_required' in str(original_processing_blocker or '').lower()
            or 'trading api listing path' in str(original_processing_blocker or '').lower()
        )
        preflight_by_market = ((getattr(listing, 'marketplace_data', None) or {}).get('marketplace_preflight') or {}).get('by_marketplace') or {}
        fresh_destination = preflight_by_market.get('ebay') if isinstance(preflight_by_market, dict) else None
        fresh_destination_blocked = bool(
            isinstance(fresh_destination, dict)
            and not bool(fresh_destination.get('stale'))
            and str(fresh_destination.get('status') or '').lower() not in {'ready', 'ready_with_warnings', 'published'}
            and bool(fresh_destination.get('blockers'))
        )
        if readiness.get('destination_preflight_pending') and not persisted_capability_blocker and not fresh_destination_blocked:
            listing.processing_state = 'retry'
            listing.processing_attempt_count = int(getattr(listing, 'processing_attempt_count', 0) or 0) + 1
            listing.processing_next_retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10)
            listing.processing_blocking_reason = destination_blockers[0] if destination_blockers else 'Destination preflight is pending; run the configured marketplace checks before publishing'
            listing.processing_error_stage = 'preflight'
            listing.needs_review = False
        elif destination_blockers or persisted_capability_blocker or fresh_destination_blocked:
            # A real, current destination blocker is actionable attention, not
            # a retry loop. Keep the exact provider/preflight reason visible.
            listing.processing_state = 'needs_attention'
            raw_destination_blockers = (fresh_destination or {}).get('blockers') if isinstance(fresh_destination, dict) else []
            raw_first = raw_destination_blockers[0] if isinstance(raw_destination_blockers, list) and raw_destination_blockers else {}
            raw_message = str(raw_first.get('message') or raw_first.get('user_message') or raw_first.get('code') or '').strip() if isinstance(raw_first, dict) else str(raw_first or '').strip()
            listing.processing_blocking_reason = destination_blockers[0] if destination_blockers else raw_message or str(original_processing_blocker)
            listing.processing_error_stage = 'preflight'
            listing.needs_review = False
        elif readiness.get('review_required') and readiness.get('publishable') and readiness.get('queue') == 'NEEDS_REVIEW':
            listing.processing_state = 'complete'
            listing.processing_blocking_reason = None
            listing.processing_error_stage = None
            listing.needs_review = True
        else:
            # Fail closed if the readiness contract adds a new pending state;
            # never infer review eligibility merely from an empty blocker list.
            listing.processing_state = 'retry'
            listing.processing_attempt_count = int(getattr(listing, 'processing_attempt_count', 0) or 0) + 1
            listing.processing_next_retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10)
            listing.processing_blocking_reason = 'Destination readiness is not confirmed; rerun publish preflight'
            listing.processing_error_stage = 'preflight'
            listing.needs_review = False
    return True


@celery_app.task(name="resume_incomplete_listings", bind=True, max_retries=None)
def resume_incomplete_listings_task(
    self,
    user_id: int | None = None,
    limit: int = 25,
    dry_run: bool = False,
    source_types: list[str] | None = None,
) -> dict[str, Any]:
    """Resume incomplete listing-level work until the backlog drains."""
    db = SessionLocal()
    try:
        service = ListingProcessingService()
        result = service.resume_backlog(
            db,
            user_id=user_id,
            limit=limit,
            dry_run=dry_run,
            worker_id="celery-listing-backfill",
            source_types={str(value).strip() for value in (source_types or []) if str(value).strip()} or None,
        )
        remaining = int(result.get("queued", 0)) + int(result.get("processing", 0)) + int(result.get("retrying", 0))
        # The Google/Slate recovery queue is explicitly scheduled as a
        # bounded, source-scoped pass.  It must drain without requiring the
        # operator to keep the UI open, even when the broad historical
        # backlog switch remains disabled for unrelated legacy sources.
        # This task is owned by the singleton Celery beat schedule.  Do not
        # recursively enqueue successors here: beat already dispatches the
        # next bounded pass, and self-enqueueing created overlapping copies
        # that exhausted Redis/backend memory and made Jobs requests time out.
        return result
    finally:
        db.close()


@celery_app.task(name="reevaluate_legacy_recovery_attention", bind=True, max_retries=None)
def reevaluate_legacy_recovery_attention_task(
    self,
    user_id: int | None = None,
    limit: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Versioned recovery-blocker upgrade pass owned by the inventory worker.

    One row is reevaluated and, only when current evidence makes it
    machine-resolvable, is marked QUALITY_REPAIR_REQUIRED.  Classification is
    deliberately not authorization for paid enrichment.
    """
    db = SessionLocal()
    try:
        service = ListingProcessingService()
        result = service.reevaluate_legacy_recovery_attention(
            db,
            user_id=user_id,
            limit=max(1, limit),
            worker_id="celery-recovery-legacy-reevaluator",
            dry_run=dry_run,
        )
        result["processed"] = [
            {"listing_id": int(listing_id), "status": "QUALITY_REPAIR_REQUIRED"}
            for listing_id in (result.get("requeued_listing_ids") or [])
        ]
        return result
    finally:
        db.close()


@celery_app.task(name="resume_vine_processing", bind=True, max_retries=None)
def resume_vine_processing_task(
    self,
    user_id: int | None = None,
    limit: int = 25,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Drain current Vine machine work without paid AI or live publishing.

    Vine is deliberately isolated from the broad historical backlog so a noisy
    recovery/import queue cannot starve owned products.  The listing-processing
    service is idempotent and preserves operator-owned fields.
    """
    with SessionLocal() as db:
        return ListingProcessingService().resume_backlog(
            db,
            user_id=user_id,
            limit=max(1, min(int(limit or 25), 100)),
            dry_run=dry_run,
            worker_id="celery-vine-processing",
            source_types={"amazon_vine"},
        )


@celery_app.task(name="resume_image_identification_backlog", bind=True, max_retries=None)
def resume_image_identification_backlog_task(self, user_id: int | None = None, limit: int = 3, dry_run: bool = False) -> dict[str, Any]:
    """Run bounded image-identification recovery for unresolved photographed inventory."""
    db = SessionLocal()
    try:
        service = ListingProcessingService()
        result = service.resume_image_identification_backlog(
            db,
            user_id=user_id,
            limit=limit,
            dry_run=dry_run,
            worker_id="celery-image-identification",
        )
        remaining = int(result.get("queued", 0)) + int(result.get("processing", 0)) + int(result.get("retrying", 0))
        # Beat owns cadence; avoid recursive self-enqueue and duplicate work.
        return result
    finally:
        db.close()


@celery_app.task(name="resume_product_research_backlog", bind=True, max_retries=None)
def resume_product_research_backlog_task(self, user_id: int | None = None, limit: int = 8, dry_run: bool = False) -> dict[str, Any]:
    """Run bounded research for weak but evidence-rich review items."""
    db = SessionLocal()
    try:
        service = ListingProcessingService()
        result = service.resume_product_research_backlog(
            db,
            user_id=user_id,
            limit=limit,
            dry_run=dry_run,
            worker_id="celery-product-research",
        )
        remaining = int(result.get("queued", 0)) + int(result.get("processing", 0)) + int(result.get("retrying", 0))
        # Beat owns cadence; avoid recursive self-enqueue and duplicate work.
        return result
    finally:
        db.close()


@celery_app.task(name="research_google_intake_pricing", bind=True, max_retries=None)
def research_google_intake_pricing_task(self, limit: int = 5, dry_run: bool = False) -> dict[str, Any]:
    """Attach bounded, read-only eBay asking evidence to timeline drafts.

    This task deliberately does not write a suggested/listing price or promote
    a draft. Browse results are asking prices, not sold-market evidence.
    """
    db = SessionLocal()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        candidates = db.execute(
            select(Listing).where(
                Listing.source_type == "intake_head_slate",
                Listing.status == ListingStatus.draft,
                Listing.needs_review.is_(False),
                Listing.sold_at.is_(None),
            ).order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(100, min(int(limit), 10) * 20))
        ).scalars().all()
        selected: list[Listing] = []
        for listing in candidates:
            data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
            pricing = data.get("pricing_analysis") if isinstance(data.get("pricing_analysis"), dict) else {}
            research_at = pricing.get("external_research_date")
            try:
                prior = datetime.fromisoformat(str(research_at).replace("Z", "+00:00")) if research_at else None
                prior_naive = prior.replace(tzinfo=None) if prior and prior.tzinfo else prior
            except (TypeError, ValueError):
                prior_naive = None
            status = str(pricing.get("external_research_status") or "")
            retry_after = timedelta(days=1) if status == "insufficient_identity" else timedelta(hours=6)
            if prior_naive and now - prior_naive < retry_after:
                continue
            selected.append(listing)
            if len(selected) >= max(1, min(int(limit), 10)):
                break
        if dry_run:
            return {"selected": len(selected), "listing_ids": [row.id for row in selected], "dry_run": True}

        result = {"selected": len(selected), "researched": 0, "blocked": 0, "failed": 0, "items": []}
        for listing in selected:
            try:
                analysis = asyncio.run(refresh_listing_external_pricing(db, listing))
                status = str(analysis.get("external_research_status") or "unknown")
                result["researched" if status == "results" else "blocked"] += 1
                result["items"].append({
                    "listing_id": listing.id,
                    "status": status,
                    "active_asking_count": int(analysis.get("external_active_asking_count") or 0),
                    "sold_comparable_count": int(analysis.get("external_sold_comparable_count") or 0),
                    "recommendation_eligible": analysis.get("recommendation_eligible") is True,
                })
            except Exception as exc:  # provider failures are isolated per item
                db.rollback()
                fresh = db.get(Listing, listing.id)
                if fresh:
                    market = dict(fresh.marketplace_data or {})
                    pricing = dict(market.get("pricing_analysis") or {})
                    pricing.update({
                        "external_research_status": "unavailable",
                        "external_research_date": now.isoformat(),
                        "external_research_error": str(exc)[:500],
                        "external_sold_comparable_count": 0,
                    })
                    market["pricing_analysis"] = pricing
                    fresh.marketplace_data = market
                    db.add(fresh)
                    db.commit()
                result["failed"] += 1
                result["items"].append({"listing_id": listing.id, "status": "unavailable", "error": str(exc)[:300]})
        return result
    finally:
        db.close()


@celery_app.task(name="drain_intake_provider_media", bind=True, max_retries=None)
def drain_intake_provider_media_task(self, user_id: int) -> dict[str, Any]:
    """Drain discovery records in chunks; a chunk limit is never an intake stop."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            return {"processed": 0, "reason": "user_missing"}
        result = IntakeSlateService().monitor_google_album(db, user=user)
        if int(result.get("processing_backlog_count") or 0) > 0:
            # One successor task maintains bounded throughput without asking an
            # operator to press import again.
            self.apply_async(args=[user_id], countdown=2)
        return result
    except Exception as exc:
        logger.exception("intake provider media drain failed", extra={"user_id": user_id})
        raise self.retry(exc=exc, countdown=min(300, 2 ** min(int(self.request.retries or 0), 8)))
    finally:
        db.close()


def _force_marketplace_listing_state(
    db,
    *,
    listing_id: int,
    marketplace: str,
    status: MarketplaceListingStatus,
    response: dict | None,
) -> None:
    response = _json_safe(response or {})
    market = MarketplaceName(marketplace)
    db.flush()
    external_listing_id = None
    if isinstance(response, dict):
        external_listing_id = (
            response.get("listing_id")
            or response.get("marketplace_listing_id")
            or response.get("external_listing_id")
            or (((response.get("bridge_completion") or {}).get("result") or {}).get("listing_id"))
            or (((response.get("bridge_completion") or {}).get("result") or {}).get("marketplace_listing_id"))
        )
    existing_row = db.execute(
        select(MarketplaceListing)
        .where(MarketplaceListing.listing_id == listing_id, MarketplaceListing.marketplace == market)
        .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
    ).scalars().first()
    # Error responses normally do not repeat the remote identity.  Preserve
    # the exact stored ID so retries remain updates against the same listing;
    # never turn an update failure into an identity-less record.
    if not external_listing_id and existing_row and existing_row.marketplace_listing_id:
        external_listing_id = existing_row.marketplace_listing_id
    updated = db.execute(
        sql_update(MarketplaceListing)
        .where(
            MarketplaceListing.listing_id == listing_id,
            MarketplaceListing.marketplace == market,
        )
        .values(
            status=status,
            raw_response=response,
            marketplace_listing_id=external_listing_id,
        )
    )
    if updated.rowcount == 0:
        db.add(
            MarketplaceListing(
                listing_id=listing_id,
                marketplace=market,
                status=status,
                raw_response=response,
                marketplace_listing_id=external_listing_id,
            )
        )


def _is_placeholder_import_title(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    placeholder_titles = {
        "chat",
        "chats",
        "marketplace",
        "facebook marketplace",
        "facebook",
    }
    return normalized in placeholder_titles or normalized.startswith("chat |") or normalized.startswith("marketplace |")


def _normalize_title_for_match(value: str | None) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    return re.sub(r"\s+", " ", normalized)

def _normalize_image_key(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    name = raw.rsplit("/", 1)[-1]
    return name.lower()


def _coerce_price_number(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _extract_source_identifiers(source_metadata: dict | None) -> dict[str, str]:
    if not isinstance(source_metadata, dict):
        return {}
    raw_payload = source_metadata.get("raw_payload") if isinstance(source_metadata.get("raw_payload"), dict) else {}
    nested = raw_payload.get("source_identifiers") if isinstance(raw_payload.get("source_identifiers"), dict) else {}
    merged = {}
    for payload in (nested, raw_payload, source_metadata):
        for key in ("ebay_listing_id", "offer_id", "sku", "source_listing_reference", "source_url"):
            value = str(payload.get(key) or "").strip() if isinstance(payload, dict) else ""
            if value:
                merged[key] = value
    return merged


def _find_duplicate_import_candidate(
    *,
    db,
    user_id: int,
    source_marketplace: str,
    raw_payload: dict,
    normalized: dict,
) -> Listing | None:
    incoming_identifiers = _extract_source_identifiers({"raw_payload": raw_payload})
    incoming_ebay_listing_id = incoming_identifiers.get("ebay_listing_id")
    incoming_title = _normalize_title_for_match(normalized.get("title"))
    incoming_price = _coerce_price_number(normalized.get("listing_price"))
    incoming_images = {
        key
        for key in (_normalize_image_key(url) for url in (normalized.get("image_urls") or []))
        if key
    }
    if not incoming_title and not incoming_ebay_listing_id:
        return None

    candidates = db.execute(select(Listing).where(Listing.user_id == user_id)).scalars().all()
    for candidate in candidates:
        if str(candidate.status).lower() in {"published", "sold"}:
            continue
        candidate_images = {key for key in (_normalize_image_key(url) for url in (candidate.image_urls or [])) if key}
        candidate_placeholder = _is_placeholder_import_title(candidate.title)
        candidate_metadata = candidate.source_metadata if isinstance(candidate.source_metadata, dict) else {}
        candidate_identifiers = _extract_source_identifiers(candidate_metadata)

        for key in ("source_listing_reference", "source_url", "offer_id", "sku"):
            incoming_value = incoming_identifiers.get(key)
            if incoming_value and candidate_identifiers.get(key) == incoming_value:
                return candidate

        if incoming_ebay_listing_id and (
            candidate.ebay_listing_id == incoming_ebay_listing_id
            or candidate_identifiers.get("ebay_listing_id") == incoming_ebay_listing_id
        ):
            return candidate

        candidate_title = _normalize_title_for_match(candidate.title)
        candidate_price = _coerce_price_number(candidate.listing_price or candidate.suggested_price or candidate.buy_it_now_price)
        if incoming_title and candidate_title == incoming_title:
            if incoming_price is None or candidate_price is None or abs(candidate_price - incoming_price) <= 1.0:
                return candidate

        if incoming_images and candidate_images and incoming_images.intersection(candidate_images):
            if incoming_price is None or candidate_price is None or abs(candidate_price - incoming_price) <= 1.0:
                return candidate
            if candidate_placeholder:
                return candidate
    return None


def _crosspost_job_control_state(db, job_id: int) -> str:
    job = db.get(MarketplaceCrosspostJob, job_id)
    return str(job.status or "").lower() if job else "missing"


def _marketplace_jobs_queue_paused(db, user_id: int) -> bool:
    user = db.get(User, user_id)
    settings_json = user.settings_json if user and isinstance(user.settings_json, dict) else {}
    return bool(settings_json.get("marketplace_jobs_paused"))


def _is_already_published_to_marketplace(db, listing: Listing, marketplace: str) -> bool:
    market = str(marketplace or "").strip().lower()
    if market == MarketplaceName.ebay.value:
        return bool(listing.ebay_listing_id or str(listing.ebay_publish_status or "").upper() == "POSTED")
    row = db.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.listing_id == listing.id,
            MarketplaceListing.marketplace == MarketplaceName(market),
        )
    ).scalar_one_or_none()
    return bool(row and row.status == MarketplaceListingStatus.PUBLISHED)


def _import_job_control_state(db, job_id: int) -> str:
    job = db.get(MarketplaceImportJob, job_id)
    return str(job.status or "").lower() if job else "missing"


def _import_job_is_stale(job: MarketplaceImportJob, *, now: datetime | None = None) -> bool:
    status_value = str(job.status or "").lower()
    if status_value not in {"queued", "running"}:
        return False
    updated_at = job.updated_at or job.created_at
    if not updated_at:
        return False
    current_time = now or datetime.utcnow()
    return updated_at <= current_time - STALE_IMPORT_JOB_AFTER


def _bridge_job_id_from_submission(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    bridge_response = payload.get("bridge_response")
    if not isinstance(bridge_response, dict):
        return None
    bridge_job_id = str(bridge_response.get("job_id") or "").strip()
    return bridge_job_id or None


def _friendly_import_failure_message(*, source_marketplace: str, error: Exception) -> str:
    message = str(error)
    normalized = message.lower()
    if source_marketplace == MarketplaceName.ebay.value:
        if "no connected ebay account for user" in normalized:
            return "Connect eBay in Settings before importing existing eBay listings."
        if "no ebay account with refresh token found" in normalized:
            return "Reconnect eBay in Settings or import a fresh access token plus refresh token before retrying the import."
        if "token refresh failed" in normalized or "invalid access token" in normalized:
            return "The saved eBay connection is no longer valid. Reconnect eBay in Settings or import fresh user tokens, then retry the import."
    return message


def _best_effort_localize_import_images(image_urls: list[str] | None) -> tuple[list[str], list[str]]:
    urls = [str(url).strip() for url in (image_urls or []) if str(url).strip()]
    if not urls:
        return [], []

    storage = LocalStorage()
    localized: list[str] = []
    failures: list[str] = []
    for url in urls:
        if not url.lower().startswith(("http://", "https://")):
            if url not in localized:
                localized.append(url)
            continue
        try:
            saved = storage.save_from_url(url, prefix="marketplace_imports")
        except Exception:
            failures.append(url)
            saved = url
        if saved not in localized:
            localized.append(saved)
    return localized, failures


def _disposition_filename(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None
    match = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip() or None


def _bridge_asset_extension(file_name: str | None, content_type: str | None) -> str:
    if file_name:
        suffix = Path(file_name).suffix
        if suffix:
            return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed == ".jpe":
        return ".jpg"
    return guessed or ".jpg"


def _best_effort_localize_bridge_assets(image_assets: list[dict[str, Any]] | None) -> tuple[list[str], list[str]]:
    assets = [item for item in (image_assets or []) if isinstance(item, dict)]
    if not assets:
        return [], []

    storage = LocalStorage()
    localized: list[str] = []
    failures: list[str] = []
    for asset in assets:
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id:
            continue
        try:
            content, content_type, content_disposition = get_bridge_asset(asset_id)
            file_name = str(asset.get("file_name") or "").strip() or _disposition_filename(content_disposition)
            saved = storage.save_bytes(
                content,
                extension=_bridge_asset_extension(file_name, content_type or str(asset.get("content_type") or "")),
                prefix="marketplace_imports",
            )
            localized.append(saved)
        except Exception:
            failures.append(asset_id)
    return localized, failures


def _find_existing_imported_listing(*, db, user_id: int, source_marketplace: str, source_listing_reference: str | None) -> Listing | None:
    reference = str(source_listing_reference or "").strip()
    if not reference:
        return None

    # Exact remote identity is authoritative for import idempotency.  Check
    # the normalized MarketplaceListing projection first so legacy rows whose
    # source_metadata was incomplete still resolve to the existing canonical
    # item instead of creating a duplicate.
    try:
        market = MarketplaceName(str(source_marketplace).lower())
    except ValueError:
        market = None
    if market is not None:
        exact = db.execute(
            select(Listing)
            .join(MarketplaceListing, MarketplaceListing.listing_id == Listing.id)
            .where(
                Listing.user_id == user_id,
                MarketplaceListing.marketplace == market,
                MarketplaceListing.marketplace_listing_id == reference,
            )
            .order_by(Listing.id.asc())
        ).scalars().first()
        if exact is not None:
            return exact

    candidates = db.execute(
        select(Listing).where(
            Listing.user_id == user_id,
            Listing.source_type == f"{source_marketplace}_import",
        )
    ).scalars().all()
    for candidate in candidates:
        source_metadata = candidate.source_metadata or {}
        raw_payload = source_metadata.get("raw_payload") if isinstance(source_metadata, dict) else {}
        known_references = {
            reference,
            str(source_metadata.get("source_listing_reference") or "").strip(),
            str(raw_payload.get("source_listing_reference") or "").strip() if isinstance(raw_payload, dict) else "",
            str(raw_payload.get("source_url") or "").strip() if isinstance(raw_payload, dict) else "",
        }
        known_references.discard("")
        if reference in known_references:
            return candidate
    return None


def _create_imported_listing(
    *,
    db,
    user_id: int,
    source_marketplace: str,
    import_job_id: int,
    import_mode: str,
    source_listing_reference: str | None,
    raw_payload: dict,
    normalized: dict,
) -> tuple[Listing, bool]:
    raw_payload = raw_payload if isinstance(raw_payload, dict) else {}
    bridge_asset_images, bridge_asset_failures = _best_effort_localize_bridge_assets(raw_payload.get("image_assets"))
    localized_images = bridge_asset_images
    image_import_failures = bridge_asset_failures
    if not localized_images:
        localized_images, image_import_failures = _best_effort_localize_import_images(normalized.get("image_urls") or [])
    normalized_reference = str(source_listing_reference or "").strip() or None
    source_metadata = {
        "import_job_id": import_job_id,
        "source_marketplace": source_marketplace,
        "source_listing_reference": normalized_reference,
        "import_mode": import_mode,
        "raw_payload": raw_payload,
        "original_image_urls": normalized.get("image_urls") or [],
    }
    listing_images = normalize_listing_images(
        image_urls=localized_images,
        source_url=None,
        source_page_url=raw_payload.get("source_url") if isinstance(raw_payload, dict) else None,
        source_platform=source_marketplace,
        default_is_reference=True,
        approved=False,
    )
    condition_data = derive_condition_data(
        listing={"condition": normalized.get("condition"), "source_type": f"{source_marketplace}_import"},
        source_type=f"{source_marketplace}_import",
        source_metadata=source_metadata,
        existing={"condition_source": "import"},
    )
    shipping_profile = derive_shipping_profile(
        listing={"title": normalized.get("title"), "description": normalized.get("description")},
        item_specifics=normalized.get("item_specifics") or {},
        existing={"estimated": True, "manual_measurement_needed": True},
    )
    if raw_payload.get("image_assets"):
        source_metadata["bridge_image_assets"] = raw_payload.get("image_assets")
    if image_import_failures:
        source_metadata["image_import_failures"] = image_import_failures

    existing_listing = _find_existing_imported_listing(
        db=db,
        user_id=user_id,
        source_marketplace=source_marketplace,
        source_listing_reference=normalized_reference
        or (raw_payload.get("source_listing_reference") if isinstance(raw_payload, dict) else None)
        or (raw_payload.get("source_url") if isinstance(raw_payload, dict) else None),
    )
    if not existing_listing:
        existing_listing = _find_duplicate_import_candidate(
            db=db,
            user_id=user_id,
            source_marketplace=source_marketplace,
            raw_payload=raw_payload,
            normalized=normalized,
        )
    marketplace_data = normalize_marketplace_data(
        {
            "source_marketplace": source_marketplace,
            "manual_entry": False,
            "targets": [MarketplaceName.ebay.value],
        }
    )
    if existing_listing:
        existing_source_metadata = existing_listing.source_metadata if isinstance(existing_listing.source_metadata, dict) else {}
        import_sources = existing_source_metadata.get("import_sources")
        if not isinstance(import_sources, list):
            import_sources = []
        import_sources = [item for item in import_sources if isinstance(item, dict)]
        import_sources.append(
            {
                "source_marketplace": source_marketplace,
                "source_listing_reference": normalized_reference,
                "import_job_id": import_job_id,
                "import_mode": import_mode,
            }
        )
        incoming_title = normalized.get("title") or None
        if (not existing_listing.title or _is_placeholder_import_title(existing_listing.title)) and incoming_title:
            existing_listing.title = incoming_title
        if not existing_listing.description:
            existing_listing.description = normalized.get("description") or None
        if not existing_listing.category_id:
            existing_listing.category_id = normalized.get("category_id") or None
        if not existing_listing.condition:
            existing_listing.condition = normalized.get("condition") or None
        if existing_listing.listing_price is None:
            existing_listing.listing_price = normalized.get("listing_price")
        if not existing_listing.image_urls and localized_images:
            existing_listing.image_urls = localized_images
        if not existing_listing.listing_images and listing_images:
            existing_listing.listing_images = listing_images
        if not existing_listing.item_specifics:
            existing_listing.item_specifics = normalized.get("item_specifics") or {}
        if not existing_listing.tags:
            existing_listing.tags = normalized.get("tags") or []
        if not existing_listing.quantity:
            existing_listing.quantity = int(normalized.get("quantity") or 1)
        existing_listing.source_metadata = {**existing_source_metadata, **source_metadata, "import_sources": import_sources}
        existing_listing.condition_data = existing_listing.condition_data or condition_data
        existing_listing.shipping_profile = existing_listing.shipping_profile or shipping_profile
        existing_listing.marketplace_data = existing_listing.marketplace_data or marketplace_data
        if isinstance(existing_listing.marketplace_data, dict):
            existing_listing.marketplace_data = normalize_marketplace_data(
                {
                    **existing_listing.marketplace_data,
                    "source_marketplace": existing_listing.marketplace_data.get("source_marketplace") or source_marketplace,
                    "import_sources": sorted(
                        {*(existing_listing.marketplace_data.get("import_sources") or []), source_marketplace}
                    ),
                }
            )
        existing_listing.needs_review = True
        _persist_imported_remote_identity(db, existing_listing, source_marketplace, normalized)
        db.add(existing_listing)
        db.flush()
        return existing_listing, False

    listing = Listing(
        user_id=user_id,
        status=ListingStatus.draft,
        title=normalized.get("title") or None,
        description=normalized.get("description") or None,
        category_id=normalized.get("category_id") or None,
        condition=normalized.get("condition") or None,
        listing_price=normalized.get("listing_price"),
        quantity=int(normalized.get("quantity") or 1),
        image_urls=localized_images,
        listing_images=listing_images,
        item_specifics=normalized.get("item_specifics") or {},
        tags=normalized.get("tags") or [],
        source_type=f"{source_marketplace}_import",
        source_metadata=source_metadata,
        condition_data=condition_data,
        shipping_profile=shipping_profile,
        marketplace_data=marketplace_data,
        needs_review=True,
    )
    db.add(listing)
    db.flush()
    _persist_imported_remote_identity(db, listing, source_marketplace, normalized)
    return listing, True


def _persist_imported_remote_identity(db, listing: Listing, source_marketplace: str, normalized: dict) -> None:
    """Attach an imported remote listing identity without creating duplicates."""
    remote_id = str(normalized.get("remote_listing_id") or "").strip()
    if not remote_id:
        return
    try:
        market = MarketplaceName(str(source_marketplace).lower())
    except ValueError:
        return
    row = db.execute(select(MarketplaceListing).where(
        MarketplaceListing.listing_id == listing.id,
        MarketplaceListing.marketplace == market,
    )).scalars().first()
    status_value = str(normalized.get("remote_status") or "published").upper()
    status = MarketplaceListingStatus.PUBLISHED if status_value in {"ACTIVE", "PUBLISHED", "LIVE", "UPDATED"} else MarketplaceListingStatus.UPDATED
    if row is None:
        row = MarketplaceListing(listing_id=listing.id, marketplace=market)
    row.marketplace_listing_id = remote_id
    row.status = status
    row.raw_response = {
        **(row.raw_response if isinstance(row.raw_response, dict) else {}),
        "imported": True,
        "source_url": normalized.get("remote_url"),
    }
    db.add(row)


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
    dont_autoretry_for=(EbaySellerRestrictedError,),
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
        user = db.get(User, listing.user_id)
        if not user:
            raise ValueError("User not found")

        try:
            rate_limiter.acquire(marketplace)
            execution_mode = resolve_execution_mode(listing=listing, user=user, marketplace=marketplace)
            if execution_mode == "browser_assist" and not has_online_compatible_extension(db, user.id):
                # Keep the extension as the primary transport, but make the
                # existing hosted browser bridge an actual fallback for the
                # single-listing publish entry point as well as bulk jobs.
                execution_mode = "hosted_browser_assist"
            if execution_mode == "direct_api":
                result = multi_platform_publisher.publish(db, listing, marketplace)
                upsert_marketplace_listing(
                    db,
                    listing_id=listing_id,
                    marketplace=marketplace,
                    status=result.status,
                    response=result.response,
                )
                db.commit()
                return {
                    "marketplace": marketplace,
                    "execution_mode": execution_mode,
                    "status": result.status.value,
                    "response": result.response,
                }

            if execution_mode in {"browser_assist", "provider_assist"}:
                preflight = _json_safe(MarketplacePreflightService().preflight_listing(db, listing, marketplace))
                if preflight.get("blockers"):
                    response = {"status": "BLOCKED", "error_code": "PREFLIGHT_BLOCKED", "preflight": preflight}
                    upsert_marketplace_listing(
                        db,
                        listing_id=listing_id,
                        marketplace=marketplace,
                        status=MarketplaceListingStatus.FAILED,
                        response=response,
                    )
                    db.commit()
                    return {"marketplace": marketplace, "execution_mode": "browser_extension", **response}
                try:
                    extension_job, created = queue_extension_marketplace_action(
                        db,
                        user_id=int(user.id),
                        listing=listing,
                        marketplace=marketplace,
                        action="CREATE",
                    )
                except MarketplaceExtensionJobError as exc:
                    response = {"status": "FAILED", "error_code": exc.code, "error": str(exc), "execution_mode": "browser_extension"}
                    upsert_marketplace_listing(
                        db,
                        listing_id=listing_id,
                        marketplace=marketplace,
                        status=MarketplaceListingStatus.FAILED,
                        response=response,
                    )
                    db.commit()
                    return {"marketplace": marketplace, **response}
                response = {
                    "status": extension_job.status,
                    "execution_mode": "browser_extension",
                    "extension_job_id": extension_job.id,
                    "deduplicated": not created,
                }
                upsert_marketplace_listing(
                    db,
                    listing_id=listing_id,
                    marketplace=marketplace,
                    status=MarketplaceListingStatus.PENDING,
                    response=response,
                )
                db.commit()
                return {"marketplace": marketplace, **response}

            response = execute_secondary_marketplace_path(
                listing=listing,
                marketplace=marketplace,
                execution_mode=execution_mode,
            )
            upsert_marketplace_listing(
                db,
                listing_id=listing.id,
                marketplace=marketplace,
                status=MarketplaceListingStatus.PENDING,
                response=response,
            )
            db.commit()
            return {
                "marketplace": marketplace,
                "execution_mode": execution_mode,
                "status": "planned",
                "response": response,
            }
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


@celery_app.task(bind=True, name="process_marketplace_crosspost_job")
def process_marketplace_crosspost_job_task(self, job_id: int) -> dict:
    with SessionLocal() as db:
        job = db.get(MarketplaceCrosspostJob, job_id)
        if not job:
            raise ValueError("Crosspost job not found")
        listing = db.get(Listing, job.listing_id)
        if not listing:
            job.status = "failed"
            job.last_error = "Listing not found"
            db.add(job)
            db.commit()
            raise ValueError("Listing not found")
        user = db.get(User, job.user_id)
        if not user:
            job.status = "failed"
            job.last_error = "User not found"
            db.add(job)
            db.commit()
            raise ValueError("User not found")

        if str(job.status).lower() in {"canceled", "paused"}:
            return {"job_id": job_id, "status": str(job.status).lower()}

        if _marketplace_jobs_queue_paused(db, job.user_id):
            job.status = "paused"
            job.last_error = "Queue paused by operator before execution."
            db.add(job); db.commit()
            return {"job_id": job_id, "status": "paused"}

        # Durable broker recovery can enqueue a second message while an older
        # message is delayed. Only one worker may claim a child operation.
        # Without this compare-and-set, both messages could issue CREATE.
        claimed = db.execute(
            sql_update(MarketplaceCrosspostJob)
            .where(
                MarketplaceCrosspostJob.id == job_id,
                MarketplaceCrosspostJob.status == "queued",
            )
            .values(status="running", updated_at=datetime.now(UTC).replace(tzinfo=None))
        )
        if int(claimed.rowcount or 0) != 1:
            db.rollback()
            db.refresh(job)
            return {"job_id": job_id, "status": str(job.status or "unknown").lower(), "duplicate_delivery": True}
        db.commit()
        db.refresh(job)

        results: list[dict] = []
        failed_markets: list[str] = []
        assisted_jobs_pending = False
        pending_bridge_polls: list[tuple[int, int, str, str, str]] = []
        targets = job.target_marketplaces or []
        operation = str((job.execution_plan or {}).get("operation") or "create").lower()
        batch_preflights: dict[str, dict] = {}
        # Preflight every create destination before touching any remote market,
        # but keep the results destination-scoped.  A blocked Facebook session
        # must never prevent an otherwise-ready eBay target from publishing
        # (and vice versa).  The old all-or-nothing gate left durable jobs in a
        # failed state without attempting the healthy destination.
        if operation not in {"update", "end"}:
            preflight_service = MarketplacePreflightService()
            for market in targets:
                try:
                    plan = job.execution_plan or {}
                    preflights_by_market = plan.get("preflight_by_marketplace")
                    # Singular preflight fields are a legacy, one-destination
                    # representation.  Never apply one marketplace's
                    # readiness result to another destination.
                    planned_preflight = (
                        preflights_by_market.get(str(market).lower())
                        if isinstance(preflights_by_market, dict)
                        else (plan.get("preflight") if len(targets) == 1 else None)
                    )
                    # The operator approval route persists the exact
                    # destination preflight used to create this child job.
                    # Reusing it prevents a second, materially different
                    # readiness contract from rejecting a job after the
                    # operator already received a pass.  Older jobs without
                    # a persisted plan still receive a fresh offline-safe
                    # preflight.
                    legacy_facebook_extension_gate = bool(
                        str(market).lower() == MarketplaceName.facebook.value
                        and isinstance(planned_preflight, dict)
                        and any(
                            str(issue.get("code") or "").upper() == "FACEBOOK_ASSISTED_WORKFLOW_UNVERIFIED"
                            for issue in (planned_preflight.get("blockers") or [])
                            if isinstance(issue, dict)
                        )
                    )
                    if isinstance(planned_preflight, dict) and planned_preflight.get("status") and not legacy_facebook_extension_gate:
                        preflight = _json_safe(planned_preflight)
                    else:
                        preflight = _json_safe(preflight_service.preflight_listing(db, listing, market))
                    preflight_service.cache_preflight_summary(db, listing, preflight)
                except Exception as exc:
                    preflight = {"marketplace": market, "status": "blocked", "blockers": [{"code": "PREFLIGHT_ERROR", "message": str(exc), "retryable": True}]}
                batch_preflights[market] = preflight
            # Persist the destination-scoped preflight before canonical
            # readiness is evaluated below.  Without this commit the worker
            # can successfully calculate a fresh ready preflight, then the
            # canonical gate reads the previous transaction snapshot and
            # reports "Destination preflight is pending", causing a job that
            # was just accepted to fail before the publisher runs.
            db.commit()
            # Do not return here: healthy destinations must still be attempted
            # and blocked destinations must receive an explicit,
            # destination-scoped result.
        for market in targets:
            db.expire_all()
            control_state = _crosspost_job_control_state(db, job_id)
            if control_state in {"canceled", "paused"}:
                stopped_job = db.get(MarketplaceCrosspostJob, job_id)
                if stopped_job:
                    stopped_job.result_summary = {
                        **(stopped_job.result_summary if isinstance(stopped_job.result_summary, dict) else {}),
                        "results": _json_safe(results),
                        "stopped_by_operator": control_state,
                    }
                    stopped_job.last_error = f"{control_state.title()} by operator; remaining destinations were not started."
                    db.add(stopped_job)
                    db.commit()
                return {"job_id": job_id, "status": control_state, "results": results}
            if _marketplace_jobs_queue_paused(db, job.user_id):
                job = db.get(MarketplaceCrosspostJob, job_id)
                if job:
                    job.status = "paused"
                    job.result_summary = {"results": _json_safe(results), "paused_by_operator": True}
                    job.last_error = "Queue paused by operator; remaining destinations were not started."
                    db.add(job); db.commit()
                return {"job_id": job_id, "status": "paused", "results": results}
            if operation == "end" and str(market).lower() == MarketplaceName.ebay.value:
                expected_identity = str((job.execution_plan or {}).get("external_listing_id") or "").strip()
                try:
                    ended = asyncio.run(end_ebay_listing(
                        listing,
                        db,
                        expected_external_listing_id=expected_identity,
                    ))
                    results.append({"marketplace": market, "execution_mode": "direct_api", "operation": "END", "status": ended["status"], "external_listing_id": expected_identity, "response": ended})
                except Exception as exc:
                    failed_markets.append(market)
                    results.append({"marketplace": market, "execution_mode": "direct_api", "operation": "END", "status": "failed", "external_listing_id": expected_identity or None, "error": str(exc)})
                continue
            if operation == "end":
                try:
                    extension_job, created = queue_extension_marketplace_action(
                        db,
                        user_id=job.user_id,
                        listing=listing,
                        marketplace=market,
                        action="END",
                        priority=int(job.priority or 1),
                        crosspost_job=job,
                    )
                    assisted_jobs_pending = assisted_jobs_pending or extension_job.status not in {"COMPLETED", "FAILED", "CANCELLED"}
                    results.append({"marketplace": market, "execution_mode": "browser_extension", "operation": "END", "status": str(extension_job.status).lower(), "extension_job_id": extension_job.id, "external_listing_id": extension_job.external_listing_id, "deduplicated": not created})
                except MarketplaceExtensionJobError as exc:
                    failed_markets.append(market)
                    results.append({"marketplace": market, "execution_mode": "browser_extension", "operation": "END", "status": "failed", "error_code": exc.code, "error": str(exc)})
                continue
            if operation == "update" and str(market).lower() == "ebay":
                try:
                    expected_identity = str((job.execution_plan or {}).get("external_listing_id") or "").strip()
                    revised = asyncio.run(revise_ebay_listing(listing, db, expected_external_listing_id=expected_identity))
                    upsert_marketplace_listing(
                        db,
                        listing_id=listing.id,
                        marketplace=market,
                        status=MarketplaceListingStatus.UPDATED,
                        response=revised,
                    )
                    _force_marketplace_listing_state(
                        db,
                        listing_id=listing.id,
                        marketplace=market,
                        status=MarketplaceListingStatus.UPDATED,
                        response=revised,
                    )
                    results.append({"marketplace": market, "execution_mode": "direct_api", "operation": "UPDATE", "status": "UPDATED", "response": revised})
                except Exception as exc:
                    failed_markets.append(market)
                    failure_response = {
                        "status": "FAILED",
                        "operation": "UPDATE",
                        "external_listing_id": str((job.execution_plan or {}).get("external_listing_id") or "").strip() or None,
                        "error": str(exc),
                    }
                    # A failed update is a destination failure, not a reason
                    # to create a replacement listing. Persist it on the exact
                    # marketplace identity so the editor/jobs UI can show the
                    # real blocker and retry that destination only.
                    upsert_marketplace_listing(
                        db,
                        listing_id=listing.id,
                        marketplace=market,
                        status=MarketplaceListingStatus.FAILED,
                        response=failure_response,
                    )
                    _force_marketplace_listing_state(
                        db,
                        listing_id=listing.id,
                        marketplace=market,
                        status=MarketplaceListingStatus.FAILED,
                        response=failure_response,
                    )
                    results.append({"marketplace": market, "execution_mode": "direct_api", "operation": "UPDATE", "status": "failed", "error": str(exc), "response": failure_response})
                continue
            if operation == "update" and str(market).lower() != MarketplaceName.ebay.value:
                try:
                    extension_job, created = queue_extension_marketplace_action(
                        db,
                        user_id=job.user_id,
                        listing=listing,
                        marketplace=market,
                        action="UPDATE",
                        priority=int(job.priority or 1),
                        crosspost_job=job,
                    )
                    assisted_jobs_pending = assisted_jobs_pending or extension_job.status not in {"COMPLETED", "FAILED", "CANCELLED"}
                    results.append({
                        "marketplace": market,
                        "execution_mode": "browser_extension",
                        "operation": "UPDATE",
                        "status": extension_job.status.lower(),
                        "extension_job_id": extension_job.id,
                        "deduplicated": not created,
                    })
                except MarketplaceExtensionJobError as exc:
                    failed_markets.append(market)
                    results.append({"marketplace": market, "execution_mode": "browser_extension", "operation": "UPDATE", "status": "failed", "error_code": exc.code, "error": str(exc)})
                continue
            if _is_already_published_to_marketplace(db, listing, market):
                results.append(
                    {
                        "marketplace": market,
                        "execution_mode": "skipped",
                        "status": "skipped_already_published",
                        "response": {"status": "SKIPPED_ALREADY_PUBLISHED"},
                    }
                )
                continue
            execution_mode = resolve_execution_mode(listing=listing, user=user, marketplace=market)
            if execution_mode == "browser_assist" and not has_online_compatible_extension(db, user.id):
                # Extension is primary when the tenant has a fresh, compatible
                # heartbeat. Otherwise use the hosted browser bridge as the
                # fallback transport; the canonical mapper/job/result handling
                # remains unchanged.
                execution_mode = "hosted_browser_assist"
            preflight = batch_preflights.get(market)
            if preflight is None:
                preflight = _json_safe(MarketplacePreflightService().preflight_listing(db, listing, market))
            preflight_status = str(preflight.get("status") or "").strip().lower()
            if preflight.get("blockers") or preflight_status not in {"ready", "ready_with_warnings", "published"}:
                failed_markets.append(market)
                response = {"preflight": preflight, "error": "Publish blocked by marketplace preflight."}
                upsert_marketplace_listing(db, listing_id=listing.id, marketplace=market, status=MarketplaceListingStatus.FAILED, response=response)
                _force_marketplace_listing_state(db, listing_id=listing.id, marketplace=market, status=MarketplaceListingStatus.FAILED, response=response)
                results.append({"marketplace": market, "execution_mode": execution_mode, "status": "blocked", "response": response})
                continue
            plan = job.execution_plan or {}
            payloads_by_market = plan.get("approved_execution_payload_by_marketplace")
            hashes_by_market = plan.get("preflight_payload_hash_by_marketplace")
            # As with preflight, a legacy snapshot is valid only for a job
            # with exactly one destination.  Reusing an eBay snapshot for
            # Facebook (or vice versa) is unsafe and caused invalid jobs to
            # reach provider automation.
            planned_payload = (
                payloads_by_market.get(str(market).lower())
                if isinstance(payloads_by_market, dict)
                else (plan.get("approved_execution_payload") if len(targets) == 1 else None)
            )
            current_payload = build_marketplace_payload(listing, market)
            current_payload_hash = compute_payload_hash(current_payload)
            payload = planned_payload if isinstance(planned_payload, dict) else current_payload
            execution_payload_hash = compute_payload_hash(payload)
            planned_hash = str(
                (hashes_by_market.get(str(market).lower()) if isinstance(hashes_by_market, dict) else (plan.get("preflight_payload_hash") if len(targets) == 1 else None))
                or ""
            ).strip() or None
            # New operator-approved jobs carry the exact payload that was
            # preflighted. Execute that durable representation.  The current
            # listing hash is retained diagnostically; canonical edits are
            # responsible for invalidating approval and queuing a fresh plan,
            # while background enrichment is explicitly frozen after approval.
            if planned_hash and execution_payload_hash and planned_hash != execution_payload_hash:
                failed_markets.append(market)
                mismatch = {
                    "code": "PREFLIGHT_EXECUTION_PAYLOAD_MISMATCH",
                    "message": "The payload changed after preflight; publication was stopped before the provider call.",
                    "preflight_payload_hash": planned_hash,
                    "execution_payload_hash": execution_payload_hash,
                    "retryable": True,
                }
                response = {"error": mismatch["message"], "blockers": [mismatch], "preflight": preflight}
                upsert_marketplace_listing(db, listing_id=listing.id, marketplace=market, status=MarketplaceListingStatus.FAILED, response=response)
                _force_marketplace_listing_state(db, listing_id=listing.id, marketplace=market, status=MarketplaceListingStatus.FAILED, response=response)
                results.append({"marketplace": market, "execution_mode": execution_mode, "status": "blocked", "response": response})
                continue
            execution_plan = dict(job.execution_plan or {})
            execution_plan.setdefault("payload_hashes", {})[market] = {
                "preflight": planned_hash,
                "execution": execution_payload_hash,
                "current_listing": current_payload_hash,
                "approved_payload_drift": bool(isinstance(planned_payload, dict) and current_payload_hash != execution_payload_hash),
                "same": bool(not planned_hash or not execution_payload_hash or planned_hash == execution_payload_hash),
            }
            job.execution_plan = execution_plan
            db.add(job)
            if execution_mode == "direct_api":
                try:
                    result = multi_platform_publisher.publish(db, listing, market)
                except EbayIntegrationError as exc:
                    # Persist the exact provider response and finish this child.
                    # Retrying the whole task after inventory/offer mutations
                    # can replay CREATE, while the durable dispatcher used to
                    # replay a stale running row after Celery exhausted retries.
                    failed_markets.append(market)
                    response = _ebay_crosspost_error_payload(exc)
                    repair = (listing.marketplace_data or {}).get("publish_failure_repair") if isinstance(listing.marketplace_data, dict) else {}
                    applied_repairs = repair.get("applied_actions") if isinstance(repair, dict) else []
                    if response.get("remote_outcome") == "NOT_PUBLISHED" and applied_repairs:
                        # Provider validation rejections are definitive: eBay
                        # explicitly refused the offer publish/create. If the
                        # failure handler repaired evidence-backed fields and a
                        # fresh live preflight is now clean, retry this exact
                        # destination once against the same offer/SKU. Never
                        # retry ambiguous provider outcomes.
                        refreshed_preflight = _json_safe(MarketplacePreflightService().preflight_listing(db, listing, market))
                        if not refreshed_preflight.get("blockers") and str(refreshed_preflight.get("status") or "").lower() in {"ready", "ready_with_warnings", "published"}:
                            try:
                                repaired_result = multi_platform_publisher.publish(db, listing, market)
                                failed_markets.pop()
                                upsert_marketplace_listing(db, listing_id=listing.id, marketplace=market, status=repaired_result.status, response=repaired_result.response)
                                _force_marketplace_listing_state(db, listing_id=listing.id, marketplace=market, status=repaired_result.status, response=repaired_result.response)
                                results.append({
                                    "marketplace": market,
                                    "execution_mode": execution_mode,
                                    "operation": "CREATE",
                                    "status": repaired_result.status.value,
                                    "response": repaired_result.response,
                                    "automatic_repair_retry": True,
                                    "initial_provider_rejection": response,
                                })
                                continue
                            except EbayIntegrationError as retry_exc:
                                response = _ebay_crosspost_error_payload(retry_exc)
                                response["automatic_repair_retry"] = True
                                response["initial_provider_rejection"] = _ebay_crosspost_error_payload(exc)
                    upsert_marketplace_listing(db, listing_id=listing.id, marketplace=market, status=MarketplaceListingStatus.FAILED, response=response)
                    _force_marketplace_listing_state(db, listing_id=listing.id, marketplace=market, status=MarketplaceListingStatus.FAILED, response=response)
                    results.append({"marketplace": market, "execution_mode": execution_mode, "operation": "CREATE", "status": "failed", "response": response})
                    continue
                upsert_marketplace_listing(
                    db,
                    listing_id=listing.id,
                    marketplace=market,
                    status=result.status,
                    response=result.response,
                )
                _force_marketplace_listing_state(
                    db,
                    listing_id=listing.id,
                    marketplace=market,
                    status=result.status,
                    response=result.response,
                )
                results.append(
                    {
                        "marketplace": market,
                        "execution_mode": execution_mode,
                        "status": result.status.value,
                        "response": result.response,
                    }
                )
            elif execution_mode in {"browser_assist", "provider_assist"}:
                try:
                    extension_job, created = queue_extension_marketplace_action(
                        db,
                        user_id=job.user_id,
                        listing=listing,
                        marketplace=market,
                        action="CREATE",
                        priority=int(job.priority or 1),
                        crosspost_job=job,
                    )
                    assisted_jobs_pending = assisted_jobs_pending or extension_job.status not in {"COMPLETED", "FAILED", "CANCELLED"}
                    response = {
                        "status": extension_job.status,
                        "execution_mode": "browser_extension",
                        "extension_job_id": extension_job.id,
                        "deduplicated": not created,
                    }
                    upsert_marketplace_listing(
                        db,
                        listing_id=listing.id,
                        marketplace=market,
                        status=MarketplaceListingStatus.PENDING,
                        response=response,
                    )
                    results.append({
                        "marketplace": market,
                        "execution_mode": "browser_extension",
                        "operation": "CREATE",
                        "status": str(extension_job.status).lower(),
                        "extension_job_id": extension_job.id,
                        "deduplicated": not created,
                    })
                except MarketplaceExtensionJobError as exc:
                    failed_markets.append(market)
                    response = {"error_code": exc.code, "error": str(exc), "execution_mode": "browser_extension"}
                    upsert_marketplace_listing(db, listing_id=listing.id, marketplace=market, status=MarketplaceListingStatus.FAILED, response=response)
                    results.append({"marketplace": market, "execution_mode": "browser_extension", "operation": "CREATE", "status": "failed", **response})
            else:
                response = execute_secondary_marketplace_path(
                    listing=listing,
                    marketplace=market,
                    execution_mode=execution_mode,
                )
                bridge_job_id = _bridge_job_id_from_submission(
                    response.get("bridge_submission") if isinstance(response, dict) else None
                )
                result_status = str((response or {}).get("status") or "planned")
                listing_status = MarketplaceListingStatus.PENDING
                error_message = None

                if bridge_job_id:
                    if str(market).lower() == MarketplaceName.facebook.value:
                        # Facebook's hosted runner can take several minutes.
                        # Do not hold the single publication worker in a tight
                        # poll loop while it runs; that stalls unrelated eBay
                        # jobs in the durable queue. Persist pending state and
                        # observe the already-created bridge job asynchronously.
                        response = {
                            **(response if isinstance(response, dict) else {}),
                            "bridge_job_id": bridge_job_id,
                            "bridge_poll_status": "pending",
                        }
                        result_status = "browser_automation_pending"
                        listing_status = MarketplaceListingStatus.PENDING
                        assisted_jobs_pending = True
                        pending_bridge_polls.append((job_id, listing.id, str(market), bridge_job_id, str(execution_mode)))
                    else:
                        try:
                            bridge_completion = wait_for_bridge_job(job_id=bridge_job_id, timeout_seconds=600, poll_interval_seconds=1.0)
                            response = {**response, "bridge_completion": bridge_completion}
                            completion_status = str(bridge_completion.get("status") or "").strip().lower()
                            if completion_status != "completed":
                                error_message = str(bridge_completion.get("error") or f"Bridge cross-post job finished with status '{completion_status}'")
                            else:
                                bridge_result = bridge_completion.get("result") if isinstance(bridge_completion.get("result"), dict) else {}
                                result_status = str(bridge_result.get("status") or response.get("status") or "planned")
                                submitted_to_marketplace = bool(bridge_result.get("submitted")) or result_status.strip().lower() in {"submitted_to_marketplace", "published"}
                                listing_urls = bridge_result.get("listing_urls") if isinstance(bridge_result.get("listing_urls"), list) else []
                                marketplace_listing_id = str(bridge_result.get("marketplace_listing_id") or "").strip() or None
                                if submitted_to_marketplace:
                                    listing_status = MarketplaceListingStatus.PUBLISHED
                                    response = {**response, "status": result_status, "bridge_completion": bridge_completion, "submitted": True}
                                    if marketplace_listing_id:
                                        response["marketplace_listing_id"] = marketplace_listing_id
                                    if listing_urls:
                                        response["listing_urls"] = listing_urls
                        except Exception as exc:
                            error_message = str(exc)

                if error_message:
                    failed_markets.append(market)
                    failure_response = {
                        **(response if isinstance(response, dict) else {}),
                        "error": error_message,
                    }
                    upsert_marketplace_listing(
                        db,
                        listing_id=listing.id,
                        marketplace=market,
                        status=MarketplaceListingStatus.FAILED,
                        response=failure_response,
                    )
                    _force_marketplace_listing_state(
                        db,
                        listing_id=listing.id,
                        marketplace=market,
                        status=MarketplaceListingStatus.FAILED,
                        response=failure_response,
                    )
                    results.append(
                        {
                            "marketplace": market,
                            "execution_mode": execution_mode,
                            "status": "failed",
                            "error": error_message,
                            "response": failure_response,
                        }
                    )
                    continue

                upsert_marketplace_listing(
                    db,
                    listing_id=listing.id,
                    marketplace=market,
                    status=listing_status,
                    response=response,
                )
                _force_marketplace_listing_state(
                    db,
                    listing_id=listing.id,
                    marketplace=market,
                    status=listing_status,
                    response=response,
                )
                results.append(
                    {
                        "marketplace": market,
                        "execution_mode": execution_mode,
                        "status": result_status,
                        "response": response,
                    }
                )

        db.expire_all()
        control_state = _crosspost_job_control_state(db, job_id)
        if control_state in {"canceled", "paused"}:
            # A provider call may have completed just before the operator
            # paused/canceled this job. Preserve its result before stopping;
            # otherwise the remote side effect can be real while job history
            # misleadingly says nothing happened.
            job = db.get(MarketplaceCrosspostJob, job_id)
            if job:
                job.result_summary = {
                    **(job.result_summary if isinstance(job.result_summary, dict) else {}),
                    "results": _json_safe(results),
                    "stopped_by_operator": control_state,
                }
                job.last_error = (
                    "Paused by operator after the in-flight destination finished; remaining destinations were not started."
                    if control_state == "paused"
                    else "Canceled by operator after the in-flight destination finished; remaining destinations were not started."
                )
                db.add(job)
                db.commit()
            return {"job_id": job_id, "status": control_state, "results": results}
        # Publisher/preflight adapters can return datetime values in their
        # diagnostic payloads.  JSONB columns must receive a fully serializable
        # result; otherwise every otherwise-successful publish is retried with
        # ``StatementError: datetime is not JSON serializable`` after the
        # marketplace call has already completed.
        results = _json_safe(results)
        # Unpublish/archive finalization is aggregate across every child
        # destination.  A single successful END must never archive a listing
        # while another destination is still running, failed, or unknown.
        blocked_results = [item for item in results if str(item.get("status") or "").lower() in {"blocked", "failed", "error"}]
        successful_results = [item for item in results if str(item.get("status") or "").lower() in {"published", "updated", "planned", "ready", "success"}]
        # A failed publish is not a return to review. If every selected
        # destination was blocked, persist explicit attention state so the
        # queue cannot offer the same known-doomed listing again.
        if blocked_results and not successful_results and not assisted_jobs_pending:
            listing.needs_review = False
            listing.processing_state = "blocked"
            first_response = blocked_results[0].get("response") if isinstance(blocked_results[0].get("response"), dict) else {}
            first_blocker = next(
                (
                    str(issue.get("message") or issue.get("user_message") or issue.get("code") or "").strip()
                    for issue in (first_response.get("preflight") or {}).get("blockers") or []
                    if isinstance(issue, dict) and str(issue.get("message") or issue.get("user_message") or issue.get("code") or "").strip()
                ),
                "Marketplace publish was blocked; review the destination result before retrying.",
            )
            listing.processing_blocking_reason = first_blocker
            db.add(listing)
        job.status = "failed" if failed_markets and not assisted_jobs_pending else "running" if assisted_jobs_pending else "completed"
        job.last_error = f"Cross-post execution failed for: {', '.join(failed_markets)}" if failed_markets else None
        job.attempt_count = int(job.attempt_count or 0) + 1
        if failed_markets:
            # Lower numeric priority is higher priority; demote automatic
            # failures so a bounded retry cannot starve fresh operator work.
            job.priority = min(10, int(job.priority or 1) + 1)
            job.next_attempt_at = datetime.now(UTC) + timedelta(minutes=min(120, 2 ** min(job.attempt_count, 6)))
        job.result_summary = {"results": results}
        operation = str((job.execution_plan or {}).get("operation") or "create").lower()
        if failed_markets and operation == "end":
            sale_source = str((job.execution_plan or {}).get("sale_source") or "").strip()
            for market in failed_markets:
                external_id = str((job.execution_plan or {}).get("external_listing_id") or "")
                title = (
                    f"SOLD ITEM STILL LIVE ON {market.upper()}"
                    if sale_source
                    else f"LISTING END FAILED ON {market.upper()}"
                )
                failure = next((result for result in results if result.get("marketplace") == market and result.get("status") == "failed"), {})
                failure_response = failure.get("response") if isinstance(failure.get("response"), dict) else {}
                reason = str(failure.get("error") or failure_response.get("error") or failure_response.get("error_code") or failure.get("status") or "Removal was not confirmed")
                create_process_notification(
                    db,
                    user_id=job.user_id,
                    title=title,
                    message=f"{listing.title or 'This item'} could not be removed from {market}. Reason: {reason}. Open the exact listing and retry or confirm removal.",
                    notification_type="marketplace_delist_failed",
                    href=f"/listings/{listing.id}",
                    metadata_json={"listing_id": listing.id, "marketplace": market, "job_id": job.id, "external_listing_id": external_id or None, "reason": reason, "sale_source": sale_source or None},
                )
        revision_id = (job.execution_plan or {}).get("revision_id")
        if revision_id:
            revision_row = db.get(ListingRevision, int(revision_id))
            if revision_row:
                revision_row.marketplace_results = {str(r.get("marketplace")): r for r in results}
                revision_row.status = "failed" if failed_markets else "completed"
                revision_row.sync_state = "partially_synced" if failed_markets and results else ("update_failed" if failed_markets else "synced")
                db.add(revision_row)
        marketplace_data = dict(listing.marketplace_data or {}) if isinstance(listing.marketplace_data, dict) else {}
        remote_sync = dict(marketplace_data.get("remote_sync") or {}) if isinstance(marketplace_data.get("remote_sync"), dict) else {}
        for result in results:
            market = str(result.get("marketplace") or "").strip().lower()
            if not market:
                continue
            prior = dict(remote_sync.get(market) or {}) if isinstance(remote_sync.get(market), dict) else {}
            status = str(result.get("status") or "").strip().lower()
            response = result.get("response") if isinstance(result.get("response"), dict) else {}
            confirmed_id = response.get("listing_id") or response.get("marketplace_listing_id") or response.get("external_listing_id") or prior.get("remote_id")
            remote_sync[market] = {
                **prior,
                "state": "confirmed" if status in {"updated", "published", "submitted_to_marketplace"} else "blocked" if status in {"failed", "blocked"} else "pending",
                "remote_id": confirmed_id,
                "last_result": result,
                "last_error": result.get("error") if status in {"failed", "blocked"} else None,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        marketplace_data["remote_sync"] = remote_sync
        listing.marketplace_data = marketplace_data
        db.add(listing)
        db.add(job)
        db.commit()
        for poll_args in pending_bridge_polls:
            try:
                poll_hosted_bridge_publish_task.apply_async(args=list(poll_args), countdown=10, priority=1)
            except Exception as exc:
                # The durable parent still carries the bridge ID. The periodic
                # dispatcher below will recover polling work if broker enqueue
                # fails; it must not resubmit the remote CREATE operation.
                logger.warning("hosted_bridge_publish_poll_enqueue_failed", extra={"crosspost_job_id": job_id, "bridge_job_id": poll_args[3], "error": str(exc)[:300]})
        batch_id = str(getattr(job, "batch_id", None) or (job.execution_plan or {}).get("bulk_batch_id") or "").strip()
        if batch_id:
            from app.services.marketplace_orchestrator import finalize_unpublish_archive_listing, recompute_publish_batch
            if operation == "end" and bool((job.execution_plan or {}).get("unpublish_and_archive")):
                finalize_unpublish_archive_listing(db, batch_id, listing.id)
            recompute_publish_batch(db, batch_id)
            db.commit()
        return {"job_id": job_id, "status": job.status, "results": results}


@celery_app.task(name="poll_hosted_bridge_publish")
def poll_hosted_bridge_publish_task(
    crosspost_job_id: int,
    listing_id: int,
    marketplace: str,
    bridge_job_id: str,
    execution_mode: str,
) -> dict[str, Any]:
    """Observe one hosted publish job without occupying a worker in a wait loop.

    Polling only reads the bridge's durable job record. It never submits or
    retries the marketplace CREATE action, so delivery/recovery duplicates
    cannot create duplicate remote listings.
    """
    poll_again = False
    with SessionLocal() as db:
        job = db.get(MarketplaceCrosspostJob, int(crosspost_job_id))
        listing = db.get(Listing, int(listing_id))
        if not job or not listing:
            return {"status": "missing_parent_or_listing"}
        summary = dict(job.result_summary or {}) if isinstance(job.result_summary, dict) else {}
        results = list(summary.get("results") or [])
        result_index = next(
            (
                index for index, item in enumerate(results)
                if isinstance(item, dict)
                and str(item.get("marketplace") or "").lower() == str(marketplace).lower()
                and str((item.get("response") or {}).get("bridge_job_id") or "") == str(bridge_job_id)
            ),
            None,
        )
        if result_index is None:
            return {"status": "bridge_result_not_in_parent"}
        item = dict(results[result_index])
        response = dict(item.get("response") or {}) if isinstance(item.get("response"), dict) else {}
        if response.get("bridge_poll_status") in {"completed", "failed", "reconciliation_required"}:
            return {"status": response["bridge_poll_status"], "already_final": True}

        try:
            bridge = get_bridge_job(str(bridge_job_id))
            bridge_status = str(bridge.get("status") or "").strip().lower()
            if bridge_status not in {"completed", "failed", "canceled"}:
                poll_again = True
                response.update({"bridge_poll_status": "pending", "bridge_poll_attempts": int(response.get("bridge_poll_attempts") or 0) + 1})
                item.update({"status": "browser_automation_pending", "response": response})
            elif bridge_status == "completed":
                result = bridge.get("result") if isinstance(bridge.get("result"), dict) else {}
                result_status = str(result.get("status") or "").strip().lower()
                submitted = bool(result.get("submitted")) or result_status in {"submitted_to_marketplace", "published"}
                remote_id = str(result.get("marketplace_listing_id") or result.get("listing_id") or "").strip() or None
                urls = result.get("listing_urls") if isinstance(result.get("listing_urls"), list) else []
                if submitted:
                    response.update({"bridge_poll_status": "completed", "bridge_completion": bridge, "status": "submitted_to_marketplace", "submitted": True})
                    if remote_id:
                        response["marketplace_listing_id"] = remote_id
                    if urls:
                        response["listing_urls"] = urls
                    if str(marketplace).lower() == "facebook" and not (remote_id or urls):
                        response["bridge_confirmation_status"] = "submitted_without_visible_listing"
                    item.update({"status": "submitted_to_marketplace", "response": response})
                    _force_marketplace_listing_state(
                        db, listing_id=listing.id, marketplace=marketplace,
                        status=MarketplaceListingStatus.PUBLISHED if remote_id or urls else MarketplaceListingStatus.PENDING,
                        response=response,
                    )
                else:
                    message = str(result.get("error") or bridge.get("error") or "Hosted browser flow finished without confirming marketplace submission.")
                    response.update({"bridge_poll_status": "failed", "bridge_completion": bridge, "error": message, "submission_performed": False, "remote_outcome": "NOT_PUBLISHED"})
                    item.update({"status": "failed", "error": message, "response": response})
                    _force_marketplace_listing_state(db, listing_id=listing.id, marketplace=marketplace, status=MarketplaceListingStatus.FAILED, response=response)
            else:
                message = str(bridge.get("error") or f"Hosted browser job ended with status {bridge_status}.")
                context_match = re.search(r"context=(\{.*\})", message)
                context = {}
                if context_match:
                    try:
                        context = json.loads(context_match.group(1))
                    except (TypeError, ValueError):
                        context = {}
                submission_state = str(context.get("submission_state") or "REMOTE_STATE_UNKNOWN").upper()
                if submission_state == "NOT_SUBMITTED":
                    response.update({
                        "bridge_poll_status": "failed",
                        "bridge_completion": bridge,
                        "error": message,
                        "submission_performed": False,
                        "remote_outcome": "NOT_PUBLISHED",
                    })
                    item.update({"status": "failed", "error": message, "response": response})
                    local_status = MarketplaceListingStatus.FAILED
                else:
                    # Without explicit pre-submit evidence, a failed hosted
                    # browser job may have crossed the marketplace submit
                    # edge. Keep it out of automatic replay until reconciled.
                    response.update({
                        "bridge_poll_status": "reconciliation_required",
                        "bridge_completion": bridge,
                        "error": message,
                        "submission_performed": bool(context.get("submission_performed")),
                        "remote_outcome": "REMOTE_STATE_UNKNOWN",
                        "remote_state": "REMOTE_STATE_UNKNOWN",
                    })
                    item.update({"status": "REMOTE_STATE_UNKNOWN", "error": message, "response": response})
                    local_status = MarketplaceListingStatus.PENDING
                _force_marketplace_listing_state(db, listing_id=listing.id, marketplace=marketplace, status=local_status, response=response)
        except Exception as exc:
            attempts = int(response.get("bridge_poll_attempts") or 0) + 1
            if attempts < 120:
                poll_again = True
                response.update({"bridge_poll_status": "pending", "bridge_poll_attempts": attempts, "bridge_poll_warning": str(exc)[:500]})
                item.update({"status": "browser_automation_pending", "response": response})
            else:
                message = "Hosted browser submission could not be reconciled after repeated status checks; do not replay until remote state is checked."
                response.update({"bridge_poll_status": "reconciliation_required", "bridge_poll_attempts": attempts, "bridge_poll_warning": str(exc)[:500], "remote_state": "REMOTE_STATE_UNKNOWN"})
                item.update({"status": "REMOTE_STATE_UNKNOWN", "error": message, "response": response})
                _force_marketplace_listing_state(db, listing_id=listing.id, marketplace=marketplace, status=MarketplaceListingStatus.PENDING, response=response)

        results[result_index] = item
        summary["results"] = results
        job.result_summary = summary
        pending = any(
            str(entry.get("status") or "").lower() == "browser_automation_pending"
            for entry in results if isinstance(entry, dict)
        )
        failed = any(str(entry.get("status") or "").lower() in {"failed", "remote_state_unknown"} for entry in results if isinstance(entry, dict))
        job.status = "running" if pending else "failed" if failed else "completed"
        job.last_error = next((str(entry.get("error")) for entry in results if isinstance(entry, dict) and entry.get("error") and str(entry.get("status") or "").lower() in {"failed", "remote_state_unknown"}), None)
        job.updated_at = datetime.now(UTC).replace(tzinfo=None)
        db.add(job)
        db.commit()
        batch_id = str(job.batch_id or (job.execution_plan or {}).get("bulk_batch_id") or "").strip()
        if batch_id:
            from app.services.marketplace_orchestrator import recompute_publish_batch
            recompute_publish_batch(db, batch_id)
            db.commit()
        outcome = str(item.get("status") or "unknown")
        if poll_again:
            try:
                poll_hosted_bridge_publish_task.apply_async(
                    args=[crosspost_job_id, listing_id, marketplace, bridge_job_id, execution_mode],
                    countdown=15,
                    priority=1,
                )
            except Exception as exc:
                logger.warning("hosted_bridge_publish_poll_reschedule_failed", extra={"crosspost_job_id": crosspost_job_id, "bridge_job_id": bridge_job_id, "error": str(exc)[:300]})
        return {"status": outcome, "poll_again": poll_again}


@celery_app.task(name="dispatch_pending_hosted_bridge_polls")
def dispatch_pending_hosted_bridge_polls_task(limit: int = 50) -> dict[str, Any]:
    """Recover bridge status polling after worker/broker restarts."""
    dispatched = 0
    with SessionLocal() as db:
        jobs = db.execute(
            select(MarketplaceCrosspostJob)
            .where(MarketplaceCrosspostJob.status == "running")
            .order_by(MarketplaceCrosspostJob.updated_at.asc())
            .limit(max(1, min(int(limit) * 10, 500)))
        ).scalars().all()
        for job in jobs:
            for item in ((job.result_summary or {}).get("results") or []):
                if not isinstance(item, dict) or str(item.get("status") or "").lower() != "browser_automation_pending":
                    continue
                response = item.get("response") if isinstance(item.get("response"), dict) else {}
                bridge_id = str(response.get("bridge_job_id") or "").strip()
                if not bridge_id:
                    continue
                try:
                    poll_hosted_bridge_publish_task.apply_async(
                        args=[job.id, job.listing_id, str(item.get("marketplace") or ""), bridge_id, str(item.get("execution_mode") or "hosted_browser_assist")],
                        priority=1,
                    )
                    dispatched += 1
                    if dispatched >= max(1, min(int(limit), 100)):
                        break
                except Exception:
                    logger.exception("hosted_bridge_publish_poll_recovery_failed", extra={"crosspost_job_id": job.id, "bridge_job_id": bridge_id})
            if dispatched >= max(1, min(int(limit), 100)):
                break
    return {"dispatched": dispatched}


@celery_app.task(name="dispatch_queued_crosspost_jobs")
def dispatch_queued_crosspost_jobs_task(limit: int = 25) -> dict[str, Any]:
    """Dispatch queued work; never replay an ambiguous running publish."""
    dispatched: list[dict[str, Any]] = []
    stale_task_cutoff = datetime.now(UTC).replace(tzinfo=None) - STALE_CROSSPOST_JOB_AFTER
    with SessionLocal() as db:
        stale_running = db.execute(
            select(MarketplaceCrosspostJob)
            .where(
                MarketplaceCrosspostJob.status == "running",
                MarketplaceCrosspostJob.updated_at < stale_task_cutoff,
            )
            .with_for_update(skip_locked=True)
            .limit(max(1, min(int(limit), 100)))
        ).scalars().all()
        for stale_job in stale_running:
            summary = dict(stale_job.result_summary or {}) if isinstance(stale_job.result_summary, dict) else {}
            summary["recovery"] = {
                "classification": "REMOTE_STATE_UNKNOWN",
                "reason": "Worker heartbeat expired after execution began; automatic replay is unsafe until remote state is reconciled.",
                "task_id": stale_job.task_id,
                "classified_at": datetime.now(UTC).isoformat(),
            }
            stale_job.status = "failed"
            stale_job.last_error = "Worker heartbeat expired after execution began. Remote state must be reconciled before retry; no automatic replay was scheduled."
            stale_job.result_summary = _json_safe(summary)
            db.add(stale_job)
        if stale_running:
            db.commit()
            from app.services.marketplace_orchestrator import recompute_publish_batch
            for stale_job in stale_running:
                batch_id = str(stale_job.batch_id or (stale_job.execution_plan or {}).get("bulk_batch_id") or "").strip()
                if batch_id:
                    recompute_publish_batch(db, batch_id)
            db.commit()

        for _ in range(max(1, min(int(limit), 100))):
            job = db.execute(
                select(MarketplaceCrosspostJob)
                .where(
                    MarketplaceCrosspostJob.status == "queued",
                    or_(
                        MarketplaceCrosspostJob.task_id.is_(None),
                        # A broker/result-store restart can leave a durable
                        # job pointing at a task that was never delivered.
                        # Duplicate deliveries are safe because the worker
                        # atomically claims queued->running before execution.
                        and_(
                            MarketplaceCrosspostJob.task_id.is_not(None),
                            MarketplaceCrosspostJob.updated_at < stale_task_cutoff,
                        ),
                    ),
                )
                .order_by(MarketplaceCrosspostJob.priority.asc(), MarketplaceCrosspostJob.created_at.asc(), MarketplaceCrosspostJob.id.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            ).scalars().first()
            if not job:
                break
            if _marketplace_jobs_queue_paused(db, int(job.user_id)):
                job.status = "paused"
                job.last_error = "Queue paused by operator; stale work was held instead of redispatched."
                db.add(job)
                db.commit()
                continue
            try:
                had_stale_task = bool(job.task_id)
                if had_stale_task:
                    job.task_id = None
                    job.last_error = "Recovered a stale queued delivery; worker will claim this job atomically before execution."
                    db.add(job)
                    db.flush()
                task = process_marketplace_crosspost_job_task.apply_async(
                    args=[job.id],
                    priority=max(0, min(10, int(job.priority or 1))),
                )
            except Exception as exc:
                db.rollback()
                logger.warning("crosspost_dispatch_deferred", extra={"job_id": job.id, "error": str(exc)[:500]})
                break
            job.task_id = task.id
            db.add(job)
            db.commit()
            dispatched.append({"job_id": job.id, "task_id": task.id, "recovered": had_stale_task})
    return {"dispatched": dispatched, "count": len(dispatched)}


@celery_app.task(name="repair_blocked_facebook_publish_targets")
def repair_blocked_facebook_publish_targets_task(limit: int = 25) -> dict[str, Any]:
    """Revalidate authorized Facebook targets blocked before any submit call.

    Only deterministic category/shipping preflight blocks with zero provider
    attempts are eligible. Failed or ambiguous submissions are never replayed.
    """
    repaired: list[int] = []
    with SessionLocal() as db:
        jobs = db.execute(
            select(MarketplaceCrosspostJob)
            .where(
                MarketplaceCrosspostJob.status.in_(["blocked", "failed"]),
                cast(MarketplaceCrosspostJob.target_marketplaces, Text).ilike("%facebook%"),
            )
            .order_by(MarketplaceCrosspostJob.created_at.asc(), MarketplaceCrosspostJob.id.asc())
            .with_for_update(skip_locked=True)
            # `limit` caps successful repairs, not candidate scanning. Limiting
            # the oldest generic failed jobs before checking eligibility meant
            # unrelated blockers could permanently starve newer FB targets.
            .limit(5000)
        ).scalars().all()
        for job in jobs:
            if len(repaired) >= max(1, min(int(limit), 100)):
                break
            targets = [str(value).lower() for value in (job.target_marketplaces or [])]
            if MarketplaceName.facebook.value not in targets:
                continue
            if _marketplace_jobs_queue_paused(db, int(job.user_id)):
                continue
            result_rows = (job.result_summary or {}).get("results") if isinstance(job.result_summary, dict) else []
            prior = next((row for row in result_rows or [] if isinstance(row, dict) and str(row.get("marketplace") or "").lower() == MarketplaceName.facebook.value), None)
            prior_response = prior.get("response") if isinstance(prior, dict) and isinstance(prior.get("response"), dict) else {}
            preflight = prior_response.get("preflight") if isinstance(prior_response.get("preflight"), dict) else {}
            recorded_blockers = preflight.get("blockers") if isinstance(preflight.get("blockers"), list) else prior_response.get("error")
            codes = {
                str(issue.get("code") or "").upper()
                for issue in (recorded_blockers or [])
                if isinstance(issue, dict)
            }
            if not codes or not codes.issubset({"FACEBOOK_CATEGORY_MISSING", "FACEBOOK_NO_SHIPPING_COMPATIBLE_CATEGORY"}):
                continue
            # The parent may be a combined eBay+Facebook job whose eBay half
            # already ran (possibly ambiguously).  Never reset or replay that
            # parent.  We may split only the Facebook target because its own
            # result proves it was blocked before any Facebook submission.
            if prior_response.get("provider_call_attempted") or prior_response.get("bridge_submission"):
                continue
            if targets == [MarketplaceName.facebook.value] and int(job.attempt_count or 0) > 0:
                continue
            listing = db.get(Listing, job.listing_id)
            if not listing:
                continue
            preflight_service = MarketplacePreflightService()
            fresh = preflight_service.preflight_listing(db, listing, MarketplaceName.facebook.value)
            preflight_service.cache_preflight_summary(db, listing, fresh)
            if fresh.get("blockers") or str(fresh.get("status") or "").lower() not in {"ready", "ready_with_warnings", "published"}:
                continue
            payload = build_marketplace_payload(listing, MarketplaceName.facebook.value)
            plan = dict(job.execution_plan or {}) if isinstance(job.execution_plan, dict) else {}
            plan.setdefault("automatic_preflight_repair_history", []).append({
                "marketplace": MarketplaceName.facebook.value,
                "prior_blocker_codes": sorted(codes),
                "repaired_at": datetime.now(UTC).isoformat(),
                "preflight_status": fresh.get("status"),
            })
            plan["preflight"] = _json_safe(fresh)
            plan["approved_execution_payload"] = _json_safe(payload)
            plan["preflight_payload_hash"] = compute_payload_hash(payload)
            recovery_summary = {
                "results": [],
                "automatic_preflight_repair": {
                    "prior_blocker_codes": sorted(codes),
                    "preflight_status": fresh.get("status"),
                    "source_job_id": job.id,
                    "scope": "facebook_only",
                },
            }
            if targets == [MarketplaceName.facebook.value]:
                # A one-marketplace job with zero attempts can be reset safely.
                job.execution_plan = plan
                job.status = "queued"
                job.task_id = None
                job.last_error = None
                job.result_summary = recovery_summary
                recovery_job = job
            else:
                # Split off the explicitly-authorized, definitely-unsubmitted
                # Facebook target.  The original combined job and its eBay
                # result (including REMOTE_STATE_UNKNOWN) remain unchanged.
                sibling_jobs = db.execute(
                    select(MarketplaceCrosspostJob).where(
                        MarketplaceCrosspostJob.batch_id == job.batch_id,
                        MarketplaceCrosspostJob.listing_id == job.listing_id,
                        MarketplaceCrosspostJob.status.in_(
                            ["queued", "running", "submitted_to_marketplace", "succeeded", "published"]
                        ),
                    ).order_by(MarketplaceCrosspostJob.id.desc())
                ).scalars().all()
                existing_fb_recovery = next(
                    (
                        sibling for sibling in sibling_jobs
                        if [str(value).lower() for value in (sibling.target_marketplaces or [])]
                        == [MarketplaceName.facebook.value]
                    ),
                    None,
                )
                if existing_fb_recovery:
                    continue
                recovery_job = MarketplaceCrosspostJob(
                    user_id=job.user_id,
                    listing_id=job.listing_id,
                    source_marketplace=job.source_marketplace,
                    target_marketplaces=[MarketplaceName.facebook.value],
                    requested_mode="operator_approved_facebook_preflight_recovery",
                    status="queued",
                    execution_plan=plan,
                    result_summary=recovery_summary,
                    priority=0,
                    requested_by=job.requested_by,
                    batch_id=job.batch_id,
                )
                db.add(recovery_job)
            db.flush()
            db.commit()
            try:
                task = process_marketplace_crosspost_job_task.apply_async(args=[recovery_job.id], priority=0)
                recovery_job.task_id = task.id
                db.add(recovery_job)
                db.commit()
                repaired.append(recovery_job.id)
            except Exception as exc:
                db.rollback()
                logger.warning("facebook_preflight_repair_dispatch_failed", extra={"crosspost_job_id": job.id, "error": str(exc)[:300]})
            batch_id = str(job.batch_id or (job.execution_plan or {}).get("bulk_batch_id") or "").strip()
            if batch_id:
                from app.services.marketplace_orchestrator import recompute_publish_batch
                recompute_publish_batch(db, batch_id)
                db.commit()
    return {"repaired_and_queued": repaired, "count": len(repaired)}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="process_marketplace_import_job",
)
def process_marketplace_import_job_task(self, job_id: int) -> dict:
    with SessionLocal() as db:
        job = db.get(MarketplaceImportJob, job_id)
        if not job:
            raise ValueError("Import job not found")
        user = db.get(User, job.user_id)
        if not user:
            job.status = "failed"
            job.last_error = "User not found"
            db.add(job)
            db.commit()
            raise ValueError("User not found")

        if str(job.status).lower() in {"canceled", "paused"}:
            return {"job_id": job_id, "status": str(job.status).lower()}

        if _marketplace_jobs_queue_paused(db, job.user_id):
            job.status = "paused"
            job.last_error = "Queue paused by operator before import execution."
            db.add(job); db.commit()
            return {"job_id": job_id, "status": "paused"}

        job.status = "running"
        db.add(job)
        db.commit()
        try:
            normalized = normalize_import_payload(
                source_marketplace=job.source_marketplace,
                payload=job.payload or {},
            )
            imported_payloads: list[dict] = []
            bridge_result: dict | None = None
            bridge_completion: dict | None = None
            db.expire_all()
            import_control = _import_job_control_state(db, job_id)
            if import_control in {"canceled", "paused"}:
                return {"job_id": job_id, "status": import_control}
            if job.source_marketplace == MarketplaceName.ebay.value:
                try:
                    imported_payloads = asyncio.run(
                        get_active_ebay_listings(
                            user.id,
                            db,
                            limit=int((job.payload or {}).get("max_listings") or 50),
                        )
                    )
                except EbayIntegrationError as exc:
                    raise RuntimeError(
                        _friendly_import_failure_message(
                            source_marketplace=job.source_marketplace,
                            error=exc,
                        )
                    ) from exc
                normalized = {
                    **normalized,
                    "imported_listing_count": len(imported_payloads),
                    "import_source": "ebay_api",
                }
            elif job.import_mode in {"provider_assist", "browser_assist"}:
                bridge_result = submit_bridge_job(
                    job_type="import",
                    execution_mode=job.import_mode,
                    payload={
                        "source_marketplace": job.source_marketplace,
                        "source_listing_reference": job.source_listing_reference,
                        "max_listings": (job.payload or {}).get("max_listings"),
                        "payload": job.payload or {},
                        "normalized_preview": normalized,
                    },
                )
                bridge_job = ((bridge_result.get("bridge_response") or {}) if isinstance(bridge_result, dict) else {})
                bridge_job_id = str(bridge_job.get("job_id") or "").strip()
                if bridge_job_id:
                    bridge_completion = wait_for_bridge_job(job_id=bridge_job_id, timeout_seconds=180, poll_interval_seconds=1.0)
                    completion_status = str(bridge_completion.get("status") or "").strip().lower()
                    if completion_status != "completed":
                        job.status = "failed"
                        job.last_error = str(bridge_completion.get("error") or f"Bridge import job finished with status '{completion_status}'")
                        job.normalized_preview = {
                            "bridge_submission": bridge_result,
                            "bridge_completion": bridge_completion,
                            "normalized_preview": normalized,
                        }
                        db.add(job)
                        db.commit()
                        return {"job_id": job.id, "status": "failed", "error": job.last_error}
                    bridge_payload = bridge_completion.get("result") or {}
                    imported_payloads = [
                        item for item in (bridge_payload.get("imported_listings") or []) if isinstance(item, dict)
                    ]
                    if imported_payloads:
                        normalized = {
                            **normalized,
                            "bridge_submission": bridge_result,
                            "bridge_completion": bridge_completion,
                            "imported_listing_count": len(imported_payloads),
                        }
                    else:
                        normalized = {
                            **normalized,
                            "bridge_submission": bridge_result,
                            "bridge_completion": bridge_completion,
                        }
                else:
                    normalized = {
                        **normalized,
                        "bridge_submission": bridge_result,
                    }
            created_listing_ids: list[int] = []
            reused_listing_ids: list[int] = []
            if imported_payloads:
                for imported_payload in imported_payloads:
                    normalized_item = normalize_import_payload(
                        source_marketplace=job.source_marketplace,
                        payload=imported_payload,
                    )
                    listing, created = _create_imported_listing(
                        db=db,
                        user_id=user.id,
                        source_marketplace=job.source_marketplace,
                        import_job_id=job.id,
                        import_mode=job.import_mode,
                        source_listing_reference=imported_payload.get("source_listing_reference") or imported_payload.get("source_url") or job.source_listing_reference,
                        raw_payload=imported_payload,
                        normalized=normalized_item,
                    )
                    if created:
                        created_listing_ids.append(listing.id)
                    else:
                        reused_listing_ids.append(listing.id)
                resolved_listing_ids = [*created_listing_ids, *reused_listing_ids]
                job.created_listing_id = resolved_listing_ids[0] if resolved_listing_ids else None
                job.normalized_preview = {
                    **(normalized if isinstance(normalized, dict) else {}),
                    "created_listing_ids": resolved_listing_ids,
                    "new_listing_ids": created_listing_ids,
                    "reused_listing_ids": reused_listing_ids,
                }
                db.add(job)
                db.commit()
                return {
                    "job_id": job.id,
                    "created_listing_ids": resolved_listing_ids,
                    "new_listing_ids": created_listing_ids,
                    "reused_listing_ids": reused_listing_ids,
                }

            job.normalized_preview = normalized
            db.expire_all()
            import_control = _import_job_control_state(db, job_id)
            if import_control in {"canceled", "paused"}:
                db.add(job)
                db.commit()
                return {"job_id": job_id, "status": import_control, "normalized_preview": normalized}

            listing, created = _create_imported_listing(
                db=db,
                user_id=user.id,
                source_marketplace=job.source_marketplace,
                import_job_id=job.id,
                import_mode=job.import_mode,
                source_listing_reference=job.source_listing_reference,
                raw_payload=job.payload or {},
                normalized=normalized,
            )

            job.created_listing_id = listing.id
            job.normalized_preview = {
                **(normalized if isinstance(normalized, dict) else {}),
                "created_listing_ids": [listing.id],
                "new_listing_ids": [listing.id] if created else [],
                "reused_listing_ids": [] if created else [listing.id],
            }
            job.status = "completed"
            db.add(job)
            db.commit()
            return {
                "job_id": job.id,
                "created_listing_id": listing.id,
                "new_listing_ids": [listing.id] if created else [],
                "reused_listing_ids": [] if created else [listing.id],
            }
        except Exception as exc:
            job.status = "failed"
            job.attempt_count = int(job.attempt_count or 0) + 1
            job.priority = min(10, int(job.priority or 1) + 1)
            job.next_attempt_at = datetime.now(UTC) + timedelta(minutes=min(120, 2 ** min(job.attempt_count, 6)))
            job.last_error = _friendly_import_failure_message(
                source_marketplace=job.source_marketplace,
                error=exc,
            )
            if not job.normalized_preview:
                job.normalized_preview = normalize_import_payload(
                    source_marketplace=job.source_marketplace,
                    payload=job.payload or {},
                )
            db.add(job)
            db.commit()
            return {"job_id": job.id, "status": "failed", "error": job.last_error}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
    name="sync_sold_everywhere",
)
def sync_sold_everywhere_task(self, listing_ids: list[int]) -> dict:
    # Reconcile sold or depleted inventory across connected marketplaces.
    processed: list[int] = []
    with SessionLocal() as db:
        query = select(Listing)
        if listing_ids:
            query = query.where(Listing.id.in_(listing_ids))
        listings = db.execute(query).scalars().all()
        for listing in listings:
            if listing.sold_at is None and int(listing.quantity or 0) > 0 and listing.sale_price is None:
                continue
            user = listing.user or db.get(User, listing.user_id)
            if not user:
                continue
            asyncio.run(
                sale_detection_service._fanout_quantity_adjustment(
                    db,
                    listing,
                    user,
                    sold_platform="manual",
                    quantity_sold=max(1, int(listing.quantity or 1)),
                    dry_run=False,
                    sale_amount=listing.sale_price,
                )
            )
            rows = db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing.id)).scalars().all()
            for row in rows:
                if row.status in {MarketplaceListingStatus.UPDATED, MarketplaceListingStatus.DELETED}:
                    row.raw_response = {**(row.raw_response or {}), "sold_sync": "reconciled"}
                    db.add(row)
            processed.append(listing.id)
        db.commit()

    return {"processed_listing_ids": processed, "count": len(processed)}


@celery_app.task(name="purge_expired_sold_media")
def purge_expired_sold_media_task(limit: int = 100) -> dict[str, Any]:
    """Purge sold-out listing media after a recoverable retention window.

    Sale reconciliation only queues retention metadata. This task is the sole
    automatic destructive media path: it waits 30 days unless the configured
    storage volume is under pressure, and never purges a listing with an
    active remote marketplace identity. Source intake photos are not touched;
    only the listing's materialized customer media references are cleared.
    """
    db = SessionLocal()
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        usage = shutil.disk_usage(settings.storage_root)
        pressure = bool(usage.total and (usage.free / usage.total) < 0.15)
        candidates = db.scalars(
            select(Listing)
            .where(
                Listing.sold_at.is_not(None),
                Listing.quantity <= 0,
                Listing.image_urls.is_not(None),
            )
            .order_by(Listing.sold_at.asc(), Listing.id.asc())
            .limit(max(1, int(limit or 100)) * 3)
        ).all()
        queued = due = purged = skipped_active = skipped_relisted = 0
        for listing in candidates:
            data = dict(listing.marketplace_data or {})
            retention = dict(data.get("media_retention") or {})
            if retention.get("status") == "purged":
                continue
            if not retention:
                # Backfill a recoverable queue record for historical sold-out
                # rows created before the retention workflow existed.  This
                # does not delete media; it makes the 30-day policy visible to
                # the next scheduled pass and to the operator queue UI.
                sold_at = listing.sold_at or now
                retention = {
                    "status": "queued",
                    "queued_at": (listing.sold_at or now).isoformat(),
                    "purge_after": (sold_at + timedelta(days=30)).isoformat(),
                    "reason": "historical_sold_out_backfill",
                }
                data["media_retention"] = retention
                listing.marketplace_data = data
                db.add(listing)
                db.commit()
            queued += 1
            if int(listing.quantity or 0) > 0:
                skipped_relisted += 1
                continue
            active_remote = db.scalar(
                select(MarketplaceListing.id).where(
                    MarketplaceListing.listing_id == listing.id,
                    MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
                ).limit(1)
            )
            if active_remote:
                skipped_active += 1
                continue
            purge_after = retention.get("purge_after")
            try:
                purge_at = datetime.fromisoformat(str(purge_after).replace("Z", "+00:00")).replace(tzinfo=None)
            except (TypeError, ValueError):
                purge_at = (listing.sold_at or now) + timedelta(days=30)
            if not pressure and now < purge_at:
                continue
            due += 1
            result = purge_listing_media(db, listing, clear_references=True)
            retention.update({
                "status": "purged",
                "purged_at": now.isoformat(),
                "purge_mode": "storage_pressure" if pressure and now < purge_at else "retention_expired",
                "cleanup": result,
            })
            data["media_retention"] = retention
            listing.marketplace_data = data
            db.add(listing)
            db.commit()
            purged += 1
            if purged >= max(1, int(limit or 100)):
                break
        return {
            "queued": queued,
            "due": due,
            "purged": purged,
            "skipped_active": skipped_active,
            "skipped_relisted": skipped_relisted,
            "storage_pressure": pressure,
            "free_bytes": int(usage.free),
            "total_bytes": int(usage.total),
        }
    finally:
        db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="poll_for_sales",
)
def poll_for_sales_task(self, dry_run: bool | None = None) -> dict:
    if not settings.sale_detection_enabled:
        logger.info("Sale detection polling disabled by config")
        return {"disabled": True, "processed_users": 0}

    resolved_dry_run = settings.sale_detection_dry_run if dry_run is None else dry_run
    with SessionLocal() as db:
        logger.info("Sale detection polling task started", extra={"dry_run": resolved_dry_run})
        result = sale_detection_service.poll_all_users(
            db,
            dry_run=resolved_dry_run,
            lookback_minutes=max(
                10,
                settings.sale_detection_poll_minutes + 5,
                settings.sale_detection_lookback_minutes,
            ),
        )
        logger.info("Sale detection polling task completed", extra=result)
        return result


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="sync_ebay_active_listings",
)
def sync_ebay_active_listings_task(self, user_id: int | None = None, limit: int = 1000) -> dict:
    with SessionLocal() as db:
        if user_id is not None:
            result = asyncio.run(sync_ebay_active_listings(user_id, db, limit=limit))
            logger.info("eBay sync task completed", extra=result)
            return result
        users = db.execute(select(User)).scalars().all()
        results = []
        for user in users:
            try:
                result = asyncio.run(sync_ebay_active_listings(user.id, db, limit=limit))
            except Exception as exc:  # pragma: no cover - logged for operator visibility
                logger.exception("eBay sync failed", extra={"user_id": user.id, "error": str(exc)})
                result = {"user_id": user.id, "status": "failed", "error": str(exc)}
            results.append(result)
        return {"processed_users": len(results), "results": results}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
    name="hydrate_ebay_history_media",
)
def hydrate_ebay_history_media_task(self, user_id: int | None = None, limit: int = 1000) -> dict:
    """Read-only recovery of missing images on imported eBay history rows."""
    with SessionLocal() as db:
        if user_id is not None:
            return asyncio.run(hydrate_ebay_history_media(int(user_id), db, limit=limit, only_missing=True))
        results = []
        for user in db.execute(select(User).order_by(User.id.asc())).scalars():
            try:
                results.append(asyncio.run(hydrate_ebay_history_media(user.id, db, limit=limit, only_missing=True)))
            except Exception as exc:  # pragma: no cover - keep other accounts moving
                logger.exception("eBay history media hydration failed", extra={"user_id": user.id, "error": str(exc)})
                results.append({"user_id": user.id, "status": "failed", "error": str(exc)})
        return {"processed_users": len(results), "results": results}


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


@celery_app.task(name="flag_stale_listings")
def flag_stale_listings_task() -> dict:
    stale_count = 0
    with SessionLocal() as db:
        listings = db.execute(select(Listing)).scalars().all()
        stale_cutoff = datetime.utcnow() - timedelta(days=inventory_service.STALE_AFTER_DAYS)
        for listing in listings:
            is_stale = listing.last_refreshed is None or listing.last_refreshed < stale_cutoff
            listing.stale_flag = is_stale
            stale_count += 1 if is_stale else 0
            db.add(listing)
        db.commit()
    return {"processed": len(listings), "stale": stale_count}


@celery_app.task(name="bulk_process_inventory_chunk")
def bulk_process_inventory_chunk(job_id: str, action: str, payload: dict, listing_ids: list[int], batch_index: int = 0) -> dict:
    processed = 0
    errors: list[dict] = []
    with SessionLocal() as db:
        job = db.get(BulkJob, job_id)
        if not job:
            return {"processed": 0, "errors": [{"message": "Bulk job not found"}]}
        job.status = "running"
        db.add(job)
        # The durable BulkJob is the tenant boundary for worker execution;
        # never trust a stale/mutated chunk to reach another user's listing.
        listings = db.execute(
            select(Listing).where(
                Listing.user_id == job.user_id,
                Listing.id.in_(listing_ids),
            )
        ).scalars().all()
        for listing in listings:
            try:
                if action == "edit":
                    inventory_service.update_listing_inventory(
                        listing,
                        quantity=payload.get("quantity"),
                        platform_quantities=payload.get("platform_quantities"),
                    )
                elif action == "delist":
                    inventory_service.update_listing_inventory(listing, delist=True)
                elif action == "relist":
                    inventory_service.update_listing_inventory(listing, relist=True)
                elif action == "label":
                    inventory_service.update_listing_inventory(
                        listing,
                        labels_to_add=payload.get("add_labels"),
                        labels_to_remove=payload.get("remove_labels"),
                    )
                elif action == "mark_sold":
                    inventory_service.update_listing_inventory(
                        listing,
                        mark_sold=True,
                        sale_price=payload.get("sale_price"),
                    )
                elif action in {"refresh", "autobump"}:
                    listing.last_refreshed = datetime.utcnow()
                    listing.stale_flag = False
                elif action == "repair_recovery_copy":
                    if str(listing.source_type or "").strip() != "media_inventory_recovery":
                        raise ValueError("Only media_inventory_recovery listings can be repaired by this action")
                    current_shipping = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
                    marketplace_data = dict(listing.marketplace_data or {})
                    quality_summary = dict(marketplace_data.get("quality_summary") or {})
                    title = str(listing.title or listing.suggested_title or "Recovered inventory item").strip()
                    shipping = derive_shipping_profile(
                        listing={
                            "title": title,
                            "description": listing.description or "",
                            "listing_price": listing.listing_price or listing.suggested_price or listing.buy_it_now_price or listing.estimated_value,
                        },
                        item_specifics=listing.item_specifics or {},
                        existing=current_shipping,
                    )
                    cleaned_description = _product_listing_description(
                        title=title,
                        listing=listing,
                        shipping_profile=shipping,
                    )
                    suggestion, suggestion_reason = suggest_category_from_text(
                        title,
                        cleaned_description,
                        listing.category_suggestion,
                        " ".join((listing.tags or [])[:8]),
                    )
                    source = dict(listing.source_metadata or {})
                    recovery = dict(source.get("recovery") or {})
                    recovery["copy_repair"] = {
                        "description_rewritten": cleaned_description != (listing.description or ""),
                        "category_refresh_reason": suggestion_reason,
                        "shipping_rule": "buyer_pays_under_10" if shipping.get("buyer_pays_shipping") else "seller_pays_10_and_over",
                    }
                    source["recovery"] = recovery
                    quality_summary["ready_for_publish_queue"] = True
                    quality_summary["status"] = "needs_review"
                    marketplace_data["quality_summary"] = quality_summary
                    if listing.description != cleaned_description:
                        listing.description = cleaned_description
                    if _needs_category_refresh(listing.category_suggestion) and suggestion and suggestion != "Other > Needs category review":
                        listing.category_suggestion = suggestion
                    if listing.shipping_profile != shipping:
                        listing.shipping_profile = shipping
                    listing.needs_review = True
                    listing.marketplace_data = marketplace_data
                    listing.source_metadata = source
                else:
                    raise ValueError(f"Unsupported action: {action}")
                if payload.get("marketplaces"):
                    data = listing.marketplace_data or {}
                    data["scheduled_refresh"] = {
                        "marketplaces": payload.get("marketplaces"),
                        "requested_at": datetime.utcnow().isoformat(),
                    }
                    listing.marketplace_data = data
                db.add(listing)
                processed += 1
            except (InventorySafetyError, ValueError) as exc:
                errors.append({"listing_id": listing.id, "error": str(exc)})

        db.commit()
        job.processed_items = min(job.total_items, (job.processed_items or 0) + processed + len(errors))
        existing_errors = job.errors or []
        job.errors = [*existing_errors, *errors]
        job.error_count = len(job.errors or [])
        db.add(job)
        db.commit()

    return {"processed": processed, "errors": errors, "batch_index": batch_index}


@celery_app.task(name="bulk_finalize_job")
def bulk_finalize_job(results: list[dict], job_id: str) -> dict:
    with SessionLocal() as db:
        job = db.get(BulkJob, job_id)
        if not job:
            return {"job_id": job_id, "status": "missing"}
        total_errors = sum(len(result.get("errors") or []) for result in (results or []))
        job.status = "completed_with_errors" if total_errors else "completed"
        job.error_count = total_errors
        job.processed_items = job.total_items
        db.add(job)
        db.commit()
        return {
            "job_id": job_id,
            "status": job.status,
            "processed_items": job.processed_items,
            "total_items": job.total_items,
            "errors": total_errors,
        }




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

        readiness = canonical_listing_readiness(listing, marketplace=MarketplaceName.ebay.value)
        if not readiness.get("publishable"):
            logger.warning("Autonomous publish blocked by canonical readiness", extra={"listing_id": listing_id, "blocking_reasons": readiness.get("blocking_reasons")})
            return {
                "listing_id": listing_id,
                "status": "BLOCKED_READINESS",
                "blocking_reasons": readiness.get("blocking_reasons") or [],
                "canonical_readiness": readiness,
            }

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
            ebay_result = multi_platform_publisher.publish(db, listing, MarketplaceName.ebay.value).response
            listing.status = ListingStatus.PUBLISHED
            user = db.get(User, listing.user_id)
            enabled_platforms = get_enabled_platforms(user)
            crosspost_targets = [market for market in enabled_platforms if market != MarketplaceName.ebay.value] if settings.autonomous_crosspost_enabled else []
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
                enriched = service.enrich_photo(listing.raw_photo_path, db=db, user_id=listing.user_id)
                listing.title = enriched.get("title") or listing.title
                if not _preserve_existing_description(listing):
                    listing.description = enriched.get("description") or listing.description
                listing.category_id = enriched.get("category_id")
                listing.category_suggestion = enriched.get("category_suggestion")
                listing.tags = enriched.get("tags")
                listing.item_specifics = enriched.get("item_specifics")
                listing.estimated_value = enriched.get("estimated_value")
                listing.status = "PROCESSED"
                listing.last_refreshed = datetime.utcnow()
                listing.platform_quantities = listing.platform_quantities or {"inventory": max(1, int(listing.quantity or 1))}
                listing.marketplace_data = {
                    **(listing.marketplace_data or {}),
                    "ingestion_source": "autonomous_photo_batch",
                    "sale_detection_ready": True,
                }
                db.add(listing)
                processed += 1
                logger.info("Photo enrichment complete", extra={"listing_id": listing.id, "status": listing.status})
                # Use the process settings snapshot.  Reloading from the
                # environment here made tests and recovery runs unexpectedly
                # re-enable autonomous publishing, then block on Redis after
                # the listing itself had already been processed.
                runtime_settings = settings
                if runtime_settings.autonomous_mode:
                    logger.info(
                        "Queueing autonomous publish from photo pipeline",
                        extra={"listing_id": listing.id, "dry_run": runtime_settings.autonomous_dry_run},
                    )
                    autonomous_publish.delay(listing.id, dry_run=runtime_settings.autonomous_dry_run)
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
    retry_jitter=True,
    max_retries=3,
    name="process_storage_unit_listing",
)
def process_storage_unit_listing(self, listing_id: int, batch_id: int) -> dict:
    service = PhotoEnrichmentService()
    with SessionLocal() as db:
        listing = db.get(Listing, listing_id)
        batch = db.get(StorageUnitBatch, batch_id)
        if not listing or not batch:
            raise ValueError("Listing or batch not found")
        if not listing.raw_photo_path:
            listing.status = ListingStatus.FAILED
            batch.processed_items += 1
            db.add_all([listing, batch])
            db.commit()
            return {"listing_id": listing_id, "status": "FAILED"}
        try:
            try:
                enriched = service.enrich_photo(listing.raw_photo_path, db=db, user_id=listing.user_id)
            except TypeError as exc:
                # Keep compatibility with lightweight providers/test doubles
                # that implement the original path-only enrichment contract.
                if "unexpected keyword argument" not in str(exc):
                    raise
                enriched = service.enrich_photo(listing.raw_photo_path)
            listing.title = enriched.get("title") or listing.title
            if not _preserve_existing_description(listing):
                listing.description = enriched.get("description") or listing.description
            listing.category_id = enriched.get("category_id")
            listing.category_suggestion = enriched.get("category_suggestion")
            listing.tags = enriched.get("tags")
            listing.item_specifics = enriched.get("item_specifics")
            listing.estimated_value = enriched.get("estimated_value")
            listing.status = ListingStatus.PROCESSED
            listing.last_refreshed = datetime.utcnow()
            listing.platform_quantities = listing.platform_quantities or {"inventory": max(1, int(listing.quantity or 1))}
            listing.marketplace_data = {
                **(listing.marketplace_data or {}),
                "ingestion_source": "autonomous_storage_batch",
                "sale_detection_ready": True,
            }
            runtime_settings = settings
            if runtime_settings.autonomous_mode:
                autonomous_publish.delay(listing.id, dry_run=runtime_settings.autonomous_dry_run)
            else:
                auto_price_listing.delay(listing.id)
            batch.processed_items += 1
            db.add_all([listing, batch])
            db.commit()
            return {"listing_id": listing_id, "status": "PROCESSED"}
        except Exception as exc:
            listing.status = ListingStatus.FAILED
            batch.processed_items += 1
            db.add_all([listing, batch])
            db.commit()
            logger.exception("Storage unit listing processing failed", extra={"listing_id": listing_id, "error": str(exc)})
            raise


@celery_app.task(name="finalize_storage_unit_batch")
def finalize_storage_unit_batch(results: list[dict], batch_id: int) -> dict:
    with SessionLocal() as db:
        batch = db.get(StorageUnitBatch, batch_id)
        if not batch:
            return {"batch_id": batch_id, "status": "MISSING"}
        failed_count = sum(1 for result in results if result.get("status") == "FAILED")
        batch.status = "FAILED" if failed_count else "COMPLETED"
        db.add(batch)
        db.commit()
        return {"batch_id": batch_id, "status": batch.status, "processed": batch.processed_items, "total": batch.total_items}


def enqueue_storage_unit_batch_pipeline(batch_id: int, listing_ids: list[int]):
    workflow = chord(
        group(process_storage_unit_listing.s(listing_id, batch_id) for listing_id in listing_ids),
        finalize_storage_unit_batch.s(batch_id),
    )
    return workflow.apply_async()


@celery_app.task(name="process_overnight_storage_batches")
def process_overnight_storage_batches() -> dict:
    started = 0
    with SessionLocal() as db:
        batches = db.execute(
            select(StorageUnitBatch).where(
                StorageUnitBatch.overnight_mode.is_(True),
                StorageUnitBatch.status.in_(["INGESTED", "QUEUED"]),
            )
        ).scalars().all()
        for batch in batches:
            listing_ids = [listing.id for listing in batch.listings]
            if not listing_ids:
                continue
            batch.status = "PROCESSING"
            async_result = enqueue_storage_unit_batch_pipeline(batch.id, listing_ids)
            batch.pipeline_task_id = async_result.id
            db.add(batch)
            started += 1
        db.commit()
    return {"started": started}


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


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=3,
    name="send_personalized_offers",
)
def send_personalized_offers_task(self) -> dict:
    checked_accounts = 0
    sent = 0
    skipped = 0
    candidates = 0
    with SessionLocal() as db:
        accounts = db.execute(
            select(MarketplaceAccount).where(MarketplaceAccount.marketplace == MarketplaceName.ebay)
        ).scalars().all()
        service = OfferService()
        for account in accounts:
            checked_accounts += 1
            result = service.send_personalized_offers(db, account, force=False)
            candidates += result["candidates"]
            sent += result["sent"]
            skipped += result["skipped"]
    return {"accounts_checked": checked_accounts, "candidates": candidates, "sent": sent, "skipped": skipped}
