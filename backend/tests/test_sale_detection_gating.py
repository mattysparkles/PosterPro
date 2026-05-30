from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, User
from app.services.sale_detection_service import SaleDetectionService


def test_sale_detection_defaults_only_poll_supported_marketplaces(db_session, monkeypatch):
    user = User(email="sales@example.com", password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    account = MarketplaceAccount(
        user_id=user.id,
        marketplace=MarketplaceName.ebay,
        external_account_id="acct",
        access_token="token",
        refresh_token=None,
        token_expires_at=None,
    )
    db_session.add(account)
    db_session.commit()

    user.sale_detection_platforms = [MarketplaceName.ebay.value, MarketplaceName.mercari.value]
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    called: list[str] = []

    class _Connector:
        async def poll_sales(self, user_id: int, *, since: str):  # noqa: ARG002
            return []

    def _fake_get_connector(marketplace: str):
        called.append(marketplace)
        return _Connector()

    monkeypatch.setattr("app.services.sale_detection_service.get_connector", _fake_get_connector)

    service = SaleDetectionService()
    result = service.poll_user_sales(db_session, user, dry_run=True, lookback_minutes=30)

    assert result["marketplaces_requested"] == [MarketplaceName.ebay.value, MarketplaceName.mercari.value]
    assert result["marketplaces_polled"] == [MarketplaceName.ebay.value]
    assert result["marketplaces_skipped"] == [MarketplaceName.mercari.value]
    assert called == [MarketplaceName.ebay.value]
