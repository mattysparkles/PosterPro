from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models.enums import MarketplaceName

_DEFAULT_CHANNEL_SETTINGS = {
    MarketplaceName.ebay.value: {
        "enabled": True,
        "publish_mode": "direct_api",
        "status": "draft",
        "fulfillment": "shipping",
        "shipping_available": True,
    },
    MarketplaceName.facebook.value: {
        "enabled": False,
        "publish_mode": "manual_or_provider",
        "status": "manual_setup",
        "fulfillment": "local_or_shipping",
        "shipping_available": False,
        "renewal_mode": "manual",
    },
    MarketplaceName.etsy.value: {
        "enabled": False,
        "publish_mode": "manual_or_provider",
        "status": "draft",
        "fulfillment": "shipping",
        "shipping_available": True,
    },
    MarketplaceName.mercari.value: {
        "enabled": False,
        "publish_mode": "manual_or_provider",
        "status": "draft",
        "fulfillment": "shipping",
        "shipping_available": True,
    },
    MarketplaceName.poshmark.value: {
        "enabled": False,
        "publish_mode": "manual_or_provider",
        "status": "draft",
        "fulfillment": "shipping",
        "shipping_available": True,
    },
    MarketplaceName.depop.value: {
        "enabled": False,
        "publish_mode": "manual_or_provider",
        "status": "draft",
        "fulfillment": "shipping",
        "shipping_available": True,
    },
    MarketplaceName.whatnot.value: {
        "enabled": False,
        "publish_mode": "manual_or_provider",
        "status": "draft",
        "fulfillment": "live_sale",
        "shipping_available": True,
    },
    MarketplaceName.vinted.value: {
        "enabled": False,
        "publish_mode": "manual_or_provider",
        "status": "draft",
        "fulfillment": "shipping",
        "shipping_available": True,
    },
}


def default_marketplace_data() -> dict[str, Any]:
    return {
        "crosspost_mode": "approval_required",
        "targets": [MarketplaceName.ebay.value],
        "import_sources": [],
        "source_marketplace": None,
        "manual_entry": True,
        "shipping": {
            "mode": "calculated",
            "domestic_service": "usps_ground_advantage",
            "international_enabled": False,
            "local_pickup_enabled": False,
            "free_shipping": False,
            "handling_time_days": 2,
            "facebook_meetup_notes": "",
        },
        "channels": deepcopy(_DEFAULT_CHANNEL_SETTINGS),
    }


def normalize_marketplace_data(raw: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_marketplace_data()
    if not isinstance(raw, dict):
        return normalized

    for key in ("crosspost_mode", "source_marketplace", "manual_entry"):
        if key in raw:
            normalized[key] = raw[key]

    targets = raw.get("targets")
    if isinstance(targets, list):
        valid_targets = []
        for target in targets:
            name = str(target or "").strip().lower()
            if name in MarketplaceName._value2member_map_ and name not in valid_targets:
                valid_targets.append(name)
        normalized["targets"] = valid_targets or normalized["targets"]

    import_sources = raw.get("import_sources")
    if isinstance(import_sources, list):
        normalized["import_sources"] = [str(source).strip() for source in import_sources if str(source).strip()]

    shipping = raw.get("shipping")
    if isinstance(shipping, dict):
        normalized["shipping"].update({key: value for key, value in shipping.items() if value is not None})

    channels = raw.get("channels")
    if isinstance(channels, dict):
        for name, defaults in normalized["channels"].items():
            candidate = channels.get(name)
            if isinstance(candidate, dict):
                defaults.update({key: value for key, value in candidate.items() if value is not None})

    return normalized
