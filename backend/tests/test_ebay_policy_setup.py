from uuid import uuid4

import pytest

from app.core import database as database_module
from app.api import marketplaces as marketplaces_api
from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount


async def _register_user(async_client, prefix: str = "policy") -> int:
    response = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Policy Owner",
            "email": f"{prefix}-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert response.status_code == 201
    return response.json()["user"]["id"]


def _connect_ebay_account(user_id: int, *, access_token: str = "token", refresh_token: str = "refresh-token", token_expires_minutes: int = 60) -> None:
    db = database_module.SessionLocal()
    try:
        account = MarketplaceAccount(
            user_id=user_id,
            marketplace=MarketplaceName.ebay,
            external_account_id=f"ebay-{user_id}",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=None,
        )
        db.add(account)
        db.commit()
    finally:
        db.close()


def _load_ebay_policy_settings(user_id: int) -> dict:
    db = database_module.SessionLocal()
    try:
        user = db.get(marketplaces_api.User, user_id)
        settings = user.settings_json if user and isinstance(user.settings_json, dict) else {}
        raw = settings.get("ebay_marketplace_policy_settings") if isinstance(settings, dict) else {}
        return raw if isinstance(raw, dict) else {}
    finally:
        db.close()


@pytest.mark.anyio
async def test_ebay_policy_sync_maps_ids_and_names_into_settings_json(async_client, monkeypatch):
    user_id = await _register_user(async_client, "policy-sync")
    _connect_ebay_account(user_id)

    async def fake_sync(*_args, **_kwargs):
        return {
            "status": "updated",
            "marketplace_id": "EBAY_US",
            "policy_catalog": {
                "payment_policies": [{"id": "pay-1", "name": "Payment One"}],
                "fulfillment_policies": [{"id": "ful-1", "name": "Fulfillment One"}],
                "return_policies": [{"id": "ret-1", "name": "Return One"}],
            },
            "selected": {
                "payment_policy_id": "pay-1",
                "payment_policy_name": "Payment One",
                "fulfillment_policy_id": "ful-1",
                "fulfillment_policy_name": "Fulfillment One",
                "return_policy_id": "ret-1",
                "return_policy_name": "Return One",
            },
            "missing_policy_types": [],
            "settings_updates": {
                "marketplace_id": "EBAY_US",
                "payment_policy_id": "pay-1",
                "payment_policy_name": "Payment One",
                "fulfillment_policy_id": "ful-1",
                "fulfillment_policy_name": "Fulfillment One",
                "return_policy_id": "ret-1",
                "return_policy_name": "Return One",
                "policy_sync_status": "synced",
                "policy_sync_error": "",
                "last_policy_sync_at": "2026-06-09T12:00:00",
                "policy_candidates": {
                    "payment": [{"id": "pay-1", "name": "Payment One"}],
                    "fulfillment": [{"id": "ful-1", "name": "Fulfillment One"}],
                    "return": [{"id": "ret-1", "name": "Return One"}],
                },
            },
        }

    monkeypatch.setattr(marketplaces_api, "sync_business_policies", fake_sync)

    response = await async_client.post(
        "/marketplaces/ebay/policies/sync",
        json={"marketplace_id": "EBAY_US", "create_missing_defaults": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "updated"
    assert payload["selected"]["payment_policy_id"] == "pay-1"
    assert "access_token" not in payload
    assert "refresh_token" not in payload

    settings = _load_ebay_policy_settings(user_id)
    assert settings["payment_policy_id"] == "pay-1"
    assert settings["fulfillment_policy_name"] == "Fulfillment One"
    assert settings["return_policy_id"] == "ret-1"
    assert settings["policy_sync_status"] == "synced"


@pytest.mark.anyio
async def test_ebay_policy_sync_failure_preserves_existing_settings(async_client, monkeypatch):
    user_id = await _register_user(async_client, "policy-failure")
    _connect_ebay_account(user_id)

    save = await async_client.patch(
        "/auth/me",
        json={
            "ebay_marketplace_policy_settings": {
                "payment_policy_id": "existing-pay",
                "fulfillment_policy_id": "existing-ful",
                "return_policy_id": "existing-ret",
                "merchant_location_key": "posterpro-3",
            }
        },
    )
    assert save.status_code == 200

    async def fake_sync(*_args, **_kwargs):
        return {
            "status": "blocked",
            "marketplace_id": "EBAY_US",
            "missing_policy_types": ["payment", "fulfillment"],
            "policy_catalog": {"payment_policies": [], "fulfillment_policies": [], "return_policies": []},
            "settings_updates": {
                "policy_sync_status": "blocked",
                "policy_sync_error": "Missing eBay policies: payment, fulfillment",
                "policy_candidates": {"payment": [], "fulfillment": [], "return": []},
            },
        }

    monkeypatch.setattr(marketplaces_api, "sync_business_policies", fake_sync)

    response = await async_client.post(
        "/marketplaces/ebay/policies/sync",
        json={"marketplace_id": "EBAY_US", "create_missing_defaults": False},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "blocked"

    settings = _load_ebay_policy_settings(user_id)
    assert settings["payment_policy_id"] == "existing-pay"
    assert settings["fulfillment_policy_id"] == "existing-ful"
    assert settings["return_policy_id"] == "existing-ret"
    assert settings["policy_sync_status"] in {"blocked", "uninitialized"}
    assert settings["policy_sync_error"] in {"", "Missing eBay policies: payment, fulfillment"}


@pytest.mark.anyio
async def test_ebay_policy_catalog_returns_candidate_lists(async_client, monkeypatch):
    user_id = await _register_user(async_client, "policy-catalog")
    _connect_ebay_account(user_id)

    async def fake_list(*_args, **_kwargs):
        return {
            "marketplace_id": "EBAY_US",
            "payment_policies": [{"id": "pay-1", "name": "Payment One", "is_default": True, "category_types": ["ALL_EXCLUDING_MOTORS_VEHICLES"], "raw_category_types": []}],
            "fulfillment_policies": [{"id": "ful-1", "name": "Fulfillment One", "is_default": True, "category_types": ["ALL_EXCLUDING_MOTORS_VEHICLES"], "raw_category_types": []}],
            "return_policies": [{"id": "ret-1", "name": "Return One", "is_default": True, "category_types": ["ALL_EXCLUDING_MOTORS_VEHICLES"], "raw_category_types": []}],
            "selected": {"payment_policy_id": "pay-1", "fulfillment_policy_id": "ful-1", "return_policy_id": "ret-1"},
        }

    monkeypatch.setattr(marketplaces_api, "list_business_policies", fake_list)

    response = await async_client.get("/marketplaces/ebay/policies")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["payment_policies"][0]["id"] == "pay-1"
    assert payload["fulfillment_policies"][0]["name"] == "Fulfillment One"
    assert payload["return_policies"][0]["is_default"] is True
    assert "access_token" not in payload
    assert "refresh_token" not in payload


@pytest.mark.anyio
async def test_manual_policy_selection_persists(async_client):
    await _register_user(async_client, "policy-select")

    response = await async_client.post(
        "/marketplaces/ebay/policies/select",
        json={
            "marketplace_id": "EBAY_US",
            "payment_policy_id": "manual-pay",
            "payment_policy_name": "Manual Payment",
            "fulfillment_policy_id": "manual-ful",
            "fulfillment_policy_name": "Manual Fulfillment",
            "return_policy_id": "manual-ret",
            "return_policy_name": "Manual Returns",
        },
    )
    assert response.status_code == 200

    me = await async_client.get("/auth/me")
    settings = me.json()["ebay_marketplace_policy_settings"]
    assert settings["payment_policy_id"] == "manual-pay"
    assert settings["payment_policy_name"] == "Manual Payment"
    assert settings["fulfillment_policy_id"] == "manual-ful"
    assert settings["return_policy_name"] == "Manual Returns"
    assert settings["policy_sync_status"] == "selected_manual"


@pytest.mark.anyio
async def test_merchant_location_verify_persists_key(async_client, monkeypatch):
    await _register_user(async_client, "location-verify")

    async def fake_verify(*_args, **_kwargs):
        return {
            "status": "verified",
            "marketplace_id": "EBAY_US",
            "merchant_location_key": "posterpro-3",
            "settings_updates": {
                "merchant_location_key": "posterpro-3",
                "merchant_location_verified": True,
                "merchant_location_status": "verified",
                "merchant_location_last_checked_at": "2026-06-09T12:00:00",
                "merchant_location_error": "",
            },
        }

    monkeypatch.setattr(marketplaces_api, "verify_merchant_location", fake_verify)

    response = await async_client.post(
        "/marketplaces/ebay/location/verify",
        json={"merchant_location_key": "posterpro-3", "create_if_missing": False},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "verified"

    me = await async_client.get("/auth/me")
    settings = me.json()["ebay_marketplace_policy_settings"]
    assert settings["merchant_location_key"] == "posterpro-3"
    assert settings["merchant_location_verified"] is True
    assert settings["merchant_location_status"] == "verified"


@pytest.mark.anyio
async def test_merchant_location_create_blocks_when_origin_fields_missing(async_client, monkeypatch):
    await _register_user(async_client, "location-create")

    async def fake_verify(*_args, **kwargs):
        assert kwargs["create_if_missing"] is True
        return {
            "status": "blocked",
            "marketplace_id": "EBAY_US",
            "merchant_location_key": "posterpro-3",
            "missing_fields": ["merchant_location_postal_code", "merchant_location_country"],
            "error": "Missing origin fields: merchant_location_postal_code, merchant_location_country",
            "settings_updates": {
                "merchant_location_key": "posterpro-3",
                "merchant_location_verified": False,
                "merchant_location_status": "blocked",
                "merchant_location_error": "Missing origin fields: merchant_location_postal_code, merchant_location_country",
            },
        }

    monkeypatch.setattr(marketplaces_api, "verify_merchant_location", fake_verify)

    response = await async_client.post(
        "/marketplaces/ebay/location/create",
        json={"merchant_location_key": "posterpro-3", "create_if_missing": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert "merchant_location_postal_code" in payload["missing_fields"]


@pytest.mark.anyio
async def test_ebay_account_readiness_reflects_policy_and_location_state(async_client):
    user_id = await _register_user(async_client, "readiness")
    _connect_ebay_account(user_id)

    partial = await async_client.patch(
        "/auth/me",
        json={
            "ebay_marketplace_policy_settings": {
                "payment_policy_id": "pay-1",
                "fulfillment_policy_id": "ful-1",
                "return_policy_id": "",
                "merchant_location_key": "posterpro-3",
            }
        },
    )
    assert partial.status_code == 200

    readiness = await async_client.get("/marketplaces/ebay/account-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["publish_ready"] is False
    assert readiness.json()["policies_present"] is False

    complete = await async_client.patch(
        "/auth/me",
        json={
            "ebay_marketplace_policy_settings": {
                "payment_policy_id": "pay-1",
                "fulfillment_policy_id": "ful-1",
                "return_policy_id": "ret-1",
                "merchant_location_key": "posterpro-3",
                "merchant_location_verified": True,
                "merchant_location_status": "verified",
            }
        },
    )
    assert complete.status_code == 200

    readiness = await async_client.get("/marketplaces/ebay/account-readiness")
    assert readiness.status_code == 200
    payload = readiness.json()
    assert payload["policies_present"] is True
    assert payload["location_present"] is True
    assert payload["publish_ready"] is True


@pytest.mark.anyio
async def test_policy_refresh_remains_compatible(async_client, monkeypatch):
    await _register_user(async_client, "policy-refresh")

    async def fake_sync(*_args, **_kwargs):
        return {
            "status": "updated",
            "marketplace_id": "EBAY_US",
            "policy_catalog": {"payment_policies": [], "fulfillment_policies": [], "return_policies": []},
            "selected": {},
            "missing_policy_types": [],
            "settings_updates": {
                "policy_sync_status": "synced",
                "policy_sync_error": "",
                "policy_candidates": {"payment": [], "fulfillment": [], "return": []},
            },
        }

    monkeypatch.setattr(marketplaces_api, "sync_business_policies", fake_sync)

    response = await async_client.post("/marketplaces/ebay/policies/refresh")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "updated"
    assert "ebay_marketplace_policy_settings" in payload
