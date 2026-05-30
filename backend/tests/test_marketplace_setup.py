import pytest

@pytest.mark.anyio
async def test_manual_marketplace_setup_controls_publish_readiness(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Owner",
            "email": "owner@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    initial_summary = await async_client.get(f"/users/{user_id}/setup")
    assert initial_summary.status_code == 200
    mercari = next(item for item in initial_summary.json()["marketplace_connections"] if item["marketplace"] == "mercari")
    assert mercari["connection_mode"] == "manual"
    assert mercari["can_publish"] is False
    assert mercari["can_sync_sales"] is False

    blocked_publish = await async_client.put(
        f"/users/{user_id}/platform-config",
        json={"marketplaces": ["mercari"]},
    )
    assert blocked_publish.status_code == 400
    assert "incomplete" in blocked_publish.json()["detail"].lower()

    manual_setup = await async_client.put(
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

    enabled_publish = await async_client.put(
        f"/users/{user_id}/platform-config",
        json={"marketplaces": ["mercari"]},
    )
    assert enabled_publish.status_code == 200
    assert enabled_publish.json()["enabled_platforms"] == ["mercari"]

    blocked_sales = await async_client.put(
        f"/sales/settings/{user_id}",
        json={"marketplaces": ["mercari"]},
    )
    assert blocked_sales.status_code == 400
    assert "not available" in blocked_sales.json()["detail"].lower()

    final_summary = await async_client.get(f"/users/{user_id}/setup")
    assert final_summary.status_code == 200
    mercari_final = next(item for item in final_summary.json()["marketplace_connections"] if item["marketplace"] == "mercari")
    assert mercari_final["enabled_for_publishing"] is True
    assert mercari_final["display_name"] == "Sparkles Mercari"
    assert mercari_final["account_handle"] == "@sparkles"


@pytest.mark.anyio
async def test_manual_marketplace_setup_uses_profile_defaults_before_save(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Owner Defaults",
            "email": "owner-defaults@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    summary = await async_client.get(f"/users/{user_id}/setup")
    assert summary.status_code == 200

    etsy = next(item for item in summary.json()["marketplace_connections"] if item["marketplace"] == "etsy")
    depop = next(item for item in summary.json()["marketplace_connections"] if item["marketplace"] == "depop")

    assert etsy["import_mode"] == "csv_assist"
    assert etsy["publish_mode"] == "browser_assist"
    assert etsy["shipping_scope"] == "shipping_only"

    assert depop["import_mode"] == "provider_assist"
    assert depop["publish_mode"] == "provider_assist"
    assert depop["shipping_scope"] == "shipping_only"


@pytest.mark.anyio
async def test_manual_marketplace_setup_persists_bridge_account_key_only(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Owner Bridge",
            "email": "owner-bridge@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    save = await async_client.put(
        f"/users/{user_id}/marketplace-connections/mercari",
        json={
            "bridge_account_key": "mercari-main",
            "publish_mode": "browser_assist",
            "workflow_state": "draft",
        },
    )
    assert save.status_code == 200
    saved = save.json()
    assert saved["bridge_account_key"] == "mercari-main"
    assert saved["publish_mode"] == "browser_assist"

    summary = await async_client.get(f"/users/{user_id}/setup")
    assert summary.status_code == 200
    mercari = next(item for item in summary.json()["marketplace_connections"] if item["marketplace"] == "mercari")
    assert mercari["bridge_account_key"] == "mercari-main"
    assert mercari["publish_mode"] == "browser_assist"
