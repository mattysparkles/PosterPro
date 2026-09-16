from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_automation_toggle_is_admin_only_and_persists(async_client, monkeypatch, tmp_path):
    from app.api import auth as auth_api
    from app.core.config import settings

    env_path = tmp_path / 'posterpro.env'
    monkeypatch.setattr(auth_api, '_BACKEND_ENV_PATH', env_path)
    monkeypatch.setattr(settings, 'autonomous_mode', True)
    denied = await async_client.post('/config/toggle-autonomous', json={'enabled': False})
    assert denied.status_code == 401

    register = await async_client.post('/auth/register', json={
        'full_name': 'Automation Admin',
        'email': f'automation-admin-{uuid4()}@example.com',
        'password': 'supersecret123',
    })
    assert register.status_code == 201
    response = await async_client.post('/config/toggle-autonomous', json={'enabled': False})
    assert response.status_code == 200
    assert response.json()['autonomous_mode'] is False
    assert 'AUTONOMOUS_MODE=false' in env_path.read_text(encoding='utf-8')

@pytest.mark.anyio
async def test_register_login_password_change_reset_and_view_mode(async_client):
    register_response = await async_client.post(
        "/auth/register",
        json={
            "full_name": "PosterPro Owner",
            "email": "owner@example.com",
            "password": "supersecret123",
        },
    )
    assert register_response.status_code == 201
    register_data = register_response.json()
    assert register_data["user"]["email"] == "owner@example.com"
    assert register_data["user"]["is_admin"] is True
    assert register_data["user"]["effective_is_admin"] is True
    assert register_data["is_bootstrap_admin"] is True

    me_response = await async_client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["role"] == "owner"

    logout_response = await async_client.post("/auth/logout")
    assert logout_response.status_code == 200
    assert (await async_client.get("/auth/me")).status_code == 401

    login_response = await async_client.post(
        "/auth/login",
        json={
            "email": "owner@example.com",
            "password": "supersecret123",
        },
    )
    assert login_response.status_code == 200
    assert login_response.json()["is_bootstrap_admin"] is True

    change_password_response = await async_client.post(
        "/auth/password/change",
        json={
            "current_password": "supersecret123",
            "new_password": "supersecret456",
        },
    )
    assert change_password_response.status_code == 200

    await async_client.post("/auth/logout")
    assert (
        (await async_client.post(
            "/auth/login",
            json={
                "email": "owner@example.com",
                "password": "supersecret123",
            },
        )).status_code
        == 400
    )
    assert (
        (await async_client.post(
            "/auth/login",
            json={
                "email": "owner@example.com",
                "password": "supersecret456",
            },
        )).status_code
        == 200
    )

    preview_response = await async_client.post("/auth/session/view-mode", json={"view_as_regular": True})
    assert preview_response.status_code == 200
    preview_data = preview_response.json()
    assert preview_data["is_admin"] is True
    assert preview_data["effective_is_admin"] is False
    assert preview_data["view_as_regular"] is True
    assert preview_data["role"] == "public"

    restore_response = await async_client.post("/auth/session/view-mode", json={"view_as_regular": False})
    assert restore_response.status_code == 200
    assert restore_response.json()["effective_is_admin"] is True

    forgot_response = await async_client.post(
        "/auth/password/forgot",
        json={"email": "owner@example.com"},
    )
    assert forgot_response.status_code == 200
    reset_token = forgot_response.json()["reset_token_preview"]
    assert reset_token

    reset_response = await async_client.post(
        "/auth/password/reset",
        json={
            "token": reset_token,
            "new_password": "supersecret789",
        },
    )
    assert reset_response.status_code == 200
    assert reset_response.json()["user"]["email"] == "owner@example.com"

    await async_client.post("/auth/logout")
    final_login_response = await async_client.post(
        "/auth/login",
        json={
            "email": "owner@example.com",
            "password": "supersecret789",
        },
    )
    assert final_login_response.status_code == 200


@pytest.mark.anyio
async def test_auth_routes_support_cross_origin_session_requests(async_client):
    options_response = await async_client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3030",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert options_response.status_code == 200
    assert options_response.headers["access-control-allow-origin"] == "http://localhost:3030"
    assert options_response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.anyio
async def test_dashboard_metric_layout_is_persisted_per_user_and_sanitized(async_client):
    register = await async_client.post("/auth/register", json={"full_name": "Layout Owner", "email": "dashboard-layout@example.com", "password": "supersecret123"})
    assert register.status_code == 201
    saved = await async_client.patch("/auth/me", json={"profile_preferences": {"dashboard_metrics": {"order": ["live", "ready", "live", "secret"], "visible": {"live": True, "ready": False, "secret": True}}}})
    assert saved.status_code == 200
    layout = saved.json()["profile_preferences"]["dashboard_metrics"]
    assert layout["order"] == ["live", "ready", "review", "draft"]
    assert layout["visible"] == {"ready": False, "review": True, "live": True, "draft": True}
    reread = await async_client.get("/auth/me")
    assert reread.json()["profile_preferences"]["dashboard_metrics"] == layout
    second = await async_client.post("/auth/register", json={"full_name": "Separate Layout Owner", "email": f"dashboard-layout-{uuid4()}@example.com", "password": "supersecret123"})
    assert second.status_code == 201
    assert second.json()["user"]["profile_preferences"].get("dashboard_metrics") is None
