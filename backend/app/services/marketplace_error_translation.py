from __future__ import annotations

from typing import Any


def _matches(message: str, *patterns: str) -> bool:
    lowered = message.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def translate_marketplace_error(marketplace: str, error: Any) -> dict[str, Any]:
    raw_error = str(error or "").strip()
    message = raw_error.lower()
    marketplace = str(marketplace or "").lower()

    if marketplace == "ebay":
        if _matches(message, "invalid access token", "oauth", "expired token", "invalid token", "401"):
            return {
                "code": "EBAY_OAUTH_EXPIRED",
                "marketplace_code": "oauth",
                "field": "account",
                "user_message": "eBay connection is not usable right now.",
                "fix_hint": "Reconnect eBay in Settings and retry publish.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "reconnect_ebay",
                "raw_error": raw_error,
            }
        if _matches(message, "missing scope", "insufficient permissions", "permission denied"):
            return {
                "code": "EBAY_MISSING_SCOPE",
                "marketplace_code": "oauth",
                "field": "account",
                "user_message": "The eBay account is missing a required API permission.",
                "fix_hint": "Reconnect eBay with the correct seller account and permissions.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "reconnect_ebay",
                "raw_error": raw_error,
            }
        if _matches(message, "sandbox", "production", "credential mismatch"):
            return {
                "code": "EBAY_CREDENTIAL_ENV_MISMATCH",
                "marketplace_code": "oauth",
                "field": "account",
                "user_message": "The eBay credential environment does not match this deployment.",
                "fix_hint": "Reconnect eBay using the same production or sandbox environment that PosterPro is configured for.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "reconnect_ebay",
                "raw_error": raw_error,
            }
        if _matches(message, "title too long"):
            return {
                "code": "EBAY_TITLE_TOO_LONG",
                "marketplace_code": "request",
                "field": "title",
                "user_message": "eBay rejected the title because it is too long.",
                "fix_hint": "Shorten the title to stay within eBay limits.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "manual_review",
                "raw_error": raw_error,
            }
        if _matches(message, "invalid category", "category not found"):
            return {
                "code": "EBAY_INVALID_CATEGORY",
                "marketplace_code": "category",
                "field": "category_id",
                "user_message": "eBay could not accept the selected category.",
                "fix_hint": "Choose a different eBay category or rerun category matching.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_category",
                "raw_error": raw_error,
            }
        if _matches(message, "leaf category"):
            return {
                "code": "EBAY_CATEGORY_NOT_LEAF",
                "marketplace_code": "category",
                "field": "category_id",
                "user_message": "The selected eBay category is not a usable leaf category.",
                "fix_hint": "Choose a more specific leaf category before retrying.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_category",
                "raw_error": raw_error,
            }
        if _matches(message, "item specific", "aspect"):
            return {
                "code": "EBAY_ITEM_SPECIFIC_MISSING",
                "marketplace_code": "category",
                "field": "item_specifics",
                "user_message": "eBay requires additional item specifics for this category.",
                "fix_hint": "Fill in the missing required aspects in the listing editor.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_aspects",
                "raw_error": raw_error,
            }
        if _matches(message, "invalid aspect value", "aspect value"):
            return {
                "code": "EBAY_ASPECT_INVALID",
                "marketplace_code": "category",
                "field": "item_specifics",
                "user_message": "One of the eBay item specifics has an invalid value.",
                "fix_hint": "Correct the item specific value to one allowed by the category metadata.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_aspects",
                "raw_error": raw_error,
            }
        if _matches(message, "business policy", "fulfillment policy", "payment policy", "return policy"):
            return {
                "code": "EBAY_POLICY_MISSING",
                "marketplace_code": "policy",
                "field": "ebay_marketplace_policy_settings",
                "user_message": "eBay listing policies are missing or invalid.",
                "fix_hint": "Save the payment, fulfillment, return, and merchant location settings in eBay settings.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_policies",
                "raw_error": raw_error,
            }
        if _matches(message, "invalid shipping policy", "shipping policy id", "fulfillment policy id"):
            return {
                "code": "EBAY_POLICY_MISSING",
                "marketplace_code": "policy",
                "field": "ebay_marketplace_policy_settings.fulfillment_policy_id",
                "user_message": "eBay shipping or fulfillment policy is invalid.",
                "fix_hint": "Sync or save the fulfillment policy again in Settings, then retry.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_policies",
                "raw_error": raw_error,
            }
        if _matches(message, "shipping", "package", "weight", "dimension"):
            return {
                "code": "EBAY_SHIPPING_DATA_INVALID",
                "marketplace_code": "shipping",
                "field": "shipping_profile",
                "user_message": "eBay rejected the shipping or package details.",
                "fix_hint": "Check weight, dimensions, and shipping policy values before retrying.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_shipping",
                "raw_error": raw_error,
            }
        if _matches(message, "invalid payment policy", "payment policy id"):
            return {
                "code": "EBAY_POLICY_MISSING",
                "marketplace_code": "policy",
                "field": "ebay_marketplace_policy_settings.payment_policy_id",
                "user_message": "eBay payment policy is invalid.",
                "fix_hint": "Sync or save the payment policy again in Settings, then retry.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_policies",
                "raw_error": raw_error,
            }
        if _matches(message, "invalid return policy", "return policy id"):
            return {
                "code": "EBAY_POLICY_MISSING",
                "marketplace_code": "policy",
                "field": "ebay_marketplace_policy_settings.return_policy_id",
                "user_message": "eBay return policy is invalid.",
                "fix_hint": "Sync or save the return policy again in Settings, then retry.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_policies",
                "raw_error": raw_error,
            }
        if _matches(message, "merchant location"):
            return {
                "code": "EBAY_MERCHANT_LOCATION_MISSING",
                "marketplace_code": "policy",
                "field": "merchant_location_key",
                "user_message": "eBay could not find a valid merchant location key.",
                "fix_hint": "Save a merchant location key in eBay policy settings or sync policies from eBay.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_policies",
                "raw_error": raw_error,
            }
        if _matches(message, "invalid image url", "image fetch failed", "missing file", "zero byte", "zero-byte"):
            return {
                "code": "EBAY_IMAGE_URL_INVALID",
                "marketplace_code": "media",
                "field": "image_urls",
                "user_message": "One or more eBay image URLs or files are not usable.",
                "fix_hint": "Verify the photo exists, is approved, and is publicly reachable before retrying.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_photos",
                "raw_error": raw_error,
            }
        if _matches(message, "duplicate sku", "offer entity already exists"):
            return {
                "code": "EBAY_DUPLICATE_SKU",
                "marketplace_code": "inventory",
                "field": "sku",
                "user_message": "eBay already has an offer for this SKU.",
                "fix_hint": "Use the existing eBay offer, or relist after removing the stale offer.",
                "severity": "warning",
                "retryable": True,
                "operator_action": "manual_review",
                "raw_error": raw_error,
            }
        if _matches(message, "rate limit", "too many requests"):
            return {
                "code": "EBAY_RATE_LIMIT",
                "marketplace_code": "api",
                "field": None,
                "user_message": "eBay rate limited the request.",
                "fix_hint": "Wait briefly and retry the publish operation.",
                "severity": "warning",
                "retryable": True,
                "operator_action": "wait_and_retry",
                "raw_error": raw_error,
            }
        if _matches(message, "revision limit", "revise limit", "revision exceeded"):
            return {
                "code": "EBAY_REVISION_LIMIT",
                "marketplace_code": "inventory",
                "field": None,
                "user_message": "eBay revision limit was reached.",
                "fix_hint": "Wait or create a new listing flow instead of retrying the same revision path.",
                "severity": "warning",
                "retryable": False,
                "operator_action": "manual_review",
                "raw_error": raw_error,
            }
        if _matches(message, "account restriction", "selling limit", "not eligible", "business policy"):
            return {
                "code": "EBAY_ACCOUNT_RESTRICTION",
                "marketplace_code": "account",
                "field": "account",
                "user_message": "The eBay account is restricted for this publish action.",
                "fix_hint": "Review the eBay account readiness panel and resolve the account or policy restriction before retrying.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "contact_marketplace",
                "raw_error": raw_error,
            }
        if _matches(message, "sandbox", "production", "credential mismatch"):
            return {
                "code": "EBAY_CREDENTIAL_ENV_MISMATCH",
                "marketplace_code": "oauth",
                "field": "account",
                "user_message": "The eBay credential environment does not match this deployment.",
                "fix_hint": "Reconnect eBay using the matching sandbox or production app settings.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "reconnect_ebay",
                "raw_error": raw_error,
            }
        if _matches(message, "invalid upc", "invalid gtin", "gtin", "upc"):
            return {
                "code": "EBAY_INVALID_UPC_GTIN",
                "marketplace_code": "category",
                "field": "item_specifics",
                "user_message": "eBay rejected the product identifier.",
                "fix_hint": "Remove the identifier or correct it to a valid UPC/GTIN for this item.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_aspects",
                "raw_error": raw_error,
            }
        if _matches(message, "image", "media"):
            return {
                "code": "EBAY_IMAGE_URL_INVALID",
                "marketplace_code": "media",
                "field": "image_urls",
                "user_message": "One or more eBay images were rejected.",
                "fix_hint": "Approve actual item photos and retry with valid public image URLs.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "fix_photos",
                "raw_error": raw_error,
            }

    if marketplace in {"facebook", "fb", "facebook marketplace"}:
        if _matches(message, "browser not connected", "session expired", "connect session expired", "bridge not configured", "session stale"):
            return {
                "code": "FACEBOOK_BROWSER_UNAVAILABLE",
                "marketplace_code": "bridge",
                "field": "bridge_account_key",
                "user_message": "Facebook assisted publishing cannot start because the browser session is unavailable.",
                "fix_hint": "Reconnect the Facebook bridge session in Settings and retry.",
                "severity": "blocker",
                "retryable": True,
                "operator_action": "manual_review",
                "raw_error": raw_error,
            }
        if _matches(message, "upload failed", "image upload failed"):
            return {
                "code": "FACEBOOK_IMAGE_UPLOAD_FAILED",
                "marketplace_code": "media",
                "field": "listing_images",
                "user_message": "Facebook image upload failed during assisted publish.",
                "fix_hint": "Verify the photos are accessible and try the handoff again.",
                "severity": "blocker",
                "retryable": True,
                "operator_action": "fix_photos",
                "raw_error": raw_error,
            }
        if _matches(message, "final submit unsupported", "handoff only"):
            return {
                "code": "FACEBOOK_FINAL_SUBMIT_UNSUPPORTED",
                "marketplace_code": "bridge",
                "field": "bridge_session",
                "user_message": "Facebook is assisted/handoff only in this workspace.",
                "fix_hint": "Use the bridge desktop handoff flow instead of expecting native direct publish.",
                "severity": "warning",
                "retryable": False,
                "operator_action": "manual_review",
                "raw_error": raw_error,
            }
        if _matches(message, "field missing", "title", "price", "category", "description", "location"):
            field = "listing"
            for candidate in ("title", "price", "category", "description"):
                if candidate in message:
                    field = candidate
                    break
            return {
                "code": "FACEBOOK_FIELD_MISSING",
                "marketplace_code": "form",
                "field": field,
                "user_message": "A required Facebook Marketplace field is missing.",
                "fix_hint": "Fill in the missing marketplace fields in the listing editor and retry.",
                "severity": "blocker",
                "retryable": False,
                "operator_action": "manual_review",
                "raw_error": raw_error,
            }
        if _matches(message, "blocked by ui change", "navigation failed", "manual intervention required", "selector changed"):
            return {
                "code": "FACEBOOK_UI_CHANGED",
                "marketplace_code": "bridge",
                "field": "bridge_session",
                "user_message": "Facebook Marketplace changed the page flow and PosterPro needs operator review.",
                "fix_hint": "Open the bridge desktop and complete the assisted handoff manually.",
                "severity": "warning",
                "retryable": True,
                "operator_action": "manual_review",
                "raw_error": raw_error,
            }

    return {
        "code": "MARKETPLACE_ERROR",
        "marketplace_code": "generic",
        "field": None,
        "user_message": raw_error or "Marketplace request failed.",
        "fix_hint": "Open the raw error details and correct the blocked marketplace field or connection.",
        "severity": "blocker",
        "retryable": False,
        "operator_action": "manual_review",
        "raw_error": raw_error,
    }
