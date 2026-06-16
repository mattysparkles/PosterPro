from __future__ import annotations

from difflib import SequenceMatcher
import re
from urllib.parse import quote_plus

import httpx
from app.services.amazon_media import AmazonProductMediaProvider


class AmazonProductDiscoveryService:
    def __init__(self, provider: AmazonProductMediaProvider):
        self.provider = provider

    def discover_for_item(
        self,
        *,
        asin: str | None,
        product_name: str | None,
        manual_url: str | None = None,
        allow_title_search: bool = True,
    ) -> dict:
        if manual_url:
            return self._discover_from_url(manual_url, confidence="manual")

        normalized_asin = self._normalize_asin(asin)
        if normalized_asin:
            media = self.provider.lookup_by_asin(normalized_asin, title_hint=product_name)
            description = self.provider.fetch_product_page_description(normalized_asin, title_hint=product_name)
            result = {
                "status": "matched" if (media.get("gallery_image_urls") or media.get("primary_image_url")) else "manual_review_needed",
                "confidence": "high",
                "asin": normalized_asin,
                "title": product_name,
                "source_page_url": self.provider.get_product_url(normalized_asin),
                "images": media.get("gallery_image_urls") or ([media.get("primary_image_url")] if media.get("primary_image_url") else []),
                "local_asset_ids": media.get("local_asset_ids") or [],
                "image_status": media.get("status") or "pending",
                "description": description,
            }
            if product_name and allow_title_search and not result["images"]:
                title_result = self._discover_from_title(product_name)
                if title_result.get("images"):
                    title_result["status"] = "matched"
                    title_result["confidence"] = "high" if title_result.get("confidence") == "high" else "medium"
                    return title_result
            return result

        if product_name and allow_title_search:
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

    def discover_for_vine_item(self, *, asin: str | None, product_name: str | None, manual_url: str | None = None) -> dict:
        return self.discover_for_item(
            asin=asin,
            product_name=product_name,
            manual_url=manual_url,
            allow_title_search=True,
        )

    def _discover_from_title(self, title: str) -> dict:
        suffix = self.provider.get_product_url("B000000000").split("/dp/")[0].split("amazon.", 1)[1]
        search_url = f"https://www.amazon.{suffix}/s?k={quote_plus(title)}"
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
        try:
            with httpx.Client(timeout=12, follow_redirects=True, headers=headers) as client:
                response = client.get(search_url)
            if response.status_code >= 400:
                return self._manual_needed(search_url, "search_http_error")

            candidates = self._extract_search_candidates(response.text)
            scored_candidates = sorted(
                (
                    (self._score_title_match(title, candidate["title"]), candidate)
                    for candidate in candidates
                ),
                key=lambda pair: pair[0],
                reverse=True,
            )
            if not scored_candidates:
                return self._manual_needed(search_url, "no_search_match")

            best_score, best_candidate = scored_candidates[0]
            if best_score < 0.55:
                return self._manual_needed(search_url, "no_search_match")

            asin = best_candidate["asin"]
            resolved_title = best_candidate["title"] or title
            media = self.provider.lookup_by_asin(asin, title_hint=resolved_title)
            description = self.provider.fetch_product_page_description(asin, title_hint=resolved_title)
            return {
                "status": "matched",
                "confidence": "high" if best_score >= 0.75 else "medium",
                "asin": asin,
                "title": resolved_title,
                "source_page_url": self.provider.get_product_url(asin),
                "images": media.get("gallery_image_urls") or ([media.get("primary_image_url")] if media.get("primary_image_url") else []),
                "local_asset_ids": media.get("local_asset_ids") or [],
                "image_status": media.get("status") or "pending",
                "description": description,
                "search_score": round(best_score, 3),
            }
        except Exception:
            return self._manual_needed(search_url, "search_failed")

    def _discover_from_url(self, manual_url: str, *, confidence: str) -> dict:
        asin_match = re.search(r"/dp/([A-Z0-9]{10})", manual_url.upper())
        if asin_match:
            asin = asin_match.group(1)
            media = self.provider.lookup_by_asin(asin)
            description = self.provider.fetch_product_page_description(asin)
            return {
                "status": "matched",
                "confidence": confidence,
                "asin": asin,
                "title": None,
                "source_page_url": manual_url,
                "images": media.get("gallery_image_urls") or ([media.get("primary_image_url")] if media.get("primary_image_url") else []),
                "local_asset_ids": media.get("local_asset_ids") or [],
                "image_status": media.get("status") or "pending",
                "description": description,
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

    def _extract_search_candidates(self, html: str) -> list[dict]:
        candidates: list[dict] = []
        seen_asins: set[str] = set()
        title_patterns = [
            r'<span[^>]*class="a-size-medium a-color-base a-text-normal"[^>]*>(.*?)</span>',
            r'<span[^>]*class="a-size-base-plus a-color-base a-text-normal"[^>]*>(.*?)</span>',
            r'<h2[^>]*>(.*?)</h2>',
        ]
        for match in re.finditer(r'data-asin="([A-Z0-9]{10})"', html, flags=re.IGNORECASE):
            asin = match.group(1).upper()
            if asin in seen_asins:
                continue
            seen_asins.add(asin)
            window = html[match.start() : min(len(html), match.start() + 6000)]
            candidate_title = None
            for pattern in title_patterns:
                title_match = re.search(pattern, window, flags=re.IGNORECASE | re.DOTALL)
                if title_match:
                    candidate_title = self._clean_title_text(title_match.group(1))
                    if candidate_title:
                        break
            if candidate_title:
                candidates.append({"asin": asin, "title": candidate_title})
            if len(candidates) >= 24:
                break
        return candidates

    def _clean_title_text(self, value: str | None) -> str:
        text = re.sub(r"<[^>]+>", " ", str(value or ""))
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _score_title_match(self, query: str | None, candidate: str | None) -> float:
        normalized_query = self._normalize_text(query)
        normalized_candidate = self._normalize_text(candidate)
        if not normalized_query or not normalized_candidate:
            return 0.0
        query_tokens = set(normalized_query.split())
        candidate_tokens = set(normalized_candidate.split())
        if not query_tokens or not candidate_tokens:
            return 0.0
        overlap = len(query_tokens & candidate_tokens)
        recall = overlap / len(query_tokens)
        precision = overlap / len(candidate_tokens)
        similarity = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
        return round((0.5 * recall) + (0.2 * precision) + (0.3 * similarity), 4)

    def _normalize_text(self, value: str | None) -> str:
        text = re.sub(r"<[^>]+>", " ", str(value or "").lower())
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()
