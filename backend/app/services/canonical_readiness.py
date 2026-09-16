"""Single, explainable readiness result for queue and publish consumers."""
from __future__ import annotations

from typing import Any

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
    missing_required_aspects = [str(value).strip() for value in (stored.get("missing_required_aspects") or []) if str(value).strip()]
    if missing_required_aspects:
        blockers.extend(f"Required marketplace detail missing: {value}" for value in missing_required_aspects)
    description = str(getattr(listing, "canonical_description", None) or getattr(listing, "description", None) or "").strip()
    if not description:
        blockers.append("Description is missing")
    elif len(description) < 180:
        # A short description may still be valid for a simple item, but it is
        # not evidence of a hard blocker by itself. Keep it visible as a
        # quality warning while preserving the operator-review flow.
        warnings.append("Description is brief; review product details before publishing")
        if str(getattr(listing, "source_type", "") or "").lower() in {"amazon_vine", "google_photos_album", "photo_intake"}:
            words = [word for word in description.split() if word.strip()]
            unique_words = {word.strip(".,:;!?()[]{}\"'").lower() for word in words}
            if len(words) < 18 or len(unique_words) < 12:
                blockers.append("Listing description needs product-specific enrichment")
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
    processing_state = str(getattr(listing, "processing_state", "") or "").lower()
    processing_complete = processing_state in {"complete", "completed", "ready"}
    attention = bool(getattr(listing, "processing_blocking_reason", None)) or processing_state in {"needs_attention", "failed", "error"}
    publishable = bool(base.get("ready_for_publish")) and not attention and not blockers
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
        "marketplace_readiness": dict(base.get("marketplace_readiness") or {}),
        "queue": "NEEDS_ATTENTION" if (attention or blockers) else "NEEDS_REVIEW" if getattr(listing, "needs_review", False) else "PROCESSING" if not processing_complete else "READY",
    }
    if marketplace:
        result["marketplace"] = str(marketplace).lower()
    return result
