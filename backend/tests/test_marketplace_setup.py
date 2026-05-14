from fastapi.testclient import TestClient

from app.main import app


def test_manual_marketplace_setup_controls_publish_readiness():
    client = TestClient(app)

    register = client.post(
        "/auth/register",
        json={
            "full_name": "Owner",
            "email": "owner@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    initial_summary = client.get(f"/users/{user_id}/setup")
    assert initial_summary.status_code == 200
    mercari = next(item for item in initial_summary.json()["marketplace_connections"] if item["marketplace"] == "mercari")
    assert mercari["connection_mode"] == "manual"
    assert mercari["can_publish"] is False
    assert mercari["can_sync_sales"] is False

    blocked_publish = client.put(
        f"/users/{user_id}/platform-config",
        json={"marketplaces": ["mercari"]},
    )
    assert blocked_publish.status_code == 400
    assert "incomplete" in blocked_publish.json()["detail"].lower()

    manual_setup = client.put(
        f"/users/{user_id}/marketplace-connections/mercari",
        json={
            "display_name": "Sparkles Mercari",
            "account_handle": "@sparkles",
            "notes": "Primary manual channel",
            "workflow_state": "ready",
        },
    )
    assert manual_setup.status_code == 200
    manual_data = manual_setup.json()
    assert manual_data["connected"] is True
    assert manual_data["can_publish"] is True

    enabled_publish = client.put(
        f"/users/{user_id}/platform-config",
        json={"marketplaces": ["mercari"]},
    )
    assert enabled_publish.status_code == 200
    assert enabled_publish.json()["enabled_platforms"] == ["mercari"]

    blocked_sales = client.put(
        f"/sales/settings/{user_id}",
        json={"marketplaces": ["mercari"]},
    )
    assert blocked_sales.status_code == 400
    assert "not available" in blocked_sales.json()["detail"].lower()

    final_summary = client.get(f"/users/{user_id}/setup")
    assert final_summary.status_code == 200
    mercari_final = next(item for item in final_summary.json()["marketplace_connections"] if item["marketplace"] == "mercari")
    assert mercari_final["enabled_for_publishing"] is True
    assert mercari_final["display_name"] == "Sparkles Mercari"
    assert mercari_final["account_handle"] == "@sparkles"
