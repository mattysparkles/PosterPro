"""Deprecated V1 entry point retained as a safe guardrail.

The prior implementation could write a simplistic single-result enrichment.
Use `reprocess_recovery_validation_sample_v2.py` until the full-group audit is
complete; this command must never mutate recovery drafts.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import Listing, MediaRecoveryItemGroup
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.photo_enrichment import PhotoEnrichmentService


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


def main(limit: int = 10) -> None:
    raise RuntimeError(
        "Legacy recovery enrichment is disabled. Use the full-group evidence V2 reprocessor while recovery is frozen."
    )
    service = PhotoEnrichmentService()
    with SessionLocal() as db:
        groups = db.execute(
            select(MediaRecoveryItemGroup)
            .where(MediaRecoveryItemGroup.run_id == 1, MediaRecoveryItemGroup.grouping_status == "confirmed")
            .order_by(MediaRecoveryItemGroup.recovery_item_id)
        ).scalars().all()
        pending = [
            group for group in groups
            if not ((group.analysis_json or {}).get("image_enrichment_v1"))
            and (listing := db.get(Listing, group.draft_listing_id)) is not None
            and str(listing.title or "").startswith("Recovered photographed inventory item")
        ][:limit]
        result = {"processed": 0, "enriched": 0, "errors": 0, "listing_ids": []}
        for group in pending:
            result["processed"] += 1
            listing = db.get(Listing, group.draft_listing_id)
            if not listing or not group.media_paths_json:
                continue
            analysis = dict(group.analysis_json or {})
            try:
                evidence = service.enrich_group(list(group.media_paths_json or []))
                title = str(evidence.get("title") or "").strip()[:80]
                description = str(evidence.get("description") or "").strip()
                if not title or title.lower() in {"unknown", "unknown item"}:
                    raise ValueError("image analysis did not return a usable product title")
                listing.title = title
                listing.description = (
                    f"{description}\n\n"
                    "Evidence: recovered from the preserved, item-specific photo set. "
                    "Review all attached photos for condition, completeness, dimensions, and compatibility before publishing."
                )
                listing.category_suggestion = str(evidence.get("category_suggestion") or listing.category_suggestion or "General resale")[:255]
                specifics = evidence.get("item_specifics") if isinstance(evidence.get("item_specifics"), dict) else {}
                listing.item_specifics = {**(listing.item_specifics or {}), **{str(key).title(): str(value) for key, value in specifics.items() if value}}
                tags = evidence.get("tags") if isinstance(evidence.get("tags"), list) else []
                listing.tags = list(dict.fromkeys([str(tag) for tag in tags if str(tag).strip()] + list(listing.tags or [])))[:16]
                if evidence.get("estimated_value"):
                    price = round(float(evidence["estimated_value"]), 2)
                    listing.estimated_value = price
                    listing.suggested_price = price
                    listing.listing_price = price
                    listing.buy_it_now_price = price
                source = dict(listing.source_metadata or {})
                recovery = dict(source.get("recovery") or {})
                recovery["image_analysis"] = evidence
                recovery["photo_level_evidence"] = evidence.get("photo_evidence") or []
                recovery["fact_sources"] = evidence.get("fact_sources") or {}
                recovery["identity_status"] = "image_evidence_candidate"
                recovery["field_confidence"] = {**(recovery.get("field_confidence") or {}), "identity": 0.7}
                source["recovery"] = recovery
                listing.source_metadata = source
                preflight = _json_safe(MarketplacePreflightService().preflight_listing(db, listing, "ebay"))
                listing.marketplace_data = {**(listing.marketplace_data or {}), "ebay_preflight": preflight, "ready_for_ebay_review": preflight["status"] in {"ready", "ready_with_warnings"}, "needs_ebay_review": True}
                analysis["image_enrichment_v1"] = {"status": "completed", "evidence": evidence}
                group.analysis_json = analysis
                db.commit()
                result["enriched"] += 1
                result["listing_ids"].append(listing.id)
            except Exception as exc:  # keep the next bounded group moving
                analysis["image_enrichment_v1"] = {"status": "error", "error": str(exc)[:400]}
                group.analysis_json = analysis
                db.commit()
                result["errors"] += 1
        print(result)


if __name__ == "__main__":
    main()
