from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx
from app.services.amazon_media import AmazonProductMediaProvider


class AmazonProductDiscoveryService:
    def __init__(self, provider: AmazonProductMediaProvider):
        self.provider = provider

    def discover_for_item(self, *, asin: str | None, product_name: str | None, manual_url: str | None = None) -> dict:
        if manual_url:
            return self._discover_from_url(manual_url, confidence="manual")

        normalized_asin = self._normalize_asin(asin)
        if normalized_asin:
            media = self.provider.lookup_by_asin(normalized_asin)
            return {
                "status": "matched" if (media.get("gallery_image_urls") or media.get("primary_image_url")) else "manual_review_needed",
                "confidence": "high",
                "asin": normalized_asin,
                "title": product_name,
                "source_page_url": self.provider.get_product_url(normalized_asin),
                "images": media.get("gallery_image_urls") or ([media.get("primary_image_url")] if media.get("primary_image_url") else []),
                "local_asset_ids": media.get("local_asset_ids") or [],
                "image_status": media.get("status") or "pending",
            }

        if product_name:
            return self._discover_from_title(product_name)

        return {
            "status": "manual_review_needed",
            "confidence": "low",
            "asin": None,
            "title": product_name,
            "source_page_url": None,
            "images": [],
            "local_asset_ids": [],
            "image_status": "missing_identifiers",
        }

    def _discover_from_title(self, title: str) -> dict:
        suffix = self.provider.get_product_url("B000000000").split("/dp/")[0].split("amazon.", 1)[1]
        search_url = f"https://www.amazon.{suffix}/s?k={quote_plus(title)}"
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
        try:
            with httpx.Client(timeout=12, follow_redirects=True, headers=headers) as client:
                response = client.get(search_url)
            if response.status_code >= 400:
                return self._manual_needed(search_url, "search_http_error")

            match = re.search(r'/dp/([A-Z0-9]{10})', response.text)
            if not match:
                return self._manual_needed(search_url, "no_search_match")
            asin = match.group(1)
            media = self.provider.lookup_by_asin(asin)
            return {
                "status": "matched",
                "confidence": "medium",
                "asin": asin,
                "title": title,
                "source_page_url": self.provider.get_product_url(asin),
                "images": media.get("gallery_image_urls") or ([media.get("primary_image_url")] if media.get("primary_image_url") else []),
                "local_asset_ids": media.get("local_asset_ids") or [],
                "image_status": media.get("status") or "pending",
            }
        except Exception:
            return self._manual_needed(search_url, "search_failed")

    def _discover_from_url(self, manual_url: str, *, confidence: str) -> dict:
        asin_match = re.search(r"/dp/([A-Z0-9]{10})", manual_url.upper())
        if asin_match:
            asin = asin_match.group(1)
            media = self.provider.lookup_by_asin(asin)
            return {
                "status": "matched",
                "confidence": confidence,
                "asin": asin,
                "title": None,
                "source_page_url": manual_url,
                "images": media.get("gallery_image_urls") or ([media.get("primary_image_url")] if media.get("primary_image_url") else []),
                "local_asset_ids": media.get("local_asset_ids") or [],
                "image_status": media.get("status") or "pending",
            }
        return self._manual_needed(manual_url, "manual_url_missing_asin")

    def _manual_needed(self, source_url: str, reason: str) -> dict:
        return {
            "status": "manual_review_needed",
            "confidence": "low",
            "asin": None,
            "title": None,
            "source_page_url": source_url,
            "images": [],
            "local_asset_ids": [],
            "image_status": reason,
        }

    def _normalize_asin(self, asin: str | None) -> str | None:
        value = (asin or "").strip().upper()
        return value if re.fullmatch(r"[A-Z0-9]{10}", value) else None
