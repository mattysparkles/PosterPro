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
from app.core.config import settings
from app.core.database import get_db
from app.core.secrets import encrypt_secret, mask_secret
from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, User
from app.services.email_service import EmailDeliveryError, send_password_reset_email, smtp_configured

router = APIRouter(prefix="/auth", tags=["auth"])

_BACKEND_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_STRING_SETTING_FIELDS = {
    "app_base_url": "APP_BASE_URL",
    "ebay_client_id": "EBAY_CLIENT_ID",
    "ebay_redirect_uri": "EBAY_REDIRECT_URI",
    "storage_root": "STORAGE_ROOT",
    "environment": "ENVIRONMENT",
    "amazon_marketplace_region": "AMAZON_MARKETPLACE_REGION",
    "amazon_media_fetch_mode": "AMAZON_MEDIA_FETCH_MODE",
    "smtp_host": "SMTP_HOST",
    "smtp_username": "SMTP_USERNAME",
    "smtp_from_email": "SMTP_FROM_EMAIL",
    "smtp_from_name": "SMTP_FROM_NAME",
}
_BOOL_SETTING_FIELDS = {
    "autonomous_dry_run": "AUTONOMOUS_DRY_RUN",
    "autonomous_crosspost_enabled": "AUTONOMOUS_CROSSPOST_ENABLED",
    "sale_detection_enabled": "SALE_DETECTION_ENABLED",
    "sale_detection_dry_run": "SALE_DETECTION_DRY_RUN",
    "amazon_vine_import_enabled": "AMAZON_VINE_IMPORT_ENABLED",
    "amazon_vine_import_premium_only": "AMAZON_VINE_IMPORT_PREMIUM_ONLY",
    "amazon_media_lookup_enabled": "AMAZON_MEDIA_LOOKUP_ENABLED",
    "amazon_media_page_fallback_enabled": "AMAZON_MEDIA_PAGE_FALLBACK_ENABLED",
    "smtp_use_tls": "SMTP_USE_TLS",
}
_INT_SETTING_FIELDS = {
    "sale_detection_poll_minutes": "SALE_DETECTION_POLL_MINUTES",
    "amazon_media_rate_limit_per_minute": "AMAZON_MEDIA_RATE_LIMIT_PER_MINUTE",
    "smtp_port": "SMTP_PORT",
}
_SECRET_SETTING_FIELDS = {
    "openai_api_key": "OPENAI_API_KEY_ENC",
    "photoroom_api_key": "PHOTOROOM_API_KEY_ENC",
    "ebay_client_secret": "EBAY_CLIENT_SECRET_ENC",
    "amazon_paapi_access_key": "AMAZON_PAAPI_ACCESS_KEY_ENC",
    "amazon_paapi_secret_key": "AMAZON_PAAPI_SECRET_KEY_ENC",
    "amazon_paapi_partner_tag": "AMAZON_PAAPI_PARTNER_TAG_ENC",
    "smtp_password": "SMTP_PASSWORD_ENC",
}
_SECRET_PLAIN_ENV_FIELDS = {
    "openai_api_key": "OPENAI_API_KEY",
    "photoroom_api_key": "PHOTOROOM_API_KEY",
    "ebay_client_secret": "EBAY_CLIENT_SECRET",
    "smtp_password": "SMTP_PASSWORD",
}

_DEFAULT_WORKFLOW_PREFERENCES = {
    "review_before_publish": True,
    "auto_publish_after_approval": False,
    "bulk_approval_enabled": True,
    "listing_preview_mode": "marketplace",
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
    }


def _persist_workflow_preferences(user: User, updates: dict) -> None:
    settings_json = dict(user.settings_json or {})
    current = _workflow_preferences(user)
    current.update(updates)
    settings_json["workflow_preferences"] = current
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


def _build_settings_panel_response(current_user: User, *, ebay_connected: bool) -> dict:
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
        "ebay": {
            "client_id_configured": bool(settings.ebay_client_id),
            "client_secret_configured": bool(settings.ebay_client_secret),
            "redirect_uri": settings.ebay_redirect_uri or "",
            "oauth_ready": bool(settings.ebay_client_id and settings.ebay_client_secret and settings.ebay_redirect_uri),
            "connected": ebay_connected,
        },
        "api_keys": {
            "openai_configured": bool(settings.openai_api_key),
            "photoroom_configured": bool(settings.photoroom_api_key),
        },
        "automation": {
            "autonomous_mode": settings.autonomous_mode,
            "autonomous_dry_run": settings.autonomous_dry_run,
            "autonomous_crosspost_enabled": settings.autonomous_crosspost_enabled,
            "sale_detection_enabled": settings.sale_detection_enabled,
            "sale_detection_dry_run": settings.sale_detection_dry_run,
            "sale_detection_poll_minutes": settings.sale_detection_poll_minutes,
        },
        "server": {
            "app_base_url": settings.app_base_url or "",
            "environment": settings.environment,
            "storage_root": settings.storage_root,
            "database_url_configured": bool(settings.database_url),
            "redis_url_configured": bool(settings.redis_url),
            "session_secret_configured": bool(settings.session_secret),
            "can_manage": is_effective_admin(current_user),
        },
        "email": {
            "configured": smtp_configured(),
            "host": settings.smtp_host or "",
            "port": settings.smtp_port,
            "username": settings.smtp_username or "",
            "from_email": settings.smtp_from_email or "",
            "from_name": settings.smtp_from_name or "",
            "use_tls": settings.smtp_use_tls,
            "password_configured": bool(settings.smtp_password),
        },
        "amazon": {
            "vine_import_enabled": settings.amazon_vine_import_enabled,
            "vine_import_premium_only": settings.amazon_vine_import_premium_only,
            "media_lookup_enabled": settings.amazon_media_lookup_enabled,
            "media_page_fallback_enabled": settings.amazon_media_page_fallback_enabled,
            "marketplace_region": settings.amazon_marketplace_region,
            "media_fetch_mode": settings.amazon_media_fetch_mode,
            "media_rate_limit_per_minute": settings.amazon_media_rate_limit_per_minute,
            "paapi_access_key_configured": bool(settings.amazon_paapi_access_key),
            "paapi_secret_key_configured": bool(settings.amazon_paapi_secret_key),
            "paapi_partner_tag_configured": bool(settings.amazon_paapi_partner_tag),
            "paapi_access_key_masked": mask_secret(settings.amazon_paapi_access_key),
            "paapi_partner_tag_masked": mask_secret(settings.amazon_paapi_partner_tag),
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
    if workflow_updates:
        _persist_workflow_preferences(current_user, workflow_updates)
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
    return _build_settings_panel_response(current_user, ebay_connected=ebay_account is not None)


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
    return _build_settings_panel_response(current_user, ebay_connected=ebay_account is not None)


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
    if preview_token and settings.environment.lower() != "production":
        response["reset_token_preview"] = preview_token
    if user and preview_token and smtp_configured():
        try:
            reset_link = send_password_reset_email(user.email, preview_token)
            response["delivery"] = "email"
            if settings.environment.lower() != "production":
                response["reset_link_preview"] = reset_link
        except EmailDeliveryError as exc:
            response["delivery"] = "error"
            if settings.environment.lower() != "production":
                response["email_error"] = str(exc)
    elif user and preview_token:
        response["delivery"] = "preview_token" if settings.environment.lower() != "production" else "token_only"
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
