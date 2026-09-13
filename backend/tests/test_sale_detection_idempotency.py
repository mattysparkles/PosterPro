from uuid import uuid4

from app.models.enums import MarketplaceName
from app.models.models import Sale, User
from app.services.sale_detection_service import SaleDetectionService


def test_sale_dedup_uses_order_identity_when_order_is_available(db_session):
    user = User(email=f"sale-idempotency-{uuid4()}@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        Sale(
            user_id=user.id,
            platform=MarketplaceName.mercari,
            marketplace_order_id="order-1",
            marketplace_listing_id="listing-relisted",
            quantity=1,
            amount=20.0,
            status="SYNCED",
        )
    )
    db_session.commit()

    service = SaleDetectionService()
    assert service._already_processed(
        db_session, user.id, "mercari", "order-1", "listing-relisted"
    )
    # Same external listing, different order: a real subsequent sale.
    assert not service._already_processed(
        db_session, user.id, "mercari", "order-2", "listing-relisted"
    )


def test_sale_dedup_falls_back_to_listing_identity_without_order_id(db_session):
    user = User(email=f"sale-idempotency-fallback-{uuid4()}@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        Sale(
            user_id=user.id,
            platform=MarketplaceName.facebook,
            marketplace_order_id=None,
            marketplace_listing_id="listing-no-order",
            quantity=1,
            status="SYNCED",
        )
    )
    db_session.commit()

    service = SaleDetectionService()
    assert service._already_processed(
        db_session, user.id, "facebook", None, "listing-no-order"
    )
    assert not service._already_processed(
        db_session, user.id, "facebook", None, "another-listing"
    )
