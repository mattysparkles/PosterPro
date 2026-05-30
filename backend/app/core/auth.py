from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sys
import time
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.models import User

SESSION_COOKIE_NAME = "posterpro_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
PASSWORD_ITERATIONS = 10_000 if "pytest" in sys.modules else 390_000
VINE_ALLOWED_ROLES = {"owner", "admin", "employee"}
PASSWORD_RESET_TTL_SECONDS = 60 * 60


def _secret_key() -> bytes:
    secret = settings.session_secret or f"{settings.app_name}:{settings.environment}:posterpro"
    return secret.encode("utf-8")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        algorithm, iterations_text, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations_text),
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def issue_session_token(user_id: int, *, view_as_regular: bool = False) -> str:
    issued_at = int(time.time())
    mode_flag = "1" if view_as_regular else "0"
    payload = f"{user_id}:{issued_at}:{mode_flag}"
    signature = hmac.new(_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def _urlsafe_b64decode_text(value: str) -> str:
    # Some clients/proxies can drop trailing "=" padding in cookie values.
    # Accept both padded and unpadded forms so valid sessions stay readable.
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")


def parse_session_token(token: str) -> tuple[int, bool]:
    try:
        raw = _urlsafe_b64decode_text(token)
        parts = raw.split(":")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc

    if len(parts) == 3:
        user_id_text, issued_at_text, signature = parts
        mode_flag = "0"
        payload = f"{user_id_text}:{issued_at_text}"
    elif len(parts) == 4:
        user_id_text, issued_at_text, mode_flag, signature = parts
        payload = f"{user_id_text}:{issued_at_text}:{mode_flag}"
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    expected = hmac.new(_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    issued_at = int(issued_at_text)
    if int(time.time()) - issued_at > SESSION_MAX_AGE_SECONDS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    return int(user_id_text), mode_flag == "1"


def set_session_cookie(response: Response, user_id: int, *, view_as_regular: bool = False) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=issue_session_token(user_id, view_as_regular=view_as_regular),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.environment.lower() == "production",
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def is_viewing_as_regular(user: User | None) -> bool:
    return bool(getattr(user, "_posterpro_view_as_regular", False))


def is_effective_admin(user: User | None) -> bool:
    return bool(user and user.is_admin and not is_viewing_as_regular(user))


def resolve_user_scope(current_user: User, requested_user_id: int | None = None) -> int:
    resolved = requested_user_id or current_user.id
    if resolved != current_user.id and not is_effective_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return resolved


def ensure_user_owns_resource(current_user: User, owner_user_id: int) -> None:
    if owner_user_id != current_user.id and not is_effective_admin(current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")


def get_user_role(user: User | None) -> str:
    if not user:
        return "public"
    if is_viewing_as_regular(user):
        return "public"
    role = (user.role or "").strip().lower()
    if role:
        return role
    if user.is_admin:
        return "admin"
    return "public"


def user_has_vine_access(user: User | None) -> bool:
    if not user:
        return False
    # Keep Vine operator access available for owners/admins even when using
    # "view as regular" mode for storefront/testing flows.
    if user.is_admin:
        return True
    role = (user.role or "").strip().lower()
    return role in VINE_ALLOWED_ROLES


def user_has_premium_access(user: User | None) -> bool:
    if not user:
        return False
    settings_json = user.settings_json or {}
    plan = str(settings_json.get("plan") or "").strip().lower()
    return plan in {"premium", "pro", "enterprise"} or get_user_role(user) in {"owner", "admin"}


def ensure_vine_access(current_user: User) -> None:
    if not settings.amazon_vine_import_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Amazon Vine import is disabled")
    if not user_has_vine_access(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Amazon Vine import is restricted")
    if settings.amazon_vine_import_premium_only and not user_has_premium_access(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Amazon Vine import requires a premium plan")


SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)]


def get_optional_current_user(
    session_token: SessionCookie = None,
    db: Session = Depends(get_db),
) -> User | None:
    if not session_token:
        return None
    user_id, view_as_regular = parse_session_token(session_token)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    setattr(user, "_posterpro_view_as_regular", view_as_regular)
    return user


def get_current_user(
    current_user: User | None = Depends(get_optional_current_user),
) -> User:
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return current_user


def new_password_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password_reset_token(token: str) -> str:
    return hmac.new(_secret_key(), token.encode("utf-8"), hashlib.sha256).hexdigest()
