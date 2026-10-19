from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import re
import logging
from typing import Any

from sqlalchemy import Text, and_, case, cast, exists, func, not_, or_, select
from sqlalchemy.orm import Session

from app.models.enums import ListingStatus, MarketplaceListingStatus
from app.models.models import IntakePhoto, IntakePhotoBatch, Listing, ListingProcessingEvent, MarketplaceCrosspostJob, MarketplaceListing, MediaRecoveryItemGroup, MediaRecoveryRun, User, VineImportItem
from app.services.category_rules import suggest_category_from_text, verified_category_id
from app.services.listing_ai import ListingAIService, assess_description_quality, build_listing_description, build_marketplace_title
from app.services.listing_provenance import is_human_owned_field, mark_field_provenance
from app.services.listing_review import derive_shipping_profile, summarize_listing_readiness, sync_listing_review_state
from app.services.listing_specificity import assess_listing_specificity, classify_listing_reviewability, is_bare_identifier_title, is_caption_like_title, is_generic_sellable_title
from app.services.photo_enrichment import PhotoEnrichmentService
from app.services.pricing_research_service import compute_listing_quality_summary
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.process_notifications import create_process_notification
from app.services.vine_import_service import VineImportService
from app.services.canonical_readiness import (
    canonical_listing_readiness,
    hydrate_authoritative_listing_price,
    resolve_authoritative_listing_price,
)
from app.services.vine_taxonomy_repair import validate_or_resolve_vine_category
from app.services.marketplace_field_mapper import apply_generated_marketplace_drafts, build_marketplace_draft_previews, destination_category_snapshot, persist_marketplace_description_variants
from app.services.marketplace_preflight import MarketplacePreflightService
from scripts.repair_recovery_draft_copy import _needs_category_refresh, _product_listing_description

# All machine-generated intake drafts must enter the same resumable listing
# intelligence pipeline.  Google/Slate drafts were previously omitted here,
# leaving filename titles and thin copy stuck in Processing forever.
TARGET_SOURCE_TYPES = {"amazon_vine", "media_inventory_recovery", "intake_head_slate"}
logger = logging.getLogger(__name__)
PROCESSING_COMPLETE = "complete"
PROCESSING_PROCESSING = "processing"
PROCESSING_RETRY = "retry"
PROCESSING_BLOCKED = "blocked"
PROCESSING_NEEDS_ATTENTION = "needs_attention"
PROCESSING_QUEUED = "queued"
# A deterministic audit outcome.  It is intentionally absent from the normal
# worker claim states; only an explicitly bounded AI-repair campaign may admit
# it to ``queued``.
PROCESSING_QUALITY_REPAIR_REQUIRED = "quality_repair_required"
MAX_AUTO_DRAFT_GROUP_PHOTOS = 100
# Bump this only when a materially new recovery capability warrants another
# bounded pass over previously human-classified inventory.  The version is
# persisted per listing so unchanged physical-evidence blockers are never
# retried every scheduler tick.
RECOVERY_LEGACY_REEVALUATION_VERSION = "recovery-legacy-reevaluation-v1"
RECOVERY_LEGACY_HUMAN_STAGE = "legacy_human_verified_v1"
RECOVERY_LEGACY_ATTEMPTED_STAGE = "legacy_capability_attempted_v1"


def _now() -> datetime:
    return datetime.now(UTC)


def _not_archived_listing():
    """Exclude retained historical rows from every active processing query."""
    return and_(
        or_(Listing.custom_labels.is_(None), not_(cast(Listing.custom_labels, Text).ilike("%archived_vine%"))),
        or_(Listing.custom_labels.is_(None), not_(cast(Listing.custom_labels, Text).ilike("%archived_sold%"))),
        or_(Listing.custom_labels.is_(None), not_(cast(Listing.custom_labels, Text).ilike("%archived_ebay%"))),
    )


def _not_frozen_operator_payload():
    """Exclude listings whose operator-approved publish payload must not be rewritten."""
    return and_(
        Listing.source_metadata["operator_approved_at"].as_string().is_(None),
        Listing.source_metadata["approval_attempted_at"].as_string().is_(None),
        Listing.source_metadata["operator_approved_by_user_id"].as_string().is_(None),
    )


def _fresh_ebay_preflight_blocker(listing: Listing) -> dict[str, Any] | None:
    """Return a current, explicit eBay execution blocker, if one is persisted.

    Recovery paths must not turn a destination capability/policy failure back
    into generic machine work merely because canonical readiness still reports
    Processing. The same check is used by both blocked-row recovery and stale
    machine-state recovery to prevent a blocked -> queued -> blocked loop.
    """
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    preflight = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else {}
    by_marketplace = preflight.get("by_marketplace") if isinstance(preflight.get("by_marketplace"), dict) else {}
    ebay = by_marketplace.get("ebay") if isinstance(by_marketplace.get("ebay"), dict) else None
    if not isinstance(ebay, dict) or bool(preflight.get("stale") or ebay.get("stale")):
        return None
    status = str(ebay.get("status") or "").strip().lower()
    if status in {"ready", "ready_with_warnings", "needs_review", "published"}:
        return None
    blockers = ebay.get("blockers") or ebay.get("execution_blockers") or ebay.get("preparation_blockers") or []
    if not blockers:
        return None
    first = blockers[0]
    if isinstance(first, dict):
        return {
            "code": str(first.get("code") or "DESTINATION_PREFLIGHT_BLOCKED"),
            "message": str(first.get("message") or first.get("user_message") or first.get("code") or "Current eBay preflight is blocked"),
        }
    return {"code": "DESTINATION_PREFLIGHT_BLOCKED", "message": str(first)}


def _has_explicit_grouping_review(listing: Listing) -> bool:
    """Whether durable intake evidence already requires a human group repair."""
    metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    grouping_review = metadata.get("grouping_review") if isinstance(metadata.get("grouping_review"), dict) else {}
    source_facts = metadata.get("source_facts") if isinstance(metadata.get("source_facts"), dict) else {}
    recovery = metadata.get("recovery") if isinstance(metadata.get("recovery"), dict) else {}
    image_identity = recovery.get("image_identity_v1") if isinstance(recovery.get("image_identity_v1"), dict) else {}
    return bool(
        str(listing.processing_stage or "").strip().lower() == "grouping_review"
        or str(grouping_review.get("status") or "").strip().lower() == "required"
        or str(source_facts.get("quality_gate") or "").strip().lower() == "needs_grouping_review"
        or str(source_facts.get("group_kind") or "").strip().lower() == "multiple_unrelated_products"
        or str(image_identity.get("quality_gate") or "").strip().lower() == "needs_grouping_review"
        or str(image_identity.get("group_kind") or "").strip().lower() == "multiple_unrelated_products"
    )


def _grouping_review_reason(listing: Listing) -> str:
    metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    grouping_review = metadata.get("grouping_review") if isinstance(metadata.get("grouping_review"), dict) else {}
    reason = str(grouping_review.get("reason") or "").strip()
    if reason:
        return reason
    recovery = metadata.get("recovery") if isinstance(metadata.get("recovery"), dict) else {}
    image_identity = recovery.get("image_identity_v1") if isinstance(recovery.get("image_identity_v1"), dict) else {}
    if image_identity.get("quality_gate") == "needs_grouping_review":
        return "photo_grouping_review_required"
    return "Saved photo evidence requires review of the item group boundary before drafting."


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _record_unverified_price_estimate(source_metadata: dict[str, Any], source: str, value: Any) -> dict[str, Any]:
    """Keep visual/model values as provenance only, never as a seller ask."""
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        return source_metadata
    if amount <= 0:
        return source_metadata
    updated = dict(source_metadata or {})
    estimates = dict(updated.get("unverified_price_estimates") or {})
    estimates[str(source)] = {"amount": amount, "not_a_recommendation": True}
    updated["unverified_price_estimates"] = estimates
    return updated


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


def _machine_review_quality_blockers(listing: Listing) -> list[str]:
    """Fail closed before exposing a machine draft as Needs Review.

    Canonical readiness intentionally permits a human review for marketplace
    configuration.  It was too permissive for product-draft quality: captions,
    slate assets and opaque/default prices could be marked reviewable.  This
    gate is product-only and deliberately leaves destination blockers in their
    destination state.
    """
    blockers: list[str] = []
    title = _normalize_text(listing.title)
    if _generic_placeholder(title) or len(title) < 12:
        blockers.append("IDENTITY_OR_TITLE_INCOMPLETE")
    category = _normalize_text(listing.category_suggestion)
    if not category or _needs_category_refresh(category):
        blockers.append("CANONICAL_CATEGORY_INCOMPLETE")
    description = _normalize_text(listing.description)
    if _generic_placeholder(description) or len(description) < 80:
        blockers.append("DESCRIPTION_INCOMPLETE")
    images = listing.listing_images if isinstance(listing.listing_images, list) else []
    public_images = [image for image in images if isinstance(image, dict) and not image.get("is_reference")]
    if not public_images:
        blockers.append("PRODUCT_IMAGES_MISSING")
    for image in public_images:
        metadata = image.get("metadata") if isinstance(image.get("metadata"), dict) else {}
        label = _normalize_text(image.get("label")).lower()
        path = _normalize_text(image.get("storage_path")).lower()
        if metadata.get("is_slate") or metadata.get("image_type") == "slate" or "slate" in label or "/intake-slates/" in path:
            blockers.append("SLATE_IMAGE_IN_PRODUCT_SET")
            break
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    pricing = marketplace_data.get("pricing_analysis") if isinstance(marketplace_data.get("pricing_analysis"), dict) else {}
    provenance = _normalize_text(pricing.get("provenance") or pricing.get("pricing_source")).upper()
    if not provenance:
        blockers.append("PRICING_PROVENANCE_MISSING")
    return sorted(set(blockers))


def _needs_description_enrichment(text: str | None, *, source_type: str | None, source_metadata: dict[str, Any] | None = None) -> bool:
    """Return whether machine-owned copy is too thin for a finished draft.

    Length is only used as a retry signal here, never as proof of quality. A
    short operator-edited description remains authoritative; generated Google
    photo/Vine/recovery copy is sent back through synthesis so verified facts
    can be incorporated before it reaches the review queue.
    """
    if is_human_owned_field(source_metadata if isinstance(source_metadata, dict) else {}, "description"):
        return False
    normalized = _normalize_text(text)
    if not normalized or len(normalized) >= 180:
        return False
    return str(source_type or "").strip().lower() in {
        "intake_head_slate", "google_photos_album", "photo_intake", "amazon_vine"
    }


def _usable_specific_value(value: Any) -> bool:
    """Validate an item-specific value without applying title heuristics.

    Brand/model values such as ``Logitech`` or ``LINK`` can look like a
    caption or bare identifier when passed through the title-quality helpers,
    but they are precisely the evidence we must preserve in structured
    aspects.  Only empty/explicit placeholder values are rejected here.
    """
    normalized = _normalize_text(value)
    return bool(normalized) and normalized.lower() not in {
        "unknown", "n/a", "na", "none", "null", "not available", "needs review",
    }


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
    # Older Google/Slate drafts stored the field-by-field synthesis under
    # listing_intelligence rather than recovery. Treat a coherent one-item
    # synthesis as cached evidence so an AI-provider outage does not erase
    # usable identity facts. Never promote a synthesis explicitly marked as
    # multiple unrelated products; that remains a grouping-review blocker.
    intelligence = source_metadata.get("listing_intelligence") if isinstance(source_metadata.get("listing_intelligence"), dict) else {}
    synthesis = intelligence.get("group_synthesis") if isinstance(intelligence.get("group_synthesis"), dict) else {}
    if str(synthesis.get("group_kind") or "").strip().lower() in {"one_item", "intentional_set"}:
        payloads.append(synthesis)
    return payloads


def _best_recovery_payload_with_key(source_metadata: dict[str, Any] | None) -> tuple[str | None, dict[str, Any]]:
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    for key in ("identity", "full_group_evidence_v3", "full_group_evidence_v2", "image_identity_v1"):
        payload = recovery.get(key)
        if isinstance(payload, dict) and _has_meaningful_recovery_identity(payload):
            return key, payload
    intelligence = source_metadata.get("listing_intelligence") if isinstance(source_metadata.get("listing_intelligence"), dict) else {}
    synthesis = intelligence.get("group_synthesis") if isinstance(intelligence.get("group_synthesis"), dict) else {}
    if (
        str(synthesis.get("group_kind") or "").strip().lower() in {"one_item", "intentional_set"}
        and _has_meaningful_recovery_identity(synthesis)
    ):
        return "listing_intelligence.group_synthesis", synthesis
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
            if not _usable_specific_value(value_text):
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
            if candidate_text and _usable_specific_value(candidate_text):
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
        query = select(Listing).where(Listing.source_type.in_(sorted(TARGET_SOURCE_TYPES)), _not_archived_listing())
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
            # Canonical queue counts are derived from the same readiness
            # service used by listing APIs/publish gating.  The legacy fields
            # above remain for worker/backlog compatibility, but callers that
            # render user-facing queues must not infer status from the raw
            # processing_state/needs_review columns alone.
            "canonical_queue": defaultdict(int),
            "canonical_blocker_breakdown": defaultdict(int),
        }
        now = _now()
        oldest_queued: datetime | None = None
        last_success: datetime | None = None
        for listing in rows:
            canonical = canonical_listing_readiness(listing)
            canonical_queue = str(canonical.get("queue") or "UNKNOWN")
            summary["canonical_queue"][canonical_queue] += 1
            if canonical_queue == "NEEDS_ATTENTION":
                for reason in canonical.get("blocking_reasons") or []:
                    normalized_reason = _normalize_text(reason)
                    if normalized_reason:
                        summary["canonical_blocker_breakdown"][normalized_reason] += 1
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
        summary["canonical_queue"] = dict(summary["canonical_queue"])
        summary["canonical_blocker_breakdown"] = [
            {"reason": reason, "count": count}
            for reason, count in sorted(summary["canonical_blocker_breakdown"].items(), key=lambda item: (-item[1], item[0]))
        ]
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
                if _usable_specific_value(value):
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
        active_remote_projection = exists(
            select(MarketplaceListing.id).where(
                MarketplaceListing.listing_id == Listing.id,
                MarketplaceListing.status.in_([
                    MarketplaceListingStatus.PENDING,
                    MarketplaceListingStatus.PUBLISHED,
                    MarketplaceListingStatus.UPDATED,
                    MarketplaceListingStatus.SOLD,
                    MarketplaceListingStatus.CLOSED,
                ]),
            )
        )
        query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            _not_archived_listing(),
            Listing.ebay_listing_id.is_(None),
            Listing.status.notin_([ListingStatus.PUBLISHED, ListingStatus.posted, ListingStatus.archived]),
            or_(Listing.ebay_publish_status.is_(None), Listing.ebay_publish_status.notin_(["POSTING", "POSTED"])),
            not_(active_remote_projection),
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

    def _classify_legacy_recovery_blocker(self, listing: Listing) -> tuple[str, str]:
        """Classify historical recovery Attention without doing provider work.

        This is deliberately evidence-led.  It reopens stale drafting limits
        (generic identity/destination-preflight coupling) only when usable
        media or stored recovery evidence exists.  Physical safety, contents,
        grouping, condition, fitment, and duplicate conflicts remain human
        verification work until a future capability supplies new evidence.
        """
        metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
        recovery = metadata.get("recovery") if isinstance(metadata.get("recovery"), dict) else {}
        source_facts = metadata.get("source_facts") if isinstance(metadata.get("source_facts"), dict) else {}
        reason = _normalize_text(listing.processing_blocking_reason).lower()
        stage = _normalize_text(listing.processing_stage).lower()
        has_media = bool(listing.listing_images or listing.image_urls)
        has_recovery_evidence = bool(recovery or source_facts)
        has_machine_evidence = has_media or has_recovery_evidence

        if _has_explicit_grouping_review(listing) or stage == "grouping_review":
            return "STILL_REQUIRES_HUMAN", "explicit_photo_group_boundary_conflict"
        human_stages = {
            "safety_identity_verification",
            "restricted_product_review",
            "fitment_verification",
            "condition_completeness_verification",
            "identity_condition_verification",
            "photo_condition_verification",
            "duplicate_resolution",
        }
        if stage in human_stages:
            return "STILL_REQUIRES_HUMAN", f"physical_or_safety_verification:{stage}"
        human_markers = (
            "add actual product photos",
            "add clear",
            "verify contents",
            "working condition",
            "function test",
            "exact application",
            "exact compatibility",
            "hazardous",
            "prohibited",
            "expired",
            "do not create a duplicate",
            "cannot truthfully represent one",
            "different products",
            "identity contradiction",
        )
        if any(marker in reason for marker in human_markers):
            return "STILL_REQUIRES_HUMAN", "physical_evidence_or_policy_required"

        legacy_markers = (
            "generic_or_underspecified_title",
            "missing_specific_identifier_evidence",
            "product identity is too generic",
            "insufficient_identity_evidence",
            "needs_image_identification",
            "generic_or_caption_identity",
            "select an intended marketplace",
            "current marketplace preflight is not publishable",
            "destination readiness remains externally blocked",
        )
        if has_machine_evidence and any(marker in reason for marker in legacy_markers):
            return "NOW_MACHINE_RESOLVABLE", "current_identity_or_canonical_draft_capability"
        return "STILL_REQUIRES_HUMAN", "no_new_machine_resolution_path"

    def reevaluate_legacy_recovery_attention(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        limit: int = 1,
        worker_id: str = "recovery-legacy-reevaluator",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Boundedly reclassify historical recovery Attention using current rules.

        The method claims no listing for enrichment itself.  It records a
        durable versioned outcome and returns newly queued listing IDs for the
        inventory worker to process vertically with image identification
        enabled.  Human outcomes are stamped with this capability version so
        Beat does not create a retry storm on unchanged evidence.
        """
        query = (
            select(Listing)
            .outerjoin(MediaRecoveryItemGroup, MediaRecoveryItemGroup.draft_listing_id == Listing.id)
            .outerjoin(MediaRecoveryRun, MediaRecoveryRun.id == MediaRecoveryItemGroup.run_id)
            .where(
                Listing.source_type == "media_inventory_recovery",
                _not_archived_listing(),
                Listing.ebay_listing_id.is_(None),
                Listing.sold_at.is_(None),
                Listing.needs_review.is_(False),
                Listing.processing_state.in_([PROCESSING_NEEDS_ATTENTION, PROCESSING_BLOCKED, "failed"]),
                or_(
                    Listing.processing_stage.is_(None),
                    Listing.processing_stage.notin_([RECOVERY_LEGACY_HUMAN_STAGE, RECOVERY_LEGACY_ATTEMPTED_STAGE]),
                ),
            )
        )
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        # Group creation time is the durable per-item recovery ingestion
        # chronology.  Run creation and group ID only resolve equal/missing
        # timestamps, never override a genuinely newer recovered item.
        query = query.order_by(
            MediaRecoveryItemGroup.created_at.desc().nullslast(),
            MediaRecoveryRun.created_at.desc().nullslast(),
            MediaRecoveryItemGroup.id.desc().nullslast(),
            Listing.created_at.desc(),
            Listing.id.desc(),
        ).limit(max(1, limit * 100))
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(of=Listing, skip_locked=True)

        examined = 0
        preserved = 0
        requeued: list[int] = []
        outcomes: defaultdict[str, int] = defaultdict(int)
        for listing in db.execute(query).scalars().all():
            if len(requeued) >= max(1, limit):
                break
            if self._is_terminal(listing):
                continue
            examined += 1
            outcome, classification = self._classify_legacy_recovery_blocker(listing)
            outcomes[f"{outcome}:{classification}"] += 1
            metadata = dict(listing.source_metadata or {})
            previous = metadata.get("processing_reevaluation") if isinstance(metadata.get("processing_reevaluation"), dict) else {}
            record = {
                "version": RECOVERY_LEGACY_REEVALUATION_VERSION,
                "last_reevaluated_at": _now().isoformat(),
                "reevaluation_count": int(previous.get("reevaluation_count") or 0) + 1,
                "outcome": outcome,
                "classification": classification,
                "original_stage": listing.processing_stage,
                "original_blocker": listing.processing_blocking_reason,
                "worker_id": worker_id,
            }
            if outcome == "NOW_MACHINE_RESOLVABLE":
                record["requeue_reason"] = classification
                # Re-evaluation establishes that the blocker is machine
                # resolvable; it does not itself authorize a paid repair.
                listing.processing_state = PROCESSING_QUALITY_REPAIR_REQUIRED
                listing.processing_stage = "legacy_capability_requeue"
                listing.processing_next_retry_at = None
                listing.processing_lease_expires_at = None
                listing.processing_last_error = None
                listing.processing_error_stage = None
                listing.processing_blocking_reason = None
                metadata["processing_reevaluation"] = record
                listing.source_metadata = metadata
                db.add(listing)
                self._record_event(
                    db,
                    listing=listing,
                    event_type="legacy_blocker_requeued",
                    stage="legacy_capability_requeue",
                    message=classification,
                    details=record,
                )
                requeued.append(listing.id)
                continue

            # Retain the exact original blocker and expose the reevaluation
            # verdict durably without asking the worker to revisit it until a
            # later capability-version change.
            record["next_eligible_reevaluation"] = "capability_version_change_or_new_evidence"
            metadata["processing_reevaluation"] = record
            listing.source_metadata = metadata
            listing.processing_stage = RECOVERY_LEGACY_HUMAN_STAGE
            listing.processing_lease_expires_at = None
            db.add(listing)
            self._record_event(
                db,
                listing=listing,
                event_type="legacy_blocker_preserved",
                stage=RECOVERY_LEGACY_HUMAN_STAGE,
                message=classification,
                details=record,
            )
            preserved += 1
        if not dry_run:
            db.commit()
        return {
            "capability_version": RECOVERY_LEGACY_REEVALUATION_VERSION,
            "examined": examined,
            "preserved_human": preserved,
            "requeued_listing_ids": requeued,
            "outcomes": [{"classification": key, "count": count} for key, count in sorted(outcomes.items())],
        }

    def reconcile_review_contract_rows(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        source_types: set[str] | None = None,
        listing_ids: set[int] | None = None,
        limit: int = 100,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove stale Needs Review markers when canonical readiness disagrees.

        This deliberately performs no enrichment or provider calls.  It is
        safe to run as a bounded convergence pass and will not touch published,
        sold, archived, or operator-approved records.
        """
        source_types = source_types or TARGET_SOURCE_TYPES
        active_remote_projection = exists(
            select(MarketplaceListing.id).where(
                MarketplaceListing.listing_id == Listing.id,
                MarketplaceListing.status.in_([
                    MarketplaceListingStatus.PENDING,
                    MarketplaceListingStatus.PUBLISHED,
                    MarketplaceListingStatus.UPDATED,
                    MarketplaceListingStatus.SOLD,
                    MarketplaceListingStatus.CLOSED,
                ]),
            )
        )
        query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            _not_archived_listing(),
            Listing.ebay_listing_id.is_(None),
            Listing.status.notin_([ListingStatus.PUBLISHED, ListingStatus.posted, ListingStatus.archived]),
            or_(Listing.ebay_publish_status.is_(None), Listing.ebay_publish_status.notin_(["POSTING", "POSTED"])),
            not_(active_remote_projection),
            Listing.sold_at.is_(None),
            Listing.needs_review.is_(True),
        )
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        if listing_ids is not None:
            query = query.where(Listing.id.in_(sorted(listing_ids)))
        query = query.order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, limit))
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)

        changed: list[dict[str, Any]] = []
        for row in db.execute(query).scalars().all():
            metadata = row.source_metadata if isinstance(row.source_metadata, dict) else {}
            if any(metadata.get(key) for key in ("operator_approved_at", "approval_attempted_at", "operator_approved_by_user_id")):
                continue
            quality_blockers = _machine_review_quality_blockers(row)
            if quality_blockers:
                reason = quality_blockers[0]
                changed.append({"listing_id": row.id, "queue": "QUALITY_REPAIR_REQUIRED", "state": PROCESSING_QUALITY_REPAIR_REQUIRED, "reason": reason, "quality_blockers": quality_blockers})
                if dry_run:
                    continue
                row.needs_review = False
                row.processing_blocking_reason = reason
                # Classification is not authorization to spend AI tokens.
                row.processing_state = PROCESSING_QUALITY_REPAIR_REQUIRED
                row.processing_stage = "machine_quality_repair"
                row.processing_next_retry_at = None
                row.processing_lease_expires_at = None
                db.add(row)
                self._record_event(
                    db, listing=row, event_type="review_contract_invalidated", stage="machine_quality_repair",
                    message=reason, details={"quality_blockers": quality_blockers},
                )
                continue
            readiness = canonical_listing_readiness(row)
            if (
                readiness.get("review_required")
                and str(readiness.get("queue") or "").upper() == "NEEDS_REVIEW"
            ):
                continue
            reasons = list(dict.fromkeys(
                list(readiness.get("canonical_blocking_reasons") or [])
                + list(readiness.get("destination_blocking_reasons") or [])
                + list(readiness.get("blocking_reasons") or [])
            ))
            queue = str(readiness.get("queue") or "").upper()
            next_state = PROCESSING_QUEUED if queue == "PROCESSING" or readiness.get("destination_preflight_pending") else PROCESSING_NEEDS_ATTENTION
            reason = str(reasons[0]) if reasons else "Current marketplace preflight is not publishable"
            changed.append({"listing_id": row.id, "queue": queue, "state": next_state, "reason": reason})
            if dry_run:
                continue
            row.needs_review = False
            row.processing_blocking_reason = reason
            row.processing_state = next_state
            row.processing_stage = "preflight" if readiness.get("destination_preflight_pending") else (row.processing_stage or "quality_validation")
            row.processing_next_retry_at = None
            row.processing_lease_expires_at = None
            db.add(row)
            self._record_event(
                db,
                listing=row,
                event_type="review_contract_invalidated",
                stage=row.processing_stage,
                message=reason,
                details={"queue": queue, "destination_preflight_pending": bool(readiness.get("destination_preflight_pending"))},
            )
        if changed and not dry_run:
            db.commit()
        return {"examined": len(changed), "invalidated": 0 if dry_run else len(changed), "dry_run": dry_run, "rows": changed}

    def admit_ai_repair_campaign(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        max_listings: int = 1,
        max_tokens: int = 10_000,
        worker_id: str = "explicit-ai-repair-campaign",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Explicitly and boundedly admit legacy quality repairs.

        This method is intentionally *not* called by Beat.  The caller must
        create a bounded campaign and accept its conservative token envelope;
        normal quality classification and reset maintenance cannot reach it.
        """
        max_listings = max(0, min(int(max_listings), 25))
        max_tokens = max(0, int(max_tokens))
        # A group can consume at most one 10k photo-analysis reservation.
        capacity = min(max_listings, max_tokens // 10_000)
        if capacity <= 0:
            return {"admitted_listing_ids": [], "max_listings": max_listings, "max_tokens": max_tokens, "reason": "campaign_token_envelope_too_small"}
        query = select(Listing).where(
            Listing.processing_state == PROCESSING_QUALITY_REPAIR_REQUIRED,
            Listing.needs_review.is_(False),
            _not_archived_listing(),
        ).order_by(Listing.updated_at.desc(), Listing.id.desc()).limit(capacity)
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        rows = db.execute(query).scalars().all()
        admitted = []
        for listing in rows:
            admitted.append(listing.id)
            if dry_run:
                continue
            listing.processing_state = PROCESSING_QUEUED
            listing.processing_stage = "ai_repair_queued"
            listing.processing_next_retry_at = None
            metadata = dict(listing.source_metadata or {})
            metadata["ai_repair_campaign"] = {
                "admitted_at": _now().isoformat(), "worker_id": worker_id,
                "max_tokens": max_tokens, "max_listings": max_listings,
            }
            listing.source_metadata = metadata
            self._record_event(db, listing=listing, event_type="ai_repair_campaign_admitted", stage="ai_repair_queued", message="Explicit bounded AI repair admission", details=metadata["ai_repair_campaign"])
        if admitted and not dry_run:
            db.commit()
        return {"admitted_listing_ids": admitted, "max_listings": max_listings, "max_tokens": max_tokens}

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
        # A prior sweep could requeue a terminal listing despite its frozen
        # operator-approved payload. Restore only stale machine-state fields;
        # never touch the approved content or its publish-job history.
        frozen_reconciled = 0
        if not dry_run:
            frozen_query = select(Listing).where(
                Listing.source_type.in_(sorted(source_types)),
                _not_archived_listing(),
                Listing.ebay_listing_id.is_(None),
                Listing.sold_at.is_(None),
                Listing.processing_state.in_([PROCESSING_QUEUED, PROCESSING_RETRY, PROCESSING_PROCESSING]),
            )
            if user_id is not None:
                frozen_query = frozen_query.where(Listing.user_id == user_id)
            frozen_rows = db.execute(frozen_query.order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, limit * 100))).scalars().all()
            for frozen in frozen_rows:
                metadata = frozen.source_metadata if isinstance(frozen.source_metadata, dict) else {}
                if not any(metadata.get(key) for key in ("operator_approved_at", "approval_attempted_at", "operator_approved_by_user_id")):
                    continue
                frozen.processing_state = PROCESSING_COMPLETE
                frozen.processing_stage = "operator_approved_payload_frozen"
                frozen.processing_lease_expires_at = None
                frozen.processing_next_retry_at = None
                frozen.processing_blocking_reason = None
                frozen.processing_last_error = None
                frozen.processing_error_stage = None
                db.add(frozen)
                frozen_reconciled += 1
            if frozen_reconciled:
                db.commit()
        # A crashed/abandoned worker can leave a listing in ``processing``
        # indefinitely.  An expired lease is authoritative evidence that no
        # worker still owns it; requeue it before selecting the next tranche so
        # the UI never reports Processing without a resumable job.
        stale_query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            _not_archived_listing(),
            _not_frozen_operator_payload(),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
            Listing.processing_state == PROCESSING_PROCESSING,
            or_(
                Listing.processing_lease_expires_at.is_(None),
                Listing.processing_lease_expires_at < now,
            ),
        )
        if user_id is not None:
            stale_query = stale_query.where(Listing.user_id == user_id)
        stale_query = stale_query.order_by(Listing.processing_lease_expires_at.asc(), Listing.id.asc()).limit(max(1, limit * 5))
        stale_rows = db.execute(stale_query).scalars().all()
        stale_requeued = 0
        for stale in stale_rows:
            stale.processing_state = PROCESSING_QUEUED
            stale.processing_stage = "stale_processing_requeue"
            stale.processing_lease_expires_at = None
            stale.processing_next_retry_at = None
            stale.processing_last_error = None
            stale.processing_error_stage = None
            db.add(stale)
            stale_requeued += 1
        if stale_requeued:
            db.commit()
        # Some older photo-derived rows were marked terminal by a quality-gate
        # pass even though no review marker, worker lease, or follow-up job was
        # ever recorded.  They are resumable machine work, not human blockers.
        stalled_terminal_query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            _not_archived_listing(),
            _not_frozen_operator_payload(),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
            Listing.needs_review.is_(False),
            Listing.processing_state.in_([PROCESSING_COMPLETE, "processed", "ready"]),
            Listing.processing_stage.in_(["quality_gate", "quality_validation"]),
            Listing.processing_lease_expires_at.is_(None),
        )
        if user_id is not None:
            stalled_terminal_query = stalled_terminal_query.where(Listing.user_id == user_id)
        stalled_terminal_rows = db.execute(
            stalled_terminal_query.order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, limit * 5))
        ).scalars().all()
        stalled_terminal_requeued = 0
        for stalled in stalled_terminal_rows:
            stalled.processing_state = PROCESSING_QUEUED
            stalled.processing_stage = "stalled_processing_requeue"
            stalled.processing_lease_expires_at = None
            stalled.processing_next_retry_at = None
            stalled.processing_last_error = None
            stalled.processing_error_stage = None
            stalled.processing_blocking_reason = None
            db.add(stalled)
            stalled_terminal_requeued += 1
        if stalled_terminal_requeued:
            db.commit()
        # Older quality passes can leave a machine-owned row in a terminal
        # state with no lease, retry timestamp, or follow-up job even though
        # canonical readiness still reports unfinished PosterPro work (most
        # commonly missing destination preflight). Requeue only rows that the
        # canonical contract explicitly classifies as Processing; genuine
        # human blockers remain Attention and are not disturbed.
        stalled_machine_query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            _not_archived_listing(),
            _not_frozen_operator_payload(),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
            Listing.needs_review.is_(False),
            Listing.processing_state.in_([PROCESSING_COMPLETE, "processed", "ready", PROCESSING_NEEDS_ATTENTION, PROCESSING_BLOCKED]),
            Listing.processing_next_retry_at.is_(None),
            Listing.processing_lease_expires_at.is_(None),
        )
        if user_id is not None:
            stalled_machine_query = stalled_machine_query.where(Listing.user_id == user_id)
        stalled_machine_rows = db.execute(
            stalled_machine_query.order_by(Listing.updated_at.asc(), Listing.id.asc()).limit(max(1, limit * 5))
        ).scalars().all()
        stalled_machine_requeued = 0
        stalled_destination_blockers = 0
        stalled_grouping_reviews_preserved = 0
        for stalled in stalled_machine_rows:
            if _has_explicit_grouping_review(stalled):
                stalled.processing_state = PROCESSING_NEEDS_ATTENTION
                stalled.processing_stage = "grouping_review"
                stalled.processing_error_stage = "grouping_review"
                stalled.processing_blocking_reason = stalled.processing_blocking_reason or _grouping_review_reason(stalled)
                stalled.processing_lease_expires_at = None
                db.add(stalled)
                stalled_grouping_reviews_preserved += 1
                continue
            destination_blocker = _fresh_ebay_preflight_blocker(stalled)
            if destination_blocker:
                # A fresh destination blocker is not stale machine work. Keep
                # the listing actionable in Needs Attention and let the
                # destination capability/policy change (or fresh preflight)
                # be the event that makes it eligible again.
                stalled.processing_state = PROCESSING_NEEDS_ATTENTION
                stalled.processing_stage = "preflight"
                stalled.processing_error_stage = "preflight"
                stalled.processing_blocking_reason = destination_blocker["message"]
                stalled.processing_lease_expires_at = None
                db.add(stalled)
                stalled_destination_blockers += 1
                continue
            readiness = canonical_listing_readiness(stalled)
            if str(readiness.get("queue") or "").upper() != "PROCESSING":
                continue
            stalled.processing_state = PROCESSING_QUEUED
            stalled.processing_stage = stalled.processing_stage or "stalled_processing_requeue"
            stalled.processing_next_retry_at = None
            stalled.processing_lease_expires_at = None
            stalled.processing_last_error = None
            stalled.processing_error_stage = None
            stalled.processing_blocking_reason = None
            db.add(stalled)
            stalled_machine_requeued += 1
        if stalled_machine_requeued or stalled_destination_blockers or stalled_grouping_reviews_preserved:
            db.commit()
        # Older workers set ``needs_review`` after canonical copy checks even
        # when destination preflight or specificity was still blocked.
        review_contract = self.reconcile_review_contract_rows(
            db,
            user_id=user_id,
            source_types=source_types,
            limit=max(1, limit * 5),
            dry_run=dry_run,
        )
        invalidated_review_rows = review_contract["invalidated"]
        self._requeue_now_reviewable_blocked_listings(db, user_id=user_id, source_types=source_types, limit=max(1, limit * 5))
        # Select actionable states in SQL before applying the finer-grained
        # recovery checks below.  The previous broad query could fill its
        # small oldest-row window with blocked/complete records and never
        # reach queued Google drafts, leaving a visible backlog that processed
        # zero items.
        actionable_states = or_(
            Listing.processing_state == PROCESSING_QUEUED,
            and_(
                Listing.processing_state == PROCESSING_RETRY,
                or_(Listing.processing_next_retry_at.is_(None), Listing.processing_next_retry_at <= now),
            ),
            and_(
                Listing.processing_state.in_([PROCESSING_BLOCKED, PROCESSING_NEEDS_ATTENTION, "failed"]),
                Listing.processing_next_retry_at.is_not(None),
                Listing.processing_next_retry_at <= now,
            ),
            Listing.processing_state.in_([PROCESSING_COMPLETE, "processed", "ready"]),
        )
        query = select(Listing).where(
            Listing.source_type.in_(sorted(source_types)),
            _not_archived_listing(),
            _not_frozen_operator_payload(),
            Listing.ebay_listing_id.is_(None),
            Listing.sold_at.is_(None),
            Listing.needs_review.is_(False),
            # Archived Vine history is retained for audit but must never fill
            # the bounded candidate window ahead of current owned inventory.
            actionable_states,
        )
        if user_id is not None:
            query = query.where(Listing.user_id == user_id)
        state_priority = case(
            (Listing.processing_state == PROCESSING_QUEUED, 0),
            (Listing.processing_state == PROCESSING_RETRY, 1),
            (Listing.processing_state == PROCESSING_PROCESSING, 2),
            (Listing.processing_state.in_([PROCESSING_BLOCKED, PROCESSING_NEEDS_ATTENTION, "failed"]), 3),
            else_=4,
        )
        if source_types == {"media_inventory_recovery"}:
            # Recovery groups have a durable numeric work order.  Highest
            # group first is the owner-requested Google intake priority, and
            # keeping the SQL selection to one leased row preserves the
            # vertical, complete-one-item-before-next workflow.
            # Prefer actual per-group recovery ingestion chronology without
            # excluding legacy drafts that predate group linkage. PostgreSQL
            # cannot lock the nullable side of an outer join, so the claim
            # below explicitly locks only ``Listing``.
            query = query.outerjoin(
                MediaRecoveryItemGroup,
                MediaRecoveryItemGroup.draft_listing_id == Listing.id,
            ).outerjoin(
                MediaRecoveryRun,
                MediaRecoveryRun.id == MediaRecoveryItemGroup.run_id,
            ).order_by(
                state_priority,
                MediaRecoveryItemGroup.created_at.desc().nullslast(),
                MediaRecoveryRun.created_at.desc().nullslast(),
                MediaRecoveryItemGroup.id.desc().nullslast(),
                Listing.id.desc(),
            )
        elif source_types == {"intake_head_slate"}:
            # The dedicated Google/Slate lane is ordered by the actual source
            # capture chronology of the linked intake group.  Listing creation
            # timestamps are reconciliation artifacts and can be newer for an
            # old photo; they are not a business chronology.  A newer valid
            # group therefore wins over an older retry, with stable fallbacks.
            intake_item_id = Listing.source_metadata["intake"]["item_id"].as_string()
            query = query.outerjoin(
                IntakePhotoBatch,
                and_(
                    IntakePhotoBatch.user_id == Listing.user_id,
                    IntakePhotoBatch.item_id == intake_item_id,
                ),
            )
            group_capture_at = (
                select(func.max(func.coalesce(IntakePhoto.captured_at, IntakePhoto.imported_at, IntakePhoto.created_at)))
                .where(IntakePhoto.batch_id == IntakePhotoBatch.id)
                .correlate(IntakePhotoBatch)
                .scalar_subquery()
            )
            query = query.order_by(
                group_capture_at.desc().nullslast(),
                state_priority,
                IntakePhotoBatch.id.desc().nullslast(),
                Listing.id.desc(),
            )
        else:
            query = query.order_by(state_priority, Listing.updated_at.asc(), Listing.id.asc())
        query = query.limit(max(1, limit * 100))
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(of=Listing, skip_locked=True)
        candidates = []
        for listing in db.execute(query).scalars().all():
            if self._is_terminal(listing):
                continue
            if _has_explicit_grouping_review(listing):
                # Persist the human boundary review rather than repeatedly
                # entering the enrichment pipeline with unchanged grouping
                # evidence.
                blocker_reason = _grouping_review_reason(listing)
                if (
                    listing.processing_state != PROCESSING_NEEDS_ATTENTION
                    or not listing.processing_blocking_reason
                    or listing.processing_stage != "grouping_review"
                ):
                    self._mark_listing_needs_attention(
                        db,
                        listing=listing,
                        stage="grouping_review",
                        worker_id=worker_id,
                        blocking_reason=blocker_reason,
                        message=blocker_reason,
                    )
                    listing.needs_review = False
                continue
            state = str(listing.processing_state or PROCESSING_QUEUED).strip().lower()
            if state in {PROCESSING_QUEUED, PROCESSING_RETRY}:
                candidates.append(listing)
                continue
            next_retry = _ensure_aware(listing.processing_next_retry_at)
            if state in {PROCESSING_BLOCKED, PROCESSING_NEEDS_ATTENTION, "failed"} and next_retry and next_retry <= now:
                candidates.append(listing)
                continue
            if state == PROCESSING_COMPLETE:
                # A previous pass can have recorded ``complete`` while the
                # canonical marketplace contract still reports Processing
                # (most commonly a category/aspect/preflight repair).  Those
                # rows are machine work, not terminal inventory.  Re-enter
                # the bounded pipeline instead of leaving them parked forever.
                canonical = canonical_listing_readiness(listing)
                if (
                    str(canonical.get("queue") or "").upper() == "PROCESSING"
                    or self._recovery_requires_refresh(listing)
                ):
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
                if fresh is not None and hydrate_authoritative_listing_price(fresh):
                    db.add(fresh)
                if fresh is not None and resolve_authoritative_listing_price(fresh) is None:
                    fresh.needs_review = False
                    self._mark_listing_needs_attention(
                        db,
                        listing=fresh,
                        stage="pricing_research",
                        worker_id=worker_id,
                        blocking_reason="MISSING_VALID_PRICE",
                        message="No authoritative positive listing price is available.",
                        details={"reason_code": "MISSING_VALID_PRICE", "source": "resume_backlog"},
                    )
                    db.commit()
                elif fresh is not None and not fresh.needs_review:
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
                "stale_requeued": stale_requeued,
                "stalled_terminal_requeued": stalled_terminal_requeued,
                "stalled_machine_requeued": stalled_machine_requeued,
                "stalled_destination_blockers_preserved": stalled_destination_blockers,
                "stalled_grouping_reviews_preserved": stalled_grouping_reviews_preserved,
                "invalidated_unpublishable_reviews": invalidated_review_rows,
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
            _not_archived_listing(),
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
            if not dry_run and not result.get("ready_for_review"):
                fresh = db.get(Listing, listing.id)
                metadata = fresh.source_metadata if fresh and isinstance(fresh.source_metadata, dict) else {}
                frozen_approval = any(
                    metadata.get(key)
                    for key in ("operator_approved_at", "approval_attempted_at", "operator_approved_by_user_id")
                )
                # An image-identification failure is machine-incomplete and
                # cannot remain in Needs Review because of an old marker.
                # Never clear an operator-approved payload's review state.
                if fresh and fresh.needs_review and not frozen_approval:
                    fresh.needs_review = False
                    db.add(fresh)
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
            if not dry_run and not result.get("ready_for_review"):
                # This backlog is specifically repairing records carrying an
                # old review marker. If the current quality/preflight gate did
                # not pass, clear that marker so the record cannot remain in
                # the operator-facing review queue while machine work remains.
                fresh = db.get(Listing, listing.id)
                if fresh is not None and fresh.needs_review:
                    fresh.needs_review = False
                    db.add(fresh)
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
        reconciled = 0
        for listing in db.execute(query).scalars().all():
            if self._is_terminal(listing):
                continue
            # Canonical completeness is not sufficient to requeue a draft if
            # a fresh destination preflight has already proved it cannot be
            # executed. This path previously cleared the exact eBay Motors
            # Trading-API blocker because it considered only generic product
            # quality, producing an endless blocked -> queued -> blocked loop.
            if bool(getattr(listing, "restricted_review_required", False)):
                restricted = getattr(listing, "restricted_reasons", None) or []
                reason_text = ", ".join(str(reason).replace("-", " ") for reason in restricted if str(reason).strip())
                listing.processing_state = PROCESSING_NEEDS_ATTENTION
                listing.needs_review = False
                listing.processing_error_stage = "policy_review"
                listing.processing_blocking_reason = (
                    f"Marketplace policy review required: {reason_text or 'restricted item'}; "
                    "confirm each selected marketplace permits this product before assigning a category or publishing."
                )
                db.add(listing)
                reconciled += 1
                continue
            marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
            preflight_state = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else {}
            by_marketplace = preflight_state.get("by_marketplace") if isinstance(preflight_state.get("by_marketplace"), dict) else {}
            ebay_preflight = by_marketplace.get("ebay") if isinstance(by_marketplace.get("ebay"), dict) else None
            if (
                isinstance(ebay_preflight, dict)
                and not bool(preflight_state.get("stale") or ebay_preflight.get("stale"))
                and str(ebay_preflight.get("status") or "").strip().lower() not in {"ready", "ready_with_warnings", "needs_review", "published"}
                and bool(ebay_preflight.get("blockers"))
            ):
                blockers = ebay_preflight.get("blockers") or []
                first = blockers[0] if blockers else {}
                message = str(first.get("message") or first.get("user_message") or first.get("code") or "Current eBay preflight is blocked") if isinstance(first, dict) else str(first)
                listing.processing_state = PROCESSING_NEEDS_ATTENTION
                listing.needs_review = False
                listing.processing_error_stage = "preflight"
                listing.processing_blocking_reason = message
                db.add(listing)
                reconciled += 1
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
        if requeued or reconciled:
            db.commit()
        return requeued

    def _ensure_machine_pricing(self, db: Session, *, listing: Listing) -> dict[str, Any] | None:
        """Persist honest pricing evidence before a machine draft is reviewable.

        Older recovery and Head-Slate paths wrote generated/default values
        directly to ``listing_price``. A price with no analysis is not a
        researched price, so it is replaced by the shared research service and
        explicitly marked as a comp-backed result or FALLBACK_ESTIMATE.
        """
        marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        analysis = marketplace_data.get("pricing_analysis") if isinstance(marketplace_data.get("pricing_analysis"), dict) else {}
        provenance = str(analysis.get("provenance") or analysis.get("pricing_source") or "").strip().lower()
        source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
        source_retail_present = any(source_metadata.get(key) for key in ("estimated_tax_value", "source_price", "msrp", "retail_price"))
        invalid_source_retail_label = provenance in {"source_msrp", "source_retail_evidence"} and not source_retail_present
        if provenance and provenance not in {"fallback", "unknown", "placeholder", "legacy"} and not invalid_source_retail_label:
            return analysis
        result = PricingIntelligenceService().recommend_price(
            db,
            listing.id or 0,
            ignore_unproven_listing_price=True,
        )
        manual_price = marketplace_data.get("manual_price_override")
        if not manual_price:
            recommended = result.get("recommended_price")
            try:
                recommended_price = round(float(recommended), 2)
            except (TypeError, ValueError):
                recommended_price = None
            if recommended_price and recommended_price > 0:
                listing.listing_price = recommended_price
                listing.suggested_price = recommended_price
                listing.buy_it_now_price = recommended_price
                db.add(listing)
                db.commit()
                db.refresh(listing)
        return result

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
        # Operator approval freezes the canonical payload.  Background
        # enrichment must not rewrite title/description/specifics/category
        # between preflight and the durable publish worker; doing so creates a
        # false payload-drift failure after the operator has already approved
        # the exact listing.  An explicit new approval/retry can clear these
        # markers and re-enter processing deliberately.
        approval_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
        if not dry_run and (
            approval_metadata.get("operator_approved_at")
            or approval_metadata.get("approval_attempted_at")
            or approval_metadata.get("operator_approved_by_user_id")
        ):
            return {
                "listing_id": listing.id,
                "source_type": listing.source_type,
                "status": "skipped_operator_approved_payload",
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
        if source_type == "intake_head_slate":
            source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
            intake_metadata = source_metadata.get("intake") if isinstance(source_metadata.get("intake"), dict) else {}
            source_facts = source_metadata.get("source_facts") if isinstance(source_metadata.get("source_facts"), dict) else {}
            intake_item_id = str(intake_metadata.get("item_id") or "").strip()
            group_kind = str(source_facts.get("group_kind") or "").strip().lower()
            grouping_quality_gate = str(source_facts.get("quality_gate") or "").strip().lower()
            if group_kind == "multiple_unrelated_products" or grouping_quality_gate == "needs_grouping_review":
                reason = (
                    "Saved photo analysis found multiple unrelated products in this slate group. "
                    "Review the group boundary before drafting so separate items are not combined."
                )
                repair_result.update({
                    "grouping_review": True,
                    "grouping_review_reason": reason,
                    "group_item_id": intake_item_id or None,
                    "group_kind": group_kind or None,
                    "identity_conflicts": source_facts.get("conflicting_evidence") or [],
                })
                if not dry_run:
                    source_metadata = dict(source_metadata)
                    source_metadata["grouping_review"] = {
                        "status": "required",
                        "reason": reason,
                        "group_kind": group_kind or None,
                        "identity_conflicts": source_facts.get("conflicting_evidence") or [],
                        "item_id": intake_item_id or None,
                        "checked_at": _now().isoformat(),
                    }
                    listing.source_metadata = source_metadata
            if not repair_result.get("grouping_review") and intake_item_id:
                batch = db.execute(
                    select(IntakePhotoBatch).where(
                        IntakePhotoBatch.user_id == listing.user_id,
                        IntakePhotoBatch.item_id == intake_item_id,
                    ).order_by(IntakePhotoBatch.id.desc()).limit(1)
                ).scalar_one_or_none()
                public_count = int(getattr(batch, "public_photo_count", 0) or 0) if batch else 0
                batch_status = str(getattr(batch, "status", "") or "").strip().lower() if batch else ""
                if public_count > MAX_AUTO_DRAFT_GROUP_PHOTOS and batch_status in {"drafted", "closed", "complete"}:
                    reason = (
                        f"This slate group contains {public_count} listing photos, beyond the "
                        f"{MAX_AUTO_DRAFT_GROUP_PHOTOS}-photo automatic grouping limit. Review the group boundary "
                        "before drafting so photos from different items are not combined."
                    )
                    repair_result.update({
                        "grouping_review": True,
                        "grouping_review_reason": reason,
                        "group_photo_count": public_count,
                        "group_item_id": intake_item_id,
                    })
                    if not dry_run:
                        source_metadata = dict(source_metadata)
                        source_metadata["grouping_review"] = {
                            "status": "required",
                            "reason": reason,
                            "public_photo_count": public_count,
                            "threshold": MAX_AUTO_DRAFT_GROUP_PHOTOS,
                            "item_id": intake_item_id,
                            "checked_at": _now().isoformat(),
                        }
                        listing.source_metadata = source_metadata
                        listing.needs_review = False
                        listing.processing_state = PROCESSING_NEEDS_ATTENTION
                        listing.processing_stage = "grouping_review"
                        listing.processing_error_stage = "grouping_review"
                        listing.processing_blocking_reason = reason
                        listing.processing_next_retry_at = None
                        listing.processing_lease_expires_at = None
                        db.add(listing)
                        self._record_event(
                            db,
                            listing=listing,
                            event_type="grouping_review_required",
                            stage="grouping_review",
                            message=reason,
                            details={"item_id": intake_item_id, "public_photo_count": public_count, "threshold": MAX_AUTO_DRAFT_GROUP_PHOTOS},
                        )
                        db.commit()
                    return {
                        **repair_result,
                        "status": PROCESSING_NEEDS_ATTENTION,
                        "ready_for_review": False,
                        "blocking": [reason],
                    }
        vine_category_changed = False
        if source_type == "amazon_vine":
            # Vine category validity is resolved against PosterPro's synced
            # local tree before readiness is evaluated. This avoids treating
            # an unpersisted validation snapshot as a missing category and
            # prevents per-listing Taxonomy API requests during queue retries.
            category_result = validate_or_resolve_vine_category(db, listing, dry_run=dry_run)
            repair_result["local_category"] = category_result
            vine_category_changed = bool(category_result.get("changed"))
        if not dry_run:
            # A broad legacy label such as "Fashion" or "Automotive Parts"
            # is not a resolved canonical category when the identified title
            # names a known product family. Prefer the reusable deterministic
            # path before falling back to destination taxonomy work.
            category_hint = str(listing.category_suggestion or "").strip()
            if _needs_category_refresh(category_hint) or ">" not in category_hint:
                canonical_category, category_method = suggest_category_from_text(
                    listing.title or "",
                    listing.description or "",
                    category_hint,
                    " ".join(str(value) for value in (listing.tags or []) if str(value).strip()),
                    " ".join(str(value) for value in (listing.item_specifics or {}).values() if value),
                )
                if canonical_category and canonical_category != "Other > Needs category review":
                    listing.category_suggestion = canonical_category
                    metadata = dict(listing.source_metadata or {})
                    metadata["canonical_category"] = {
                        "path": canonical_category,
                        "method": category_method,
                        "confidence": "high" if category_method == "keyword_rules" else "low",
                        "resolved_at": _now().isoformat(),
                    }
                    listing.source_metadata = metadata
                    if not listing.category_id:
                        listing.category_id = verified_category_id(canonical_category)
                    db.add(listing)
                    db.commit()
                    db.refresh(listing)
            # Pricing is part of canonical draft readiness, including the
            # already-polished fast path below.  Run this before that shortcut
            # so a legacy $19.99 field can never bypass the provenance repair.
            early_pricing = self._ensure_machine_pricing(db, listing=listing)
            if early_pricing:
                repair_result["pricing"] = {
                    "provenance": early_pricing.get("provenance"),
                    "pricing_source": early_pricing.get("pricing_source"),
                    "comp_count_used": early_pricing.get("comp_count_used"),
                    "recommended_price": early_pricing.get("recommended_price"),
                }
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
        # Recovery rows may have durable evidence that is newer or more
        # specific than the denormalized listing fields.  Compute this before
        # the fast-path so a stale historical draft is enriched instead of
        # being incorrectly treated as already complete (and, importantly,
        # avoid referencing a variable only initialized inside the recovery
        # worker below).
        recovery_refinement_needed = (
            self._recovery_requires_refresh(listing)
            if source_type == "media_inventory_recovery"
            else False
        )
        vine_category_is_current = True
        vine_preflight_is_current = True
        if source_type == "amazon_vine":
            # The already-ready shortcut used to bypass the local-tree audit.
            # That let an old valid-but-wrong leaf (and a stale/missing eBay
            # preflight) remain Ready indefinitely. Only skip deterministic
            # work when the currently assigned Vine leaf is semantically
            # valid locally AND its destination preflight is fresh.
            category_check = validate_or_resolve_vine_category(db, listing, dry_run=True)
            vine_category_is_current = category_check.get("status") == "validated" and not category_check.get("changed")
            preflight_state = listing.marketplace_data.get("marketplace_preflight") if isinstance(listing.marketplace_data, dict) else None
            by_marketplace = preflight_state.get("by_marketplace") if isinstance(preflight_state, dict) else None
            ebay_preflight = by_marketplace.get("ebay") if isinstance(by_marketplace, dict) else None
            vine_preflight_is_current = bool(
                isinstance(ebay_preflight, dict)
                and not preflight_state.get("stale")
                and not ebay_preflight.get("stale")
                and str(ebay_preflight.get("status") or "").lower() in {"ready", "ready_with_warnings", "needs_review", "published"}
                and not ebay_preflight.get("blockers")
            )
        if (
            not dry_run
            and current_quality.get("ready_for_publish_queue")
            and not polish_needed
            and not recovery_title_guard
            and not recovery_refinement_needed
            and vine_category_is_current
            and vine_preflight_is_current
        ):
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
            # A persisted intake grouping decision outranks item enrichment.
            # Do not rewrite copy or spend AI capacity while its evidence says
            # one timeline group contains unrelated products.
            if not repair_result.get("grouping_review"):
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
            # Resolve canonical category from the identified product for every
            # intake lane, not only Vine/recovery retry branches. Marketplace
            # IDs remain optional destination mappings; the canonical path is
            # useful and durable even when a destination tree lacks a leaf.
            if _needs_category_refresh(refreshed.category_suggestion):
                canonical_category, category_method = suggest_category_from_text(
                    refreshed.title or "",
                    refreshed.description or "",
                    refreshed.category_suggestion,
                    " ".join(str(value) for value in (refreshed.tags or []) if str(value).strip()),
                    " ".join(str(value) for value in (refreshed.item_specifics or {}).values() if value),
                )
                if canonical_category and canonical_category != "Other > Needs category review":
                    refreshed.category_suggestion = canonical_category
                    metadata = dict(refreshed.source_metadata or {})
                    metadata["canonical_category"] = {
                        "path": canonical_category,
                        "method": category_method,
                        "confidence": "high" if category_method == "keyword_rules" else "low",
                        "resolved_at": _now().isoformat(),
                    }
                    refreshed.source_metadata = metadata
            if not refreshed.category_id:
                refreshed.category_id = verified_category_id(refreshed.category_suggestion)
            pricing_result = self._ensure_machine_pricing(db, listing=refreshed)
            if pricing_result:
                repair_result["pricing"] = {
                    "provenance": pricing_result.get("provenance"),
                    "pricing_source": pricing_result.get("pricing_source"),
                    "comp_count_used": pricing_result.get("comp_count_used"),
                    "recommended_price": pricing_result.get("recommended_price"),
                }
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
                if _generic_placeholder(refreshed.title) and not repair_result.get("recovery_identity_meaningful") and allow_image_identification:
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
            # Processing must include the same destination preflight that
            # gates Needs Review. Older Vine and photo-intake rows reached the
            # quality gate without one, leaving them permanently parked as
            # "preflight pending" despite completed product work.
            if source_type in {"amazon_vine", "intake_head_slate", "google_photos_album"}:
                existing_preflight = refreshed.marketplace_data.get("marketplace_preflight") if isinstance(refreshed.marketplace_data, dict) else None
                existing_by_market = existing_preflight.get("by_marketplace") if isinstance(existing_preflight, dict) else {}
                marketplace_data = refreshed.marketplace_data if isinstance(refreshed.marketplace_data, dict) else {}
                targets = {str(value).strip().lower() for value in (marketplace_data.get("targets") or []) if str(value).strip()}
                channels = marketplace_data.get("channels") if isinstance(marketplace_data.get("channels"), dict) else {}
                if channels and source_type != "amazon_vine":
                    targets = {
                        target for target in targets
                        if not isinstance(channels.get(target), dict) or bool(channels[target].get("enabled"))
                    }
                # Vine publishing is explicitly eBay-only. For photo intake,
                # absent targets use the product's default eBay destination.
                if source_type == "amazon_vine":
                    targets = {"ebay"}
                elif not targets:
                    targets = {"ebay"}
                for target in sorted(targets):
                    cached = existing_by_market.get(target) if isinstance(existing_by_market, dict) else None
                    stale = not isinstance(existing_preflight, dict) or bool(existing_preflight.get("stale")) or not isinstance(cached, dict) or bool(cached.get("stale"))
                    if source_type == "amazon_vine" and vine_category_changed:
                        stale = True
                    if stale:
                        try:
                            service = MarketplacePreflightService()
                            preflight = service.preflight_listing(db, refreshed, target)
                            service.cache_preflight_summary(db, refreshed, preflight)
                            db.flush()
                            existing_by_market = (refreshed.marketplace_data or {}).get("marketplace_preflight", {}).get("by_marketplace", existing_by_market)
                        except Exception as exc:  # destination state remains explicit
                            logger.warning("Listing destination preflight refresh failed", extra={"listing_id": refreshed.id, "marketplace": target, "error": str(exc)})
            # Readiness cannot pass while this same worker still presents the
            # row as actively processing. Evaluate the candidate terminal
            # state, then below either commit it as complete or replace it
            # with a retry/attention state based on the exact blockers.
            refreshed.processing_state = PROCESSING_COMPLETE
            refreshed.processing_stage = "quality_gate"
            refreshed.processing_blocking_reason = None
            refreshed.processing_lease_expires_at = None
            # The queue worker must use the same canonical readiness contract
            # as API/UI/publish consumers.  The legacy summary above remains
            # persisted for compatibility, but it cannot be the final gate
            # because it does not evaluate canonical description quality.
            canonical_result = canonical_listing_readiness(refreshed)
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

            # Evaluate the state produced by this pass. Using the pre-repair
            # snapshot here could strand a successfully enriched item in a
            # retry/attention state even after category, images, shipping, or
            # identity were repaired.
            review_blockers = list(readiness.get("blockers") or [])
            review_blockers.extend(quality.get("specificity_blockers") or [])
            review_blockers.extend(canonical_result.get("blocking_reasons") or [])
            review_blockers.extend(canonical_result.get("destination_blocking_reasons") or [])
            review_ready = bool(
                canonical_result.get("review_required")
                and str(canonical_result.get("queue") or "").upper() == "NEEDS_REVIEW"
            )
            if quality.get("specificity_status") == "trusted_for_draft" and not review_blockers and review_ready:
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
            if not blockers and not review_ready:
                blockers = ["Current destination preflight is missing, stale, or not publishable"]
            warnings = list(quality.get("warnings") or current_readiness.get("warnings") or [])
            blocker_text = " ".join(blockers).lower()
            last_error_text = str(refreshed.processing_last_error or "").lower()
            provider_rate_limited = any(token in f"{blocker_text} {last_error_text}" for token in (
                "http 429", "status 429", "rate limit", "too many requests", "throttl"
            ))
            retryable = provider_rate_limited or bool(any(token in blocker_text for token in (
                "missing image", "missing photos", "no images", "primary image",
                "price missing", "category missing", "validated marketplace category",
                "taxonomy", "type is required", "title missing", "description missing",
                "product-specific enrichment", "source prose", "source-page",
                "every selected marketplace must pass current preflight",
            )))
            # These blockers explicitly describe work PosterPro can still do
            # (image acquisition, description enrichment, taxonomy/category
            # resolution, or pricing).  Keep them in retry/backoff rather than
            # converting them into a human Attention state merely because a
            # previous worker pass hit its attempt threshold. Human Attention
            # is reserved for evidence that automation is genuinely exhausted.
            if retryable:
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
            # Metadata refresh derives a human-readable category hint from
            # Amazon's broad browse data. Revalidate that refreshed category
            # against both synchronized trees before readiness; otherwise an
            # old static alias can overwrite a correct Motors/local leaf (or
            # leave an obvious product category unvalidated) on every retry.
            updated["local_category"] = validate_or_resolve_vine_category(db, listing)
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
        # Preserve an explicit grouping-quality decision even when the
        # identity payload is intentionally generic.  Group-boundary review is
        # a higher-priority human action than product identity enrichment.
        image_quality_gate = str(image_identity.get("quality_gate") or "").strip().lower()
        image_group_kind = str(image_identity.get("group_kind") or "").strip().lower()
        if image_quality_gate == "needs_grouping_review" or image_group_kind == "multiple_unrelated_products":
            updated["grouping_review"] = True
            updated["grouping_review_reason"] = "photo_grouping_review_required"
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
        needs_repair = (
            _generic_placeholder(listing.title)
            or _generic_placeholder(listing.description)
            or _needs_description_enrichment(
                listing.description,
                source_type=listing.source_type,
                source_metadata=source_metadata,
            )
            or _needs_category_refresh(listing.category_suggestion)
            # A category display name is not enough for marketplace
            # publishability. Re-enter the enrichment path whenever a
            # Google/Slate draft has no verified taxonomy ID, so cached
            # verified mappings can be applied and unknown paths can remain
            # honestly unresolved for the live taxonomy worker.
            or (
                str(listing.source_type or "").strip().lower() in {"intake_head_slate", "google_photos_album"}
                and not str(listing.category_id or "").strip()
            )
            or specific.get("status") != "trusted_for_draft"
        )
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
                try:
                    sample = self.photo_enrichment.enrich_group(
                        sample_paths, db=db, user_id=listing.user_id,
                        lineage={"listing_id": listing.id, "operation": "legacy_repair"},
                    )
                except TypeError as exc:
                    # Keep older/test enrichment providers compatible while
                    # production providers receive durable ownership context.
                    # A provider that rejects only the newer keyword contract
                    # must not be recorded as an image-identification failure.
                    if "unexpected keyword argument" not in str(exc):
                        raise
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
            try:
                generated = self.listing_ai.generate(image_signals, db=db, user_id=listing.user_id, listing_id=listing.id)
            except TypeError as exc:
                # Keep lightweight/test providers and older integrations
                # compatible while the production provider receives durable
                # ownership context.
                if "unexpected keyword argument" not in str(exc):
                    raise
                generated = self.listing_ai.generate(image_signals)
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
                    if _usable_specific_value(value):
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
                if recovery_estimated_value:
                    source_metadata = _record_unverified_price_estimate(
                        source_metadata, "recovery_image_estimate", recovery_estimated_value,
                    )
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
                if estimated_value:
                    source_metadata = _record_unverified_price_estimate(
                        source_metadata, "image_identity_estimate", estimated_value,
                    )
            if _generic_placeholder(listing.title):
                listing.title = generated.get("title") or base_title or listing.title
            if _generic_placeholder(listing.description):
                listing.description = generated.get("description") or base_description or listing.description
            generated_description = _normalize_text(generated.get("description"))
            existing_canonical = _normalize_text(getattr(listing, "canonical_description", None))
            if (
                existing_canonical
                and not is_human_owned_field(source_metadata, "description")
                and len(existing_canonical) > len(_normalize_text(listing.description))
            ):
                # Some intake drafts already have a richer canonical master
                # from the initial group synthesis while the legacy display
                # field remained short. Prefer that verified master before
                # asking the marketplace adapter to render variants.
                listing.description = existing_canonical
            if (
                generated_description
                and not is_human_owned_field(source_metadata, "description")
                and (
                    _generic_placeholder(listing.description)
                    or _needs_description_enrichment(
                        listing.description,
                        source_type=listing.source_type,
                        source_metadata=source_metadata,
                    )
                )
                and len(generated_description) > len(_normalize_text(listing.description))
            ):
                listing.description = generated_description
            if not is_human_owned_field(source_metadata, "description") and listing.description:
                # Keep the rich master in sync with the machine-owned listing
                # copy; marketplace variants are regenerated below without
                # touching operator-edited destinations.
                listing.canonical_description = listing.description
            if _needs_category_refresh(listing.category_suggestion):
                category, _ = suggest_category_from_text(
                    listing.title or generated.get("title") or "",
                    listing.description or generated.get("description") or "",
                    listing.category_suggestion,
                    " ".join(generated.get("tags") or []),
                )
                if category:
                    listing.category_suggestion = category
            if not listing.category_id:
                # Persist an ID only when the canonical path is present in the
                # verified taxonomy cache. Unknown paths remain unresolved and
                # continue through readiness/AI escalation rather than getting
                # an invented marketplace category.
                listing.category_id = verified_category_id(listing.category_suggestion)
            if not listing.item_specifics:
                listing.item_specifics = generated.get("item_specifics") or {}
            if not listing.tags:
                listing.tags = generated.get("tags") or []
            if generated.get("estimated_value"):
                source_metadata = _record_unverified_price_estimate(
                    source_metadata, "listing_ai_estimate", generated.get("estimated_value"),
                )
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
            apply_generated_marketplace_drafts(listing, generated.get("marketplace_drafts"))
            # All intake sources converge on the same destination-copy model;
            # generated variants refresh here while operator-edited variants
            # remain protected by their provenance marker.
            persist_marketplace_description_variants(listing, regenerate_generated=True)
            listing.marketplace_data = {
                **(listing.marketplace_data or {}),
                "draft_previews": build_marketplace_draft_previews(listing),
                "destination_categories": destination_category_snapshot(listing),
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
        source_type = str(listing.source_type or "").strip().lower()
        source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
        needs_generated_enrichment = (
            _generic_placeholder(listing.title)
            or _generic_placeholder(listing.description)
            or (
                source_type in {"intake_head_slate", "google_photos_album", "photo_intake"}
                and (
                    not str(listing.category_id or "").strip()
                    or _needs_description_enrichment(
                        listing.description,
                        source_type=source_type,
                        source_metadata=source_metadata,
                    )
                )
            )
        )
        if needs_generated_enrichment:
            group_synthesis: dict[str, Any] = {}
            if source_type in {"intake_head_slate", "google_photos_album", "photo_intake"}:
                # Reuse a previously persisted coherent Google/Slate
                # synthesis before attempting provider work. This keeps the
                # catalog moving during an AI outage and preserves evidence
                # already extracted from the corrected photo group.
                cached_intelligence = source_metadata.get("listing_intelligence") if isinstance(source_metadata.get("listing_intelligence"), dict) else {}
                cached_synthesis = cached_intelligence.get("group_synthesis") if isinstance(cached_intelligence.get("group_synthesis"), dict) else {}
                if str(cached_synthesis.get("group_kind") or "").strip().lower() in {"one_item", "intentional_set"}:
                    group_synthesis = cached_synthesis
                image_paths = [
                    _resolve_local_media_path(str(image.get("storage_path") or image.get("local_path") or ""))
                    for image in (listing.listing_images or [])
                    if isinstance(image, dict)
                ]
                image_paths = [path for path in image_paths if path][:6]
                if image_paths:
                    try:
                        evidence = self.photo_enrichment.enrich_group(
                            image_paths, db=db, user_id=listing.user_id,
                            lineage={"listing_id": listing.id, "operation": "newest_intake" if source_type == "intake_head_slate" else "legacy_repair"},
                        )
                        provider_synthesis = evidence.get("group_synthesis") if isinstance(evidence, dict) and isinstance(evidence.get("group_synthesis"), dict) else {}
                        if provider_synthesis:
                            group_synthesis = provider_synthesis
                    except Exception as exc:  # noqa: BLE001
                        updated["photo_enrichment_error"] = str(exc)
                if group_synthesis:
                    # Cache photo-group analysis on the listing. The previous
                    # generic path used it in memory but did not persist it,
                    # causing every retry to repeat expensive image/provider
                    # work before reaching the same quality blocker.
                    listing_intelligence = source_metadata.get("listing_intelligence") if isinstance(source_metadata.get("listing_intelligence"), dict) else {}
                    source_metadata["listing_intelligence"] = {
                        **listing_intelligence,
                        "group_synthesis": group_synthesis,
                        "group_synthesis_cached_at": _now().isoformat(),
                    }
                    if not any(isinstance(source_metadata.get(key), dict) for key in ("amazon_product_facts", "source_facts", "product_facts")):
                        source_metadata["source_facts"] = group_synthesis
                    listing.source_metadata = source_metadata
                cached_identity = group_synthesis.get("identity") if isinstance(group_synthesis.get("identity"), dict) else {}
                cached_title = str(cached_identity.get("title") or group_synthesis.get("title") or "").strip()
                if cached_title and not is_human_owned_field(source_metadata, "title") and (_generic_placeholder(listing.title) or is_generic_sellable_title(listing.title)):
                    listing.title = cached_title
                    source_metadata = mark_field_provenance(source_metadata, field="title", provenance="listing_intelligence.group_synthesis")
                cached_category = str(group_synthesis.get("category") or "").strip()
                if cached_category and not is_human_owned_field(source_metadata, "category_suggestion") and (_needs_category_refresh(listing.category_suggestion) or not listing.category_id):
                    listing.category_suggestion = cached_category
                    source_metadata = mark_field_provenance(source_metadata, field="category_suggestion", provenance="listing_intelligence.group_synthesis")
                cached_specifics = group_synthesis.get("item_specifics") if isinstance(group_synthesis.get("item_specifics"), dict) else {}
                if cached_specifics and not is_human_owned_field(source_metadata, "item_specifics") and not listing.item_specifics:
                    listing.item_specifics = cached_specifics
            payload = {
                    "title_hint": listing.title or (listing.source_metadata or {}).get("product_name") or "Recovered inventory item",
                    "source_type": listing.source_type,
                    "image_count": len(listing.image_urls or []),
                    "existing_specifics": listing.item_specifics or {},
                    "photo_keywords": [str(listing.category_suggestion or ""), str(listing.source_type or "")],
                    "group_synthesis": group_synthesis,
                    # Pass bounded product evidence into the copy generator and
                    # its quality gate. Previously the Google path supplied
                    # only title/image count/category, so it could not write a
                    # fact-rich description even when the corrected group
                    # already had a synthesis or verified source facts.
                    "source_facts": (
                        source_metadata.get("amazon_product_facts")
                        or source_metadata.get("source_facts")
                        or source_metadata.get("product_facts")
                        or group_synthesis
                    ),
                    "source_metadata": {
                        "source_facts": (
                            source_metadata.get("amazon_product_facts")
                            or source_metadata.get("source_facts")
                            or source_metadata.get("product_facts")
                            or group_synthesis
                        )
                    },
            }
            try:
                generated = self.listing_ai.generate(payload, db=db, user_id=listing.user_id, listing_id=listing.id)
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                generated = self.listing_ai.generate(payload)
            if not is_human_owned_field(source_metadata, "title") and (_generic_placeholder(listing.title) or is_generic_sellable_title(listing.title)):
                generated_title = str(generated.get("title") or "").strip()
                if generated_title:
                    listing.title = generated_title
                    source_metadata = mark_field_provenance(source_metadata, field="title", provenance=generated.get("generation_source") or "listing_intelligence")
            generated_description = str(generated.get("description") or "").strip()
            current_description = str(listing.canonical_description or listing.description or "").strip()
            description_needs_repair = (
                _generic_placeholder(current_description)
                or _needs_description_enrichment(current_description, source_type=source_type, source_metadata=source_metadata)
            )
            if generated_description and description_needs_repair and not is_human_owned_field(source_metadata, "description"):
                current_quality = assess_description_quality(
                    current_description,
                    title=listing.title,
                    source_metadata=payload.get("source_metadata"),
                )
                generated_quality = assess_description_quality(
                    generated_description,
                    title=generated.get("title") or listing.title,
                    source_metadata=payload.get("source_metadata"),
                )
                # Provenance alone never passes quality. Only persist a
                # candidate that passes the same content gate as other copy;
                # otherwise leave the existing text and the blocker intact.
                if generated_quality.get("quality") == "reviewable":
                    listing.description = generated_description
                    listing.canonical_description = generated_description
                    source_metadata = mark_field_provenance(
                        source_metadata,
                        field="description",
                        provenance=generated.get("generation_source") or "listing_intelligence",
                    )
                    listing.source_metadata = source_metadata
                else:
                    updated["description_quality"] = generated_quality
                    updated["previous_description_quality"] = current_quality
            if not listing.item_specifics:
                listing.item_specifics = generated.get("item_specifics") or {}
            if _needs_category_refresh(listing.category_suggestion) and not is_human_owned_field(source_metadata, "category_suggestion"):
                category, _ = suggest_category_from_text(
                    listing.title or generated.get("title") or "",
                    listing.description or generated.get("description") or "",
                    listing.category_suggestion,
                    " ".join(generated.get("tags") or []),
                )
                if category:
                    listing.category_suggestion = category
                    source_metadata = mark_field_provenance(source_metadata, field="category_suggestion", provenance="listing_intelligence.category_resolver")
            if not listing.category_id:
                # Only persist IDs from the verified local taxonomy cache. A
                # missing cache entry remains an honest processing blocker;
                # never invent an ID from a source breadcrumb or title.
                listing.category_id = verified_category_id(listing.category_suggestion)
            listing.tags = listing.tags or generated.get("tags") or []
            listing.shipping_profile = derive_shipping_profile(
                listing={"title": listing.title, "description": listing.description, "listing_price": listing.listing_price or listing.suggested_price},
                item_specifics=listing.item_specifics,
                existing=listing.shipping_profile,
            )
            listing.source_metadata = source_metadata
            apply_generated_marketplace_drafts(listing, generated.get("marketplace_drafts"))
            persist_marketplace_description_variants(listing, regenerate_generated=True)
            listing.marketplace_data = {
                **(listing.marketplace_data or {}),
                "draft_previews": build_marketplace_draft_previews(listing),
                "destination_categories": destination_category_snapshot(listing),
            }
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
        return bool({"archived_vine", "archived_sold", "archived_ebay"} & labels)

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
        hydrate_authoritative_listing_price(listing)
        if resolve_authoritative_listing_price(listing) is None:
            # A completed machine pass is not a review-ready listing when no
            # real ask exists. Keep the failure explicit and actionable rather
            # than persisting a stale needs_review bit.
            listing.needs_review = False
            self._mark_listing_needs_attention(
                db,
                listing=listing,
                stage="pricing_research",
                worker_id=worker_id,
                blocking_reason="MISSING_VALID_PRICE",
                message="No authoritative positive listing price is available.",
                details={"reason_code": "MISSING_VALID_PRICE", **(details or {})},
            )
            return
        quality_blockers = _machine_review_quality_blockers(listing)
        if quality_blockers:
            listing.needs_review = False
            self._mark_listing_failure(
                db,
                listing=listing,
                stage="machine_quality_repair",
                error=RuntimeError(quality_blockers[0]),
                worker_id=worker_id,
                retryable=True,
                blocking_reason=quality_blockers[0],
                details={"quality_blockers": quality_blockers},
            )
            return
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
        if hydrate_authoritative_listing_price(fresh):
            db.add(fresh)
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
        canonical = canonical_listing_readiness(fresh)
        blockers = list(canonical.get("blocking_reasons") or [])
        blockers.extend(quality.get("specificity_blockers") or [])
        blockers.extend(_machine_review_quality_blockers(fresh))
        if (
            quality.get("specificity_status") != "trusted_for_draft"
            or blockers
            or not canonical.get("review_required")
            or str(canonical.get("queue") or "").upper() != "NEEDS_REVIEW"
        ):
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
