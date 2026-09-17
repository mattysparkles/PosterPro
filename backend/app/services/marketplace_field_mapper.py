from __future__ import annotations

from typing import Any

from app.models.models import Listing
from app.services.customer_description import sanitize_customer_description


MARKETPLACE_DESCRIPTION_LIMITS = {"mercari": 1000}


def _condense_description(text: str, limit: int = 1000) -> str:
    """Condense by complete sentences/sections, never by a blind substring."""
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    sentences = [part.strip() for part in __import__("re").split(r"(?<=[.!?])\s+", clean) if part.strip()]
    selected: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*selected, sentence])
        if len(candidate) > limit:
            break
        selected.append(sentence)
    result = " ".join(selected).strip()
    if not result and clean:
        words: list[str] = []
        for word in clean.split():
            candidate = " ".join([*words, word])
            if len(candidate) > limit:
                break
            words.append(word)
        result = " ".join(words)
    if len(result) < min(320, limit) and result != clean:
        # Preserve the most useful tail sections when the opening is unusually
        # long, still respecting sentence boundaries.
        for sentence in reversed(sentences):
            candidate = " ".join([sentence, *selected])
            if len(candidate) <= limit:
                result = candidate
    return result[:limit].rstrip(" ,;:-")


def marketplace_description_variants(listing: Listing) -> dict[str, str]:
    """Return canonical and channel-specific copy without mutating the listing."""
    canonical = str(getattr(listing, "canonical_description", None) or listing.description or "").strip()
    safe, _ = sanitize_customer_description(canonical)
    stored = getattr(listing, "marketplace_descriptions", None)
    stored = stored if isinstance(stored, dict) else {}
    return {
        "canonical": safe,
        "ebay": str(stored.get("ebay") or safe).strip(),
        "facebook": str(stored.get("facebook") or safe).strip(),
        "mercari": _condense_description(str(stored.get("mercari") or safe), 1000),
    }


def persist_marketplace_description_variants(listing: Listing, *, regenerate_generated: bool = False) -> dict[str, str]:
    """Materialize channel copy while preserving explicit operator overrides.

    ``regenerate_generated`` is used by controlled enrichment/reprocessing. A
    variant marked ``operator_edited`` is never replaced; unmarked legacy
    variants are treated as generated during that explicit refresh.
    """
    variants = marketplace_description_variants(listing)
    existing = getattr(listing, "marketplace_descriptions", None)
    existing = existing if isinstance(existing, dict) else {}
    provenance = dict(existing.get("_provenance") or {}) if isinstance(existing.get("_provenance"), dict) else {}
    marketplace_data = getattr(listing, "marketplace_data", None)
    if isinstance(marketplace_data, dict) and isinstance(marketplace_data.get("marketplace_description_provenance"), dict):
        provenance.update({str(key): str(value) for key, value in marketplace_data["marketplace_description_provenance"].items() if str(key).strip()})
    rendered = dict(existing)
    rendered.pop("_provenance", None)
    for channel, value in (("ebay", variants["canonical"]), ("facebook", variants["canonical"]), ("mercari", variants["mercari"])):
        if provenance.get(channel) == "operator_edited":
            continue
        if regenerate_generated or not rendered.get(channel):
            rendered[channel] = value
            provenance[channel] = "generated"
    if provenance:
        rendered["_provenance"] = provenance
    listing.marketplace_descriptions = rendered
    return {"canonical": variants["canonical"], **{key: str(value or "") for key, value in rendered.items()}}


def _trim_to_word_limit(value: str | None, limit: int) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    words = text.split(" ")
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).strip()


def _price(listing: Listing) -> float | None:
    return listing.listing_price or listing.suggested_price or listing.buy_it_now_price or listing.estimated_value


def _marketplace_override(listing: Listing, marketplace: str, field: str, fallback: Any = None) -> Any:
    data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    overrides = data.get("marketplace_overrides") if isinstance(data.get("marketplace_overrides"), dict) else {}
    market = overrides.get(str(marketplace).lower()) if isinstance(overrides.get(str(marketplace).lower()), dict) else {}
    value = market.get(field)
    return fallback if value in (None, "") else value


def _marketplace_item_specifics(listing: Listing, marketplace: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Apply destination-specific item-specific overrides without mutating canonical facts."""
    override = _marketplace_override(listing, marketplace, "item_specifics")
    return dict(override) if isinstance(override, dict) else dict(fallback)


def _specific(item_specifics: dict[str, Any], *names: str) -> Any:
    """Read known canonical specifics without inventing destination values."""
    normalized = {str(key).strip().casefold().replace("_", " "): value for key, value in item_specifics.items()}
    for name in names:
        value = normalized.get(name.casefold().replace("_", " "))
        if value not in (None, "", []):
            return value
    return None


def _canonical_marketplace_images(listing: Listing) -> list[str]:
    """Resolve normalized canonical media in its persisted order, then legacy URLs.

    The Timeline primary-photo selection is materialized as display_order zero.
    Rejected, reference-only, deleted, and Slate assets are never sent to a
    marketplace adapter.
    """
    from app.services.ebay_service import _to_public_image_url

    result: list[str] = []
    rows = list(getattr(listing, "listing_images", None) or [])
    rows.sort(key=lambda row: (int(row.get("display_order") or 0), str(row.get("storage_path") or row.get("url") or "")) if isinstance(row, dict) else (10**9, ""))
    for row in rows:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        role = str(row.get("timeline_role") or metadata.get("timeline_role") or "").upper()
        if (
            row.get("is_reference")
            or row.get("timeline_deleted")
            or row.get("is_slate")
            or metadata.get("timeline_deleted")
            or metadata.get("is_slate")
            or role in {"HEAD", "TAIL", "SLATE"}
            or str(row.get("operator_state") or "").lower() == "rejected"
        ):
            continue
        candidate = str(row.get("storage_path") or row.get("url") or row.get("local_path") or "").strip()
        resolved = _to_public_image_url(candidate) if candidate else ""
        if resolved and resolved not in result:
            result.append(resolved)
    if not result:
        for candidate in listing.image_urls or []:
            resolved = _to_public_image_url(str(candidate or "").strip())
            if resolved and resolved not in result:
                result.append(resolved)
    return result


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
    variants = marketplace_description_variants(listing)
    customer_description, removed_internal = sanitize_customer_description(variants["canonical"])
    specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    return {
        "title": listing.title,
        "description": customer_description,
        "description_variants": variants,
        "description_sanitized": bool(removed_internal),
        "price": _price(listing),
        "condition": listing.condition,
        "quantity": listing.quantity,
        # A canonical eBay category ID is not a valid taxonomy value on other
        # marketplaces. Their adapters receive a semantic classification hint
        # only and must resolve their own destination category.
        "category": listing.category_suggestion or (listing.item_specifics or {}).get("Type"),
        "item_specifics": specifics,
        "brand": _specific(specifics, "Brand"),
        "size": _specific(specifics, "Size", "Apparel Size"),
        "color": _specific(specifics, "Color", "Colour"),
        "material": _specific(specifics, "Material", "Materials"),
        "dimensions": {
            key: _specific(specifics, key)
            for key in ("Dimensions", "Item Length", "Item Width", "Item Height")
            if _specific(specifics, key) not in (None, "", [])
        },
        "weight": _specific(specifics, "Item Weight", "Weight", "Shipping Weight"),
        "sku": source_metadata.get("sku") or (listing.marketplace_data or {}).get("sku"),
        "inventory_id": source_metadata.get("inventory_id") or str(listing.id),
        "tags": listing.tags or [],
        "image_urls": _canonical_marketplace_images(listing),
        "shipping": {
            "mode": shipping.get("mode"),
            "domestic_service": shipping.get("domestic_service"),
            "handling_time_days": shipping.get("handling_time_days"),
            "free_shipping": shipping.get("free_shipping"),
            "international_enabled": shipping.get("international_enabled"),
            "local_pickup_enabled": shipping.get("local_pickup_enabled"),
            "location": shipping.get("location"),
            "postal_code": shipping.get("postal_code"),
            "parcel_size": shipping.get("parcel_size"),
            "parcel_weight": shipping.get("parcel_weight"),
            "shipping_payer": shipping.get("shipping_payer"),
            "shipping_method": shipping.get("shipping_method"),
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
        destination_specifics = _marketplace_item_specifics(listing, market, shared["item_specifics"])
        return {
            "marketplace": market,
            "title": _marketplace_override(listing, market, "title", shared["title"]),
            "description": _marketplace_override(listing, market, "description", shared["description_variants"]["ebay"]),
            "price": _marketplace_override(listing, market, "price", shared["price"]),
            "condition": _marketplace_override(listing, market, "condition", shared["condition"]),
            "quantity": shared["quantity"],
            # Category edits are destination-scoped. Older mutation plans use
            # ``category`` while newer callers may provide ``category_id``;
            # accept both without ever replacing the canonical category.
            "category_id": _marketplace_override(
                listing,
                market,
                "category_id",
                _marketplace_override(listing, market, "category", listing.category_id),
            ),
            "item_specifics": destination_specifics,
            "brand": shared["brand"],
            "size": shared["size"],
            "color": shared["color"],
            "material": shared["material"],
            "dimensions": shared["dimensions"],
            "weight": shared["weight"],
            "sku": shared["sku"],
            "inventory_id": shared["inventory_id"],
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
        destination_specifics = _marketplace_item_specifics(listing, market, shared["item_specifics"])
        return {
            "marketplace": market,
            "title": _marketplace_override(listing, market, "title", shared["title"]),
            "description": _marketplace_override(listing, market, "description", shared["description_variants"]["facebook"]),
            "price": _marketplace_override(listing, market, "price", shared["price"]),
            "condition": _marketplace_override(listing, market, "condition", shared["condition"]),
            "availability": "in stock" if (shared["quantity"] or 0) > 0 else "out of stock",
            "delivery_method": "local_pickup"
            if shipping.get("local_pickup_enabled")
            else "shipping"
            if shipping.get("mode") in {"calculated", "flat"}
            else "manual",
            "meetup_notes": meetup_notes,
            "image_urls": _facebook_image_urls(shared["image_urls"]),
            "category_hint": shared["category"],
            "brand": _specific(destination_specifics, "Brand") or shared["brand"],
            "size": _specific(destination_specifics, "Size", "Apparel Size") or shared["size"],
            "color": _specific(destination_specifics, "Color", "Colour") or shared["color"],
            "material": _specific(destination_specifics, "Material", "Materials") or shared["material"],
            "item_specifics": destination_specifics,
            "dimensions": shared["dimensions"],
            "weight": shared["weight"],
            "location": shipping.get("location") or shipping.get("postal_code"),
        }

    if market == "etsy":
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description_variants"].get("etsy") or shared["description"],
            "price": shared["price"],
            "quantity": shared["quantity"],
            "category_hint": shared["category"],
            "item_specifics": shared["item_specifics"],
            "brand": shared["brand"],
            "size": shared["size"],
            "color": shared["color"],
            "dimensions": shared["dimensions"],
            "weight": shared["weight"],
            "image_urls": shared["image_urls"],
            "shipping_profile": {
                "mode": shipping.get("mode") or "flat",
                "domestic_service": shipping.get("domestic_service"),
                "handling_time_days": shipping.get("handling_time_days"),
                "free_shipping": shipping.get("free_shipping"),
            },
            "materials": shared["tags"][:10],
            # Etsy's required maker/age classifications are not inferred from
            # a resale listing. Leave them absent until truthful marketplace-
            # specific values have been supplied.
            "who_made": _specific(shared["item_specifics"], "Who Made"),
            "when_made": _specific(shared["item_specifics"], "When Made"),
        }

    if market == "mercari":
        destination_specifics = _marketplace_item_specifics(listing, market, shared["item_specifics"])
        return {
            "marketplace": market,
            "title": _marketplace_override(listing, market, "title", shared["title"]),
            "description": _marketplace_override(listing, market, "description", shared["description_variants"]["mercari"]),
            "price": _marketplace_override(listing, market, "price", shared["price"]),
            "condition": _marketplace_override(listing, market, "condition", shared["condition"]),
            "category_hint": shared["category"],
            "brand": _specific(destination_specifics, "Brand") or shared["brand"],
            "size": _specific(destination_specifics, "Size", "Apparel Size") or shared["size"],
            "color": _specific(destination_specifics, "Color", "Colour") or shared["color"],
            "material": _specific(destination_specifics, "Material", "Materials") or shared["material"],
            "item_specifics": destination_specifics,
            "dimensions": shared["dimensions"],
            "weight": shared["weight"],
            "quantity": shared["quantity"],
            "inventory_id": shared["inventory_id"],
            "image_urls": shared["image_urls"],
            "shipping": {
                "prepaid": shipping.get("free_shipping"),
                "local_pickup_enabled": shipping.get("local_pickup_enabled"),
                "shipping_payer": shipping.get("shipping_payer"),
                "shipping_method": shipping.get("shipping_method"),
                "parcel_size": shipping.get("parcel_size"),
                "parcel_weight": shipping.get("parcel_weight") or shared["weight"],
            },
        }

    if market == "poshmark":
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "listing_price": shared["price"],
            "size": shared["size"],
            "brand": shared["brand"],
            "color": shared["color"],
            "material": shared["material"],
            "dimensions": shared["dimensions"],
            "weight": shared["weight"],
            "shipping": {
                "parcel_weight": shared["weight"],
                "parcel_size": shipping.get("parcel_size"),
                "shipping_payer": shipping.get("shipping_payer"),
            },
            "quantity": shared["quantity"],
            "original_price": (shared["item_specifics"] or {}).get("Original Price"),
            "subcategory_hint": _specific(shared["item_specifics"], "Subcategory", "Department"),
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
            "brand": shared["brand"],
            "size": shared["size"],
            "color": shared["color"],
            "material": shared["material"],
            "dimensions": shared["dimensions"],
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
            "brand": shared["brand"],
            "size": shared["size"],
            "color": shared["color"],
            "material": shared["material"],
            "dimensions": shared["dimensions"],
            "weight": shared["weight"],
            "condition": shared["condition"],
            "category_hint": shared["category"],
            "image_urls": shared["image_urls"],
            "shipping": {
                "domestic_service": shipping.get("domestic_service"),
                "international_enabled": shipping.get("international_enabled"),
                "parcel_size": shipping.get("parcel_size"),
                "parcel_weight": shipping.get("parcel_weight") or shared["weight"],
            },
        }

    if market == "offerup":
        return {
            "marketplace": market,
            "title": shared["title"],
            "description": shared["description"],
            "price": shared["price"],
            "condition": shared["condition"],
            "category_hint": shared["category"],
            "location": shipping.get("location") or shipping.get("postal_code"),
            "brand": shared["brand"],
            "size": shared["size"],
            "color": shared["color"],
            "material": shared["material"],
            "dimensions": shared["dimensions"],
            "weight": shared["weight"],
            "shipping": {
                "enabled": bool(shipping.get("free_shipping") or shipping.get("mode")),
                "local_pickup_enabled": shipping.get("local_pickup_enabled"),
                "shipping_payer": shipping.get("shipping_payer"),
                "parcel_size": shipping.get("parcel_size"),
                "parcel_weight": shipping.get("parcel_weight") or shared["weight"],
            },
            "image_urls": shared["image_urls"],
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
