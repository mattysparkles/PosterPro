"""Reprocess existing recovery drafts with full-group evidence.

This command never creates a Listing.  It only updates existing recovery drafts
after their associated group has passed the coherent-group portion of V2.
"""
from __future__ import annotations

import json
import argparse
from collections import Counter

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import Listing, MediaRecoveryItemGroup, MediaRecoveryMedia, MediaRecoveryPhotoEvidence, MediaRecoveryRun
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.photo_enrichment import FULL_GROUP_EVIDENCE_PIPELINE_VERSION, PHOTO_EVIDENCE_PIPELINE_VERSION, PhotoEnrichmentService, quality_gate


def _safe(value):
    return json.loads(json.dumps(value, default=str))


def _locked(listing: Listing, field: str) -> bool:
    recovery = ((listing.source_metadata or {}).get("recovery") or {})
    return field in set(recovery.get("operator_locked_fields") or [])


def select_validation_groups(db, run_id: int, limit: int = 20) -> list[MediaRecoveryItemGroup]:
    """Stratified sample: five specific, five defaults, five low-photo, five complex."""
    groups = db.execute(select(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.run_id == run_id, MediaRecoveryItemGroup.draft_listing_id.is_not(None)).order_by(MediaRecoveryItemGroup.id)).scalars().all()
    buckets = {"specific": [], "default": [], "low_photo": [], "complex": []}
    for group in groups:
        listing = db.get(Listing, group.draft_listing_id)
        if not listing:
            continue
        paths = list(group.media_paths_json or [])
        generic = str(listing.title or "").lower().startswith("recovered photographed")
        if not generic: buckets["specific"].append(group)
        if float(listing.listing_price or 0) == 19.99: buckets["default"].append(group)
        if len(paths) <= 1: buckets["low_photo"].append(group)
        if len(paths) > 5 or group.grouping_status == "needs_grouping_review": buckets["complex"].append(group)
    chosen, used = [], set()
    for name in ("specific", "default", "low_photo", "complex"):
        selected = 0
        for group in buckets[name]:
            if group.id not in used:
                chosen.append(group); used.add(group.id)
                selected += 1
                if selected >= 5: break
    for group in groups:
        if len(chosen) >= limit: break
        if group.id not in used:
            chosen.append(group); used.add(group.id)
    return chosen[:limit]


def _is_excluded(media: MediaRecoveryMedia) -> str | None:
    if media.duplicate_of_media_id: return "exact_duplicate"
    if not (media.file_metadata_json or {}).get("readable", True): return "unreadable"
    slate = media.slate_evidence_json or {}
    if slate.get("classification") in {"confirmed_slate", "manual_confirmed_slate"}: return "confirmed_slate"
    if media.final_disposition in {"confirmed_slate", "probable_slate"}: return media.final_disposition
    return None


def _upsert_photo_evidence(db, *, run, group, listing, media, payload):
    row = db.execute(select(MediaRecoveryPhotoEvidence).where(MediaRecoveryPhotoEvidence.recovery_group_id == group.id, MediaRecoveryPhotoEvidence.media_id == media.id, MediaRecoveryPhotoEvidence.pipeline_version == PHOTO_EVIDENCE_PIPELINE_VERSION)).scalar_one_or_none()
    row = row or MediaRecoveryPhotoEvidence(run_id=run.id, recovery_group_id=group.id, media_id=media.id, listing_id=listing.id, pipeline_version=PHOTO_EVIDENCE_PIPELINE_VERSION)
    barcode_type, barcode_value = payload.get("decoded_barcode_type"), payload.get("decoded_barcode_value")
    row.listing_id, row.photo_role, row.ocr_text = listing.id, str(payload.get("photo_role") or "unclassified"), payload.get("ocr_text")
    row.barcode_attempts_json, row.decoded_barcode_type, row.decoded_barcode_value = payload.get("barcode_attempts") or [], barcode_type, barcode_value
    for field in ("brand", "product_name", "model", "mpn", "manufacturer_sku", "upc", "ean", "gtin", "isbn", "packaging_identity", "condition_evidence", "measurement_evidence", "testing_evidence", "confidence", "extraction_method", "error_status"):
        setattr(row, field, payload.get(field))
    row.specifications_json, row.included_components_json, row.damage_json = payload.get("specifications") or {}, payload.get("included_components") or [], payload.get("damage") or []
    row.evidence_json = {key: value for key, value in payload.items() if key not in {"barcode_attempts", "ocr_text"}}
    db.add(row)
    return row


def _payload_from_row(row: MediaRecoveryPhotoEvidence, media: MediaRecoveryMedia) -> dict:
    payload = dict(row.evidence_json or {})
    payload.update({
        "media_id": media.id, "photo_path": media.absolute_path, "photo_role": row.photo_role,
        "ocr_text": row.ocr_text, "barcode_attempts": row.barcode_attempts_json or [],
        "decoded_barcode_type": row.decoded_barcode_type, "decoded_barcode_value": row.decoded_barcode_value,
        "brand": row.brand, "product_name": row.product_name, "model": row.model, "mpn": row.mpn,
        "manufacturer_sku": row.manufacturer_sku, "upc": row.upc, "ean": row.ean, "gtin": row.gtin,
        "isbn": row.isbn, "specifications": row.specifications_json or {}, "packaging_identity": row.packaging_identity,
        "included_components": row.included_components_json or [], "damage": row.damage_json or [],
        "condition_evidence": row.condition_evidence, "measurement_evidence": row.measurement_evidence,
        "testing_evidence": row.testing_evidence, "confidence": row.confidence or 0.0,
        "extraction_method": row.extraction_method, "error_status": row.error_status,
    })
    return payload


def _apply_coherent_synthesis(db, listing: Listing, group: MediaRecoveryItemGroup, synthesis: dict) -> bool:
    before = {field: getattr(listing, field) for field in ("title", "description", "category_suggestion", "item_specifics", "condition", "estimated_value", "listing_price")}
    identity = synthesis.get("identity") or {}
    if not _locked(listing, "title") and identity.get("title"):
        listing.title = str(identity["title"])[:255]
    if not _locked(listing, "category_suggestion") and synthesis.get("category"):
        listing.category_suggestion = str(synthesis["category"])[:255]
    if not _locked(listing, "item_specifics"):
        specifics = synthesis.get("item_specifics") if isinstance(synthesis.get("item_specifics"), dict) else {}
        listing.item_specifics = {**(listing.item_specifics or {}), **specifics}
    if not _locked(listing, "condition") and synthesis.get("condition"):
        listing.condition = str(synthesis["condition"])[:64]
    if not _locked(listing, "description"):
        listing.description = (listing.description or "") + "\n\nFull-group evidence: " + str(synthesis.get("reason_selected") or "")
    source = dict(listing.source_metadata or {}); recovery = dict(source.get("recovery") or {})
    recovery[FULL_GROUP_EVIDENCE_PIPELINE_VERSION] = synthesis
    recovery["quality_gate"] = synthesis.get("quality_gate")
    recovery["identity_status"] = "coherent_all_photo_evidence_v3"
    recovery["before_after_diff_v3"] = {key: {"before": before[key], "after": getattr(listing, key)} for key in before if before[key] != getattr(listing, key)}
    source["recovery"] = recovery; listing.source_metadata = source
    return bool(recovery["before_after_diff_v3"])


def reprocess_group(db, *, run, group, service: PhotoEnrichmentService) -> dict:
    listing = db.get(Listing, group.draft_listing_id)
    media_by_path = {row.absolute_path: row for row in db.execute(select(MediaRecoveryMedia).where(MediaRecoveryMedia.run_id == run.id, MediaRecoveryMedia.absolute_path.in_(list(group.media_paths_json or [])))).scalars()}
    evidence = []; excluded = Counter()
    for path in list(group.media_paths_json or []):
        media = media_by_path.get(path)
        if not media: continue
        existing = db.execute(select(MediaRecoveryPhotoEvidence).where(MediaRecoveryPhotoEvidence.recovery_group_id == group.id, MediaRecoveryPhotoEvidence.media_id == media.id, MediaRecoveryPhotoEvidence.pipeline_version == PHOTO_EVIDENCE_PIPELINE_VERSION)).scalar_one_or_none()
        if existing:
            payload = _payload_from_row(existing, media)
            if payload.get("error_status"): excluded[payload["error_status"]] += 1
            evidence.append(payload)
            continue
        reason = _is_excluded(media)
        payload = {"media_id": media.id, "photo_path": media.absolute_path, "photo_role": "duplicate" if reason == "exact_duplicate" else "slate" if reason else "unclassified", "excluded_reason": reason, "error_status": reason, "barcode_attempts": [], "confidence": 0.0, "extraction_method": "exclusion"} if reason else service.extract_photo_evidence(media.absolute_path, media_id=media.id, metadata=media.file_metadata_json or {})
        if reason: excluded[reason] += 1
        evidence.append(payload); _upsert_photo_evidence(db, run=run, group=group, listing=listing, media=media, payload=payload)
        # Durable photo checkpoints make a large group safe to resume after a
        # worker/backend interruption without repeating expensive analysis.
        db.commit()
    synthesis = service.synthesize_group_evidence(evidence)
    synthesis["quality_gate"] = quality_gate(synthesis)
    analysis = dict(group.analysis_json or {}); analysis[FULL_GROUP_EVIDENCE_PIPELINE_VERSION] = synthesis; group.analysis_json = analysis
    changed = False
    if synthesis["group_kind"] == "multiple_unrelated_products":
        group.grouping_status = "needs_grouping_review"
        source = dict(listing.source_metadata or {}); recovery = dict(source.get("recovery") or {})
        recovery.update({FULL_GROUP_EVIDENCE_PIPELINE_VERSION: synthesis, "quality_gate": "needs_grouping_review", "identity_status": "blocked_mixed_group"})
        source["recovery"] = recovery; listing.source_metadata = source; listing.needs_review = True
    else:
        changed = _apply_coherent_synthesis(db, listing, group, synthesis)
    preflight = _safe(MarketplacePreflightService().preflight_listing(db, listing, "ebay"))
    listing.marketplace_data = {**(listing.marketplace_data or {}), "ebay_preflight": preflight, "ready_for_ebay_review": False, "needs_ebay_review": True}
    db.commit()
    return {"listing_id": listing.id, "photos": len(evidence), "excluded": dict(excluded), "mixed": synthesis["group_kind"] == "multiple_unrelated_products", "changed": changed, "quality_gate": synthesis["quality_gate"]}


def select_pending_recovery_groups(db, run_id: int, *, limit: int) -> list[MediaRecoveryItemGroup]:
    """Return a stable bounded slice of existing recovery drafts needing V3.

    This is intentionally draft-only: it never reaches broad parents without a
    draft and never creates a child or listing as part of a quality pass.
    """
    groups = db.execute(
        select(MediaRecoveryItemGroup)
        .where(
            MediaRecoveryItemGroup.run_id == run_id,
            MediaRecoveryItemGroup.draft_listing_id.is_not(None),
        )
        .order_by(MediaRecoveryItemGroup.id)
    ).scalars().all()
    return [
        group for group in groups
        if FULL_GROUP_EVIDENCE_PIPELINE_VERSION not in (group.analysis_json or {})
    ][:limit]


def main(limit: int = 20, max_groups: int | None = None, all_pending: bool = False) -> None:
    with SessionLocal() as db:
        run = db.get(MediaRecoveryRun, 1)
        if not run or run.draft_creation_state != "frozen_for_quality_audit":
            raise RuntimeError("Validation requires recovery run 1 to remain frozen_for_quality_audit")
        groups = select_pending_recovery_groups(db, run.id, limit=limit) if all_pending else select_validation_groups(db, run.id, limit)
        print({"selected_listing_ids": [group.draft_listing_id for group in groups]})
        pending = [group for group in groups if FULL_GROUP_EVIDENCE_PIPELINE_VERSION not in (group.analysis_json or {})]
        if max_groups is not None:
            pending = pending[:max_groups]
        service = PhotoEnrichmentService()
        results = []
        for group in pending:
            try:
                results.append(reprocess_group(db, run=run, group=group, service=service))
            except Exception as exc:
                # One damaged image, model outage, or legacy record must not
                # strand the rest of the audit queue.  Keep the failure on the
                # group itself so it is visible and retryable on the next run.
                db.rollback()
                failed_group = db.get(MediaRecoveryItemGroup, group.id)
                if failed_group:
                    analysis = dict(failed_group.analysis_json or {})
                    analysis[f"{FULL_GROUP_EVIDENCE_PIPELINE_VERSION}_error"] = {
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
                    failed_group.analysis_json = analysis
                    db.commit()
                results.append({"group_id": group.id, "listing_id": group.draft_listing_id,
                                "error": type(exc).__name__, "retryable": True})
        print({"processed": len(results), "results": results})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-groups", type=int)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--all-pending", action="store_true")
    args = parser.parse_args()
    main(limit=args.limit, max_groups=args.max_groups, all_pending=args.all_pending)
