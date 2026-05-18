from types import SimpleNamespace

from app.api import marketplace_jobs
from app.services.bridge_desktop import parse_bridge_desktop_token


def test_active_bridge_connect_session_exposes_desktop_access(monkeypatch):
    monkeypatch.setattr(
        marketplace_jobs,
        "get_bridge_connect_session",
        lambda _connect_session_id: {
            "connect_session_id": "connect-active-1",
            "marketplace": "facebook",
            "account_key": "facebook-main",
            "display_name": "Facebook Browser",
            "login_handle": "seller@example.com",
            "status": "waiting_for_login",
            "created_at": "2026-05-17T10:00:00+00:00",
            "updated_at": "2026-05-17T10:01:00+00:00",
            "started_at": "2026-05-17T10:00:30+00:00",
            "completed_at": None,
            "wait_timeout_seconds": 300,
            "message": "Waiting for login",
            "error": None,
            "result": None,
        },
    )

    payload = marketplace_jobs.get_marketplace_bridge_connect_session(
        "connect-active-1",
        current_user=SimpleNamespace(id=42),
    )

    assert payload["connect_session_id"] == "connect-active-1"
    assert payload["status"] == "waiting_for_login"
    assert payload["desktop_access"]["websocket_path"] == "marketplace-jobs/bridge-desktop/ws"
    assert payload["desktop_access"]["token"]
    assert payload["desktop_access"]["expires_at"]
    claims = parse_bridge_desktop_token(payload["desktop_access"]["token"])
    assert claims["user_id"] == 42
    assert claims["connect_session_id"] == "connect-active-1"


def test_terminal_bridge_connect_session_omits_desktop_access(monkeypatch):
    monkeypatch.setattr(
        marketplace_jobs,
        "get_bridge_connect_session",
        lambda _connect_session_id: {
            "connect_session_id": "connect-complete-1",
            "marketplace": "facebook",
            "account_key": "facebook-main",
            "display_name": "Facebook Browser",
            "login_handle": "seller@example.com",
            "status": "completed",
            "created_at": "2026-05-17T10:00:00+00:00",
            "updated_at": "2026-05-17T10:05:00+00:00",
            "started_at": "2026-05-17T10:00:30+00:00",
            "completed_at": "2026-05-17T10:05:00+00:00",
            "wait_timeout_seconds": 300,
            "message": "Completed",
            "error": None,
            "result": None,
        },
    )

    payload = marketplace_jobs.get_marketplace_bridge_connect_session(
        "connect-complete-1",
        current_user=SimpleNamespace(id=42),
    )

    assert payload["status"] == "completed"
    assert "desktop_access" not in payload
