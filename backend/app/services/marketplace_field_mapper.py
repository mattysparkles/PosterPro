from __future__ import annotations

from typing import Any

from app.models.models import Listing


def _price(listing: Listing) -> float | None:
    return listing.listing_price or listing.suggested_price or listing.buy_it_now_price or listing.estimated_value


def _shared_payload(listing: Listing) -> dict[str, Any]:
    marketplace_shipping = ((listing.marketplace_data or {}).get("shipping") or {}) if isinstance(listing.marketplace_data, dict) else {}
    shipping_profile = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
    shipping = {
        "mode": shipping_profile.get("shipping_charge_mode"),
        "free_shipping": shipping_profile.get("free_shipping"),
        "buyer_pays_shipping": shipping_profile.get("buyer_pays_shipping"),
        **shipping_profile,
        **marketplace_shipping,
    }
    return {
        "title": listing.title,
        "description": listing.description,
        "price": _price(listing),
        "condition": listing.condition,
        "quantity": listing.quantity,
        "category": listing.category_id or listing.category_suggestion,
        "item_specifics": listing.item_specifics or {},
        "tags": listing.tags or [],
        "image_urls": listing.image_urls or [],
        "shipping": {
            "mode": shipping.get("mode"),
            "domestic_service": shipping.get("domestic_service"),
            "handling_time_days": shipping.get("handling_time_days"),
            "free_shipping": shipping.get("free_shipping"),
            "international_enabled": shipping.get("international_enabled"),
            "local_pickup_enabled": shipping.get("local_pickup_enabled"),
        },
    }


def _facebook_image_urls(image_urls: list[str]) -> list[str]:
    cleaned = [str(url).strip() for url in (image_urls or []) if str(url).strip()]
    if not cleaned:
        return []
    preferred = [url for url in cleaned if not url.lower().endswith(".webp")]
    active = preferred or cleaned
    return active[:8]


def build_marketplace_payload(listing: Listing, marketplace: str) -> dict[str, Any]:
    market = marketplace.lower()
    shared = _shared_payload(listing)
    shipping = shared["shipping"]

    if market == "ebay":
        marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "price": shared["price"],
            "condition": shared["condition"],
            "quantity": shared["quantity"],
            "category_id": listing.category_id,
            "item_specifics": shared["item_specifics"],
            "item_specifics_provenance": marketplace_data.get("ebay_item_specifics_provenance") or {},
            "item_specifics_approximate": marketplace_data.get("ebay_item_specifics_approximate") or [],
            "image_urls": shared["image_urls"],
            "shipping_policy": {
                "service": shipping.get("domestic_service"),
                "free_shipping": shipping.get("free_shipping"),
                "handling_time_days": shipping.get("handling_time_days"),
                "international_enabled": shipping.get("international_enabled"),
            },
        }

    if market == "facebook":
        meetup_notes = (((listing.marketplace_data or {}).get("shipping") or {}).get("facebook_meetup_notes"))
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "price": shared["price"],
            "condition": shared["condition"],
            "availability": "in stock" if (shared["quantity"] or 0) > 0 else "out of stock",
            "delivery_method": "local_pickup"
            if shipping.get("local_pickup_enabled")
            else "shipping"
            if shipping.get("mode") in {"calculated", "flat"}
            else "manual",
            "meetup_notes": meetup_notes,
            "image_urls": _facebook_image_urls(shared["image_urls"]),
            "category_hint": shared["category"],
        }

    if market == "etsy":
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "price": shared["price"],
            "quantity": shared["quantity"],
            "category_hint": shared["category"],
            "item_specifics": shared["item_specifics"],
            "image_urls": shared["image_urls"],
            "shipping_profile": {
                "mode": shipping.get("mode") or "flat",
                "domestic_service": shipping.get("domestic_service"),
                "handling_time_days": shipping.get("handling_time_days"),
                "free_shipping": shipping.get("free_shipping"),
            },
            "materials": shared["tags"][:10],
            "who_made": "i_did",
            "when_made": "made_to_order",
        }

    if market == "mercari":
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "price": shared["price"],
            "condition": shared["condition"],
            "category_hint": shared["category"],
            "brand": (shared["item_specifics"] or {}).get("Brand"),
            "image_urls": shared["image_urls"],
            "shipping": {
                "prepaid": shipping.get("free_shipping"),
                "local_pickup_enabled": shipping.get("local_pickup_enabled"),
            },
        }

    if market == "poshmark":
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "listing_price": shared["price"],
            "size": (shared["item_specifics"] or {}).get("Size"),
            "brand": (shared["item_specifics"] or {}).get("Brand"),
            "category_hint": shared["category"],
            "condition": shared["condition"],
            "image_urls": shared["image_urls"],
        }

    if market == "depop":
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "price": shared["price"],
            "brand": (shared["item_specifics"] or {}).get("Brand"),
            "category_hint": shared["category"],
            "condition": shared["condition"],
            "image_urls": shared["image_urls"],
            "hashtags": shared["tags"][:5],
        }

    if market == "whatnot":
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "starting_bid": shared["price"],
            "category_hint": shared["category"],
            "condition": shared["condition"],
            "quantity": shared["quantity"],
            "image_urls": shared["image_urls"],
            "live_sale_ready": True,
        }

    if market == "vinted":
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "price": shared["price"],
            "brand": (shared["item_specifics"] or {}).get("Brand"),
            "size": (shared["item_specifics"] or {}).get("Size"),
            "condition": shared["condition"],
            "category_hint": shared["category"],
            "image_urls": shared["image_urls"],
            "shipping": {
                "domestic_service": shipping.get("domestic_service"),
                "international_enabled": shipping.get("international_enabled"),
            },
        }

    return {
        "marketplace": market,
        "headline": shared["title"],
        "description": shared["description"],
        "price": shared["price"],
        "condition": shared["condition"],
        "quantity": shared["quantity"],
        "category_hint": shared["category"],
        "item_specifics": shared["item_specifics"],
        "image_urls": shared["image_urls"],
        "shipping": shipping,
    }


def normalize_import_payload(*, source_marketplace: str, payload: dict[str, Any]) -> dict[str, Any]:
    item = payload.get("listing") if isinstance(payload.get("listing"), dict) else payload
    images = item.get("image_urls") or item.get("images") or item.get("photos") or []
    if not isinstance(images, list):
        images = []
    item_specifics = item.get("item_specifics") or item.get("attributes") or {}
    if not isinstance(item_specifics, dict):
        item_specifics = {}
    tags = item.get("tags") or item.get("keywords") or []
    if not isinstance(tags, list):
        tags = []

    return {
        "title": item.get("title") or item.get("headline") or "",
        "description": item.get("description") or item.get("details") or "",
        "category_id": item.get("category_id") or item.get("category") or "",
        "condition": item.get("condition") or "",
        "listing_price": item.get("listing_price") or item.get("price"),
        "quantity": item.get("quantity") or 1,
        "image_urls": [str(url).strip() for url in images if str(url).strip()],
        "item_specifics": item_specifics,
        "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
        "source_marketplace": source_marketplace.lower(),
    }
