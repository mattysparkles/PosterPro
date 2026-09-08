from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
from datetime import date, datetime, timedelta, UTC
from pathlib import Path
from typing import Any

from celery import chord, group
from sqlalchemy import select, update as sql_update, case

from app.core.config import settings
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
    User,
)
from app.services.listing_workspace import normalize_marketplace_data
from app.services.listing_ai import ListingAIService
from app.services.category_rules import suggest_category_from_text
from app.services.listing_review import derive_condition_data, derive_shipping_profile, normalize_listing_images
from app.services.listing_processing import ListingProcessingService
from app.services.marketplace_execution import resolve_execution_mode
from app.services.marketplace_field_mapper import build_marketplace_payload, normalize_import_payload
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.automation_bridge import submit_bridge_job, wait_for_bridge_job, get_bridge_asset, AutomationBridgeError
from app.services.secondary_marketplace_execution import execute_secondary_marketplace_path
from app.services.inventory_service import InventorySafetyError, InventoryService
from app.services.storage import LocalStorage
from app.services.analytics_service import AnalyticsService
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.photo_enrichment import PhotoEnrichmentService
from app.services.pricing_service import PricingService
from app.services.multi_platform_publisher import get_enabled_platforms, multi_platform_publisher, upsert_marketplace_listing
from app.services.offer_service import OfferService
from app.services.sale_detection_service import SaleDetectionService
from app.services.intake_slate import IntakeSlateService
from app.services.ai_guard import release_waiting_for_reset, recover_stale_reservations
from app.services.vine_import_service import VineImportService, VINE_IMAGE_BACKFILL_CUTOFF
from app.services.ebay_service import EbayIntegrationError, get_active_ebay_listings, sync_ebay_active_listings, revise_ebay_listing
from app.services.ebay_service import search_ebay_categories, get_or_refresh_account, build_ebay_item_specifics, get_required_item_specifics, verify_ebay_category
from app.workers.celery_app import celery_app
from app.services.clustering import cluster_embeddings
from app.services.rate_limiter import rate_limiter
from scripts.repair_recovery_draft_copy import _needs_category_refresh, _product_listing_description

logger = logging.getLogger(__name__)

STALE_IMPORT_JOB_AFTER = timedelta(minutes=20)

sale_detection_service = SaleDetectionService()
inventory_service = InventoryService()

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
                    if str(listing.source_type or "").lower() == "amazon_vine" and isinstance(evidence, dict):
                        from app.services.listing_ai import build_listing_description
                        candidate_description = build_listing_description(
                            title=str(evidence.get("title") or evidence.get("product_name") or listing.title or "Item"),
                            item_specifics=listing.item_specifics or {},
                            source_label="Amazon/Vine product facts",
                            source_metadata={"recovery": {"identity": evidence}},
                        )
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
                    suggestion = " ".join(str(v).strip() for v in (evidence_title, evidence_type, listing.title) if str(v or "").strip()) or listing.category_suggestion or job.operator_note
                    account = db.execute(select(MarketplaceAccount).where(MarketplaceAccount.user_id == job.user_id, MarketplaceAccount.marketplace == MarketplaceName.ebay)).scalars().first()
                    try:
                        candidates = asyncio.run(search_ebay_categories(str(suggestion or listing.title or ""), account)) if account else []
                    except Exception as exc:
                        candidates = []; category_blocked = True; job.result = {"category_state": "CATEGORY_LOOKUP_FAILED", "error": str(exc)[:500]}
                    query_terms = set(re.findall(r"[a-z0-9]+", f"{listing.title or ''} {job.operator_note or ''}".lower()))
                    ranked = sorted(candidates, key=lambda c: len(query_terms & set(re.findall(r"[a-z0-9]+", str(c.get('category_name') or '').lower()))), reverse=True)
                    top_score = len(query_terms & set(re.findall(r"[a-z0-9]+", str((ranked[0] if ranked else {}).get('category_name') or '').lower()))) if ranked else 0
                    next_score = len(query_terms & set(re.findall(r"[a-z0-9]+", str((ranked[1] if len(ranked)>1 else {}).get('category_name') or '').lower()))) if len(ranked)>1 else -1
                    if ranked and ranked[0].get("category_id") and ranked[0].get("publishable") is True and (len(ranked) == 1 or top_score > next_score):
                        chosen = ranked[0]; cid = str(chosen["category_id"])
                        verification = asyncio.run(verify_ebay_category(account, cid, str(chosen.get("taxonomy_tree_id") or "0"))) if account else {"verified": False, "publishable": False}
                        if not verification.get("verified") or not verification.get("publishable"):
                            category_blocked = True; job.result = {**(job.result or {}), "category_state": "CATEGORY_NON_LEAF" if verification.get("verified") else "CATEGORY_LOOKUP_FAILED"}
                        else:
                            listing.category_id = cid; listing.category_suggestion = chosen.get("category_name") or suggestion; path = chosen.get("breadcrumb") or chosen.get("category_tree") or chosen.get("category_name")
                            listing.source_metadata = {**source_meta, "category_path": path, "category_name": chosen.get("category_name"), "taxonomy_tree_id": chosen.get("taxonomy_tree_id"), "leaf_verified": True, "publishable": True, "category_provenance": "CORRECTION_TAXONOMY", "category_candidates": ranked};
                            changed["category"] = {"before": {"category_id": before.get("category_id"), "path": source_meta.get("category_path")}, "after": {"category_id": cid, "path": path}, "validation": "taxonomy_leaf_verified"}
                        if verification.get("verified") and verification.get("publishable"):
                            try:
                                aspect_payload = asyncio.run(get_required_item_specifics(account.access_token, cid))
                                required_names = [str(a.get("localizedAspectName") or "").strip() for a in (aspect_payload.get("aspects") or []) if (a.get("aspectConstraint") or {}).get("aspectRequired")]
                                specifics = asyncio.run(build_ebay_item_specifics(listing, account, cid)); known = dict(listing.item_specifics or {}); known.update({k: v for k, v in specifics.items() if v}); listing.item_specifics = known
                                missing = [name for name in required_names if not known.get(name)]
                                changed["aspects"] = {"before": before.get("item_specifics"), "after": known, "required": required_names, "missing_required": missing};
                                if missing: category_blocked = True; job.result = {**(job.result or {}), "category_state": "CATEGORY_FIXED_ASPECTS_MISSING", "missing_aspects": missing}
                            except Exception as exc:
                                category_blocked = True; job.failure_reason = f"ASPECT_REQUIRED: {exc}"
                    else:
                        category_blocked = True
                        job.result = {"category_state": "NEEDS_OPERATOR_REVIEW", "category_candidates": ranked[:10]}
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


@celery_app.task(name="resume_incomplete_listings", bind=True, max_retries=None)
def resume_incomplete_listings_task(self, user_id: int | None = None, limit: int = 25, dry_run: bool = False) -> dict[str, Any]:
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
        )
        remaining = int(result.get("queued", 0)) + int(result.get("processing", 0)) + int(result.get("retrying", 0))
        if settings.historical_backlog_auto_resume_enabled and remaining > 0 and not dry_run:
            self.apply_async(args=[user_id, limit, dry_run], countdown=15)
        return result
    finally:
        db.close()


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
        if settings.historical_backlog_auto_resume_enabled and remaining > 0 and not dry_run:
            self.apply_async(args=[user_id, limit, dry_run], countdown=30)
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
        if settings.historical_backlog_auto_resume_enabled and remaining > 0 and not dry_run:
            self.apply_async(args=[user_id, limit, dry_run], countdown=45)
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


def _crosspost_job_canceled(db, job_id: int) -> bool:
    job = db.get(MarketplaceCrosspostJob, job_id)
    return bool(job and str(job.status).lower() == "canceled")


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


def _import_job_canceled(db, job_id: int) -> bool:
    job = db.get(MarketplaceImportJob, job_id)
    return bool(job and str(job.status).lower() == "canceled")


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
    return listing, True


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


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="process_marketplace_crosspost_job",
)
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

        if str(job.status).lower() == "canceled":
            return {"job_id": job_id, "status": "canceled"}

        job.status = "running"
        db.add(job)
        db.commit()

        results: list[dict] = []
        failed_markets: list[str] = []
        targets = job.target_marketplaces or []
        for market in targets:
            db.expire_all()
            if _crosspost_job_canceled(db, job_id):
                return {"job_id": job_id, "status": "canceled", "results": results}
            operation = str((job.execution_plan or {}).get("operation") or "create").lower()
            if operation == "update" and str(market).lower() == "ebay":
                try:
                    revised = asyncio.run(revise_ebay_listing(listing, db))
                    results.append({"marketplace": market, "execution_mode": "direct_api", "operation": "UPDATE", "status": "UPDATED", "response": revised})
                except Exception as exc:
                    failed_markets.append(market)
                    results.append({"marketplace": market, "execution_mode": "direct_api", "operation": "UPDATE", "status": "failed", "error": str(exc)})
                continue
            if operation == "update":
                # Assisted marketplaces do not yet expose a safe revise
                # contract. Never fall through to CREATE for a manual update;
                # preserve the canonical save and surface an actionable retry.
                failed_markets.append(market)
                results.append({"marketplace": market, "execution_mode": "unsupported_update", "operation": "UPDATE", "status": "failed", "error": "UPDATE_NOT_SUPPORTED_FOR_MARKETPLACE"})
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
            preflight = _json_safe(MarketplacePreflightService().preflight_listing(db, listing, market))
            if preflight.get("blockers"):
                failed_markets.append(market)
                response = {"preflight": preflight, "error": "Publish blocked by marketplace preflight."}
                upsert_marketplace_listing(db, listing_id=listing.id, marketplace=market, status=MarketplaceListingStatus.FAILED, response=response)
                _force_marketplace_listing_state(db, listing_id=listing.id, marketplace=market, status=MarketplaceListingStatus.FAILED, response=response)
                results.append({"marketplace": market, "execution_mode": execution_mode, "status": "blocked", "response": response})
                continue
            if listing.status not in {ListingStatus.ready, ListingStatus.posted}:
                listing.status = ListingStatus.ready
                listing.needs_review = False
                db.add(listing)
                db.commit()
            payload = build_marketplace_payload(listing, market)
            if execution_mode == "direct_api":
                result = multi_platform_publisher.publish(db, listing, market)
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
                    try:
                        bridge_completion = wait_for_bridge_job(
                            job_id=bridge_job_id,
                            timeout_seconds=600,
                            poll_interval_seconds=1.0,
                        )
                        response = {
                            **response,
                            "bridge_completion": bridge_completion,
                        }
                        completion_status = str(bridge_completion.get("status") or "").strip().lower()
                        if completion_status != "completed":
                            error_message = str(
                                bridge_completion.get("error")
                                or f"Bridge cross-post job finished with status '{completion_status}'"
                            )
                        else:
                            bridge_result = bridge_completion.get("result") if isinstance(bridge_completion.get("result"), dict) else {}
                            result_status = str(bridge_result.get("status") or response.get("status") or "planned")
                            submitted_to_marketplace = bool(bridge_result.get("submitted")) or str(result_status).strip().lower() in {"submitted_to_marketplace", "published"}
                            listing_urls = bridge_result.get("listing_urls")
                            if not isinstance(listing_urls, list):
                                listing_urls = []
                            marketplace_listing_id = str(bridge_result.get("marketplace_listing_id") or "").strip() or None
                            submission_visible = bool(marketplace_listing_id or listing_urls)
                            facebook_needs_visibility = market == "facebook" and execution_mode == "browser_assist"
                            if submitted_to_marketplace and (submission_visible or not facebook_needs_visibility):
                                listing_status = MarketplaceListingStatus.PUBLISHED
                                response = {
                                    **(response if isinstance(response, dict) else {}),
                                    "status": result_status,
                                    "bridge_completion": bridge_completion,
                                    "submitted": True,
                                }
                                if marketplace_listing_id:
                                    response["marketplace_listing_id"] = marketplace_listing_id
                                if listing_urls:
                                    response["listing_urls"] = listing_urls
                            elif submitted_to_marketplace and facebook_needs_visibility:
                                response = {
                                    **(response if isinstance(response, dict) else {}),
                                    "status": result_status,
                                    "bridge_completion": bridge_completion,
                                    "submitted": True,
                                    "bridge_confirmation_status": "submitted_without_visible_listing",
                                }
                    except Exception as exc:
                        bridge_error = str(exc)
                        if execution_mode == "browser_assist" and (
                            "bridge job fetch failed" in bridge_error.lower()
                            or "did not finish within" in bridge_error.lower()
                        ):
                            response = {
                                **(response if isinstance(response, dict) else {}),
                                "bridge_fetch_status": "pending",
                                "bridge_fetch_warning": bridge_error,
                            }
                            result_status = str(response.get("status") or "BROWSER_AUTOMATION_READY")
                            listing_status = MarketplaceListingStatus.PENDING
                            error_message = None
                        else:
                            error_message = bridge_error

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
        if _crosspost_job_canceled(db, job_id):
            return {"job_id": job_id, "status": "canceled", "results": results}
        job.status = "failed" if failed_markets else "completed"
        job.last_error = f"Cross-post execution failed for: {', '.join(failed_markets)}" if failed_markets else None
        job.attempt_count = int(job.attempt_count or 0) + 1
        if failed_markets:
            # Lower numeric priority is higher priority; demote automatic
            # failures so a bounded retry cannot starve fresh operator work.
            job.priority = min(10, int(job.priority or 1) + 1)
            job.next_attempt_at = datetime.now(UTC) + timedelta(minutes=min(120, 2 ** min(job.attempt_count, 6)))
        job.result_summary = {"results": results}
        revision_id = (job.execution_plan or {}).get("revision_id")
        if revision_id:
            revision_row = db.get(ListingRevision, int(revision_id))
            if revision_row:
                revision_row.marketplace_results = {str(r.get("marketplace")): r for r in results}
                revision_row.status = "failed" if failed_markets else "completed"
                revision_row.sync_state = "partially_synced" if failed_markets and results else ("update_failed" if failed_markets else "synced")
                db.add(revision_row)
        db.add(job)
        db.commit()
        return {"job_id": job_id, "status": job.status, "results": results}


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

        if str(job.status).lower() == "canceled":
            return {"job_id": job_id, "status": "canceled"}

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
            if _import_job_canceled(db, job_id):
                return {"job_id": job_id, "status": "canceled"}
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
                job.status = "completed"
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
            if _import_job_canceled(db, job_id):
                db.add(job)
                db.commit()
                return {"job_id": job_id, "status": "canceled", "normalized_preview": normalized}

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
            lookback_minutes=max(10, settings.sale_detection_poll_minutes + 5),
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
def sync_ebay_active_listings_task(self, user_id: int | None = None, limit: int = 100) -> dict:
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
                enriched = service.enrich_photo(listing.raw_photo_path)
                listing.title = enriched.get("title") or listing.title
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
                if settings.autonomous_mode:
                    logger.info(
                        "Queueing autonomous publish from photo pipeline",
                        extra={"listing_id": listing.id, "dry_run": settings.autonomous_dry_run},
                    )
                    autonomous_publish.delay(listing.id, dry_run=settings.autonomous_dry_run)
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
            enriched = service.enrich_photo(listing.raw_photo_path)
            listing.title = enriched.get("title") or listing.title
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
            if settings.autonomous_mode:
                autonomous_publish.delay(listing.id, dry_run=settings.autonomous_dry_run)
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
