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
    processing_state = str(getattr(listing, "processing_state", "") or "").lower()
    processing_complete = processing_state in {"complete", "completed", "ready"}
    attention = bool(getattr(listing, "processing_blocking_reason", None)) or processing_state in {"needs_attention", "failed", "error"}
    publishable = bool(base.get("ready_for_publish")) and not attention and not blockers
    result = {
        "processing_complete": processing_complete,
        "enrichment_complete": bool(stored.get("enrichment_complete", processing_complete)),
        "quality_complete": bool(stored.get("quality_complete", bool(getattr(listing, "description", None)))),
        "review_required": bool(getattr(listing, "needs_review", False)) and not attention,
        "attention_required": attention or bool(blockers),
        "publishable": publishable,
        "warnings": warnings,
        "blocking_reasons": blockers,
        "missing_required_aspects": list(stored.get("missing_required_aspects") or []),
        "marketplace_readiness": dict(base.get("marketplace_readiness") or {}),
        "queue": "NEEDS_ATTENTION" if (attention or blockers) else "NEEDS_REVIEW" if getattr(listing, "needs_review", False) else "PROCESSING" if not processing_complete else "READY",
    }
    if marketplace:
        result["marketplace"] = str(marketplace).lower()
    return result
