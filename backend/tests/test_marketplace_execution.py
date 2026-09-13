from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models.models import Listing, MarketplaceExtensionDevice, User
from app.services.marketplace_execution import has_online_compatible_extension, resolve_execution_mode


def _device(db, user, *, version="0.2.2", seen_at=None, revoked_at=None):
    device = MarketplaceExtensionDevice(
        user_id=user.id,
        device_key=f"device-{uuid4()}",
        token_hash=f"token-{uuid4()}",
        extension_version=version,
        last_seen_at=seen_at,
        revoked_at=revoked_at,
    )
    db.add(device)
    db.flush()
    return device


def test_extension_compatibility_requires_recent_unrevoked_supported_version(db_session):
    now = datetime.now(UTC)
    user = User(email="extension-compatibility@example.com")
    db_session.add(user)
    db_session.flush()

    _device(db_session, user, version="0.1.9", seen_at=now.replace(tzinfo=None))
    _device(db_session, user, version="0.2.2", seen_at=(now - timedelta(minutes=3)).replace(tzinfo=None))
    assert not has_online_compatible_extension(db_session, user.id, now=now)

    _device(
        db_session,
        user,
        version="0.2.2",
        seen_at=now.replace(tzinfo=None),
        revoked_at=now.replace(tzinfo=None),
    )
    assert not has_online_compatible_extension(db_session, user.id, now=now)

    _device(db_session, user, version="0.2.2", seen_at=now.replace(tzinfo=None))
    assert has_online_compatible_extension(db_session, user.id, now=now)


def test_hosted_browser_assist_can_be_selected_from_saved_marketplace_settings(db_session):
    user = User(
        email="hosted-mode@example.com",
        settings_json={
            "marketplace_connections": {
                "facebook": {"publish_mode": "hosted_browser_assist"}
            }
        },
    )
    db_session.add(user)
    db_session.flush()
    listing = Listing(user_id=user.id, title="Lamp", description="Desk lamp", listing_price=15)
    db_session.add(listing)
    db_session.flush()

    assert resolve_execution_mode(listing=listing, user=user, marketplace="facebook") == "hosted_browser_assist"
