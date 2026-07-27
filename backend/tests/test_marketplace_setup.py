import pytest

from app.core.database import SessionLocal
from app.models.models import Cluster, Listing, User
from app.services.marketplace_execution import resolve_execution_mode


def _seed_listing(user_id: int) -> int:
    with SessionLocal() as db:
        cluster = Cluster(user_id=user_id, title_hint="Test Item")
        db.add(cluster)
        db.flush()
        listing = Listing(user_id=user_id, cluster_id=cluster.id, title="Test Item", description="Test listing")
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return listing.id


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


@pytest.mark.anyio
async def test_ready_facebook_browser_channel_is_inferred_into_enabled_platforms(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Operator",
            "email": "operator-facebook@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    configured = await async_client.put(
        f"/users/{user_id}/marketplace-connections/facebook",
        json={
            "display_name": "Facebook Marketplace",
            "account_handle": "mattysparkles",
            "notes": "Browser-assisted seller channel",
            "workflow_state": "ready",
            "import_mode": "browser_assist",
            "publish_mode": "browser_assist",
            "bridge_account_key": "facebook-main",
        },
    )
    assert configured.status_code == 200
    assert configured.json()["connected"] is True

    from app.core.database import SessionLocal
    from app.models.models import User

    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.enabled_platforms = None
        db.add(user)
        db.commit()

    platform_config = await async_client.get(f"/users/{user_id}/platform-config")
    assert platform_config.status_code == 200
    assert platform_config.json()["enabled_platforms"] == ["ebay", "facebook"]

    setup_summary = await async_client.get(f"/users/{user_id}/setup")
    assert setup_summary.status_code == 200
    facebook = next(item for item in setup_summary.json()["marketplace_connections"] if item["marketplace"] == "facebook")
    assert facebook["enabled_for_publishing"] is True


@pytest.mark.anyio
async def test_facebook_saved_browser_assist_overrides_manual_or_provider_listing_default(async_client):
    user = User(
        settings_json={
            "marketplace_connections": {
                "facebook": {
                    "publish_mode": "browser_assist",
                    "workflow_state": "ready",
                    "bridge_account_key": "facebook-main",
                }
            }
        }
    )
    listing = Listing(
        marketplace_data={
            "channels": {
                "facebook": {
                    "publish_mode": "manual_or_provider",
                    "enabled": True,
                }
            }
        }
    )

    assert resolve_execution_mode(listing=listing, user=user, marketplace="facebook") == "browser_assist"
