from types import SimpleNamespace

from app.models.models import User
from app.services import google_photos_oauth as oauth


def test_google_photos_auth_url_includes_scopes_and_state(monkeypatch):
    runtime = SimpleNamespace(
        google_photos_client_id="google-client",
        google_photos_client_secret="google-secret",
        google_photos_redirect_uri="https://posterpro.example.com/api/intake/google-photos/callback",
        app_base_url="https://posterpro.example.com",
    )
    monkeypatch.setattr(oauth, "reload_settings", lambda: runtime)
    monkeypatch.setattr(oauth, "settings", SimpleNamespace(session_secret="oauth-test-secret"))

    auth_url = oauth.build_auth_url(7)

    assert auth_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=google-client" in auth_url
    assert "redirect_uri=https%3A%2F%2Fposterpro.example.com%2Fapi%2Fintake%2Fgoogle-photos%2Fcallback" in auth_url
    assert "https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fphotoslibrary.appendonly" in auth_url
    assert "state=7%3A" in auth_url


def test_google_photos_connection_state_round_trip_encrypts_tokens(db_session, monkeypatch):
    monkeypatch.setattr(oauth, "settings", SimpleNamespace(session_secret="oauth-test-secret"))

    user = User(email="google-oauth@example.com", is_admin=False, role="public")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    state = oauth.save_google_photos_connection(
        user=user,
        db=db_session,
        token_payload={
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "scope": "openid email profile https://www.googleapis.com/auth/photoslibrary.appendonly",
        },
        userinfo={"sub": "google-subject", "email": "google@example.com", "name": "Google User"},
        redirect_uri="https://posterpro.example.com/api/intake/google-photos/callback",
    )

    assert state["connected"] is True
    assert state["connection_state"] == "connected"
    assert state["account_email"] == "google@example.com"
    assert state["account_subject"] == "google-subject"
    assert state["has_refresh_token"] is True
    assert state["last_error"] is None

    stored = user.settings_json["google_photos_oauth"]
    assert stored["access_token_enc"] != "access-token"
    assert stored["refresh_token_enc"] != "refresh-token"
    assert "access-token" not in stored["access_token_enc"]

    loaded = oauth.get_google_photos_oauth_state(user)
    assert loaded["connected"] is True
    assert loaded["account_email"] == "google@example.com"
    assert loaded["has_refresh_token"] is True
