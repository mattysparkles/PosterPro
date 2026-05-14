from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import settings
from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, User

MANUAL_MARKETPLACES = {
    MarketplaceName.facebook.value,
    MarketplaceName.mercari.value,
    MarketplaceName.poshmark.value,
    MarketplaceName.depop.value,
    MarketplaceName.whatnot.value,
    MarketplaceName.vinted.value,
}

MANUAL_WORKFLOW_READY = "ready"


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
        normalized[name] = {
            "display_name": str(value.get("display_name") or "").strip(),
            "account_handle": str(value.get("account_handle") or "").strip(),
            "notes": str(value.get("notes") or "").strip(),
            "workflow_state": str(value.get("workflow_state") or "").strip().lower() or "draft",
        }
    return normalized


def save_manual_marketplace_settings(user: User, marketplace: str, payload: Mapping[str, Any]) -> None:
    name = marketplace.strip().lower()
    settings_json = dict(user.settings_json or {})
    manual_settings = dict(load_manual_marketplace_settings(user))
    display_name = str(payload.get("display_name") or "").strip()
    account_handle = str(payload.get("account_handle") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    workflow_state = str(payload.get("workflow_state") or "").strip().lower() or "draft"
    if workflow_state not in {"draft", MANUAL_WORKFLOW_READY}:
        workflow_state = "draft"

    if display_name or account_handle or notes or workflow_state == MANUAL_WORKFLOW_READY:
        manual_settings[name] = {
            "display_name": display_name,
            "account_handle": account_handle,
            "notes": notes,
            "workflow_state": workflow_state,
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
    manual_settings = load_manual_marketplace_settings(user).get(name, {})
    display_name = str(manual_settings.get("display_name") or "").strip()
    account_handle = str(manual_settings.get("account_handle") or "").strip()
    notes = str(manual_settings.get("notes") or "").strip()
    workflow_state = str(manual_settings.get("workflow_state") or "").strip().lower() or "draft"

    if name == MarketplaceName.ebay.value:
        oauth_ready = bool(settings.ebay_client_id and settings.ebay_client_secret and settings.ebay_redirect_uri)
        connected = account is not None and bool(account.access_token)
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
            "status_note": "OAuth app is ready. Connect the current operator account."
            if oauth_ready and not connected
            else "eBay is connected for this operator."
            if connected
            else "Server eBay OAuth credentials are missing.",
            "display_name": display_name or "eBay account",
            "account_handle": account_handle,
            "notes": notes,
            "workflow_state": "ready" if connected else "draft",
            "can_publish": connected,
            "can_sync_sales": connected,
        }

    is_manual = name in MANUAL_MARKETPLACES
    has_profile = bool(display_name or account_handle)
    connected = is_manual and workflow_state == MANUAL_WORKFLOW_READY and has_profile
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
        "status_note": "Manual operator workflow is saved for this marketplace."
        if connected
        else "Setup details are saved, but this marketplace is not marked ready yet."
        if has_profile
        else "Add shop details and workflow notes to make this channel usable for this account.",
        "display_name": display_name,
        "account_handle": account_handle,
        "notes": notes,
        "workflow_state": workflow_state,
        "can_publish": connected,
        "can_sync_sales": False,
    }
