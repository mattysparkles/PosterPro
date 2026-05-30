from pathlib import Path

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
