from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import settings


class AutomationBridgeError(RuntimeError):
    pass


def automation_bridge_ready() -> bool:
    return bool(
        settings.automation_bridge_enabled
        and settings.automation_bridge_url
        and settings.automation_bridge_api_key
    )


def bridge_browser_submit_policy() -> dict[str, Any]:
    if not automation_bridge_ready():
        return {
            "configured": False,
            "browser_submit_enabled": False,
            "policy_label": "Bridge not configured",
            "policy_note": "PosterPro cannot verify bridge submit behavior until the automation bridge is fully configured.",
        }
    try:
        health = get_automation_bridge_health()
    except AutomationBridgeError:
        return {
            "configured": True,
            "browser_submit_enabled": False,
            "policy_label": "Bridge policy unavailable",
            "policy_note": "PosterPro could not confirm the live bridge submit policy. Assisted browser runs should be treated as review-first until the bridge health check succeeds.",
        }

    submit_enabled = bool(health.get("browser_submit_enabled"))
    return {
        "configured": True,
        "browser_submit_enabled": submit_enabled,
        "policy_label": "Final submit enabled" if submit_enabled else "Draft and review",
        "policy_note": (
            "The live bridge is allowed to click the final marketplace action when the browser flow reaches a supported submit step."
            if submit_enabled
            else "The live bridge currently stops at draft-fill or assisted handoff. Operators should expect to review and complete final marketplace submission themselves."
        ),
    }


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.automation_bridge_api_key}",
        "Content-Type": "application/json",
    }


def _bridge_base_url() -> str:
    return str(settings.automation_bridge_url).rstrip("/")


def _timeout_seconds() -> int:
    return max(5, int(settings.automation_bridge_timeout_seconds or 30))


def _raise_bridge_error(prefix: str, exc: Exception, response: httpx.Response | None = None) -> None:
    detail: str | None = None
    if response is not None:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            raw_detail = payload.get("detail") or payload.get("message") or payload.get("error")
            if isinstance(raw_detail, str) and raw_detail.strip():
                detail = raw_detail.strip()
        if not detail:
            raw_text = (response.text or "").strip()
            if raw_text:
                detail = raw_text
    if detail:
        raise AutomationBridgeError(f"{prefix}: {detail}") from exc
    raise AutomationBridgeError(f"{prefix}: {exc}") from exc


def submit_bridge_job(*, job_type: str, execution_mode: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not automation_bridge_ready():
        return {
            "status": "BRIDGE_NOT_CONFIGURED",
            "job_type": job_type,
            "execution_mode": execution_mode,
            "submitted_payload": payload,
        }

    base_url = _bridge_base_url()
    url = f"{base_url}/jobs/{job_type}"
    request_payload = {
        "job_type": job_type,
        "execution_mode": execution_mode,
        "payload": payload,
    }
    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.post(url, json=request_payload, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge returned a non-object response")
            return {
                "status": "SUBMITTED_TO_BRIDGE",
                "bridge_url": base_url,
                "bridge_response": data,
            }
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge submission failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge submission failed", exc)


def smoke_test_automation_bridge() -> dict[str, Any]:
    if not automation_bridge_ready():
        return {
            "ok": False,
            "status": "BRIDGE_NOT_CONFIGURED",
            "message": "Automation bridge is not fully configured.",
        }

    base_url = _bridge_base_url()
    timeout = _timeout_seconds()
    candidates = [
        ("GET", f"{base_url}/health"),
        ("GET", base_url),
    ]
    errors: list[str] = []

    with httpx.Client(timeout=timeout, headers=_headers()) as client:
        for method, url in candidates:
            try:
                response = client.request(method, url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                body = response.json() if "application/json" in content_type else response.text[:500]
                return {
                    "ok": True,
                    "status": "BRIDGE_REACHABLE",
                    "bridge_url": base_url,
                    "checked_url": url,
                    "http_status": response.status_code,
                    "response": body,
                }
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{method} {url}: {exc}")

    return {
        "ok": False,
        "status": "BRIDGE_UNREACHABLE",
        "bridge_url": base_url,
        "message": "Automation bridge did not respond successfully to connectivity probes.",
        "errors": errors,
    }


def get_automation_bridge_health() -> dict[str, Any]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/health"
    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.get(url, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge health returned a non-object response")
            return data
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge health check failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge health check failed", exc)


def list_bridge_accounts(*, marketplace: str | None = None) -> dict[str, Any]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/accounts"
    params = {"marketplace": marketplace} if marketplace else None
    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.get(url, params=params, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge accounts endpoint returned a non-object response")
            return data
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge account listing failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge account listing failed", exc)


def upsert_bridge_account(*, marketplace: str, account_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/accounts/{marketplace.strip().lower()}/{account_key.strip().lower()}"
    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.put(url, json=payload, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge account upsert returned a non-object response")
            return data
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge account upsert failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge account upsert failed", exc)


def update_bridge_account_session(*, marketplace: str, account_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/accounts/{marketplace.strip().lower()}/{account_key.strip().lower()}/session"
    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.post(url, json=payload, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge session update returned a non-object response")
            return data
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge session update failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge session update failed", exc)


def start_bridge_account_connect(*, marketplace: str, account_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/accounts/{marketplace.strip().lower()}/{account_key.strip().lower()}/connect/start"
    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.post(url, json=payload, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge connect start returned a non-object response")
            return data
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge account connect start failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge account connect start failed", exc)


def get_bridge_connect_session(connect_session_id: str) -> dict[str, Any]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/connect-sessions/{connect_session_id.strip()}"
    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.get(url, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge connect session fetch returned a non-object response")
            return data
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge connect session fetch failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge connect session fetch failed", exc)


def get_active_bridge_connect_session() -> dict[str, Any] | None:
    health = get_automation_bridge_health()
    connect_session_id = str(health.get("active_connect_session_id") or "").strip()
    if not connect_session_id:
        return None
    return get_bridge_connect_session(connect_session_id)


def get_bridge_connect_desktop_frame(connect_session_id: str) -> bytes:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/connect-sessions/{connect_session_id.strip()}/desktop-frame"
    try:
        with httpx.Client(timeout=max(_timeout_seconds(), 20)) as client:
            response = client.get(url, headers=_headers())
            response.raise_for_status()
            return bytes(response.content)
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge desktop frame fetch failed", exc, exc.response)
    except httpx.HTTPError as exc:
        _raise_bridge_error("Automation bridge desktop frame fetch failed", exc)


def get_bridge_asset(asset_id: str) -> tuple[bytes, str | None, str | None]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/assets/{asset_id.strip()}"
    try:
        with httpx.Client(timeout=max(_timeout_seconds(), 20)) as client:
            response = client.get(url, headers=_headers())
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").strip() or None
            disposition = str(response.headers.get("content-disposition") or "").strip() or None
            return bytes(response.content), content_type, disposition
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge asset fetch failed", exc, exc.response)
    except httpx.HTTPError as exc:
        _raise_bridge_error("Automation bridge asset fetch failed", exc)


def send_bridge_connect_desktop_action(*, connect_session_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    normalized_action = action.strip().lower()
    if normalized_action not in {"click", "type", "key"}:
        raise AutomationBridgeError(f"Unsupported bridge desktop action '{action}'")
    base_url = _bridge_base_url()
    url = f"{base_url}/connect-sessions/{connect_session_id.strip()}/desktop-actions/{normalized_action}"
    try:
        with httpx.Client(timeout=max(_timeout_seconds(), 20)) as client:
            response = client.post(url, json=payload, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge desktop action returned a non-object response")
            return data
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge desktop action failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge desktop action failed", exc)


def connect_bridge_account(*, marketplace: str, account_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/accounts/{marketplace.strip().lower()}/{account_key.strip().lower()}/connect"
    requested_timeout = payload.get("wait_timeout_seconds")
    try:
        connect_timeout = max(_timeout_seconds(), int(requested_timeout or 300) + 30)
    except (TypeError, ValueError):
        connect_timeout = max(_timeout_seconds(), 330)
    try:
        with httpx.Client(timeout=connect_timeout) as client:
            response = client.post(url, json=payload, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge account connect returned a non-object response")
            return data
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge account connect failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge account connect failed", exc)


def get_bridge_job(job_id: str) -> dict[str, Any]:
    if not automation_bridge_ready():
        raise AutomationBridgeError("Automation bridge is not fully configured")
    base_url = _bridge_base_url()
    url = f"{base_url}/jobs/{job_id.strip()}"
    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.get(url, headers=_headers())
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise AutomationBridgeError("Automation bridge job fetch returned a non-object response")
            return data
    except httpx.HTTPStatusError as exc:
        _raise_bridge_error("Automation bridge job fetch failed", exc, exc.response)
    except (httpx.HTTPError, ValueError) as exc:
        _raise_bridge_error("Automation bridge job fetch failed", exc)


def wait_for_bridge_job(*, job_id: str, timeout_seconds: int = 120, poll_interval_seconds: float = 1.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(5, int(timeout_seconds or 120))
    while True:
        job = get_bridge_job(job_id)
        status = str(job.get("status") or "").strip().lower()
        if status in {"completed", "failed", "canceled"}:
            return job
        if time.monotonic() >= deadline:
            raise AutomationBridgeError(f"Automation bridge job {job_id} did not finish within {timeout_seconds} seconds")
        time.sleep(max(0.25, poll_interval_seconds))
