from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Image, ProductMediaCache
from app.services.storage import LocalStorage


def _extract_json_ld_blocks(html: str) -> list[dict]:
    matches = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, flags=re.IGNORECASE | re.DOTALL)
    parsed_blocks: list[dict] = []
    for raw in matches:
        try:
            payload = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        blocks = payload if isinstance(payload, list) else [payload]
        for block in blocks:
            if isinstance(block, dict):
                parsed_blocks.append(block)
    return parsed_blocks


def _extract_json_ld_images(html: str) -> list[str]:
    urls: list[str] = []
    for block in _extract_json_ld_blocks(html):
        image = block.get("image")
        if isinstance(image, list):
            urls.extend(str(item) for item in image if item)
        elif isinstance(image, str):
            urls.append(image)
    return urls


def _extract_json_ld_descriptions(html: str) -> list[str]:
    descriptions: list[str] = []
    for block in _extract_json_ld_blocks(html):
        description = block.get("description")
        if isinstance(description, str) and description.strip():
            descriptions.append(description.strip())
    return descriptions


def _clean_text(value: str | None) -> str:
    text = html_lib.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_product_description(html: str) -> str | None:
    candidates: list[str] = []
    og_description = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', html, flags=re.IGNORECASE)
    if og_description:
        candidates.append(_clean_text(og_description.group(1)))
    candidates.extend(_clean_text(value) for value in _extract_json_ld_descriptions(html))

    section_patterns = [
        r'<div[^>]+id="productDescription"[^>]*>(.*?)</div>',
        r'<div[^>]+id="feature-bullets"[^>]*>(.*?)</div>',
    ]
    for pattern in section_patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        fragment = match.group(1)
        bullets = re.findall(r'<span[^>]*class="a-list-item"[^>]*>(.*?)</span>', fragment, flags=re.IGNORECASE | re.DOTALL)
        if bullets:
            candidates.extend(_clean_text(bullet) for bullet in bullets)
        else:
            candidates.append(_clean_text(fragment))

    cleaned = [candidate for candidate in candidates if candidate]
    if not cleaned:
        return None
    description = "\n".join(dict.fromkeys(cleaned[:8]))
    return description[:1200].strip() or None


def _extract_amazon_product_facts(html: str) -> dict:
    """Extract factual, reusable product-page fields without copying page copy.

    Amazon changes its presentation frequently, so this deliberately accepts
    several stable page/JSON-LD patterns and returns an empty field when a
    value cannot be read.  The caller keeps the source values as provenance and
    writes new listing copy separately.
    """
    title = ""
    title_match = re.search(r'<span[^>]+id="productTitle"[^>]*>(.*?)</span>', html, flags=re.I | re.S)
    if title_match:
        title = _clean_text(title_match.group(1))
    if not title:
        og_title = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, flags=re.I)
        title = _clean_text(og_title.group(1)) if og_title else ""

    price = None
    price_patterns = (
        r'<span[^>]+class="[^"]*a-price-whole[^"]*"[^>]*>\s*([0-9,]+)',
        r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)',
        r'<span[^>]+id="priceblock_[^"]+"[^>]*>\s*\$?\s*([0-9,]+(?:\.[0-9]{1,2})?)',
    )
    for pattern in price_patterns:
        match = re.search(pattern, html, flags=re.I | re.S)
        if not match:
            continue
        try:
            candidate = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if candidate > 0:
            price = round(candidate, 2)
            break

    bullets: list[str] = []
    bullet_block = re.search(r'<div[^>]+id="feature-bullets"[^>]*>(.*?)</div>', html, flags=re.I | re.S)
    if bullet_block:
        for raw in re.findall(r'<span[^>]*class="[^"]*a-list-item[^"]*"[^>]*>(.*?)</span>', bullet_block.group(1), flags=re.I | re.S):
            cleaned = _clean_text(raw)
            if cleaned and cleaned not in bullets:
                bullets.append(cleaned)

    details: dict[str, str] = {}
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, flags=re.I | re.S):
        cells = [_clean_text(cell) for cell in re.findall(r'<(?:th|td)[^>]*>(.*?)</(?:th|td)>', row, flags=re.I | re.S)]
        if len(cells) >= 2 and cells[0] and cells[1] and len(cells[0]) < 100:
            details[cells[0]] = cells[1]

    breadcrumbs = [_clean_text(value) for value in re.findall(r'<a[^>]+class="[^"]*a-link-normal[^"]*"[^>]*>(.*?)</a>', html, flags=re.I | re.S)]
    breadcrumbs = [value for value in breadcrumbs if value and len(value) < 100]
    return {
        "title": title[:512],
        "current_price": price,
        "feature_bullets": bullets[:12],
        "specifications": details,
        "breadcrumbs": breadcrumbs[:12],
        "description": _extract_product_description(html),
    }


def _is_amazon_media_url(url: str | None) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower()
    if not host:
        return False
    return "amazon." in host or host.endswith("amazonaws.com") or "images-amazon.com" in host


class AmazonProductMediaProvider:
    def __init__(self, db: Session, *, owner_user_id: int | None = None):
        self.db = db
        self.storage = LocalStorage()
        self.owner_user_id = owner_user_id

    def get_product_url(self, asin: str, region: str | None = None) -> str:
        suffix = _amazon_domain_suffix(region or settings.amazon_marketplace_region)
        return f"https://www.amazon.{suffix}/dp/{asin}"

    def lookup_by_asin(self, asin: str, *, title_hint: str | None = None) -> dict:
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
                "description": None,
            }

        if settings.amazon_media_lookup_enabled and settings.amazon_media_page_fallback_enabled:
            return self._lookup_from_product_page(asin, title_hint=title_hint)

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

    def cache_gallery_from_remote_urls(self, *, asin: str, image_urls: list[str], title_hint: str | None = None, source_provider: str = "bridge_browser") -> dict:
        cleaned = [str(url).strip() for url in image_urls if str(url).strip() and _is_amazon_media_url(url)]
        if not cleaned:
            return self._cache_result(
                asin,
                product_url=self.get_product_url(asin),
                gallery_image_urls=[],
                local_asset_ids=[],
                primary_image_url=None,
                fetch_status="blocked",
                fetch_error="no_images_found",
                source_provider=source_provider,
        )
        basenames = _build_filename_variants(title_hint or asin, asin, limit=12)
        images: list[Image] = []
        public_urls: list[str] = []
        for index, url in enumerate(cleaned[:12]):
            basename = basenames[index] if index < len(basenames) else f"{(basenames[0] if basenames else 'vine-product')}-{index + 1}"
            try:
                local_path = self.storage.save_from_url(
                    url,
                    prefix="amazon-vine",
                    suggested_basename=basename,
                )
            except Exception:
                continue
            if self.owner_user_id is not None:
                image = Image(
                    user_id=self.owner_user_id,
                    source_url=url,
                    local_path=local_path,
                    image_metadata={
                        "source": "amazon_vine",
                        "asin": asin,
                        "title_hint": title_hint,
                        "sequence": len(images) + 1,
                        "provider": source_provider,
                    },
                )
                self.db.add(image)
                self.db.flush()
                images.append(image)
            public_urls.append(_to_public_media_path(local_path))
        if not public_urls:
            return self._cache_result(
                asin,
                product_url=self.get_product_url(asin),
                gallery_image_urls=[],
                local_asset_ids=[],
                primary_image_url=None,
                fetch_status="blocked",
                fetch_error="no_images_saved",
                source_provider=source_provider,
            )
        return self._cache_result(
            asin,
            product_url=self.get_product_url(asin),
            gallery_image_urls=public_urls,
            local_asset_ids=[image.id for image in images],
            primary_image_url=public_urls[0] if public_urls else None,
            fetch_status="fetched",
            fetch_error=None,
            source_provider=source_provider,
        )

    def _lookup_from_product_page(self, asin: str, *, title_hint: str | None = None) -> dict:
        product_url = self.get_product_url(asin)
        try:
            request_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            }
            with httpx.Client(timeout=15, follow_redirects=True, headers=request_headers) as client:
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
            gallery = [url for url in _extract_json_ld_images(html) if _is_amazon_media_url(url)]
            description = _extract_product_description(html)
            if og_match and _is_amazon_media_url(og_match.group(1)) and og_match.group(1) not in gallery:
                gallery.insert(0, og_match.group(1))
            if not gallery:
                result = self._cache_result(
                    asin,
                    product_url=product_url,
                    gallery_image_urls=[],
                    local_asset_ids=[],
                    primary_image_url=None,
                    fetch_status="blocked",
                    fetch_error="blocked",
                    source_provider="page_metadata",
                )
                result["description"] = description
                return result

            gallery = gallery[:12]
            basenames = _build_filename_variants(title_hint or asin, asin, limit=12)
            images: list[Image] = []
            public_urls: list[str] = []
            for index, url in enumerate(gallery):
                basename = basenames[index] if index < len(basenames) else f"{(basenames[0] if basenames else 'vine-product')}-{index + 1}"
                try:
                    local_path = self.storage.save_from_url(
                        url,
                        prefix="amazon-vine",
                        suggested_basename=basename,
                    )
                except Exception:
                    continue
                if self.owner_user_id is not None:
                    image = Image(
                        user_id=self.owner_user_id,
                        source_url=url,
                        local_path=local_path,
                        image_metadata={
                            "source": "amazon_vine",
                            "asin": asin,
                            "title_hint": title_hint,
                            "sequence": len(images) + 1,
                        },
                    )
                    self.db.add(image)
                    self.db.flush()
                    images.append(image)
                public_urls.append(_to_public_media_path(local_path))
            if not public_urls:
                result = self._cache_result(
                    asin,
                    product_url=product_url,
                    gallery_image_urls=[],
                    local_asset_ids=[],
                    primary_image_url=None,
                    fetch_status="blocked",
                    fetch_error="no_images_saved",
                    source_provider="page_metadata",
                )
                result["description"] = description
                return result

            result = self._cache_result(
                asin,
                product_url=product_url,
                gallery_image_urls=public_urls,
                local_asset_ids=[image.id for image in images],
                primary_image_url=public_urls[0] if public_urls else None,
                fetch_status="fetched",
                fetch_error=None,
                source_provider="page_metadata",
            )
            result["description"] = description
            return result
        except Exception as exc:  # noqa: BLE001
            result = self._cache_result(
                asin,
                product_url=product_url,
                gallery_image_urls=[],
                local_asset_ids=[],
                primary_image_url=None,
                fetch_status="blocked",
                fetch_error=str(exc),
                source_provider="page_metadata",
            )
            result["description"] = None
            return result

    def fetch_product_page_description(self, asin: str, *, title_hint: str | None = None) -> str | None:
        return self.fetch_product_page_facts(asin, title_hint=title_hint).get("description")

    def fetch_product_page_facts(self, asin: str, *, title_hint: str | None = None) -> dict:
        """Return source facts used to enrich a Vine draft.

        This intentionally does not persist unverified text as a listing. The
        Vine service records the facts as Amazon provenance and creates its own
        concise description from them.
        """
        product_url = self.get_product_url(asin)
        try:
            request_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            }
            with httpx.Client(timeout=15, follow_redirects=True, headers=request_headers) as client:
                response = client.get(product_url)
            if response.status_code >= 400:
                return {}
            return _extract_amazon_product_facts(response.text)
        except Exception:
            return {}

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


def _build_filename_variants(label: str, asin: str, limit: int = 8) -> list[str]:
    raw = re.sub(r"[^a-z0-9]+", " ", str(label or "").lower()).strip()
    tokens = [token for token in raw.split() if len(token) > 2]
    if not tokens:
        tokens = ["vine", "product"]
    unique_tokens: list[str] = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique_tokens.append(token)
    product_slug = "-".join(unique_tokens[:10])[:72].strip("-") or "amazon-vine-product"
    # The source gallery does not reliably label each angle.  Use honest,
    # varied image purposes rather than unsupported marketing terms or random
    # identifiers. A numeric suffix is added by storage only if necessary.
    purposes = [
        "product-front",
        "product-angle",
        "product-detail",
        "product-features",
        "product-packaging",
        "product-specification",
        "product-gallery",
        "product-in-use",
        "included-components",
        "product-dimensions",
        "product-rear-view",
        "product-additional-view",
    ]
    return [f"{product_slug}-{purpose}"[:90].strip("-") for purpose in purposes[:limit]]
