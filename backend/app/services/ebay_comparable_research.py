"""Read-only eBay Browse active-asking research adapter.

Browse results are active asking prices, never sold-price evidence. The
Marketplace Insights sales-history API is a separate restricted capability and
must not be inferred from Browse results.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import reload_settings
from app.services.ebay_service import EbayIntegrationError
from app.services.rate_limiter import rate_limiter


def _identity_query(listing: Any) -> tuple[str | None, str | None]:
    specifics = listing.item_specifics if isinstance(getattr(listing, "item_specifics", None), dict) else {}
    source = listing.source_metadata if isinstance(getattr(listing, "source_metadata", None), dict) else {}
    brand = str(specifics.get("Brand") or source.get("brand") or "").strip()
    mpn = str(specifics.get("MPN") or specifics.get("Model") or source.get("mpn") or source.get("model") or "").strip()
    upc = str(specifics.get("UPC") or specifics.get("EAN") or source.get("upc") or source.get("ean") or "").strip()
    if upc:
        return None, upc
    parts = [brand, mpn] if mpn else []
    if not parts:
        title = " ".join(str(listing.title or "").split())
        # Search usable titles only; internal intake labels/OCR fragments are
        # not converted into confident product-market research queries.
        if len(title) >= 10 and not any(token in title.casefold() for token in (
            "head slate", "unknown item", "generic item", "untitled capture",
            "product pending", "auto-generated item group",
        )):
            parts = [title]
    query = " ".join(part for part in parts if part).strip()
    return (query[:120] or None), None


async def search_ebay_active_comparables(listing: Any, *, limit: int = 50) -> dict[str, Any]:
    """Return actual eBay Browse asking listings and query provenance."""
    query, gtin = _identity_query(listing)
    if not query and not gtin:
        return {
            "status": "insufficient_identity",
            "source": "ebay_browse_active",
            "researched_at": datetime.now(UTC).isoformat(),
            "query": None,
            "comparables": [],
            "message": "A product title, model/part number, or UPC/EAN is required for comparable search.",
        }
    runtime = reload_settings()
    if not runtime.ebay_client_id or not runtime.ebay_client_secret:
        raise EbayIntegrationError("eBay application credentials are not configured for read-only comparable research")

    api_root = "https://api.ebay.com" if runtime.environment == "production" else "https://api.sandbox.ebay.com"
    credentials = f"{runtime.ebay_client_id}:{runtime.ebay_client_secret}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("ascii")
    async with httpx.AsyncClient(timeout=httpx.Timeout(25)) as client:
        await rate_limiter.acquire_async("ebay")
        token_response = await client.post(
            f"{api_root}/identity/v1/oauth2/token",
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
            headers={"Authorization": f"Basic {basic_auth}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_response.status_code >= 400:
            raise EbayIntegrationError(f"eBay application-token request failed ({token_response.status_code}); active comparable search was not run")
        token_payload = token_response.json()
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise EbayIntegrationError("eBay application-token response did not include an access token")

        params: dict[str, Any] = {"limit": max(1, min(int(limit), 50)), "fieldgroups": "EXTENDED"}
        if gtin:
            params["gtin"] = gtin
        else:
            params["q"] = query
        await rate_limiter.acquire_async("ebay")
        response = await client.get(
            f"{api_root}/buy/browse/v1/item_summary/search",
            params=params,
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                "Accept": "application/json",
            },
        )
    if response.status_code >= 400:
        raise EbayIntegrationError(f"eBay Browse active comparable search failed ({response.status_code})")
    payload = response.json()
    researched_at = datetime.now(UTC).isoformat()
    comparables: list[dict[str, Any]] = []
    for item in payload.get("itemSummaries") or []:
        if not isinstance(item, dict):
            continue
        price = item.get("price") if isinstance(item.get("price"), dict) else {}
        shipping_options = item.get("shippingOptions") if isinstance(item.get("shippingOptions"), list) else []
        shipping_cost = None
        for option in shipping_options:
            cost = option.get("shippingCost") if isinstance(option, dict) and isinstance(option.get("shippingCost"), dict) else None
            if cost and cost.get("value") not in (None, ""):
                try:
                    shipping_cost = round(float(cost["value"]), 2)
                except (TypeError, ValueError):
                    shipping_cost = None
                break
        if price.get("value") in (None, ""):
            continue
        try:
            asking_price = round(float(price["value"]), 2)
        except (TypeError, ValueError):
            continue
        if asking_price <= 0:
            continue
        comparables.append({
            "source_marketplace": "ebay",
            "comp_type": "active",
            "source_label": "eBay active asking listing (not sold evidence)",
            "source_record_id": str(item.get("itemId") or "").strip() or None,
            "title": str(item.get("title") or "").strip(),
            "price": asking_price,
            "shipping_price": shipping_cost,
            "currency": str(price.get("currency") or "USD").upper(),
            "condition": str(item.get("condition") or "").strip() or None,
            "sold_status": "asking_active",
            "research_date": researched_at,
            "match_rationale": f"Matched by {'GTIN' if gtin else 'query'}: {gtin or query}",
            "source_url": str(item.get("itemWebUrl") or "").strip() or None,
            "category": str(item.get("categoryPath") or "").strip() or None,
            "matched_identifiers": {"upc": gtin} if gtin else {},
            "shipping_inclusive": shipping_cost is not None,
        })
    return {
        "status": "results" if comparables else "no_results",
        "source": "ebay_browse_active",
        "researched_at": researched_at,
        "query": gtin or query,
        "result_count": len(comparables),
        "active_asking_count": len(comparables),
        "sold_comparable_count": 0,
        "message": "Browse provides current asking prices only; no sold comparables were returned by this source.",
        "comparables": comparables,
    }
