from datetime import datetime, timedelta

from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, User
from app.services.marketplace_setup import marketplace_status_snapshot


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
