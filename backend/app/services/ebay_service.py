from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import EbayPublishStatus, MarketplaceName
from app.models.models import Listing, MarketplaceAccount
from app.services.rate_limiter import rate_limiter



class EbayIntegrationError(RuntimeError):
    """Raised for eBay API integration errors."""


@dataclass(slots=True)
class EbayTokenBundle:
    access_token: str
    refresh_token: str | None
    expires_in: int


class EbayAPIClient:
    """Thin async wrapper around eBay APIs with retries and standardized errors."""

    def __init__(self, access_token: str, *, sandbox: bool = True, timeout_seconds: int = 30):
        self.access_token = access_token
        self.base_url = "https://api.sandbox.ebay.com" if sandbox else "https://api.ebay.com"
        self.timeout = httpx.Timeout(timeout_seconds)

    async def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        request_headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        }

        backoff_seconds = 0.5
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, retries + 1):
                await rate_limiter.acquire_async("ebay")
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=payload,
                    headers=request_headers,
                )
                if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds *= 2
                    continue

                if response.status_code >= 400:
                    detail = _safe_json(response)
                    raise EbayIntegrationError(
                        f"eBay API request failed ({response.status_code}) for {path}: {json.dumps(detail)}"
                    )
                return _safe_json(response)

        raise EbayIntegrationError(f"eBay API retry exhaustion for {path}")


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}
    except Exception:
        return {"raw": response.text}


def _oauth_base() -> str:
    return "https://auth.sandbox.ebay.com/oauth2/authorize" if settings.environment != "production" else "https://auth.ebay.com/oauth2/authorize"


def _token_endpoint() -> str:
    return "https://api.sandbox.ebay.com/identity/v1/oauth2/token" if settings.environment != "production" else "https://api.ebay.com/identity/v1/oauth2/token"


def _scopes() -> str:
    return " ".join(
        [
            "https://api.ebay.com/oauth/api_scope",
            "https://api.ebay.com/oauth/api_scope/sell.inventory",
            "https://api.ebay.com/oauth/api_scope/sell.account",
            "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
        ]
    )


def build_ebay_auth_url(user_id: int, redirect_uri: str) -> str:
    state = _make_oauth_state(user_id)
    query = urlencode(
        {
            "client_id": settings.ebay_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _scopes(),
            "state": state,
        }
    )
    return f"{_oauth_base()}?{query}"


def _make_oauth_state(user_id: int) -> str:
    random_nonce = secrets.token_urlsafe(16)
    payload = f"{user_id}:{random_nonce}".encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("utf-8")


def parse_oauth_state(state: str) -> int:
    decoded = base64.urlsafe_b64decode(state.encode("utf-8")).decode("utf-8")
    user_id_text, _ = decoded.split(":", maxsplit=1)
    return int(user_id_text)


async def authenticate_user_ebay(user_id: int, redirect_uri: str) -> str:
    """Return user-specific OAuth URL; callback handling stores token in DB."""
    if not settings.ebay_client_id or not settings.ebay_client_secret:
        raise EbayIntegrationError("Missing eBay OAuth settings (ebay_client_id / ebay_client_secret)")
    if not redirect_uri:
        raise EbayIntegrationError("redirect_uri is required")
    return build_ebay_auth_url(user_id, redirect_uri)


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> EbayTokenBundle:
    credentials = f"{settings.ebay_client_id}:{settings.ebay_client_secret}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("utf-8")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        response = await client.post(_token_endpoint(), data=data, headers=headers)

    if response.status_code >= 400:
        raise EbayIntegrationError(f"OAuth token exchange failed: {response.text}")

    token_data = response.json()
    return EbayTokenBundle(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_in=int(token_data.get("expires_in", 7200)),
    )


async def refresh_ebay_token(user_id: int, db: Session) -> MarketplaceAccount:
    account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == user_id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    if not account or not account.refresh_token:
        raise EbayIntegrationError("No eBay account with refresh token found")

    credentials = f"{settings.ebay_client_id}:{settings.ebay_client_secret}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("utf-8")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": account.refresh_token,
        "scope": _scopes(),
    }
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        response = await client.post(_token_endpoint(), data=data, headers=headers)

    if response.status_code >= 400:
        raise EbayIntegrationError(f"Token refresh failed: {response.text}")

    payload = response.json()
    account.access_token = payload["access_token"]
    account.token_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=int(payload.get("expires_in", 7200)))
    if payload.get("refresh_token"):
        account.refresh_token = payload["refresh_token"]
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


async def get_or_refresh_account(user_id: int, db: Session) -> MarketplaceAccount:
    account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == user_id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    if not account:
        raise EbayIntegrationError("No connected eBay account for user")
    if account.token_expires_at and account.token_expires_at <= datetime.utcnow() + timedelta(minutes=5):
        return await refresh_ebay_token(user_id, db)
    return account


async def create_inventory_location(user_id: int, db: Session) -> dict[str, Any]:
    account = await get_or_refresh_account(user_id, db)
    client = EbayAPIClient(account.access_token)
    location_key = f"posterpro-{user_id}"
    payload = {
        "name": "PosterPro Default Location",
        "location": {
            "address": {
                "addressLine1": "123 Marketplace St",
                "city": "San Jose",
                "stateOrProvince": "CA",
                "postalCode": "95125",
                "country": "US",
            }
        },
        "merchantLocationStatus": "ENABLED",
        "locationTypes": ["WAREHOUSE"],
    }
    await client.request("POST", f"/sell/inventory/v1/location/{location_key}", payload=payload)
    return {"merchantLocationKey": location_key}


async def create_or_replace_item(listing: Listing, account: MarketplaceAccount) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    sku = f"posterpro-{listing.user_id}-{listing.id}"
    # Example payload for createOrReplaceInventoryItem (official field names)
    payload = {
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "condition": "USED_GOOD",
        "product": {
            "title": listing.title or f"PosterPro Listing #{listing.id}",
            "description": listing.description or "No description provided",
            "aspects": {"Brand": ["Unbranded"]},
            "imageUrls": [],
        },
    }
    response = await client.request("PUT", f"/sell/inventory/v1/inventory_item/{sku}", payload=payload)
    return {"sku": sku, "response": response}


async def create_offer_for_item(listing: Listing, account: MarketplaceAccount, sku: str) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    price = listing.suggested_price or 19.99
    policies = await get_business_policy_ids(account.access_token)

    # Example payload for createOffer (official field names)
    payload = {
        "sku": sku,
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "availableQuantity": 1,
        "categoryId": listing.category_suggestion or "171485",
        "listingPolicies": {
            "paymentPolicyId": policies.get("paymentPolicyId"),
            "returnPolicyId": policies.get("returnPolicyId"),
            "fulfillmentPolicyId": policies.get("fulfillmentPolicyId"),
        },
        "merchantLocationKey": f"posterpro-{listing.user_id}",
        "pricingSummary": {"price": {"value": str(price), "currency": "USD"}},
    }
    response = await client.request("POST", "/sell/inventory/v1/offer", payload=payload)
    return {"offerId": response.get("offerId"), "response": response}


async def publish_offer(listing: Listing, account: MarketplaceAccount, offer_id: str) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    # Example payload for publishOffer (official field names)
    response = await client.request("POST", f"/sell/inventory/v1/offer/{offer_id}/publish")
    listing_id = response.get("listingId")
    if not listing_id:
        raise EbayIntegrationError("publishOffer did not return listingId")
    return {"listingId": listing_id, "response": response}


async def get_category_tree(access_token: str, marketplace_id: str = "EBAY_US") -> dict[str, Any]:
    client = EbayAPIClient(access_token)
    trees = await client.request("GET", "/commerce/taxonomy/v1/get_default_category_tree_id", params={"marketplace_id": marketplace_id})
    tree_id = trees.get("categoryTreeId")
    if not tree_id:
        return trees
    return await client.request("GET", f"/commerce/taxonomy/v1/category_tree/{tree_id}")


async def get_business_policy_ids(access_token: str, marketplace_id: str = "EBAY_US") -> dict[str, Any]:
    client = EbayAPIClient(access_token)
    headers = {"Content-Language": "en-US"}
    base = f"/sell/account/v1"
    payment = await client.request("GET", f"{base}/payment_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    shipping = await client.request("GET", f"{base}/fulfillment_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    returns = await client.request("GET", f"{base}/return_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    return {
        "paymentPolicyId": (payment.get("paymentPolicies") or [{}])[0].get("paymentPolicyId"),
        "fulfillmentPolicyId": (shipping.get("fulfillmentPolicies") or [{}])[0].get("fulfillmentPolicyId"),
        "returnPolicyId": (returns.get("returnPolicies") or [{}])[0].get("returnPolicyId"),
    }


async def get_required_item_specifics(access_token: str, category_id: str, marketplace_id: str = "EBAY_US") -> dict[str, Any]:
    client = EbayAPIClient(access_token)
    tree = await client.request("GET", "/commerce/taxonomy/v1/get_default_category_tree_id", params={"marketplace_id": marketplace_id})
    tree_id = tree.get("categoryTreeId")
    if not tree_id:
        raise EbayIntegrationError("Unable to resolve category tree id")
    return await client.request(
        "GET",
        f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category",
        params={"category_id": category_id},
    )


async def get_incoming_best_offers(
    account: MarketplaceAccount,
    *,
    limit: int = 100,
    marketplace_id: str = "EBAY_US",
) -> list[dict[str, Any]]:
    """
    Pull incoming buyer offers from eBay's negotiation API.
    """
    client = EbayAPIClient(account.access_token)
    response = await client.request(
        "GET",
        "/sell/negotiation/v1/find_offers",
        params={
            "limit": limit,
            "marketplace_id": marketplace_id,
            "offer_type": "COUNTER_OFFER",
        },
    )
    offers = response.get("offers") or response.get("bestOffers") or []
    return [offer for offer in offers if isinstance(offer, dict)]


async def get_fulfillment_orders(
    account: MarketplaceAccount,
    *,
    limit: int = 50,
    filter_expression: str | None = None,
) -> list[dict[str, Any]]:
    """Pull paid/completed orders from eBay Fulfillment API."""
    client = EbayAPIClient(account.access_token)
    params: dict[str, Any] = {"limit": limit}
    if filter_expression:
        params["filter"] = filter_expression
    response = await client.request("GET", "/sell/fulfillment/v1/order", params=params)
    orders = response.get("orders") or []
    return [order for order in orders if isinstance(order, dict)]


async def accept_best_offer(account: MarketplaceAccount, offer_id: str) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    return await client.request("POST", f"/sell/negotiation/v1/offer/{offer_id}/accept")


async def reject_best_offer(account: MarketplaceAccount, offer_id: str, reason: str | None = None) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    payload = {"declineReason": reason} if reason else None
    return await client.request("POST", f"/sell/negotiation/v1/offer/{offer_id}/decline", payload=payload)


async def publish_listing_to_ebay(listing: Listing, db: Session, *, relist: bool = False) -> dict[str, Any]:
    listing.ebay_publish_status = EbayPublishStatus.POSTING
    db.add(listing)
    db.commit()

    try:
        account = await get_or_refresh_account(listing.user_id, db)
        location_data = await create_inventory_location(listing.user_id, db)
        item_data = await create_or_replace_item(listing, account)
        offer_data = await create_offer_for_item(listing, account, item_data["sku"])
        publish_data = await publish_offer(listing, account, offer_data["offerId"])

        previous_listing_id = listing.ebay_listing_id
        listing.ebay_listing_id = publish_data["listingId"]
        listing.ebay_publish_status = EbayPublishStatus.POSTED
        previous_data = listing.marketplace_data or {}
        history = list(previous_data.get("auto_relist_history") or [])
        if relist:
            history.append(
                {
                    "action": "AUTO_RELISTED",
                    "previous_listing_id": previous_listing_id,
                    "new_listing_id": publish_data["listingId"],
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        listing.marketplace_data = {
            **previous_data,
            "location": location_data,
            "item": item_data,
            "offer": offer_data,
            "publish": publish_data,
            "ebay_url": f"https://www.ebay.com/itm/{publish_data['listingId']}",
            "last_publish_action": "relist" if relist else "publish",
            "auto_relist_history": history,
        }
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return {
            "listing_id": publish_data["listingId"],
            "status": listing.ebay_publish_status,
            "ebay_url": listing.marketplace_data["ebay_url"],
        }
    except Exception as exc:
        listing.ebay_publish_status = EbayPublishStatus.FAILED
        listing.marketplace_data = {"error": str(exc)}
        db.add(listing)
        db.commit()
        raise
