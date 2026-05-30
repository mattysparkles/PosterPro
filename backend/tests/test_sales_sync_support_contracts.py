from app.services.marketplace_setup import marketplace_status_snapshot
from app.models.models import User


def test_facebook_sales_sync_support_contract_enables_can_sync_sales_when_ready(db_session):
    user = User(
        email="fb-sales@example.com",
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

    snapshot = marketplace_status_snapshot(marketplace="facebook", account=None, user=user)
    assert snapshot["sales_sync_support_level"] == "browser_assist"
    assert snapshot["can_sync_sales"] is True

