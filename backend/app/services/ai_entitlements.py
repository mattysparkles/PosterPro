"""Central, server-side AI mode and sponsored-usage decisions.

Paid billing is not active in this deployment. A tenant cannot grant itself a
sponsored entitlement through a request payload; activation requires a stored
server-side active subscription and an explicit feature grant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core import config as config_module
from app.core.secrets import decrypt_secret_if_needed
from app.models.models import User


def _user_settings(user: User | None) -> dict[str, Any]:
    value = getattr(user, "settings_json", None) if user else None
    return value if isinstance(value, dict) else {}


def ai_provider_config(user: User | None) -> dict[str, Any]:
    value = _user_settings(user).get("ai_provider")
    return value if isinstance(value, dict) else {}


def sponsored_ai_entitlement(user: User | None, *, now: datetime | None = None) -> dict[str, Any]:
    """Return effective sponsored-AI entitlement; false unless billing grants it."""
    root = _user_settings(user)
    subscription = root.get("subscription") if isinstance(root.get("subscription"), dict) else {}
    entitlements = subscription.get("entitlements") if isinstance(subscription.get("entitlements"), dict) else {}
    active = str(subscription.get("status") or "").strip().lower() == "active"
    # The fail-closed meter-ready gate prevents a configuration mistake from
    # turning on unmetered platform spend before monthly tenant accounting is
    # implemented and enabled.
    runtime_settings = config_module.settings
    allowed = bool(runtime_settings.sponsored_ai_enabled and runtime_settings.sponsored_ai_metering_ready and active and entitlements.get("sponsored_ai") is True)
    allowance = max(0, int(entitlements.get("ai_monthly_tokens") or 0)) if allowed else 0
    period = str(subscription.get("period_start") or "")
    usage = root.get("ai_usage_period") if isinstance(root.get("ai_usage_period"), dict) else {}
    used = max(0, int(usage.get("tokens") or 0)) if usage.get("period_start") == period else 0
    remaining = max(0, allowance - used)
    return {
        "entitled": allowed and remaining > 0,
        "subscription_active": active,
        "allowance_tokens": allowance,
        "used_tokens": used,
        "remaining_tokens": remaining,
        "period_start": period or None,
        "reason": "available" if allowed and remaining > 0 else "billing_not_active_meter_not_ready_or_entitlement_missing",
    }


def resolve_openai_key(db, user_id: int | None) -> tuple[str | None, str]:
    """Return a key for the requested tenant and its effective provider mode."""
    if db is None or user_id is None:
        return config_module.settings.openai_api_key, "platform_system"
    user = db.get(User, user_id)
    if not user:
        return None, "disabled"
    config = ai_provider_config(user)
    mode = str(config.get("mode") or "").strip().upper()
    if mode == "BYO_OPENAI":
        encoded = config.get("openai_api_key_enc")
        key = decrypt_secret_if_needed(encoded, secret_key=config_module.settings.session_secret) if isinstance(encoded, str) else None
        return (key, "byo_openai") if key else (None, "disabled")
    if mode == "POSTERPRO_SPONSORED":
        entitlement = sponsored_ai_entitlement(user)
        return (config_module.settings.openai_api_key, "posterpro_sponsored") if entitlement["entitled"] else (None, "disabled")
    # Preserve the platform operator's existing admin tooling. Normal tenants
    # must explicitly choose BYO or receive a real server-side entitlement.
    if user.is_admin and not bool(getattr(user, "_posterpro_view_as_regular", False)):
        return config_module.settings.openai_api_key, "platform_admin"
    return None, "disabled"


def public_ai_setup_state(user: User | None) -> dict[str, Any]:
    config = ai_provider_config(user)
    mode = str(config.get("mode") or "").strip().upper()
    entitlement = sponsored_ai_entitlement(user)
    if mode == "BYO_OPENAI" and config.get("verified_at") and config.get("last_verification") != "failed":
        state = "CONNECTED"
    elif mode == "POSTERPRO_SPONSORED" and entitlement["entitled"]:
        state = "CONNECTED"
    elif mode == "POSTERPRO_SPONSORED":
        state = "COMING_SOON"
    elif user and user.is_admin and config_module.settings.openai_api_key:
        mode = "PLATFORM_DEFAULT"
        state = "CONNECTED"
    else:
        state = "NOT_CONFIGURED"
    return {
        "mode": mode or None,
        "state": state,
        "byo_configured": bool(config.get("openai_api_key_enc")),
        "verified_at": config.get("verified_at"),
        "model": str(config.get("verified_model") or "gpt-4o-mini") if config.get("verified_at") else None,
        "sponsored": {
            "available": bool(entitlement["entitled"]),
            "coming_soon": not bool(entitlement["entitled"]),
            "allowance_tokens": entitlement["allowance_tokens"],
            "used_tokens": entitlement["used_tokens"],
            "remaining_tokens": entitlement["remaining_tokens"],
        },
    }
