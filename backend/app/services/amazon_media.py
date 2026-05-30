from __future__ import annotations

import json
import re
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Image, ProductMediaCache
from app.services.storage import LocalStorage


def _extract_json_ld_images(html: str) -> list[str]:
    matches = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, flags=re.IGNORECASE | re.DOTALL)
    urls: list[str] = []
    for raw in matches:
        try:
            payload = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        blocks = payload if isinstance(payload, list) else [payload]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            image = block.get("image")
            if isinstance(image, list):
                urls.extend(str(item) for item in image if item)
            elif isinstance(image, str):
                urls.append(image)
    return urls


class AmazonProductMediaProvider:
    def __init__(self, db: Session, *, owner_user_id: int | None = None):
        self.db = db
        self.storage = LocalStorage()
        self.owner_user_id = owner_user_id

    def get_product_url(self, asin: str, region: str | None = None) -> str:
        suffix = _amazon_domain_suffix(region or settings.amazon_marketplace_region)
        return f"https://www.amazon.{suffix}/dp/{asin}"

    def lookup_by_asin(self, asin: str) -> dict:
        cached = self.db.execute(
            select(ProductMediaCache).where(
                ProductMediaCache.asin == asin,
                ProductMediaCache.marketplace_region == settings.amazon_marketplace_region.upper(),
            )
        ).scalar_one_or_none()
        if cached and cached.fetch_status == "fetched":
            return {
                "status": "cached",
                "primary_image_url": cached.primary_image_url,
                "gallery_image_urls": cached.gallery_image_urls_json or [],
                "local_asset_ids": cached.local_asset_ids_json or [],
            }

        if settings.amazon_media_lookup_enabled and settings.amazon_media_page_fallback_enabled:
            return self._lookup_from_product_page(asin)

        return self._cache_result(
            asin,
            product_url=self.get_product_url(asin),
            gallery_image_urls=[],
            local_asset_ids=[],
            primary_image_url=None,
            fetch_status="manual_only",
            fetch_error="manual_only",
            source_provider="manual",
        )

    def fetch_primary_image(self, asin: str) -> str | None:
        return self.lookup_by_asin(asin).get("primary_image_url")

    def fetch_gallery_images(self, asin: str) -> list[str]:
        return self.lookup_by_asin(asin).get("gallery_image_urls") or []

    def _lookup_from_product_page(self, asin: str) -> dict:
        product_url = self.get_product_url(asin)
        try:
            with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "PosterPro/1.0"}) as client:
                response = client.get(product_url)
            if response.status_code >= 400:
                return self._cache_result(
                    asin,
                    product_url=product_url,
                    gallery_image_urls=[],
                    local_asset_ids=[],
                    primary_image_url=None,
                    fetch_status="blocked",
                    fetch_error=f"http_{response.status_code}",
                    source_provider="page_metadata",
                )

            html = response.text
            og_match = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html, flags=re.IGNORECASE)
            gallery = _extract_json_ld_images(html)
            if og_match and og_match.group(1) not in gallery:
                gallery.insert(0, og_match.group(1))
            if not gallery:
                return self._cache_result(
                    asin,
                    product_url=product_url,
                    gallery_image_urls=[],
                    local_asset_ids=[],
                    primary_image_url=None,
                    fetch_status="blocked",
                    fetch_error="blocked",
                    source_provider="page_metadata",
                )

            local_paths = [self.storage.save_from_url(url, prefix="amazon-vine") for url in gallery[:5]]
            images: list[Image] = []
            public_urls: list[str] = []
            for original_url, local_path in zip(gallery[:5], local_paths, strict=True):
                if self.owner_user_id is not None:
                    image = Image(user_id=self.owner_user_id, source_url=original_url, local_path=local_path)
                    self.db.add(image)
                    self.db.flush()
                    images.append(image)
                public_urls.append(_to_public_media_path(local_path))

            return self._cache_result(
                asin,
                product_url=product_url,
                gallery_image_urls=public_urls,
                local_asset_ids=[image.id for image in images],
                primary_image_url=public_urls[0] if public_urls else None,
                fetch_status="fetched",
                fetch_error=None,
                source_provider="page_metadata",
            )
        except Exception as exc:  # noqa: BLE001
            return self._cache_result(
                asin,
                product_url=product_url,
                gallery_image_urls=[],
                local_asset_ids=[],
                primary_image_url=None,
                fetch_status="blocked",
                fetch_error=str(exc),
                source_provider="page_metadata",
            )

    def _cache_result(
        self,
        asin: str,
        *,
        product_url: str,
        gallery_image_urls: list[str],
        local_asset_ids: list[int],
        primary_image_url: str | None,
        fetch_status: str,
        fetch_error: str | None,
        source_provider: str,
    ) -> dict:
        cache = self.db.execute(
            select(ProductMediaCache).where(
                ProductMediaCache.asin == asin,
                ProductMediaCache.marketplace_region == settings.amazon_marketplace_region.upper(),
            )
        ).scalar_one_or_none()
        if cache is None:
            cache = ProductMediaCache(asin=asin, marketplace_region=settings.amazon_marketplace_region.upper())
        cache.product_url = product_url
        cache.primary_image_url = primary_image_url
        cache.gallery_image_urls_json = gallery_image_urls
        cache.local_asset_ids_json = local_asset_ids
        cache.source_provider = source_provider
        cache.fetch_status = fetch_status
        cache.fetch_error = fetch_error
        cache.fetched_at = datetime.utcnow()
        self.db.add(cache)
        self.db.commit()
        self.db.refresh(cache)
        return {
            "status": cache.fetch_status,
            "primary_image_url": cache.primary_image_url,
            "gallery_image_urls": cache.gallery_image_urls_json or [],
            "local_asset_ids": cache.local_asset_ids_json or [],
        }


def _to_public_media_path(path: str) -> str:
    marker = "/storage/"
    if marker in path:
        return f"/media/{path.split(marker, 1)[1]}"
    return path


def _amazon_domain_suffix(region: str | None) -> str:
    normalized = (region or "US").strip().upper()
    return {
        "US": "com",
        "CA": "ca",
        "MX": "com.mx",
        "BR": "com.br",
        "UK": "co.uk",
        "GB": "co.uk",
        "DE": "de",
        "FR": "fr",
        "IT": "it",
        "ES": "es",
        "NL": "nl",
        "SE": "se",
        "PL": "pl",
        "TR": "com.tr",
        "AE": "ae",
        "SA": "sa",
        "EG": "eg",
        "IN": "in",
        "JP": "co.jp",
        "AU": "com.au",
        "SG": "sg",
    }.get(normalized, "com")
