from __future__ import annotations

import hashlib
import asyncio
import hmac
import json
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import String, and_, asc, cast, desc, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.secrets import encrypt_secret
from app.models.enums import EbayPublishStatus, ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, Sale, StorefrontAffiliateClick, StorefrontOrder, StorefrontOrderItem, StorefrontPaymentAttempt, StorefrontProfile, User
from app.services.commerce_entitlements import commerce_entitlement, public_commerce_entitlements
from app.services.listing_review import normalize_listing_images
from app.services.sale_detection_service import SaleDetectionService
from app.services.process_notifications import create_process_notification

router = APIRouter(tags=["storefront"])
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])?$")
_MARKET_HOSTS = {
    "ebay": {"ebay.com", "www.ebay.com", "ebay.co.uk", "www.ebay.co.uk", "ebay.ca", "www.ebay.ca"},
    "facebook": {"facebook.com", "www.facebook.com", "m.facebook.com"},
    "mercari": {"mercari.com", "www.mercari.com"},
    "poshmark": {"poshmark.com", "www.poshmark.com"},
    "vinted": {"vinted.com", "www.vinted.com", "vinted.co.uk", "www.vinted.co.uk"},
    "etsy": {"etsy.com", "www.etsy.com"},
    "offerup": {"offerup.com", "www.offerup.com"},
}
_CRYPTO = {"BTC", "ETH", "XLM", "XMR", "DOGE", "PEP", "LTC", "RVN", "DASH", "XRP"}


def _crypto_address_looks_valid(currency: str, address: str) -> bool:
    value = address.strip()
    checks = {
        "BTC": r"(?:bc1[a-zA-HJ-NP-Z0-9]{11,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})",
        "ETH": r"0x[a-fA-F0-9]{40}",
        "XLM": r"G[A-Z2-7]{55}",
        "XMR": r"[48][1-9A-HJ-NP-Za-km-z]{94,105}",
        "DOGE": r"D[1-9A-HJ-NP-Za-km-z]{25,34}",
        "PEP": r"[DP][1-9A-HJ-NP-Za-km-z]{25,35}",
        "LTC": r"(?:ltc1[a-zA-HJ-NP-Z0-9]{11,87}|[LM3][a-km-zA-HJ-NP-Z1-9]{25,34})",
        "RVN": r"R[1-9A-HJ-NP-Za-km-z]{25,34}",
        "DASH": r"X[1-9A-HJ-NP-Za-km-z]{25,34}",
        "XRP": r"r[1-9A-HJ-NP-Za-km-z]{24,34}",
    }
    return bool(re.fullmatch(checks[currency], value))


class StorefrontSettingsUpdate(BaseModel):
    slug: str = Field(min_length=3, max_length=80)
    store_name: str = Field(min_length=1, max_length=160)
    enabled: bool = False
    description: str = Field(default="", max_length=1200)
    logo_url: str | None = Field(default=None, max_length=1000)
    banner_url: str | None = Field(default=None, max_length=1000)
    contact_email: str | None = Field(default=None, max_length=255)
    accent_color: str = Field(default="#1d4f7a", pattern=r"^#[0-9a-fA-F]{6}$")
    public_settings: dict = Field(default_factory=dict)
    payment_settings: dict = Field(default_factory=dict)
    provider_secrets: dict[str, str] = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not _SLUG.fullmatch(value):
            raise ValueError("Use 3–80 lowercase letters, numbers, or hyphens; start and end with a letter or number.")
        return value


class StoreCheckoutRequest(BaseModel):
    listing_id: int = Field(gt=0)
    customer_name: str = Field(min_length=1, max_length=255)
    customer_email: str = Field(min_length=3, max_length=255)
    payment_method: str = Field(pattern=r"^(cashapp|venmo)$")
    idempotency_key: str = Field(min_length=12, max_length=128)
    shipping_address: dict = Field(default_factory=dict)


class StoreOrderShipmentUpdate(BaseModel):
    carrier: str = Field(min_length=1, max_length=64)
    tracking_number: str = Field(min_length=3, max_length=120)


class ManualPaymentConfirmation(BaseModel):
    reference: str | None = Field(default=None, max_length=255)
    internal_note: str | None = Field(default=None, max_length=1000)


def _profile(db: Session, slug: str, *, require_enabled: bool = True) -> StorefrontProfile:
    profile = db.execute(select(StorefrontProfile).where(StorefrontProfile.slug == slug.strip().lower())).scalar_one_or_none()
    if profile is None or (require_enabled and not profile.enabled):
        raise HTTPException(status_code=404, detail="Store not found")
    return profile


def _require_public_catalog(db: Session, profile: StorefrontProfile) -> User:
    user = db.get(User, profile.user_id)
    if not commerce_entitlement(user, "storefront.public_catalog")["entitled"]:
        raise HTTPException(status_code=404, detail="Store not found")
    return user


def _public_url(path: str | None) -> str | None:
    if not path:
        return None
    raw = str(path).strip()
    if raw.startswith(("/media/", "/api/media/")):
        return raw.replace("/api/media/", "/media/", 1)
    try:
        resolved = Path(raw).resolve()
        return f"/media/{resolved.relative_to(Path(settings.storage_root).resolve()).as_posix()}"
    except (ValueError, OSError):
        parsed = urlparse(raw)
        return raw if parsed.scheme == "https" and parsed.netloc else None


def _safe_marketplace_url(marketplace: str, raw_url: str | None, external_id: str | None = None) -> str | None:
    market = marketplace.lower()
    candidate = str(raw_url or "").strip()
    parsed = urlparse(candidate)
    allowed = _MARKET_HOSTS.get(market, set())
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname.lower() in allowed and parsed.username is None and parsed.password is None:
        if market == "ebay" and external_id:
            # Do not trust a stale/mismatched stored URL to send the buyer to
            # another seller's item. Prefer the exact stored item identity.
            path_ids = [segment for segment in parsed.path.split("/") if segment == external_id]
            if not path_ids:
                return f"https://www.ebay.com/itm/{external_id}" if re.fullmatch(r"[A-Za-z0-9-]{1,64}", external_id) else None
        # Stored marketplace URLs can contain stale tracker/query values. Only
        # keep the canonical path here; valid EPN parameters are added server-side.
        return urlunparse(parsed._replace(query="", fragment=""))
    if market == "ebay" and external_id and re.fullmatch(r"[A-Za-z0-9-]{1,64}", external_id):
        return f"https://www.ebay.com/itm/{external_id}"
    return None


def _epn_url(url: str, *, custom_id: str | None = None) -> str | None:
    required = {
        "campid": settings.ebay_epn_campaign_id,
        "mkcid": settings.ebay_epn_channel_id,
        "mkrid": settings.ebay_epn_rotation_id,
        "toolid": settings.ebay_epn_tool_id,
        "mkevt": settings.ebay_epn_event_type,
    }
    if not all(str(value or "").strip() for value in required.values()):
        return None
    parsed = urlparse(url)
    if parsed.hostname not in _MARKET_HOSTS["ebay"]:
        return None
    query: dict[str, str] = {}
    query.update({key: str(value).strip() for key, value in required.items()})
    if custom_id:
        query["customid"] = custom_id
    return urlunparse(parsed._replace(query=urlencode(query)))


def _product_payload(listing: Listing, profile: StorefrontProfile, *, detail: bool = False) -> dict:
    rows = sorted(listing.marketplace_listings or [], key=lambda row: (row.updated_at or listing.updated_at, row.id or 0), reverse=True)
    destinations = {}
    for row in rows:
        status = str(getattr(row.status, "value", row.status) or "").upper()
        market = str(getattr(row.marketplace, "value", row.marketplace) or "").lower()
        identity = str(row.marketplace_listing_id or "").strip()
        if status not in {"PUBLISHED", "UPDATED"} or not identity or market in destinations:
            continue
        raw = row.raw_response if isinstance(row.raw_response, dict) else {}
        url = _safe_marketplace_url(market, raw.get("listing_url") or raw.get("url"), identity)
        if url:
            destinations[market] = {"marketplace": market, "state": "LAST_KNOWN", "outbound_path": f"/api/public/stores/{profile.slug}/outbound/{listing.id}/{market}"}
    ebay_id = str(listing.ebay_listing_id or "").strip()
    ebay_state = str(getattr(listing.ebay_publish_status, "value", listing.ebay_publish_status) or "").upper()
    if "ebay" not in destinations and ebay_id and (listing.status == ListingStatus.PUBLISHED or ebay_state == EbayPublishStatus.POSTED.value):
        url = _safe_marketplace_url("ebay", None, ebay_id)
        if url:
            destinations["ebay"] = {"marketplace": "ebay", "state": "LAST_KNOWN", "outbound_path": f"/api/public/stores/{profile.slug}/outbound/{listing.id}/ebay"}

    source_metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    images = normalize_listing_images(
        listing_images=listing.listing_images,
        image_urls=listing.image_urls,
        source_url=source_metadata.get("source_image_url"),
        source_page_url=source_metadata.get("amazon_source_page_url"),
        source_platform=listing.source_type or "storefront",
        default_is_reference=listing.source_type in {"amazon_vine", "google_photos_album"},
        approved=True,
    )
    image_urls = []
    for image in images:
        if image.get("is_reference") or image.get("operator_state") == "rejected":
            continue
        url = _public_url(image.get("storage_path"))
        if url and url not in image_urls:
            image_urls.append(url)
    public_settings = profile.public_settings_json if isinstance(profile.public_settings_json, dict) else {}
    payload = {
        "id": listing.id,
        "slug": f"{listing.id}",
        "title": listing.title or listing.suggested_title or "Item",
        "price": float(listing.listing_price or listing.suggested_price or 0),
        "currency": "USD",
        "condition": str(listing.condition or "").strip() or None,
        "category": listing.category_suggestion or listing.category_id,
        "thumbnail_url": image_urls[0] if image_urls else None,
        "image_urls": image_urls,
        "availability": "In stock" if int(listing.quantity or 0) > 0 else "Unavailable",
        "marketplace_links": list(destinations.values()) if public_settings.get("show_marketplace_links", True) else [],
        "store_product_url": f"/store/{profile.slug}/products/{listing.id}",
    }
    if detail:
        payload.update({"description": listing.description or "", "specifications": _safe_specifics(listing.item_specifics), "sku": getattr(listing, "inventory_sku", None) if public_settings.get("show_sku") else None, "shipping_message": str(public_settings.get("shipping_message") or "Shipping details are confirmed at purchase.")})
    return payload


def _safe_specifics(value) -> dict:
    if not isinstance(value, dict):
        return {}
    blocked = ("internal", "private", "cost", "profit", "box", "slate", "source", "debug", "token", "secret", "note")
    safe = {}
    for key, val in value.items():
        if any(token in str(key).lower() for token in blocked) or isinstance(val, (dict, list)):
            continue
        if isinstance(val, (str, int, float, bool)) and str(val).strip():
            safe[str(key)[:64]] = str(val)[:300]
    return safe


def _eligible_filters(profile: StorefrontProfile, *, search: str, category: str | None, condition: str | None, brand: str | None, min_price: float | None, max_price: float | None, marketplace: str | None):
    user_id = profile.user_id
    live_projection = exists(select(1).where(
        MarketplaceListing.listing_id == Listing.id,
        MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
        MarketplaceListing.marketplace_listing_id.is_not(None),
        func.trim(MarketplaceListing.marketplace_listing_id) != "",
    ))
    ebay_identity = and_(Listing.ebay_listing_id.is_not(None), func.trim(Listing.ebay_listing_id) != "", or_(Listing.status == ListingStatus.PUBLISHED, Listing.ebay_publish_status == EbayPublishStatus.POSTED))
    price = func.coalesce(Listing.listing_price, Listing.suggested_price, 0)
    # The tenant explicitly enables the public store; its published canonical
    # catalog is eligible even when an item is sold only through the store.
    # Marketplace purchase buttons are still independently gated on exact,
    # confirmed external listing identities in _product_payload/outbound.
    public_listing_state = Listing.status.in_([ListingStatus.PUBLISHED, ListingStatus.posted])
    filters = [Listing.user_id == user_id, Listing.status != ListingStatus.archived, Listing.sold_at.is_(None), func.coalesce(Listing.quantity, 1) > 0, Listing.title.is_not(None), func.trim(Listing.title) != "", price > 0, or_(public_listing_state, live_projection, ebay_identity)]
    labels = func.coalesce(func.lower(cast(Listing.custom_labels, String)), "")
    filters.append(~labels.contains("archived_vine"))
    filters.append(~labels.contains("archived_sold"))
    filters.append(~labels.contains('"archived"'))
    source = Listing.source_metadata["recovery"]
    filters.append(func.coalesce(or_(source["merged_into_recovery_item_id"].as_string().is_not(None), source["merged_into_recovery_group_id"].as_string().is_not(None)), False).is_(False))
    if search:
        like = f"%{search.strip()}%"
        filters.append(or_(Listing.title.ilike(like), Listing.description.ilike(like), Listing.category_suggestion.ilike(like), Listing.category_id.ilike(like), cast(Listing.item_specifics, String).ilike(like), cast(Listing.tags, String).ilike(like)))
    if category:
        like = f"%{category.strip()}%"
        filters.append(or_(Listing.category_id.ilike(like), Listing.category_suggestion.ilike(like)))
    if condition:
        filters.append(Listing.condition.ilike(f"%{condition.strip()}%"))
    if brand:
        filters.append(cast(Listing.item_specifics, String).ilike(f"%{brand.strip()}%"))
    if min_price is not None:
        filters.append(price >= min_price)
    if max_price is not None:
        filters.append(price <= max_price)
    if marketplace:
        if marketplace == "ebay":
            filters.append(ebay_identity)
        else:
            filters.append(exists(select(1).where(MarketplaceListing.listing_id == Listing.id, MarketplaceListing.marketplace == marketplace, MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]), MarketplaceListing.marketplace_listing_id.is_not(None), func.trim(MarketplaceListing.marketplace_listing_id) != "")))
    return filters


def _profile_public(profile: StorefrontProfile, user: User) -> dict:
    public = profile.public_settings_json if isinstance(profile.public_settings_json, dict) else {}
    manual_entitled = commerce_entitlement(user, "payments.manual_methods")["entitled"]
    checkout_entitled = commerce_entitlement(user, "storefront.direct_checkout")["entitled"]
    payment_methods = []
    if settings.commerce_billing_enabled and manual_entitled and checkout_entitled:
        for method in ("cashapp", "venmo"):
            config = (profile.payment_settings_json or {}).get(method, {})
            if isinstance(config, dict) and config.get("enabled") and str(config.get("handle") or "").strip():
                payment_methods.append({"method": method, "label": str(config.get("label") or method.title()), "discount_percent": float(config.get("discount_percent") or 0)})
    epn_ready = all(str(value or "").strip() for value in (settings.ebay_epn_campaign_id, settings.ebay_epn_channel_id, settings.ebay_epn_rotation_id, settings.ebay_epn_tool_id, settings.ebay_epn_event_type))
    # Platform administrators may configure and test commerce while billing is
    # disabled, but the public store must never advertise live direct checkout
    # until provider execution is enabled for the deployment.
    direct_checkout_live = bool(settings.commerce_billing_enabled and payment_methods)
    return {
        "slug": profile.slug, "store_name": profile.store_name, "description": profile.description or "",
        "logo_url": _public_url(profile.logo_url), "banner_url": _public_url(profile.banner_url), "accent_color": profile.accent_color,
        "contact_email": profile.contact_email if public.get("show_contact_email") else None,
        "social_links": {str(key)[:32]: value for key, value in (public.get("social_links", {}) if isinstance(public.get("social_links"), dict) else {}).items() if isinstance(value, str) and urlparse(value).scheme == "https" and urlparse(value).hostname and urlparse(value).username is None and urlparse(value).password is None},
        "default_sort": public.get("default_sort") if public.get("default_sort") in {"newest", "price", "name", "featured"} else "newest",
        "policies": public.get("policies", {}) if isinstance(public.get("policies"), dict) else {},
        # Provider checkout and verified payment reconciliation are not enabled yet.
        "direct_checkout_available": direct_checkout_live,
        "payment_methods": payment_methods,
        "affiliate_status": {"ebay": "CONFIGURED" if epn_ready else "CONFIG_REQUIRED", "facebook": "UNSUPPORTED", "mercari": "UNSUPPORTED", "poshmark": "UNSUPPORTED", "vinted": "UNSUPPORTED", "etsy": "UNSUPPORTED", "offerup": "UNSUPPORTED"},
    }


def _commerce_settings_status() -> dict:
    epn_ready = all(str(value or "").strip() for value in (settings.ebay_epn_campaign_id, settings.ebay_epn_channel_id, settings.ebay_epn_rotation_id, settings.ebay_epn_tool_id, settings.ebay_epn_event_type))
    return {
        "affiliate_links": {market: ("CONFIGURED" if epn_ready else "CONFIG_REQUIRED") if market == "ebay" else "UNSUPPORTED" for market in _MARKET_HOSTS},
        "payments": {"stripe": "CONFIG_REQUIRED", "paypal": "CONFIG_REQUIRED", "cashapp": "MANUAL_CONFIRMATION_ONLY", "venmo": "MANUAL_CONFIRMATION_ONLY", "crypto": "MANUAL_CONFIRMATION_ONLY"},
        "direct_checkout": "NOT_IMPLEMENTED",
    }


@router.get("/storefront/settings")
def get_storefront_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    profile = db.execute(select(StorefrontProfile).where(StorefrontProfile.user_id == current_user.id)).scalar_one_or_none()
    if profile is None:
        stem = re.sub(r"[^a-z0-9]+", "-", (current_user.full_name or "my-store").lower()).strip("-") or "my-store"
        return {"configured": False, "profile": {"slug": f"{stem[:60]}-{current_user.id}", "store_name": current_user.full_name or "My Store", "enabled": False, "description": "", "logo_url": None, "banner_url": None, "contact_email": None, "accent_color": "#1d4f7a", "public_settings": {}, "payment_settings": {}}, "provider_credentials_configured": False, "entitlements": public_commerce_entitlements(current_user), **_commerce_settings_status()}
    secrets = profile.provider_secrets_enc
    return {"configured": True, "profile": {"slug": profile.slug, "store_name": profile.store_name, "enabled": profile.enabled, "description": profile.description or "", "logo_url": profile.logo_url, "banner_url": profile.banner_url, "contact_email": profile.contact_email, "accent_color": profile.accent_color, "public_settings": profile.public_settings_json or {}, "payment_settings": profile.payment_settings_json or {}}, "provider_credentials_configured": bool(secrets), "entitlements": public_commerce_entitlements(current_user), **_commerce_settings_status()}


@router.put("/storefront/settings")
def put_storefront_settings(payload: StorefrontSettingsUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    conflict = db.execute(select(StorefrontProfile.id).where(StorefrontProfile.slug == payload.slug, StorefrontProfile.user_id != current_user.id)).first()
    if conflict:
        raise HTTPException(status_code=409, detail="That store link is already in use. Choose another one.")
    profile = db.execute(select(StorefrontProfile).where(StorefrontProfile.user_id == current_user.id)).scalar_one_or_none()
    if profile is None:
        profile = StorefrontProfile(user_id=current_user.id, slug=payload.slug, store_name=payload.store_name.strip())
    profile.slug = payload.slug
    profile.store_name = payload.store_name.strip()
    profile.enabled = payload.enabled
    profile.description = payload.description.strip()
    profile.logo_url = payload.logo_url.strip() if payload.logo_url else None
    profile.banner_url = payload.banner_url.strip() if payload.banner_url else None
    profile.contact_email = payload.contact_email.strip() if payload.contact_email else None
    profile.accent_color = payload.accent_color.lower()
    profile.public_settings_json = {key: value for key, value in payload.public_settings.items() if key in {"show_contact_email", "show_sku", "show_marketplace_links", "shipping_message", "policies", "social_links", "featured_listing_ids", "default_sort"}}
    if commerce_entitlement(current_user, "affiliate.custom_ids")["entitled"]:
        affiliate_ids = payload.public_settings.get("affiliate_ids")
        if isinstance(affiliate_ids, dict):
            profile.public_settings_json["affiliate_ids"] = {
                str(market).lower(): str(value).strip()[:64]
                for market, value in affiliate_ids.items()
                if str(market).lower() in _MARKET_HOSTS and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(value).strip())
            }
    payment = payload.payment_settings if isinstance(payload.payment_settings, dict) else {}
    allowed_methods = {"stripe", "paypal", "cashapp", "venmo", "crypto"}
    normalized_payment = {}
    for method, config in payment.items():
        key = str(method).lower()
        if key not in allowed_methods or not isinstance(config, dict):
            continue
        discount = float(config.get("discount_percent") or 0)
        if not 0 <= discount <= 25:
            raise HTTPException(status_code=422, detail=f"{key} discount must be between 0 and 25 percent.")
        verification = str(config.get("verification_mode") or "MANUAL_CONFIRMATION").upper()
        if verification not in {"MANUAL_CONFIRMATION", "AUTOMATIC_PROVIDER"}:
            raise HTTPException(status_code=422, detail="Choose manual confirmation or a supported automatic provider.")
        if key in {"cashapp", "venmo"} and verification != "MANUAL_CONFIRMATION":
            raise HTTPException(status_code=422, detail=f"{key.title()} is manual-confirmation only until a verified merchant API is connected.")
        if key == "crypto" and verification != "MANUAL_CONFIRMATION":
            raise HTTPException(status_code=422, detail="Crypto is manual-confirmation only until a chain monitor is connected.")
        normalized_payment[key] = {"enabled": bool(config.get("enabled")), "label": str(config.get("label") or key.title())[:64], "handle": str(config.get("handle") or "")[:120], "instructions": str(config.get("instructions") or "")[:1000], "discount_percent": discount, "minimum_order": max(0, float(config.get("minimum_order") or 0)), "maximum_order": max(0, float(config.get("maximum_order") or 0)), "verification_mode": verification, "test_mode": bool(config.get("test_mode", True)), "currencies": [str(value).upper() for value in config.get("currencies", []) if str(value).upper() in _CRYPTO], "wallets": {str(currency).upper(): {"network": str(item.get("network") or "")[:64], "address": str(item.get("address") or "")[:240], "memo": str(item.get("memo") or "")[:120]} for currency, item in (config.get("wallets") or {}).items() if str(currency).upper() in _CRYPTO and isinstance(item, dict)}}
        if key == "cashapp" and normalized_payment[key]["handle"] and not re.fullmatch(r"\$[A-Za-z0-9._-]{1,50}", normalized_payment[key]["handle"]):
            raise HTTPException(status_code=422, detail="Enter the Cash App tag including its leading $ sign.")
        if key == "venmo" and normalized_payment[key]["handle"] and not re.fullmatch(r"@?[A-Za-z0-9_-]{1,50}", normalized_payment[key]["handle"]):
            raise HTTPException(status_code=422, detail="Enter a valid Venmo username.")
    for currency, wallet in normalized_payment.get("crypto", {}).get("wallets", {}).items():
        if normalized_payment["crypto"].get("enabled") and currency in normalized_payment["crypto"].get("currencies", []):
            if not wallet["address"] or not wallet["network"]:
                raise HTTPException(status_code=422, detail=f"Add a receive address and network for enabled {currency} payments.")
            if not _crypto_address_looks_valid(currency, wallet["address"]):
                raise HTTPException(status_code=422, detail=f"The {currency} address format does not look valid. Check the address and network; this does not verify ownership.")
    profile.payment_settings_json = normalized_payment
    if payload.provider_secrets:
        allowed_secrets = {key: value.strip() for key, value in payload.provider_secrets.items() if key in {"stripe_secret_key", "stripe_webhook_secret", "paypal_client_id", "paypal_client_secret"} and value.strip()}
        if allowed_secrets:
            profile.provider_secrets_enc = encrypt_secret(json.dumps(allowed_secrets), secret_key=settings.session_secret)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return get_storefront_settings(db, current_user)


@router.get("/public/stores/{store_slug}")
def get_public_store(store_slug: str, db: Session = Depends(get_db)):
    profile = _profile(db, store_slug)
    user = _require_public_catalog(db, profile)
    return {"store": _profile_public(profile, user), "catalog_url": f"/store/{profile.slug}"}


@router.get("/public/stores/{store_slug}/listings")
def get_public_storefront_listings(
    store_slug: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=96),
    search: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=160),
    condition: str | None = Query(default=None, max_length=64),
    brand: str | None = Query(default=None, max_length=100),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    marketplace: str | None = Query(default=None, max_length=32),
    sort_by: str | None = Query(default=None, max_length=32),
    sort_dir: str = Query(default="desc", max_length=4),
    db: Session = Depends(get_db),
):
    if page_size not in {12, 24, 48, 96}:
        raise HTTPException(status_code=422, detail="Choose a page size of 12, 24, 48, or 96.")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=422, detail="Minimum price cannot exceed maximum price.")
    if marketplace and marketplace.lower() not in _MARKET_HOSTS:
        raise HTTPException(status_code=422, detail="That marketplace filter is not supported.")
    profile = _profile(db, store_slug)
    _require_public_catalog(db, profile)
    filters = _eligible_filters(profile, search=(search or ""), category=category, condition=condition, brand=brand, min_price=min_price, max_price=max_price, marketplace=marketplace.lower() if marketplace else None)
    total = int(db.execute(select(func.count(Listing.id)).where(*filters)).scalar_one())
    store_public = profile.public_settings_json if isinstance(profile.public_settings_json, dict) else {}
    sort_key = str(sort_by or store_public.get("default_sort") or "newest").lower()
    sort_map = {"newest": Listing.created_at, "price": func.coalesce(Listing.listing_price, Listing.suggested_price, 0), "name": Listing.title}
    column = sort_map.get(sort_key, Listing.created_at)
    ordering = asc(column) if sort_dir.lower() == "asc" else desc(column)
    settings_json = profile.public_settings_json if isinstance(profile.public_settings_json, dict) else {}
    featured = [int(value) for value in settings_json.get("featured_listing_ids", []) if str(value).isdigit()]
    statement = select(Listing).options(selectinload(Listing.marketplace_listings)).where(*filters)
    if sort_key == "featured" and featured:
        statement = statement.order_by(desc(Listing.id.in_(featured)), desc(Listing.created_at))
    else:
        statement = statement.order_by(ordering, desc(Listing.id))
    rows = db.execute(statement.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return {"items": [_product_payload(row, profile) for row in rows], "total": total, "page": page, "page_size": page_size, "total_pages": max(1, (total + page_size - 1) // page_size)}


@router.get("/public/stores/{store_slug}/products/{listing_id}")
def get_public_storefront_product(store_slug: str, listing_id: int, db: Session = Depends(get_db)):
    profile = _profile(db, store_slug)
    user = _require_public_catalog(db, profile)
    filters = _eligible_filters(profile, search="", category=None, condition=None, brand=None, min_price=None, max_price=None, marketplace=None)
    listing = db.execute(select(Listing).options(selectinload(Listing.marketplace_listings)).where(Listing.id == listing_id, *filters)).scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"store": _profile_public(profile, user), "product": _product_payload(listing, profile, detail=True)}


def _checkout_payload(order: StorefrontOrder, *, buyer_token: bool = False) -> dict:
    payload = {
        "order_number": order.order_number,
        "status": order.status,
        "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "subtotal": order.subtotal,
        "discount_amount": order.discount_amount,
        "total": order.total,
        "currency": order.currency,
        "reservation_expires_at": order.reservation_expires_at.isoformat() if order.reservation_expires_at else None,
    }
    if buyer_token:
        payload["checkout_token"] = order.checkout_token
        payload["payment_instructions"] = (order.payment_config_snapshot or {}).get("instructions", "")
        payload["payment_label"] = (order.payment_config_snapshot or {}).get("label", order.payment_method.title())
        payload["payment_handle"] = (order.payment_config_snapshot or {}).get("handle", "")
    return payload


@router.post("/public/stores/{store_slug}/checkout")
def create_storefront_checkout(store_slug: str, payload: StoreCheckoutRequest, db: Session = Depends(get_db)):
    profile = _profile(db, store_slug)
    user = _require_public_catalog(db, profile)
    existing = db.execute(select(StorefrontOrder).where(StorefrontOrder.user_id == profile.user_id, StorefrontOrder.idempotency_key == payload.idempotency_key)).scalar_one_or_none()
    if existing:
        return _checkout_payload(existing, buyer_token=True)
    if not commerce_entitlement(user, "storefront.direct_checkout")["entitled"] or not commerce_entitlement(user, "payments.manual_methods")["entitled"]:
        raise HTTPException(status_code=409, detail="Direct checkout is not enabled for this store.")
    config = (profile.payment_settings_json or {}).get(payload.payment_method)
    if not isinstance(config, dict) or not config.get("enabled") or not str(config.get("handle") or "").strip():
        raise HTTPException(status_code=409, detail="That payment method is not available for this store.")
    if "@" not in payload.customer_email or payload.customer_email.startswith("@") or payload.customer_email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    required_address_fields = ("street", "city", "region", "postal_code", "country")
    if any(not str(payload.shipping_address.get(key) or "").strip() for key in required_address_fields):
        raise HTTPException(status_code=422, detail="Enter a complete shipping address.")
    filters = _eligible_filters(profile, search="", category=None, condition=None, brand=None, min_price=None, max_price=None, marketplace=None)
    listing = db.execute(select(Listing).where(Listing.id == payload.listing_id, *filters).with_for_update()).scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=404, detail="This item is no longer available.")
    now = datetime.utcnow()
    reserved = int(db.execute(
        select(func.coalesce(func.sum(StorefrontOrderItem.quantity), 0))
        .join(StorefrontOrder, StorefrontOrder.id == StorefrontOrderItem.order_id)
        .where(StorefrontOrder.user_id == profile.user_id, StorefrontOrderItem.listing_id == listing.id, StorefrontOrder.payment_status.in_(["AWAITING_PAYMENT", "PAYMENT_PENDING_VERIFICATION"]), StorefrontOrder.reservation_expires_at > now)
    ).scalar_one() or 0)
    if max(0, int(listing.quantity or 0)) - reserved < 1:
        raise HTTPException(status_code=409, detail="This item is currently reserved or sold.")
    subtotal = round(float(listing.listing_price or listing.suggested_price or 0), 2)
    if subtotal <= 0:
        raise HTTPException(status_code=409, detail="This item has no available direct-sale price.")
    if subtotal < float(config.get("minimum_order") or 0) or (float(config.get("maximum_order") or 0) and subtotal > float(config["maximum_order"])):
        raise HTTPException(status_code=409, detail="This order is outside the seller's payment limits.")
    discount_percent = float(config.get("discount_percent") or 0)
    discount_amount = round(subtotal * discount_percent / 100, 2)
    total = round(subtotal - discount_amount, 2)
    safe_address = {key: str(payload.shipping_address.get(key) or "").strip()[:240] for key in ("street", "street2", "city", "region", "postal_code", "country") if payload.shipping_address.get(key)}
    token = secrets.token_urlsafe(32)
    order = StorefrontOrder(
        order_number=f"PP-{secrets.token_hex(6).upper()}", user_id=profile.user_id, store_id=profile.id,
        idempotency_key=payload.idempotency_key, checkout_token=token, status="AWAITING_PAYMENT", payment_status="AWAITING_PAYMENT",
        fulfillment_status="UNFULFILLED", payment_method=payload.payment_method, provider="manual",
        customer_name=payload.customer_name.strip(), customer_email=payload.customer_email.strip()[:255], shipping_address_json=safe_address,
        currency="USD", subtotal=subtotal, discount_percent=discount_percent, discount_amount=discount_amount, total=total,
        payment_config_snapshot={"label": str(config.get("label") or payload.payment_method.title())[:64], "handle": str(config.get("handle") or "")[:120], "instructions": str(config.get("instructions") or "")[:1000], "verification_mode": "MANUAL_CONFIRMATION"},
        reservation_expires_at=now + timedelta(minutes=20),
    )
    db.add(order)
    db.flush()
    db.add(StorefrontOrderItem(order_id=order.id, listing_id=listing.id, title_snapshot=listing.title or "Item", sku_snapshot=getattr(listing, "inventory_sku", None), quantity=1, unit_price=subtotal, item_snapshot={"condition": listing.condition, "category": listing.category_suggestion or listing.category_id}))
    db.add(StorefrontPaymentAttempt(user_id=profile.user_id, order_id=order.id, provider="manual", status="AWAITING_PAYMENT", amount=total, currency="USD", safe_result_json={"verification_mode": "MANUAL_CONFIRMATION"}))
    db.commit()
    db.refresh(order)
    return _checkout_payload(order, buyer_token=True)


@router.post("/public/stores/{store_slug}/orders/{order_number}/payment-sent")
def storefront_buyer_reported_payment(store_slug: str, order_number: str, token: str = Query(min_length=20, max_length=100), db: Session = Depends(get_db)):
    profile = _profile(db, store_slug)
    order = db.execute(select(StorefrontOrder).where(StorefrontOrder.store_id == profile.id, StorefrontOrder.order_number == order_number).with_for_update()).scalar_one_or_none()
    if order is None or not hmac.compare_digest(order.checkout_token, token):
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_status == "PAID":
        return _checkout_payload(order)
    if order.payment_status not in {"AWAITING_PAYMENT", "PAYMENT_PENDING_VERIFICATION"} or not order.reservation_expires_at or order.reservation_expires_at <= datetime.utcnow():
        raise HTTPException(status_code=409, detail="This checkout has expired. Contact the seller before sending payment.")
    order.status = "PAYMENT_PENDING_VERIFICATION"
    order.payment_status = "PAYMENT_PENDING_VERIFICATION"
    attempt = db.execute(select(StorefrontPaymentAttempt).where(StorefrontPaymentAttempt.order_id == order.id).order_by(StorefrontPaymentAttempt.id.desc())).scalar_one_or_none()
    if attempt:
        attempt.status = "PAYMENT_PENDING_VERIFICATION"
        db.add(attempt)
    db.add(order)
    db.commit()
    return _checkout_payload(order)


@router.get("/storefront/orders")
def list_storefront_orders(db: Session = Depends(get_db), current_user: User = Depends(get_current_user), status: str | None = Query(default=None, max_length=40)):
    statement = select(StorefrontOrder).where(StorefrontOrder.user_id == current_user.id)
    if status:
        statement = statement.where(StorefrontOrder.payment_status == status.upper())
    orders = db.execute(statement.order_by(StorefrontOrder.created_at.desc()).limit(100)).scalars().all()
    output = []
    items_by_order: dict[int, list[StorefrontOrderItem]] = {}
    order_ids = [order.id for order in orders]
    if order_ids:
        for item in db.execute(select(StorefrontOrderItem).where(StorefrontOrderItem.order_id.in_(order_ids))).scalars().all():
            items_by_order.setdefault(item.order_id, []).append(item)
    for order in orders:
        items = items_by_order.get(order.id, [])
        output.append({"id": order.id, **_checkout_payload(order), "customer_name": order.customer_name, "customer_email": order.customer_email, "shipping_address": order.shipping_address_json or {}, "fulfillment_status": order.fulfillment_status, "carrier": order.carrier, "tracking_number": order.tracking_number, "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None, "confirmed_by": order.confirmed_by, "paid_at": order.paid_at.isoformat() if order.paid_at else None, "sale_id": order.sale_id, "internal_note": order.internal_note, "items": [{"listing_id": item.listing_id, "title": item.title_snapshot, "quantity": item.quantity, "unit_price": item.unit_price} for item in items]})
    return {"items": output, "total_returned": len(orders)}


@router.post("/storefront/orders/{order_id}/reject-payment")
def reject_storefront_manual_payment(order_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    order = db.execute(select(StorefrontOrder).where(StorefrontOrder.id == order_id, StorefrontOrder.user_id == current_user.id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_status == "PAID":
        raise HTTPException(status_code=409, detail="A paid order cannot be rejected here.")
    order.status = "PAYMENT_FAILED"
    order.payment_status = "PAYMENT_FAILED"
    order.reservation_expires_at = datetime.utcnow()
    db.add(order)
    attempt = db.execute(select(StorefrontPaymentAttempt).where(StorefrontPaymentAttempt.order_id == order.id).order_by(StorefrontPaymentAttempt.id.desc())).scalar_one_or_none()
    if attempt:
        attempt.status = "REJECTED"
        db.add(attempt)
    db.commit()
    return _checkout_payload(order)


@router.post("/storefront/orders/{order_id}/shipment")
def update_storefront_shipment(order_id: int, payload: StoreOrderShipmentUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    order = db.execute(select(StorefrontOrder).where(StorefrontOrder.id == order_id, StorefrontOrder.user_id == current_user.id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_status != "PAID":
        raise HTTPException(status_code=409, detail="Only paid orders can be shipped.")
    order.carrier = payload.carrier.strip()
    order.tracking_number = payload.tracking_number.strip()
    order.shipped_at = order.shipped_at or datetime.utcnow()
    order.fulfillment_status = "SHIPPED"
    db.add(order)
    db.commit()
    return _checkout_payload(order)


@router.post("/storefront/orders/{order_id}/confirm-payment")
def confirm_storefront_manual_payment(order_id: int, payload: ManualPaymentConfirmation | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    order = db.execute(select(StorefrontOrder).where(StorefrontOrder.id == order_id, StorefrontOrder.user_id == current_user.id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_status == "PAID" and order.sale_id:
        return {"order": _checkout_payload(order), "sale_id": order.sale_id, "idempotent": True}
    if order.payment_status != "PAYMENT_PENDING_VERIFICATION":
        raise HTTPException(status_code=409, detail="The buyer has not reported payment. Review the payment externally first.")
    line = db.execute(select(StorefrontOrderItem).where(StorefrontOrderItem.order_id == order.id).limit(1)).scalar_one_or_none()
    listing = db.execute(select(Listing).where(Listing.id == line.listing_id, Listing.user_id == current_user.id).with_for_update()).scalar_one_or_none() if line and line.listing_id else None
    if listing is None or listing.sold_at is not None or int(listing.quantity or 0) < line.quantity:
        order.status = "MANUAL_REVIEW"
        order.payment_status = "MANUAL_REVIEW"
        db.add(order)
        db.commit()
        raise HTTPException(status_code=409, detail="Inventory changed before payment confirmation. Order moved to manual review; do not confirm payment until resolved.")
    sale = Sale(user_id=current_user.id, listing_id=listing.id, platform=MarketplaceName.storefront_direct, marketplace_order_id=order.order_number, marketplace_listing_id=f"DIRECT-{listing.id}-{order.order_number}", quantity=line.quantity, amount=order.total, currency=order.currency, sold_at=datetime.utcnow(), status="SYNCING", details={"source": "STOREFRONT_DIRECT", "order_number": order.order_number, "discount_amount": order.discount_amount, "payment_method": order.payment_method})
    db.add(sale)
    db.flush()
    try:
        coroutine = SaleDetectionService()._fanout_quantity_adjustment(db, listing, current_user, sold_platform=MarketplaceName.storefront_direct.value, quantity_sold=line.quantity, dry_run=False, sale_amount=order.total)
        # FastAPI invokes sync endpoints in a worker normally; route tests may
        # call them inline from an event loop. Isolate the legacy async wrapper
        # so asyncio.run remains safe in both cases.
        with ThreadPoolExecutor(max_workers=1) as executor:
            fanout = executor.submit(asyncio.run, coroutine).result()
    except Exception as exc:
        db.rollback()
        order = db.execute(select(StorefrontOrder).where(StorefrontOrder.id == order_id, StorefrontOrder.user_id == current_user.id)).scalar_one()
        order.status = "PAYMENT_PENDING_VERIFICATION"
        order.payment_status = "PAYMENT_PENDING_VERIFICATION"
        order.internal_note = f"Payment was confirmed externally but inventory reconciliation failed: {type(exc).__name__}"
        db.add(order)
        db.commit()
        raise HTTPException(status_code=503, detail="Payment confirmation was not recorded; the order remains pending verification because inventory reconciliation needs attention.") from exc
    sale.status = "SYNCED"
    sale.details = {**(sale.details or {}), "fanout": fanout}
    order.status = "PAID"
    order.payment_status = "PAID"
    order.fulfillment_status = "READY_TO_SHIP"
    order.confirmed_by = current_user.id
    order.paid_at = datetime.utcnow()
    order.sale_id = sale.id
    order.confirmation_reference = payload.reference.strip()[:255] if payload and payload.reference else None
    order.internal_note = payload.internal_note.strip()[:1000] if payload and payload.internal_note else order.internal_note
    attempt = db.execute(select(StorefrontPaymentAttempt).where(StorefrontPaymentAttempt.order_id == order.id).order_by(StorefrontPaymentAttempt.id.desc())).scalar_one_or_none()
    if attempt:
        attempt.status = "VERIFIED_MANUAL"
        attempt.verified_at = order.paid_at
        attempt.safe_result_json = {**(attempt.safe_result_json or {}), "verified_by": current_user.id, "verification_mode": "MANUAL_CONFIRMATION"}
        db.add(attempt)
    db.add_all([sale, order])
    create_process_notification(db, user_id=current_user.id, title="DIRECT STORE ORDER PAID", message=f"{listing.title or 'Item'} is ready to ship. Order {order.order_number}.", notification_type="storefront_order_paid", href=f"/sales?order={order.order_number}", metadata_json={"order_id": order.id, "sale_id": sale.id})
    for market, result in fanout.items():
        response = result.get("response", {}) if isinstance(result, dict) else {}
        if response.get("status") in {"NOT_ENQUEUED", "UNSUPPORTED_ACTION"}:
            create_process_notification(db, user_id=current_user.id, title=f"SOLD ITEM STILL LIVE ON {market.upper()}", message=f"{listing.title or 'Item'} sold from the storefront, but its {market} listing could not be removed: {response.get('error_code') or response.get('status')}.", notification_type="sold_item_still_live", href=f"/listings/{listing.id}", metadata_json={"listing_id": listing.id, "marketplace": market, "external_listing_id": response.get("external_listing_id"), "reason": response.get("error_code") or response.get("status")})
    db.commit()
    return {"order": _checkout_payload(order), "sale_id": sale.id, "fanout": fanout, "idempotent": False}


@router.get("/public/stores/{store_slug}/outbound/{listing_id}/{marketplace}")
def storefront_outbound(store_slug: str, listing_id: int, marketplace: str, request: Request, db: Session = Depends(get_db)):
    profile = _profile(db, store_slug)
    store_user = _require_public_catalog(db, profile)
    if not commerce_entitlement(store_user, "storefront.external_purchase_links")["entitled"]:
        raise HTTPException(status_code=404, detail="Purchase option not found")
    market = marketplace.lower()
    filters = _eligible_filters(profile, search="", category=None, condition=None, brand=None, min_price=None, max_price=None, marketplace=market)
    listing = db.execute(select(Listing).options(selectinload(Listing.marketplace_listings)).where(Listing.id == listing_id, *filters)).scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=404, detail="Purchase option is no longer available")
    product = _product_payload(listing, profile)
    if not any(item["marketplace"] == market for item in product["marketplace_links"]):
        raise HTTPException(status_code=404, detail="No confirmed marketplace link is available")
    attribution = "none"
    url = None
    for row in listing.marketplace_listings or []:
        row_market = str(getattr(row.marketplace, "value", row.marketplace) or "").lower()
        row_status = str(getattr(row.status, "value", row.status) or "").upper()
        if row_market == market and row_status in {"PUBLISHED", "UPDATED"} and str(row.marketplace_listing_id or "").strip():
            raw = row.raw_response if isinstance(row.raw_response, dict) else {}
            url = _safe_marketplace_url(market, raw.get("listing_url") or raw.get("url"), row.marketplace_listing_id)
            if url:
                break
    if not url and market == "ebay" and listing.ebay_listing_id and (listing.status == ListingStatus.PUBLISHED or str(getattr(listing.ebay_publish_status, "value", listing.ebay_publish_status)) == EbayPublishStatus.POSTED.value):
        url = _safe_marketplace_url("ebay", None, listing.ebay_listing_id)
    if not url:
        raise HTTPException(status_code=404, detail="Marketplace URL could not be verified safely")
    destination_profile = profile.public_settings_json if isinstance(profile.public_settings_json, dict) else {}
    tenant_ids = destination_profile.get("affiliate_ids") if isinstance(destination_profile.get("affiliate_ids"), dict) else {}
    custom_id = str(tenant_ids.get(market) or "").strip()
    if market == "ebay":
        epn = _epn_url(url, custom_id=custom_id if custom_id and commerce_entitlement(db.get(User, profile.user_id), "affiliate.custom_ids")["entitled"] else None)
        if epn:
            url = epn
            attribution = "tenant" if custom_id and commerce_entitlement(db.get(User, profile.user_id), "affiliate.custom_ids")["entitled"] else "platform"
    referrer = urlparse(request.headers.get("referer") or "").hostname
    db.add(StorefrontAffiliateClick(user_id=profile.user_id, store_id=profile.id, listing_id=listing.id, marketplace=market, attribution_mode=attribution, referrer_host=referrer[:255] if referrer else None))
    db.commit()
    return RedirectResponse(url=url, status_code=302)


@router.get("/storefront/analytics/outbound")
def storefront_outbound_analytics(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.execute(
        select(StorefrontAffiliateClick.marketplace, StorefrontAffiliateClick.attribution_mode, func.date(StorefrontAffiliateClick.clicked_at), func.count())
        .where(StorefrontAffiliateClick.user_id == current_user.id)
        .group_by(StorefrontAffiliateClick.marketplace, StorefrontAffiliateClick.attribution_mode, func.date(StorefrontAffiliateClick.clicked_at))
        .order_by(func.date(StorefrontAffiliateClick.clicked_at).desc())
        .limit(180)
    ).all()
    return {"clicks": [{"marketplace": row[0], "attribution": row[1], "day": str(row[2]), "count": int(row[3])} for row in rows], "conversions": "NOT_REPORTED_BY_MARKETPLACE"}
