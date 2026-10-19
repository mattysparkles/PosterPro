from __future__ import annotations

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
import hashlib
import json
import mimetypes
import re
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.models.enums import ListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceExtensionDevice, MarketplaceExtensionJob
from app.services.ebay_service import build_ebay_publish_plan
from app.services.listing_review import normalize_listing_images, summarize_listing_readiness
from app.services.marketplace_error_translation import translate_marketplace_error
from app.services.marketplace_field_mapper import build_marketplace_payload
from app.services.pricing_research_service import compute_listing_quality_summary
from app.services.canonical_readiness import canonical_listing_readiness
from app.services.automation_bridge import bridge_browser_submit_policy
from app.services.physical_inventory_availability import availability_for_listing
from app.services.shipping_profitability_policy import estimated_parcel_fields, unverified_parcel_fields

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
    "EBAY_WEIGHT_UNVERIFIED",
    "EBAY_DIMENSIONS_MISSING",
    "EBAY_DIMENSIONS_UNVERIFIED",
    "CONDITION_MISSING",
}

ACCOUNT_EXECUTION_BLOCKER_CODES = {
    "FACEBOOK_ASSISTED_WORKFLOW_UNVERIFIED",
    "EBAY_PAYMENT_POLICY_MISSING",
    "EBAY_FULFILLMENT_POLICY_MISSING",
    "EBAY_RETURN_POLICY_MISSING",
    "EBAY_MERCHANT_LOCATION_MISSING",
}


def compute_payload_hash(payload: Any) -> str | None:
    """Stable hash for comparing the exact preflight and execution payload."""
    if payload is None:
        return None
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _is_account_execution_blocker(issue: dict[str, Any]) -> bool:
    """Account/session setup must not be mistaken for incomplete listing data."""
    code = str(issue.get("code") or "").strip().upper()
    field = str(issue.get("field") or "").strip().lower()
    return code in ACCOUNT_EXECUTION_BLOCKER_CODES or field.startswith(("settings.", "marketplace_connections."))


def _issue(code: str, message: str, *, field: str | None = None, fix_hint: str | None = None, severity: str = "blocker", retryable: bool = False) -> dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "message": message,
        "fix_hint": fix_hint,
        "severity": severity,
        "retryable": retryable,
    }


def _facebook_publish_capability(db: Session, user_id: int) -> dict[str, Any]:
    """Separate supervised-workflow readiness from verified live capability.

    A passing diagnostic may let an operator start a real browser handoff, but
    it is not evidence that a listing was posted. Keeping these states apart
    removes the circular first-post gate without claiming a live publication.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    devices = db.query(MarketplaceExtensionDevice).filter(
        MarketplaceExtensionDevice.user_id == int(user_id),
        MarketplaceExtensionDevice.revoked_at.is_(None),
    ).all()
    fresh = [d for d in devices if d.last_seen_at and d.last_seen_at >= now - timedelta(minutes=3)]
    device = sorted(fresh, key=lambda row: row.last_seen_at or datetime.min, reverse=True)[0] if fresh else None
    state: dict[str, Any] = {
        "extension_connected": bool(device),
        "device_id": device.id if device else None,
        "extension_version": device.extension_version if device else None,
        "session_detected": False,
        "marketplace_accessible": False,
        "create_form_detected": False,
        "safe_diagnostic_passed": False,
        "assisted_workflow_capable": False,
        "publish_capable": False,
        "status": "BLOCKED",
        "execution_transport": "browser_extension",
    }

    # A healthy hosted bridge is an isolated Playwright worker, not the
    # operator's foreground browser.  It is a valid assisted transport when
    # an extension heartbeat is unavailable, and avoids turning an optional
    # browser companion into a hard publication gate.  Do not claim a remote
    # listing exists here; execution still records and verifies its result.
    bridge_policy = bridge_browser_submit_policy()
    state["hosted_bridge_configured"] = bool(bridge_policy.get("configured"))
    state["hosted_bridge_submit_enabled"] = bool(bridge_policy.get("browser_submit_enabled"))
    state["hosted_bridge_policy"] = str(bridge_policy.get("policy_label") or "") or None
    hosted_bridge_ready = bool(
        bridge_policy.get("configured") and bridge_policy.get("browser_submit_enabled")
    )
    diagnostic = db.query(MarketplaceExtensionJob).filter(
        MarketplaceExtensionJob.user_id == int(user_id),
        MarketplaceExtensionJob.marketplace == "facebook",
        MarketplaceExtensionJob.action == "DIAGNOSTIC",
    ).order_by(MarketplaceExtensionJob.created_at.desc(), MarketplaceExtensionJob.id.desc()).first()
    result = diagnostic.result if diagnostic and isinstance(diagnostic.result, dict) else {}
    diagnostic_is_current = bool(
        diagnostic
        and diagnostic.created_at
        and diagnostic.created_at >= now - timedelta(hours=24)
    )
    state["session_detected"] = str(result.get("login_state") or "").upper() == "LOGGED_IN"
    state["marketplace_accessible"] = str(result.get("page_state") or "").upper() in {"FORM_AVAILABLE", "FORM_DETECTED"}
    state["create_form_detected"] = state["marketplace_accessible"]
    state["safe_diagnostic_passed"] = bool(
        diagnostic_is_current
        and str(diagnostic.status or "").upper() == "COMPLETED"
        and result.get("capability_ready") is True
    )
    state["assisted_workflow_capable"] = bool(
        device
        and state["safe_diagnostic_passed"]
        and state["session_detected"]
        and state["marketplace_accessible"]
    )
    if hosted_bridge_ready and not state["assisted_workflow_capable"]:
        state["assisted_workflow_capable"] = True
        state["execution_transport"] = "hosted_browser_assist"
    state["publish_capable"] = bool(
        state["safe_diagnostic_passed"] and result.get("live_publish_verified") is True and result.get("remote_listing_id")
    )
    if state["publish_capable"]:
        state["status"] = "READY"
        state["reason"] = "Facebook live publish capability was verified with a real listing and remote identity."
        return state
    if state["assisted_workflow_capable"]:
        state["status"] = "ASSISTED_READY"
        state["reason"] = (
            "The paired browser is logged in and the Facebook listing form passed diagnostics. "
            "The operator must submit the form and confirm its remote listing identity in Jobs."
            if state["execution_transport"] == "browser_extension"
            else "The isolated hosted browser bridge is healthy and will run this Facebook workflow in the background without taking focus from the operator's browser."
        )
        return state
    if not device:
        state["reason"] = "Facebook browser extension is offline; open PosterPro in the paired browser to reconnect it."
    elif not diagnostic_is_current or str(diagnostic.status or "").upper() != "COMPLETED":
        state["reason"] = "Facebook browser workflow has not passed a form diagnostic in the last 24 hours."
    elif not state["safe_diagnostic_passed"]:
        missing = ", ".join(str(v) for v in (result.get("missing_required_fields") or []))
        state["reason"] = f"Facebook safe form diagnostic failed{': ' + missing if missing else ''}."
    else:
        state["reason"] = "Facebook safe diagnostic passed, but live publish has not yet been verified with an operator-authorized real listing."
    return state


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_datetime_for_compare(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _run_async_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


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
    listing_price = getattr(listing, "listing_price", None)
    suggested_price = getattr(listing, "suggested_price", None)
    return {
        "current_price": pricing.get("current_price") or listing_price or suggested_price,
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
    provenance = shipping.get("provenance") if isinstance(shipping.get("provenance"), dict) else {}
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
        "estimated": bool(shipping.get("estimated")),
        "estimated_fields": estimated_parcel_fields(shipping),
        "unverified_measurement_fields": unverified_parcel_fields(shipping),
        "measurement_provenance": provenance,
        "shipping_profile_source": shipping.get("shipping_profile_source"),
        "shipping_notes": shipping.get("shipping_notes"),
    }


class MarketplacePreflightService:
    def evaluate_publishability(
        self,
        db: Session,
        listing: Listing,
        marketplace: str,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Single publishability contract shared by queues and publishers.

        A cached result is usable only while fresh.  Missing/stale results are
        evaluated before a listing can be considered reviewable, so the UI
        cannot advertise a row that the publish path will immediately reject.
        """
        market = str(marketplace or "").strip().lower()
        preflight = None if refresh else self._cached_marketplace_preflight(listing, market)
        if preflight is None:
            preflight = self.preflight_listing(db, listing, market)
            self.cache_preflight_summary(db, listing, preflight)
        canonical = canonical_listing_readiness(listing, marketplace=market)
        blockers = [item for item in (preflight.get("blockers") or []) if isinstance(item, dict)]
        publishable = (
            str(preflight.get("status") or "").lower() in {"ready", "ready_with_warnings", "published"}
            and not blockers
            and bool(canonical.get("publishable"))
            and not bool(canonical.get("destination_preflight_pending"))
        )
        return {
            "marketplace": market,
            "publishable": publishable,
            "canonical": canonical,
            "preflight": preflight,
            "blockers": blockers,
            "warnings": [item for item in (preflight.get("warnings") or []) if isinstance(item, dict)],
            "stale": False,
        }

    def preflight_listing(
        self,
        db: Session,
        listing: Listing,
        marketplace: str,
        *,
        # Listing preparation is cache/local-first.  Shared marketplace
        # metadata is synchronized explicitly; a per-listing preflight must
        # not hammer eBay taxonomy endpoints or turn a 429 into a publish
        # blocker.
        allow_live_metadata_refresh: bool = False,
    ) -> dict[str, Any]:
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
                "listing_price": getattr(listing, "listing_price", None),
                "suggested_price": getattr(listing, "suggested_price", None),
            },
        )
        # Keep preflight's marketplace-specific diagnostics, but expose the
        # canonical readiness contract so callers do not have to reconcile a
        # second, divergent definition of processing/blocking state.
        canonical = canonical_listing_readiness(listing, marketplace=market)
        quality = compute_listing_quality_summary(listing, pricing_analysis=pricing)
        blockers = self._base_blockers(listing, market, pricing, readiness)
        warnings = self._base_warnings(listing, market, pricing, readiness)
        physical_inventory = availability_for_listing(db, listing)
        # A deterministic physical link is authoritative: a listing cannot be
        # queued for a quantity that its linked tote/package stock cannot
        # satisfy. Unlinked legacy drafts remain publishable but conspicuously
        # warn so an operator can attach them through scanner/manifest intake.
        if physical_inventory.get("tracked"):
            if not physical_inventory.get("sufficient"):
                blockers.append(_issue(
                    "PHYSICAL_STOCK_INSUFFICIENT",
                    "Physical inventory has %(available)s available unit(s), but this listing requests %(requested)s."
                    % {"available": physical_inventory.get("available_quantity", 0), "requested": physical_inventory.get("requested_quantity", 0)},
                    field="marketplace_data.physical_inventory",
                    fix_hint="Reconcile tote/package stock, reduce listing quantity, or add the received units before publishing.",
                ))
        else:
            warnings.append(_issue(
                "PHYSICAL_STOCK_UNLINKED",
                "This legacy listing is not linked to a physical inventory product, so availability cannot be verified.",
                field="marketplace_data.physical_inventory",
                fix_hint="Scan or reconcile the product into a tote/package before publishing to enable physical-stock protection.",
                severity="warning",
            ))
        # The canonical readiness service is the contract shared by queues and
        # publish workers.  Preserve its actionable blockers in this
        # destination preflight as well; otherwise the editor could display a
        # clean preflight while the queue/publisher correctly reject the same
        # listing.
        existing_blocker_messages = {
            str(issue.get("message") or issue.get("user_message") or issue.get("code") or "").strip().lower()
            for issue in blockers
            if isinstance(issue, dict)
        }
        # Do not feed aggregate/destination blockers back into the selected
        # marketplace. Canonical product blockers are shared; this destination
        # must be judged by its own category, payload, account, and transport
        # contract.
        for reason in canonical.get("canonical_blocking_reasons", canonical.get("blocking_reasons") or []) or []:
            message = str(reason or "").strip()
            # A missing/stale destination result is a workflow state, not a
            # product defect. This function is the operation that creates that
            # result; feeding the pending marker back into its own blocker list
            # made the first preflight permanently fail itself.
            if canonical.get("destination_preflight_pending") and "destination preflight is pending" in message.lower():
                continue
            if not message or message.lower() in existing_blocker_messages:
                continue
            blockers.append(_issue(
                "CANONICAL_READINESS_BLOCKED",
                message,
                fix_hint="Resolve the listed PosterPro readiness blocker before publishing.",
            ))
            existing_blocker_messages.add(message.lower())
        payload_preview: dict[str, Any] = {}
        policy_summary: dict[str, Any] = {}
        category_summary: dict[str, Any] = {}
        connection_blockers: list[dict[str, Any]] = []

        if market == MarketplaceName.ebay.value:
            ebay_plan = self._build_ebay_preflight(
                db, listing, readiness, pricing,
                allow_live_metadata_refresh=allow_live_metadata_refresh,
            )
            blockers.extend(ebay_plan["blockers"])
            warnings.extend(ebay_plan["warnings"])
            payload_preview = ebay_plan["payload_preview"]
            policy_summary = ebay_plan["policy_summary"]
            category_summary = ebay_plan["category_summary"]
        elif market == MarketplaceName.facebook.value:
            fb_plan = self._build_facebook_preflight(db, listing, readiness, pricing)
            blockers.extend(fb_plan["blockers"])
            warnings.extend(fb_plan["warnings"])
            payload_preview = fb_plan["payload_preview"]
            category_summary = fb_plan["category_summary"]
            policy_summary = fb_plan["policy_summary"]
            capability = _facebook_publish_capability(db, listing.user_id)
            policy_summary["facebook_capability"] = capability
            if not capability.get("assisted_workflow_capable"):
                connection_blockers.append(_issue(
                    "FACEBOOK_ASSISTED_WORKFLOW_UNVERIFIED",
                    str(capability.get("reason") or "Facebook browser-assisted workflow is not ready."),
                    field="marketplace_connections.facebook",
                    fix_hint="Pair the browser extension, sign in to Facebook Marketplace, and pass the form diagnostic. The operator must submit and confirm the resulting listing in Jobs.",
                    retryable=True,
                ))
        else:
            payload_preview = build_marketplace_payload(listing, market)

        # Preparation measures whether this product has the destination data
        # and a valid payload.  Account/session readiness is reported
        # separately: an unconnected marketplace must not make the product
        # incomplete or prevent a different destination from publishing it.
        preparation_blockers = [issue for issue in blockers if not _is_account_execution_blocker(issue)]
        # Legacy listings can remain preparation-complete while carrying the
        # non-blocking migration reminder that their physical stock has not
        # yet been linked. It is surfaced in ``warnings`` and the returned
        # inventory summary, but should not relabel an otherwise complete
        # product as needing preparation work.
        preparation_warnings = [
            issue for issue in warnings
            if not _is_account_execution_blocker(issue)
            and str(issue.get("code") or "") != "PHYSICAL_STOCK_UNLINKED"
        ]
        execution_blockers = [*blockers, *connection_blockers]
        preparation_status = "blocked" if preparation_blockers else "ready_with_warnings" if preparation_warnings else "ready"
        status = "ready"
        if execution_blockers:
            status = "blocked"
        elif warnings:
            status = "ready_with_warnings"
        elif quality.get("status") in {"blocked", "needs_review", "research_partial"} and not canonical.get("canonical_product_complete", False):
            status = "needs_review"

        if listing.ebay_publish_status and str(listing.ebay_publish_status).lower() == "posted":
            status = "published"
        elif listing.status == "error":
            status = "failed"

        suggested_fixes = [issue["fix_hint"] for issue in execution_blockers if issue.get("fix_hint")]
        required_operator_actions = sorted({issue["fix_hint"] or issue["message"] for issue in execution_blockers})

        return {
            "listing_id": listing.id,
            "marketplace": market,
            "status": status,
            "blockers": execution_blockers,
            "execution_status": status,
            "execution_blockers": execution_blockers,
            "connection_blockers": connection_blockers,
            "preparation_status": preparation_status,
            "preparation_blockers": preparation_blockers,
            "preparation_warnings": preparation_warnings,
            "warnings": warnings,
            "missing_fields": [issue["field"] for issue in execution_blockers if issue.get("field")],
            "invalid_fields": [issue["field"] for issue in execution_blockers if issue.get("field") and "INVALID" in issue["code"]],
            "suggested_fixes": suggested_fixes,
            "required_operator_actions": required_operator_actions,
            "payload_preview": {"payload": payload_preview, "sections": list(payload_preview.keys()) if isinstance(payload_preview, dict) else []},
            # The worker executes the canonical marketplace mapper, not the
            # eBay-specific preview envelope above.  Persist that exact
            # execution representation so the integrity check compares like
            # with like instead of rejecting every valid eBay job.
            "execution_payload_hash": compute_payload_hash(build_marketplace_payload(listing, market)),
            "policy_summary": policy_summary,
            "category_summary": category_summary,
            "shipping_summary": _shipping_summary(listing),
            "image_summary": _image_summary(listing, readiness),
            "pricing_summary": _listing_pricing_summary(listing),
            "condition_summary": _condition_summary(listing),
            "physical_inventory_summary": physical_inventory,
            "last_checked_at": datetime.now(UTC),
            "source_version": "preflight_v1",
            "quality_summary": quality,
            "readiness_summary": readiness,
            "canonical_readiness": canonical,
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
        marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        if isinstance(marketplace_data.get("pricing_review_pause"), dict) and marketplace_data["pricing_review_pause"].get("active"):
            blockers.append(_issue("PRICING_REVIEW_PAUSED", "Publishing is paused while the operator reviews a high-confidence price risk.", field="listing_price", fix_hint="Review the sold-comparable evidence and choose leave price, apply the protected price, or clear the pause."))
        risk = pricing.get("underpricing_risk") if isinstance(pricing.get("underpricing_risk"), dict) else {}
        acknowledgement = marketplace_data.get("pricing_underpricing_acknowledgement") if isinstance(marketplace_data.get("pricing_underpricing_acknowledgement"), dict) else {}
        if risk.get("level") == "SEVERE" and risk.get("evidence_signature") and acknowledgement.get("evidence_signature") != risk.get("evidence_signature"):
            blockers.append(_issue("UNDERPRICING_REVIEW_REQUIRED", "High-confidence sold evidence suggests this price may be substantially too low.", field="listing_price", fix_hint="Review the sold-comparable evidence and choose Leave Price As Is, Auto-fix Price, or Pause Listing."))
        if not (listing.title or "").strip():
            blockers.append(_issue("TITLE_MISSING", "Draft title is missing.", field="title", fix_hint="Enter a concise product title."))
        if not (listing.description or "").strip():
            blockers.append(_issue("DESCRIPTION_MISSING", "Draft description is missing.", field="description", fix_hint="Add the key item details, defects, and included accessories."))
        elif str(getattr(listing, "source_type", "") or "").lower() == "amazon_vine":
            description = " ".join(str(listing.description or "").split()).lower()
            facts = (listing.source_metadata or {}).get("amazon_product_facts") if isinstance(listing.source_metadata, dict) else {}
            substantive = len((facts or {}).get("feature_bullets") or []) + len((facts or {}).get("specifications") or {}) + sum(bool((facts or {}).get(key)) for key in ("brand", "model", "material", "capacity", "product_description"))
            # Feature bullets are normalized into factual fragments by the
            # Vine formatter; compare long-form product prose here to avoid
            # mistaking expected title/spec overlap for copied prose.
            source_prose = [str((facts or {}).get("product_description") or "")]
            desc_tokens = re.findall(r"[a-z0-9]+", description)
            title_tokens = re.findall(r"[a-z0-9]+", str(listing.title or "").lower())
            source_copy = False
            for prose in source_prose:
                source_tokens = re.findall(r"[a-z0-9]+", prose.lower())
                if len(source_tokens) < 18:
                    continue
                # Amazon bullets frequently begin with the exact product title;
                # that identity overlap is expected and is not plagiarism.
                if title_tokens and source_tokens[: min(8, len(title_tokens))] == title_tokens[: min(8, len(title_tokens))]:
                    source_tokens = source_tokens[len(title_tokens):]
                if len(source_tokens) < 18:
                    continue
                # Product titles, model numbers, and short factual phrases are
                # expected to overlap.  Flag only a long contiguous run that
                # indicates source prose was copied into buyer-facing copy.
                haystack = " ".join(desc_tokens)
                needle = " ".join(source_tokens)
                if needle in haystack or any(" ".join(source_tokens[i:i + 18]) in haystack for i in range(max(1, len(source_tokens) - 17))):
                    source_copy = True
                    break
            placeholder = "verified amazon product record" in description or "being prepared from" in description
            title_only = len(description) < 180 and description.replace(".", "") == " ".join(str(listing.title or "").split()).lower().replace(".", "")
            # Description enrichment is recommended but must not prevent an
            # otherwise publishable Vine listing from being submitted. A
            # missing description remains a hard blocker above; quality and
            # originality recommendations are surfaced separately by the
            # readiness/diagnostic payload.
        listing_price = getattr(listing, "listing_price", None)
        suggested_price = getattr(listing, "suggested_price", None)
        manual_price = marketplace_data.get("manual_price_override")
        manual_price_reason = str(marketplace_data.get("manual_price_override_reason") or "").strip()
        pricing_provenance = str(pricing.get("provenance") or "").strip().lower()
        marketplace_overrides = marketplace_data.get("marketplace_overrides") if isinstance(marketplace_data.get("marketplace_overrides"), dict) else {}
        ebay_override = marketplace_overrides.get("ebay") if isinstance(marketplace_overrides.get("ebay"), dict) else {}
        operator_price_evidence = (
            manual_price not in (None, "") and bool(manual_price_reason)
            or ebay_override.get("price") not in (None, "")
        ) or pricing_provenance.startswith(("operator", "manual"))
        source_type = str(getattr(listing, "source_type", "") or "").strip().lower()
        research_gated_source = source_type in {
            "intake_head_slate", "google_photos_album", "media_inventory_recovery", "amazon_vine",
        }
        trusted_research = pricing.get("recommendation_eligible") is True or pricing_provenance in {
            "sold_comps", "active_comps", "source_msrp", "source_retail", "market_comps",
        }
        if (
            (pricing.get("recommendation_eligible") is False or (research_gated_source and not trusted_research))
            and not operator_price_evidence
        ):
            blockers.append(_issue(
                "PRICING_EVIDENCE_MISSING",
                "The stored amount is an unsupported estimate, not an authorized listing price.",
                field="listing_price",
                fix_hint="Complete comparable/source-price research or enter and document an operator-approved price.",
            ))
        if not (listing_price or suggested_price or pricing.get("current_price") or pricing.get("recommended_price")):
            blockers.append(_issue("PRICE_MISSING", "Listing price is missing.", field="listing_price", fix_hint="Apply a pricing recommendation or enter a list price."))
        if int(getattr(listing, "quantity", 0) or 0) <= 0:
            blockers.append(_issue("QUANTITY_INVALID", "Quantity must be at least 1.", field="quantity", fix_hint="Set quantity to 1 for a single-item listing."))
        if not (listing.condition or (listing.condition_data or {}).get("condition_bucket")):
            blockers.append(_issue("CONDITION_MISSING", "Condition review is missing.", field="condition", fix_hint="Set the condition bucket and operator notes before publish."))
        if not (listing.category_id or listing.category_suggestion or listing.detected_category_guess):
            blockers.append(_issue("CATEGORY_MISSING", "Category is missing.", field="category_id", fix_hint="Choose a category suggestion or assign a marketplace category."))
        if not readiness.get("actual_image_count"):
            blockers.append(_issue("ACTUAL_PHOTOS_MISSING", "No approved actual item photos are attached.", field="listing_images", fix_hint="Approve or upload real item photos before publishing."))
        if readiness.get("manual_photo_needed"):
            blockers.append(_issue("REFERENCE_IMAGES_ONLY", "Only source/reference images are attached.", field="listing_images", fix_hint="Add or approve actual item photos before publishing.", severity="warning"))
        shipping_check = readiness.get("shipping_checklist", {})
        # Estimated weight/dimensions are valid draft data.  Only block eBay
        # when the payload is actually missing a usable value; an operator can
        # replace the estimate with measured packaging later.
        if marketplace == MarketplaceName.ebay.value and (
            not shipping_check.get("weight_present") or not shipping_check.get("package_dimensions_present")
        ):
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

    def _build_facebook_preflight(self, db: Session, listing: Listing, readiness: dict[str, Any], pricing: dict[str, Any]) -> dict[str, Any]:
        from app.models.models import User
        from app.services.facebook_category_resolver import resolve_facebook_category, listing_is_explicitly_local_only, listing_is_shippable
        from app.services.marketplace_setup import load_manual_marketplace_settings

        payload = build_marketplace_payload(listing, MarketplaceName.facebook.value)
        user = db.get(User, listing.user_id)
        preferences = load_manual_marketplace_settings(user).get("facebook", {}) if user else {}
        decision = resolve_facebook_category(listing, prefer_shipping_capable=bool(preferences.get("prefer_shipping_capable_categories", True)), allow_local_pickup_only=bool(preferences.get("allow_local_pickup_only_categories", False)))
        payload.update({"category_hint": decision.category, "facebook_category": decision.category, "category_source": decision.source, "category_confidence": decision.confidence, "category_supports_shipping": decision.supports_shipping})
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        if not payload.get("title"):
            blockers.append(_issue("FACEBOOK_TITLE_MISSING", "Facebook title is missing.", field="title", fix_hint="Add a concise title that names the actual item."))
        if not payload.get("price"):
            blockers.append(_issue("FACEBOOK_PRICE_MISSING", "Facebook price is missing.", field="listing_price", fix_hint="Apply pricing before handing this listing off."))
        if not payload.get("category_hint"):
            blockers.append(_issue("FACEBOOK_CATEGORY_MISSING", "Facebook category is missing.", field="category_id", fix_hint="Choose a category suggestion or enter a category hint."))
        if listing_is_shippable(listing) and not listing_is_explicitly_local_only(listing) and not decision.supports_shipping:
            blockers.append(_issue("FACEBOOK_NO_SHIPPING_COMPATIBLE_CATEGORY", "No evidence-backed Facebook category supports shipping for this shippable item.", field="category_id", fix_hint="Add verified product details or explicitly mark the item local-pickup-only."))
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
                "category_source": payload.get("category_source"),
                "category_confidence": payload.get("category_confidence"),
                "supports_shipping": payload.get("category_supports_shipping"),
            },
        }

    def _build_ebay_preflight(
        self,
        db: Session,
        listing: Listing,
        readiness: dict[str, Any],
        pricing: dict[str, Any],
        *,
        allow_live_metadata_refresh: bool = True,
    ) -> dict[str, Any]:
        try:
            try:
                plan = _run_async_sync(build_ebay_publish_plan(
                    listing, db,
                    allow_create_policies=False,
                    allow_live_metadata_refresh=allow_live_metadata_refresh,
                ))
            except TypeError as exc:
                # Keep the preflight seam compatible with lightweight plan
                # providers used by integrations/tests that predate the
                # optional live-metadata flag.  This is only a signature
                # fallback; real plan errors still follow the outer handler.
                if "allow_live_metadata_refresh" not in str(exc):
                    raise
                plan = _run_async_sync(build_ebay_publish_plan(
                    listing, db,
                    allow_create_policies=False,
                ))
        except Exception as exc:
            translated = translate_marketplace_error("ebay", exc)
            retryable_warning = bool(translated.get("retryable")) and str(translated.get("severity") or "").lower() == "warning"
            return {
                "blockers": [] if retryable_warning else [translated],
                "warnings": [translated] if retryable_warning else [],
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

        # Required-aspect semantics are feedback on category quality.  A
        # current consumer product should not be parked in Needs Attention to
        # answer "Original/Reproduction", nor should a false Motors match be
        # left behind a publisher-path warning.  Reconsider with live aspect
        # metadata before exposing either blocker to the operator.
        suspicious_original = any(str(name).strip().lower() in {"original/reproduction", "original reproduction"}
                                  for name in (aspect_summary.get("missing_required") or aspect_summary.get("required") or []))
        suspicious_motors = str(category.get("taxonomy_tree_id") or "") == "100"
        suspicious_semantics = bool(category.get("semanticFlags") or category.get("revalidationRequired"))
        if allow_live_metadata_refresh and (suspicious_original or suspicious_motors or suspicious_semantics):
            try:
                from app.services.ebay_category_readiness_repair import repair_ebay_category_readiness
                repaired = _run_async_sync(repair_ebay_category_readiness(
                    db, listing,
                    reason=("required_aspect_semantic_sanity" if suspicious_original else
                            "motors_publisher_compatibility_sanity" if suspicious_motors else
                            "category_semantic_flag_second_pass"),
                ))
                if repaired.get("changed"):
                    db.flush()
                    plan = _run_async_sync(build_ebay_publish_plan(
                        listing, db, allow_create_policies=False, allow_live_metadata_refresh=True,
                    ))
                    category = plan.get("category") or {}
                    policy_settings = plan.get("policy_settings") or {}
                    aspect_summary = plan.get("aspect_summary") or {}
            except Exception:
                # A taxonomy outage must not fabricate a new category.  The
                # normal plan/preflight continues and exposes only real data.
                pass

        # Motors is a distinct taxonomy and publisher domain. Only verified
        # tree-100 product/category pairs may use the Trading create path;
        # never send a Motors category ID through the EBAY_US Inventory offer path.
        motors_identity_verified = (
            str(category.get("taxonomy_marketplace") or category.get("taxonomyMarketplace") or "").upper() == "EBAY_MOTORS_US"
            and str(category.get("taxonomy_tree_id") or category.get("taxonomyTreeId") or "") == "100"
            and bool(category.get("category_path") or category.get("categoryPath"))
            and bool(category.get("ownership_verified", category.get("ownershipVerified", False)))
        )
        motors_semantics_verified = str(category.get("semantic_motors_classification") or category.get("semanticMotorsClassification") or "") == "TRUE_MOTORS"
        if (str(category.get("taxonomy_tree_id") or category.get("taxonomyTreeId") or "").strip() == "100"
                and motors_identity_verified and motors_semantics_verified
                and not bool(category.get("revalidation_required") or category.get("revalidationRequired"))):
            category["publisher_path"] = "TRADING_API_MOTORS"
            category["publisherPath"] = "TRADING_API_MOTORS"
            category["semantic_motors_classification"] = "TRUE_MOTORS"
            category["semanticMotorsClassification"] = "TRUE_MOTORS"
        elif category.get("revalidation_required") or category.get("revalidationRequired") or str(category.get("taxonomy_tree_id") or category.get("taxonomyTreeId") or "").strip() == "100":
            blockers.append(_issue(
                "CATEGORY_REVALIDATION_REQUIRED",
                "The saved eBay category does not have verified taxonomy ownership and product-semantic evidence. Re-resolve it before publishing.",
                field="category_id",
                fix_hint="Resolve the category against its explicit eBay taxonomy tree and verify product fit before preflight.",
            ))

        if not listing.category_id and not listing.category_suggestion and not listing.detected_category_guess:
            blockers.append(_issue("EBAY_CATEGORY_MISSING", "eBay category is missing.", field="category_id", fix_hint="Choose a reliable eBay category before publish."))
        elif not category.get("metadata_available"):
            warnings.append(_issue("EBAY_CATEGORY_METADATA_UNAVAILABLE", "eBay category metadata could not be fetched right now.", field="category_id", fix_hint="Retry preflight when eBay metadata is available.", severity="warning", retryable=True))
        elif not listing.category_id:
            warnings.append(_issue("EBAY_CATEGORY_NEEDS_REVIEW", "eBay category was inferred instead of explicitly chosen.", field="category_id", fix_hint="Review the category suggestion before publishing.", severity="warning"))
        if str(getattr(listing, "source_type", "") or "").lower() == "amazon_vine" and category.get("leaf_verified") is not True:
            blockers.append(_issue(
                "EBAY_CATEGORY_LEAF_UNVERIFIED",
                "The resolved Vine eBay category has not been confirmed as a current leaf category.",
                field="category_id",
                fix_hint="Resolve and verify a current leaf category in the eBay taxonomy before publishing.",
            ))

        for field_key, code, label in [
            ("payment_policy_id", "EBAY_PAYMENT_POLICY_MISSING", "payment policy"),
            ("fulfillment_policy_id", "EBAY_FULFILLMENT_POLICY_MISSING", "fulfillment policy"),
            ("return_policy_id", "EBAY_RETURN_POLICY_MISSING", "return policy"),
            ("merchant_location_key", "EBAY_MERCHANT_LOCATION_MISSING", "merchant location key"),
        ]:
            if not str(policy_settings.get(field_key) or "").strip():
                blockers.append(_issue(code, f"eBay {label} is missing.", field=f"settings.ebay_marketplace_policy_settings.{field_key}", fix_hint=f"Save a valid eBay {label} in Settings or sync it from eBay."))

        # Trading API Motors listings cannot rely on Inventory API's location
        # assumptions.  Make the actual Trading-path prerequisites explicit
        # during preflight so an operator sees the missing account setup, not
        # a generic category/data blocker.
        if category.get("publisher_path") == "TRADING_API_MOTORS":
            trading_policy_ids = plan.get("policy_ids") if isinstance(plan.get("policy_ids"), dict) else {}
            if not all(
                str(trading_policy_ids.get(key) or "").strip()
                for key in ("paymentPolicyId", "fulfillmentPolicyId", "returnPolicyId")
            ):
                blockers.append(_issue(
                    "EBAY_TRADING_BUSINESS_POLICIES_MISSING",
                    "The connected eBay account needs verified payment, fulfillment, and return business policies before a Motors listing can be created.",
                    field="settings.ebay_marketplace_policy_settings",
                    fix_hint="Sync the connected eBay seller business policies, then rerun preflight.",
                ))
            if not policy_settings.get("merchant_location_verified"):
                blockers.append(_issue(
                    "EBAY_TRADING_MERCHANT_LOCATION_UNVERIFIED",
                    "The connected eBay account needs a verified ship-from location before a Motors listing can be created.",
                    field="settings.ebay_marketplace_policy_settings.merchant_location_key",
                    fix_hint="Verify the eBay merchant location in Settings, then rerun preflight.",
                ))

        missing_required = aspect_summary.get("missing_required") or []
        for aspect_name in missing_required:
            blockers.append(_issue(
                "EBAY_REQUIRED_ASPECT_MISSING",
                f"{aspect_name} is required for this eBay category.",
                field=f"item_specifics.{aspect_name}",
                fix_hint=f"Enter {aspect_name} in the item specifics section or choose a category that does not require it.",
            ))

        outgoing_aspects = {}
        try:
            outgoing_aspects = ((plan.get("inventory_item_payload") or {}).get("product") or {}).get("aspects") or {}
        except AttributeError:
            outgoing_aspects = {}
        # Canonical Amazon facts are intentionally richer than any one eBay
        # category.  Only report an unsupported-aspect warning when the field
        # actually survives into the eBay payload; filtered source facts are
        # not marketplace warnings.
        for aspect_name in (aspect_summary.get("unsupported") or []):
            if aspect_name not in outgoing_aspects:
                continue
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
        elif "package_weight" in estimated_parcel_fields(shipping_profile):
            blockers.append(_issue("EBAY_WEIGHT_UNVERIFIED", "Package weight is an estimate, not a verified packed measurement.", field="shipping_profile.package_weight", fix_hint="Weigh the packed item and record the measured value and provenance before publishing with weight-based shipping."))
        elif "package_weight" in unverified_parcel_fields(shipping_profile):
            blockers.append(_issue("EBAY_WEIGHT_UNVERIFIED", "Package weight has no packed-measurement confirmation.", field="shipping_profile.package_weight", fix_hint="Weigh the final packed parcel and explicitly confirm the measurement before publishing with weight-based shipping."))
        if not (shipping_profile.get("package_dimensions") or {}).get("length") or not (shipping_profile.get("package_dimensions") or {}).get("width") or not (shipping_profile.get("package_dimensions") or {}).get("height"):
            blockers.append(_issue("EBAY_DIMENSIONS_MISSING", "Package dimensions are missing.", field="shipping_profile.package_dimensions", fix_hint="Enter length, width, and height for the packed item."))
        elif "package_dimensions" in estimated_parcel_fields(shipping_profile):
            blockers.append(_issue("EBAY_DIMENSIONS_UNVERIFIED", "Package dimensions are estimates, not verified packed measurements.", field="shipping_profile.package_dimensions", fix_hint="Measure the packed item's length, width, and height before publishing where dimensional shipping data is required."))
        elif "package_dimensions" in unverified_parcel_fields(shipping_profile):
            blockers.append(_issue("EBAY_DIMENSIONS_UNVERIFIED", "Package dimensions have no packed-measurement confirmation.", field="shipping_profile.package_dimensions", fix_hint="Measure the final packed parcel and explicitly confirm all three dimensions before publishing where dimensional shipping data is required."))
        image_summary = _image_summary(listing, readiness)
        # The publish-plan builder validates dimensions and resolves internal
        # storage paths to the public eBay image URLs. Use that authoritative
        # result when available; checking only the compatibility column here
        # incorrectly blocked imported rows whose normalized listing_images
        # were valid and publishable.
        plan_image_summary = plan.get("image_summary") if isinstance(plan.get("image_summary"), dict) else {}
        if int(plan_image_summary.get("image_count") or 0) > 0:
            image_summary["public_image_ready"] = True
        if readiness.get("manual_photo_needed"):
            warnings.append(_issue("EBAY_REFERENCE_ONLY_IMAGES", "eBay will work better with actual item photos instead of reference images.", field="listing_images", fix_hint="Approve actual item photos before publishing.", severity="warning"))
        if image_summary.get("actual_image_present") and not image_summary.get("reference_only") and not image_summary.get("public_image_ready"):
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
        last_checked_at = _normalize_datetime_for_compare(_parse_datetime(cached.get("last_checked_at")))
        if not last_checked_at:
            return None
        listing_updated_at = _normalize_datetime_for_compare(listing.updated_at)
        # Persisting the compact preflight summary updates the parent JSON
        # column and therefore ``listing.updated_at`` in the same transaction.
        # Treat that tiny write-order skew as the cache write itself, while
        # still invalidating genuinely older results after a meaningful edit.
        if listing_updated_at and last_checked_at < listing_updated_at and (listing_updated_at - last_checked_at).total_seconds() > 5:
            return None
        if datetime.now(UTC).replace(tzinfo=None) - last_checked_at > PRELIGHT_CACHE_TTL:
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
            "execution_status": preflight.get("execution_status", status),
            "execution_blockers": preflight.get("execution_blockers", blockers),
            "connection_blockers": preflight.get("connection_blockers", []),
            "preparation_status": preflight.get("preparation_status", status),
            "preparation_blockers": preflight.get("preparation_blockers", blockers),
            "preparation_warnings": preflight.get("preparation_warnings", warnings),
            # Preserve the normalized issue objects, not only parallel code
            # and message arrays. Consumers such as Draft Editor, Jobs, and
            # admin diagnostics need field/fix metadata to explain a block.
            "blockers": blockers,
            "warnings": warnings,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "blocker_codes": [str(item.get("code") or "").strip() for item in blockers if str(item.get("code") or "").strip()],
            "warning_codes": [str(item.get("code") or "").strip() for item in warnings if str(item.get("code") or "").strip()],
            "blocker_messages": [str(item.get("message") or item.get("user_message") or item.get("raw_error") or item.get("code") or "").strip() for item in blockers if str(item.get("message") or item.get("user_message") or item.get("raw_error") or item.get("code") or "").strip()],
            "warning_messages": [str(item.get("message") or item.get("user_message") or item.get("raw_error") or item.get("code") or "").strip() for item in warnings if str(item.get("message") or item.get("user_message") or item.get("raw_error") or item.get("code") or "").strip()],
            "missing_fields": [str(item or "").strip() for item in (preflight.get("missing_fields") or []) if str(item or "").strip()],
            "invalid_fields": [str(item or "").strip() for item in (preflight.get("invalid_fields") or []) if str(item or "").strip()],
            "ready": status in {"ready", "ready_with_warnings", "published"},
            "payload_preview_available": bool(payload_preview.get("payload")),
            "cached": cached,
            "stale": bool(preflight.get("stale")),
            "last_checked_at": last_checked_at,
            "source_version": str(preflight.get("source_version") or PRELIGHT_CACHE_VERSION),
            # Keep the exact qualified category identity in the durable
            # preflight projection. The operator UI must not display a local
            # category path while hiding the different tree/category used by
            # the publish plan.
            "category_summary": dict(preflight.get("category_summary") or {}) if isinstance(preflight.get("category_summary"), dict) else {},
            "top_blocker_code": blockers[0].get("code") if blockers else None,
            "top_warning_code": warnings[0].get("code") if warnings else None,
            "top_blocker_message": (blockers[0].get("message") or blockers[0].get("user_message") or blockers[0].get("raw_error") or blockers[0].get("code")) if blockers else None,
            "top_warning_message": (warnings[0].get("message") or warnings[0].get("user_message") or warnings[0].get("raw_error") or warnings[0].get("code")) if warnings else None,
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
        # JSON columns may already contain the same dict identity after a
        # prior read; explicitly mark it dirty so fresh blocker state cannot
        # be silently skipped on commit.
        try:
            flag_modified(listing, "marketplace_data")
        except (AttributeError, KeyError):
            # Lightweight listing doubles used by integration tests are not
            # SQLAlchemy instrumented objects. The assignment above is still
            # sufficient for those callers; mapped production rows are always
            # instrumented and take the dirty-tracking path.
            pass
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
                        "listing_price": getattr(listing, "listing_price", None),
                        "suggested_price": getattr(listing, "suggested_price", None),
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
                        "listing_price": getattr(listing, "listing_price", None),
                        "suggested_price": getattr(listing, "suggested_price", None),
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
            price = float(
                getattr(listing, "listing_price", None)
                or getattr(listing, "suggested_price", None)
                or getattr(listing, "buy_it_now_price", None)
                or getattr(listing, "estimated_value", None)
                or 0
            )
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
            price = float(
                getattr(listing, "listing_price", None)
                or getattr(listing, "suggested_price", None)
                or getattr(listing, "buy_it_now_price", None)
                or getattr(listing, "estimated_value", None)
                or 0
            )
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
            if not str(listing.category_id or "").strip():
                try:
                    plan = _run_async_sync(build_ebay_publish_plan(listing, db, allow_create_policies=False))
                except Exception:  # noqa: BLE001
                    plan = {}
                category = plan.get("category") if isinstance(plan, dict) else {}
                category_id = str((category or {}).get("category_id") or "").strip()
                category_name = str((category or {}).get("category_name") or "").strip()
                if category_id.isdigit():
                    listing.category_id = category_id
                    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
                    listing.marketplace_data = {
                        **marketplace_data,
                        "ebay_last_resolved_category": {
                            "category_id": category_id,
                            "category_name": category_name or category_id,
                            "source": str((category or {}).get("source") or "ebay_taxonomy").strip() or "ebay_taxonomy",
                        },
                    }
                    if category_name and not str(listing.category_suggestion or "").strip():
                        listing.category_suggestion = category_name
                    changes["applied"].append("category_id")
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
