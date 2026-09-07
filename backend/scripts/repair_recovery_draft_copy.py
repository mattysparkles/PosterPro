"""Rewrite recovery draft copy so it reads like a listing, not an image caption."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import Listing
from app.services.category_rules import suggest_category_from_text
from app.services.listing_ai import build_listing_description
from app.services.listing_review import derive_shipping_profile


def _clean_description(*, title: str, description: str, shipping_profile: dict[str, Any] | None) -> str:
    text = str(description or "").strip()
    text = text.split("Item notes:", 1)[0]
    text = text.split("Recovered from preserved inventory photos.", 1)[0]
    text = re.sub(r"^(the\s+)?(image|photo|picture)s?\s+(shows|show)\s+", "", text, flags=re.I)
    text = re.sub(r"^this\s+(image|photo|picture)\s+(shows|show)\s+", "", text, flags=re.I)
    text = text.strip().rstrip(".")
    if text:
        lowered = text.lower()
        if lowered.startswith(("a ", "an ", "the ", "this ", "image ", "photo ", "picture ", "packaging ", "package ", "item ")):
            text = ""
    fact_sentence = ""
    if text:
        fact_sentence = text[0].upper() + text[1:]
        title_norm = re.sub(r"\s+", " ", title).strip().lower()
        fact_norm = re.sub(r"\s+", " ", fact_sentence).strip().lower().rstrip(".")
        if not fact_sentence or fact_norm == title_norm:
            fact_sentence = ""
    body_parts = [
        f"{title}.",
        "Recovered from preserved inventory photos.",
    ]
    if fact_sentence:
        body_parts.append(f"Item notes: {fact_sentence}.")
    body = " ".join(body_parts).strip()
    package_weight = (shipping_profile or {}).get("package_weight")
    package_dimensions = (shipping_profile or {}).get("package_dimensions") if isinstance((shipping_profile or {}).get("package_dimensions"), dict) else {}
    package_bits: list[str] = []
    if package_weight:
        package_bits.append(f"Package weight estimate: {package_weight}.")
    if any(package_dimensions.get(key) for key in ("length", "width", "height")):
        dims = " × ".join(str(package_dimensions.get(key)) for key in ("length", "width", "height") if package_dimensions.get(key))
        if dims:
            package_bits.append(f"Package dimensions estimate: {dims}.")
    if package_bits:
        package_bits.append("Measurements should be confirmed before publish.")
    package_bits.append("Review the attached photos for condition, included parts, measurements, and compatibility before publishing.")
    return " ".join([body, *package_bits]).strip()


def _product_listing_description(*, title: str, listing: Listing, shipping_profile: dict[str, Any] | None) -> str:
    recovery = dict((listing.source_metadata or {}).get("recovery") or {})
    item_specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
    photo_notes = recovery.get("estimated_field_warnings") if isinstance(recovery.get("estimated_field_warnings"), list) else []
    parts = [
        build_listing_description(
            title=title,
            item_specifics=item_specifics,
            included=(listing.condition_data or {}).get("included_accessories") if isinstance(listing.condition_data, dict) else None,
            condition_notes=str((listing.condition_data or {}).get("item_condition_notes") or "").strip() if isinstance(listing.condition_data, dict) else None,
            photo_notes=[str(item) for item in photo_notes if str(item).strip()],
        )
    ]
    package_weight = (shipping_profile or {}).get("package_weight")
    package_dimensions = (shipping_profile or {}).get("package_dimensions") if isinstance((shipping_profile or {}).get("package_dimensions"), dict) else {}
    if package_weight:
        parts.append(f"Package weight estimate: {package_weight}.")
    if any(package_dimensions.get(key) for key in ("length", "width", "height")):
        dims = " × ".join(str(package_dimensions.get(key)) for key in ("length", "width", "height") if package_dimensions.get(key))
        if dims:
            parts.append(f"Package dimensions estimate: {dims}.")
    parts.append("Measurements should be confirmed before publish.")
    parts.append("Review the attached photos for condition, included parts, measurements, and compatibility before publishing.")
    return " ".join(parts).strip()


def _needs_category_refresh(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    return not text or "collectibles > cameras" in text or text.startswith("general resale") or text.startswith("other > needs category review")


def main() -> None:
    with SessionLocal() as db:
        listings = db.execute(
            select(Listing).where(Listing.source_type == "media_inventory_recovery")
        ).scalars().all()
        updated = []
        for listing in listings:
            title = str(listing.title or listing.suggested_title or "Recovered inventory item").strip()
            current_shipping = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
            shipping = derive_shipping_profile(
                listing={
                    "title": title,
                    "description": listing.description or "",
                    "listing_price": listing.listing_price or listing.suggested_price or listing.buy_it_now_price or listing.estimated_value,
                },
                item_specifics=listing.item_specifics or {},
                existing=current_shipping,
            )
            cleaned_description = _product_listing_description(title=title, listing=listing, shipping_profile=shipping)
            suggestion, suggestion_reason = suggest_category_from_text(title, cleaned_description, listing.category_suggestion, " ".join((listing.tags or [])[:8]))

            source = dict(listing.source_metadata or {})
            recovery = dict(source.get("recovery") or {})
            recovery["copy_repair"] = {
                "description_rewritten": cleaned_description != (listing.description or ""),
                "category_refresh_reason": suggestion_reason,
                "shipping_rule": "buyer_pays_under_10" if shipping.get("buyer_pays_shipping") else "seller_pays_10_and_over",
            }
            source["recovery"] = recovery

            changed = False
            if listing.description != cleaned_description:
                listing.description = cleaned_description
                changed = True
            if _needs_category_refresh(listing.category_suggestion) and suggestion and suggestion != "Other > Needs category review":
                listing.category_suggestion = suggestion
                changed = True
            if listing.shipping_profile != shipping:
                listing.shipping_profile = shipping
                changed = True
            if changed:
                listing.source_metadata = source
                updated.append(listing.id)
        db.commit()
        print({"updated_count": len(updated), "updated_ids": updated[:50]})


if __name__ == "__main__":
    main()
