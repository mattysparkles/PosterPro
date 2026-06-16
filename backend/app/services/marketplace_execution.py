from __future__ import annotations

from typing import Any

from app.models.enums import MarketplaceName
from app.models.models import Listing, User
from app.services.marketplace_setup import load_manual_marketplace_settings


def _listing_channel_settings(listing: Listing | None, marketplace: str) -> dict[str, Any]:
    data = listing.marketplace_data or {}
    channels = data.get("channels") or {}
    channel = channels.get(marketplace)
    return channel if isinstance(channel, dict) else {}


def resolve_execution_mode(*, listing: Listing | None, user: User | None, marketplace: str) -> str:
    market = marketplace.lower()
    channel_settings = _listing_channel_settings(listing, market)
    publish_mode = str(channel_settings.get("publish_mode") or "").strip().lower()
    manual_settings = load_manual_marketplace_settings(user).get(market, {})
    saved_mode = str(manual_settings.get("publish_mode") or "").strip().lower()
    candidate = publish_mode or saved_mode

    if market == MarketplaceName.ebay.value:
        if candidate in {"browser_assist", "provider_assist"}:
            return candidate
        return "direct_api"

    if candidate == "provider_assist":
        return "provider_assist"
    if candidate == "browser_assist":
        return "browser_assist"
    if candidate in {"manual_review", "draft_only"}:
        return "manual_only"
    return "manual_only"
