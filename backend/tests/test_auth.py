from fastapi.testclient import TestClient

from app.main import app


def test_register_login_password_change_reset_and_view_mode():
    client = TestClient(app)

    register_response = client.post(
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

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["role"] == "owner"

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200
    assert client.get("/auth/me").status_code == 401

    login_response = client.post(
        "/auth/login",
        json={
            "email": "owner@example.com",
            "password": "supersecret123",
        },
    )
    assert login_response.status_code == 200

    change_password_response = client.post(
        "/auth/password/change",
        json={
            "current_password": "supersecret123",
            "new_password": "supersecret456",
        },
    )
    assert change_password_response.status_code == 200

    client.post("/auth/logout")
    assert (
        client.post(
            "/auth/login",
            json={
                "email": "owner@example.com",
                "password": "supersecret123",
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/auth/login",
            json={
                "email": "owner@example.com",
                "password": "supersecret456",
            },
        ).status_code
        == 200
    )

    preview_response = client.post("/auth/session/view-mode", json={"view_as_regular": True})
    assert preview_response.status_code == 200
    preview_data = preview_response.json()
    assert preview_data["is_admin"] is True
    assert preview_data["effective_is_admin"] is False
    assert preview_data["view_as_regular"] is True
    assert preview_data["role"] == "public"

    restore_response = client.post("/auth/session/view-mode", json={"view_as_regular": False})
    assert restore_response.status_code == 200
    assert restore_response.json()["effective_is_admin"] is True

    forgot_response = client.post(
        "/auth/password/forgot",
        json={"email": "owner@example.com"},
    )
    assert forgot_response.status_code == 200
    reset_token = forgot_response.json()["reset_token_preview"]
    assert reset_token

    reset_response = client.post(
        "/auth/password/reset",
        json={
            "token": reset_token,
            "new_password": "supersecret789",
        },
    )
    assert reset_response.status_code == 200
    assert reset_response.json()["user"]["email"] == "owner@example.com"

    client.post("/auth/logout")
    final_login_response = client.post(
        "/auth/login",
        json={
            "email": "owner@example.com",
            "password": "supersecret789",
        },
    )
    assert final_login_response.status_code == 200


def test_auth_routes_support_cross_origin_session_requests():
    client = TestClient(app)
    options_response = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3030",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert options_response.status_code == 200
    assert options_response.headers["access-control-allow-origin"] == "http://localhost:3030"
    assert options_response.headers["access-control-allow-credentials"] == "true"
