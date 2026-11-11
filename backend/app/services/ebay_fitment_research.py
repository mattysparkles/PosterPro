"""Read-only, exact-identity fitment research using eBay's official catalog.

Catalog keyword results are candidates, not assertions. Compatibility is
returned only after one product matches both the operator's exact brand and
normalized MPN, and eBay's Motors metadata endpoint returns vehicle records.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.services.ebay_service import EbayAPIClient


def _norm(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _specifics(listing: Any) -> dict[str, Any]:
    value = getattr(listing, "item_specifics", None)
    return value if isinstance(value, dict) else {}


def _identity(listing: Any) -> tuple[str, str, str | None]:
    specifics = _specifics(listing)
    brand = str(specifics.get("Brand") or specifics.get("Brand Name") or getattr(listing, "brand", None) or "").strip()
    mpn = str(specifics.get("MPN") or specifics.get("Manufacturer Part Number") or "").strip()
    gtin = str(specifics.get("UPC") or specifics.get("EAN") or specifics.get("GTIN") or "").strip()
    return brand, mpn, gtin or None


def _aspect_values(product: dict[str, Any], *names: str) -> list[str]:
    wanted = {name.casefold() for name in names}
    values: list[str] = []
    aspects = product.get("aspects")
    if isinstance(aspects, list):
        for aspect in aspects:
            if not isinstance(aspect, dict) or str(aspect.get("localizedName") or "").casefold() not in wanted:
                continue
            values.extend(str(value).strip() for value in aspect.get("localizedValues") or [] if str(value).strip())
    elif isinstance(aspects, dict):
        for key, raw in aspects.items():
            if str(key).casefold() not in wanted:
                continue
            values.extend(str(value).strip() for value in (raw if isinstance(raw, list) else [raw]) if str(value).strip())
    return values


def _matches_exact(product: dict[str, Any], brand: str, mpn: str, gtin: str | None = None) -> bool:
    brands = [str(product.get("brand") or "").strip(), *_aspect_values(product, "Brand", "Brand Name")]
    mpns = product.get("mpn") if isinstance(product.get("mpn"), list) else [product.get("mpn")]
    mpns = [str(value).strip() for value in mpns if str(value or "").strip()]
    mpns.extend(_aspect_values(product, "MPN", "Manufacturer Part Number"))
    gtins: list[str] = []
    for field in ("gtin", "upc", "ean", "isbn"):
        value = product.get(field)
        gtins.extend(str(row).strip() for row in (value if isinstance(value, list) else [value]) if str(row or "").strip())
    gtins.extend(_aspect_values(product, "GTIN", "UPC", "EAN", "ISBN"))
    if gtin:
        if not any(_norm(value) == _norm(gtin) for value in gtins):
            return False
        # A GTIN is sufficient identity, but reject contradictory metadata
        # when the candidate also publishes the corresponding field.
        if brand and any(brands) and not any(_norm(value) == _norm(brand) for value in brands if value):
            return False
        if mpn and any(mpns) and not any(_norm(value) == _norm(mpn) for value in mpns if value):
            return False
        return True
    return bool(brand and mpn) and (
        any(_norm(value) == _norm(brand) for value in brands if value)
        and any(_norm(value) == _norm(mpn) for value in mpns if value)
    )


async def research_ebay_product_fitment(listing: Any, account: Any) -> dict[str, Any]:
    """Return exact eBay catalog candidates and sourced fitment, never guesses."""
    researched_at = datetime.now(UTC).isoformat()
    brand, mpn, gtin = _identity(listing)
    if not (brand and mpn) and not gtin:
        return {
            "status": "insufficient_identity", "researched_at": researched_at,
            "source": "ebay_catalog_and_motors_metadata", "candidates": [],
            "compatibilities": [],
            "blocker": "Provide a verified Brand+MPN pair or GTIN before fitment research.",
        }

    client = EbayAPIClient(account.access_token)
    params: dict[str, Any] = {"limit": 50}
    if gtin:
        params["gtin"] = gtin
    if mpn:
        params["mpn"] = mpn
    summaries = await client.request(
        "GET", "/commerce/catalog/v1_beta/product_summary/search", params=params,
        headers={"X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
    )
    candidates = [row for row in summaries.get("productSummaries") or [] if isinstance(row, dict)]
    exact_summaries = [row for row in candidates if _matches_exact(row, brand, mpn, gtin)]
    candidate_view = [{
        "epid": str(row.get("epid") or ""),
        "title": str(row.get("title") or ""),
        "brand": str(row.get("brand") or ""),
        "mpn": row.get("mpn") or [],
        "product_url": str(row.get("productWebUrl") or "") or None,
        "exact_identity_match": row in exact_summaries,
    } for row in candidates[:15]]

    if len(exact_summaries) != 1:
        status = "exact_catalog_match_not_found" if not exact_summaries else "ambiguous_exact_catalog_match"
        return {
            "status": status, "researched_at": researched_at,
            "source": "ebay_catalog_and_motors_metadata", "query": {"brand": brand or None, "mpn": mpn or None, "gtin": gtin},
            "candidates": candidate_view, "compatibilities": [],
            "blocker": "No unique eBay catalog product matched the exact identity; do not assert vehicle fitment.",
        }

    summary = exact_summaries[0]
    epid = str(summary.get("epid") or "").strip()
    if not epid:
        return {
            "status": "catalog_product_id_missing", "researched_at": researched_at,
            "source": "ebay_catalog_and_motors_metadata", "candidates": candidate_view,
            "compatibilities": [], "blocker": "The exact catalog match did not include an ePID.",
        }
    product = await client.request(
        "GET", f"/commerce/catalog/v1_beta/product/{epid}",
        headers={"X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
    )
    if not _matches_exact(product, brand, mpn, gtin):
        return {
            "status": "catalog_identity_conflict", "researched_at": researched_at,
            "source": "ebay_catalog_and_motors_metadata", "candidates": candidate_view,
            "compatibilities": [],
            "blocker": "Full catalog product details do not confirm the exact Brand+MPN pair.",
        }

    result = await client.request(
        "POST", "/sell/metadata/v1/compatibilities/get_product_compatibilities",
        payload={"productIdentifier": {"epid": epid}},
        headers={"X-EBAY-C-MARKETPLACE-ID": "EBAY_MOTORS_US"},
    )
    compatibilities = []
    for detail in result.get("compatibilityDetails") or []:
        if not isinstance(detail, dict):
            continue
        properties = detail.get("compatibilityProperties") or []
        compatibilities.append({
            "properties": [
                {"name": str(prop.get("name") or ""), "value": str(prop.get("value") or "")}
                for prop in properties if isinstance(prop, dict)
            ],
            "source": "ebay_motors_catalog_compatibility",
            "epid": epid,
        })
    return {
        "status": "compatibility_records_found" if compatibilities else "compatibility_records_unavailable",
        "researched_at": researched_at,
        "source": "ebay_catalog_and_motors_metadata",
        "query": {"brand": brand, "mpn": mpn, "gtin": gtin},
        "product": {
            "epid": epid,
            "title": str(product.get("title") or summary.get("title") or ""),
            "brand": str(product.get("brand") or ""),
            "mpn": product.get("mpn") or [],
            "product_url": str(product.get("productWebUrl") or summary.get("productWebUrl") or "") or None,
            "aspects": product.get("aspects") or [],
        },
        "candidates": candidate_view,
        "compatibilities": compatibilities,
        "blocker": None if compatibilities else "The exact catalog product is verified, but eBay returned no Motors fitment records; do not infer vehicle compatibility.",
    }
