from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

import app.main as bridge_main


def test_connect_session_store_reuses_same_active_session_for_same_account(tmp_path: Path):
    store = bridge_main.ConnectSessionStore(tmp_path)
    store.executor.submit = lambda *args, **kwargs: None

    payload = bridge_main.BridgeAccountConnectRequest(login_handle="seller@example.com", wait_timeout_seconds=90)
    first = store.create_session(
        marketplace="facebook",
        account_key="facebook-main",
        bridge_account={"display_name": "Facebook Main"},
        payload=payload,
    )
    second = store.create_session(
        marketplace="facebook",
        account_key="facebook-main",
        bridge_account={"display_name": "Facebook Main"},
        payload=payload,
    )

    assert second["connect_session_id"] == first["connect_session_id"]
    assert second["status"] == "queued"


def test_connect_session_store_blocks_different_active_account(tmp_path: Path):
    store = bridge_main.ConnectSessionStore(tmp_path)
    store.executor.submit = lambda *args, **kwargs: None

    payload = bridge_main.BridgeAccountConnectRequest(login_handle="seller@example.com", wait_timeout_seconds=90)
    store.create_session(
        marketplace="facebook",
        account_key="facebook-main",
        bridge_account={"display_name": "Facebook Main"},
        payload=payload,
    )

    try:
        store.create_session(
            marketplace="mercari",
            account_key="mercari-main",
            bridge_account={"display_name": "Mercari Main"},
            payload=payload,
        )
    except bridge_main.ActiveConnectSessionError as exc:
        assert "already running" in str(exc)
    else:
        raise AssertionError("Expected an active connect-session conflict")


def test_connect_session_desktop_frame_requires_active_session(monkeypatch):
    monkeypatch.setattr(
        bridge_main.connect_session_store,
        "get_session",
        lambda _connect_session_id: {
            "connect_session_id": "connect-complete-1",
            "marketplace": "facebook",
            "account_key": "facebook-main",
            "status": "completed",
        },
    )

    try:
        bridge_main.get_connect_session_desktop_frame("connect-complete-1")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "no longer active" in str(exc.detail).lower()
    else:
        raise AssertionError("Expected completed connect sessions to reject desktop-frame access")


def test_connect_session_desktop_actions_validate_payload(monkeypatch):
    monkeypatch.setattr(
        bridge_main.connect_session_store,
        "get_session",
        lambda _connect_session_id: {
            "connect_session_id": "connect-active-1",
            "marketplace": "facebook",
            "account_key": "facebook-main",
            "status": "waiting_for_login",
        },
    )

    for call, expected_message in (
        (
            lambda: bridge_main.click_connect_session_desktop(
                "connect-active-1",
                bridge_main.BridgeDesktopActionRequest(),
            ),
            "requires x and y coordinates",
        ),
        (
            lambda: bridge_main.type_connect_session_desktop(
                "connect-active-1",
                bridge_main.BridgeDesktopActionRequest(),
            ),
            "requires text",
        ),
        (
            lambda: bridge_main.key_connect_session_desktop(
                "connect-active-1",
                bridge_main.BridgeDesktopActionRequest(),
            ),
            "requires a key",
        ),
    ):
        try:
            call()
        except HTTPException as exc:
            assert exc.status_code == 400
            assert expected_message in str(exc.detail).lower()
        else:
            raise AssertionError(f"Expected desktop action validation failure for {expected_message}")


def test_bridge_never_fabricates_external_identity_for_direct_api_action(tmp_path: Path):
    store = bridge_main.JobStore(tmp_path, default_delay_seconds=0, max_workers=1)
    try:
        job = {
            "job_id": "direct-api-test",
            "job_type": "crosspost",
            "execution_mode": "direct_api",
            "payload": {"marketplace": "ebay", "listing_id": 123, "payload": {"title": "No publish"}},
        }
        try:
            store._run_crosspost_job(job)
        except RuntimeError as exc:
            assert "must be executed by PosterPro" in str(exc)
        else:
            raise AssertionError("Bridge must not report a synthetic direct-API publication")
    finally:
        store.executor.shutdown(wait=False, cancel_futures=True)


def test_vine_gallery_recovery_requires_existing_authorized_amazon_account(tmp_path: Path, monkeypatch):
    store = bridge_main.JobStore(tmp_path, default_delay_seconds=0, max_workers=1)
    calls = []
    monkeypatch.setattr(bridge_main.settings, "browser_headless", True)
    monkeypatch.setattr(bridge_main.account_store, "list_accounts", lambda _marketplace=None: [{"account_key": "amazon-main", "browser_enabled": True, "credential_configured": True, "session_state": "ready"}])
    monkeypatch.setattr(bridge_main.account_store, "resolve_account", lambda *args: calls.append(args) or (_ for _ in ()).throw(RuntimeError("No bridge account is ready for marketplace 'amazon'")))
    job = {
        "job_id": "vine-recovery-auth-test", "job_type": "import", "execution_mode": "browser_assist",
        "payload": {"source_marketplace": "amazon", "operation": "vine_image_gallery_recovery", "asin": "B000000001"},
    }
    try:
        try:
            store._run_import_job(job)
        except RuntimeError as exc:
            assert "no bridge account is ready" in str(exc).lower()
        else:
            raise AssertionError("Vine recovery must not run without an authorized Amazon account")
        assert len(calls) == 1
    finally:
        store.executor.shutdown(wait=False, cancel_futures=True)


def test_vine_gallery_recovery_refuses_ambiguous_paired_amazon_accounts(tmp_path: Path, monkeypatch):
    store = bridge_main.JobStore(tmp_path, default_delay_seconds=0, max_workers=1)
    monkeypatch.setattr(bridge_main.settings, "browser_headless", True)
    account = {"browser_enabled": True, "credential_configured": True, "session_state": "ready"}
    monkeypatch.setattr(bridge_main.account_store, "list_accounts", lambda _marketplace=None: [{**account, "account_key": "one"}, {**account, "account_key": "two"}])
    monkeypatch.setattr(bridge_main.account_store, "resolve_account", lambda *args: (_ for _ in ()).throw(AssertionError("ambiguous accounts must not be selected")))
    job = {"job_id": "vine-recovery-ambiguous", "job_type": "import", "execution_mode": "browser_assist", "payload": {"source_marketplace": "amazon", "operation": "vine_image_gallery_recovery", "asin": "B000000001"}}
    try:
        try:
            store._run_import_job(job)
        except RuntimeError as exc:
            assert "exactly one ready paired amazon" in str(exc).lower()
        else:
            raise AssertionError("ambiguous Amazon sessions must remain pending for operator selection")
    finally:
        store.executor.shutdown(wait=False, cancel_futures=True)


def test_vine_gallery_recovery_refuses_non_headless_runner(tmp_path: Path, monkeypatch):
    store = bridge_main.JobStore(tmp_path, default_delay_seconds=0, max_workers=1)
    monkeypatch.setattr(bridge_main.settings, "browser_headless", False)
    monkeypatch.setattr(bridge_main.account_store, "resolve_account", lambda *args: (_ for _ in ()).throw(AssertionError("must reject before resolving or launching")))
    job = {
        "job_id": "vine-recovery-headless-test", "job_type": "import", "execution_mode": "browser_assist",
        "payload": {"source_marketplace": "amazon", "operation": "vine_image_gallery_recovery", "asin": "B000000001"},
    }
    try:
        try:
            store._run_import_job(job)
        except RuntimeError as exc:
            assert "authorized headless" in str(exc).lower()
        else:
            raise AssertionError("Vine recovery must refuse a foreground browser")
    finally:
        store.executor.shutdown(wait=False, cancel_futures=True)


def test_vine_recovery_uses_ready_account_state_in_new_headless_context_not_extension_pairing(tmp_path: Path, monkeypatch):
    store = bridge_main.JobStore(tmp_path, default_delay_seconds=0, max_workers=1)
    monkeypatch.setattr(bridge_main.settings, "browser_headless", True)
    monkeypatch.setattr(bridge_main.settings, "runner_mode", "playwright")
    configured = {
        "account_key": "amazon-disposable-test",
        "browser_enabled": True,
        "credential_configured": True,
        "session_state": "ready",
        "session_payload": {"cookies": [{"name": "test-only", "value": "redacted"}]},
    }
    monkeypatch.setattr(bridge_main.account_store, "list_accounts", lambda _marketplace=None: [configured])
    monkeypatch.setattr(bridge_main.account_store, "resolve_account", lambda *_args: configured)
    captured = {}

    class FakeRunner:
        def run_import(self, *, job_id, bridge_account, payload):
            captured.update(job_id=job_id, bridge_account=bridge_account, payload=payload)
            return {"imported_listings": []}

    def create_runner(marketplace, config):
        captured["marketplace"] = marketplace
        captured["headless"] = config.headless
        return FakeRunner()

    monkeypatch.setattr(bridge_main, "create_marketplace_browser_runner", create_runner)
    monkeypatch.setattr(bridge_main.connect_session_store, "get_session", lambda *_args: (_ for _ in ()).throw(AssertionError("browser-extension pairing is not this headless route")))
    job = {
        "job_id": "vine-recovery-headless-context",
        "job_type": "import",
        "execution_mode": "browser_assist",
        "payload": {"source_marketplace": "amazon", "operation": "vine_image_gallery_recovery", "asin": "B000000046"},
    }
    try:
        store._run_import_job(job)
        assert captured["marketplace"] == "amazon"
        assert captured["headless"] is True
        assert captured["bridge_account"]["account_key"] == "amazon-disposable-test"
        assert captured["bridge_account"]["session_payload"]["cookies"]
    finally:
        store.executor.shutdown(wait=False, cancel_futures=True)


def test_vine_recovery_bridge_idempotency_key_is_atomic_under_concurrent_delivery(tmp_path: Path):
    store = bridge_main.JobStore(tmp_path, default_delay_seconds=0, max_workers=1)
    submitted = []
    store.executor.submit = lambda _fn, job_id: submitted.append(job_id)
    request = {
        "source_marketplace": "amazon",
        "operation": "vine_image_gallery_recovery",
        "acquisition_request_key": "vine-image-recovery:job-42",
        "asin": "B000000042",
    }
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: store.create_job("import", "browser_assist", request), range(16)))
        assert len({row["job_id"] for row in results}) == 1
        assert len(store.jobs) == 1
        assert len(submitted) == 1
    finally:
        store.executor.shutdown(wait=True)
