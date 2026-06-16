from __future__ import annotations

from collections.abc import Iterable


def _safe_list(value) -> list:
    return [item for item in (value or []) if item]


def _slug_tokens(*values: str | None) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if not value:
            continue
        cleaned = "".join(char.lower() if char.isalnum() else " " for char in str(value))
        tokens.extend(part for part in cleaned.split() if part)
    return tokens


def normalize_listing_images(
    *,
    listing_images: Iterable[dict] | None = None,
    image_urls: Iterable[str] | None = None,
    source_url: str | None = None,
    source_page_url: str | None = None,
    source_platform: str | None = None,
    default_is_reference: bool = False,
    approved: bool = False,
) -> list[dict]:
    normalized: list[dict] = []
    seen_keys: set[str] = set()

    def push(entry: dict, fallback_order: int) -> None:
        path = str(entry.get("storage_path") or entry.get("url") or entry.get("local_path") or "").strip()
        origin = str(entry.get("source_url") or "").strip()
        key = f"{path}|{origin}"
        if not path or key in seen_keys:
            return
        seen_keys.add(key)
        role = str(entry.get("role") or ("primary" if not normalized else "alternate_angle")).strip() or "alternate_angle"
        confidence = entry.get("confidence")
        try:
            confidence_value = round(float(confidence), 3) if confidence is not None else (1.0 if approved else 0.72)
        except (TypeError, ValueError):
            confidence_value = 1.0 if approved else 0.72
        operator_state = str(entry.get("operator_state") or ("approved" if approved else "suggested")).strip() or "suggested"
        is_reference = bool(entry.get("is_reference"))
        if "is_reference" not in entry:
            is_reference = default_is_reference
        normalized.append(
            {
                "storage_path": path,
                "source_url": origin or source_url,
                "source_page_url": str(entry.get("source_page_url") or source_page_url or "").strip() or None,
                "source_platform": str(entry.get("source_platform") or source_platform or "upload").strip() or "upload",
                "role": role,
                "confidence": confidence_value,
                "operator_state": operator_state,
                "operator_approved": operator_state == "approved",
                "operator_rejected": operator_state == "rejected",
                "display_order": int(entry.get("display_order") or fallback_order),
                "is_reference": is_reference,
                "warning": str(entry.get("warning") or ("Reference image: verify actual item photos before publish." if is_reference else "")).strip() or None,
                "label": str(entry.get("label") or "").strip() or None,
                "metadata": entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {},
            }
        )

    for index, entry in enumerate(_safe_list(listing_images)):
        if isinstance(entry, dict):
            push(entry, index)
    for index, image_url in enumerate(_safe_list(image_urls), start=len(normalized)):
        push({"storage_path": image_url}, index)
    normalized.sort(key=lambda item: (int(item.get("display_order") or 0), item.get("storage_path") or ""))
    if normalized:
        normalized[0]["role"] = "primary"
        normalized[0]["display_order"] = 0
    return normalized


def derive_condition_data(
    *,
    listing: dict | None = None,
    source_type: str | None = None,
    source_metadata: dict | None = None,
    existing: dict | None = None,
) -> dict:
    listing = listing or {}
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    existing = existing if isinstance(existing, dict) else {}
    raw_condition = str(listing.get("condition") or existing.get("condition") or "").strip()
    source_type_value = str(source_type or listing.get("source_type") or "").strip().lower()
    default_state = "needs_review"
    confidence = existing.get("condition_confidence")
    if confidence is None:
        confidence = 0.25 if source_type_value in {"amazon_vine", "google_photos_album"} else 0.45
    if raw_condition:
        lowered = raw_condition.lower()
        if "new" in lowered and "box" in lowered:
            default_state = "new_in_box"
        elif "open" in lowered or "box" in lowered:
            default_state = "open_box"
        elif "parts" in lowered:
            default_state = "parts_only"
        elif "used" in lowered:
            default_state = "used"

    if source_type_value == "amazon_vine" and default_state == "needs_review":
        default_state = "open_box_or_used_unknown"
    if source_type_value.endswith("_import") and default_state == "needs_review":
        default_state = "import_condition_unverified"

    notes = str(existing.get("item_condition_notes") or "").strip()
    if not notes and source_type_value == "amazon_vine":
        notes = "Condition not yet verified. Review actual item photos, completeness, packaging, and accessories before publish."

    return {
        "condition": raw_condition or listing.get("condition") or "Needs review",
        "condition_bucket": str(existing.get("condition_bucket") or default_state),
        "condition_source": str(existing.get("condition_source") or ("import" if raw_condition else "default_unknown")),
        "condition_confidence": float(confidence),
        "open_box": bool(existing.get("open_box", default_state in {"open_box", "open_box_or_used_unknown"})),
        "new_in_box": bool(existing.get("new_in_box", default_state == "new_in_box")),
        "used": bool(existing.get("used", default_state in {"used", "open_box_or_used_unknown"})),
        "parts_only": bool(existing.get("parts_only", default_state == "parts_only")),
        "tested_state": str(existing.get("tested_state") or "untested"),
        "missing_accessories": bool(existing.get("missing_accessories", False)),
        "missing_accessories_notes": str(existing.get("missing_accessories_notes") or "").strip() or None,
        "included_accessories": _safe_list(existing.get("included_accessories")),
        "damage_notes": str(existing.get("damage_notes") or "").strip() or None,
        "packaging_condition": str(existing.get("packaging_condition") or "unknown"),
        "item_condition_notes": notes or None,
        "operator_review_required": bool(existing.get("operator_review_required", True)),
    }


def derive_shipping_profile(
    *,
    listing: dict | None = None,
    item_specifics: dict | None = None,
    existing: dict | None = None,
) -> dict:
    listing = listing or {}
    item_specifics = item_specifics if isinstance(item_specifics, dict) else {}
    existing = existing if isinstance(existing, dict) else {}
    title = str(listing.get("title") or "").strip()
    description = str(listing.get("description") or "").strip()
    tokens = set(_slug_tokens(title, description, item_specifics.get("Type"), item_specifics.get("Model")))
    fragile = any(token in tokens for token in {"glass", "ceramic", "monitor", "screen", "lamp", "mirror"})
    battery = any(token in tokens for token in {"battery", "lithium", "rechargeable", "powerbank"})
    liquid = any(token in tokens for token in {"liquid", "shampoo", "cleaner", "spray"})
    aerosol = "aerosol" in tokens
    oversize = any(token in tokens for token in {"furniture", "chair", "table", "ladder"})
    local_pickup = oversize or fragile
    package_weight = existing.get("package_weight")
    package_dimensions = existing.get("package_dimensions") if isinstance(existing.get("package_dimensions"), dict) else {}
    missing_weight = not package_weight
    missing_dimensions = not any(package_dimensions.get(key) for key in ("length", "width", "height"))
    estimated = bool(existing.get("estimated", missing_weight or missing_dimensions))
    return {
        "item_weight": existing.get("item_weight"),
        "package_weight": package_weight,
        "item_dimensions": existing.get("item_dimensions") if isinstance(existing.get("item_dimensions"), dict) else {},
        "package_dimensions": package_dimensions,
        "shipping_class_suggestion": str(existing.get("shipping_class_suggestion") or ("local_pickup_only" if local_pickup else "standard_ground")),
        "dimensional_weight": existing.get("dimensional_weight"),
        "fragile": bool(existing.get("fragile", fragile)),
        "battery": bool(existing.get("battery", battery)),
        "liquid": bool(existing.get("liquid", liquid)),
        "aerosol": bool(existing.get("aerosol", aerosol)),
        "hazmat": bool(existing.get("hazmat", battery or aerosol)),
        "oversize": bool(existing.get("oversize", oversize)),
        "local_pickup_recommended": bool(existing.get("local_pickup_recommended", local_pickup)),
        "manual_measurement_needed": bool(existing.get("manual_measurement_needed", missing_weight or missing_dimensions)),
        "shipping_notes": str(existing.get("shipping_notes") or "").strip() or None,
        "source_confidence": existing.get("source_confidence") or {},
        "estimated": estimated,
    }


def summarize_listing_readiness(
    *,
    listing_images: Iterable[dict] | None,
    condition_data: dict | None,
    shipping_profile: dict | None,
    listing: dict | None = None,
) -> dict:
    listing = listing or {}
    images = [item for item in _safe_list(listing_images) if isinstance(item, dict)]
    condition_data = condition_data if isinstance(condition_data, dict) else {}
    shipping_profile = shipping_profile if isinstance(shipping_profile, dict) else {}
    approved_images = [item for item in images if item.get("operator_state") != "rejected"]
    actual_item_images = [item for item in approved_images if not item.get("is_reference") and item.get("operator_state") == "approved"]
    primary_image = next((item for item in approved_images if item.get("role") == "primary"), approved_images[0] if approved_images else None)
    blockers: list[str] = []
    warnings: list[str] = []

    if not approved_images:
        blockers.append("No images attached")
    elif not actual_item_images:
        blockers.append("Only source/reference images attached")
    if not primary_image:
        blockers.append("Primary image not set")
    if condition_data.get("operator_review_required", True):
        warnings.append("Condition needs operator review")
    if shipping_profile.get("manual_measurement_needed", True):
        blockers.append("Package weight or dimensions still need review")
    if not listing.get("category_id") and not listing.get("category_suggestion"):
        warnings.append("Category still needs review")
    if not listing.get("listing_price") and not listing.get("suggested_price"):
        blockers.append("Price missing")
    if shipping_profile.get("battery"):
        warnings.append("Battery shipping review required")
    if shipping_profile.get("fragile"):
        warnings.append("Fragile packaging review required")

    image_roles = {str(item.get("role") or "") for item in images}
    if not ({"box", "label", "barcode"} & image_roles):
        warnings.append("No box, label, or barcode image attached")

    return {
        "images_missing": not approved_images,
        "manual_photo_needed": not actual_item_images,
        "primary_image_present": bool(primary_image),
        "approved_image_count": len(approved_images),
        "actual_image_count": len(actual_item_images),
        "reference_image_count": len([item for item in approved_images if item.get("is_reference")]),
        "blocked_for_publish": bool(blockers),
        "blockers": blockers,
        "warnings": warnings,
        "ready_for_publish": not blockers,
        "shipping_checklist": {
            "weight_present": bool(shipping_profile.get("package_weight")),
            "package_dimensions_present": bool(
                isinstance(shipping_profile.get("package_dimensions"), dict)
                and any((shipping_profile.get("package_dimensions") or {}).get(key) for key in ("length", "width", "height"))
            ),
            "shipping_profile_selected": bool(shipping_profile.get("shipping_class_suggestion")),
            "oversized": bool(shipping_profile.get("oversize")),
            "fragile": bool(shipping_profile.get("fragile")),
            "hazmat_or_battery": bool(shipping_profile.get("hazmat") or shipping_profile.get("battery") or shipping_profile.get("liquid") or shipping_profile.get("aerosol")),
            "local_pickup_recommended": bool(shipping_profile.get("local_pickup_recommended")),
            "manual_measurement_needed": bool(shipping_profile.get("manual_measurement_needed")),
            "box_or_label_photo_present": bool({"box", "label", "barcode"} & image_roles),
        },
        "marketplace_readiness": {
            "ebay": {
                "condition_ready": bool(condition_data.get("condition")),
                "category_ready": bool(listing.get("category_id") or listing.get("category_suggestion")),
                "photos_ready": bool(actual_item_images),
                "shipping_ready": not shipping_profile.get("manual_measurement_needed", True),
            },
            "facebook": {
                "condition_ready": bool(condition_data.get("condition")),
                "photos_ready": bool(actual_item_images or approved_images),
                "price_ready": bool(listing.get("listing_price") or listing.get("suggested_price")),
            },
        },
    }


def sync_listing_review_state(*, listing) -> None:
    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    source_type_value = str(listing.source_type or "").strip().lower()
    listing.listing_images = normalize_listing_images(
        listing_images=listing.listing_images,
        image_urls=listing.image_urls,
        source_url=source_metadata.get("source_image_url"),
        source_page_url=source_metadata.get("amazon_source_page_url"),
        source_platform=listing.source_type or "upload",
        default_is_reference=bool(
            source_type_value in {"amazon_vine", "google_photos_album"}
            or str(source_metadata.get("source_marketplace") or "").strip()
        ),
        approved=source_type_value in {"upload", "storage_batch"},
    )
    listing.image_urls = [item["storage_path"] for item in (listing.listing_images or []) if item.get("operator_state") != "rejected"]
    listing.condition_data = derive_condition_data(
        listing={
            "condition": listing.condition,
            "source_type": listing.source_type,
        },
        source_type=listing.source_type,
        source_metadata=listing.source_metadata,
        existing=listing.condition_data,
    )
    listing.shipping_profile = derive_shipping_profile(
        listing={
            "title": listing.title,
            "description": listing.description,
        },
        item_specifics=listing.item_specifics,
        existing=listing.shipping_profile,
    )
