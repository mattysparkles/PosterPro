from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import settings
from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, User
from app.services.ebay_service import summarize_ebay_account_health

MANUAL_MARKETPLACES = {
    MarketplaceName.etsy.value,
    MarketplaceName.facebook.value,
    MarketplaceName.mercari.value,
    MarketplaceName.poshmark.value,
    MarketplaceName.depop.value,
    MarketplaceName.whatnot.value,
    MarketplaceName.vinted.value,
}

MANUAL_WORKFLOW_READY = "ready"

PUBLISH_SUPPORT_LABELS = {
    "direct_api": "Direct API publish",
    "browser_assist": "Browser-assisted publish",
    "provider_assist": "Provider-assisted publish",
    "manual_review": "Manual review publish",
    "draft_only": "Draft-only assisted publish",
}

IMPORT_SUPPORT_LABELS = {
    "direct_api": "Direct import",
    "browser_assist": "Browser-assisted import",
    "provider_assist": "Provider-assisted import",
    "csv_assist": "CSV-assisted import",
    "manual": "Manual import",
}

SALES_SYNC_SUPPORT_LABELS = {
    "direct_api": "Live sales sync",
    "unsupported": "Sales sync unavailable",
}

MARKETPLACE_SETUP_PROFILES: dict[str, dict[str, Any]] = {
    MarketplaceName.etsy.value: {
        "status_label": "shop details",
        "draft_note": "Add Etsy shop details, fulfillment expectations, and bridge/provider notes before enabling the channel.",
        "saved_note": "Etsy setup details are saved, but the channel is not marked ready yet.",
        "ready_note": "Etsy browser/provider-assisted workflow is saved for this marketplace.",
        "default_import_mode": "csv_assist",
        "default_publish_mode": "browser_assist",
        "default_shipping_scope": "shipping_only",
    },
    MarketplaceName.facebook.value: {
        "status_label": "marketplace profile",
        "draft_note": "Add Facebook Marketplace profile details and browser-assist notes before enabling the channel.",
        "saved_note": "Facebook setup details are saved, but the channel is not marked ready yet.",
        "ready_note": "Facebook browser/provider-assisted workflow is saved for this marketplace.",
        "default_import_mode": "browser_assist",
        "default_publish_mode": "browser_assist",
        "default_shipping_scope": "local_only",
    },
    MarketplaceName.mercari.value: {
        "status_label": "closet details",
        "draft_note": "Add Mercari shop details and fulfillment notes before enabling the channel.",
        "saved_note": "Mercari setup details are saved, but the channel is not marked ready yet.",
        "ready_note": "Mercari browser/provider-assisted workflow is saved for this marketplace.",
        "default_import_mode": "manual",
        "default_publish_mode": "browser_assist",
        "default_shipping_scope": "shipping_only",
    },
    MarketplaceName.poshmark.value: {
        "status_label": "closet details",
        "draft_note": "Add Poshmark closet details and operator workflow notes before enabling the channel.",
        "saved_note": "Poshmark setup details are saved, but the channel is not marked ready yet.",
        "ready_note": "Poshmark browser/provider-assisted workflow is saved for this marketplace.",
        "default_import_mode": "manual",
        "default_publish_mode": "browser_assist",
        "default_shipping_scope": "shipping_only",
    },
    MarketplaceName.depop.value: {
        "status_label": "shop details",
        "draft_note": "Add Depop shop details and shipping notes before enabling the channel.",
        "saved_note": "Depop setup details are saved, but the channel is not marked ready yet.",
        "ready_note": "Depop provider/browser-assisted workflow is saved for this marketplace.",
        "default_import_mode": "provider_assist",
        "default_publish_mode": "provider_assist",
        "default_shipping_scope": "shipping_only",
    },
    MarketplaceName.whatnot.value: {
        "status_label": "seller details",
        "draft_note": "Add Whatnot seller details and live-selling workflow notes before enabling the channel.",
        "saved_note": "Whatnot setup details are saved, but the channel is not marked ready yet.",
        "ready_note": "Whatnot browser/provider-assisted workflow is saved for this marketplace.",
        "default_import_mode": "manual",
        "default_publish_mode": "browser_assist",
        "default_shipping_scope": "shipping_only",
    },
    MarketplaceName.vinted.value: {
        "status_label": "closet details",
        "draft_note": "Add Vinted account details and shipping notes before enabling the channel.",
        "saved_note": "Vinted setup details are saved, but the channel is not marked ready yet.",
        "ready_note": "Vinted provider/browser-assisted workflow is saved for this marketplace.",
        "default_import_mode": "provider_assist",
        "default_publish_mode": "provider_assist",
        "default_shipping_scope": "shipping_only",
    },
}

MARKETPLACE_UI_PRIORITY = {
    MarketplaceName.ebay.value: 1,
    MarketplaceName.facebook.value: 2,
    MarketplaceName.mercari.value: 3,
    MarketplaceName.poshmark.value: 4,
    MarketplaceName.whatnot.value: 5,
    MarketplaceName.etsy.value: 6,
    MarketplaceName.depop.value: 7,
    MarketplaceName.vinted.value: 8,
}


def _normalize_import_listing_limit(value: Any) -> int:
    try:
        parsed = int(value or 10)
    except (TypeError, ValueError):
        parsed = 10
    return max(1, min(50, parsed))


def _publish_support_contract(*, marketplace: str, publish_mode: str) -> tuple[str, str, str]:
    if marketplace == MarketplaceName.ebay.value:
        return (
            "direct_api",
            PUBLISH_SUPPORT_LABELS["direct_api"],
            "PosterPro can publish directly to eBay through the native API path for connected operator accounts.",
        )

    normalized_mode = publish_mode if publish_mode in {"browser_assist", "provider_assist", "draft_only", "manual_review"} else "manual_review"
    if normalized_mode == "browser_assist":
        return (
            "browser_assist",
            PUBLISH_SUPPORT_LABELS["browser_assist"],
            "PosterPro prepares and runs an assisted browser workflow for this marketplace. Final submission may still require operator review depending on the bridge policy and live marketplace flow.",
        )
    if normalized_mode == "provider_assist":
        return (
            "provider_assist",
            PUBLISH_SUPPORT_LABELS["provider_assist"],
            "PosterPro prepares a provider-assisted packet for this marketplace instead of a native direct publish call.",
        )
    if normalized_mode == "draft_only":
        return (
            "draft_only",
            PUBLISH_SUPPORT_LABELS["draft_only"],
            "PosterPro can prepare a draft or assisted handoff, but the operator should expect to complete the final marketplace submission manually.",
        )
    return (
        "manual_review",
        PUBLISH_SUPPORT_LABELS["manual_review"],
        "PosterPro stores workflow state and operator guidance for this marketplace, but listing completion still depends on manual review or assisted handoff.",
    )


def _import_support_contract(*, marketplace: str, import_mode: str) -> tuple[str, str, str]:
    if marketplace == MarketplaceName.ebay.value:
        return (
            "direct_api",
            IMPORT_SUPPORT_LABELS["direct_api"],
            "PosterPro can import supported eBay listings through the connected operator account when the saved credentials are still usable.",
        )

    normalized_mode = import_mode if import_mode in {"browser_assist", "provider_assist", "csv_assist", "manual"} else "manual"
    if normalized_mode == "browser_assist":
        return (
            "browser_assist",
            IMPORT_SUPPORT_LABELS["browser_assist"],
            "PosterPro relies on an authenticated bridge browser session to pull listing data from this marketplace.",
        )
    if normalized_mode == "provider_assist":
        return (
            "provider_assist",
            IMPORT_SUPPORT_LABELS["provider_assist"],
            "PosterPro expects a provider-assisted or externally prepared import path for this marketplace rather than a native direct import.",
        )
    if normalized_mode == "csv_assist":
        return (
            "csv_assist",
            IMPORT_SUPPORT_LABELS["csv_assist"],
            "PosterPro expects operator-prepared CSV or catalog data for this marketplace instead of a native direct import.",
        )
    return (
        "manual",
        IMPORT_SUPPORT_LABELS["manual"],
        "PosterPro does not provide a native automated import path for this marketplace in the current deployment.",
    )


def _sales_sync_support_contract(*, marketplace: str) -> tuple[str, str, str]:
    if marketplace == MarketplaceName.ebay.value:
        return (
            "direct_api",
            SALES_SYNC_SUPPORT_LABELS["direct_api"],
            "PosterPro can poll eBay sold-order activity for connected operator accounts.",
        )
    return (
        "unsupported",
        SALES_SYNC_SUPPORT_LABELS["unsupported"],
        "PosterPro does not currently provide real sold-order detection for this marketplace in this deployment.",
    )


def load_manual_marketplace_settings(user: User | None) -> dict[str, dict[str, Any]]:
    settings_json = user.settings_json or {}
    raw = settings_json.get("marketplace_connections")
    if not isinstance(raw, Mapping):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        name = str(key or "").strip().lower()
        if name not in MarketplaceName._value2member_map_ or not isinstance(value, Mapping):
            continue
        profile = MARKETPLACE_SETUP_PROFILES.get(name, {})
        normalized[name] = {
            "display_name": str(value.get("display_name") or "").strip(),
            "account_handle": str(value.get("account_handle") or "").strip(),
            "notes": str(value.get("notes") or "").strip(),
            "workflow_state": str(value.get("workflow_state") or "").strip().lower() or "draft",
            "import_mode": str(value.get("import_mode") or "").strip().lower() or str(profile.get("default_import_mode") or "manual"),
            "publish_mode": str(value.get("publish_mode") or "").strip().lower() or str(profile.get("default_publish_mode") or "manual_review"),
            "shipping_scope": str(value.get("shipping_scope") or "").strip().lower() or str(profile.get("default_shipping_scope") or "local_only"),
            "renewal_mode": str(value.get("renewal_mode") or "").strip().lower() or "manual",
            "support_url": str(value.get("support_url") or "").strip(),
            "bridge_account_key": str(value.get("bridge_account_key") or "").strip().lower(),
            "import_listing_limit": _normalize_import_listing_limit(value.get("import_listing_limit")),
        }
    return normalized


def save_manual_marketplace_settings(user: User, marketplace: str, payload: Mapping[str, Any]) -> None:
    name = marketplace.strip().lower()
    profile = MARKETPLACE_SETUP_PROFILES.get(name, {})
    settings_json = dict(user.settings_json or {})
    manual_settings = dict(load_manual_marketplace_settings(user))
    display_name = str(payload.get("display_name") or "").strip()
    account_handle = str(payload.get("account_handle") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    workflow_state = str(payload.get("workflow_state") or "").strip().lower() or "draft"
    import_mode = str(payload.get("import_mode") or "").strip().lower() or str(profile.get("default_import_mode") or "manual")
    publish_mode = str(payload.get("publish_mode") or "").strip().lower() or str(profile.get("default_publish_mode") or "manual_review")
    shipping_scope = str(payload.get("shipping_scope") or "").strip().lower() or str(profile.get("default_shipping_scope") or "local_only")
    renewal_mode = str(payload.get("renewal_mode") or "").strip().lower() or "manual"
    support_url = str(payload.get("support_url") or "").strip()
    bridge_account_key = str(payload.get("bridge_account_key") or "").strip().lower()
    import_listing_limit = _normalize_import_listing_limit(payload.get("import_listing_limit"))
    if workflow_state not in {"draft", MANUAL_WORKFLOW_READY}:
        workflow_state = "draft"
    if import_mode not in {"manual", "csv_assist", "provider_assist", "browser_assist"}:
        import_mode = "manual"
    if publish_mode not in {"manual_review", "draft_only", "provider_assist", "browser_assist"}:
        publish_mode = "manual_review"
    if shipping_scope not in {"local_only", "shipping_only", "local_and_shipping"}:
        shipping_scope = "local_only"
    if renewal_mode not in {"manual", "daily", "scheduled"}:
        renewal_mode = "manual"

    default_import_mode = str(profile.get("default_import_mode") or "manual")
    default_publish_mode = str(profile.get("default_publish_mode") or "manual_review")
    default_shipping_scope = str(profile.get("default_shipping_scope") or "local_only")
    has_saved_state = any(
        [
            display_name,
            account_handle,
            notes,
            support_url,
            bridge_account_key,
            workflow_state == MANUAL_WORKFLOW_READY,
            import_mode != default_import_mode,
            publish_mode != default_publish_mode,
            shipping_scope != default_shipping_scope,
            renewal_mode != "manual",
            import_listing_limit != 10,
        ]
    )

    if has_saved_state:
        manual_settings[name] = {
            "display_name": display_name,
            "account_handle": account_handle,
            "notes": notes,
            "workflow_state": workflow_state,
            "import_mode": import_mode,
            "publish_mode": publish_mode,
            "shipping_scope": shipping_scope,
            "renewal_mode": renewal_mode,
            "support_url": support_url,
            "bridge_account_key": bridge_account_key,
            "import_listing_limit": import_listing_limit,
        }
    else:
        manual_settings.pop(name, None)

    settings_json["marketplace_connections"] = manual_settings
    user.settings_json = settings_json


def marketplace_status_snapshot(
    *,
    marketplace: str,
    account: MarketplaceAccount | None,
    user: User,
) -> dict[str, Any]:
    name = marketplace.strip().lower()
    profile = MARKETPLACE_SETUP_PROFILES.get(name, {})
    manual_settings = load_manual_marketplace_settings(user).get(name, {})
    display_name = str(manual_settings.get("display_name") or "").strip()
    account_handle = str(manual_settings.get("account_handle") or "").strip()
    notes = str(manual_settings.get("notes") or "").strip()
    workflow_state = str(manual_settings.get("workflow_state") or "").strip().lower() or "draft"
    import_mode = str(manual_settings.get("import_mode") or "").strip().lower() or str(profile.get("default_import_mode") or "manual")
    publish_mode = str(manual_settings.get("publish_mode") or "").strip().lower() or str(profile.get("default_publish_mode") or "manual_review")
    shipping_scope = str(manual_settings.get("shipping_scope") or "").strip().lower() or str(profile.get("default_shipping_scope") or "local_only")
    renewal_mode = str(manual_settings.get("renewal_mode") or "").strip().lower() or "manual"
    support_url = str(manual_settings.get("support_url") or "").strip()
    bridge_account_key = str(manual_settings.get("bridge_account_key") or "").strip().lower()
    import_listing_limit = _normalize_import_listing_limit(manual_settings.get("import_listing_limit"))
    ui_priority = MARKETPLACE_UI_PRIORITY.get(name, 99)

    if name == MarketplaceName.ebay.value:
        oauth_ready = bool(settings.ebay_client_id and settings.ebay_client_secret and (settings.ebay_runame or settings.ebay_redirect_uri))
        health = summarize_ebay_account_health(account)
        connected = health["connected"]
        publish_support_level, publish_support_label, publish_support_note = _publish_support_contract(marketplace=name, publish_mode="direct_api")
        import_support_level, import_support_label, import_support_note = _import_support_contract(marketplace=name, import_mode="direct_api")
        sales_sync_support_level, sales_sync_support_label, sales_sync_support_note = _sales_sync_support_contract(marketplace=name)
        return {
            "marketplace": name,
            "supports_oauth": True,
            "connection_mode": "oauth",
            "connected": connected,
            "available": oauth_ready,
            "enabled_for_publishing": False,
            "enabled_for_sale_detection": False,
            "external_account_id": account.external_account_id if account else None,
            "token_expires_at": account.token_expires_at if account else None,
            "has_refresh_token": health["has_refresh_token"],
            "token_status": health["token_status"],
            "import_ready": oauth_ready and health["import_ready"],
            "reconnect_required": health["reconnect_required"],
            "publish_support_level": publish_support_level,
            "publish_support_label": publish_support_label,
            "publish_support_note": publish_support_note,
            "import_support_level": import_support_level,
            "import_support_label": import_support_label,
            "import_support_note": import_support_note,
            "sales_sync_support_level": sales_sync_support_level,
            "sales_sync_support_label": sales_sync_support_label,
            "sales_sync_support_note": sales_sync_support_note,
            "status_note": "Server eBay OAuth credentials are missing."
            if not oauth_ready
            else "OAuth app is ready. Connect the current operator account."
            if not connected
            else health["status_note"],
            "display_name": display_name or "eBay account",
            "account_handle": account_handle,
            "notes": notes,
            "workflow_state": "ready" if connected else "draft",
            "can_publish": connected,
            "can_sync_sales": connected,
            "ui_priority": ui_priority,
            "ui_state_tone": "success" if connected and health["import_ready"] else "warning" if connected else "default",
            "ui_primary_action": "Import listings" if connected and health["import_ready"] else "Reconnect eBay" if connected else "Connect eBay",
            "ui_secondary_actions": ["Review credentials", "Open Settings"],
        }

    is_manual = name in MANUAL_MARKETPLACES
    has_profile = bool(display_name or account_handle or bridge_account_key)
    connected = is_manual and workflow_state == MANUAL_WORKFLOW_READY and has_profile
    publish_support_level, publish_support_label, publish_support_note = _publish_support_contract(marketplace=name, publish_mode=publish_mode)
    import_support_level, import_support_label, import_support_note = _import_support_contract(marketplace=name, import_mode=import_mode)
    sales_sync_support_level, sales_sync_support_label, sales_sync_support_note = _sales_sync_support_contract(marketplace=name)
    return {
        "marketplace": name,
        "supports_oauth": False,
        "connection_mode": "manual",
        "connected": connected,
        "available": is_manual,
        "enabled_for_publishing": False,
        "enabled_for_sale_detection": False,
        "external_account_id": account_handle or display_name or None,
        "token_expires_at": None,
        "publish_support_level": publish_support_level,
        "publish_support_label": publish_support_label,
        "publish_support_note": publish_support_note,
        "import_support_level": import_support_level,
        "import_support_label": import_support_label,
        "import_support_note": import_support_note,
        "sales_sync_support_level": sales_sync_support_level,
        "sales_sync_support_label": sales_sync_support_label,
        "sales_sync_support_note": sales_sync_support_note,
        "status_note": str(profile.get("ready_note") or "Manual operator workflow is saved for this marketplace.")
        if connected
        else str(profile.get("saved_note") or "Setup details are saved, but this marketplace is not marked ready yet.")
        if has_profile
        else str(profile.get("draft_note") or "Add shop details and workflow notes to make this channel usable for this account."),
        "display_name": display_name,
        "account_handle": account_handle,
        "notes": notes,
        "workflow_state": workflow_state,
        "import_mode": import_mode,
        "publish_mode": publish_mode,
        "shipping_scope": shipping_scope,
        "renewal_mode": renewal_mode,
        "support_url": support_url,
        "bridge_account_key": bridge_account_key,
        "import_listing_limit": import_listing_limit,
        "can_publish": connected,
        "can_sync_sales": False,
        "ui_priority": ui_priority,
        "ui_state_tone": "success" if connected else "warning" if has_profile else "default",
        "ui_primary_action": "Run assisted workflow" if connected else "Mark ready" if has_profile else "Complete setup",
        "ui_secondary_actions": ["Open setup drawer", "Review support contract"],
    }
