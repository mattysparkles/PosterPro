from __future__ import annotations

from typing import Any

from app.connectors.fallback_connector import ProxyAutomationConnector
from app.core import database
from app.models.models import User
from app.services.automation_bridge import AutomationBridgeError, submit_bridge_job, wait_for_bridge_job


def submit_sales_poll_job(*, execution_mode: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit a Facebook sales poll through the automation bridge.

    Kept as a small module-level seam so the connector can be tested without
    opening a browser and so future bridge implementations can replace the
    transport without changing sale reconciliation.
    """
    return submit_bridge_job(job_type="sales_poll", execution_mode=execution_mode, payload=payload)


class FacebookMarketplaceConnector(ProxyAutomationConnector):
    name = "facebook"

    async def poll_sales(self, user_id: int, since: str | None = None) -> list[dict[str, Any]]:
        """Poll assisted Facebook sales and return canonical sale events.

        Facebook has no supported direct API in PosterPro, so the request is
        delegated to the browser bridge.  A missing/stale connection is a
        recoverable empty poll rather than a fabricated sale event.
        """
        with database.SessionLocal() as db:
            user = db.get(User, user_id)
            settings = (user.settings_json or {}) if user else {}
            connection = ((settings.get("marketplace_connections") or {}).get("facebook") or {})

        account_key = connection.get("bridge_account_key") or connection.get("account_key")
        if not account_key:
            return []

        execution_mode = str(connection.get("import_mode") or connection.get("publish_mode") or "browser_assist")
        payload = {
            "marketplace": self.name,
            "account_key": account_key,
            "since": since,
            "operation": "sales_poll",
        }
        try:
            submitted = submit_sales_poll_job(execution_mode=execution_mode, payload=payload)
        except AutomationBridgeError:
            # A bridge that has not implemented sales polling must not make
            # the reconciliation worker fail the whole marketplace sweep.
            return []
        bridge_response = submitted.get("bridge_response") if isinstance(submitted, dict) else None
        job_id = (bridge_response or {}).get("job_id") if isinstance(bridge_response, dict) else None
        job_id = job_id or (submitted.get("job_id") if isinstance(submitted, dict) else None)
        if not job_id:
            return []

        try:
            completed = wait_for_bridge_job(job_id=str(job_id), timeout_seconds=120, poll_interval_seconds=1.0)
        except AutomationBridgeError:
            return []
        result = completed.get("result") if isinstance(completed, dict) else None
        events = result.get("events") if isinstance(result, dict) else None
        if not isinstance(events, list):
            return []

        normalized: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            normalized.append({**event, "marketplace": self.name})
        return normalized
