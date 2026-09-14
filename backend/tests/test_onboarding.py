from __future__ import annotations

from uuid import uuid4

import pytest

from app.core import config as config_module
from app.core.secrets import decrypt_secret_if_needed
from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, MarketplaceExtensionDevice, MarketplaceExtensionJob, User
from app.services.ai_entitlements import resolve_openai_key, sponsored_ai_entitlement
from app.services.onboarding_service import onboarding_snapshot


def test_sponsored_ai_requires_server_enabled_active_subscription_and_explicit_grant(db_session, monkeypatch):
    user = User(email=f"setup-entitlement-{uuid4()}@example.com", role="owner", settings_json={
        "subscription": {"status": "active", "entitlements": {"sponsored_ai": True, "ai_monthly_tokens": 5000}},
    })
    db_session.add(user); db_session.flush()
    monkeypatch.setattr(config_module.settings, "sponsored_ai_enabled", False)
    assert sponsored_ai_entitlement(user)["entitled"] is False
    monkeypatch.setattr(config_module.settings, "sponsored_ai_enabled", True)
    assert sponsored_ai_entitlement(user)["entitled"] is False
    monkeypatch.setattr(config_module.settings, "sponsored_ai_metering_ready", True)
    assert sponsored_ai_entitlement(user)["entitled"] is True
    user.settings_json["subscription"]["status"] = "past_due"
    assert sponsored_ai_entitlement(user)["entitled"] is False


def test_openai_key_resolution_is_tenant_scoped_and_managed_is_not_self_activated(db_session, monkeypatch):
    from app.core import secrets

    monkeypatch.setattr(config_module.settings, "openai_api_key_plain", "platform-secret")
    monkeypatch.setattr(config_module.settings, "openai_api_key_enc", None)
    owner = User(email=f"setup-owner-{uuid4()}@example.com", role="owner", is_admin=False)
    owner_key = secrets.encrypt_secret("tenant-owner-secret", secret_key=config_module.settings.session_secret)
    owner.settings_json = {"ai_provider": {"mode": "BYO_OPENAI", "openai_api_key_enc": owner_key}}
    other = User(email=f"setup-other-{uuid4()}@example.com", role="owner", is_admin=False, settings_json={})
    db_session.add_all([owner, other]); db_session.flush()

    assert resolve_openai_key(db_session, owner.id) == ("tenant-owner-secret", "byo_openai")
    assert resolve_openai_key(db_session, other.id) == (None, "disabled")
    other.settings_json = {"ai_provider": {"mode": "POSTERPRO_SPONSORED"}}
    assert resolve_openai_key(db_session, other.id) == (None, "disabled")


def test_onboarding_is_resumable_and_marketplace_status_is_not_assumed_ready(db_session):
    user = User(email=f"setup-state-{uuid4()}@example.com", role="owner", settings_json={
        "guided_onboarding_v1": {"started_at": "2026-09-14T12:00:00+00:00", "current_step": "ai", "selected_marketplaces": ["facebook"], "marketplace_choice_saved": True},
    })
    db_session.add(user); db_session.flush()
    snapshot = onboarding_snapshot(user, db_session)
    by_id = {task["id"]: task for task in snapshot["tasks"]}
    assert snapshot["current_step"] == "ai"
    assert snapshot["selected_marketplaces"] == ["facebook"]
    assert by_id["marketplace:facebook"]["status"] == "EXTENSION_REQUIRED"
    assert "not tested" in by_id["marketplace:facebook"]["message"]
    assert by_id["browser_extension"]["status"] == "EXTENSION_REQUIRED"
    assert by_id["ai"]["status"] == "NOT_CONFIGURED"
    assert by_id["marketplace:facebook"]["guidance"]["open_url"] == "https://www.facebook.com/marketplace/"
    assert any("form test" in step.lower() for step in by_id["marketplace:facebook"]["guidance"]["steps"])
    assert snapshot["completed"] is False


def test_onboarding_assisted_marketplace_requires_passed_live_diagnostic(db_session):
    from datetime import UTC, datetime

    user = User(email=f"setup-diagnostic-{uuid4()}@example.com", role="owner", settings_json={
        "guided_onboarding_v1": {"started_at": "2026-09-14T12:00:00+00:00", "selected_marketplaces": ["facebook"], "marketplace_choice_saved": True},
    })
    db_session.add(user); db_session.flush()
    device = MarketplaceExtensionDevice(user_id=user.id, device_key=f"device-{uuid4()}", token_hash=f"hash-{uuid4()}", extension_version="0.3.0", last_seen_at=datetime.now(UTC).replace(tzinfo=None))
    db_session.add(device); db_session.flush()
    snapshot = onboarding_snapshot(user, db_session)
    task = next(row for row in snapshot["tasks"] if row["id"] == "marketplace:facebook")
    assert task["status"] == "OPERATOR_TEST_REQUIRED"

    job = MarketplaceExtensionJob(user_id=user.id, marketplace="facebook", action="DIAGNOSTIC", status="COMPLETED", payload_version=1, payload_snapshot={"diagnostic": True}, result={"capability_ready": True, "field_results": [{"field": "category", "required": True, "detected": True, "attempted": True, "filled": True}]})
    db_session.add(job); db_session.flush()
    task = next(row for row in onboarding_snapshot(user, db_session)["tasks"] if row["id"] == "marketplace:facebook")
    assert task["status"] == "CONNECTED"
    assert "diagnostic passed" in task["message"].lower()

    job.status = "COMPLETED"
    job.result = {"capability_ready": False, "missing_required_fields": ["condition"], "field_results": [{"field": "condition", "required": True, "detected": False, "attempted": True, "filled": False, "error_code": "FIELD_NOT_FOUND"}]}
    db_session.flush()
    task = next(row for row in onboarding_snapshot(user, db_session)["tasks"] if row["id"] == "marketplace:facebook")
    assert task["status"] == "NEEDS_ATTENTION"
    assert task["missing_required_fields"] == ["condition"]


def test_google_photos_expired_token_is_reported_without_refreshing_credentials(db_session, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from app.core.secrets import encrypt_secret
    import app.services.onboarding_service as onboarding_service

    user = User(email=f"google-expired-{uuid4()}@example.com", role="owner", settings_json={
        "guided_onboarding_v1": {"started_at": "2026-09-14T12:00:00+00:00"},
        "google_photos_oauth": {
            "connected": True,
            "access_token_enc": encrypt_secret("saved-access-token", secret_key=config_module.settings.session_secret),
            "refresh_token_enc": encrypt_secret("saved-refresh-token", secret_key=config_module.settings.session_secret),
            "token_expires_at": (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat(),
        },
    })
    db_session.add(user); db_session.flush()
    monkeypatch.setattr(onboarding_service, "google_photos_oauth_ready", lambda: True)
    before = user.settings_json["google_photos_oauth"]["access_token_enc"]
    task = next(task for task in onboarding_service.onboarding_snapshot(user, db_session)["tasks"] if task["id"] == "google_photos")
    assert task["status"] == "EXPIRED"
    assert "will not change" in task["message"]
    assert user.settings_json["google_photos_oauth"]["access_token_enc"] == before
    assert task["guidance"]["steps"]


def test_ebay_connection_is_not_publish_ready_until_seller_policies_and_location_are_verified(db_session):
    from datetime import UTC, datetime

    user = User(email=f"setup-ebay-state-{uuid4()}@example.com", role="owner", settings_json={
        "guided_onboarding_v1": {"started_at": "2026-09-14T12:00:00+00:00", "selected_marketplaces": ["ebay"], "marketplace_choice_saved": True},
    })
    db_session.add(user); db_session.flush()
    account = MarketplaceAccount(user_id=user.id, marketplace=MarketplaceName.ebay, external_account_id="seller-1", access_token="access", refresh_token="refresh", connection_status="connected", last_successful_check_at=datetime.now(UTC).replace(tzinfo=None))
    db_session.add(account); db_session.flush()
    state = onboarding_snapshot(user, db_session)
    assert next(task for task in state["tasks"] if task["id"] == "marketplace:ebay")["status"] == "CONNECTED_BUT_NEEDS_ATTENTION"

    user.settings_json = {"guided_onboarding_v1": {"started_at": "2026-09-14T12:00:00+00:00", "selected_marketplaces": ["ebay"], "marketplace_choice_saved": True}, "ebay_marketplace_policy_settings": {
        "payment_policy_id": "p-1", "fulfillment_policy_id": "f-1", "return_policy_id": "r-1",
        "merchant_location_key": "loc-1", "merchant_location_verified": True,
    }}
    state = onboarding_snapshot(user, db_session)
    assert next(task for task in state["tasks"] if task["id"] == "marketplace:ebay")["status"] == "CONNECTED"


@pytest.mark.anyio
async def test_onboarding_progress_is_user_scoped_and_none_destination_choice_persists(async_client):
    first_email = f"setup-first-{uuid4()}@example.com"
    first = await async_client.post("/auth/register", json={"full_name": "First Setup", "email": first_email, "password": "supersecret123"})
    assert first.status_code == 201
    first_user_id = first.json()["user"]["id"]
    selected = await async_client.put("/onboarding/selection", json={"marketplaces": ["facebook", "ebay"]})
    assert selected.status_code == 200
    assert selected.json()["selected_marketplaces"] == ["facebook", "ebay"]
    assert all("api_key" not in str(task).lower() for task in selected.json()["tasks"])

    second_email = f"setup-second-{uuid4()}@example.com"
    second = await async_client.post("/auth/register", json={"full_name": "Second Setup", "email": second_email, "password": "supersecret123"})
    assert second.status_code == 201
    empty_choice = await async_client.put("/onboarding/selection", json={"marketplaces": []})
    assert empty_choice.status_code == 200
    assert empty_choice.json()["selected_marketplaces"] == []
    own = await async_client.get("/onboarding/state")
    assert own.json()["selected_marketplaces"] == []

    first_login = await async_client.post("/auth/login", json={"email": first_email, "password": "supersecret123"})
    assert first_login.status_code == 200
    first_state = await async_client.get("/onboarding/state")
    assert first_state.json()["selected_marketplaces"] == ["facebook", "ebay"]
    assert first_user_id != second.json()["user"]["id"]


@pytest.mark.anyio
async def test_byo_openai_test_saves_encrypted_key_without_echoing_it(async_client, monkeypatch):
    import app.api.onboarding as onboarding_api

    class GoodResponse:
        status_code = 200
        is_error = False

    class GoodClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, _url, *, headers, json):
            assert headers["Authorization"] == "Bearer sk-test-tenant-key-123456"
            assert json["model"] == "gpt-4o-mini"
            return GoodResponse()

    monkeypatch.setattr(onboarding_api.httpx, "AsyncClient", GoodClient)
    register = await async_client.post("/auth/register", json={"full_name": "BYO AI", "email": f"setup-ai-{uuid4()}@example.com", "password": "supersecret123"})
    assert register.status_code == 201
    result = await async_client.post("/onboarding/ai/test", json={"api_key": "sk-test-tenant-key-123456"})
    assert result.status_code == 200
    assert result.json()["state"] == "CONNECTED"
    assert "sk-test-tenant-key-123456" not in result.text
    from app.core.database import SessionLocal
    from app.models.models import User
    db = SessionLocal()
    try:
        db_session_user = db.query(User).filter(User.email == register.json()["user"]["email"]).one()
        config = db_session_user.settings_json["ai_provider"]
        assert config["openai_api_key_enc"] != "sk-test-tenant-key-123456"
        assert decrypt_secret_if_needed(config["openai_api_key_enc"], secret_key=onboarding_api.settings.session_secret) == "sk-test-tenant-key-123456"
    finally:
        db.close()
    verified = await async_client.post("/onboarding/verify/ai")
    assert verified.status_code == 200
    assert verified.json()["verification"]["level"] == "LIVE_PROVIDER_TEST"


@pytest.mark.anyio
async def test_managed_ai_is_truthfully_coming_soon_without_entitlement(async_client, monkeypatch):
    register = await async_client.post("/auth/register", json={"full_name": "Managed AI", "email": f"setup-managed-{uuid4()}@example.com", "password": "supersecret123"})
    assert register.status_code == 201
    result = await async_client.post("/onboarding/ai/mode", json={"mode": "POSTERPRO_SPONSORED"})
    assert result.status_code == 200
    assert result.json()["status"] == "COMING_SOON"
    assert result.json()["activated"] is False
    tracked = await async_client.post("/onboarding/event", json={"event": "PLAN_VIEWED_FROM_AI_SETUP", "task_id": "ai"})
    assert tracked.status_code == 200


@pytest.mark.anyio
async def test_user_can_skip_ai_without_falsely_marking_marketplace_readiness(async_client):
    register = await async_client.post("/auth/register", json={"full_name": "Skip AI", "email": f"setup-skip-ai-{uuid4()}@example.com", "password": "supersecret123"})
    assert register.status_code == 201
    selected = await async_client.put("/onboarding/selection", json={"marketplaces": []})
    assert selected.status_code == 200
    chosen = await async_client.post("/onboarding/ai/mode", json={"mode": "DISABLED"})
    assert chosen.status_code == 200
    state = await async_client.get("/onboarding/state")
    tasks = {task["id"]: task for task in state.json()["tasks"]}
    assert tasks["ai"]["status"] == "SKIPPED"
    assert tasks["ai"]["required"] is False
    assert tasks["test_workflow"]["status"] == "READY"
    assert state.json()["completed"] is True


@pytest.mark.anyio
async def test_ebay_onboarding_verification_runs_read_only_seller_api_check(async_client, db_session, monkeypatch):
    import app.api.onboarding as onboarding_api
    from app.models.enums import MarketplaceName
    from app.models.models import MarketplaceAccount

    register = await async_client.post("/auth/register", json={"full_name": "eBay Setup", "email": f"setup-ebay-{uuid4()}@example.com", "password": "supersecret123"})
    assert register.status_code == 201
    account = MarketplaceAccount(user_id=register.json()["user"]["id"], marketplace=MarketplaceName.ebay, external_account_id="test-ebay-account", access_token="not-a-real-token", refresh_token="not-a-real-refresh-token", connection_status="connected")
    db_session.add(account); db_session.commit()
    async def get_account(user_id, db):
        assert user_id == account.user_id
        return db.query(MarketplaceAccount).filter(MarketplaceAccount.user_id == user_id, MarketplaceAccount.marketplace == MarketplaceName.ebay).one()
    async def check_policies(user_id, db, checked_account, *, marketplace_id):
        assert user_id == account.user_id and checked_account.user_id == account.user_id and marketplace_id == "EBAY_US"
        return []
    monkeypatch.setattr(onboarding_api, "get_or_refresh_account", get_account)
    monkeypatch.setattr(onboarding_api, "_list_business_policies_for_account", check_policies)
    await async_client.put("/onboarding/selection", json={"marketplaces": ["ebay"]})
    result = await async_client.post("/onboarding/verify/marketplace:ebay")
    assert result.status_code == 200
    assert result.json()["verification"]["level"] == "LIVE_SELL_API_READ"
    assert "No listing was created or changed" in result.json()["verification"]["message"]
    db_session.refresh(account)
    assert account.last_successful_check_at is not None


@pytest.mark.anyio
async def test_contextual_onboarding_help_redacts_credentials_and_uses_only_current_step(db_session, monkeypatch):
    import app.api.onboarding as onboarding_api

    user = User(email=f"setup-help-{uuid4()}@example.com", role="owner", settings_json={
        "guided_onboarding_v1": {"started_at": "2026-09-14T12:00:00+00:00", "selected_marketplaces": ["facebook"], "marketplace_choice_saved": True},
        "ai_provider": {"mode": "BYO_OPENAI"},
    })
    db_session.add(user); db_session.flush()
    calls = []

    class Response:
        is_error = False
        def json(self):
            return {"output": [{"content": [{"type": "output_text", "text": "Open Facebook in the paired browser, sign in there, then return here."}]}]}

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, _url, *, headers, json):
            calls.append((headers, json))
            return Response()

    monkeypatch.setattr(onboarding_api, "resolve_openai_key", lambda _db, _user_id: ("tenant-private-key", "byo_openai"))
    monkeypatch.setattr(onboarding_api.httpx, "AsyncClient", Client)
    result = await onboarding_api.onboarding_help(
        onboarding_api.OnboardingHelpRequest(task_id="marketplace:facebook", question="I pasted sk-thisisaverylongsecret1234 and I am confused"),
        db_session,
        user,
    )
    assert result["ai_assisted"] is True
    assert result["secrets_sent"] is False
    assert result["answer"].startswith("Open Facebook")
    sent = calls[0][1]["input"]
    assert "sk-thisisaverylongsecret1234" not in sent
    assert "[credential removed]" in sent
    assert "tenant-private-key" not in str(calls[0][1])


@pytest.mark.anyio
async def test_contextual_onboarding_help_falls_back_to_saved_guidance_without_ai(db_session, monkeypatch):
    import app.api.onboarding as onboarding_api

    user = User(email=f"setup-help-fallback-{uuid4()}@example.com", role="owner", settings_json={
        "guided_onboarding_v1": {"started_at": "2026-09-14T12:00:00+00:00", "selected_marketplaces": ["mercari"], "marketplace_choice_saved": True},
    })
    db_session.add(user); db_session.flush()
    monkeypatch.setattr(onboarding_api, "resolve_openai_key", lambda _db, _user_id: (None, "disabled"))
    result = await onboarding_api.onboarding_help(
        onboarding_api.OnboardingHelpRequest(task_id="marketplace:mercari", question="I am signed out"),
        db_session,
        user,
    )
    assert result["ai_assisted"] is False
    assert "sign-in" in result["answer"].lower()
    assert result["secrets_sent"] is False
