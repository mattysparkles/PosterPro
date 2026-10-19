from app.models.models import User
from app.services.marketplace_setup import marketplace_status_snapshot


def _user_with_manual_marketplace(marketplace: str, *, workflow_state: str, bridge_account_key: str) -> User:
    return User(
        email="sales@example.com",
        settings_json={
            "marketplace_connections": {
                marketplace: {
                    "workflow_state": workflow_state,
                    "bridge_account_key": bridge_account_key,
                }
            }
        },
    )


def test_sales_sync_contract_allows_facebook_when_ready():
    user = _user_with_manual_marketplace("facebook", workflow_state="ready", bridge_account_key="fb-account")
    snapshot = marketplace_status_snapshot(marketplace="facebook", account=None, user=user)
    assert snapshot["sales_sync_support_level"] != "unsupported"
    assert snapshot["can_sync_sales"] is True


def test_sales_sync_contract_blocks_non_supported_marketplace_even_when_ready():
    user = _user_with_manual_marketplace("etsy", workflow_state="ready", bridge_account_key="etsy-account")
    snapshot = marketplace_status_snapshot(marketplace="etsy", account=None, user=user)
    assert snapshot["sales_sync_support_level"] == "unsupported"
    assert snapshot["can_sync_sales"] is False

