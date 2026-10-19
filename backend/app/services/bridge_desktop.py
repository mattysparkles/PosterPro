from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime

from app.core.config import settings

_TOKEN_TTL_SECONDS = 60 * 60


class BridgeDesktopTokenError(RuntimeError):
    pass


def _secret_key() -> bytes:
    secret = settings.session_secret or f"{settings.app_name}:{settings.environment}:posterpro"
    return secret.encode("utf-8")


def issue_bridge_desktop_token(*, user_id: int, connect_session_id: str) -> tuple[str, str]:
    issued_at = int(time.time())
    expires_at = issued_at + _TOKEN_TTL_SECONDS
    payload = f"{user_id}:{connect_session_id}:{issued_at}:{expires_at}"
    signature = hmac.new(_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("utf-8")
    expires_at_iso = datetime.fromtimestamp(expires_at, tz=UTC).isoformat()
    return token, expires_at_iso


def parse_bridge_desktop_token(token: str) -> dict[str, str | int]:
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        user_id_text, connect_session_id, issued_at_text, expires_at_text, signature = raw.split(":", 4)
    except Exception as exc:  # noqa: BLE001
        raise BridgeDesktopTokenError("Invalid bridge desktop token") from exc

    payload = f"{user_id_text}:{connect_session_id}:{issued_at_text}:{expires_at_text}"
    expected = hmac.new(_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise BridgeDesktopTokenError("Invalid bridge desktop token")

    expires_at = int(expires_at_text)
    if int(time.time()) > expires_at:
        raise BridgeDesktopTokenError("Bridge desktop token expired")

    return {
        "user_id": int(user_id_text),
        "connect_session_id": connect_session_id,
        "issued_at": int(issued_at_text),
        "expires_at": expires_at,
    }


def bridge_desktop_target() -> tuple[str, int]:
    return settings.automation_bridge_vnc_host, int(settings.automation_bridge_vnc_port or 5901)
