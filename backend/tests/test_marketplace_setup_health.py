from datetime import datetime, timedelta

from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, User
from app.services.marketplace_setup import marketplace_status_snapshot, save_manual_marketplace_settings


def test_ebay_setup_health_marks_expired_manual_token_for_reconnect():
    user = User(email="owner-ebay-health@example.com")
    account = MarketplaceAccount(
        user_id=1,
        marketplace=MarketplaceName.ebay,
        external_account_id="ebay-health-user",
        access_token="expired-token",
        refresh_token=None,
        token_expires_at=datetime.utcnow() - timedelta(hours=1),
    )

    snapshot = marketplace_status_snapshot(
        marketplace=MarketplaceName.ebay.value,
        account=account,
        user=user,
    )

    assert snapshot["connected"] is True
    assert snapshot["has_refresh_token"] is False
    assert snapshot["token_status"] == "expired"
    assert snapshot["import_ready"] is False
    assert snapshot["reconnect_required"] is True
    assert snapshot["publish_support_level"] == "direct_api"
    assert snapshot["import_support_level"] == "direct_api"
    assert snapshot["sales_sync_support_level"] == "direct_api"
    assert snapshot["ui_primary_action"] == "Reconnect eBay"
    assert snapshot["ui_state_tone"] in {"warning", "success"}
    assert isinstance(snapshot["ui_secondary_actions"], list)


def test_manual_marketplace_support_contracts_are_explicit():
    user = User(email="owner-manual-contracts@example.com")
    save_manual_marketplace_settings(
        user,
        MarketplaceName.etsy.value,
        {
            "display_name": "Sparkles Etsy",
            "workflow_state": "ready",
        },
    )
    save_manual_marketplace_settings(
        user,
        MarketplaceName.depop.value,
        {
            "display_name": "Sparkles Depop",
            "workflow_state": "ready",
        },
    )

    etsy_snapshot = marketplace_status_snapshot(
        marketplace=MarketplaceName.etsy.value,
        account=None,
        user=user,
    )
    depop_snapshot = marketplace_status_snapshot(
        marketplace=MarketplaceName.depop.value,
        account=None,
        user=user,
    )

    assert etsy_snapshot["publish_support_level"] == "browser_assist"
    assert etsy_snapshot["import_support_level"] == "csv_assist"
    assert etsy_snapshot["sales_sync_support_level"] == "unsupported"
    assert "assisted" in (etsy_snapshot["publish_support_note"] or "").lower()

    assert depop_snapshot["publish_support_level"] == "provider_assist"
    assert depop_snapshot["import_support_level"] == "provider_assist"
    assert depop_snapshot["sales_sync_support_level"] == "unsupported"
    assert etsy_snapshot["ui_primary_action"] == "Run assisted workflow"
    assert depop_snapshot["ui_primary_action"] == "Run assisted workflow"
    assert etsy_snapshot["ui_priority"] < depop_snapshot["ui_priority"]
