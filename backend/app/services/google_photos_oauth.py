from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import reload_settings, settings
from app.core.secrets import encrypt_secret, decrypt_secret_if_needed


GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_PHOTOS_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
]
GOOGLE_OAUTH_KEY = "google_photos_oauth"


class GooglePhotosOAuthError(RuntimeError):
    pass


def _access_token_for_user(user, db) -> str:
    """Return a usable access token, refreshing the stored token when needed."""
    settings_json = _root_settings(user)
    raw = settings_json.get(GOOGLE_OAUTH_KEY)
    data = raw if isinstance(raw, dict) else {}
    token_enc = data.get("access_token_enc")
    token = decrypt_secret_if_needed(token_enc, secret_key=settings.session_secret) if isinstance(token_enc, str) else None
    expires_at = data.get("token_expires_at")
    expired = False
    if isinstance(expires_at, str) and expires_at:
        try:
            expired = datetime.fromisoformat(expires_at) <= datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=30)
        except ValueError:
            expired = False
    # Refresh proactively when a refresh token is available. Google can revoke
    # or invalidate an access token before its recorded expiry; a Slate upload
    # must not silently use a stale bearer token.
    if data.get("refresh_token_enc") and (expired or token):
        refresh_google_photos_access_token(user, db)
        refreshed = _root_settings(user).get(GOOGLE_OAUTH_KEY) or {}
        token = decrypt_secret_if_needed(refreshed.get("access_token_enc"), secret_key=settings.session_secret)
    if not token:
        raise GooglePhotosOAuthError("Google Photos is not connected or its token has expired")
    return token


def upload_photo_to_album(*, user, db, album_id: str | None, image_bytes: bytes, filename: str, description: str = "", album_title: str = "PosterPro") -> dict[str, Any]:
    """Upload one generated image through the real Google Photos API."""
    # A stored access token can be revoked before its recorded expiry. When a
    # refresh token exists, refresh immediately so the upload never uses stale
    # credentials.
    oauth_data = (_root_settings(user).get(GOOGLE_OAUTH_KEY) or {})
    if isinstance(oauth_data, dict) and oauth_data.get("refresh_token_enc"):
        refresh_google_photos_access_token(user, db)
    token = _access_token_for_user(user, db)
    clean_album = str(album_id or "").strip()
    album_product_url = None
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        # A photos.app.goo.gl share URL is not an API album ID. Create an
        # app-owned PosterPro album once, then persist/use its durable ID.
        album_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        # A previously stored app-owned album can be deleted or become
        # inaccessible after OAuth/account changes. Validate it before upload
        # and transparently provision a replacement exactly once.
        if clean_album:
            album_check = client.get(f"https://photoslibrary.googleapis.com/v1/albums/{clean_album}", headers=album_headers)
            if album_check.status_code == 404:
                clean_album = ""
            elif album_check.status_code >= 400 and album_check.status_code != 403:
                raise GooglePhotosOAuthError(_extract_error_message(album_check))
        if not clean_album:
            album_response = client.post("https://photoslibrary.googleapis.com/v1/albums", json={"album": {"title": album_title}}, headers=album_headers)
            if album_response.status_code >= 400:
                raise GooglePhotosOAuthError(_extract_error_message(album_response))
            album_payload = album_response.json()
            clean_album = str((album_payload or {}).get("id") or "").strip()
            album_product_url = (album_payload or {}).get("productUrl")
            if not clean_album:
                raise GooglePhotosOAuthError("Google Photos did not return a created album ID")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream", "X-Goog-Upload-File-Name": filename, "X-Goog-Upload-Protocol": "raw"}
        upload_response = client.post("https://photoslibrary.googleapis.com/v1/uploads", content=image_bytes, headers=headers)
        if upload_response.status_code >= 400:
            raise GooglePhotosOAuthError(_extract_error_message(upload_response))
        upload_token = upload_response.text.strip().strip('"')
        if not upload_token:
            raise GooglePhotosOAuthError("Google Photos returned no upload token")
        create_response = client.post(
            "https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate",
            json={"albumId": clean_album, "newMediaItems": [{"description": description[:1000], "simpleMediaItem": {"uploadToken": upload_token}}]},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if create_response.status_code == 404:
            # Stored album was removed or belongs to a prior account. Provision
            # one replacement and retry the same uploaded asset once.
            album_response = client.post("https://photoslibrary.googleapis.com/v1/albums", json={"album": {"title": album_title}}, headers=album_headers)
            if album_response.status_code >= 400:
                raise GooglePhotosOAuthError(_extract_error_message(album_response))
            album_payload = album_response.json()
            clean_album = str((album_payload or {}).get("id") or "").strip()
            album_product_url = (album_payload or {}).get("productUrl")
            if not clean_album:
                raise GooglePhotosOAuthError("Google Photos did not return a replacement album ID")
            create_response = client.post(
                "https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate",
                json={"albumId": clean_album, "newMediaItems": [{"description": description[:1000], "simpleMediaItem": {"uploadToken": upload_token}}]},
                headers=album_headers,
            )
        if create_response.status_code >= 400:
            raise GooglePhotosOAuthError(_extract_error_message(create_response))
        payload = create_response.json()
    results = payload.get("newMediaItemResults") if isinstance(payload, dict) else None
    result = results[0] if isinstance(results, list) and results else {}
    status = str(result.get("status", {}).get("message") or "").strip() if isinstance(result, dict) else ""
    media_item = result.get("mediaItem") if isinstance(result, dict) else {}
    media_id = media_item.get("id") if isinstance(media_item, dict) else None
    if not media_id:
        raise GooglePhotosOAuthError(status or "Google Photos did not return a media item ID")
    # Google only returns productUrl when an album is created in this call.
    # For subsequent uploads retain a stable, operator-openable URL derived
    # from the durable album resource ID instead of falling back to the share
    # URL (which is not an API album identifier).
    album_product_url = album_product_url or f"https://photos.google.com/album/{clean_album}"
    return {"media_id": media_id, "album_id": clean_album, "album_product_url": album_product_url, "status": "UPLOADED", "media_item": media_item}


def _runtime_google_settings():
    return reload_settings()


def google_photos_oauth_ready() -> bool:
    runtime_settings = _runtime_google_settings()
    return bool(runtime_settings.google_photos_client_id and runtime_settings.google_photos_client_secret and google_photos_redirect_uri(runtime_settings))


def google_photos_redirect_uri(runtime_settings=None) -> str | None:
    runtime = runtime_settings or _runtime_google_settings()
    redirect_uri = (runtime.google_photos_redirect_uri or "").strip()
    if redirect_uri:
        return redirect_uri
    app_base_url = (runtime.app_base_url or "").strip().rstrip("/")
    if app_base_url:
        return f"{app_base_url}/api/intake/google-photos/callback"
    return None


def make_oauth_state(user_id: int) -> str:
    payload = str(int(user_id))
    secret = settings.session_secret or "posterpro-google-oauth"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def parse_oauth_state(state: str) -> int:
    payload, _, signature = str(state or "").partition(":")
    if not payload or not signature:
        raise GooglePhotosOAuthError("Invalid Google OAuth state")
    secret = settings.session_secret or "posterpro-google-oauth"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise GooglePhotosOAuthError("Google OAuth state verification failed")
    return int(payload)


def build_auth_url(user_id: int, *, redirect_uri: str | None = None) -> str:
    runtime_settings = _runtime_google_settings()
    client_id = (runtime_settings.google_photos_client_id or "").strip()
    client_secret = runtime_settings.google_photos_client_secret
    callback = (redirect_uri or google_photos_redirect_uri(runtime_settings) or "").strip()
    if not client_id or not client_secret:
        raise GooglePhotosOAuthError("Missing Google Photos OAuth client settings")
    if not callback:
        raise GooglePhotosOAuthError("Missing Google Photos redirect URI")

    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": callback,
            "response_type": "code",
            "scope": " ".join(GOOGLE_PHOTOS_SCOPES),
            "access_type": "offline",
            "prompt": "consent select_account",
            "include_granted_scopes": "true",
            "state": make_oauth_state(user_id),
        }
    )
    return f"{GOOGLE_AUTH_ENDPOINT}?{query}"


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict[str, Any]:
    runtime_settings = _runtime_google_settings()
    client_id = (runtime_settings.google_photos_client_id or "").strip()
    client_secret = runtime_settings.google_photos_client_secret
    if not client_id or not client_secret:
        raise GooglePhotosOAuthError("Missing Google Photos OAuth client settings")
    if not redirect_uri:
        raise GooglePhotosOAuthError("Missing Google Photos redirect URI")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        raise GooglePhotosOAuthError(_extract_error_message(response))
    payload = response.json()
    if not isinstance(payload, dict):
        raise GooglePhotosOAuthError("Google token exchange returned an invalid response")
    return payload


async def fetch_userinfo(access_token: str) -> dict[str, Any]:
    if not access_token:
        raise GooglePhotosOAuthError("Missing Google access token")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(
            GOOGLE_USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        raise GooglePhotosOAuthError(_extract_error_message(response))
    payload = response.json()
    if not isinstance(payload, dict):
        raise GooglePhotosOAuthError("Google userinfo returned an invalid response")
    return payload


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        for key in ("error_description", "error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    detail = (response.text or "").strip().replace("\n", " ")
    return f"Google request failed ({response.status_code})" + (f": {detail[:240]}" if detail else "")


def _root_settings(user) -> dict[str, Any]:
    return dict(getattr(user, "settings_json", None) or {})


def get_google_photos_oauth_state(user) -> dict[str, Any]:
    settings_json = _root_settings(user)
    raw = settings_json.get(GOOGLE_OAUTH_KEY)
    data = raw if isinstance(raw, dict) else {}
    access_token_enc = data.get("access_token_enc")
    refresh_token_enc = data.get("refresh_token_enc")
    access_token = decrypt_secret_if_needed(access_token_enc, secret_key=settings.session_secret) if isinstance(access_token_enc, str) else None
    refresh_token = decrypt_secret_if_needed(refresh_token_enc, secret_key=settings.session_secret) if isinstance(refresh_token_enc, str) else None
    token_expires_at = data.get("token_expires_at")
    expires_dt = None
    if isinstance(token_expires_at, str) and token_expires_at:
        try:
            expires_dt = datetime.fromisoformat(token_expires_at)
        except Exception:
            expires_dt = None
    connected = bool(data.get("connected") or access_token or refresh_token)
    connection_state = str(data.get("connection_state") or ("connected" if connected else "not_connected")).strip() or "not_connected"
    if connected and expires_dt and expires_dt <= datetime.now(UTC).replace(tzinfo=None) and not refresh_token:
        connection_state = "token_expired"
    return {
        "connected": connected,
        "connection_state": connection_state,
        "account_email": data.get("account_email"),
        "account_subject": data.get("account_subject"),
        "account_name": data.get("account_name"),
        "has_refresh_token": bool(refresh_token),
        "token_expires_at": token_expires_at,
        "last_connected_at": data.get("last_connected_at"),
        "last_error": data.get("last_error"),
        "scopes": data.get("scopes") if isinstance(data.get("scopes"), list) else [],
        "redirect_uri": data.get("redirect_uri"),
    }


def refresh_google_photos_access_token(user, db) -> dict[str, Any]:
    settings_json = _root_settings(user)
    raw = settings_json.get(GOOGLE_OAUTH_KEY)
    data = raw if isinstance(raw, dict) else {}
    refresh_token_enc = data.get("refresh_token_enc")
    refresh_token = decrypt_secret_if_needed(refresh_token_enc, secret_key=settings.session_secret) if isinstance(refresh_token_enc, str) else None
    if not refresh_token:
        raise GooglePhotosOAuthError("Google Photos refresh token is not available")

    runtime_settings = _runtime_google_settings()
    client_id = (runtime_settings.google_photos_client_id or "").strip()
    client_secret = runtime_settings.google_photos_client_secret
    if not client_id or not client_secret:
        raise GooglePhotosOAuthError("Missing Google Photos OAuth client settings")

    async def _refresh() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code >= 400:
            raise GooglePhotosOAuthError(_extract_error_message(response))
        payload = response.json()
        if not isinstance(payload, dict):
            raise GooglePhotosOAuthError("Google refresh token exchange returned an invalid response")
        return payload

    token_payload = asyncio.run(_refresh())
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise GooglePhotosOAuthError("Google refresh response did not include an access token")

    expires_in = int(token_payload.get("expires_in") or 3600)
    now = datetime.now(UTC).replace(tzinfo=None)
    data["access_token_enc"] = encrypt_secret(access_token, secret_key=settings.session_secret)
    data["token_expires_at"] = (now + timedelta(seconds=expires_in)).isoformat()
    data["connection_state"] = "connected"
    data["connected"] = True
    data["last_error"] = None
    settings_json[GOOGLE_OAUTH_KEY] = data
    user.settings_json = settings_json
    flag_modified(user, "settings_json")
    db.add(user)
    db.commit()
    db.refresh(user)
    return get_google_photos_oauth_state(user)


def save_google_photos_connection(
    *,
    user,
    db,
    token_payload: dict[str, Any],
    userinfo: dict[str, Any],
    redirect_uri: str,
) -> dict[str, Any]:
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise GooglePhotosOAuthError("Google OAuth response did not include an access token")
    refresh_token = token_payload.get("refresh_token")
    scopes_raw = token_payload.get("scope")
    scopes = [scope for scope in str(scopes_raw or "").split() if scope]
    expires_in = int(token_payload.get("expires_in") or 3600)
    now = datetime.now(UTC).replace(tzinfo=None)
    settings_json = _root_settings(user)
    settings_json[GOOGLE_OAUTH_KEY] = {
        "connected": True,
        "connection_state": "connected",
        "account_subject": userinfo.get("sub"),
        "account_email": userinfo.get("email"),
        "account_name": userinfo.get("name"),
        "access_token_enc": encrypt_secret(access_token, secret_key=settings.session_secret),
        "refresh_token_enc": encrypt_secret(str(refresh_token), secret_key=settings.session_secret) if refresh_token else None,
        "token_expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "scopes": scopes,
        "last_connected_at": now.isoformat(),
        "last_error": None,
        "redirect_uri": redirect_uri,
    }
    user.settings_json = settings_json
    flag_modified(user, "settings_json")
    db.add(user)
    db.commit()
    db.refresh(user)
    return get_google_photos_oauth_state(user)
