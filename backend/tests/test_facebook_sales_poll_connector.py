import pytest

from app.connectors.facebook_marketplace_connector import FacebookMarketplaceConnector
from app.models.models import User


@pytest.mark.anyio
async def test_facebook_connector_poll_sales_uses_bridge_job(db_session, monkeypatch):
    user = User(
        email="fb-connector@example.com",
        password_hash="x",
        settings_json={
            "marketplace_connections": {
                "facebook": {
                    "workflow_state": "ready",
                    "bridge_account_key": "fb-demo",
                    "display_name": "FB",
                    "import_mode": "browser_assist",
                    "publish_mode": "browser_assist",
                }
            }
        },
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    called: dict[str, object] = {}

    def _fake_submit_sales_poll_job(*, execution_mode: str, payload: dict):
        called["execution_mode"] = execution_mode
        called["payload"] = payload
        return {"status": "SUBMITTED_TO_BRIDGE", "bridge_response": {"job_id": "job-1"}}

    def _fake_wait_for_bridge_job(*, job_id: str, timeout_seconds: int = 120, poll_interval_seconds: float = 1.0):  # noqa: ARG001
        return {
            "job_id": job_id,
            "status": "completed",
            "result": {
                "events": [
                    {
                        "marketplace": "facebook",
                        "marketplace_listing_id": "123",
                        "amount": 10.0,
                        "currency": "USD",
                        "sold_at": "2026-05-24T00:00:00Z",
                        "quantity": 1,
                    }
                ]
            },
        }

    monkeypatch.setattr(
        "app.connectors.facebook_marketplace_connector.submit_sales_poll_job",
        _fake_submit_sales_poll_job,
    )
    monkeypatch.setattr(
        "app.connectors.facebook_marketplace_connector.wait_for_bridge_job",
        _fake_wait_for_bridge_job,
    )

    connector = FacebookMarketplaceConnector()
    events = await connector.poll_sales(user.id, since="2026-05-24T00:00:00Z")
    assert called["execution_mode"] == "browser_assist"
    assert (called["payload"] or {}).get("account_key") == "fb-demo"
    assert events and events[0]["marketplace"] == "facebook"

