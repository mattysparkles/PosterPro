"""Single, explainable readiness result for queue and publish consumers."""
from __future__ import annotations

from typing import Any
import re

from app.services.listing_review import summarize_listing_readiness
from app.services.listing_specificity import classify_listing_reviewability
from app.services.listing_ai import assess_description_quality
from app.services.category_rules import is_source_noise_category


def canonical_listing_readiness(listing: Any, *, marketplace: str | None = None) -> dict[str, Any]:
    """Combine processing, content, and destination readiness without hiding blockers."""
    stored = listing.readiness_summary if isinstance(getattr(listing, "readiness_summary", None), dict) else {}
    listing_images = getattr(listing, "listing_images", None)
    if not listing_images:
        image_urls = getattr(listing, "image_urls", None) or []
        listing_images = [{"storage_path": str(url), "operator_state": "approved", "role": "primary"} for url in image_urls if str(url).strip()]
    base = summarize_listing_readiness(
        listing_images=listing_images,
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
    source_metadata = getattr(listing, "source_metadata", None) if isinstance(getattr(listing, "source_metadata", None), dict) else {}
    category_id = str(getattr(listing, "category_id", None) or "").strip()
    category_hint = str(getattr(listing, "category_suggestion", None) or "").strip()
    preflight = (getattr(listing, "marketplace_data", None) or {}).get("marketplace_preflight") if isinstance(getattr(listing, "marketplace_data", None), dict) else None
    fresh_preflight_ready = bool(isinstance(preflight, dict) and isinstance(preflight.get("by_marketplace"), dict) and any(
        isinstance(row, dict)
        and not bool(row.get("stale"))
        and str(row.get("status") or "").lower() in {"ready", "ready_with_warnings", "needs_review", "published"}
        and not bool(row.get("blockers"))
        for row in preflight["by_marketplace"].values()
    ))
    # Source breadcrumbs/policy text are never a marketplace taxonomy. A
    # missing ID plus a noisy hint is a real category blocker; a valid ID may
    # retain an old display hint without making the listing unpublishable.
    if not category_id and is_source_noise_category(category_hint) and not fresh_preflight_ready:
        blockers.append("A validated marketplace category is required; the current category hint is source-page or generic text")
    description_quality = assess_description_quality(description, title=title, source_metadata=source_metadata)
    source_type_value = str(getattr(listing, "source_type", None) or "").strip().lower()
    if re.search(r"(?:free\s+shipping|free\s+returns?|\d+[- ]day\s+(?:refund|return|replacement)|add\s+to\s+cart|buy\s+now|return\s+policy)", description, re.IGNORECASE):
        blockers.append("Description contains source-page policy or navigation text")
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
    if not description and not fresh_preflight_ready:
        blockers.append("Description is missing")
    elif len(description) < 180:
        # A short description may still be valid for a simple item, but it is
        # not evidence of a hard blocker by itself. Keep it visible as a
        # quality warning while preserving the operator-review flow.
        warnings.append("Description is brief; review product details before publishing")
        if source_type_value in {"amazon_vine", "google_photos_album", "photo_intake"}:
            words = [word for word in description.split() if word.strip()]
            unique_words = {word.strip(".,:;!?()[]{}\"'").lower() for word in words}
            # Only escalate a thin Vine description when structured source
            # evidence exists to enrich it.  Legacy/test rows without source
            # facts remain ordinary operator-review drafts rather than being
            # misclassified as blocked.
            if (isinstance(source_facts, dict) and source_fact_count or not title) and (len(words) < 18 or len(unique_words) < 12):
                blockers.append("Listing description needs product-specific enrichment")
    if not title and source_type_value in {"amazon_vine", "google_photos_album", "photo_intake"}:
        blockers.append("Product title is missing")
    reviewability = classify_listing_reviewability(
        title=title,
        description=description,
        category=category_hint,
        item_specifics=getattr(listing, "item_specifics", None) if isinstance(getattr(listing, "item_specifics", None), dict) else {},
        source_metadata=source_metadata,
        has_images=bool(listing_images),
    )
    if reviewability.get("caption_like_title") or reviewability.get("bare_identifier_title"):
        blockers.append("Product identity is too generic to safely publish")
    # Keep the most actionable content blocker first for thin legacy drafts.
    if "Listing description needs product-specific enrichment" in blockers and blockers[0] == "Product title is missing":
        blockers.remove("Listing description needs product-specific enrichment")
        blockers.insert(0, "Listing description needs product-specific enrichment")
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    # A fresh destination preflight is the evidence-producing validator for
    # legacy/imported rows. Some older records do not carry denormalized
    # image/shipping fields even though the destination preflight passed them.
    if fresh_preflight_ready:
        base_blockers = set(base.get("blockers") or [])
        derived_only = {
            "No images attached",
            "Only source/reference images attached",
            "Primary image not set",
            "Package weight or dimensions still need review",
            "Price missing",
            "Description is missing",
            "A validated marketplace category is required; the current category hint is source-page or generic text",
        }
        blockers = [reason for reason in blockers if reason not in base_blockers or reason not in derived_only]
    preflight_blockers: list[str] = []
    if isinstance(preflight, dict) and isinstance(preflight.get("by_marketplace"), dict):
        destinations = preflight["by_marketplace"].values()
        if marketplace:
            selected_destination = preflight["by_marketplace"].get(str(marketplace).lower())
            destinations = [selected_destination] if isinstance(selected_destination, dict) else []
        for destination in destinations:
            if not isinstance(destination, dict):
                continue
            for issue in destination.get("blockers") or []:
                if isinstance(issue, dict):
                    message = str(issue.get("message") or issue.get("user_message") or issue.get("raw_error") or issue.get("code") or "").strip()
                else:
                    message = str(issue).strip()
                if message:
                    preflight_blockers.append(message)
        blockers.extend(preflight_blockers)
        blockers = list(dict.fromkeys(blockers))
    if marketplace and isinstance(preflight, dict):
        row = (preflight.get("by_marketplace") or {}).get(str(marketplace).lower()) if isinstance(preflight.get("by_marketplace"), dict) else None
        if isinstance(row, dict):
            blockers.extend(str(item.get("message") or item.get("user_message") or item.get("raw_error") or item.get("code") or item) for item in (row.get("blockers") or []) if item)
            warnings.extend(str(item.get("message") or item.get("user_message") or item.get("raw_error") or item.get("code") or item) for item in (row.get("warnings") or []) if item)
            blockers = list(dict.fromkeys(blockers))
            warnings = list(dict.fromkeys(warnings))
    if str(marketplace or "").lower() == "ebay" and not str(getattr(listing, "category_id", None) or "").strip():
        blockers.append("A validated eBay category ID is required before publishing")
        blockers = list(dict.fromkeys(blockers))
    processing_state = str(getattr(listing, "processing_state", "") or "").lower()
    listing_status = str(getattr(getattr(listing, "status", None), "value", getattr(listing, "status", "")) or "").lower()
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
    # ``processed`` is the legacy terminal state used by older intake workers;
    # it represents completed machine processing even though newer workers use
    # ``complete``. Treating it as in-flight makes the publisher disagree with
    # the queue for otherwise reviewable legacy drafts.
    processing_complete = processing_state in {"complete", "completed", "processed", "ready"} or listing_status in {"ready", "processed", "posted", "published"}
    if (
        not processing_complete
        and getattr(listing, "needs_review", False)
        and processing_state == "queued"
        and fresh_preflight_ready
        and not getattr(listing, "processing_blocking_reason", None)
    ):
        # ``queued`` is the ORM/database default on older completed drafts;
        # a fresh successful preflight plus the durable review marker proves
        # that this row is awaiting the operator, not still in enrichment.
        processing_complete = True
    if (
        not processing_complete
        and getattr(listing, "needs_review", False)
        and not getattr(listing, "processing_blocking_reason", None)
        and processing_state not in {"processing", "pending", "queued", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"}
    ):
        # Older completed drafts did not persist a processing_state.  The
        # explicit review marker is the durable terminal signal for those
        # rows; do not make them appear perpetually in Processing.
        processing_complete = True
    # Older Vine workers persisted ``needs_attention`` as the processing state
    # even after a later pass cleared its blocker.  A review-marked row with no
    # current blocking reason is terminal machine work, not still-processing.
    if processing_state == "needs_attention" and not getattr(listing, "processing_blocking_reason", None) and getattr(listing, "needs_review", False):
        processing_complete = True
    # ``needs_attention`` is a legacy persisted state, not proof of a current
    # blocker.  Reclassification can clear the underlying reasons while an
    # older worker leaves that state behind.  Derive attention from current
    # actionable blockers (or an explicit failed/error state) so the queue and
    # publisher cannot keep surfacing a stale attention label.
    attention = (
        bool(getattr(listing, "processing_blocking_reason", None))
        or processing_state in {"failed", "error"}
        or (processing_state == "needs_attention" and bool(blockers))
    )
    # A listing cannot be publishable while enrichment/processing is still in
    # flight, even when the basic photo/price checks happen to pass.
    base_ready = bool(base.get("ready_for_publish")) or fresh_preflight_ready
    publishable = bool(processing_complete and base_ready) and not attention and not blockers
    destination_publishable = publishable
    if marketplace and isinstance(preflight, dict):
        destination = (preflight.get("by_marketplace") or {}).get(str(marketplace).lower()) if isinstance(preflight.get("by_marketplace"), dict) else None
        if isinstance(destination, dict):
            destination_publishable = destination_publishable and str(destination.get("status") or "").lower() in {"ready", "ready_with_warnings", "published"} and not bool(destination.get("blockers"))
    transient_processing = (
        processing_state in {"processing", "pending", "queued", "enriching", "source_enrichment", "image_enrichment", "category_resolution", "title_generation", "description_generation", "quality_validation"}
        and not processing_complete
    )
    # In-flight enrichment is not an attention state even when an intermediate
    # quality check has warnings/blockers.  It remains Processing until bounded
    # retry/fallback work is exhausted; only then can a genuine blocker enter
    # Needs Attention.
    queue = (
        "PUBLISHED" if remote_live
        else "DRAFTS" if listing_status not in {"ready", "processed", "posted", "published"} and processing_state == "queued" and not getattr(listing, "needs_review", False) and not getattr(listing, "processing_stage", None) and not getattr(listing, "processing_blocking_reason", None)
        # Explicit preflight/processing blockers outrank the transient state.
        # A queued review row with a destination blocker is actionable, not
        # merely in flight; otherwise the UI and publisher disagree about it.
        else "NEEDS_ATTENTION" if ((attention or blockers) and (not transient_processing or bool(processing_blocker) or bool(preflight_blockers)))
        else "PROCESSING" if (transient_processing or (not processing_complete and not attention and not blockers))
        else "NEEDS_REVIEW" if getattr(listing, "needs_review", False)
        else "READY"
    )
    result = {
        "processing_complete": processing_complete,
        "enrichment_complete": bool(stored.get("enrichment_complete", processing_complete)),
        "quality_complete": bool(stored.get("quality_complete", bool(description))),
        "review_required": bool(getattr(listing, "needs_review", False)) and not attention,
        "attention_required": attention or bool(blockers),
        "publishable": publishable and (not marketplace or not blockers),
        "destination_publishable": destination_publishable if marketplace else None,
        "processing_stage": str(getattr(listing, "processing_stage", None) or processing_state or "unknown"),
        "warnings": warnings,
        "blocking_reasons": blockers,
        "missing_required_aspects": missing_required_aspects,
        "source_fact_count": source_fact_count,
        "source_facts_covered": source_facts_covered,
        "source_fact_coverage": round(source_facts_covered / source_fact_count, 3) if source_fact_count else None,
        "description_quality": description_quality,
        "marketplace_readiness": dict(base.get("marketplace_readiness") or {}),
        "remote_live": remote_live,
        "remote_state": "PUBLISHED" if remote_live else "LOCAL",
        "queue": queue,
    }
    if marketplace:
        result["marketplace"] = str(marketplace).lower()
    return result
