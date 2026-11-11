"""HTTPS-only PosterPro client and Windows DPAPI-protected enrollment."""
from __future__ import annotations

import getpass
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .core import PRINTER_NAME


def config_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return root / "PosterPro" / "print-agent.json"


def protect_token(token: str) -> str:
    try:
        import win32crypt
    except ImportError as exc:
        raise RuntimeError("Token enrollment requires Windows DPAPI (pywin32).") from exc
    protected = win32crypt.CryptProtectData(token.encode("utf-8"), "PosterPro inventory print agent", None, None, None, 0)[1]
    import base64
    return base64.b64encode(protected).decode("ascii")


def unprotect_token(protected: str) -> str:
    try:
        import win32crypt
    except ImportError as exc:
        raise RuntimeError("Agent credential requires Windows DPAPI (pywin32).") from exc
    import base64
    data = base64.b64decode(protected, validate=True)
    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1].decode("utf-8")


def _https_base(url: str) -> str:
    if not url.startswith("https://"):
        raise ValueError("PosterPro server URL must use HTTPS with a valid certificate.")
    return url.rstrip("/")


def request_json(base_url: str, path: str, *, token: str | None = None, payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    url = _https_base(base_url) + "/api/inventory" + path
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            body = response.read(2_100_000)
    except urllib.error.HTTPError as exc:
        # Never print request headers or credentials in diagnostics.
        try:
            detail = json.loads(exc.read(8192).decode("utf-8", "replace")).get("detail", "")
        except Exception:
            detail = ""
        raise RuntimeError(f"PosterPro returned HTTP {exc.code}: {str(detail)[:500]}") from None
    if len(body) > 2_000_000:
        raise RuntimeError("PosterPro response exceeded the agent response limit.")
    result = json.loads(body.decode("utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("PosterPro returned an invalid structured response.")
    return result


def enroll(server_url: str, pairing_code: str, display_name: str = "Windows WT20 agent") -> dict[str, Any]:
    response = request_json(server_url, "/census/print-agents/pair", payload={
        "pairing_code": pairing_code, "display_name": display_name, "printer_name": PRINTER_NAME,
        "capabilities": {"agent_version": "0.1.0", "transport": "HTTPS outbound", "dry_run_supported": True},
    })
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {"server_url": _https_base(server_url), "agent_id": response["agent_id"], "printer_name": response["printer_name"], "token_dpapi": protect_token(response["access_token"])}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(config), encoding="utf-8")
    os.replace(temp, path)
    return {"agent_id": response["agent_id"], "printer_name": response["printer_name"], "config_path": str(path)}


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        raise RuntimeError("No paired agent configuration. Run the explicit enroll command first.")
    config = json.loads(path.read_text(encoding="utf-8"))
    config["token"] = unprotect_token(config.pop("token_dpapi"))
    if config.get("printer_name") != PRINTER_NAME:
        raise RuntimeError("Saved printer is outside the agent allowlist.")
    config["server_url"] = _https_base(str(config.get("server_url") or ""))
    return config


def prompt_enrollment() -> dict[str, Any]:
    server = input("PosterPro HTTPS URL (for example https://posterpro.sparkleserver.site): ").strip()
    code = getpass.getpass("One-time Census print-agent pairing code: ").strip()
    return enroll(server, code)

