from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.browser_runner import BrowserRunnerConfig, BrowserRunnerError, create_marketplace_browser_runner


class BridgeSettings(BaseSettings):
    bridge_api_key: str = Field(default="", validation_alias="AUTOMATION_BRIDGE_API_KEY")
    data_dir: Path = Field(default=Path("./data"), validation_alias="AUTOMATION_BRIDGE_DATA_DIR")
    runner_mode: str = Field(default="simulated", validation_alias="AUTOMATION_BRIDGE_RUNNER_MODE")
    default_job_delay_seconds: float = Field(default=0.3, validation_alias="AUTOMATION_BRIDGE_JOB_DELAY_SECONDS")
    max_workers: int = Field(default=4, validation_alias="AUTOMATION_BRIDGE_MAX_WORKERS")
    browser_headless: bool = Field(default=True, validation_alias="AUTOMATION_BRIDGE_BROWSER_HEADLESS")
    browser_timeout_ms: int = Field(default=45000, validation_alias="AUTOMATION_BRIDGE_BROWSER_TIMEOUT_MS")
    browser_submit_enabled: bool = Field(default=False, validation_alias="AUTOMATION_BRIDGE_BROWSER_SUBMIT_ENABLED")
    browser_screenshots_dir: Path = Field(default=Path("./data/screenshots"), validation_alias="AUTOMATION_BRIDGE_BROWSER_SCREENSHOTS_DIR")
    asset_ttl_seconds: int = Field(default=3600, validation_alias="AUTOMATION_BRIDGE_ASSET_TTL_SECONDS")
    assets_dir: Path = Field(default=Path("./data/assets"), validation_alias="AUTOMATION_BRIDGE_ASSETS_DIR")

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = BridgeSettings()


class BridgeJobRequest(BaseModel):
    job_type: Literal["crosspost", "import"]
    execution_mode: str = "manual_only"
    payload: dict[str, Any] = Field(default_factory=dict)


class BridgeJobResponse(BaseModel):
    job_id: str
    job_type: str
    execution_mode: str
    status: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_requested: bool = False


class BridgeAccountSessionUpdateRequest(BaseModel):
    session_state: str = "draft"
    session_payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: str | None = None
    last_tested_at: str | None = None
    notes: str | None = None


class BridgeAccountConnectRequest(BaseModel):
    display_name: str | None = None
    login_handle: str | None = None
    credential_secret: str | None = None
    notes: str | None = None
    provider_enabled: bool = False
    browser_enabled: bool = True
    expires_at: str | None = None
    wait_timeout_seconds: int = 300


class BridgeAccountUpsertRequest(BaseModel):
    display_name: str | None = None
    login_handle: str | None = None
    credential_secret: str | None = None
    notes: str | None = None
    provider_enabled: bool = False
    browser_enabled: bool = False
    session_state: str = "draft"
    session_payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: str | None = None


class BridgeMarketplaceAccountResponse(BaseModel):
    account_id: str
    marketplace: str
    account_key: str
    display_name: str | None = None
    login_handle: str | None = None
    notes: str | None = None
    provider_enabled: bool = False
    browser_enabled: bool = False
    credential_configured: bool = False
    session_state: str = "draft"
    session_payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: str | None = None
    last_tested_at: str | None = None
    created_at: str
    updated_at: str


class BridgeAccountsEnvelope(BaseModel):
    accounts: list[BridgeMarketplaceAccountResponse] = Field(default_factory=list)


class BridgeConnectSessionResponse(BaseModel):
    connect_session_id: str
    marketplace: str
    account_key: str
    display_name: str | None = None
    login_handle: str | None = None
    status: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    wait_timeout_seconds: int = 300
    message: str | None = None
    error: str | None = None
    result: BridgeMarketplaceAccountResponse | None = None


class BridgeDesktopActionRequest(BaseModel):
    x: int | None = None
    y: int | None = None
    text: str | None = None
    key: str | None = None


class BridgeAssetResponse(BaseModel):
    asset_id: str
    file_name: str
    content_type: str
    size_bytes: int
    created_at: str
    expires_at: str
    source_url: str | None = None
    download_path: str


def _sanitize_connect_session_account_result(account: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(account, dict):
        return None
    sanitized = deepcopy(account)
    sanitized["session_payload"] = {}
    return sanitized


class JobStore:
    def __init__(self, data_dir: Path, *, default_delay_seconds: float, max_workers: int) -> None:
        self.data_dir = data_dir
        self.path = self.data_dir / "jobs.json"
        self.default_delay_seconds = default_delay_seconds
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.jobs: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save()
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            jobs = payload.get("jobs", {})
            if isinstance(jobs, dict):
                self.jobs = jobs
        except Exception:
            self.jobs = {}

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"jobs": self.jobs}, indent=2, sort_keys=True), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _next_job_id(self) -> str:
        return uuid4().hex

    def create_job(self, job_type: str, execution_mode: str, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = self._next_job_id()
        now = self._now()
        job = {
            "job_id": job_id,
            "job_type": job_type,
            "execution_mode": execution_mode,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "payload": payload,
            "result": None,
            "error": None,
            "cancel_requested": False,
        }
        with self.lock:
            self.jobs[job_id] = job
            self._save()
        self.executor.submit(self._process_job, job_id)
        return deepcopy(job)

    def list_jobs(self, *, job_type: str | None = None, status_value: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            jobs = list(self.jobs.values())
        if job_type:
            jobs = [job for job in jobs if job["job_type"] == job_type]
        if status_value:
            jobs = [job for job in jobs if job["status"] == status_value]
        jobs.sort(key=lambda item: item["created_at"], reverse=True)
        return [deepcopy(job) for job in jobs[:limit]]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return deepcopy(job) if job else None

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job["status"] in {"completed", "failed", "canceled"}:
                return deepcopy(job)
            job["cancel_requested"] = True
            if job["status"] == "queued":
                job["status"] = "canceled"
                job["completed_at"] = self._now()
            job["updated_at"] = self._now()
            self._save()
            return deepcopy(job)

    def _update_job(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            job = self.jobs[job_id]
            job.update(changes)
            job["updated_at"] = self._now()
            self._save()
            return deepcopy(job)

    def _process_job(self, job_id: str) -> None:
        time.sleep(self.default_delay_seconds)
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job["status"] == "canceled":
                return
            job["status"] = "running"
            job["started_at"] = self._now()
            job["updated_at"] = self._now()
            self._save()

        try:
            result = self._run_job(job_id)
            with self.lock:
                job = self.jobs.get(job_id)
                if not job:
                    return
                if job["cancel_requested"]:
                    job["status"] = "canceled"
                    job["completed_at"] = self._now()
                    job["updated_at"] = self._now()
                    self._save()
                    return
                job["status"] = "completed"
                job["result"] = result
                job["completed_at"] = self._now()
                job["updated_at"] = self._now()
                self._save()
        except Exception as exc:
            with self.lock:
                job = self.jobs.get(job_id)
                if not job:
                    return
                job["status"] = "failed"
                job["error"] = str(exc)
                job["completed_at"] = self._now()
                job["updated_at"] = self._now()
                self._save()

    def _run_job(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = deepcopy(self.jobs[job_id])

        if job["job_type"] == "import":
            return self._run_import_job(job)
        return self._run_crosspost_job(job)

    def _run_import_job(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job["payload"] or {}
        source_marketplace = str(payload.get("source_marketplace") or "unknown").lower()
        execution_mode = job["execution_mode"]
        bridge_account = None
        if execution_mode in {"provider_assist", "browser_assist"}:
            bridge_account = account_store.resolve_account(source_marketplace, execution_mode, payload)
        if execution_mode == "browser_assist" and settings.runner_mode in {"playwright", "browser"}:
            runner = create_marketplace_browser_runner(
                source_marketplace,
                BrowserRunnerConfig(
                    headless=settings.browser_headless,
                    timeout_ms=settings.browser_timeout_ms,
                    submit_enabled=settings.browser_submit_enabled,
                    screenshots_dir=settings.browser_screenshots_dir,
                    asset_persistor=asset_store.create_asset,
                ),
            )
            result = runner.run_import(
                job_id=job["job_id"],
                bridge_account=bridge_account,
                payload=payload,
            )
            session_state = result.get("session_state")
            if session_state and bridge_account:
                account_store.update_session(
                    source_marketplace,
                    bridge_account["account_key"],
                    BridgeAccountSessionUpdateRequest(
                        session_state=session_state.get("session_state") or "active",
                        session_payload=session_state.get("session_payload") or {},
                        last_tested_at=datetime.now(UTC).isoformat(),
                    ),
                )
            return {
                **result,
                "bridge_runner": settings.runner_mode,
            }
        normalized = payload.get("normalized_preview") or payload.get("payload") or {}
        created_listing = {
            "title": normalized.get("title") or "Imported listing",
            "description": normalized.get("description") or "",
            "category_id": normalized.get("category_id") or None,
            "condition": normalized.get("condition") or None,
            "listing_price": normalized.get("listing_price") or None,
            "quantity": normalized.get("quantity") or 1,
            "image_urls": normalized.get("image_urls") or [],
            "item_specifics": normalized.get("item_specifics") or {},
            "tags": normalized.get("tags") or [],
            "source_marketplace": source_marketplace,
            "source_listing_reference": payload.get("source_listing_reference"),
        }
        return {
            "job_id": job["job_id"],
            "job_type": "import",
            "execution_mode": job["execution_mode"],
            "status": "import_completed",
            "bridge_runner": settings.runner_mode,
            "created_listing": created_listing,
            "import_mode": payload.get("import_mode") or "manual",
            "bridge_account": bridge_account,
            "notes": [
                "Bridge normalized the source payload into a canonical listing structure.",
                "PosterPro can now store or inspect this result as a real import job artifact.",
            ],
        }

    def _run_crosspost_job(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job["payload"] or {}
        marketplace = str(payload.get("marketplace") or "unknown").lower()
        listing_id = payload.get("listing_id")
        execution_mode = job["execution_mode"]
        base_payload = payload.get("payload") or payload
        bridge_account = None
        if execution_mode in {"provider_assist", "browser_assist"}:
            bridge_account = account_store.resolve_account(marketplace, execution_mode, payload)
        if execution_mode == "browser_assist" and settings.runner_mode in {"playwright", "browser"}:
            runner = create_marketplace_browser_runner(
                marketplace,
                BrowserRunnerConfig(
                    headless=settings.browser_headless,
                    timeout_ms=settings.browser_timeout_ms,
                    submit_enabled=settings.browser_submit_enabled,
                    screenshots_dir=settings.browser_screenshots_dir,
                    asset_persistor=asset_store.create_asset,
                ),
            )
            result = runner.run_crosspost(
                job_id=job["job_id"],
                bridge_account=bridge_account,
                payload=payload,
            )
            session_state = result.get("session_state")
            if session_state and bridge_account:
                account_store.update_session(
                    marketplace,
                    bridge_account["account_key"],
                    BridgeAccountSessionUpdateRequest(
                        session_state=session_state.get("session_state") or "active",
                        session_payload=session_state.get("session_payload") or {},
                        last_tested_at=datetime.now(UTC).isoformat(),
                    ),
                )
            return {
                **result,
                "bridge_runner": settings.runner_mode,
            }
        if execution_mode == "direct_api":
            return {
                "job_id": job["job_id"],
                "job_type": "crosspost",
                "execution_mode": execution_mode,
                "status": "published",
                "bridge_runner": settings.runner_mode,
                "marketplace": marketplace,
                "external_listing_id": f"{marketplace.upper()}-{listing_id}-{job['job_id'][:8]}",
                "submitted_payload": base_payload,
            }

        if execution_mode == "provider_assist":
            return {
                "job_id": job["job_id"],
                "job_type": "crosspost",
                "execution_mode": execution_mode,
                "status": "provider_packet_ready",
                "bridge_runner": settings.runner_mode,
                "marketplace": marketplace,
                "provider_packet": {
                    "marketplace": marketplace,
                    "listing_id": listing_id,
                    "payload": base_payload,
                    "renewal_plan": payload.get("renewal_plan"),
                    "bridge_account": bridge_account,
                },
                "bridge_account": bridge_account,
                "notes": [
                    "A provider integration can pick up this packet and complete the actual marketplace publish.",
                ],
            }

        if execution_mode == "browser_assist":
            return {
                "job_id": job["job_id"],
                "job_type": "crosspost",
                "execution_mode": execution_mode,
                "status": "browser_handoff_ready",
                "bridge_runner": settings.runner_mode,
                "marketplace": marketplace,
                "browser_handoff": {
                    "marketplace": marketplace,
                    "listing_id": listing_id,
                    "payload": base_payload,
                    "shipping_scope": payload.get("shipping_scope"),
                    "renewal_plan": payload.get("renewal_plan"),
                    "bridge_account": bridge_account,
                },
                "bridge_account": bridge_account,
                "notes": [
                    "A browser automation runner can consume this handoff to perform the marketplace workflow.",
                ],
            }

        return {
            "job_id": job["job_id"],
            "job_type": "crosspost",
            "execution_mode": "manual_only",
            "status": "manual_packet_ready",
            "bridge_runner": settings.runner_mode,
            "marketplace": marketplace,
            "manual_handoff": {
                "listing_id": listing_id,
                "payload": base_payload,
            },
            "notes": [
                "No direct automation path was used for this marketplace.",
                "The bridge produced a structured operator packet instead.",
            ],
        }


class AccountStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = self.data_dir / "accounts.json"
        self.lock = threading.RLock()
        self.accounts: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save()
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            accounts = payload.get("accounts", {})
            if isinstance(accounts, dict):
                self.accounts = accounts
        except Exception:
            self.accounts = {}

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"accounts": self.accounts}, indent=2, sort_keys=True), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _make_account_id(self, marketplace: str, account_key: str) -> str:
        return f"{marketplace}:{account_key}"

    def _serialize(self, account: dict[str, Any]) -> dict[str, Any]:
        return {
            "account_id": account["account_id"],
            "marketplace": account["marketplace"],
            "account_key": account["account_key"],
            "display_name": account.get("display_name"),
            "login_handle": account.get("login_handle"),
            "notes": account.get("notes"),
            "provider_enabled": bool(account.get("provider_enabled")),
            "browser_enabled": bool(account.get("browser_enabled")),
            "credential_configured": bool(account.get("credential_secret")),
            "session_state": account.get("session_state") or "draft",
            "session_payload": account.get("session_payload") or {},
            "expires_at": account.get("expires_at"),
            "last_tested_at": account.get("last_tested_at"),
            "created_at": account["created_at"],
            "updated_at": account["updated_at"],
        }

    def list_accounts(self, marketplace: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            accounts = list(self.accounts.values())
        if marketplace:
            accounts = [item for item in accounts if item["marketplace"] == marketplace]
        accounts.sort(key=lambda item: (item["marketplace"], item.get("display_name") or item["account_key"]))
        return [self._serialize(account) for account in accounts]

    def get_account(self, marketplace: str, account_key: str) -> dict[str, Any] | None:
        account_id = self._make_account_id(marketplace, account_key)
        with self.lock:
            account = self.accounts.get(account_id)
        return self._serialize(account) if account else None

    def upsert_account(self, marketplace: str, account_key: str, payload: BridgeAccountUpsertRequest) -> dict[str, Any]:
        account_id = self._make_account_id(marketplace, account_key)
        now = self._now()
        with self.lock:
            existing = self.accounts.get(account_id)
            if existing:
                account = existing
            else:
                account = {
                    "account_id": account_id,
                    "marketplace": marketplace,
                    "account_key": account_key,
                    "created_at": now,
                }
            if payload.display_name is not None:
                account["display_name"] = payload.display_name.strip() or None
            if payload.login_handle is not None:
                account["login_handle"] = payload.login_handle.strip() or None
            if payload.credential_secret is not None:
                account["credential_secret"] = payload.credential_secret.strip() or None
            if payload.notes is not None:
                account["notes"] = payload.notes.strip() or None
            account["provider_enabled"] = bool(payload.provider_enabled)
            account["browser_enabled"] = bool(payload.browser_enabled)
            account["session_state"] = (payload.session_state or "draft").strip().lower()
            account["session_payload"] = payload.session_payload or {}
            account["expires_at"] = payload.expires_at
            account["updated_at"] = now
            self.accounts[account_id] = account
            self._save()
            return self._serialize(account)

    def update_session(self, marketplace: str, account_key: str, payload: BridgeAccountSessionUpdateRequest) -> dict[str, Any]:
        account_id = self._make_account_id(marketplace, account_key)
        now = self._now()
        with self.lock:
            account = self.accounts.get(account_id)
            if not account:
                raise KeyError(account_id)
            account["session_state"] = (payload.session_state or "draft").strip().lower()
            account["session_payload"] = payload.session_payload or {}
            account["expires_at"] = payload.expires_at
            account["last_tested_at"] = payload.last_tested_at or now
            if payload.notes is not None:
                account["notes"] = payload.notes.strip() or None
            account["updated_at"] = now
            self._save()
            return self._serialize(account)

    def resolve_account(self, marketplace: str, execution_mode: str, payload: dict[str, Any]) -> dict[str, Any]:
        requested_key = str(payload.get("account_key") or payload.get("account_handle") or "").strip().lower()
        with self.lock:
            candidates = [deepcopy(item) for item in self.accounts.values() if item["marketplace"] == marketplace]
        if requested_key:
            candidates = [item for item in candidates if str(item["account_key"]).lower() == requested_key or str(item.get("login_handle") or "").lower() == requested_key]
        if execution_mode == "provider_assist":
            candidates = [item for item in candidates if item.get("provider_enabled")]
        if execution_mode == "browser_assist":
            candidates = [item for item in candidates if item.get("browser_enabled")]
        if execution_mode in {"provider_assist", "browser_assist"}:
            candidates = [item for item in candidates if item.get("credential_secret")]
        if execution_mode == "browser_assist":
            candidates = [item for item in candidates if str(item.get("session_state") or "").lower() in {"ready", "active", "valid"}]
        if not candidates:
            raise RuntimeError(f"No bridge account is ready for marketplace '{marketplace}' and execution mode '{execution_mode}'")
        candidates.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return self._serialize(candidates[0])


class ActiveConnectSessionError(RuntimeError):
    pass


class ConnectSessionStore:
    ACTIVE_STATUSES = {"queued", "launching_browser", "opening_marketplace", "waiting_for_login", "validating_session"}

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = self.data_dir / "connect_sessions.json"
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.sessions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save()
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            sessions = payload.get("sessions", {})
            if isinstance(sessions, dict):
                self.sessions = sessions
        except Exception:
            self.sessions = {}

        repaired = False
        for session in self.sessions.values():
            if str(session.get("status") or "").strip().lower() in self.ACTIVE_STATUSES:
                now = self._now()
                session["status"] = "failed"
                session["error"] = f"Bridge restarted while the {str(session.get('marketplace') or 'marketplace')} connect session was in progress"
                session["message"] = session["error"]
                session["completed_at"] = now
                session["updated_at"] = now
                repaired = True
        if repaired:
            self._save()

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"sessions": self.sessions}, indent=2, sort_keys=True), encoding="utf-8")

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _serialize(self, session: dict[str, Any]) -> dict[str, Any]:
        serialized = deepcopy(session)
        serialized["result"] = _sanitize_connect_session_account_result(serialized.get("result"))
        return serialized

    def _active_session_locked(self) -> dict[str, Any] | None:
        for session in self.sessions.values():
            if str(session.get("status") or "").strip().lower() in self.ACTIVE_STATUSES:
                return session
        return None

    def active_session_id(self) -> str | None:
        with self.lock:
            session = self._active_session_locked()
            return str(session.get("connect_session_id")) if session else None

    def get_session(self, connect_session_id: str) -> dict[str, Any] | None:
        with self.lock:
            session = self.sessions.get(connect_session_id)
            return self._serialize(session) if session else None

    def create_session(
        self,
        *,
        marketplace: str,
        account_key: str,
        bridge_account: dict[str, Any],
        payload: BridgeAccountConnectRequest,
    ) -> dict[str, Any]:
        with self.lock:
            active = self._active_session_locked()
            if active:
                active_marketplace = str(active.get("marketplace") or "").strip().lower()
                active_account_key = str(active.get("account_key") or "").strip().lower()
                if active_marketplace == marketplace and active_account_key == account_key:
                    return self._serialize(active)
                raise ActiveConnectSessionError(
                    f"Another {str(active.get('marketplace') or 'marketplace')} connect session is already running for bridge account '{active.get('account_key')}'. "
                    "Finish or wait for that session before starting another."
                )
            connect_session_id = uuid4().hex
            now = self._now()
            requested_login_handle = (payload.login_handle or "").strip() or None
            session = {
                "connect_session_id": connect_session_id,
                "marketplace": marketplace,
                "account_key": account_key,
                "display_name": bridge_account.get("display_name"),
                "login_handle": requested_login_handle,
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "completed_at": None,
                "wait_timeout_seconds": max(60, int(payload.wait_timeout_seconds or 300)),
                "message": f"Queued {marketplace.capitalize()} connect session.",
                "error": None,
                "result": None,
            }
            self.sessions[connect_session_id] = session
            self._save()

        self.executor.submit(
            self._process_session,
            connect_session_id,
            marketplace,
            account_key,
            payload.model_dump(),
        )
        return self._serialize(session)

    def _update_session(self, connect_session_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            session = self.sessions[connect_session_id]
            session.update(changes)
            session["updated_at"] = self._now()
            self._save()
            return self._serialize(session)

    def _process_session(
        self,
        connect_session_id: str,
        marketplace: str,
        account_key: str,
        payload: dict[str, Any],
    ) -> None:
        started_at = self._now()
        self._update_session(
            connect_session_id,
            status="launching_browser",
            started_at=started_at,
            message=f"Starting the {marketplace.capitalize()} browser session.",
        )

        bridge_account = account_store.get_account(marketplace, account_key)
        if not bridge_account:
            self._update_session(
                connect_session_id,
                status="failed",
                completed_at=self._now(),
                error="Bridge account not found",
                message="Bridge account not found.",
            )
            return

        runner = create_marketplace_browser_runner(
            marketplace,
            BrowserRunnerConfig(
                headless=settings.browser_headless,
                timeout_ms=settings.browser_timeout_ms,
                submit_enabled=False,
                screenshots_dir=settings.browser_screenshots_dir,
            ),
        )

        try:
            result = runner.capture_session(
                account_key=account_key,
                bridge_account=bridge_account,
                login_handle=payload.get("login_handle"),
                wait_timeout_seconds=payload.get("wait_timeout_seconds") or 300,
                status_callback=lambda status, message: self._update_session(
                    connect_session_id,
                    status=status,
                    message=message,
                ),
            )
            session_state = result.get("session_state") or {}
            account = account_store.update_session(
                marketplace,
                account_key,
                BridgeAccountSessionUpdateRequest(
                    session_state=session_state.get("session_state") or "active",
                    session_payload=session_state.get("session_payload") or {},
                    expires_at=payload.get("expires_at") if payload.get("expires_at") is not None else bridge_account.get("expires_at"),
                    last_tested_at=datetime.now(UTC).isoformat(),
                    notes=payload.get("notes") if payload.get("notes") is not None else bridge_account.get("notes"),
                ),
            )
            self._update_session(
                connect_session_id,
                status="completed",
                completed_at=self._now(),
                message=f"{marketplace.capitalize()} account connected and session captured.",
                result=_sanitize_connect_session_account_result(account),
                error=None,
            )
        except BrowserRunnerError as exc:
            account_store.update_session(
                marketplace,
                account_key,
                BridgeAccountSessionUpdateRequest(
                    session_state="invalid",
                    session_payload=bridge_account.get("session_payload") or {},
                    expires_at=bridge_account.get("expires_at"),
                    last_tested_at=datetime.now(UTC).isoformat(),
                    notes=payload.get("notes") if payload.get("notes") is not None else bridge_account.get("notes"),
                ),
            )
            self._update_session(
                connect_session_id,
                status="failed",
                completed_at=self._now(),
                error=str(exc),
                message=str(exc),
            )
        except Exception as exc:
            self._update_session(
                connect_session_id,
                status="failed",
                completed_at=self._now(),
                error=f"Unexpected bridge connect failure: {exc}",
                message="Unexpected bridge connect failure.",
            )


class AssetStore:
    def __init__(self, data_dir: Path, assets_dir: Path, *, ttl_seconds: int) -> None:
        self.data_dir = data_dir
        self.assets_dir = assets_dir
        self.path = self.data_dir / "assets.json"
        self.ttl_seconds = max(300, int(ttl_seconds or 3600))
        self.lock = threading.RLock()
        self.assets: dict[str, dict[str, Any]] = {}
        self._load()

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _now_iso(self) -> str:
        return self._now().isoformat()

    def _load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save()
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            assets = payload.get("assets", {})
            if isinstance(assets, dict):
                self.assets = assets
        except Exception:
            self.assets = {}
        self._purge_expired()

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"assets": self.assets}, indent=2, sort_keys=True), encoding="utf-8")

    def _purge_expired(self) -> None:
        now = self._now()
        expired_ids: list[str] = []
        for asset_id, asset in list(self.assets.items()):
            expires_at = str(asset.get("expires_at") or "").strip()
            file_path = Path(str(asset.get("file_path") or ""))
            try:
                expired = bool(expires_at) and datetime.fromisoformat(expires_at) <= now
            except ValueError:
                expired = True
            if expired or not file_path.exists():
                if file_path:
                    file_path.unlink(missing_ok=True)
                expired_ids.append(asset_id)
        for asset_id in expired_ids:
            self.assets.pop(asset_id, None)
        if expired_ids:
            self._save()

    def _serialize(self, asset: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_id": asset["asset_id"],
            "file_name": asset["file_name"],
            "content_type": asset["content_type"],
            "size_bytes": int(asset.get("size_bytes") or 0),
            "created_at": asset["created_at"],
            "expires_at": asset["expires_at"],
            "source_url": asset.get("source_url"),
            "download_path": f"/assets/{asset['asset_id']}",
        }

    def create_asset(self, data: bytes, content_type: str | None = None, file_name: str | None = None, source_url: str | None = None) -> dict[str, Any]:
        asset_id = uuid4().hex
        created_at = self._now()
        expires_at = created_at.timestamp() + self.ttl_seconds
        suffix = Path(file_name or "asset.bin").suffix or ".bin"
        normalized_name = Path(file_name or f"{asset_id}{suffix}").name
        target = self.assets_dir / f"{asset_id}{suffix}"
        target.write_bytes(data)
        asset = {
            "asset_id": asset_id,
            "file_name": normalized_name,
            "content_type": (content_type or "application/octet-stream").strip() or "application/octet-stream",
            "size_bytes": len(data),
            "created_at": created_at.isoformat(),
            "expires_at": datetime.fromtimestamp(expires_at, tz=UTC).isoformat(),
            "source_url": source_url.strip() if source_url else None,
            "file_path": str(target),
        }
        with self.lock:
            self._purge_expired()
            self.assets[asset_id] = asset
            self._save()
            return self._serialize(asset)

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        normalized_id = asset_id.strip()
        with self.lock:
            self._purge_expired()
            asset = self.assets.get(normalized_id)
            if not asset:
                return None
            return deepcopy(asset)


security = HTTPBearer(auto_error=False)


def _require_auth(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
    if not settings.bridge_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bridge API key not configured")
    if not credentials or credentials.scheme.lower() != "bearer" or credentials.credentials != settings.bridge_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bridge token")


def _desktop_env() -> dict[str, str]:
    env = dict()
    env.update(**{"DISPLAY": ":99"})
    return env


def _require_connect_session(connect_session_id: str) -> dict[str, Any]:
    session = connect_session_store.get_session(connect_session_id.strip())
    if not session:
        raise HTTPException(status_code=404, detail="Bridge connect session not found")
    return session


def _require_live_connect_session(connect_session_id: str) -> dict[str, Any]:
    session = _require_connect_session(connect_session_id)
    status_value = str(session.get("status") or "").strip().lower()
    if status_value not in ConnectSessionStore.ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Bridge connect session is no longer active (status: {status_value or 'unknown'})",
        )
    return session


def _desktop_frame_png() -> bytes:
    try:
        result = subprocess.run(
            [
                "/usr/bin/ffmpeg",
                "-loglevel",
                "error",
                "-f",
                "x11grab",
                "-video_size",
                "1440x900",
                "-i",
                ":99",
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "-",
            ],
            capture_output=True,
            check=True,
            env={**os.environ, **_desktop_env()},
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"Could not capture the bridge desktop frame: {exc.stderr.decode('utf-8', errors='ignore').strip() or exc}") from exc
    if not result.stdout:
        raise HTTPException(status_code=500, detail="Bridge desktop frame capture returned no image data")
    return result.stdout


def _activate_browser_window() -> None:
    try:
        search = subprocess.run(
            ["/usr/bin/xdotool", "search", "--onlyvisible", "--name", "Facebook|Chrome"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **_desktop_env()},
        )
        window_ids = [line.strip() for line in search.stdout.splitlines() if line.strip()]
        if window_ids:
            subprocess.run(
                ["/usr/bin/xdotool", "windowactivate", "--sync", window_ids[-1]],
                capture_output=True,
                check=False,
                env={**os.environ, **_desktop_env()},
            )
    except Exception:
        return


def _run_xdotool(args: list[str]) -> None:
    _activate_browser_window()
    try:
        subprocess.run(
            ["/usr/bin/xdotool", *args],
            capture_output=True,
            check=True,
            env={**os.environ, **_desktop_env()},
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"Bridge desktop input failed: {exc.stderr.decode('utf-8', errors='ignore').strip() or exc}") from exc


account_store = AccountStore(settings.data_dir)
connect_session_store = ConnectSessionStore(settings.data_dir)
asset_store = AssetStore(settings.data_dir, settings.assets_dir, ttl_seconds=settings.asset_ttl_seconds)
store = JobStore(settings.data_dir, default_delay_seconds=settings.default_job_delay_seconds, max_workers=settings.max_workers)
app = FastAPI(title="PosterPro Automation Bridge")


@app.get("/health")
def health() -> dict[str, Any]:
    jobs = store.list_jobs(limit=1)
    return {
        "ok": True,
        "configured": bool(settings.bridge_api_key),
        "runner_mode": settings.runner_mode,
        "data_dir": str(settings.data_dir),
        "job_count": len(store.jobs),
        "account_count": len(account_store.accounts),
        "connect_session_count": len(connect_session_store.sessions),
        "asset_count": len(asset_store.assets),
        "active_connect_session_id": connect_session_store.active_session_id(),
        "browser_submit_enabled": settings.browser_submit_enabled,
        "latest_job_id": jobs[0]["job_id"] if jobs else None,
    }


@app.post("/jobs/import", response_model=BridgeJobResponse, dependencies=[Depends(_require_auth)])
def create_import_job(payload: BridgeJobRequest) -> BridgeJobResponse:
    if payload.job_type != "import":
        raise HTTPException(status_code=400, detail="job_type must be import for this route")
    job = store.create_job(payload.job_type, payload.execution_mode, payload.payload)
    return BridgeJobResponse(**job)


@app.post("/jobs/crosspost", response_model=BridgeJobResponse, dependencies=[Depends(_require_auth)])
def create_crosspost_job(payload: BridgeJobRequest) -> BridgeJobResponse:
    if payload.job_type != "crosspost":
        raise HTTPException(status_code=400, detail="job_type must be crosspost for this route")
    job = store.create_job(payload.job_type, payload.execution_mode, payload.payload)
    return BridgeJobResponse(**job)


@app.get("/jobs", response_model=list[BridgeJobResponse], dependencies=[Depends(_require_auth)])
def list_jobs(job_type: str | None = None, status_value: str | None = None, limit: int = 100) -> list[BridgeJobResponse]:
    return [BridgeJobResponse(**job) for job in store.list_jobs(job_type=job_type, status_value=status_value, limit=limit)]


@app.get("/jobs/{job_id}", response_model=BridgeJobResponse, dependencies=[Depends(_require_auth)])
def get_job(job_id: str) -> BridgeJobResponse:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return BridgeJobResponse(**job)


@app.post("/jobs/{job_id}/cancel", response_model=BridgeJobResponse, dependencies=[Depends(_require_auth)])
def cancel_job(job_id: str) -> BridgeJobResponse:
    try:
        job = store.cancel_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return BridgeJobResponse(**job)


@app.get("/accounts", response_model=BridgeAccountsEnvelope, dependencies=[Depends(_require_auth)])
def list_accounts(marketplace: str | None = None) -> BridgeAccountsEnvelope:
    normalized = marketplace.strip().lower() if marketplace else None
    return BridgeAccountsEnvelope(accounts=[BridgeMarketplaceAccountResponse(**item) for item in account_store.list_accounts(normalized)])


@app.put("/accounts/{marketplace}/{account_key}", response_model=BridgeMarketplaceAccountResponse, dependencies=[Depends(_require_auth)])
def upsert_account(marketplace: str, account_key: str, payload: BridgeAccountUpsertRequest) -> BridgeMarketplaceAccountResponse:
    normalized_marketplace = marketplace.strip().lower()
    normalized_key = account_key.strip().lower()
    account = account_store.upsert_account(normalized_marketplace, normalized_key, payload)
    return BridgeMarketplaceAccountResponse(**account)


@app.post("/accounts/{marketplace}/{account_key}/session", response_model=BridgeMarketplaceAccountResponse, dependencies=[Depends(_require_auth)])
def update_account_session(marketplace: str, account_key: str, payload: BridgeAccountSessionUpdateRequest) -> BridgeMarketplaceAccountResponse:
    normalized_marketplace = marketplace.strip().lower()
    normalized_key = account_key.strip().lower()
    try:
        account = account_store.update_session(normalized_marketplace, normalized_key, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Bridge account not found") from exc
    return BridgeMarketplaceAccountResponse(**account)


def _prepare_connect_account(marketplace: str, account_key: str, payload: BridgeAccountConnectRequest) -> tuple[str, str, dict[str, Any], dict[str, Any] | None]:
    normalized_marketplace = marketplace.strip().lower()
    normalized_key = account_key.strip().lower()
    supported_marketplaces = {"facebook", "mercari", "poshmark", "etsy", "depop", "whatnot", "vinted"}
    if normalized_marketplace not in supported_marketplaces:
        raise HTTPException(
            status_code=400,
            detail=f"Bridge connect flow is not implemented for marketplace '{normalized_marketplace}'",
        )

    existing_account = account_store.get_account(normalized_marketplace, normalized_key)
    upsert_payload = BridgeAccountUpsertRequest(
        display_name=payload.display_name,
        login_handle=payload.login_handle,
        credential_secret=payload.credential_secret if payload.credential_secret is not None else (
            None if existing_account and existing_account.get("credential_configured") else f"{normalized_marketplace}-session-captured"
        ),
        notes=payload.notes,
        provider_enabled=payload.provider_enabled,
        browser_enabled=payload.browser_enabled,
        session_state=(existing_account or {}).get("session_state") or "draft",
        session_payload=(existing_account or {}).get("session_payload") or {},
        expires_at=payload.expires_at if payload.expires_at is not None else (existing_account or {}).get("expires_at"),
    )
    account = account_store.upsert_account(normalized_marketplace, normalized_key, upsert_payload)
    return normalized_marketplace, normalized_key, account, existing_account


@app.post("/accounts/{marketplace}/{account_key}/connect/start", response_model=BridgeConnectSessionResponse, dependencies=[Depends(_require_auth)])
def start_connect_account(marketplace: str, account_key: str, payload: BridgeAccountConnectRequest) -> BridgeConnectSessionResponse:
    normalized_marketplace, normalized_key, account, _ = _prepare_connect_account(marketplace, account_key, payload)
    try:
        session = connect_session_store.create_session(
            marketplace=normalized_marketplace,
            account_key=normalized_key,
            bridge_account=account,
            payload=payload,
        )
    except ActiveConnectSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return BridgeConnectSessionResponse(**session)


@app.get("/connect-sessions/{connect_session_id}", response_model=BridgeConnectSessionResponse, dependencies=[Depends(_require_auth)])
def get_connect_session(connect_session_id: str) -> BridgeConnectSessionResponse:
    session = connect_session_store.get_session(connect_session_id.strip())
    if not session:
        raise HTTPException(status_code=404, detail="Bridge connect session not found")
    return BridgeConnectSessionResponse(**session)


@app.get("/connect-sessions/{connect_session_id}/desktop-frame", dependencies=[Depends(_require_auth)])
def get_connect_session_desktop_frame(connect_session_id: str) -> Response:
    _require_live_connect_session(connect_session_id)
    return Response(
        content=_desktop_frame_png(),
        media_type="image/png",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/assets/{asset_id}", dependencies=[Depends(_require_auth)])
def get_asset(asset_id: str) -> Response:
    asset = asset_store.get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Bridge asset not found")
    file_path = Path(str(asset.get("file_path") or ""))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Bridge asset file is no longer available")
    return Response(
        content=file_path.read_bytes(),
        media_type=str(asset.get("content_type") or "application/octet-stream"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Content-Disposition": f'inline; filename="{str(asset.get("file_name") or file_path.name)}"',
        },
    )


@app.post("/connect-sessions/{connect_session_id}/desktop-actions/click", dependencies=[Depends(_require_auth)])
def click_connect_session_desktop(connect_session_id: str, payload: BridgeDesktopActionRequest) -> dict[str, Any]:
    _require_live_connect_session(connect_session_id)
    if payload.x is None or payload.y is None:
        raise HTTPException(status_code=400, detail="Desktop click requires x and y coordinates")
    _run_xdotool(["mousemove", "--sync", str(int(payload.x)), str(int(payload.y)), "click", "1"])
    return {"ok": True, "action": "click", "x": int(payload.x), "y": int(payload.y)}


@app.post("/connect-sessions/{connect_session_id}/desktop-actions/type", dependencies=[Depends(_require_auth)])
def type_connect_session_desktop(connect_session_id: str, payload: BridgeDesktopActionRequest) -> dict[str, Any]:
    _require_live_connect_session(connect_session_id)
    text = str(payload.text or "")
    if not text:
        raise HTTPException(status_code=400, detail="Desktop type requires text")
    _run_xdotool(["type", "--clearmodifiers", "--delay", "25", "--", text])
    return {"ok": True, "action": "type", "length": len(text)}


@app.post("/connect-sessions/{connect_session_id}/desktop-actions/key", dependencies=[Depends(_require_auth)])
def key_connect_session_desktop(connect_session_id: str, payload: BridgeDesktopActionRequest) -> dict[str, Any]:
    _require_live_connect_session(connect_session_id)
    key = str(payload.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Desktop key action requires a key")
    _run_xdotool(["key", "--clearmodifiers", key])
    return {"ok": True, "action": "key", "key": key}


@app.post("/accounts/{marketplace}/{account_key}/connect", response_model=BridgeMarketplaceAccountResponse, dependencies=[Depends(_require_auth)])
def connect_account(marketplace: str, account_key: str, payload: BridgeAccountConnectRequest) -> BridgeMarketplaceAccountResponse:
    normalized_marketplace, normalized_key, account, _ = _prepare_connect_account(marketplace, account_key, payload)

    runner = FacebookMarketplaceBrowserRunner(
        BrowserRunnerConfig(
            headless=settings.browser_headless,
            timeout_ms=settings.browser_timeout_ms,
            submit_enabled=False,
            screenshots_dir=settings.browser_screenshots_dir,
        )
    )
    try:
        result = runner.capture_session(
            account_key=normalized_key,
            bridge_account=account,
            login_handle=(payload.login_handle or "").strip() or None,
            wait_timeout_seconds=payload.wait_timeout_seconds,
        )
    except BrowserRunnerError as exc:
        account_store.update_session(
            normalized_marketplace,
            normalized_key,
            BridgeAccountSessionUpdateRequest(
                session_state="invalid",
                session_payload=account.get("session_payload") or {},
                expires_at=account.get("expires_at"),
                last_tested_at=datetime.now(UTC).isoformat(),
                notes=payload.notes if payload.notes is not None else account.get("notes"),
            ),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_state = result.get("session_state") or {}
    account = account_store.update_session(
        normalized_marketplace,
        normalized_key,
        BridgeAccountSessionUpdateRequest(
            session_state=session_state.get("session_state") or "active",
            session_payload=session_state.get("session_payload") or {},
            expires_at=payload.expires_at if payload.expires_at is not None else account.get("expires_at"),
            last_tested_at=datetime.now(UTC).isoformat(),
            notes=payload.notes if payload.notes is not None else account.get("notes"),
        ),
    )
    return BridgeMarketplaceAccountResponse(**account)
