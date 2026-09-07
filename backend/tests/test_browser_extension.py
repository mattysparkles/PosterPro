from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_browser_extension_session_import_marks_mercari_ready(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Extension Operator",
            "email": f"browser-extension-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    from app.api import browser_extension as browser_extension_api

    monkeypatch.setattr(
        browser_extension_api,
        "upsert_bridge_account",
        lambda **kwargs: {
            "account_key": kwargs.get("account_key"),
            "marketplace": kwargs.get("marketplace"),
            "session_state": kwargs.get("payload", {}).get("session_state"),
        },
    )
    monkeypatch.setattr(
        browser_extension_api,
        "update_bridge_account_session",
        lambda **kwargs: {
            "account_key": kwargs.get("account_key"),
            "marketplace": kwargs.get("marketplace"),
            "session_state": kwargs.get("payload", {}).get("session_state"),
        },
    )

    response = await async_client.post(
        "/browser-extension/sessions/import",
        json={
            "marketplace": "mercari",
            "account_key": "mercari-main",
            "display_name": "Mercari Main",
            "login_handle": "@sparkles",
            "notes": "Captured from extension",
            "workflow_state": "ready",
            "import_mode": "browser_assist",
            "publish_mode": "browser_assist",
            "shipping_scope": "shipping_only",
            "renewal_mode": "manual",
            "bridge_session_state": "ready",
            "session_payload": {
                "cookies": [{"name": "session", "value": "abc123"}],
                "origins": [],
            },
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["marketplace"] == "mercari"
    assert data["connected"] is True
    assert data["workflow_state"] == "ready"
    assert data["bridge_account_key"] == "mercari-main"
    assert data["publish_mode"] == "browser_assist"

    summary = await async_client.get(f"/users/{user_id}/setup")
    assert summary.status_code == 200
    mercari = next(item for item in summary.json()["marketplace_connections"] if item["marketplace"] == "mercari")
    assert mercari["connected"] is True
    assert mercari["bridge_account_key"] == "mercari-main"
    assert mercari["workflow_state"] == "ready"
