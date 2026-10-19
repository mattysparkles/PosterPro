from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.models.enums import MarketplaceName
from app.models.models import Listing, MarketplaceExtensionDevice, User
from app.services.marketplace_setup import MARKETPLACE_SETUP_PROFILES, load_manual_marketplace_settings


MIN_EXTENSION_VERSION = (0, 2, 0)


def _version_tuple(value: str | None) -> tuple[int, ...]:
    try:
        parts = tuple(int(part) for part in str(value or "").split(".")[:3])
        return (parts + (0, 0, 0))[:3] if parts else ()
    except ValueError:
        return ()


def has_online_compatible_extension(db, user_id: int, *, now: datetime | None = None) -> bool:
    """Return whether this user has a recently heartbeating supported agent."""
    current = now or datetime.now(UTC)
    cutoff = (current - timedelta(minutes=2)).replace(tzinfo=None)
    rows = db.execute(
        select(MarketplaceExtensionDevice.extension_version).where(
            MarketplaceExtensionDevice.user_id == int(user_id),
            MarketplaceExtensionDevice.revoked_at.is_(None),
            MarketplaceExtensionDevice.last_seen_at.is_not(None),
            MarketplaceExtensionDevice.last_seen_at >= cutoff,
        )
    ).scalars().all()
    return any(_version_tuple(version) >= MIN_EXTENSION_VERSION for version in rows)


def _listing_channel_settings(listing: Listing | None, marketplace: str) -> dict[str, Any]:
    data = listing.marketplace_data or {}
    channels = data.get("channels") or {}
    channel = channels.get(marketplace)
    return channel if isinstance(channel, dict) else {}


def resolve_execution_mode(*, listing: Listing | None, user: User | None, marketplace: str) -> str:
    market = marketplace.lower()
    if market == MarketplaceName.ebay.value:
        return "direct_api"

    channel_settings = _listing_channel_settings(listing, market)
    publish_mode = str(channel_settings.get("publish_mode") or "").strip().lower()
    manual_settings = load_manual_marketplace_settings(user).get(market, {})
    saved_mode = str(manual_settings.get("publish_mode") or "").strip().lower()
    profile_default = str(MARKETPLACE_SETUP_PROFILES.get(market, {}).get("default_publish_mode") or "manual_review").strip().lower()

    legacy_modes = {
        "",
        "manual_or_provider",
        "manual_or_browser",
        "browser_or_provider",
        "manual_setup",
        "draft",
        "approval_required",
    }

    candidate = publish_mode
    if candidate in legacy_modes:
        candidate = saved_mode if saved_mode not in legacy_modes else profile_default
    if candidate in legacy_modes:
        candidate = profile_default

    if candidate == "provider_assist":
        return "provider_assist"
    if candidate == "browser_assist":
        return "browser_assist"
    if candidate == "hosted_browser_assist":
        return "hosted_browser_assist"
    if candidate in {"manual_review", "draft_only", "manual_only"}:
        return "manual_only"
    return "manual_only"
