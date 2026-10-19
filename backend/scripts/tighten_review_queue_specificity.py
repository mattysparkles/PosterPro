"""Demote generic or otherwise non-review-ready listings out of Needs Review.

This maintenance pass recomputes the quality summary using the shared
specificity gate and moves listings back to Drafts when they are not actually
ready for the operator review queue.
"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.core.database import SessionLocal
from app.models.enums import ListingStatus
from app.models.models import Listing
from app.services.category_rules import suggest_category_from_text
from app.services.listing_ai import build_listing_description, build_marketplace_title
from app.services.pricing_research_service import compute_listing_quality_summary

TERMINAL_STATUSES = {
    ListingStatus.archived,
    ListingStatus.posted,
    ListingStatus.PUBLISHED,
}


def _current_pricing_snapshot(listing: Listing) -> dict:
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    pricing = marketplace_data.get("pricing_analysis") if isinstance(marketplace_data.get("pricing_analysis"), dict) else {}
    if pricing:
        return pricing
    current_price = listing.listing_price or listing.suggested_price or listing.buy_it_now_price or listing.estimated_value
    return {"current_price": current_price}


def _title_is_generic(title: str | None) -> bool:
    text = " ".join(str(title or "").split()).strip().lower()
    if not text:
        return True
    if text in {"needs review", "unknown", "unknown item", "unknown product", "miscellaneous", "product label", "parts label"}:
        return True
    return len(text.split()) <= 2


def _rewrite_listing_copy(listing: Listing) -> tuple[str, str, str]:
    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    title_hint = str(listing.title or "").strip()
    if not title_hint and isinstance(source_metadata.get("recovery"), dict):
        recovery = source_metadata.get("recovery") or {}
        title_hint = str(recovery.get("product_name") or recovery.get("title") or recovery.get("identifier") or "").strip()
    item_specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
    category_hint = str(listing.category_suggestion or "").strip()
    refined_title = build_marketplace_title(title=title_hint, item_specifics=item_specifics, category_hint=category_hint)
    if _title_is_generic(title_hint) and refined_title:
        listing.title = refined_title

    photo_notes = []
    if listing.image_urls:
        photo_notes.append(f"{len(listing.image_urls)} photo(s) available for review.")
    if listing.cluster_id:
        photo_notes.append(f"Cluster {listing.cluster_id} evidence retained.")
    listing.description = build_listing_description(
        title=listing.title,
        item_specifics=item_specifics,
        included=str((listing.marketplace_data or {}).get("included_components") or item_specifics.get("Included") or "").strip() or None,
        condition_notes=str((listing.condition_data or {}).get("item_condition_notes") or listing.condition or "").strip() or None,
        photo_notes=photo_notes or None,
        source_label=str(listing.source_type or "").replace("_", " ") or None,
    )
    category, _ = suggest_category_from_text(
        listing.title,
        listing.description,
        listing.category_suggestion,
        listing.source_type,
        " ".join(str(v) for v in item_specifics.values()),
    )
    listing.category_suggestion = category
    return listing.title or "", listing.description or "", category


def main() -> None:
    with SessionLocal() as db:
        rows = db.execute(
            select(Listing)
            .where(or_(Listing.needs_review.is_(True), Listing.restricted_review_required.is_(True)))
            .order_by(Listing.updated_at.desc())
        ).scalars().all()
        updated: list[int] = []
        reviewed: list[int] = []
        for listing in rows:
            if listing.status in TERMINAL_STATUSES:
                continue
            _rewrite_listing_copy(listing)
            pricing = _current_pricing_snapshot(listing)
            quality = compute_listing_quality_summary(listing, pricing_analysis=pricing)
            reviewed.append(listing.id)
            marketplace_data = dict(listing.marketplace_data or {})
            marketplace_data["quality_summary"] = quality
            review_repair = dict(marketplace_data.get("review_repair") or {})
            review_repair["tightened_specificity"] = True
            review_repair["specificity_status"] = quality.get("specificity_status")
            review_repair["ready_for_publish_queue"] = bool(quality.get("ready_for_publish_queue"))
            review_repair["blockers"] = quality.get("specificity_blockers") or quality.get("blockers") or []
            marketplace_data["review_repair"] = review_repair
            listing.marketplace_data = marketplace_data

            source_metadata = dict(listing.source_metadata or {})
            source_metadata["review_repair"] = {
                "specificity_status": quality.get("specificity_status"),
                "specificity_blockers": quality.get("specificity_blockers") or [],
                "ready_for_publish_queue": bool(quality.get("ready_for_publish_queue")),
            }
            listing.source_metadata = source_metadata

            if not quality.get("ready_for_publish_queue"):
                listing.needs_review = False
                listing.restricted_review_required = False
                if listing.status != ListingStatus.draft:
                    listing.status = ListingStatus.draft
                updated.append(listing.id)
        db.commit()
        print({"reviewed_count": len(reviewed), "updated_count": len(updated), "updated_ids": updated[:100]})


if __name__ == "__main__":
    main()
