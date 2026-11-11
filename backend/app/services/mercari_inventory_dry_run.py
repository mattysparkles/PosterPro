"""Build an immutable, non-submitting Mercari preview from owned inventory."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.models import Listing, User
from app.services.marketplace_extension_jobs import build_extension_marketplace_payload


OPERATION = "MERCARI_INVENTORY_DRY_RUN"
TITLE_LIMIT = 80
DESCRIPTION_LIMIT = 1000
PHOTO_LIMIT = 12


def convert_packed_weight_to_ounces(value: Any, unit: Any) -> float:
    """Validate and normalize an explicitly supplied packed shipping weight."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Enter an explicit packed shipping weight and unit") from None
    normalized_unit = str(unit or "").strip().lower()
    factors = {"oz": 1.0, "lb": 16.0, "g": 1.0 / 28.349523125, "kg": 1000.0 / 28.349523125}
    if not math.isfinite(number) or number <= 0 or normalized_unit not in factors:
        raise ValueError("Packed shipping weight must be positive and use oz, lb, g, or kg")
    ounces = number * factors[normalized_unit]
    if not math.isfinite(ounces) or ounces <= 0 or ounces > 2400:
        raise ValueError("Packed shipping weight is outside the supported safe range")
    return round(ounces, 3)


def _clean(value: Any) -> str | None:
    text_value = " ".join(str(value or "").split()).strip()
    return text_value or None


def _category_candidate(db: Session, listing: Listing) -> dict[str, Any]:
    """Return a candidate taxonomy path without treating imported taxonomy as verified."""
    hint = _clean(listing.category_suggestion or (listing.item_specifics or {}).get("Type")) or ""
    query = f"{listing.title or ''} {hint}"
    words = {word for word in re.findall(r"[a-z0-9]+", query.casefold()) if len(word) > 2}
    candidates: list[dict[str, Any]] = []
    if words:
        try:
            rows = db.execute(text("""
            SELECT version_id, path_json, full_path, leaf_status, verification_status, source_namespace
              FROM mercari_taxonomy_categories
             WHERE version_id = (SELECT version_id FROM mercari_taxonomy_versions ORDER BY captured_at DESC LIMIT 1)
               AND (lower(full_path) LIKE '%closet%' OR lower(full_path) LIKE '%storage%')
             ORDER BY full_path
             LIMIT 100
            """)).mappings().all()
        except SQLAlchemyError:
            rows = []
        for row in rows:
            try:
                path = json.loads(row["path_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(path, list) and path and all(isinstance(part, str) and part.strip() for part in path):
                candidate_words = {word for word in re.findall(r"[a-z0-9]+", str(row["full_path"]).casefold()) if len(word) > 2}
                overlap = words & candidate_words
                if not overlap:
                    continue
                candidates.append({
                    "version_id": str(row["version_id"] or ""),
                    "path": [part.strip() for part in path],
                    "full_path": str(row["full_path"] or ""),
                    "leaf_status": str(row["leaf_status"] or "unknown"),
                    "verification_status": str(row["verification_status"] or "unverified"),
                    "source_namespace": str(row["source_namespace"] or "unknown"),
                    "match_score": round(len(overlap) / max(1, len(words)), 3),
                    "matched_terms": sorted(overlap),
                })
    candidates.sort(key=lambda row: (-row["match_score"], row["full_path"]))
    candidates = candidates[:10]
    verified = next((row for row in candidates if row["verification_status"] in {"path_verified", "operator_verified"} and row["leaf_status"] == "confirmed_leaf"), None)
    selected = verified or (candidates[0] if candidates else None)
    return {
        "source_hint": hint or None,
        "candidate": selected,
        "status": "verified_live_mapping" if verified else "unverified_candidate" if selected else "requires_live_operator_resolution",
        "alternatives": candidates[:10],
    }


def build_mercari_inventory_dry_run_payload(
    db: Session, *, user: User, listing: Listing, operator_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a destination projection while preserving the source listing untouched."""
    payload = build_extension_marketplace_payload(
        db, user=user, listing=listing, marketplace="mercari", action="DIAGNOSTIC"
    )
    original_title = _clean(payload.get("title") or listing.title) or ""
    original_description = _clean(payload.get("description") or listing.canonical_description or listing.description) or ""
    if not original_title or not original_description:
        raise ValueError("A title and existing product description are required for this dry run")

    title = original_title[:TITLE_LIMIT].rstrip()
    description = original_description[:DESCRIPTION_LIMIT].rstrip()
    # This workflow is a preview of the stored inventory price. Marketplace
    # overrides/research suggestions are deliberately not applied here.
    price = listing.listing_price
    if price is None:
        raise ValueError("An existing listing price is required; automatic repricing is disabled")
    try:
        price = round(float(price), 2)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("The existing listing price is not a valid numeric amount") from None
    if price <= 0:
        raise ValueError("The existing listing price must be positive")

    specifics = payload.get("item_specifics") if isinstance(payload.get("item_specifics"), dict) else {}
    source_meta = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    brand = _clean(specifics.get("Brand") or source_meta.get("brand"))
    if brand and brand.casefold() in {"needs review", "unknown", "n/a", "none"}:
        brand = None
    condition = _clean(payload.get("condition") or listing.condition)
    condition_data = listing.condition_data if isinstance(listing.condition_data, dict) else {}
    condition_needs_review = bool(condition_data.get("operator_review_required"))
    image_urls = payload.get("image_urls") if isinstance(payload.get("image_urls"), list) else []
    # Mapper output is already canonical-order and duplicate-free. Enforce the
    # Mercari maximum again at the operation boundary and never log its URLs.
    cleaned_image_urls = [str(url).strip() for url in image_urls if str(url).strip()]
    deduped_images = list(dict.fromkeys(cleaned_image_urls))
    duplicate_source_count = len(cleaned_image_urls) - len(deduped_images)
    if not deduped_images:
        raise ValueError("No accessible product photographs are attached to this inventory item")
    if len(deduped_images) > PHOTO_LIMIT:
        deduped_images = deduped_images[:PHOTO_LIMIT]

    category_resolution = _category_candidate(db, listing)
    observed_intermediate = ["Home", "Storage & Organization", "Clothing & closet storage"]
    category_resolution = {
        **category_resolution,
        "status": "unverified_observed_intermediate",
        "candidate": {
            "path": observed_intermediate,
            "full_path": " > ".join(observed_intermediate),
            "leaf_status": "intermediate_unverified",
            "verification_status": "unverified",
            "evidence_source": "operator_screenshot_2026-10-22",
        },
    }
    category_candidate = category_resolution["candidate"]
    category_path = category_candidate["path"]
    operator_inputs = operator_inputs or {}
    raw_weight = operator_inputs.get("package_weight")
    weight_unit = str(operator_inputs.get("package_weight_unit") or "").lower() or None
    packed_weight_oz = convert_packed_weight_to_ounces(raw_weight, weight_unit) if raw_weight is not None else None
    shoebox_fit = str(operator_inputs.get("shoebox_fit") or "unknown").lower()
    shipping_payer_input = str(operator_inputs.get("shipping_payer") or "unknown").lower()
    preferred_service = _clean(operator_inputs.get("preferred_shipping_service"))
    condition_choice = _clean(operator_inputs.get("condition_choice"))
    shipping = payload.get("shipping") if isinstance(payload.get("shipping"), dict) else {}
    shipping_profile = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
    estimated_shipping = bool(shipping_profile.get("estimated") or shipping_profile.get("estimated_fields"))
    missing = []
    if category_resolution["status"] != "verified_live_mapping":
        missing.append("category_confirmation")
    if condition_needs_review:
        missing.append("condition_packaging_review")
    if estimated_shipping:
        missing.append("shipping_measurements_review")
    if packed_weight_oz is None:
        missing.append("packed_weight_review")
    if shoebox_fit not in {"yes", "no"}:
        missing.append("shoebox_fit_review")
    if not preferred_service:
        missing.append("shipping_service_review")
    if shipping_payer_input == "unknown" and not shipping.get("shipping_payer"):
        missing.append("shipping_payer_review")
    if not condition_choice:
        missing.append("condition_choice_review")

    # Destination values are a separate projection. Unavailable facts remain
    # null and are reported for review rather than synthesized.
    payload.update({
        "operation": OPERATION,
        "diagnostic": False,
        "title": title,
        "description": description,
        "price": price,
        "condition": condition_choice or condition,
        "condition_choice": condition_choice,
        "condition_source": "explicit_operator_input" if condition_choice else "inventory_requires_destination_choice",
        "brand": brand,
        "brand_selection": "known_brand" if brand else "no_brand",
        # Do not copy apparel sizing into a home-storage listing.
        "size": (_clean(specifics.get("Size") or specifics.get("Apparel Size"))
                 if category_path and category_path[0].casefold() in {"women", "men", "kids", "clothing"}
                 else None),
        "color": _clean(specifics.get("Color") or specifics.get("Colour")),
        "category": " > ".join(category_path) if category_path else None,
        "category_path": category_path or [],
        "category_resolution": category_resolution,
        "department": category_path[0] if category_path else None,
        "subcategory": category_path[-1] if category_path else None,
        "delivery_method": "shipping",
        "shipping": {
            **shipping,
            "mode": "shipping",
            "shipping_payer": shipping.get("shipping_payer"),
            "parcel_size": shipping.get("parcel_size"),
            "parcel_weight": shipping.get("parcel_weight"),
            "package_weight_value": float(raw_weight) if raw_weight is not None else None,
            "package_weight_unit": weight_unit,
            "package_weight_ounces": packed_weight_oz,
            "package_weight_source": "explicit_operator_input" if packed_weight_oz is not None else None,
            "shoebox_fit": shoebox_fit,
            "shoebox_fit_source": "explicit_operator_input" if shoebox_fit in {"yes", "no"} else None,
            "preferred_service": preferred_service,
            "preferred_service_source": "explicit_operator_input" if preferred_service else None,
            "shipping_payer_override": None if shipping_payer_input == "unknown" else shipping_payer_input,
            "shipping_payer_source": "explicit_operator_input" if shipping_payer_input != "unknown" else ("inventory" if shipping.get("shipping_payer") else None),
            "estimated": estimated_shipping,
            "review_required": estimated_shipping,
        },
        "image_urls": deduped_images,
        "photo_source_count": len(cleaned_image_urls),
        "photo_duplicate_source_count": duplicate_source_count,
        "operator_review_fields": missing,
        "start_url": "https://www.mercari.com/sell/",
    })
    provenance = {
        "listing_id": int(listing.id),
        "owner_user_id": int(listing.user_id),
        "source_type": _clean(listing.source_type),
        "source_item_id": _clean((source_meta.get("inventory_id") if isinstance(source_meta, dict) else None)),
        "photo_source": "posterpro_listing_images",
        "photo_count": len(deduped_images),
    }
    return {
        "operation": OPERATION,
        "marketplace_payload": payload,
        "inventory_provenance": provenance,
        "operator_inputs": {
            "package_weight": raw_weight,
            "package_weight_unit": weight_unit,
            "shoebox_fit": shoebox_fit,
            "shipping_payer": shipping_payer_input,
            "preferred_shipping_service": preferred_service,
            "condition_choice": condition_choice,
        },
        "source_snapshot": {
            "title": listing.title,
            "description": listing.canonical_description or listing.description,
            "price": float(listing.listing_price) if listing.listing_price is not None else None,
            "condition": listing.condition,
            "item_specifics": dict(specifics),
            "condition_notes": _clean(condition_data.get("condition_notes") or condition_data.get("item_condition_notes")),
        },
        "review_required": bool(missing),
        "review_fields": missing,
        "submission_performed": False,
    }
