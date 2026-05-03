from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.models import User

SESSION_COOKIE_NAME = "posterpro_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
PASSWORD_ITERATIONS = 390_000


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


def issue_session_token(user_id: int) -> str:
    issued_at = int(time.time())
    payload = f"{user_id}:{issued_at}"
    signature = hmac.new(_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def parse_session_token(token: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        user_id_text, issued_at_text, signature = raw.split(":", 2)
        payload = f"{user_id_text}:{issued_at_text}"
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc

    expected = hmac.new(_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    issued_at = int(issued_at_text)
    if int(time.time()) - issued_at > SESSION_MAX_AGE_SECONDS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    return int(user_id_text)


def set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=issue_session_token(user_id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.environment.lower() == "production",
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def resolve_user_scope(current_user: User, requested_user_id: int | None = None) -> int:
    resolved = requested_user_id or current_user.id
    if resolved != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return resolved


def ensure_user_owns_resource(current_user: User, owner_user_id: int) -> None:
    if owner_user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")


SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)]


def get_optional_current_user(
    session_token: SessionCookie = None,
    db: Session = Depends(get_db),
) -> User | None:
    if not session_token:
        return None
    user_id = parse_session_token(session_token)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def get_current_user(
    current_user: User | None = Depends(get_optional_current_user),
) -> User:
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return current_user
