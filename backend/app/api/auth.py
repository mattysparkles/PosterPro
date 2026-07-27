from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AuthChangePasswordRequest,
    AuthForgotPasswordRequest,
    AuthLoginRequest,
    AuthPasswordResetRequest,
    AuthSessionResponse,
    AuthRegisterRequest,
    AuthViewModeRequest,
    HostedPagesPublishRequest,
    HostedPagesThemeImportRequest,
    HostedPagesUpdateRequest,
    ServerSettingsUpdateRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.core.auth import (
    clear_session_cookie,
    get_user_role,
    get_current_user,
    hash_password,
    hash_password_reset_token,
    is_effective_admin,
    is_viewing_as_regular,
    new_password_reset_token,
    normalize_email,
    set_session_cookie,
    user_has_vine_access,
    verify_password,
)
from app.core.config import reload_settings, settings
from app.core.database import get_db
from app.core.secrets import encrypt_secret, mask_secret
from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, User
from app.services.email_service import EmailDeliveryError, send_password_reset_email, smtp_configured
from app.services.ebay_service import summarize_ebay_account_health
from app.services.site_content_service import (
    build_public_page_urls,
    import_theme_pack,
    load_site_content,
    publish_draft_pages,
    save_draft_pages,
    save_site_content,
)
from app.services.automation_bridge import bridge_browser_submit_policy

router = APIRouter(prefix="/auth", tags=["auth"])

_BACKEND_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_STRING_SETTING_FIELDS = {
    "app_base_url": "APP_BASE_URL",
    "ebay_client_id": "EBAY_CLIENT_ID",
    "ebay_runame": "EBAY_RUNAME",
    "ebay_redirect_uri": "EBAY_REDIRECT_URI",
    "storage_root": "STORAGE_ROOT",
    "environment": "ENVIRONMENT",
    "amazon_marketplace_region": "AMAZON_MARKETPLACE_REGION",
    "amazon_media_fetch_mode": "AMAZON_MEDIA_FETCH_MODE",
    "smtp_host": "SMTP_HOST",
    "smtp_username": "SMTP_USERNAME",
    "smtp_from_email": "SMTP_FROM_EMAIL",
    "smtp_from_name": "SMTP_FROM_NAME",
    "automation_bridge_url": "AUTOMATION_BRIDGE_URL",
}
_BOOL_SETTING_FIELDS = {
    "autonomous_dry_run": "AUTONOMOUS_DRY_RUN",
    "autonomous_crosspost_enabled": "AUTONOMOUS_CROSSPOST_ENABLED",
    "automation_bridge_enabled": "AUTOMATION_BRIDGE_ENABLED",
    "sale_detection_enabled": "SALE_DETECTION_ENABLED",
    "sale_detection_dry_run": "SALE_DETECTION_DRY_RUN",
    "amazon_vine_import_enabled": "AMAZON_VINE_IMPORT_ENABLED",
    "amazon_vine_import_premium_only": "AMAZON_VINE_IMPORT_PREMIUM_ONLY",
    "amazon_media_lookup_enabled": "AMAZON_MEDIA_LOOKUP_ENABLED",
    "amazon_media_page_fallback_enabled": "AMAZON_MEDIA_PAGE_FALLBACK_ENABLED",
    "smtp_use_tls": "SMTP_USE_TLS",
}
_INT_SETTING_FIELDS = {
    "automation_bridge_timeout_seconds": "AUTOMATION_BRIDGE_TIMEOUT_SECONDS",
    "sale_detection_poll_minutes": "SALE_DETECTION_POLL_MINUTES",
    "amazon_media_rate_limit_per_minute": "AMAZON_MEDIA_RATE_LIMIT_PER_MINUTE",
    "smtp_port": "SMTP_PORT",
}
_SECRET_SETTING_FIELDS = {
    "openai_api_key": "OPENAI_API_KEY_ENC",
    "photoroom_api_key": "PHOTOROOM_API_KEY_ENC",
    "automation_bridge_api_key": "AUTOMATION_BRIDGE_API_KEY_ENC",
    "ebay_client_secret": "EBAY_CLIENT_SECRET_ENC",
    "amazon_paapi_access_key": "AMAZON_PAAPI_ACCESS_KEY_ENC",
    "amazon_paapi_secret_key": "AMAZON_PAAPI_SECRET_KEY_ENC",
    "amazon_paapi_partner_tag": "AMAZON_PAAPI_PARTNER_TAG_ENC",
    "smtp_password": "SMTP_PASSWORD_ENC",
}
_SECRET_PLAIN_ENV_FIELDS = {
    "openai_api_key": "OPENAI_API_KEY",
    "photoroom_api_key": "PHOTOROOM_API_KEY",
    "automation_bridge_api_key": "AUTOMATION_BRIDGE_API_KEY",
    "ebay_client_secret": "EBAY_CLIENT_SECRET",
    "smtp_password": "SMTP_PASSWORD",
}

_DEFAULT_WORKFLOW_PREFERENCES = {
    "review_before_publish": True,
    "auto_publish_after_approval": False,
    "bulk_approval_enabled": True,
    "listing_preview_mode": "marketplace",
    "default_preview_marketplace": "ebay",
}
_DEFAULT_VINE_PREFERENCES = {
    "enforce_six_month_lock": True,
}
_DEFAULT_SOLD_SYNC_PREFERENCES = {
    "sold_out_delist_everywhere": True,
    "out_of_stock_delist_everywhere": False,
    "remove_media_on_sold_out": False,
}
_DEFAULT_EBAY_POLICY_SETTINGS = {
    "fulfillment_policy_id": "",
    "fulfillment_policy_name": "",
    "payment_policy_id": "",
    "payment_policy_name": "",
    "return_policy_id": "",
    "return_policy_name": "",
    "merchant_location_key": "",
    "merchant_location_verified": False,
    "merchant_location_status": "unverified",
    "merchant_location_last_checked_at": "",
    "merchant_location_error": "",
    "merchant_location_location_name": "PosterPro Default Location",
    "merchant_location_postal_code": "95125",
    "merchant_location_country": "US",
    "merchant_location_city": "San Jose",
    "merchant_location_state_or_province": "CA",
    "merchant_location_phone": "",
    "shipping_service_code": "USPSGroundAdvantage",
    "handling_time_days": 1,
    "local_pickup_allowed": False,
    "calculated_shipping": False,
    "package_weight_required": True,
    "package_dimensions_required": True,
    "marketplace_id": "EBAY_US",
    "last_policy_sync_at": "",
    "policy_sync_status": "uninitialized",
    "policy_sync_error": "",
    "policy_candidates": {},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _password_reset_payload(user: User) -> dict:
    settings_json = user.settings_json or {}
    payload = settings_json.get("password_reset")
    return payload if isinstance(payload, dict) else {}


def _persist_password_reset(user: User, *, token_hash: str, expires_at: datetime) -> None:
    settings_json = dict(user.settings_json or {})
    settings_json["password_reset"] = {
        "token_hash": token_hash,
        "expires_at": expires_at.isoformat(),
    }
    user.settings_json = settings_json


def _clear_password_reset(user: User) -> None:
    settings_json = dict(user.settings_json or {})
    settings_json.pop("password_reset", None)
    user.settings_json = settings_json


def _workflow_preferences(user: User | None) -> dict:
    if not user:
        return dict(_DEFAULT_WORKFLOW_PREFERENCES)
    settings_json = user.settings_json or {}
    raw = settings_json.get("workflow_preferences")
    stored = raw if isinstance(raw, dict) else {}
    return {
        "review_before_publish": bool(stored.get("review_before_publish", _DEFAULT_WORKFLOW_PREFERENCES["review_before_publish"])),
        "auto_publish_after_approval": bool(stored.get("auto_publish_after_approval", _DEFAULT_WORKFLOW_PREFERENCES["auto_publish_after_approval"])),
        "bulk_approval_enabled": bool(stored.get("bulk_approval_enabled", _DEFAULT_WORKFLOW_PREFERENCES["bulk_approval_enabled"])),
        "listing_preview_mode": str(stored.get("listing_preview_mode") or _DEFAULT_WORKFLOW_PREFERENCES["listing_preview_mode"]),
        "default_preview_marketplace": str(stored.get("default_preview_marketplace") or _DEFAULT_WORKFLOW_PREFERENCES["default_preview_marketplace"]),
    }


def _persist_workflow_preferences(user: User, updates: dict) -> None:
    settings_json = dict(user.settings_json or {})
    current = _workflow_preferences(user)
    current.update(updates)
    settings_json["workflow_preferences"] = current
    user.settings_json = settings_json


def _vine_preferences(user: User | None) -> dict:
    if not user:
        return dict(_DEFAULT_VINE_PREFERENCES)
    settings_json = user.settings_json or {}
    raw = settings_json.get("vine_preferences")
    stored = raw if isinstance(raw, dict) else {}
    return {
        "enforce_six_month_lock": bool(stored.get("enforce_six_month_lock", _DEFAULT_VINE_PREFERENCES["enforce_six_month_lock"])),
    }


def _persist_vine_preferences(user: User, updates: dict) -> None:
    settings_json = dict(user.settings_json or {})
    current = _vine_preferences(user)
    current.update(updates)
    settings_json["vine_preferences"] = current
    user.settings_json = settings_json


def _sold_sync_preferences(user: User | None) -> dict:
    if not user:
        return dict(_DEFAULT_SOLD_SYNC_PREFERENCES)
    settings_json = user.settings_json or {}
    raw = settings_json.get("sold_sync_preferences")
    stored = raw if isinstance(raw, dict) else {}
    return {
        "sold_out_delist_everywhere": bool(stored.get("sold_out_delist_everywhere", _DEFAULT_SOLD_SYNC_PREFERENCES["sold_out_delist_everywhere"])),
        "out_of_stock_delist_everywhere": bool(stored.get("out_of_stock_delist_everywhere", _DEFAULT_SOLD_SYNC_PREFERENCES["out_of_stock_delist_everywhere"])),
        "remove_media_on_sold_out": bool(stored.get("remove_media_on_sold_out", _DEFAULT_SOLD_SYNC_PREFERENCES["remove_media_on_sold_out"])),
    }


def _ebay_marketplace_policy_settings(user: User | None) -> dict:
    if not user:
        return dict(_DEFAULT_EBAY_POLICY_SETTINGS)
    settings_json = user.settings_json or {}
    raw = settings_json.get("ebay_marketplace_policy_settings")
    stored = raw if isinstance(raw, dict) else {}
    return {
        "fulfillment_policy_id": str(stored.get("fulfillment_policy_id") or "").strip(),
        "fulfillment_policy_name": str(stored.get("fulfillment_policy_name") or "").strip(),
        "payment_policy_id": str(stored.get("payment_policy_id") or "").strip(),
        "payment_policy_name": str(stored.get("payment_policy_name") or "").strip(),
        "return_policy_id": str(stored.get("return_policy_id") or "").strip(),
        "return_policy_name": str(stored.get("return_policy_name") or "").strip(),
        "merchant_location_key": str(stored.get("merchant_location_key") or "").strip(),
        "merchant_location_verified": bool(stored.get("merchant_location_verified")),
        "merchant_location_status": str(stored.get("merchant_location_status") or "unverified").strip(),
        "merchant_location_last_checked_at": str(stored.get("merchant_location_last_checked_at") or "").strip(),
        "merchant_location_error": str(stored.get("merchant_location_error") or "").strip(),
        "merchant_location_location_name": str(stored.get("merchant_location_location_name") or "PosterPro Default Location").strip(),
        "merchant_location_postal_code": str(stored.get("merchant_location_postal_code") or "95125").strip(),
        "merchant_location_country": str(stored.get("merchant_location_country") or "US").strip(),
        "merchant_location_city": str(stored.get("merchant_location_city") or "San Jose").strip(),
        "merchant_location_state_or_province": str(stored.get("merchant_location_state_or_province") or "CA").strip(),
        "merchant_location_phone": str(stored.get("merchant_location_phone") or "").strip(),
        "shipping_service_code": str(stored.get("shipping_service_code") or _DEFAULT_EBAY_POLICY_SETTINGS["shipping_service_code"]).strip(),
        "handling_time_days": int(stored.get("handling_time_days") or _DEFAULT_EBAY_POLICY_SETTINGS["handling_time_days"]),
        "local_pickup_allowed": bool(stored.get("local_pickup_allowed", _DEFAULT_EBAY_POLICY_SETTINGS["local_pickup_allowed"])),
        "calculated_shipping": bool(stored.get("calculated_shipping", _DEFAULT_EBAY_POLICY_SETTINGS["calculated_shipping"])),
        "package_weight_required": bool(stored.get("package_weight_required", _DEFAULT_EBAY_POLICY_SETTINGS["package_weight_required"])),
        "package_dimensions_required": bool(stored.get("package_dimensions_required", _DEFAULT_EBAY_POLICY_SETTINGS["package_dimensions_required"])),
        "marketplace_id": str(stored.get("marketplace_id") or "EBAY_US").strip() or "EBAY_US",
        "last_policy_sync_at": str(stored.get("last_policy_sync_at") or "").strip(),
        "policy_sync_status": str(stored.get("policy_sync_status") or "uninitialized").strip(),
        "policy_sync_error": str(stored.get("policy_sync_error") or "").strip(),
        "policy_candidates": stored.get("policy_candidates") if isinstance(stored.get("policy_candidates"), dict) else {},
    }


def _persist_ebay_marketplace_policy_settings(user: User, updates: dict) -> None:
    settings_json = dict(user.settings_json or {})
    current = _ebay_marketplace_policy_settings(user)
    current.update({key: value for key, value in updates.items() if value is not None})
    current["handling_time_days"] = max(1, int(current.get("handling_time_days") or 1))
    settings_json["ebay_marketplace_policy_settings"] = current
    user.settings_json = settings_json


def _persist_sold_sync_preferences(user: User, updates: dict) -> None:
    settings_json = dict(user.settings_json or {})
    current = _sold_sync_preferences(user)
    current.update(updates)
    settings_json["sold_sync_preferences"] = current
    user.settings_json = settings_json


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "is_admin": user.is_admin,
        "effective_is_admin": is_effective_admin(user),
        "view_as_regular": is_viewing_as_regular(user),
        "role": get_user_role(user),
        "can_access_vine_import": settings.amazon_vine_import_enabled and user_has_vine_access(user),
        "workflow_preferences": _workflow_preferences(user),
        "vine_enforce_six_month_lock": bool(_vine_preferences(user).get("enforce_six_month_lock", True)),
        "sold_sync_preferences": _sold_sync_preferences(user),
        "ebay_marketplace_policy_settings": _ebay_marketplace_policy_settings(user),
    }


def _write_env_overrides(updates: dict[str, str | None]) -> None:
    existing_lines = _BACKEND_ENV_PATH.read_text(encoding="utf-8").splitlines() if _BACKEND_ENV_PATH.exists() else []
    rendered_keys: set[str] = set()
    next_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            next_lines.append(line)
            continue
        key, _ = line.split("=", maxsplit=1)
        if key in updates:
            rendered_keys.add(key)
            value = updates[key]
            if value is not None:
                next_lines.append(f"{key}={value}")
        else:
            next_lines.append(line)

    for key, value in updates.items():
        if key not in rendered_keys and value is not None:
            next_lines.append(f"{key}={value}")

    _BACKEND_ENV_PATH.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def _build_settings_panel_response(current_user: User, *, ebay_account: MarketplaceAccount | None) -> dict:
    runtime_settings = reload_settings()
    site_content = load_site_content()
    page_urls = build_public_page_urls(runtime_settings.app_base_url)
    runame = runtime_settings.ebay_runame or runtime_settings.ebay_redirect_uri or ""
    ebay_health = summarize_ebay_account_health(ebay_account)
    bridge_submit_policy = bridge_browser_submit_policy()
    return {
        "profile": {
            "full_name": current_user.full_name,
            "email": current_user.email,
            "is_admin": current_user.is_admin,
            "effective_is_admin": is_effective_admin(current_user),
            "view_as_regular": is_viewing_as_regular(current_user),
            "role": get_user_role(current_user),
            "can_access_vine_import": settings.amazon_vine_import_enabled and user_has_vine_access(current_user),
        },
        "workflow": _workflow_preferences(current_user),
        "sold_sync_preferences": _sold_sync_preferences(current_user),
        "ebay_marketplace_policy_settings": _ebay_marketplace_policy_settings(current_user),
        "ebay": {
            "client_id_configured": bool(runtime_settings.ebay_client_id),
            "client_secret_configured": bool(runtime_settings.ebay_client_secret),
            "runame": runame,
            "redirect_uri": runtime_settings.ebay_redirect_uri or "",
            "oauth_ready": bool(runtime_settings.ebay_client_id and runtime_settings.ebay_client_secret and runame),
            "connected": ebay_health["connected"],
            "external_account_id": ebay_account.external_account_id if ebay_account else None,
            "token_expires_at": ebay_account.token_expires_at.isoformat() if ebay_account and ebay_account.token_expires_at else None,
            "has_refresh_token": ebay_health["has_refresh_token"],
            "token_status": ebay_health["token_status"],
            "import_ready": ebay_health["import_ready"],
            "reconnect_required": ebay_health["reconnect_required"],
            "status_note": ebay_health["status_note"],
            "policy_sync_status": _ebay_marketplace_policy_settings(current_user).get("policy_sync_status"),
            "policy_sync_error": _ebay_marketplace_policy_settings(current_user).get("policy_sync_error"),
            "merchant_location_verified": _ebay_marketplace_policy_settings(current_user).get("merchant_location_verified"),
            "merchant_location_status": _ebay_marketplace_policy_settings(current_user).get("merchant_location_status"),
            "merchant_location_last_checked_at": _ebay_marketplace_policy_settings(current_user).get("merchant_location_last_checked_at"),
            "merchant_location_error": _ebay_marketplace_policy_settings(current_user).get("merchant_location_error"),
            "privacy_policy_url": page_urls["privacy_policy_url"],
            "auth_accepted_url": page_urls["auth_accepted_url"],
            "auth_declined_url": page_urls["auth_declined_url"],
            "policy_settings": _ebay_marketplace_policy_settings(current_user),
        },
        "api_keys": {
            "openai_configured": bool(runtime_settings.openai_api_key),
            "photoroom_configured": bool(runtime_settings.photoroom_api_key),
        },
        "automation": {
            "autonomous_mode": runtime_settings.autonomous_mode,
            "autonomous_dry_run": runtime_settings.autonomous_dry_run,
            "autonomous_crosspost_enabled": runtime_settings.autonomous_crosspost_enabled,
            "automation_bridge_enabled": runtime_settings.automation_bridge_enabled,
            "automation_bridge_url": runtime_settings.automation_bridge_url or "",
            "automation_bridge_timeout_seconds": runtime_settings.automation_bridge_timeout_seconds,
            "automation_bridge_configured": bool(runtime_settings.automation_bridge_enabled and runtime_settings.automation_bridge_url and runtime_settings.automation_bridge_api_key),
            "bridge_browser_submit_enabled": bridge_submit_policy["browser_submit_enabled"],
            "bridge_browser_submit_policy_label": bridge_submit_policy["policy_label"],
            "bridge_browser_submit_policy_note": bridge_submit_policy["policy_note"],
            "sale_detection_enabled": settings.sale_detection_enabled,
            "sale_detection_dry_run": settings.sale_detection_dry_run,
            "sale_detection_poll_minutes": settings.sale_detection_poll_minutes,
            "sold_sync_enabled": settings.sold_sync_enabled,
        },
        "server": {
            "app_base_url": runtime_settings.app_base_url or "",
            "environment": runtime_settings.environment,
            "storage_root": runtime_settings.storage_root,
            "database_url_configured": bool(runtime_settings.database_url),
            "redis_url_configured": bool(runtime_settings.redis_url),
            "session_secret_configured": bool(runtime_settings.session_secret),
            "can_manage": is_effective_admin(current_user),
        },
        "email": {
            "configured": smtp_configured(),
            "host": runtime_settings.smtp_host or "",
            "port": runtime_settings.smtp_port,
            "username": runtime_settings.smtp_username or "",
            "from_email": runtime_settings.smtp_from_email or "",
            "from_name": runtime_settings.smtp_from_name or "",
            "use_tls": runtime_settings.smtp_use_tls,
            "password_configured": bool(runtime_settings.smtp_password),
        },
        "amazon": {
            "vine_import_enabled": runtime_settings.amazon_vine_import_enabled,
            "vine_import_premium_only": runtime_settings.amazon_vine_import_premium_only,
            "media_lookup_enabled": runtime_settings.amazon_media_lookup_enabled,
            "media_page_fallback_enabled": runtime_settings.amazon_media_page_fallback_enabled,
            "marketplace_region": runtime_settings.amazon_marketplace_region,
            "media_fetch_mode": runtime_settings.amazon_media_fetch_mode,
            "media_rate_limit_per_minute": runtime_settings.amazon_media_rate_limit_per_minute,
            "paapi_access_key_configured": bool(runtime_settings.amazon_paapi_access_key),
            "paapi_secret_key_configured": bool(runtime_settings.amazon_paapi_secret_key),
            "paapi_partner_tag_configured": bool(runtime_settings.amazon_paapi_partner_tag),
            "paapi_access_key_masked": mask_secret(runtime_settings.amazon_paapi_access_key),
            "paapi_partner_tag_masked": mask_secret(runtime_settings.amazon_paapi_partner_tag),
        },
        "hosted_pages": {
            "can_manage": is_effective_admin(current_user),
            "brand_name": site_content["brand_name"],
            "active_theme_id": site_content["active_theme_id"],
            "themes": site_content["themes"],
            "pages": {
                "privacy_policy": {
                    **site_content["pages"]["privacy_policy"],
                    "url": page_urls["privacy_policy_url"],
                },
                "trust_center": {
                    **site_content["pages"]["trust_center"],
                    "url": page_urls["trust_center_url"],
                },
                "operator_onboarding": {
                    **site_content["pages"]["operator_onboarding"],
                    "url": page_urls["operator_onboarding_url"],
                },
                "ebay_auth_accepted": {
                    **site_content["pages"]["ebay_auth_accepted"],
                    "url": page_urls["auth_accepted_url"],
                },
                "ebay_auth_declined": {
                    **site_content["pages"]["ebay_auth_declined"],
                    "url": page_urls["auth_declined_url"],
                },
            },
        },
    }


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return _serialize_user(current_user)


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.full_name is not None:
        cleaned = payload.full_name.strip()
        current_user.full_name = cleaned or None
    workflow_updates = {}
    if payload.review_before_publish is not None:
        workflow_updates["review_before_publish"] = payload.review_before_publish
    if payload.auto_publish_after_approval is not None:
        workflow_updates["auto_publish_after_approval"] = payload.auto_publish_after_approval
    if payload.bulk_approval_enabled is not None:
        workflow_updates["bulk_approval_enabled"] = payload.bulk_approval_enabled
    if payload.listing_preview_mode is not None:
        workflow_updates["listing_preview_mode"] = payload.listing_preview_mode.strip() or _DEFAULT_WORKFLOW_PREFERENCES["listing_preview_mode"]
    if payload.default_preview_marketplace is not None:
        workflow_updates["default_preview_marketplace"] = payload.default_preview_marketplace.strip().lower() or _DEFAULT_WORKFLOW_PREFERENCES["default_preview_marketplace"]
    if workflow_updates:
        _persist_workflow_preferences(current_user, workflow_updates)
    if payload.vine_enforce_six_month_lock is not None:
        _persist_vine_preferences(
            current_user,
            {"enforce_six_month_lock": bool(payload.vine_enforce_six_month_lock)},
        )
    sold_sync_updates = {}
    if payload.sold_out_delist_everywhere is not None:
        sold_sync_updates["sold_out_delist_everywhere"] = bool(payload.sold_out_delist_everywhere)
    if payload.out_of_stock_delist_everywhere is not None:
        sold_sync_updates["out_of_stock_delist_everywhere"] = bool(payload.out_of_stock_delist_everywhere)
    if payload.remove_media_on_sold_out is not None:
        sold_sync_updates["remove_media_on_sold_out"] = bool(payload.remove_media_on_sold_out)
    if sold_sync_updates:
        _persist_sold_sync_preferences(current_user, sold_sync_updates)
    if payload.ebay_marketplace_policy_settings is not None:
        _persist_ebay_marketplace_policy_settings(
            current_user,
            payload.ebay_marketplace_policy_settings,
        )
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user)


@router.get("/settings/panels")
def get_settings_panels(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ebay_account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == current_user.id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    return _build_settings_panel_response(current_user, ebay_account=ebay_account)


@router.put("/settings/server")
def update_server_settings(
    payload: ServerSettingsUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can change server settings")
    if not is_effective_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin preview mode cannot change server settings")

    updates: dict[str, str | None] = {}

    for field_name, env_key in _STRING_SETTING_FIELDS.items():
        raw_value = getattr(payload, field_name)
        if raw_value is None:
            continue
        normalized = raw_value.strip()
        value = normalized or None
        setattr(settings, field_name, value)
        updates[env_key] = value

    for field_name, env_key in _BOOL_SETTING_FIELDS.items():
        raw_value = getattr(payload, field_name)
        if raw_value is None:
            continue
        setattr(settings, field_name, raw_value)
        updates[env_key] = "true" if raw_value else "false"

    for field_name, env_key in _INT_SETTING_FIELDS.items():
        raw_value = getattr(payload, field_name)
        if raw_value is None:
            continue
        setattr(settings, field_name, raw_value)
        updates[env_key] = str(raw_value)

    for field_name, env_key in _SECRET_SETTING_FIELDS.items():
        raw_value = getattr(payload, field_name)
        if raw_value is None:
            continue
        encrypted = encrypt_secret(raw_value, secret_key=settings.session_secret)
        setattr(settings, f"{field_name}_enc", encrypted)
        plain_field = f"{field_name}_plain"
        if hasattr(settings, plain_field):
            setattr(settings, plain_field, None)
        updates[env_key] = encrypted
        plain_env_key = _SECRET_PLAIN_ENV_FIELDS.get(field_name)
        if plain_env_key:
            updates[plain_env_key] = None

    if updates:
        _write_env_overrides(updates)

    ebay_account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == current_user.id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    return _build_settings_panel_response(current_user, ebay_account=ebay_account)


@router.put("/settings/hosted-pages")
def update_hosted_pages(
    payload: HostedPagesUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can change hosted pages")
    if not is_effective_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin preview mode cannot change hosted pages")

    current = load_site_content()
    incoming_pages = payload.pages if isinstance(payload.pages, dict) else {
        "privacy_policy": {
            "slug": payload.privacy_policy_slug if payload.privacy_policy_slug is not None else current["pages"]["privacy_policy"]["slug"],
            "draft": {
                "title": payload.privacy_policy_title if payload.privacy_policy_title is not None else current["pages"]["privacy_policy"]["draft"]["title"],
                "blocks": [{"type": "rich_text", "html": payload.privacy_policy_html if payload.privacy_policy_html is not None else current["pages"]["privacy_policy"]["draft"]["blocks"][0].get("html", "")}],
            },
        },
        "trust_center": {
            "slug": payload.trust_center_slug if payload.trust_center_slug is not None else current["pages"]["trust_center"]["slug"],
            "draft": {
                "title": payload.trust_center_title if payload.trust_center_title is not None else current["pages"]["trust_center"]["draft"]["title"],
                "blocks": [{"type": "rich_text", "html": payload.trust_center_html if payload.trust_center_html is not None else current["pages"]["trust_center"]["draft"]["blocks"][0].get("html", "")}],
            },
        },
        "operator_onboarding": {
            "slug": payload.operator_onboarding_slug if payload.operator_onboarding_slug is not None else current["pages"]["operator_onboarding"]["slug"],
            "draft": {
                "title": payload.operator_onboarding_title if payload.operator_onboarding_title is not None else current["pages"]["operator_onboarding"]["draft"]["title"],
                "blocks": [{"type": "rich_text", "html": payload.operator_onboarding_html if payload.operator_onboarding_html is not None else current["pages"]["operator_onboarding"]["draft"]["blocks"][0].get("html", "")}],
            },
        },
        "ebay_auth_accepted": {
            "slug": payload.ebay_auth_accepted_slug if payload.ebay_auth_accepted_slug is not None else current["pages"]["ebay_auth_accepted"]["slug"],
            "draft": {
                "title": payload.ebay_auth_accepted_title if payload.ebay_auth_accepted_title is not None else current["pages"]["ebay_auth_accepted"]["draft"]["title"],
                "blocks": [{"type": "rich_text", "html": payload.ebay_auth_accepted_html if payload.ebay_auth_accepted_html is not None else current["pages"]["ebay_auth_accepted"]["draft"]["blocks"][0].get("html", "")}],
            },
        },
        "ebay_auth_declined": {
            "slug": payload.ebay_auth_declined_slug if payload.ebay_auth_declined_slug is not None else current["pages"]["ebay_auth_declined"]["slug"],
            "draft": {
                "title": payload.ebay_auth_declined_title if payload.ebay_auth_declined_title is not None else current["pages"]["ebay_auth_declined"]["draft"]["title"],
                "blocks": [{"type": "rich_text", "html": payload.ebay_auth_declined_html if payload.ebay_auth_declined_html is not None else current["pages"]["ebay_auth_declined"]["draft"]["blocks"][0].get("html", "")}],
            },
        },
    }
    save_draft_pages(
        brand_name=payload.brand_name,
        active_theme_id=payload.active_theme_id,
        pages=incoming_pages,
    )

    ebay_account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == current_user.id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    return _build_settings_panel_response(current_user, ebay_account=ebay_account)


@router.post("/settings/hosted-pages/publish")
def publish_hosted_pages(
    payload: HostedPagesPublishRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can publish CMS pages")
    if not is_effective_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin preview mode cannot publish CMS pages")

    publish_draft_pages(payload.page_keys)

    ebay_account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == current_user.id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    return _build_settings_panel_response(current_user, ebay_account=ebay_account)


@router.post("/settings/hosted-pages/import-theme")
def import_hosted_page_theme(
    payload: HostedPagesThemeImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can import CMS themes")
    if not is_effective_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin preview mode cannot import CMS themes")

    try:
        import_theme_pack(
            payload.theme_pack_json,
            replace_existing=payload.replace_existing,
            activate_imported=payload.activate_imported,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Theme pack JSON could not be imported") from exc

    ebay_account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == current_user.id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    return _build_settings_panel_response(current_user, ebay_account=ebay_account)


@router.get("/public/site-pages/{slug}")
def get_public_site_page(slug: str):
    from app.services.site_content_service import get_public_page_by_slug

    page = get_public_page_by_slug(slug)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@router.post("/register", response_model=AuthSessionResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: AuthRegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    email = normalize_email(payload.email)
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="An account with that email already exists")

    user_count = db.execute(select(func.count()).select_from(User)).scalar_one()
    user = User(
        email=email,
        full_name=payload.full_name.strip() if payload.full_name else None,
        password_hash=hash_password(payload.password),
        is_admin=user_count == 0,
        role="owner" if user_count == 0 else "public",
        enabled_platforms=[MarketplaceName.ebay.value],
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    set_session_cookie(response, user.id)
    return AuthSessionResponse(user=_serialize_user(user), is_bootstrap_admin=user.is_admin)


@router.post("/login", response_model=AuthSessionResponse)
def login(
    payload: AuthLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    email = normalize_email(payload.email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid email or password")

    set_session_cookie(response, user.id)
    return AuthSessionResponse(user=_serialize_user(user), is_bootstrap_admin=False)


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/password/change")
def change_password(
    payload: AuthChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.password_hash = hash_password(payload.new_password)
    _clear_password_reset(current_user)
    db.add(current_user)
    db.commit()
    return {"ok": True}


@router.post("/password/forgot")
def forgot_password(
    payload: AuthForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    email = normalize_email(payload.email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    preview_token = None
    if user:
        preview_token = new_password_reset_token()
        _persist_password_reset(
            user,
            token_hash=hash_password_reset_token(preview_token),
            expires_at=_utcnow() + timedelta(hours=1),
        )
        db.add(user)
        db.commit()

    response = {
        "ok": True,
        "message": "If that account exists, a password reset email or token is now available.",
    }
    runtime_settings = reload_settings()
    if preview_token and runtime_settings.environment.lower() != "production":
        response["reset_token_preview"] = preview_token
    if user and preview_token and smtp_configured():
        try:
            reset_link = send_password_reset_email(user.email, preview_token)
            response["delivery"] = "email"
            if runtime_settings.environment.lower() != "production":
                response["reset_link_preview"] = reset_link
        except EmailDeliveryError as exc:
            response["delivery"] = "error"
            if runtime_settings.environment.lower() != "production":
                response["email_error"] = str(exc)
    elif user and preview_token:
        response["delivery"] = "preview_token" if runtime_settings.environment.lower() != "production" else "token_only"
    return response


@router.post("/password/reset")
def reset_password(
    payload: AuthPasswordResetRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    users = db.execute(select(User)).scalars().all()
    token_hash = hash_password_reset_token(payload.token)
    matched_user = None
    for user in users:
        reset_payload = _password_reset_payload(user)
        if reset_payload.get("token_hash") != token_hash:
            continue
        expires_at_raw = str(reset_payload.get("expires_at") or "").strip()
        if not expires_at_raw:
            continue
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Stored reset token is invalid") from exc
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < _utcnow():
            _clear_password_reset(user)
            db.add(user)
            db.commit()
            raise HTTPException(status_code=400, detail="Reset token has expired")
        matched_user = user
        break

    if not matched_user:
        raise HTTPException(status_code=400, detail="Reset token is invalid")

    matched_user.password_hash = hash_password(payload.new_password)
    _clear_password_reset(matched_user)
    db.add(matched_user)
    db.commit()
    db.refresh(matched_user)
    set_session_cookie(response, matched_user.id)
    return AuthSessionResponse(user=_serialize_user(matched_user), is_bootstrap_admin=False)


@router.post("/session/view-mode", response_model=UserResponse)
def update_view_mode(
    payload: AuthViewModeRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
):
    if payload.view_as_regular and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admin accounts can use regular-user preview mode")
    set_session_cookie(response, current_user.id, view_as_regular=payload.view_as_regular)
    setattr(current_user, "_posterpro_view_as_regular", payload.view_as_regular)
    return _serialize_user(current_user)
