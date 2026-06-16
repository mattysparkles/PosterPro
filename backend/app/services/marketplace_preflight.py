from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
import hashlib
import mimetypes
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import ListingStatus, MarketplaceName
from app.models.models import Listing
from app.services.ebay_service import build_ebay_publish_plan
from app.services.listing_review import normalize_listing_images, summarize_listing_readiness
from app.services.marketplace_error_translation import translate_marketplace_error
from app.services.marketplace_field_mapper import build_marketplace_payload
from app.services.pricing_research_service import compute_listing_quality_summary

PRELIGHT_CACHE_TTL = timedelta(hours=24)
PRELIGHT_CACHE_VERSION = "preflight_v2"
LAUNCH_REPAIR_CODES = {
    "ACTUAL_PHOTOS_MISSING",
    "REFERENCE_IMAGES_ONLY",
    "EBAY_IMAGE_URL_INVALID",
    "CATEGORY_MISSING",
    "EBAY_CATEGORY_MISSING",
    "EBAY_REQUIRED_ASPECT_MISSING",
    "EBAY_WEIGHT_MISSING",
    "EBAY_DIMENSIONS_MISSING",
    "CONDITION_MISSING",
}


def _issue(code: str, message: str, *, field: str | None = None, fix_hint: str | None = None, severity: str = "blocker", retryable: bool = False) -> dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "message": message,
        "fix_hint": fix_hint,
        "severity": severity,
        "retryable": retryable,
    }


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _compact_preflight_issue(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": item.get("code"),
        "field": item.get("field"),
        "message": item.get("message"),
        "fix_hint": item.get("fix_hint"),
        "severity": item.get("severity") or "blocker",
        "retryable": bool(item.get("retryable")),
    }


def _listing_pricing_summary(listing: Listing) -> dict[str, Any]:
    pricing = (listing.marketplace_data or {}).get("pricing_analysis") if isinstance(listing.marketplace_data, dict) else {}
    pricing = pricing if isinstance(pricing, dict) else {}
    return {
        "current_price": pricing.get("current_price") or listing.listing_price or listing.suggested_price,
        "recommended_price": pricing.get("recommended_price"),
        "quick_sale_price": pricing.get("quick_sale_price"),
        "floor_price": pricing.get("floor_price"),
        "stretch_price": pricing.get("stretch_price"),
        "price_confidence": pricing.get("price_confidence") or pricing.get("confidence"),
        "stale": bool(pricing.get("stale")),
        "warning": pricing.get("warning"),
        "pricing_explanation": pricing.get("pricing_explanation") or pricing.get("reasoning"),
    }


def _normalized_listing_images(listing: Listing) -> list[dict[str, Any]]:
    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    source_type_value = str(listing.source_type or "").strip().lower()
    return normalize_listing_images(
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


def _image_summary(listing: Listing, readiness: dict[str, Any]) -> dict[str, Any]:
    listing_images = [image for image in _normalized_listing_images(listing) if isinstance(image, dict)]
    actual_images = [image for image in listing_images if not image.get("is_reference") and image.get("operator_state") == "approved"]
    pending_actual_images = [image for image in listing_images if not image.get("is_reference") and image.get("operator_state") not in {"approved", "rejected"}]
    reference_images = [image for image in listing_images if image.get("is_reference") or image.get("warning")]
    storage_root = Path(settings.storage_root).resolve() if settings.storage_root else None
    all_images = [image for image in listing_images if image.get("operator_state") != "rejected"]
    all_file_checks = []
    actual_file_checks = []
    reference_file_checks = []

    def build_check(storage_path: str) -> dict[str, Any]:
        if not storage_path:
            return {"path": storage_path, "exists": False, "size": 0, "mime_type": None, "reason": "missing path"}
        resolved = Path(storage_path)
        if storage_path.startswith(("http://", "https://")):
            return {"path": storage_path, "exists": True, "size": 0, "mime_type": None, "reason": "public url"}
        if storage_root and not resolved.is_absolute():
            resolved = (storage_root / storage_path).resolve()
        elif storage_root and storage_path.startswith("/media/"):
            resolved = (storage_root / storage_path.removeprefix("/media/")).resolve()
        exists = resolved.exists()
        size = resolved.stat().st_size if exists else 0
        mime_type, _ = mimetypes.guess_type(storage_path)
        return {
            "path": storage_path,
            "exists": exists,
            "size": size,
            "mime_type": mime_type,
            "reason": None if exists and size > 0 else "missing file" if not exists else "empty file",
        }

    for image in all_images:
        storage_path = str(image.get("storage_path") or "").strip()
        check = build_check(storage_path)
        all_file_checks.append(check)
        if image.get("is_reference"):
            reference_file_checks.append(check)
        else:
            actual_file_checks.append(check)
    return {
        "attached_count": len(listing_images),
        "actual_count": len(actual_images),
        "actual_pending_count": len(pending_actual_images),
        "reference_count": len(reference_images),
        "actual_image_present": bool(actual_images),
        "actual_pending_present": bool(pending_actual_images),
        "reference_only": bool(listing_images) and not bool(actual_images),
        "manual_photo_needed": bool(readiness.get("manual_photo_needed")),
        "file_checks": all_file_checks,
        "actual_file_checks": actual_file_checks,
        "reference_file_checks": reference_file_checks,
        "public_image_ready": bool(actual_file_checks) and all(check.get("exists") and (check.get("size", 1) > 0 or check.get("reason") == "public url") for check in actual_file_checks),
        "reference_image_ready": bool(reference_file_checks) and all(check.get("exists") and (check.get("size", 1) > 0 or check.get("reason") == "public url") for check in reference_file_checks),
    }


def _category_hint_from_listing(listing: Listing) -> dict[str, Any] | None:
    existing_hint = str(listing.category_suggestion or listing.detected_category_guess or "").strip()
    if existing_hint:
        return {
            "label": existing_hint,
            "confidence": 0.99 if str(listing.category_suggestion or "").strip() else 0.8,
            "source": "existing",
            "reason": "Existing category suggestion already saved on the listing.",
            "needs_review": not str(listing.category_id or "").strip(),
            "can_apply": False,
        }
    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    raw_row = source_metadata.get("raw_row_json") if isinstance(source_metadata.get("raw_row_json"), dict) else {}
    text = " ".join(
        str(value or "").strip()
        for value in (
            listing.title,
            listing.description,
            source_metadata.get("category"),
            raw_row.get("Product Name"),
            (listing.item_specifics or {}).get("Type") if isinstance(listing.item_specifics, dict) else "",
        )
        if str(value or "").strip()
    ).lower()
    keyword_rules: list[tuple[list[str], str, float, str]] = [
        (["charger", "power adapter", "adapter"], "Laptop Power Adapters & Chargers", 0.86, "Title strongly indicates a charger/power adapter product."),
        (["cat house", "feral cats", "outdoor cat"], "Cat Houses & Condos", 0.9, "Title strongly indicates a cat shelter or cat house."),
        (["duffle bag", "duffel bag", "gym bag"], "Duffel Bags", 0.92, "Title strongly indicates a duffel or gym bag."),
        (["tablet for kids", "kids tablet", "tablet"], "Tablets & eBook Readers", 0.8, "Title strongly indicates a tablet device."),
        (["inline skates", "roller skates", "skates"], "Inline & Roller Skating", 0.88, "Title strongly indicates inline skates."),
        (["rv surge protector", "surge protector"], "RV, Trailer & Camper Electrical & Lighting", 0.8, "Title strongly indicates an RV electrical protection accessory."),
        (["usb switch", "usb sharing switch", "kvm switch"], "KVM Switches", 0.79, "Title strongly indicates a USB/KVM switching accessory."),
        (["phone controller", "mobile game controller", "controller"], "Video Game Controllers", 0.81, "Title strongly indicates a gaming controller."),
        (["sandals", "heels", "slingback"], "Heels", 0.78, "Title strongly indicates women's heeled sandals."),
        (["tank heated pad", "heating pad", "rv tank"], "RV Parts & Accessories", 0.7, "Title points to an RV heating/accessory product, but the exact eBay leaf category still needs review."),
    ]
    for keywords, label, confidence, reason in keyword_rules:
        if any(keyword in text for keyword in keywords):
            return {
                "label": label,
                "confidence": confidence,
                "source": "title_heuristic",
                "reason": reason,
                "needs_review": True,
                "can_apply": confidence >= 0.78,
            }
    return None


def _repair_action_for_listing(*, blocker_codes: set[str], image_summary: dict[str, Any], suggested_category: dict[str, Any] | None) -> tuple[str, str]:
    if "ACTUAL_PHOTOS_MISSING" in blocker_codes or "REFERENCE_IMAGES_ONLY" in blocker_codes:
        if image_summary.get("attached_count"):
            return "Open image review and require actual photos", "easy"
        return "Require actual photos", "medium"
    if "EBAY_IMAGE_URL_INVALID" in blocker_codes:
        return "Validate image URLs", "easy"
    if "CATEGORY_MISSING" in blocker_codes or "EBAY_CATEGORY_MISSING" in blocker_codes:
        if suggested_category and suggested_category.get("can_apply"):
            return "Apply suggested category", "easy"
        return "Open category/aspects editor", "medium"
    if "EBAY_REQUIRED_ASPECT_MISSING" in blocker_codes:
        return "Fill required eBay item specifics", "medium"
    if "EBAY_WEIGHT_MISSING" in blocker_codes or "EBAY_DIMENSIONS_MISSING" in blocker_codes:
        return "Measure shipping weight and dimensions", "medium"
    if "CONDITION_MISSING" in blocker_codes:
        return "Finish condition review", "easy"
    return "Run eBay preflight and review blockers", "medium"


def _condition_summary(listing: Listing) -> dict[str, Any]:
    condition_data = listing.condition_data if isinstance(listing.condition_data, dict) else {}
    return {
        "condition": listing.condition,
        "condition_bucket": condition_data.get("condition_bucket"),
        "condition_confidence": condition_data.get("condition_confidence"),
        "operator_review_required": bool(condition_data.get("operator_review_required", True)),
        "open_box": bool(condition_data.get("open_box")),
        "new_in_box": bool(condition_data.get("new_in_box")),
        "used": bool(condition_data.get("used")),
        "parts_only": bool(condition_data.get("parts_only")),
        "tested_state": condition_data.get("tested_state"),
        "missing_accessories": bool(condition_data.get("missing_accessories")),
        "damage_notes": condition_data.get("damage_notes"),
        "included_accessories": condition_data.get("included_accessories"),
        "packaging_condition": condition_data.get("packaging_condition"),
        "item_condition_notes": condition_data.get("item_condition_notes"),
    }


def _shipping_summary(listing: Listing) -> dict[str, Any]:
    shipping = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
    return {
        "package_weight": shipping.get("package_weight"),
        "package_dimensions": shipping.get("package_dimensions") if isinstance(shipping.get("package_dimensions"), dict) else {},
        "shipping_class_suggestion": shipping.get("shipping_class_suggestion"),
        "fragile": bool(shipping.get("fragile")),
        "oversize": bool(shipping.get("oversize")),
        "battery": bool(shipping.get("battery")),
        "liquid": bool(shipping.get("liquid")),
        "hazmat": bool(shipping.get("hazmat")),
        "local_pickup_recommended": bool(shipping.get("local_pickup_recommended")),
        "manual_measurement_needed": bool(shipping.get("manual_measurement_needed", True)),
        "shipping_notes": shipping.get("shipping_notes"),
    }


class MarketplacePreflightService:
    def preflight_listing(self, db: Session, listing: Listing, marketplace: str) -> dict[str, Any]:
        market = str(marketplace or "").lower()
        pricing = (listing.marketplace_data or {}).get("pricing_analysis") if isinstance(listing.marketplace_data, dict) else {}
        pricing = pricing if isinstance(pricing, dict) else {}
        readiness = summarize_listing_readiness(
            listing_images=listing.listing_images,
            condition_data=listing.condition_data,
            shipping_profile=listing.shipping_profile,
            listing={
                "category_id": listing.category_id,
                "category_suggestion": listing.category_suggestion,
                "listing_price": listing.listing_price,
                "suggested_price": listing.suggested_price,
            },
        )
        quality = compute_listing_quality_summary(listing, pricing_analysis=pricing)
        blockers = self._base_blockers(listing, market, pricing, readiness)
        warnings = self._base_warnings(listing, market, pricing, readiness)
        payload_preview: dict[str, Any] = {}
        policy_summary: dict[str, Any] = {}
        category_summary: dict[str, Any] = {}

        if market == MarketplaceName.ebay.value:
            ebay_plan = self._build_ebay_preflight(db, listing, readiness, pricing)
            blockers.extend(ebay_plan["blockers"])
            warnings.extend(ebay_plan["warnings"])
            payload_preview = ebay_plan["payload_preview"]
            policy_summary = ebay_plan["policy_summary"]
            category_summary = ebay_plan["category_summary"]
        elif market == MarketplaceName.facebook.value:
            fb_plan = self._build_facebook_preflight(listing, readiness, pricing)
            blockers.extend(fb_plan["blockers"])
            warnings.extend(fb_plan["warnings"])
            payload_preview = fb_plan["payload_preview"]
            category_summary = fb_plan["category_summary"]
            policy_summary = fb_plan["policy_summary"]
        else:
            payload_preview = build_marketplace_payload(listing, market)

        status = "ready"
        if blockers:
            status = "blocked"
        elif warnings:
            status = "ready_with_warnings"
        elif quality.get("status") in {"blocked", "needs_review", "research_partial"}:
            status = "needs_review"

        if listing.ebay_publish_status and str(listing.ebay_publish_status).lower() == "posted":
            status = "published"
        elif listing.status == "error":
            status = "failed"

        suggested_fixes = [issue["fix_hint"] for issue in blockers if issue.get("fix_hint")]
        required_operator_actions = sorted({issue["fix_hint"] or issue["message"] for issue in blockers})

        return {
            "listing_id": listing.id,
            "marketplace": market,
            "status": status,
            "blockers": blockers,
            "warnings": warnings,
            "missing_fields": [issue["field"] for issue in blockers if issue.get("field")],
            "invalid_fields": [issue["field"] for issue in blockers if issue.get("field") and "INVALID" in issue["code"]],
            "suggested_fixes": suggested_fixes,
            "required_operator_actions": required_operator_actions,
            "payload_preview": {"payload": payload_preview, "sections": list(payload_preview.keys()) if isinstance(payload_preview, dict) else []},
            "policy_summary": policy_summary,
            "category_summary": category_summary,
            "shipping_summary": _shipping_summary(listing),
            "image_summary": _image_summary(listing, readiness),
            "pricing_summary": _listing_pricing_summary(listing),
            "condition_summary": _condition_summary(listing),
            "last_checked_at": datetime.now(UTC),
            "source_version": "preflight_v1",
            "quality_summary": quality,
            "readiness_summary": readiness,
        }

    def payload_preview(self, db: Session, listing: Listing, marketplace: str) -> dict[str, Any]:
        return self.preflight_listing(db, listing, marketplace)["payload_preview"]["payload"]

    def _base_blockers(
        self,
        listing: Listing,
        marketplace: str,
        pricing: dict[str, Any],
        readiness: dict[str, Any],
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        if not (listing.title or "").strip():
            blockers.append(_issue("TITLE_MISSING", "Draft title is missing.", field="title", fix_hint="Enter a concise product title."))
        if not (listing.description or "").strip():
            blockers.append(_issue("DESCRIPTION_MISSING", "Draft description is missing.", field="description", fix_hint="Add the key item details, defects, and included accessories."))
        if not (listing.listing_price or listing.suggested_price or pricing.get("current_price") or pricing.get("recommended_price")):
            blockers.append(_issue("PRICE_MISSING", "Listing price is missing.", field="listing_price", fix_hint="Apply a pricing recommendation or enter a list price."))
        if int(listing.quantity or 0) <= 0:
            blockers.append(_issue("QUANTITY_INVALID", "Quantity must be at least 1.", field="quantity", fix_hint="Set quantity to 1 for a single-item listing."))
        if not (listing.condition or (listing.condition_data or {}).get("condition_bucket")):
            blockers.append(_issue("CONDITION_MISSING", "Condition review is missing.", field="condition", fix_hint="Set the condition bucket and operator notes before publish."))
        if not (listing.category_id or listing.category_suggestion or listing.detected_category_guess):
            blockers.append(_issue("CATEGORY_MISSING", "Category is missing.", field="category_id", fix_hint="Choose a category suggestion or assign a marketplace category."))
        if not readiness.get("actual_image_count"):
            blockers.append(_issue("ACTUAL_PHOTOS_MISSING", "No approved actual item photos are attached.", field="listing_images", fix_hint="Approve or upload real item photos before publishing."))
        if readiness.get("manual_photo_needed"):
            blockers.append(_issue("REFERENCE_IMAGES_ONLY", "Only source/reference images are attached.", field="listing_images", fix_hint="Add or approve actual item photos before publishing.", severity="warning"))
        if marketplace == MarketplaceName.ebay.value and readiness.get("shipping_checklist", {}).get("manual_measurement_needed", True):
            blockers.append(_issue("EBAY_SHIPPING_NEEDS_MEASUREMENT", "Shipping measurements still need review.", field="shipping_profile", fix_hint="Measure the packaged item and save weight and dimensions."))
        return blockers

    def _base_warnings(
        self,
        listing: Listing,
        marketplace: str,
        pricing: dict[str, Any],
        readiness: dict[str, Any],
    ) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        if pricing.get("stale"):
            warnings.append(_issue("PRICING_STALE", "Pricing data is stale.", field="marketplace_data.pricing_analysis", fix_hint="Rerun pricing before queueing this listing.", severity="warning"))
        if pricing.get("price_confidence") is not None and float(pricing.get("price_confidence") or 0) < 0.45:
            warnings.append(_issue("PRICING_WEAK", "Pricing confidence is weak.", field="marketplace_data.pricing_analysis", fix_hint="Review comparable listings or enter a manual comp.", severity="warning"))
        if readiness.get("reference_image_count") and not readiness.get("actual_image_count"):
            warnings.append(_issue("REFERENCE_ONLY_IMAGES", "Only source/reference images are available.", field="listing_images", fix_hint="Approve real item photos if available.", severity="warning"))
        if marketplace == MarketplaceName.facebook.value and (listing.shipping_profile or {}).get("oversize"):
            warnings.append(_issue("FACEBOOK_OVERSIZE", "Item looks oversized for easy local sale.", field="shipping_profile", fix_hint="Mark local pickup if shipping is not practical.", severity="warning"))
        return warnings

    def _build_facebook_preflight(self, listing: Listing, readiness: dict[str, Any], pricing: dict[str, Any]) -> dict[str, Any]:
        payload = build_marketplace_payload(listing, MarketplaceName.facebook.value)
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        if not payload.get("title"):
            blockers.append(_issue("FACEBOOK_TITLE_MISSING", "Facebook title is missing.", field="title", fix_hint="Add a concise title that names the actual item."))
        if not payload.get("price"):
            blockers.append(_issue("FACEBOOK_PRICE_MISSING", "Facebook price is missing.", field="listing_price", fix_hint="Apply pricing before handing this listing off."))
        if not payload.get("category_hint"):
            blockers.append(_issue("FACEBOOK_CATEGORY_MISSING", "Facebook category is missing.", field="category_id", fix_hint="Choose a category suggestion or enter a category hint."))
        if not payload.get("description"):
            blockers.append(_issue("FACEBOOK_DESCRIPTION_MISSING", "Facebook description is missing.", field="description", fix_hint="Write a short operator-friendly description."))
        if not readiness.get("actual_image_count"):
            blockers.append(_issue("FACEBOOK_PHOTOS_MISSING", "Facebook publish needs actual photos.", field="listing_images", fix_hint="Upload or approve actual item photos before handing off."))
        if readiness.get("manual_photo_needed"):
            warnings.append(_issue("FACEBOOK_REFERENCE_ONLY", "Only source/reference images are attached.", field="listing_images", fix_hint="Facebook performs better with actual item photos.", severity="warning"))
        if pricing.get("price_confidence") is not None and float(pricing.get("price_confidence") or 0) < 0.45:
            warnings.append(_issue("FACEBOOK_PRICING_WEAK", "Pricing confidence is weak.", field="marketplace_data.pricing_analysis", fix_hint="Recheck the price before assisted publish.", severity="warning"))
        if (listing.shipping_profile or {}).get("local_pickup_recommended"):
            warnings.append(_issue("FACEBOOK_LOCAL_PICKUP_RECOMMENDED", "Local pickup appears more practical than shipping.", field="shipping_profile", fix_hint="Use local pickup notes in the Facebook handoff.", severity="warning"))
        return {
            "blockers": blockers,
            "warnings": warnings,
            "payload_preview": payload,
            "policy_summary": {
                "assisted_publish": True,
                "browser_bridge_required": True,
                "final_submit_supported": False,
            },
            "category_summary": {
                "category_hint": payload.get("category_hint"),
            },
        }

    def _build_ebay_preflight(
        self,
        db: Session,
        listing: Listing,
        readiness: dict[str, Any],
        pricing: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            plan = asyncio.run(build_ebay_publish_plan(listing, db, allow_create_policies=False))
        except Exception as exc:
            translated = translate_marketplace_error("ebay", exc)
            return {
                "blockers": [translated],
                "warnings": [],
                "payload_preview": {},
                "policy_summary": {
                    "payment_policy_id": "",
                    "fulfillment_policy_id": "",
                    "return_policy_id": "",
                    "merchant_location_key": "",
                    "shipping_service_code": "",
                    "handling_time_days": 1,
                    "local_pickup_allowed": False,
                    "calculated_shipping": False,
                    "package_weight_required": True,
                    "package_dimensions_required": True,
                    "policy_ids_available": False,
                    "missing_fields": ["payment_policy_id", "fulfillment_policy_id", "return_policy_id", "merchant_location_key"],
                },
                "category_summary": {},
            }
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        category = plan.get("category") or {}
        policy_settings = plan.get("policy_settings") or {}
        aspect_summary = plan.get("aspect_summary") or {}

        if not listing.category_id and not listing.category_suggestion and not listing.detected_category_guess:
            blockers.append(_issue("EBAY_CATEGORY_MISSING", "eBay category is missing.", field="category_id", fix_hint="Choose a reliable eBay category before publish."))
        elif not category.get("metadata_available"):
            warnings.append(_issue("EBAY_CATEGORY_METADATA_UNAVAILABLE", "eBay category metadata could not be fetched right now.", field="category_id", fix_hint="Retry preflight when eBay metadata is available.", severity="warning", retryable=True))
        elif not listing.category_id:
            warnings.append(_issue("EBAY_CATEGORY_NEEDS_REVIEW", "eBay category was inferred instead of explicitly chosen.", field="category_id", fix_hint="Review the category suggestion before publishing.", severity="warning"))

        for field_key, code, label in [
            ("payment_policy_id", "EBAY_PAYMENT_POLICY_MISSING", "payment policy"),
            ("fulfillment_policy_id", "EBAY_FULFILLMENT_POLICY_MISSING", "fulfillment policy"),
            ("return_policy_id", "EBAY_RETURN_POLICY_MISSING", "return policy"),
            ("merchant_location_key", "EBAY_MERCHANT_LOCATION_MISSING", "merchant location key"),
        ]:
            if not str(policy_settings.get(field_key) or "").strip():
                blockers.append(_issue(code, f"eBay {label} is missing.", field=f"settings.ebay_marketplace_policy_settings.{field_key}", fix_hint=f"Save a valid eBay {label} in Settings or sync it from eBay."))

        missing_required = aspect_summary.get("missing_required") or []
        for aspect_name in missing_required:
            blockers.append(_issue(
                "EBAY_REQUIRED_ASPECT_MISSING",
                f"{aspect_name} is required for this eBay category.",
                field=f"item_specifics.{aspect_name}",
                fix_hint=f"Enter {aspect_name} in the item specifics section or choose a category that does not require it.",
            ))

        for aspect_name in aspect_summary.get("unsupported") or []:
            warnings.append(_issue(
                "EBAY_UNSUPPORTED_ASPECT",
                f"{aspect_name} is not supported in the selected eBay category.",
                field=f"item_specifics.{aspect_name}",
                fix_hint="Remove or remap this aspect before publishing.",
                severity="warning",
            ))

        shipping = plan.get("shipping_summary") or {}
        shipping_profile = shipping.get("shipping_profile") or {}
        if not shipping_profile.get("package_weight"):
            blockers.append(_issue("EBAY_WEIGHT_MISSING", "Package weight is missing.", field="shipping_profile.package_weight", fix_hint="Enter the measured packaged weight before publishing."))
        if not (shipping_profile.get("package_dimensions") or {}).get("length") or not (shipping_profile.get("package_dimensions") or {}).get("width") or not (shipping_profile.get("package_dimensions") or {}).get("height"):
            blockers.append(_issue("EBAY_DIMENSIONS_MISSING", "Package dimensions are missing.", field="shipping_profile.package_dimensions", fix_hint="Enter length, width, and height for the packed item."))
        image_summary = _image_summary(listing, readiness)
        if readiness.get("manual_photo_needed"):
            warnings.append(_issue("EBAY_REFERENCE_ONLY_IMAGES", "eBay will work better with actual item photos instead of reference images.", field="listing_images", fix_hint="Approve actual item photos before publishing.", severity="warning"))
        if image_summary.get("actual_image_present") and not image_summary.get("public_image_ready"):
            blockers.append(_issue("EBAY_IMAGE_URL_INVALID", "At least one actual item photo is missing, empty, or not publicly reachable.", field="listing_images", fix_hint="Approve actual item photos and verify the image file exists before publishing."))

        if not (
            str(policy_settings.get("payment_policy_id") or "").strip()
            and str(policy_settings.get("fulfillment_policy_id") or "").strip()
            and str(policy_settings.get("return_policy_id") or "").strip()
        ):
            warnings.append(_issue("EBAY_POLICY_IDS_UNVERIFIED", "No eBay policy IDs were saved in Settings.", field="settings.ebay_marketplace_policy_settings", fix_hint="Sync or save eBay policy IDs in Settings before publish.", severity="warning"))

        if pricing.get("price_confidence") is not None and float(pricing.get("price_confidence") or 0) < 0.45:
            warnings.append(_issue("EBAY_PRICING_WEAK", "Pricing confidence is weak for eBay.", field="marketplace_data.pricing_analysis", fix_hint="Review comps or apply a manual price override.", severity="warning"))

        policy_summary = {
            "payment_policy_id": policy_settings.get("payment_policy_id") or "",
            "fulfillment_policy_id": policy_settings.get("fulfillment_policy_id") or "",
            "return_policy_id": policy_settings.get("return_policy_id") or "",
            "merchant_location_key": policy_settings.get("merchant_location_key") or "",
            "shipping_service_code": policy_settings.get("shipping_service_code") or "",
            "handling_time_days": policy_settings.get("handling_time_days") or 1,
            "local_pickup_allowed": bool(policy_settings.get("local_pickup_allowed")),
            "calculated_shipping": bool(policy_settings.get("calculated_shipping")),
            "package_weight_required": bool(policy_settings.get("package_weight_required", True)),
            "package_dimensions_required": bool(policy_settings.get("package_dimensions_required", True)),
            "policy_ids_available": bool(
                str(policy_settings.get("payment_policy_id") or "").strip()
                and str(policy_settings.get("fulfillment_policy_id") or "").strip()
                and str(policy_settings.get("return_policy_id") or "").strip()
            ),
            "missing_fields": [
                key
                for key in ("payment_policy_id", "fulfillment_policy_id", "return_policy_id", "merchant_location_key")
                if not str(policy_settings.get(key) or "").strip()
            ],
        }

        return {
            "blockers": blockers,
            "warnings": warnings,
            "payload_preview": plan.get("payload_preview") or {},
            "policy_summary": policy_summary,
            "category_summary": {
                **category,
                "required_aspects": aspect_summary.get("required") or [],
                "recommended_aspects": aspect_summary.get("recommended") or [],
            },
            "image_summary": image_summary,
        }

    def _cached_marketplace_preflight(self, listing: Listing, marketplace: str) -> dict[str, Any] | None:
        marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        preflight_state = marketplace_data.get("marketplace_preflight") if isinstance(marketplace_data.get("marketplace_preflight"), dict) else {}
        by_marketplace = preflight_state.get("by_marketplace") if isinstance(preflight_state.get("by_marketplace"), dict) else {}
        cached = by_marketplace.get(marketplace)
        if not isinstance(cached, dict):
            return None
        last_checked_at = _parse_datetime(cached.get("last_checked_at"))
        if not last_checked_at:
            return None
        if listing.updated_at and last_checked_at < listing.updated_at:
            return None
        if datetime.now(UTC) - last_checked_at > PRELIGHT_CACHE_TTL:
            return None
        cached_copy = dict(cached)
        cached_copy["cached"] = True
        cached_copy["stale"] = False
        return cached_copy

    def _compact_marketplace_preflight(self, preflight: dict[str, Any], *, cached: bool = False) -> dict[str, Any]:
        blockers = [item for item in (preflight.get("blockers") or []) if isinstance(item, dict)]
        warnings = [item for item in (preflight.get("warnings") or []) if isinstance(item, dict)]
        payload_preview = preflight.get("payload_preview") if isinstance(preflight.get("payload_preview"), dict) else {}
        status = str(preflight.get("status") or "needs_review").lower()
        last_checked_at = preflight.get("last_checked_at")
        if isinstance(last_checked_at, datetime):
            last_checked_at = last_checked_at.isoformat()
        return {
            "marketplace": preflight.get("marketplace"),
            "status": status,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "blocker_codes": [str(item.get("code") or "").strip() for item in blockers if str(item.get("code") or "").strip()],
            "warning_codes": [str(item.get("code") or "").strip() for item in warnings if str(item.get("code") or "").strip()],
            "blocker_messages": [str(item.get("message") or "").strip() for item in blockers if str(item.get("message") or "").strip()],
            "warning_messages": [str(item.get("message") or "").strip() for item in warnings if str(item.get("message") or "").strip()],
            "missing_fields": [str(item or "").strip() for item in (preflight.get("missing_fields") or []) if str(item or "").strip()],
            "invalid_fields": [str(item or "").strip() for item in (preflight.get("invalid_fields") or []) if str(item or "").strip()],
            "ready": status in {"ready", "ready_with_warnings", "published"},
            "payload_preview_available": bool(payload_preview.get("payload")),
            "cached": cached,
            "stale": bool(preflight.get("stale")),
            "last_checked_at": last_checked_at,
            "source_version": str(preflight.get("source_version") or PRELIGHT_CACHE_VERSION),
            "top_blocker_code": blockers[0].get("code") if blockers else None,
            "top_warning_code": warnings[0].get("code") if warnings else None,
            "top_blocker_message": blockers[0].get("message") if blockers else None,
            "top_warning_message": warnings[0].get("message") if warnings else None,
        }

    def cache_preflight_summary(self, db: Session, listing: Listing, preflight: dict[str, Any]) -> dict[str, Any]:
        marketplace = str(preflight.get("marketplace") or "").strip().lower()
        cached = self._compact_marketplace_preflight(preflight, cached=False)
        if not marketplace:
            return cached

        marketplace_data = dict(listing.marketplace_data or {})
        preflight_state = marketplace_data.get("marketplace_preflight")
        if not isinstance(preflight_state, dict):
            preflight_state = {}
        by_marketplace = preflight_state.get("by_marketplace")
        if not isinstance(by_marketplace, dict):
            by_marketplace = {}
        preflight_state.update(
            {
                "version": PRELIGHT_CACHE_VERSION,
                "updated_at": datetime.now(UTC).isoformat(),
                "listing_updated_at": (listing.updated_at or datetime.now(UTC)).isoformat(),
                "by_marketplace": {**by_marketplace, marketplace: cached},
            }
        )
        marketplace_data["marketplace_preflight"] = preflight_state
        listing.marketplace_data = marketplace_data
        db.add(listing)
        return cached

    def _bulk_listing_matches_filters(
        self,
        listing: Listing,
        marketplaces: list[str],
        *,
        only_drafts: bool = False,
        selected_statuses: list[str] | None = None,
        only_missing_preflight: bool = False,
        only_stale_preflight: bool = False,
        only_ready_candidates: bool = False,
        only_blocked_candidates: bool = False,
    ) -> bool:
        if only_drafts and str(listing.status or "").lower() != ListingStatus.draft.value:
            return False

        if selected_statuses:
            allowed = {str(value).strip().lower() for value in selected_statuses if str(value).strip()}
            if allowed and str(listing.status or "").lower() not in allowed:
                return False

        cached_results = [self._cached_marketplace_preflight(listing, market) for market in marketplaces]
        cached_results = [item for item in cached_results if isinstance(item, dict)]

        if only_missing_preflight and cached_results:
            cached_marketplaces = {
                str(item.get("marketplace") or "").strip().lower()
                for item in cached_results
                if str(item.get("marketplace") or "").strip()
            }
            if all(market in cached_marketplaces for market in marketplaces):
                return False

        if only_stale_preflight:
            listing_updated_at = listing.updated_at
            stale_results = [
                item
                for item in cached_results
                if _parse_datetime(item.get("last_checked_at"))
                and listing_updated_at
                and _parse_datetime(item.get("last_checked_at")) < listing_updated_at
            ]
            if not stale_results:
                return False

        if only_ready_candidates:
            if cached_results:
                if not any(item.get("ready") for item in cached_results):
                    return False
            else:
                quality = compute_listing_quality_summary(listing)
                if not any(bool(quality.get(flag)) for flag in ("ready_for_publish_queue", "ready_for_ebay", "ready_for_facebook")):
                    return False

        if only_blocked_candidates:
            if cached_results:
                if not any(item.get("status") == "blocked" for item in cached_results):
                    return False
            else:
                readiness = summarize_listing_readiness(
                    listing_images=listing.listing_images,
                    condition_data=listing.condition_data,
                    shipping_profile=listing.shipping_profile,
                    listing={
                        "category_id": listing.category_id,
                        "category_suggestion": listing.category_suggestion,
                        "listing_price": listing.listing_price,
                        "suggested_price": listing.suggested_price,
                    },
                )
                if not readiness.get("blockers"):
                    return False

        return True

    def bulk_preflight_listing_report(
        self,
        db: Session,
        listings: list[Listing],
        marketplaces: list[str],
        *,
        force_refresh: bool = False,
        only_drafts: bool = False,
        selected_statuses: list[str] | None = None,
        only_missing_preflight: bool = False,
        only_stale_preflight: bool = False,
        only_ready_candidates: bool = False,
        only_blocked_candidates: bool = False,
    ) -> dict[str, Any]:
        market_list: list[str] = []
        seen_markets: set[str] = set()
        for market in marketplaces or [MarketplaceName.ebay.value, MarketplaceName.facebook.value]:
            market_key = str(market or "").strip().lower()
            if market_key and market_key not in seen_markets:
                seen_markets.add(market_key)
                market_list.append(market_key)

        selected_listings = [
            listing
            for listing in listings
            if self._bulk_listing_matches_filters(
                listing,
                market_list,
                only_drafts=only_drafts,
                selected_statuses=selected_statuses,
                only_missing_preflight=only_missing_preflight,
                only_stale_preflight=only_stale_preflight,
                only_ready_candidates=only_ready_candidates,
                only_blocked_candidates=only_blocked_candidates,
            )
        ]

        summary = Counter()
        blocker_counter = Counter()
        warning_counter = Counter()
        items: list[dict[str, Any]] = []

        for listing in selected_listings:
            pricing_summary = _listing_pricing_summary(listing)
            shipping_summary = _shipping_summary(listing)
            image_summary = _image_summary(
                listing,
                summarize_listing_readiness(
                    listing_images=listing.listing_images,
                    condition_data=listing.condition_data,
                    shipping_profile=listing.shipping_profile,
                    listing={
                        "category_id": listing.category_id,
                        "category_suggestion": listing.category_suggestion,
                        "listing_price": listing.listing_price,
                        "suggested_price": listing.suggested_price,
                    },
                ),
            )
            listing_item = {
                "listing_id": listing.id,
                "title": listing.title,
                "marketplaces": {},
                "ready_marketplaces": [],
                "blocked_marketplaces": [],
                "warning_marketplaces": [],
                "top_blocker_code": None,
                "top_blocker_message": None,
                "top_warning_code": None,
                "top_warning_message": None,
                "price": pricing_summary.get("current_price"),
                "category": listing.category_id or listing.category_suggestion or listing.detected_category_guess,
                "condition": listing.condition,
                "image_count": image_summary.get("attached_count", 0),
                "actual_image_count": image_summary.get("actual_count", 0),
                "package_weight": shipping_summary.get("package_weight"),
                "package_dimensions": shipping_summary.get("package_dimensions"),
                "last_preflight_at": None,
            }

            for market in market_list:
                cached = None if force_refresh else self._cached_marketplace_preflight(listing, market)
                if cached is None:
                    try:
                        preflight = self.preflight_listing(db, listing, market)
                    except Exception as exc:
                        translated = translate_marketplace_error(market, exc)
                        blocker_issue = _compact_preflight_issue(
                            {
                                "code": translated["code"],
                                "field": translated.get("field"),
                                "message": translated["user_message"],
                                "fix_hint": translated.get("fix_hint"),
                                "severity": translated.get("severity") or "blocker",
                                "retryable": translated.get("retryable", False),
                            }
                        )
                        market_result = {
                            "marketplace": market,
                            "status": "failed",
                            "blocker_count": 1,
                            "warning_count": 0,
                            "blocker_codes": [translated["code"]],
                            "warning_codes": [],
                            "blocker_messages": [translated["user_message"]],
                            "warning_messages": [],
                            "missing_fields": [translated["field"]] if translated.get("field") else [],
                            "invalid_fields": [translated["field"]] if translated.get("field") else [],
                            "ready": False,
                            "payload_preview_available": False,
                            "cached": False,
                            "stale": False,
                            "last_checked_at": datetime.now(UTC),
                            "source_version": translated["code"],
                            "top_blocker_code": translated["code"],
                            "top_warning_code": None,
                            "top_blocker_message": translated["user_message"],
                            "top_warning_message": None,
                            "blockers": [blocker_issue],
                            "warnings": [],
                        }
                        blocker_counter[translated["code"]] += 1
                    else:
                        self.cache_preflight_summary(db, listing, preflight)
                        compact = self._compact_marketplace_preflight(preflight, cached=False)
                        market_result = {
                            **compact,
                            "blockers": preflight.get("blockers") or [],
                            "warnings": preflight.get("warnings") or [],
                        }
                else:
                    cached_blockers = [
                        _compact_preflight_issue(
                            {
                                "code": code,
                                "message": message,
                                "severity": "blocker",
                            }
                        )
                        for code, message in zip(cached.get("blocker_codes") or [], cached.get("blocker_messages") or [], strict=False)
                    ]
                    cached_warnings = [
                        _compact_preflight_issue(
                            {
                                "code": code,
                                "message": message,
                                "severity": "warning",
                            }
                        )
                        for code, message in zip(cached.get("warning_codes") or [], cached.get("warning_messages") or [], strict=False)
                    ]
                    market_result = {
                        **cached,
                        "blockers": cached_blockers,
                        "warnings": cached_warnings,
                    }

                listing_item["marketplaces"][market] = market_result

                if market_result.get("ready"):
                    listing_item["ready_marketplaces"].append(market)
                    summary[f"{market}_ready"] += 1
                if market_result.get("status") == "blocked":
                    listing_item["blocked_marketplaces"].append(market)
                    summary["blocked"] += 1
                if market_result.get("status") == "ready_with_warnings":
                    listing_item["warning_marketplaces"].append(market)
                    summary["ready_with_warnings"] += 1
                if market_result.get("status") == "failed":
                    summary["preflight_failed"] += 1

                for code in market_result.get("blocker_codes") or []:
                    if code:
                        blocker_counter[code] += 1
                        if code in {"ACTUAL_PHOTOS_MISSING", "FACEBOOK_PHOTOS_MISSING", "EBAY_REFERENCE_ONLY_IMAGES", "REFERENCE_IMAGES_ONLY"}:
                            summary["missing_photos"] += 1
                        if code in {"PRICING_WEAK", "EBAY_PRICING_WEAK", "FACEBOOK_PRICING_WEAK"}:
                            summary["weak_pricing"] += 1
                        if "PRICE" in code:
                            summary["missing_price"] += 1
                        if "SHIPPING" in code or "WEIGHT" in code or "DIMENSIONS" in code:
                            summary["missing_shipping"] += 1
                        if "POLICY" in code:
                            summary["missing_policies"] += 1
                        if code == "EBAY_REQUIRED_ASPECT_MISSING":
                            summary["missing_ebay_aspects"] += 1
                for code in market_result.get("warning_codes") or []:
                    if code:
                        warning_counter[code] += 1
                        if code in {"PRICING_WEAK", "EBAY_PRICING_WEAK", "FACEBOOK_PRICING_WEAK"}:
                            summary["weak_pricing"] += 1
                if any(code in {"FACEBOOK_REFERENCE_ONLY", "REFERENCE_ONLY_IMAGES", "EBAY_REFERENCE_ONLY_IMAGES"} for code in market_result.get("warning_codes") or []):
                    summary["reference_images_only"] += 1

            if listing_item["blocked_marketplaces"]:
                summary["blocked_listings"] += 1
            if listing_item["warning_marketplaces"] and not listing_item["blocked_marketplaces"]:
                summary["warning_only_listings"] += 1
            if listing_item["ready_marketplaces"]:
                summary["ready_listings"] += 1

            if listing_item["marketplaces"]:
                first_market = next(iter(listing_item["marketplaces"].values()))
                listing_item["top_blocker_code"] = first_market.get("top_blocker_code")
                listing_item["top_blocker_message"] = first_market.get("top_blocker_message")
                listing_item["top_warning_code"] = first_market.get("top_warning_code")
                listing_item["top_warning_message"] = first_market.get("top_warning_message")
                last_checked_values = [
                    checked_at
                    for checked_at in (
                        _parse_datetime(item.get("last_checked_at"))
                        for item in listing_item["marketplaces"].values()
                        if isinstance(item, dict)
                    )
                    if checked_at
                ]
                if last_checked_values:
                    listing_item["last_preflight_at"] = max(last_checked_values)

            items.append(listing_item)

        return {
            "items": items,
            "marketplaces": market_list,
            "generated_at": datetime.now(UTC),
            "summary": {
                "total_listings_checked": len(items),
                "total_marketplaces_checked": len(items) * len(market_list),
                "ready_for_ebay": summary.get("ebay_ready", 0),
                "ready_for_facebook": summary.get("facebook_ready", 0),
                "ready_with_warnings": summary.get("ready_with_warnings", 0),
                "blocked": summary.get("blocked", 0),
                "blocked_listings": summary.get("blocked_listings", 0),
                "warning_only_listings": summary.get("warning_only_listings", 0),
                "ready_listings": summary.get("ready_listings", 0),
                "missing_photos": summary.get("missing_photos", 0),
                "missing_price": summary.get("missing_price", 0),
                "missing_shipping": summary.get("missing_shipping", 0),
                "missing_policies": summary.get("missing_policies", 0),
                "missing_ebay_aspects": summary.get("missing_ebay_aspects", 0),
                "weak_pricing": summary.get("weak_pricing", 0),
                "reference_images_only": summary.get("reference_images_only", 0),
                "preflight_failed": summary.get("preflight_failed", 0),
                "blocker_codes": dict(blocker_counter),
                "warning_codes": dict(warning_counter),
                "most_common_blocker": blocker_counter.most_common(1)[0][0] if blocker_counter else None,
                "most_common_warning": warning_counter.most_common(1)[0][0] if warning_counter else None,
                "timestamp": datetime.now(UTC),
            },
        }

    def launch_candidates(
        self,
        db: Any,
        listings: list[Listing],
        *,
        marketplace: str = "ebay",
        max_items: int = 10,
        max_price: float = 50,
        include_warning_only: bool = False,
        include_local_pickup: bool = False,
        include_risky_shipping: bool = False,
    ) -> dict[str, Any]:
        market = str(marketplace or "ebay").strip().lower()
        candidates: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        summary = {
            "checked": 0,
            "selected": 0,
            "excluded": 0,
            "ready": 0,
            "warning_only": 0,
        }

        def sort_key(item: Listing) -> tuple[float, float, datetime]:
            quality_summary = item.marketplace_data.get("quality_summary") if isinstance(item.marketplace_data, dict) else None
            quality_score = quality_summary.get("score") if isinstance(quality_summary, dict) else None
            return (
                float(item.listing_price or item.suggested_price or item.buy_it_now_price or item.estimated_value or 0) or 0,
                -(float(quality_score) if quality_score is not None else 0),
                item.updated_at or datetime.min,
            )

        sorted_listings = sorted(listings, key=sort_key)
        for listing in sorted_listings:
            if summary["selected"] >= max(1, min(int(max_items or 10), 50)):
                break
            summary["checked"] += 1
            listing_status = str(getattr(listing.status, "value", listing.status) or "").strip().lower()
            ebay_status = str(getattr(listing.ebay_publish_status, "value", listing.ebay_publish_status) or "").strip().lower()
            if listing_status in {"posted", "published"} or ebay_status == "posted":
                excluded.append({
                    "listing_id": listing.id,
                    "title": listing.title,
                    "reason": "Already published",
                    "reason_excluded": "Listing is already live or marked posted.",
                    "preflight_status": "published",
                })
                continue
            preflight = self.preflight_listing(db, listing, market)
            cache_summary = self.cache_preflight_summary(db, listing, preflight)
            price = float(listing.listing_price or listing.suggested_price or listing.buy_it_now_price or listing.estimated_value or 0)
            shipping = preflight.get("shipping_summary") or {}
            image_summary = preflight.get("image_summary") or {}
            quality = preflight.get("quality_summary") or {}
            blockers = preflight.get("blockers") or []
            warnings = preflight.get("warnings") or []
            status = str(preflight.get("status") or "").lower()
            ready_like = status in {"ready", "ready_with_warnings", "published"}
            blocked_like = bool(blockers)
            warning_only = bool(warnings) and not blocked_like and status == "ready_with_warnings"
            local_pickup = bool(shipping.get("local_pickup_recommended") or shipping.get("local_pickup_only"))
            risky_shipping = bool(shipping.get("oversize") or shipping.get("hazmat") or shipping.get("battery") or shipping.get("liquid"))
            if price > max_price:
                excluded.append({
                    "listing_id": listing.id,
                    "title": listing.title,
                    "reason": f"Price {price:.2f} exceeds threshold {max_price:.2f}",
                    "reason_excluded": f"Price {price:.2f} exceeds threshold {max_price:.2f}",
                    "preflight_status": status,
                })
                continue
            if blocked_like or not ready_like:
                excluded.append({
                    "listing_id": listing.id,
                    "title": listing.title,
                    "reason": preflight.get("status") or "Not ready",
                    "reason_excluded": preflight.get("status") or "Not ready",
                    "preflight_status": status,
                })
                continue
            if warning_only and not include_warning_only:
                excluded.append({
                    "listing_id": listing.id,
                    "title": listing.title,
                    "reason": "Warning-only preflight",
                    "reason_excluded": "Warning-only preflight",
                    "preflight_status": status,
                })
                continue
            if local_pickup and not include_local_pickup:
                excluded.append({
                    "listing_id": listing.id,
                    "title": listing.title,
                    "reason": "Local pickup recommended",
                    "reason_excluded": "Local pickup recommended",
                    "preflight_status": status,
                })
                continue
            if risky_shipping and not include_risky_shipping:
                excluded.append({
                    "listing_id": listing.id,
                    "title": listing.title,
                    "reason": "Risky shipping profile",
                    "reason_excluded": "Risky shipping profile",
                    "preflight_status": status,
                })
                continue
            if not image_summary.get("actual_image_present"):
                excluded.append({
                    "listing_id": listing.id,
                    "title": listing.title,
                    "reason": "No approved actual photos",
                    "reason_excluded": "No approved actual photos",
                    "preflight_status": status,
                })
                continue

            reason_selected = "eBay-ready under launch threshold"
            if warning_only:
                reason_selected = "Ready with warnings and under launch threshold"
            candidates.append(
                {
                    "listing_id": listing.id,
                    "title": listing.title,
                    "price": price,
                    "quality_score": quality.get("score"),
                    "preflight_status": preflight.get("status"),
                    "top_warnings": [issue.get("code") for issue in warnings if isinstance(issue, dict) and issue.get("code")],
                    "payload_preview_available": bool(preflight.get("payload_preview")),
                    "reason_selected": reason_selected,
                    "reason_excluded": None,
                    "marketplace_summary": cache_summary,
                }
            )
            summary["selected"] += 1
            summary["ready"] += int(status == "ready")
            summary["warning_only"] += int(warning_only)

        return {
            "marketplace": market,
            "max_items": max_items,
            "max_price": max_price,
            "include_warning_only": include_warning_only,
            "include_local_pickup": include_local_pickup,
            "include_risky_shipping": include_risky_shipping,
            "generated_at": datetime.now(UTC),
            "candidates": candidates,
            "excluded": excluded,
            "summary": summary,
        }

    def launch_drill_dry_run(
        self,
        db: Any,
        listings: list[Listing],
        *,
        marketplace: str = "ebay",
        max_items: int = 10,
        require_ready: bool = True,
        include_payload_preview: bool = True,
    ) -> dict[str, Any]:
        market = str(marketplace or "ebay").strip().lower()
        items: list[dict[str, Any]] = []
        summary = {"checked": 0, "ready": 0, "blocked": 0, "warnings": 0}

        for listing in listings[: max(1, min(int(max_items or 10), 50))]:
            summary["checked"] += 1
            preflight = self.preflight_listing(db, listing, market)
            payload = preflight.get("payload_preview", {}).get("payload") if include_payload_preview else {}
            blockers = preflight.get("blockers") or []
            warnings = preflight.get("warnings") or []
            status = str(preflight.get("status") or "").lower()
            ready_like = status in {"ready", "ready_with_warnings", "published"}
            if require_ready and not ready_like:
                summary["blocked"] += 1
                items.append(
                    {
                        "listing_id": listing.id,
                        "title": listing.title,
                        "status": "blocked",
                        "preflight": preflight,
                        "payload_preview": payload,
                        "blockers": blockers,
                        "warnings": warnings,
                        "launch_checklist": self._launch_checklist(market, preflight),
                        "reason": "Listing is not ready for launch drill.",
                    }
                )
                continue
            if blockers:
                summary["blocked"] += 1
            if warnings:
                summary["warnings"] += 1
            if ready_like:
                summary["ready"] += 1
            items.append(
                {
                    "listing_id": listing.id,
                    "title": listing.title,
                    "status": "ready" if ready_like and not blockers else "ready_with_warnings" if ready_like else "blocked",
                    "preflight": preflight,
                    "payload_preview": payload,
                    "blockers": blockers,
                    "warnings": warnings,
                    "launch_checklist": self._launch_checklist(market, preflight),
                    "reason": None if ready_like else "Listing is not ready for launch drill.",
                }
            )
        return {
            "marketplace": market,
            "generated_at": datetime.now(UTC),
            "summary": summary,
            "items": items,
        }

    def launch_repair_queue(
        self,
        db: Session,
        listings: list[Listing],
        *,
        marketplace: str = "ebay",
        max_items: int = 50,
        max_price: float = 50,
        image_status: str | None = None,
        has_category_suggestion: bool | None = None,
        repair_difficulty: str | None = None,
    ) -> dict[str, Any]:
        market = str(marketplace or "ebay").strip().lower()
        items: list[dict[str, Any]] = []
        blocker_counter = Counter()
        summary = {
            "checked": 0,
            "included": 0,
            "missing_actual_photos": 0,
            "reference_only_images": 0,
            "invalid_image_url": 0,
            "actual_pending_review": 0,
            "actual_approved": 0,
            "no_images": 0,
            "ready_for_image_preflight": 0,
            "missing_category": 0,
            "missing_required_aspects": 0,
        }

        def sort_key(item: dict[str, Any]) -> tuple[int, int, int, float, int]:
            difficulty_rank = {"easy": 0, "medium": 1, "hard": 2}
            suggested_category = item.get("suggested_category") if isinstance(item.get("suggested_category"), dict) else {}
            return (
                difficulty_rank.get(item.get("estimated_repair_difficulty") or "medium", 1),
                0 if suggested_category.get("can_apply") else 1,
                0 if item.get("image_status") == "reference_only" else 1,
                float(item.get("quality_score") or 0) * -1,
                int(item.get("listing_id") or 0),
            )

        for listing in listings:
            summary["checked"] += 1
            listing_status = str(getattr(listing.status, "value", listing.status) or "").strip().lower()
            ebay_status = str(getattr(listing.ebay_publish_status, "value", listing.ebay_publish_status) or "").strip().lower()
            if listing_status in {"posted", "published"} or ebay_status == "posted" or listing.ebay_listing_id:
                continue
            price = float(listing.listing_price or listing.suggested_price or listing.buy_it_now_price or listing.estimated_value or 0)
            if price <= 0 or price > max_price:
                continue
            preflight = self.preflight_listing(db, listing, market)
            blocker_codes = {str(issue.get("code") or "").strip() for issue in (preflight.get("blockers") or []) if str(issue.get("code") or "").strip()}
            if not blocker_codes or not (blocker_codes & LAUNCH_REPAIR_CODES):
                continue
            image_summary = preflight.get("image_summary") or {}
            category_summary = preflight.get("category_summary") or {}
            suggested_category = _category_hint_from_listing(listing)
            recommended_action, difficulty = _repair_action_for_listing(
                blocker_codes=blocker_codes,
                image_summary=image_summary,
                suggested_category=suggested_category,
            )
            listing_images = [image for image in _normalized_listing_images(listing) if isinstance(image, dict)]
            approved_actual = [
                image for image in listing_images
                if not image.get("is_reference") and image.get("operator_state") == "approved"
            ]
            pending_actual = [
                image for image in listing_images
                if not image.get("is_reference") and image.get("operator_state") not in {"approved", "rejected"}
            ]
            actual_file_invalid = bool(image_summary.get("actual_count")) and not bool(image_summary.get("public_image_ready"))
            image_status = "no_images"
            if actual_file_invalid:
                image_status = "actual_file_invalid"
            elif approved_actual:
                image_status = "actual_approved"
            elif pending_actual:
                image_status = "actual_pending_review"
            elif image_summary.get("reference_count"):
                image_status = "reference_only"
            elif image_summary.get("attached_count"):
                image_status = "actual_pending_review"
            ready_for_image_preflight = bool(approved_actual) and not actual_file_invalid

            item = {
                "listing_id": listing.id,
                "title": listing.title,
                "price": price,
                "status": listing_status,
                "current_preflight_status": preflight.get("status"),
                "blocker_codes": sorted(blocker_codes),
                "warning_codes": [str(issue.get("code") or "").strip() for issue in (preflight.get("warnings") or []) if str(issue.get("code") or "").strip()],
                "photo_counts": {
                    "total_images": image_summary.get("attached_count", 0),
                    "actual_approved_images": image_summary.get("actual_count", 0),
                    "actual_pending_images": len(pending_actual),
                    "reference_source_images": image_summary.get("reference_count", 0),
                },
                "image_status": image_status,
                "ready_for_image_preflight": ready_for_image_preflight,
                "category_status": {
                    "category_id": listing.category_id,
                    "category_suggestion": listing.category_suggestion,
                    "detected_category_guess": listing.detected_category_guess,
                    "category_name": category_summary.get("category_name"),
                },
                "suggested_category": suggested_category,
                "required_aspects_status": {
                    "required": category_summary.get("required_aspects") or [],
                    "missing_required": [
                        issue.get("field")
                        for issue in (preflight.get("blockers") or [])
                        if issue.get("code") == "EBAY_REQUIRED_ASPECT_MISSING"
                    ],
                },
                "shipping_status": preflight.get("shipping_summary") or {},
                "condition_status": preflight.get("condition_summary") or {},
                "recommended_next_repair_action": recommended_action,
                "estimated_repair_difficulty": difficulty,
                "quality_score": (preflight.get("quality_summary") or {}).get("score"),
            }
            if image_status and str(image_status).strip().lower() != str(item.get("image_status") or "").strip().lower():
                continue
            if has_category_suggestion is not None:
                has_suggestion = bool((suggested_category or {}).get("label") or listing.category_suggestion or listing.category_id)
                if bool(has_category_suggestion) != has_suggestion:
                    continue
            if repair_difficulty and str(repair_difficulty).strip().lower() != str(item.get("estimated_repair_difficulty") or "").strip().lower():
                continue
            items.append(item)
            summary["included"] += 1
            for code in blocker_codes:
                blocker_counter[code] += 1
            if "ACTUAL_PHOTOS_MISSING" in blocker_codes:
                summary["missing_actual_photos"] += 1
            if "REFERENCE_IMAGES_ONLY" in blocker_codes:
                summary["reference_only_images"] += 1
            if "EBAY_IMAGE_URL_INVALID" in blocker_codes:
                summary["invalid_image_url"] += 1
            if image_status == "actual_pending_review":
                summary["actual_pending_review"] += 1
            if image_status == "actual_approved":
                summary["actual_approved"] += 1
            if image_status == "no_images":
                summary["no_images"] += 1
            if ready_for_image_preflight:
                summary["ready_for_image_preflight"] += 1
            if "CATEGORY_MISSING" in blocker_codes or "EBAY_CATEGORY_MISSING" in blocker_codes:
                summary["missing_category"] += 1
            if "EBAY_REQUIRED_ASPECT_MISSING" in blocker_codes:
                summary["missing_required_aspects"] += 1

        items = sorted(items, key=sort_key)[: max(1, min(int(max_items or 50), 250))]
        return {
            "marketplace": market,
            "generated_at": datetime.now(UTC),
            "summary": {
                **summary,
                "most_common_blocker": blocker_counter.most_common(1)[0][0] if blocker_counter else None,
                "blocker_codes": dict(blocker_counter),
            },
            "items": items,
        }

    def apply_repair_actions(
        self,
        db: Session,
        listing: Listing,
        *,
        apply_category_suggestion: bool = False,
        validate_images: bool = False,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {"listing_id": listing.id, "applied": [], "skipped": []}
        if apply_category_suggestion:
            suggestion = _category_hint_from_listing(listing)
            if suggestion and suggestion.get("can_apply") and not str(listing.category_id or "").strip():
                if not str(listing.category_suggestion or "").strip():
                    listing.category_suggestion = str(suggestion.get("label") or "").strip() or listing.category_suggestion
                if not str(listing.detected_category_guess or "").strip():
                    listing.detected_category_guess = str(suggestion.get("label") or "").strip() or listing.detected_category_guess
                changes["applied"].append("category_suggestion")
            else:
                changes["skipped"].append("category_suggestion")
        if validate_images:
            normalized_images = _normalized_listing_images(listing)
            if normalized_images != (listing.listing_images or []):
                listing.listing_images = normalized_images
                listing.image_urls = [item.get("storage_path") for item in normalized_images if item.get("storage_path") and item.get("operator_state") != "rejected"]
                changes["applied"].append("listing_images")
            else:
                changes["skipped"].append("listing_images")
        if changes["applied"]:
            db.add(listing)
            db.commit()
            db.refresh(listing)
        preflight = self.preflight_listing(db, listing, MarketplaceName.ebay.value)
        return {
            **changes,
            "status_after": preflight.get("status"),
            "blocker_codes_after": [issue.get("code") for issue in (preflight.get("blockers") or []) if issue.get("code")],
            "warning_codes_after": [issue.get("code") for issue in (preflight.get("warnings") or []) if issue.get("code")],
            "suggested_category": _category_hint_from_listing(listing),
            "image_summary": preflight.get("image_summary") or {},
        }

    def _launch_checklist(self, marketplace: str, preflight: dict[str, Any]) -> list[dict[str, Any]]:
        policy = preflight.get("policy_summary") or {}
        shipping = preflight.get("shipping_summary") or {}
        image_summary = preflight.get("image_summary") or {}
        checklist = [
            {"label": "Preflight ready", "ok": str(preflight.get("status") or "").lower() in {"ready", "ready_with_warnings", "published"}},
            {"label": "Photos present", "ok": bool(image_summary.get("actual_image_present"))},
            {"label": "Policies present", "ok": bool(policy.get("payment_policy_id") and policy.get("fulfillment_policy_id") and policy.get("return_policy_id"))},
            {"label": "Merchant location present", "ok": bool(policy.get("merchant_location_key"))},
            {"label": "Shipping weight present", "ok": shipping.get("package_weight") is not None},
            {"label": "Shipping dimensions present", "ok": bool(shipping.get("package_dimensions"))},
        ]
        if marketplace == MarketplaceName.ebay.value:
            checklist.extend(
                [
                    {"label": "eBay category set", "ok": bool((preflight.get("category_summary") or {}).get("category_id") or (preflight.get("category_summary") or {}).get("category_name"))},
                    {"label": "Required aspects covered", "ok": not bool((preflight.get("missing_fields") or []))},
                ]
            )
        return checklist
