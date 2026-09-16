"""Single, explainable readiness result for queue and publish consumers."""
from __future__ import annotations

from typing import Any
import re

from app.services.listing_review import summarize_listing_readiness


def canonical_listing_readiness(listing: Any, *, marketplace: str | None = None) -> dict[str, Any]:
    """Combine processing, content, and destination readiness without hiding blockers."""
    stored = listing.readiness_summary if isinstance(getattr(listing, "readiness_summary", None), dict) else {}
    base = summarize_listing_readiness(
        listing_images=getattr(listing, "listing_images", None),
        condition_data=getattr(listing, "condition_data", None),
        shipping_profile=getattr(listing, "shipping_profile", None),
        listing={"category_id": getattr(listing, "category_id", None), "category_suggestion": getattr(listing, "category_suggestion", None), "listing_price": getattr(listing, "listing_price", None), "suggested_price": getattr(listing, "suggested_price", None)},
        source_type=getattr(listing, "source_type", None),
    )
    blockers = list(dict.fromkeys([*(stored.get("blockers") or []), *(base.get("blockers") or [])]))
    warnings = list(dict.fromkeys([*(stored.get("warnings") or []), *(base.get("warnings") or [])]))
    processing_blocker = str(getattr(listing, "processing_blocking_reason", None) or "").strip()
    if processing_blocker:
        blockers.append(processing_blocker)
    missing_required_aspects = [str(value).strip() for value in (stored.get("missing_required_aspects") or []) if str(value).strip()]
    if missing_required_aspects:
        blockers.extend(f"Required marketplace detail missing: {value}" for value in missing_required_aspects)
    description = str(getattr(listing, "canonical_description", None) or getattr(listing, "description", None) or "").strip()
    title = str(getattr(listing, "title", None) or "").strip()
    source_type_value = str(getattr(listing, "source_type", None) or "").strip().lower()
    if re.search(r"(?:free\s+shipping|free\s+returns?|\d+[- ]day\s+(?:refund|return|replacement)|add\s+to\s+cart|buy\s+now|return\s+policy)", description, re.IGNORECASE):
        blockers.append("Description contains source-page policy or navigation text")
    source_metadata = getattr(listing, "source_metadata", None) if isinstance(getattr(listing, "source_metadata", None), dict) else {}
    source_facts = source_metadata.get("amazon_product_facts") if isinstance(source_metadata.get("amazon_product_facts"), dict) else source_metadata.get("source_facts")
    source_fact_count = 0
    source_facts_covered = 0
    if isinstance(source_facts, dict) and description:
        fact_values = [*(source_facts.get("feature_bullets") or []), *((source_facts.get("specifications") or {}).values() if isinstance(source_facts.get("specifications"), dict) else [])]
        fact_values = [str(value).strip() for value in fact_values if str(value or "").strip()]
        source_fact_count = len(fact_values)
        description_tokens = set(re.findall(r"[a-z0-9]{4,}", description.lower()))
        covered = sum(bool(description_tokens.intersection(set(re.findall(r"[a-z0-9]{4,}", value.lower())))) for value in fact_values)
        source_facts_covered = covered
        if len(fact_values) >= 4 and covered < max(2, min(4, len(fact_values) // 3)):
            blockers.append("Description does not cover enough verified product facts")
    if not description:
        blockers.append("Description is missing")
    elif len(description) < 180:
        # A short description may still be valid for a simple item, but it is
        # not evidence of a hard blocker by itself. Keep it visible as a
        # quality warning while preserving the operator-review flow.
        warnings.append("Description is brief; review product details before publishing")
        if source_type_value in {"amazon_vine", "google_photos_album", "photo_intake"}:
            words = [word for word in description.split() if word.strip()]
            unique_words = {word.strip(".,:;!?()[]{}\"'").lower() for word in words}
            if len(words) < 18 or len(unique_words) < 12:
                blockers.append("Listing description needs product-specific enrichment")
    if not title and source_type_value in {"amazon_vine", "google_photos_album", "photo_intake"}:
        blockers.append("Product title is missing")
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    preflight = (getattr(listing, "marketplace_data", None) or {}).get("marketplace_preflight") if isinstance(getattr(listing, "marketplace_data", None), dict) else None
    if marketplace and isinstance(preflight, dict):
        row = (preflight.get("by_marketplace") or {}).get(str(marketplace).lower()) if isinstance(preflight.get("by_marketplace"), dict) else None
        if isinstance(row, dict):
            blockers.extend(str(item.get("message") or item.get("code") or item) for item in (row.get("blockers") or []) if item)
            warnings.extend(str(item.get("message") or item.get("code") or item) for item in (row.get("warnings") or []) if item)
            blockers = list(dict.fromkeys(blockers))
            warnings = list(dict.fromkeys(warnings))
    if str(marketplace or "").lower() == "ebay" and not str(getattr(listing, "category_id", None) or "").strip():
        blockers.append("A validated eBay category ID is required before publishing")
        blockers = list(dict.fromkeys(blockers))
    processing_state = str(getattr(listing, "processing_state", "") or "").lower()
    # A remote publication is a distinct canonical state from a local draft
    # revision.  Older workers may leave local processing/readiness blockers on
    # a listing after it was successfully published; those blockers must not
    # relabel the item itself as a draft/attention row.  Keep the blockers and
    # publishable=false truth intact, but expose the remote state as the queue
    # identity so queue, detail, and publish consumers agree.
    remote_live = bool(str(getattr(listing, "ebay_listing_id", "") or "").strip()) and (
        str(getattr(getattr(listing, "status", None), "value", getattr(listing, "status", "")) or "").lower() in {"published", "posted"}
        or str(getattr(getattr(listing, "ebay_publish_status", None), "value", getattr(listing, "ebay_publish_status", "")) or "").upper() == "POSTED"
    )
    if not remote_live:
        for row in (getattr(listing, "marketplace_listings", None) or []):
            row_status = str(getattr(getattr(row, "status", None), "value", getattr(row, "status", "")) or "").upper()
            if row_status in {"PUBLISHED", "UPDATED"} and str(getattr(row, "marketplace_listing_id", "") or "").strip():
                remote_live = True
                break
    processing_complete = processing_state in {"complete", "completed", "ready"}
    attention = bool(getattr(listing, "processing_blocking_reason", None)) or processing_state in {"needs_attention", "failed", "error"}
    # A listing cannot be publishable while enrichment/processing is still in
    # flight, even when the basic photo/price checks happen to pass.
    publishable = bool(processing_complete and base.get("ready_for_publish")) and not attention and not blockers
    transient_processing = processing_state in {"processing", "pending", "queued", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"}
    queue = "PUBLISHED" if remote_live else "NEEDS_ATTENTION" if (attention or blockers) else "PROCESSING" if (transient_processing or not processing_complete) else "NEEDS_REVIEW" if getattr(listing, "needs_review", False) else "READY"
    result = {
        "processing_complete": processing_complete,
        "enrichment_complete": bool(stored.get("enrichment_complete", processing_complete)),
        "quality_complete": bool(stored.get("quality_complete", bool(description))),
        "review_required": bool(getattr(listing, "needs_review", False)) and not attention,
        "attention_required": attention or bool(blockers),
        "publishable": publishable and (not marketplace or not blockers),
        "warnings": warnings,
        "blocking_reasons": blockers,
        "missing_required_aspects": missing_required_aspects,
        "source_fact_count": source_fact_count,
        "source_facts_covered": source_facts_covered,
        "source_fact_coverage": round(source_facts_covered / source_fact_count, 3) if source_fact_count else None,
        "marketplace_readiness": dict(base.get("marketplace_readiness") or {}),
        "remote_live": remote_live,
        "remote_state": "PUBLISHED" if remote_live else "LOCAL",
        "queue": queue,
    }
    if marketplace:
        result["marketplace"] = str(marketplace).lower()
    return result
