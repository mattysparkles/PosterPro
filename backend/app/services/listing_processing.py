from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import re
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.enums import ListingStatus
from app.models.models import Listing, ListingProcessingEvent, User, VineImportItem
from app.services.category_rules import suggest_category_from_text
from app.services.listing_ai import ListingAIService, build_listing_description, build_marketplace_title
from app.services.listing_provenance import is_human_owned_field, mark_field_provenance
from app.services.listing_review import derive_shipping_profile, summarize_listing_readiness, sync_listing_review_state
from app.services.listing_specificity import assess_listing_specificity, classify_listing_reviewability, is_bare_identifier_title, is_caption_like_title
from app.services.photo_enrichment import PhotoEnrichmentService
from app.services.pricing_research_service import compute_listing_quality_summary
from app.services.process_notifications import create_process_notification
from app.services.vine_import_service import VineImportService
from scripts.repair_recovery_draft_copy import _needs_category_refresh, _product_listing_description

TARGET_SOURCE_TYPES = {"amazon_vine", "media_inventory_recovery"}
PROCESSING_COMPLETE = "complete"
PROCESSING_PROCESSING = "processing"
PROCESSING_RETRY = "retry"
PROCESSING_BLOCKED = "blocked"
PROCESSING_NEEDS_ATTENTION = "needs_attention"
PROCESSING_QUEUED = "queued"


def _now() -> datetime:
    return datetime.now(UTC)


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _generic_placeholder(text: str | None) -> bool:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return True
    if is_caption_like_title(normalized) or is_bare_identifier_title(normalized):
        return True
    markers = {
        "this item is part of a media inventory recovery",
        "this item is part of a media inventory recovery. details are limited.",
        "details are limited",
        "recovered photographed inventory item requiring identity review",
        "recovered photographed inventory item",
        "recovered inventory item",
        "marketplace listing built from the item details",
        "product listing built from the item details",
        "review the attached photos",
        "needs review",
        "general resale",
        "other > needs category review",
        "collectibles > cameras",
    }
    return any(marker in normalized for marker in markers)


def _has_meaningful_recovery_identity(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    if isinstance(identity, dict):
        for field in ("title", "brand", "model", "mpn", "identifier", "product_name", "packaging_identity"):
            if not _generic_placeholder(identity.get(field)):
                return True
    for field in ("title", "brand", "model", "mpn", "identifier", "product_name", "packaging_identity", "category", "description"):
        if not _generic_placeholder(payload.get(field)):
            return True
    item_specifics = payload.get("item_specifics") if isinstance(payload.get("item_specifics"), dict) else {}
    for field in ("Brand", "Model", "MPN", "Product Type", "Type", "Pack", "Pack Size", "Part Number"):
        if not _generic_placeholder(item_specifics.get(field)):
            return True
    if isinstance(payload.get("identity_candidates"), list) and payload.get("identity_candidates"):
        return True
    if str(payload.get("quality_gate") or "").strip().lower() in {"trusted_for_draft", "needs_measurement_review"}:
        return True
    return False


def _recovery_payloads(source_metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    payloads: list[dict[str, Any]] = []
    for key in ("identity", "full_group_evidence_v2", "full_group_evidence_v3", "image_identity_v1"):
        payload = recovery.get(key)
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _best_recovery_payload_with_key(source_metadata: dict[str, Any] | None) -> tuple[str | None, dict[str, Any]]:
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    for key in ("identity", "full_group_evidence_v3", "full_group_evidence_v2", "image_identity_v1"):
        payload = recovery.get(key)
        if isinstance(payload, dict) and _has_meaningful_recovery_identity(payload):
            return key, payload
    return None, {}


def _best_recovery_payload(source_metadata: dict[str, Any] | None) -> dict[str, Any]:
    for payload in _recovery_payloads(source_metadata):
        if _has_meaningful_recovery_identity(payload):
            return payload
    return {}


def _best_recovery_payload_from_merged_group(
    db: Session,
    *,
    listing: Listing,
    source_metadata: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any]]:
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    merged_group_id = recovery.get("merged_into_recovery_group_id")
    merged_item_id = recovery.get("merged_into_recovery_item_id")
    if merged_group_id is None and merged_item_id is None:
        return None, {}

    query = (
        select(Listing)
        .where(
            Listing.user_id == listing.user_id,
            Listing.source_type == "media_inventory_recovery",
            Listing.id != listing.id,
        )
        .order_by(Listing.updated_at.desc(), Listing.id.desc())
    )

    for sibling in db.execute(query).scalars().all():
        sibling_source_metadata = sibling.source_metadata if isinstance(sibling.source_metadata, dict) else {}
        sibling_recovery = sibling_source_metadata.get("recovery") if isinstance(sibling_source_metadata.get("recovery"), dict) else {}
        sibling_group_id = sibling_recovery.get("merged_into_recovery_group_id")
        sibling_item_id = sibling_recovery.get("merged_into_recovery_item_id")
        if merged_group_id is not None and str(sibling_group_id or "").strip() != str(merged_group_id).strip():
            continue
        if merged_item_id is not None and str(sibling_item_id or "").strip() != str(merged_item_id).strip():
            continue
        key, payload = _best_recovery_payload_with_key(sibling_source_metadata)
        if key and _has_meaningful_recovery_identity(payload):
            return f"merged_group:{key}", payload
    return None, {}


def _merge_recovery_identity(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    merged: dict[str, Any] = {}
    if isinstance(identity, dict):
        merged.update(identity)
    merged.update({key: value for key, value in payload.items() if key != "identity"})
    return merged


def _recovery_title_mismatch(current_title: str | None, recovery_title: str | None) -> bool:
    current = _normalize_text(current_title).lower()
    recovery = _normalize_text(recovery_title).lower()
    if not current or not recovery:
        return False
    if current == recovery:
        return False
    current_words = [word for word in re.split(r"[^a-z0-9]+", current) if len(word) > 2]
    recovery_words = [word for word in re.split(r"[^a-z0-9]+", recovery) if len(word) > 2]
    if not recovery_words:
        return False
    overlap = sum(1 for word in recovery_words if word in current_words)
    return overlap / max(len(recovery_words), 1) < 0.5


def _recovery_category_mismatch(current_category: str | None, recovery_category: str | None) -> bool:
    current = _normalize_text(current_category).lower()
    recovery = _normalize_text(recovery_category).lower()
    if not current or not recovery:
        return False
    if current == recovery:
        return False
    current_words = [word for word in re.split(r"[^a-z0-9]+", current) if len(word) > 2]
    recovery_words = [word for word in re.split(r"[^a-z0-9]+", recovery) if len(word) > 2]
    if not recovery_words:
        return False
    overlap = sum(1 for word in recovery_words if word in current_words)
    return overlap / max(len(recovery_words), 1) < 0.5


def _recovery_text_mismatch(current_text: str | None, recovery_text: str | None) -> bool:
    current = _normalize_text(current_text).lower()
    recovery = _normalize_text(recovery_text).lower()
    if not current or not recovery:
        return False
    if current == recovery:
        return False
    return True


def _recovery_specifics_mismatch(current_specifics: dict[str, Any] | None, recovery_specifics: dict[str, Any] | None) -> bool:
    current_specifics = current_specifics if isinstance(current_specifics, dict) else {}
    recovery_specifics = recovery_specifics if isinstance(recovery_specifics, dict) else {}
    if not recovery_specifics:
        return False
    def _normalized_map(values: dict[str, Any]) -> dict[str, Any]:
        return {
            _normalize_text(key).lower(): value
            for key, value in values.items()
            if _normalize_text(key)
        }

    current_map = _normalized_map(current_specifics)
    recovery_map = _normalized_map(recovery_specifics)
    watched_groups = (
        ("brand",),
        ("model",),
        ("mpn", "identifier", "part number", "manufacturer sku"),
        ("type", "product type", "product_name", "product name", "packaging identity", "packaging_identity"),
    )
    for aliases in watched_groups:
        recovery_value = next((recovery_map.get(alias) for alias in aliases if not _generic_placeholder(recovery_map.get(alias))), None)
        if _generic_placeholder(recovery_value):
            continue
        current_value = next((current_map.get(alias) for alias in aliases if alias in current_map), None)
        if _generic_placeholder(current_value):
            return True
        if _normalize_text(current_value).lower() != _normalize_text(recovery_value).lower():
            return True
    return False


def _apply_recovery_specifics(
    *,
    listing: Listing,
    source_metadata: dict[str, Any],
    recovery_provenance: str,
    recovery_specifics: dict[str, Any],
    inferred_specifics: dict[str, Any],
) -> bool:
    if is_human_owned_field(source_metadata, "item_specifics"):
        return False
    current_specifics = dict(listing.item_specifics or {}) if isinstance(listing.item_specifics, dict) else {}
    merged = dict(current_specifics)
    changed = False
    for specifics in (recovery_specifics, inferred_specifics):
        if not isinstance(specifics, dict):
            continue
        for key, value in specifics.items():
            value_text = _normalize_text(value)
            if not value_text or _generic_placeholder(value_text):
                continue
            if _generic_placeholder(merged.get(key)) or _normalize_text(merged.get(key)).lower() != value_text.lower():
                merged[key] = value_text
                changed = True
    preferred_type = None
    for specifics in (recovery_specifics, inferred_specifics):
        if not isinstance(specifics, dict):
            continue
        for alias in ("Type", "type", "Product Type", "product type", "product_type"):
            candidate_text = _normalize_text(specifics.get(alias))
            if candidate_text and not _generic_placeholder(candidate_text):
                preferred_type = candidate_text
                break
        if preferred_type:
            break
    if preferred_type:
        existing_type = _normalize_text(merged.get("Type"))
        if not existing_type or existing_type.lower() != preferred_type.lower():
            merged["Type"] = preferred_type
            changed = True
        legacy_type = _normalize_text(merged.get("type"))
        if not legacy_type or legacy_type.lower() != preferred_type.lower():
            merged["type"] = preferred_type
            changed = True
    if changed:
        listing.item_specifics = merged
        source_metadata = mark_field_provenance(source_metadata, field="item_specifics", provenance=recovery_provenance)
        listing.source_metadata = source_metadata
    return changed


def _repairable_blocker_reason(reason: str | None, *, source_type: str | None = None) -> bool:
    text = _normalize_text(reason).lower()
    if not text:
        return False
    if any(
        marker in text
        for marker in (
            "generic_or_underspecified_title",
            "missing_specific_identifier_evidence",
            "generic_or_placeholder_category",
            "no images attached",
            "primary image not set",
            "can't compare offset-naive and offset-aware datetimes",
            "offset-naive and offset-aware datetimes",
        )
    ):
        return True
    source = (source_type or "").strip().lower()
    if source == "amazon_vine" and "image" in text:
        return True
    if source == "media_inventory_recovery" and any(token in text for token in ("title", "identifier", "category", "image")):
        return True
    return False


def _resolve_local_media_path(media_ref: str) -> str | None:
    raw = _normalize_text(media_ref)
    if not raw:
        return None
    if raw.startswith("/media/"):
        from app.core.config import settings
        from pathlib import Path

        return str((Path(settings.storage_root).resolve() / raw.removeprefix("/media/")).resolve())
    if raw.startswith("storage/"):
        from app.core.config import settings
        from pathlib import Path

        return str((Path(settings.storage_root).resolve() / raw.removeprefix("storage/")).resolve())
    if raw.startswith("./storage/"):
        from app.core.config import settings
        from pathlib import Path

        return str((Path(settings.storage_root).resolve() / raw.removeprefix("./storage/")).resolve())
    return raw if "/" in raw else None


class ListingProcessingService:
    """Resume stalled listing-level enrichment and review promotion.

    This service works on already-created listings instead of the batch-only
    intake pipeline so historical Vine and Google Photos rows can keep
    advancing even when they are orphaned from the original ingestion batch.
    """

    def __init__(self) -> None:
        self.listing_ai = ListingAIService()
        self.photo_enrichment = PhotoEnrichmentService()
        self.vine_service = VineImportService()

    def backlog_summary(self, db: Session, *, user_id: int | None = None) -> dict[str, Any]:
        query = select(Listing).where(Listing.source_type.in_(sorted(TARGET_SOURCE_TYPES)))
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        rows = db.execute(query).scalars().all()

        summary = {
            "queued": 0,
            "processing": 0,
            "retrying": 0,
            "needs_attention": 0,
            "complete_with_blockers": 0,
            "blocked": 0,
            "failed": 0,
            "complete": 0,
            "needs_review": 0,
            "total": len(rows),
            "oldest_queued": None,
            "last_success_at": None,
            "source_breakdown": defaultdict(int),
            "stale_processing": 0,
            "blocker_breakdown": defaultdict(int),
        }
        now = _now()
        oldest_queued: datetime | None = None
        last_success: datetime | None = None
        for listing in rows:
            state = str(listing.processing_state or PROCESSING_QUEUED).strip().lower()
            summary["source_breakdown"][str(listing.source_type or "unknown").strip().lower()] += 1
            if listing.needs_review:
                summary["needs_review"] += 1
            if state == PROCESSING_PROCESSING:
                summary["processing"] += 1
                lease = _ensure_aware(listing.processing_lease_expires_at)
                if lease is not None and lease < now:
                    summary["stale_processing"] += 1
            elif state == PROCESSING_RETRY:
                summary["retrying"] += 1
            elif state in {PROCESSING_BLOCKED, PROCESSING_NEEDS_ATTENTION}:
                summary["needs_attention"] += 1
            elif state == PROCESSING_COMPLETE:
                summary["complete"] += 1
                if _normalize_text(listing.processing_blocking_reason):
                    summary["complete_with_blockers"] += 1
            elif state == "failed":
                summary["failed"] += 1
            else:
                summary["queued"] += 1
                next_retry = _ensure_aware(listing.processing_next_retry_at)
                if next_retry is None or next_retry <= now:
                    updated_at = _ensure_aware(listing.updated_at)
                    if oldest_queued is None or (updated_at and updated_at < oldest_queued):
                        oldest_queued = updated_at
            last_success_candidate = _ensure_aware(listing.processing_last_success_at)
            if last_success_candidate and (last_success is None or last_success_candidate > last_success):
                last_success = last_success_candidate
            reason = _normalize_text(listing.processing_blocking_reason)
            if state in {PROCESSING_BLOCKED, PROCESSING_NEEDS_ATTENTION, "failed"} and reason:
                summary["blocker_breakdown"][reason] += 1
            if state == PROCESSING_COMPLETE and reason:
                summary["blocker_breakdown"][reason] += 1
        summary["oldest_queued"] = oldest_queued.isoformat() if oldest_queued else None
        summary["last_success_at"] = last_success.isoformat() if last_success else None
        summary["source_breakdown"] = dict(summary["source_breakdown"])
        summary["blocker_breakdown"] = [
            {"reason": reason, "count": count}
            for reason, count in sorted(summary["blocker_breakdown"].items(), key=lambda item: (-item[1], item[0]))
        ]
        summary["stalled"] = bool(summary["queued"] or summary["processing"] or summary["retrying"]) and (
            last_success is None or (now - last_success) > timedelta(minutes=30)
        )
        return summary

    def _recovery_requires_refresh(self, listing: Listing) -> bool:
        if str(listing.source_type or "").strip().lower() != "media_inventory_recovery":
            return False
        source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
        recovery_key, recovery_evidence = _best_recovery_payload_with_key(source_metadata)
        if not recovery_key or not recovery_evidence:
            return False
        recovery_identity = _merge_recovery_identity(recovery_evidence) if _has_meaningful_recovery_identity(recovery_evidence) else {}
        recovery_specifics = recovery_evidence.get("item_specifics") if isinstance(recovery_evidence.get("item_specifics"), dict) else {}
        if recovery_identity and not is_human_owned_field(source_metadata, "title"):
            recovery_title = str(recovery_identity.get("title") or "").strip()
            if recovery_title and (_generic_placeholder(listing.title) or _recovery_title_mismatch(listing.title, recovery_title)):
                return True
        if recovery_evidence and not is_human_owned_field(source_metadata, "category_suggestion"):
            recovery_category = str(recovery_evidence.get("category") or "").strip()
            if recovery_category and (_needs_category_refresh(listing.category_suggestion) or _recovery_category_mismatch(listing.category_suggestion, recovery_category)):
                return True
        if recovery_evidence and not is_human_owned_field(source_metadata, "description"):
            recovery_description = str(recovery_evidence.get("description") or "").strip()
            if recovery_description and (_generic_placeholder(listing.description) or _recovery_text_mismatch(listing.description, recovery_description)):
                return True
        if not is_human_owned_field(source_metadata, "item_specifics") and _recovery_specifics_mismatch(listing.item_specifics, recovery_specifics):
            return True
        if not is_human_owned_field(source_metadata, "item_specifics") and recovery_identity:
            inferred_specifics: dict[str, Any] = {}
            for source_field, target_field in (
                ("brand", "Brand"),
                ("model", "Model"),
                ("mpn", "MPN"),
                ("identifier", "MPN"),
                ("product_type", "Type"),
                ("packaging_identity", "Type"),
            ):
                value = recovery_identity.get(source_field)
                if not _generic_placeholder(value):
                    inferred_specifics[target_field] = str(value).strip()
            if _recovery_specifics_mismatch(listing.item_specifics, {**recovery_specifics, **inferred_specifics}):
                return True
        return False

    def requeue_repairable_blocked_listings(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        source_types: set[str] | None = None,
        limit: int = 250,
    ) -> dict[str, Any]:
        source_types = source_types or TARGET_SOURCE_TYPES
        query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
            or_(
                Listing.processing_state.in_([PROCESSING_BLOCKED, PROCESSING_NEEDS_ATTENTION, "failed"]),
                and_(
                    Listing.processing_state == PROCESSING_COMPLETE,
                    Listing.processing_blocking_reason.is_not(None),
                    Listing.processing_blocking_reason != "",
                ),
            ),
        )
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        query = query.order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, limit))
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)

        examined = 0
        requeued = 0
        reasons: defaultdict[str, int] = defaultdict(int)
        for listing in db.execute(query).scalars().all():
            examined += 1
            blocker_reason = _normalize_text(listing.processing_blocking_reason)
            if not blocker_reason or not _repairable_blocker_reason(blocker_reason, source_type=listing.source_type):
                continue
            listing.processing_state = PROCESSING_QUEUED
            listing.processing_stage = "recovery_requeue"
            listing.processing_next_retry_at = None
            listing.processing_lease_expires_at = None
            listing.processing_last_error = None
            listing.processing_error_stage = None
            listing.processing_blocking_reason = None
            db.add(listing)
            requeued += 1
            reasons[blocker_reason] += 1
        if requeued:
            db.commit()
        return {
            "examined": examined,
            "requeued": requeued,
            "source_types": sorted(source_types),
            "reasons": [{"reason": reason, "count": count} for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))],
        }

    def resume_backlog(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        limit: int = 25,
        worker_id: str = "listing-backfill",
        dry_run: bool = False,
        source_types: set[str] | None = None,
    ) -> dict[str, Any]:
        source_types = source_types or TARGET_SOURCE_TYPES
        now = _now()
        self._requeue_now_reviewable_blocked_listings(db, user_id=user_id, source_types=source_types, limit=max(1, limit * 5))
        query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
        )
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        query = query.order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, limit * 3))
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        candidates = []
        for listing in db.execute(query).scalars().all():
            if self._is_terminal(listing):
                continue
            state = str(listing.processing_state or PROCESSING_QUEUED).strip().lower()
            if state in {PROCESSING_QUEUED, PROCESSING_PROCESSING, PROCESSING_RETRY}:
                candidates.append(listing)
                continue
            next_retry = _ensure_aware(listing.processing_next_retry_at)
            if state in {PROCESSING_BLOCKED, PROCESSING_NEEDS_ATTENTION, "failed"} and next_retry and next_retry <= now:
                candidates.append(listing)
                continue
            if state == PROCESSING_COMPLETE and self._recovery_requires_refresh(listing):
                candidates.append(listing)
        candidates = candidates[: max(1, limit)]
        results: list[dict[str, Any]] = []
        for listing in candidates:
            try:
                result = self.resume_listing(db, listing=listing, worker_id=worker_id, dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                fresh = db.get(Listing, listing.id)
                if fresh is None:
                    continue
                self._mark_listing_failure(
                    db,
                    listing=fresh,
                    stage="resume_backlog",
                    error=exc,
                    worker_id=worker_id,
                    retryable=False,
                )
                results.append({"listing_id": listing.id, "status": PROCESSING_BLOCKED, "error": str(exc)})
                continue
            if not dry_run and result.get("ready_for_review"):
                fresh = db.get(Listing, listing.id)
                if fresh is not None and not fresh.needs_review:
                    fresh.needs_review = True
                    if fresh.status == ListingStatus.draft:
                        fresh.status = ListingStatus.PROCESSED
                    fresh.processing_state = PROCESSING_COMPLETE
                    fresh.processing_last_success_at = fresh.processing_last_success_at or _now()
                    fresh.processing_next_retry_at = None
                    fresh.processing_lease_expires_at = None
                    db.add(fresh)
                    db.commit()
            if not dry_run:
                self._finalize_review_ready_listing(db, listing_id=listing.id, worker_id=worker_id)
            results.append(result)

        # Claims are committed per-listing in the helper, but this final commit
        # ensures any lingering event rows or notifications are durable.
        db.commit()
        summary = self.backlog_summary(db, user_id=user_id)
        summary.update(
            {
                "processed": len(results),
                "results": results,
                "worker_id": worker_id,
                "dry_run": dry_run,
                "limit": max(1, limit),
                "source_types": sorted(source_types),
            }
        )
        return summary

    def resume_image_identification_backlog(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        limit: int = 8,
        worker_id: str = "image-identification",
        dry_run: bool = False,
        source_types: set[str] | None = None,
    ) -> dict[str, Any]:
        source_types = source_types or {"media_inventory_recovery"}
        now = _now()
        query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
            Listing.processing_stage == "image_identification",
            or_(
                Listing.processing_state.in_([PROCESSING_NEEDS_ATTENTION, PROCESSING_BLOCKED, PROCESSING_QUEUED, PROCESSING_RETRY]),
                and_(Listing.processing_state.is_(None), Listing.processing_next_retry_at.is_(None)),
            ),
            or_(
                Listing.processing_blocking_reason == "needs_image_identification",
                Listing.processing_blocking_reason == "insufficient_identity_evidence",
                Listing.processing_blocking_reason == "generic_or_caption_identity",
                Listing.processing_blocking_reason.ilike("%missing_specific_identifier_evidence%"),
            ),
            or_(Listing.processing_next_retry_at.is_(None), Listing.processing_next_retry_at <= now),
        )
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        query = query.order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, limit))
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        candidates = [listing for listing in db.execute(query).scalars().all() if not self._is_terminal(listing)]
        processed: list[dict[str, Any]] = []
        for listing in candidates:
            try:
                result = self.resume_listing(
                    db,
                    listing=listing,
                    worker_id=worker_id,
                    dry_run=dry_run,
                    allow_image_identification=True,
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                fresh = db.get(Listing, listing.id)
                if fresh is None:
                    continue
                self._mark_listing_failure(
                    db,
                    listing=fresh,
                    stage="image_identification",
                    error=exc,
                    worker_id=worker_id,
                    retryable=True,
                    blocking_reason="needs_image_identification",
                )
                processed.append({"listing_id": listing.id, "status": PROCESSING_RETRY, "error": str(exc)})
                continue
            processed.append({"listing_id": listing.id, **result})
        db.commit()
        summary = self.backlog_summary(db, user_id=user_id)
        summary.update(
            {
                "processed": len(processed),
                "results": processed,
                "worker_id": worker_id,
                "dry_run": dry_run,
                "limit": max(1, limit),
                "source_types": sorted(source_types),
                "stage": "image_identification",
            }
        )
        return summary

    def resume_product_research_backlog(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        limit: int = 8,
        worker_id: str = "product-research",
        dry_run: bool = False,
        source_types: set[str] | None = None,
    ) -> dict[str, Any]:
        source_types = source_types or TARGET_SOURCE_TYPES
        query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            Listing.needs_review.is_(True),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
        )
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        query = query.order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, limit * 4))
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        candidates = []
        for listing in db.execute(query).scalars().all():
            if self._is_terminal(listing):
                continue
            reviewability = classify_listing_reviewability(
                title=listing.title,
                description=listing.description,
                category=listing.category_suggestion,
                item_specifics=listing.item_specifics if isinstance(listing.item_specifics, dict) else {},
                source_metadata=listing.source_metadata if isinstance(listing.source_metadata, dict) else {},
                has_images=bool(listing.image_urls or []),
            )
            if reviewability.get("reviewability_class") == "pass":
                continue
            candidates.append((listing, reviewability))
            if len(candidates) >= max(1, limit):
                break
        processed: list[dict[str, Any]] = []
        for listing, reviewability in candidates:
            try:
                result = self.resume_listing(
                    db,
                    listing=listing,
                    worker_id=worker_id,
                    dry_run=dry_run,
                    allow_image_identification=True,
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                fresh = db.get(Listing, listing.id)
                if fresh is None:
                    continue
                self._mark_listing_failure(
                    db,
                    listing=fresh,
                    stage="product_research",
                    error=exc,
                    worker_id=worker_id,
                    retryable=False,
                    blocking_reason="product_research_failed",
                )
                processed.append({"listing_id": listing.id, "status": PROCESSING_BLOCKED, "error": str(exc)})
                continue
            processed.append({"listing_id": listing.id, "reviewability_class": reviewability.get("reviewability_class"), **result})
        db.commit()
        summary = self.backlog_summary(db, user_id=user_id)
        summary.update(
            {
                "processed": len(processed),
                "results": processed,
                "worker_id": worker_id,
                "dry_run": dry_run,
                "limit": max(1, limit),
                "source_types": sorted(source_types),
                "stage": "product_research",
            }
        )
        return summary

    def _requeue_now_reviewable_blocked_listings(
        self,
        db: Session,
        *,
        user_id: int | None,
        source_types: set[str],
        limit: int,
    ) -> int:
        query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
            Listing.processing_state.in_([PROCESSING_BLOCKED, PROCESSING_NEEDS_ATTENTION, "failed"]),
            or_(Listing.processing_next_retry_at.is_(None), Listing.processing_next_retry_at <= _now()),
        )
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        query = query.order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, limit))
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        requeued = 0
        for listing in db.execute(query).scalars().all():
            if self._is_terminal(listing):
                continue
            source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
            recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
            image_identity = recovery.get("image_identity_v1") if isinstance(recovery.get("image_identity_v1"), dict) else {}
            recovery_evidence = _best_recovery_payload(source_metadata)
            grouping_review = any(
                str(source.get("quality_gate") or "").strip().lower() == "needs_grouping_review"
                or str(source.get("group_kind") or "").strip().lower() == "multiple_unrelated_products"
                for source in (image_identity, recovery_evidence)
                if isinstance(source, dict)
            )
            if grouping_review:
                continue
            readiness = summarize_listing_readiness(
                listing_images=listing.listing_images,
                condition_data=listing.condition_data,
                shipping_profile=listing.shipping_profile,
                listing={
                    "source_type": listing.source_type,
                    "category_id": listing.category_id,
                    "category_suggestion": listing.category_suggestion,
                    "listing_price": listing.listing_price,
                    "suggested_price": listing.suggested_price,
                },
            )
            pricing = {}
            if isinstance(listing.marketplace_data, dict):
                pricing = listing.marketplace_data.get("pricing_analysis") if isinstance(listing.marketplace_data.get("pricing_analysis"), dict) else {}
            quality = compute_listing_quality_summary(listing, pricing_analysis=pricing)
            if grouping_review or quality.get("ready_for_publish_queue") or (quality.get("specificity_status") == "trusted_for_draft" and not (readiness.get("blockers") or [])):
                listing.processing_state = PROCESSING_QUEUED
                listing.processing_stage = "grouping_review" if grouping_review else "recovery_requeue"
                listing.processing_next_retry_at = None
                listing.processing_lease_expires_at = None
                listing.processing_blocking_reason = None
                listing.processing_last_error = None
                listing.processing_error_stage = None
                db.add(listing)
                requeued += 1
        if requeued:
            db.commit()
        return requeued

    def resume_listing(
        self,
        db: Session,
        *,
        listing: Listing,
        worker_id: str,
        dry_run: bool = False,
        allow_image_identification: bool = False,
    ) -> dict[str, Any]:
        now = _now()
        if self._is_terminal(listing):
            return {
                "listing_id": listing.id,
                "source_type": listing.source_type,
                "status": "skipped_terminal",
                "processing_state": listing.processing_state,
            }
        next_retry_at = _ensure_aware(listing.processing_next_retry_at)
        if next_retry_at and next_retry_at > now:
            return {
                "listing_id": listing.id,
                "source_type": listing.source_type,
                "status": "deferred",
                "processing_state": listing.processing_state,
                "next_retry_at": next_retry_at.isoformat(),
            }
        lease_expires_at = _ensure_aware(listing.processing_lease_expires_at)
        if listing.processing_state == PROCESSING_PROCESSING and lease_expires_at and lease_expires_at > now:
            return {
                "listing_id": listing.id,
                "source_type": listing.source_type,
                "status": "leased",
                "processing_state": listing.processing_state,
            }

        if not dry_run:
            self._mark_processing_started(db, listing=listing, worker_id=worker_id, stage="inspect")

        repair_result: dict[str, Any] = {"listing_id": listing.id, "source_type": listing.source_type}
        source_type = str(listing.source_type or "").strip().lower()
        current_readiness = summarize_listing_readiness(
            listing_images=listing.listing_images,
            condition_data=listing.condition_data,
            shipping_profile=listing.shipping_profile,
            listing={
                "source_type": listing.source_type,
                "category_id": listing.category_id,
                "category_suggestion": listing.category_suggestion,
                "listing_price": listing.listing_price,
                "suggested_price": listing.suggested_price,
            },
        )
        current_quality = compute_listing_quality_summary(
            listing,
            pricing_analysis=(
                listing.marketplace_data.get("pricing_analysis")
                if isinstance(listing.marketplace_data, dict) and isinstance(listing.marketplace_data.get("pricing_analysis"), dict)
                else {}
            ),
        )
        polish_needed = any(
            _generic_placeholder(value)
            for value in (listing.title, listing.description, listing.category_suggestion)
        )
        recovery_title_guard = False
        if source_type == "media_inventory_recovery":
            preview_recovery = _best_recovery_payload(listing.source_metadata if isinstance(listing.source_metadata, dict) else {})
            if not _has_meaningful_recovery_identity(preview_recovery):
                _, preview_recovery = _best_recovery_payload_from_merged_group(
                    db,
                    listing=listing,
                    source_metadata=listing.source_metadata if isinstance(listing.source_metadata, dict) else {},
                )
            preview_recovery_identity = _merge_recovery_identity(preview_recovery) if _has_meaningful_recovery_identity(preview_recovery) else {}
            recovery_title_guard = _recovery_title_mismatch(listing.title, preview_recovery_identity.get("title"))
        if not dry_run and current_quality.get("ready_for_publish_queue") and not polish_needed and not recovery_title_guard and not recovery_refinement_needed:
            refreshed = db.get(Listing, listing.id) or listing
            refreshed.readiness_summary = current_readiness
            refreshed.marketplace_data = {
                **(refreshed.marketplace_data or {}),
                "quality_summary": current_quality,
            }
            refreshed.needs_review = True
            refreshed.status = ListingStatus.PROCESSED if refreshed.status == ListingStatus.draft else refreshed.status
            self._mark_listing_complete(
                db,
                listing=refreshed,
                worker_id=worker_id,
                stage="quality_gate",
                message="Listing was already ready for human review.",
                details={"readiness": current_readiness, "quality": current_quality, "skipped_repair": True},
            )
            db.add(refreshed)
            db.commit()
            return {
                **repair_result,
                "status": PROCESSING_COMPLETE,
                "ready_for_review": True,
                "blocking": [],
                "warnings": current_readiness.get("warnings") or [],
                "skipped_repair": True,
            }
        if source_type == "amazon_vine":
            repair_result.update(self._resume_vine_listing(db, listing=listing, worker_id=worker_id, dry_run=dry_run))
        elif source_type == "media_inventory_recovery":
            repair_result.update(
                self._resume_recovery_listing(
                    db,
                    listing=listing,
                    worker_id=worker_id,
                    dry_run=dry_run,
                    allow_image_identification=allow_image_identification,
                )
            )
        else:
            repair_result.update(self._resume_generic_listing(db, listing=listing, worker_id=worker_id, dry_run=dry_run))

        if not dry_run and repair_result.get("grouping_review"):
            blocker_reason = str(repair_result.get("grouping_review_reason") or "photo_grouping_review_required")
            self._mark_listing_failure(
                db,
                listing=listing,
                stage="grouping_review",
                error=RuntimeError(blocker_reason),
                worker_id=worker_id,
                retryable=False,
                blocking_reason=blocker_reason,
                details={"warnings": ["Review the grouped photos and split or merge the item boundaries before retrying."], "image_identity": (repair_result.get("image_identity") or {})},
            )
            listing.processing_state = PROCESSING_NEEDS_ATTENTION
            listing.processing_next_retry_at = None
            listing.processing_lease_expires_at = None
            db.add(listing)
            db.commit()
            return {
                **repair_result,
                "status": PROCESSING_NEEDS_ATTENTION,
                "ready_for_review": False,
                "blocking": [blocker_reason],
                "warnings": ["Review the grouped photos and split or merge the item boundaries before retrying."],
            }

        refreshed = db.get(Listing, listing.id) or listing
        if not dry_run:
            if source_type == "media_inventory_recovery":
                refreshed_metadata = refreshed.source_metadata if isinstance(refreshed.source_metadata, dict) else {}
                refreshed_recovery = refreshed_metadata.get("recovery") if isinstance(refreshed_metadata.get("recovery"), dict) else {}
                refreshed_recovery_evidence = (
                    refreshed_recovery.get("full_group_evidence_v3")
                    if isinstance(refreshed_recovery.get("full_group_evidence_v3"), dict)
                    else {}
                )
                refreshed_image_identity = (
                    refreshed_recovery.get("image_identity_v1")
                    if isinstance(refreshed_recovery.get("image_identity_v1"), dict)
                    else {}
                )
                if _generic_placeholder(refreshed.title) and not repair_result.get("recovery_identity_meaningful"):
                    self._mark_listing_needs_attention(
                        db,
                        listing=refreshed,
                        stage="image_identification",
                        worker_id=worker_id,
                        blocking_reason="generic_or_caption_identity",
                        message="Item still has a generic caption-style identity and needs stronger recovery evidence before review.",
                        details={"warnings": ["Item still has a generic caption-style identity and needs stronger recovery evidence before review."]},
                    )
                    db.commit()
                    return {
                        **repair_result,
                        "status": PROCESSING_NEEDS_ATTENTION,
                        "ready_for_review": False,
                        "blocking": ["generic_or_caption_identity"],
                        "warnings": ["Item still has a generic caption-style identity and needs stronger recovery evidence before review."],
                    }
                if _generic_placeholder(refreshed.title) and not _has_meaningful_recovery_identity(refreshed_recovery_evidence) and not _has_meaningful_recovery_identity(refreshed_image_identity):
                    self._mark_listing_needs_attention(
                        db,
                        listing=refreshed,
                        stage="image_identification",
                        worker_id=worker_id,
                        blocking_reason="needs_image_identification",
                        message="Item still needs image-based identification before it can become review-ready.",
                        details={"warnings": ["Item still needs image-based identification before it can become review-ready."]},
                    )
                    db.commit()
                    return {
                        **repair_result,
                        "status": PROCESSING_NEEDS_ATTENTION,
                        "ready_for_review": False,
                        "blocking": ["needs_image_identification"],
                        "warnings": ["Item still needs image-based identification before it can become review-ready."],
                    }
            if repair_result.get("needs_image_identification"):
                blocker_reason = str(repair_result.get("needs_image_identification_reason") or "needs_image_identification")
                self._mark_listing_needs_attention(
                    db,
                    listing=refreshed,
                    stage="image_identification",
                    worker_id=worker_id,
                    blocking_reason=blocker_reason,
                    message="Item still needs image-based identification before it can become review-ready.",
                    details={"warnings": ["Item still needs image-based identification before it can become review-ready."]},
                )
                db.commit()
                return {
                    **repair_result,
                    "status": PROCESSING_NEEDS_ATTENTION,
                    "ready_for_review": False,
                    "blocking": [blocker_reason],
                    "warnings": ["Item still needs image-based identification before it can become review-ready."],
                }
            if source_type == "amazon_vine" and _needs_category_refresh(refreshed.category_suggestion):
                category, _ = suggest_category_from_text(
                    refreshed.title or "",
                    refreshed.description or "",
                    refreshed.category_suggestion,
                    " ".join(str(value) for value in (refreshed.tags or []) if str(value).strip()),
                )
                if category:
                    refreshed.category_suggestion = category
            if source_type == "media_inventory_recovery" and (_generic_placeholder(refreshed.title) or _needs_category_refresh(refreshed.category_suggestion)):
                category, _ = suggest_category_from_text(
                    refreshed.title or "",
                    refreshed.description or "",
                    refreshed.category_suggestion,
                    " ".join(str(value) for value in (refreshed.tags or []) if str(value).strip()),
                )
                if category:
                    refreshed.category_suggestion = category
            sync_listing_review_state(listing=refreshed)
            readiness = summarize_listing_readiness(
                listing_images=refreshed.listing_images,
                condition_data=refreshed.condition_data,
                shipping_profile=refreshed.shipping_profile,
                listing={
                    "source_type": refreshed.source_type,
                    "category_id": refreshed.category_id,
                    "category_suggestion": refreshed.category_suggestion,
                    "listing_price": refreshed.listing_price,
                    "suggested_price": refreshed.suggested_price,
                },
            )
            refreshed.readiness_summary = readiness
            pricing = {}
            if isinstance(refreshed.marketplace_data, dict):
                pricing = refreshed.marketplace_data.get("pricing_analysis") if isinstance(refreshed.marketplace_data.get("pricing_analysis"), dict) else {}
            quality = compute_listing_quality_summary(refreshed, pricing_analysis=pricing)
            if isinstance(refreshed.marketplace_data, dict):
                marketplace_data = dict(refreshed.marketplace_data)
            else:
                marketplace_data = {}
            marketplace_data["quality_summary"] = quality
            refreshed.marketplace_data = marketplace_data

            review_blockers = list(current_readiness.get("blockers") or [])
            review_blockers.extend(quality.get("specificity_blockers") or [])
            if quality.get("specificity_status") == "trusted_for_draft" and not review_blockers:
                refreshed.needs_review = True
                refreshed.status = ListingStatus.PROCESSED if refreshed.status == ListingStatus.draft else refreshed.status
                self._mark_listing_complete(
                    db,
                    listing=refreshed,
                    worker_id=worker_id,
                    stage="quality_gate",
                    message="Listing is ready for human review.",
                    details={"readiness": readiness, "quality": quality},
                )
                db.add(refreshed)
                db.commit()
                return {
                    **repair_result,
                    "status": PROCESSING_COMPLETE,
                    "ready_for_review": True,
                    "blocking": [],
                    "warnings": (current_readiness.get("warnings") or []) + (quality.get("warnings") or []),
                }

            blockers = list(review_blockers or quality.get("blockers") or current_readiness.get("blockers") or [])
            warnings = list(quality.get("warnings") or current_readiness.get("warnings") or [])
            retryable = bool(any(token in " ".join(blockers).lower() for token in ("missing image", "missing photos", "price missing", "category missing", "title missing", "description missing")))
            if retryable and int(refreshed.processing_attempt_count or 0) < 4:
                self._mark_listing_failure(
                    db,
                    listing=refreshed,
                    stage="quality_gate",
                    error=RuntimeError("; ".join(blockers) or "Listing not yet ready"),
                    worker_id=worker_id,
                    retryable=True,
                    blocking_reason="; ".join(blockers[:5]) or "Listing still needs review",
                    details={"warnings": warnings},
                )
                db.commit()
                return {
                    **repair_result,
                    "status": PROCESSING_RETRY,
                    "ready_for_review": False,
                    "blocking": blockers,
                    "warnings": warnings,
                }

            self._mark_listing_failure(
                db,
                listing=refreshed,
                stage="quality_gate",
                error=RuntimeError("; ".join(blockers) or "Listing not yet ready"),
                worker_id=worker_id,
                retryable=False,
                blocking_reason="; ".join(blockers[:5]) or "Listing still needs review",
                details={"warnings": warnings},
            )
            db.commit()
            return {
                **repair_result,
                "status": PROCESSING_NEEDS_ATTENTION,
                "ready_for_review": False,
                "blocking": blockers,
                "warnings": warnings,
            }

        db.commit()
        return {
            **repair_result,
            "status": PROCESSING_PROCESSING,
            "ready_for_review": False,
        }

    def _resume_vine_listing(self, db: Session, *, listing: Listing, worker_id: str, dry_run: bool) -> dict[str, Any]:
        item = db.execute(
            select(VineImportItem)
            .where((VineImportItem.listing_id == listing.id) | (VineImportItem.inventory_item_id == listing.id))
            .order_by(VineImportItem.updated_at.desc(), VineImportItem.id.desc())
        ).scalars().first()
        if item is None:
            return {"processing_path": "vine", "note": "No linked Vine import row found"}

        updated: dict[str, Any] = {"processing_path": "vine", "vine_item_id": item.id}
        if not dry_run:
            metadata_result = self.vine_service.refresh_vine_listing_metadata(
                db,
                user_id=listing.user_id,
                listing_ids=[listing.id],
                limit=1,
            )
            updated["metadata_result"] = metadata_result
            if not (listing.image_urls or []) or "needs_photos" in {str(label).strip().lower() for label in (listing.custom_labels or [])}:
                image_result = self.vine_service.repair_vine_listing_images(
                    db,
                    user_id=listing.user_id,
                    listing_ids=[listing.id],
                    include_archived=False,
                    force_refresh=True,
                    use_bridge_session=True,
                    only_missing_images=True,
                    limit=1,
                )
                updated["image_result"] = image_result
        return updated

    def _resume_recovery_listing(
        self,
        db: Session,
        *,
        listing: Listing,
        worker_id: str,
        dry_run: bool,
        allow_image_identification: bool = False,
    ) -> dict[str, Any]:
        updated: dict[str, Any] = {"processing_path": "media_inventory_recovery"}
        source_metadata = dict(listing.source_metadata or {})
        recovery = dict(source_metadata.get("recovery") or {})
        recovery_source_key, recovery_evidence = _best_recovery_payload_with_key(source_metadata)
        if not _has_meaningful_recovery_identity(recovery_evidence):
            merged_source_key, merged_recovery_evidence = _best_recovery_payload_from_merged_group(
                db,
                listing=listing,
                source_metadata=source_metadata,
            )
            if _has_meaningful_recovery_identity(merged_recovery_evidence):
                recovery_source_key = merged_source_key or recovery_source_key
                recovery_evidence = merged_recovery_evidence
        image_identity = recovery.get("image_identity_v1") if isinstance(recovery.get("image_identity_v1"), dict) else {}
        recovery_identity = _merge_recovery_identity(recovery_evidence) if _has_meaningful_recovery_identity(recovery_evidence) else {}
        cached_image_identity = _merge_recovery_identity(image_identity) if _has_meaningful_recovery_identity(image_identity) else {}
        if not _has_meaningful_recovery_identity(recovery_identity) and _has_meaningful_recovery_identity(cached_image_identity):
            recovery_identity = cached_image_identity
        updated["recovery_identity_meaningful"] = bool(
            _has_meaningful_recovery_identity(recovery_identity) or _has_meaningful_recovery_identity(cached_image_identity)
        )
        recovery_specifics = recovery_evidence.get("item_specifics") if isinstance(recovery_evidence.get("item_specifics"), dict) else {}
        recovery_provenance_tag = recovery_source_key or "recovered_evidence"
        specific = assess_listing_specificity(
            title=listing.title,
            description=listing.description,
            category=listing.category_suggestion,
            item_specifics=listing.item_specifics if isinstance(listing.item_specifics, dict) else {},
            source_metadata=source_metadata,
            has_images=bool(listing.image_urls or []),
        )
        needs_repair = _generic_placeholder(listing.title) or _generic_placeholder(listing.description) or _needs_category_refresh(listing.category_suggestion) or specific.get("status") != "trusted_for_draft"
        recovery_refinement_needed = False
        if recovery_identity and not is_human_owned_field(source_metadata, "title"):
            recovery_title = str(recovery_identity.get("title") or "").strip()
            if recovery_title and (_generic_placeholder(listing.title) or _recovery_title_mismatch(listing.title, recovery_title)):
                recovery_refinement_needed = True
        if recovery_evidence and not is_human_owned_field(source_metadata, "category_suggestion"):
            recovery_category = str(recovery_evidence.get("category") or "").strip()
            if recovery_category and (_needs_category_refresh(listing.category_suggestion) or _recovery_category_mismatch(listing.category_suggestion, recovery_category)):
                recovery_refinement_needed = True
        if recovery_evidence and not is_human_owned_field(source_metadata, "description"):
            recovery_description = str(recovery_evidence.get("description") or "").strip()
            if recovery_description and (_generic_placeholder(listing.description) or _recovery_text_mismatch(listing.description, recovery_description)):
                recovery_refinement_needed = True
        if not is_human_owned_field(source_metadata, "item_specifics") and _recovery_specifics_mismatch(listing.item_specifics, recovery_specifics):
            recovery_refinement_needed = True
        if not is_human_owned_field(source_metadata, "item_specifics") and recovery_identity:
            inferred_preview_specifics: dict[str, Any] = {}
            for source_field, target_field in (
                ("brand", "Brand"),
                ("model", "Model"),
                ("mpn", "MPN"),
                ("identifier", "MPN"),
                ("product_type", "Type"),
                ("packaging_identity", "Type"),
            ):
                value = recovery_identity.get(source_field)
                if not _generic_placeholder(value):
                    inferred_preview_specifics[target_field] = str(value).strip()
            if _recovery_specifics_mismatch(listing.item_specifics, {**recovery_specifics, **inferred_preview_specifics}):
                recovery_refinement_needed = True
        if not needs_repair:
            if not recovery_refinement_needed:
                return updated

        image_paths = [
            _resolve_local_media_path(str(image.get("storage_path") or image.get("local_path") or ""))
            for image in (listing.listing_images or [])
            if isinstance(image, dict)
        ]
        image_paths = [path for path in image_paths if path]
        # Backlog draining reuses already-persisted recovery evidence first.
        # If a historical recovery row still lacks identity evidence but has
        # usable photos, run a bounded image-identification sample and cache the
        # result so retries do not repeat the same expensive analysis.
        enrichment: dict[str, Any] = {}
        synthesis = {}
        identity = recovery_identity or cached_image_identity or {}
        image_signals = {
            "title_hint": identity.get("title") or recovery_identity.get("title") or listing.title or listing.suggested_price or "",
            "source_type": listing.source_type,
            "image_count": len(image_paths or []),
            "existing_specifics": listing.item_specifics or {},
            "source_metadata": source_metadata,
            "photo_keywords": [
                str(identity.get("brand") or recovery_identity.get("brand") or "").strip(),
                str(identity.get("model") or recovery_identity.get("model") or "").strip(),
                str(identity.get("mpn") or recovery_identity.get("mpn") or "").strip(),
            ],
        }
        if not dry_run and image_paths and not _has_meaningful_recovery_identity(image_identity):
            if not allow_image_identification and not _has_meaningful_recovery_identity(recovery_identity):
                updated["needs_image_identification"] = True
                updated["needs_image_identification_reason"] = "insufficient_identity_evidence"
                return updated
            sample_paths = image_paths[:3]
            try:
                sample = self.photo_enrichment.enrich_group(sample_paths)
            except Exception as exc:  # noqa: BLE001
                sample = {"error": type(exc).__name__, "message": str(exc), "photos_evaluated": len(sample_paths)}
                updated["image_identity_error"] = str(exc)
            else:
                synthesis = sample.get("group_synthesis") if isinstance(sample.get("group_synthesis"), dict) else {}
                source_metadata["recovery"] = {
                    **recovery,
                    "image_identity_v1": synthesis,
                    "image_identity_sample_paths": sample_paths,
                    "image_identity_photos_evaluated": sample.get("photos_evaluated"),
                    "image_identity_photos_excluded": sample.get("photos_excluded"),
                    "image_identity_cached_at": _now().isoformat(),
                }
                recovery = dict(source_metadata.get("recovery") or {})
                image_identity = synthesis if isinstance(synthesis, dict) else {}
                if _has_meaningful_recovery_identity(image_identity):
                    identity = image_identity.get("identity") if isinstance(image_identity.get("identity"), dict) else identity
                if image_identity:
                    updated["image_identity"] = image_identity
        if not dry_run:
            generated = self.listing_ai.generate(image_signals, db=db, user_id=listing.user_id, listing_id=listing.id)
            base_title = build_marketplace_title(
                title=recovery_identity.get("title") or listing.title or "",
                item_specifics=listing.item_specifics if isinstance(listing.item_specifics, dict) else {},
                category_hint=listing.category_suggestion,
                source_metadata=source_metadata,
            )
            base_description = _product_listing_description(
                title=base_title or listing.title or "Recovered inventory item",
                listing=listing,
                shipping_profile=listing.shipping_profile,
            )
            if recovery_identity:
                recovery_title = str(recovery_identity.get("title") or "").strip()
                if recovery_title and not is_human_owned_field(source_metadata, "title") and (_generic_placeholder(listing.title) or _recovery_title_mismatch(listing.title, recovery_title)):
                    listing.title = recovery_title
                    source_metadata = mark_field_provenance(source_metadata, field="title", provenance=recovery_provenance_tag)
                recovery_category = str(recovery_evidence.get("category") or "").strip()
                if recovery_category and not is_human_owned_field(source_metadata, "category_suggestion") and (_needs_category_refresh(listing.category_suggestion) or _recovery_category_mismatch(listing.category_suggestion, recovery_category)):
                    listing.category_suggestion = recovery_category
                    source_metadata = mark_field_provenance(source_metadata, field="category_suggestion", provenance=recovery_provenance_tag)
                recovery_specifics = recovery_evidence.get("item_specifics") if isinstance(recovery_evidence.get("item_specifics"), dict) else {}
                if recovery_specifics and _apply_recovery_specifics(
                    listing=listing,
                    source_metadata=source_metadata,
                    recovery_provenance=recovery_provenance_tag,
                    recovery_specifics=recovery_specifics,
                    inferred_specifics={},
                ):
                    source_metadata = dict(listing.source_metadata or {})
                inferred_specifics: dict[str, Any] = {}
                for source_field, target_field in (
                    ("brand", "Brand"),
                    ("model", "Model"),
                    ("mpn", "MPN"),
                    ("identifier", "MPN"),
                    ("product_type", "Type"),
                    ("packaging_identity", "Type"),
                ):
                    value = recovery_identity.get(source_field)
                    if not _generic_placeholder(value):
                        inferred_specifics[target_field] = str(value).strip()
                if inferred_specifics and _apply_recovery_specifics(
                    listing=listing,
                    source_metadata=source_metadata,
                    recovery_provenance=recovery_provenance_tag,
                    recovery_specifics={},
                    inferred_specifics=inferred_specifics,
                ):
                    source_metadata = dict(listing.source_metadata or {})
                recovery_description = str(recovery_evidence.get("description") or "").strip()
                if recovery_description and not is_human_owned_field(source_metadata, "description") and (_generic_placeholder(listing.description) or _recovery_text_mismatch(listing.description, recovery_description)):
                    listing.description = recovery_description
                    source_metadata = mark_field_provenance(source_metadata, field="description", provenance=recovery_provenance_tag)
                recovery_tags = recovery_evidence.get("tags") if isinstance(recovery_evidence.get("tags"), list) else []
                if recovery_tags:
                    listing.tags = sorted({*(listing.tags or []), *[str(tag) for tag in recovery_tags if str(tag).strip()]})
                recovery_estimated_value = recovery_evidence.get("estimated_value")
                if not listing.listing_price and recovery_estimated_value:
                    listing.listing_price = recovery_estimated_value
                    listing.suggested_price = recovery_estimated_value
            if image_identity:
                identity_title = str((image_identity.get("identity") or image_identity).get("title") or "").strip()
                if identity_title and not _generic_placeholder(identity_title) and not is_human_owned_field(source_metadata, "title"):
                    listing.title = identity_title
                    source_metadata = mark_field_provenance(source_metadata, field="title", provenance="image_identity_v1")
                identity_description = str(image_identity.get("description") or "").strip()
                if identity_description and not is_human_owned_field(source_metadata, "description"):
                    listing.description = identity_description
                    source_metadata = mark_field_provenance(source_metadata, field="description", provenance="image_identity_v1")
                identity_category = str(image_identity.get("category") or "").strip()
                if identity_category and not is_human_owned_field(source_metadata, "category_suggestion"):
                    listing.category_suggestion = identity_category
                    source_metadata = mark_field_provenance(source_metadata, field="category_suggestion", provenance="image_identity_v1")
                identity_specifics = image_identity.get("item_specifics") if isinstance(image_identity.get("item_specifics"), dict) else {}
                if identity_specifics:
                    _apply_recovery_specifics(
                        listing=listing,
                        source_metadata=source_metadata,
                        recovery_provenance="image_identity_v1",
                        recovery_specifics=identity_specifics,
                        inferred_specifics={},
                    )
                    source_metadata = dict(listing.source_metadata or {})
                identity_tags = image_identity.get("tags") if isinstance(image_identity.get("tags"), list) else []
                if identity_tags:
                    listing.tags = sorted({*(listing.tags or []), *[str(tag) for tag in identity_tags if str(tag).strip()]})
                estimated_value = image_identity.get("estimated_value")
                if not listing.listing_price and estimated_value:
                    listing.listing_price = estimated_value
                    listing.suggested_price = estimated_value
            if _generic_placeholder(listing.title):
                listing.title = generated.get("title") or base_title or listing.title
            if _generic_placeholder(listing.description):
                listing.description = generated.get("description") or base_description or listing.description
            if _needs_category_refresh(listing.category_suggestion):
                category, _ = suggest_category_from_text(
                    listing.title or generated.get("title") or "",
                    listing.description or generated.get("description") or "",
                    listing.category_suggestion,
                    " ".join(generated.get("tags") or []),
                )
                if category:
                    listing.category_suggestion = category
            if not listing.item_specifics:
                listing.item_specifics = generated.get("item_specifics") or {}
            if not listing.tags:
                listing.tags = generated.get("tags") or []
            if not listing.listing_price and generated.get("estimated_value"):
                listing.listing_price = generated.get("estimated_value")
                listing.suggested_price = generated.get("estimated_value")
            shipping = derive_shipping_profile(
                listing={"title": listing.title, "description": listing.description, "listing_price": listing.listing_price or listing.suggested_price},
                item_specifics=listing.item_specifics,
                existing=listing.shipping_profile,
            )
            listing.shipping_profile = shipping
            refreshed_recovery = dict(source_metadata.get("recovery") or {})
            refreshed_recovery["backfill_refresh"] = {
                "title_refreshed": _generic_placeholder(listing.title),
                "description_refreshed": _generic_placeholder(listing.description),
                "category_refreshed": _needs_category_refresh(listing.category_suggestion),
                "image_identity_used": bool(image_identity),
                "worker_id": worker_id,
            }
            source_metadata["recovery"] = refreshed_recovery
            listing.source_metadata = source_metadata
            listing.marketplace_data = {
                **(listing.marketplace_data or {}),
                "draft_previews": (listing.marketplace_data or {}).get("draft_previews") or {},
            }
            updated["generated"] = generated
        if not _has_meaningful_recovery_identity(recovery_identity) and not _has_meaningful_recovery_identity(image_identity):
            updated["needs_image_identification"] = True
            updated["needs_image_identification_reason"] = "insufficient_identity_evidence"
        grouping_review = str((image_identity or {}).get("quality_gate") or "").strip().lower() == "needs_grouping_review" or str((image_identity or {}).get("group_kind") or "").strip().lower() == "multiple_unrelated_products"
        if grouping_review:
            updated["grouping_review"] = True
            updated["grouping_review_reason"] = "photo_grouping_review_required"
        return updated

    def _resume_generic_listing(self, db: Session, *, listing: Listing, worker_id: str, dry_run: bool) -> dict[str, Any]:
        updated = {"processing_path": "generic"}
        if dry_run:
            return updated
        if _generic_placeholder(listing.title) or _generic_placeholder(listing.description):
            generated = self.listing_ai.generate(
                {
                    "title_hint": listing.title or (listing.source_metadata or {}).get("product_name") or "Recovered inventory item",
                    "source_type": listing.source_type,
                    "image_count": len(listing.image_urls or []),
                    "existing_specifics": listing.item_specifics or {},
                    "photo_keywords": [str(listing.category_suggestion or ""), str(listing.source_type or "")],
                }, db=db, user_id=listing.user_id, listing_id=listing.id
            )
            if _generic_placeholder(listing.title):
                listing.title = generated.get("title") or listing.title
            if _generic_placeholder(listing.description):
                listing.description = generated.get("description") or listing.description
            if not listing.item_specifics:
                listing.item_specifics = generated.get("item_specifics") or {}
            if _needs_category_refresh(listing.category_suggestion):
                category, _ = suggest_category_from_text(
                    listing.title or generated.get("title") or "",
                    listing.description or generated.get("description") or "",
                    listing.category_suggestion,
                    " ".join(generated.get("tags") or []),
                )
                if category:
                    listing.category_suggestion = category
            listing.tags = listing.tags or generated.get("tags") or []
            listing.shipping_profile = derive_shipping_profile(
                listing={"title": listing.title, "description": listing.description, "listing_price": listing.listing_price or listing.suggested_price},
                item_specifics=listing.item_specifics,
                existing=listing.shipping_profile,
            )
            updated["generated"] = generated
        return updated

    def _is_terminal(self, listing: Listing) -> bool:
        status = str(listing.status or "").strip().lower()
        if listing.ebay_listing_id:
            return True
        if listing.sold_at is not None:
            return True
        if status in {"archived", "posted", "published"}:
            return True
        if str(listing.ebay_publish_status or "").upper() == "POSTED":
            return True
        labels = {str(value).strip().lower() for value in (listing.custom_labels or [])}
        return bool({"archived_vine", "archived_sold"} & labels)

    def _mark_processing_started(self, db: Session, *, listing: Listing, worker_id: str, stage: str) -> None:
        now = _now()
        listing.processing_state = PROCESSING_PROCESSING
        listing.processing_stage = stage
        listing.processing_started_at = listing.processing_started_at or now
        listing.processing_last_attempt_at = now
        listing.processing_attempt_count = int(listing.processing_attempt_count or 0) + 1
        listing.processing_worker_id = worker_id
        listing.processing_lease_expires_at = now + timedelta(minutes=20)
        listing.processing_blocking_reason = None
        listing.processing_last_error = None
        listing.processing_error_stage = None
        db.add(listing)
        self._record_event(db, listing=listing, event_type="processing_started", stage=stage, message="Listing processing resumed.", details={"worker_id": worker_id})
        db.commit()

    def _mark_listing_complete(self, db: Session, *, listing: Listing, worker_id: str, stage: str, message: str, details: dict[str, Any] | None = None) -> None:
        now = _now()
        listing.processing_state = PROCESSING_COMPLETE
        listing.processing_stage = stage
        listing.processing_last_success_at = now
        listing.processing_next_retry_at = None
        listing.processing_lease_expires_at = None
        listing.processing_worker_id = worker_id
        listing.processing_blocking_reason = None
        listing.processing_last_error = None
        listing.processing_error_stage = None
        listing.needs_review = True
        db.add(listing)
        self._record_event(db, listing=listing, event_type="processing_complete", stage=stage, message=message, details=details)
        create_process_notification(
            db,
            user_id=listing.user_id,
            notification_type="listing_processing_complete",
            title=f"Listing #{listing.id} is ready for review",
            message=message,
            href=f"/listings/{listing.id}",
            metadata_json={"listing_id": listing.id, "source_type": listing.source_type, "stage": stage, "details": details or {}},
        )
        db.add(listing)

    def _mark_listing_failure(
        self,
        db: Session,
        *,
        listing: Listing,
        stage: str,
        error: Exception,
        worker_id: str,
        retryable: bool,
        blocking_reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        listing.processing_last_attempt_at = now
        listing.processing_stage = stage
        listing.processing_worker_id = worker_id
        listing.processing_last_error = str(error)
        listing.processing_error_stage = stage
        listing.processing_blocking_reason = blocking_reason or str(error)
        listing.processing_state = PROCESSING_RETRY if retryable else PROCESSING_BLOCKED
        listing.processing_next_retry_at = (
            now + timedelta(minutes=min(120, max(5, 2 ** min(int(listing.processing_attempt_count or 0), 6))))
            if retryable
            else None
        )
        listing.processing_lease_expires_at = None
        db.add(listing)
        event_type = "processing_retry" if retryable else "processing_blocked"
        self._record_event(db, listing=listing, event_type=event_type, stage=stage, message=str(error), details={"retryable": retryable, **(details or {})})
        if not retryable:
            create_process_notification(
                db,
                user_id=listing.user_id,
                notification_type="listing_processing_blocked",
                title=f"Listing #{listing.id} needs attention",
                message=listing.processing_blocking_reason,
                href=f"/listings/{listing.id}",
                metadata_json={"listing_id": listing.id, "source_type": listing.source_type, "stage": stage, "details": details or {}},
            )

    def _mark_listing_needs_attention(
        self,
        db: Session,
        *,
        listing: Listing,
        stage: str,
        worker_id: str,
        blocking_reason: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        listing.processing_last_attempt_at = now
        listing.processing_stage = stage
        listing.processing_worker_id = worker_id
        listing.processing_last_error = message
        listing.processing_error_stage = stage
        listing.processing_blocking_reason = blocking_reason
        listing.processing_state = PROCESSING_NEEDS_ATTENTION
        listing.processing_next_retry_at = None
        listing.processing_lease_expires_at = None
        db.add(listing)
        self._record_event(
            db,
            listing=listing,
            event_type="processing_blocked",
            stage=stage,
            message=message,
            details={"retryable": False, **(details or {})},
        )
        create_process_notification(
            db,
            user_id=listing.user_id,
            notification_type="listing_processing_blocked",
            title=f"Listing #{listing.id} needs attention",
            message=blocking_reason,
            href=f"/listings/{listing.id}",
            metadata_json={"listing_id": listing.id, "source_type": listing.source_type, "stage": stage, "details": details or {}},
        )

    def _finalize_review_ready_listing(self, db: Session, *, listing_id: int, worker_id: str) -> None:
        fresh = db.get(Listing, listing_id)
        if fresh is None or fresh.needs_review:
            return
        if self._is_terminal(fresh):
            return
        current_readiness = summarize_listing_readiness(
            listing_images=fresh.listing_images,
            condition_data=fresh.condition_data,
            shipping_profile=fresh.shipping_profile,
            listing={
                "source_type": fresh.source_type,
                "category_id": fresh.category_id,
                "category_suggestion": fresh.category_suggestion,
                "listing_price": fresh.listing_price,
                "suggested_price": fresh.suggested_price,
            },
        )
        pricing = {}
        if isinstance(fresh.marketplace_data, dict):
            pricing = fresh.marketplace_data.get("pricing_analysis") if isinstance(fresh.marketplace_data.get("pricing_analysis"), dict) else {}
        quality = compute_listing_quality_summary(fresh, pricing_analysis=pricing)
        blockers = list(current_readiness.get("blockers") or [])
        blockers.extend(quality.get("specificity_blockers") or [])
        if quality.get("specificity_status") != "trusted_for_draft" or blockers:
            return
        fresh.needs_review = True
        fresh.status = ListingStatus.PROCESSED if fresh.status == ListingStatus.draft else fresh.status
        fresh.processing_state = PROCESSING_COMPLETE
        fresh.processing_stage = "quality_gate"
        fresh.processing_last_success_at = fresh.processing_last_success_at or _now()
        fresh.processing_next_retry_at = None
        fresh.processing_lease_expires_at = None
        fresh.processing_worker_id = worker_id
        fresh.processing_blocking_reason = None
        fresh.processing_last_error = None
        fresh.processing_error_stage = None
        db.add(fresh)
        db.commit()

    def _record_event(
        self,
        db: Session,
        *,
        listing: Listing,
        event_type: str,
        stage: str | None,
        message: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        db.add(
            ListingProcessingEvent(
                listing_id=listing.id,
                user_id=listing.user_id,
                event_type=event_type,
                status="completed" if event_type.endswith("complete") else "running" if event_type.endswith("started") else "queued",
                stage=stage,
                message=message,
                details_json=details or {},
            )
        )
