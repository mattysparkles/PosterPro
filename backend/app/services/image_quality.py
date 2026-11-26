"""Conservative product-image quality checks shared by Vine media intake paths.

Technical validity is deliberately separate from identity and redistribution rights.
Similarity or repeated use is evidence for review, never proof of a placeholder.
"""
from __future__ import annotations

import hashlib
import io
import httpx
import re
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

PRIME_LOGO_SHA256 = "0d319bf7425072bad431fd891c02ec8c9f954aa1a9a2e0a230a9049c3ed38105"
PRIME_LOGO_URL_PART = "/marketing/prime/2022primebrand/logos/prime_logo_rgb_prime_blue_master"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MIN_PRODUCT_EDGE = 96
ALLOWED_IMAGE_HOST_SUFFIXES = ("amazon.com", "media-amazon.com", "images-amazon.com", "amazonaws.com")
_TITLE_CONNECTIVES = {"a", "an", "and", "by", "for", "from", "in", "of", "on", "the", "to", "with", "new", "best"}


def title_identity_tokens_match(expected_title: str | None, observed_title: str | None) -> bool:
    """Return true only when observed title retains all identifying source tokens.

    This is an identity cross-check for a product-name-first search, not a
    fuzzy similarity score. Any missing model, size, color, or pack-count token
    keeps the candidate out of automatic acceptance.
    """
    normalize = lambda value: re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()
    expected = {token for token in normalize(expected_title).split() if token not in _TITLE_CONNECTIVES}
    observed = set(normalize(observed_title).split())
    return bool(expected) and expected.issubset(observed)


def image_url_allowed(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_IMAGE_HOST_SUFFIXES)


def is_known_prime_placeholder(url: str, sha256: str | None = None) -> bool:
    return PRIME_LOGO_URL_PART in str(url or "").lower() or str(sha256 or "").lower() == PRIME_LOGO_SHA256


def filter_known_placeholders(urls: list[str], *, hashes: list[str | None] | None = None) -> tuple[list[str], list[dict]]:
    """Filter only individually confirmed Prime assets; keep uncertain candidates."""
    kept: list[str] = []
    rejected: list[dict] = []
    hashes = hashes or []
    for index, raw in enumerate(urls or []):
        url = str(raw or "").strip()
        digest = hashes[index] if index < len(hashes) else None
        if not url:
            rejected.append({"index": index, "verdict": "unreachable", "reason": "empty_url"})
        elif is_known_prime_placeholder(url, digest):
            rejected.append({"index": index, "url": url, "sha256": digest, "verdict": "confirmed_placeholder", "reason": "known_amazon_prime_logo_asset"})
        else:
            kept.append(url)
    return kept, rejected


def merge_gallery_candidates(prior: list[str], incoming: list[str]) -> tuple[list[str], list[dict]]:
    """Retry-safe ordered union that removes only known placeholders."""
    accepted, rejected = filter_known_placeholders([*(prior or []), *(incoming or [])])
    return list(dict.fromkeys(accepted)), rejected


def validate_image_bytes(data: bytes, *, source_url: str = "", expected_asin: str | None = None,
                         page_asin: str | None = None, selected_variant: str | None = None,
                         expected_variant: str | None = None, content_type: str | None = None) -> dict:
    evidence: dict = {"sha256": None, "dimensions": None, "content_type": content_type, "source_host": urlparse(source_url).hostname}
    if not data or len(data) > MAX_IMAGE_BYTES:
        return {"verdict": "decode_failed", "reasons": ["empty_or_over_limit"], "evidence": evidence}
    digest = hashlib.sha256(data).hexdigest()
    evidence["sha256"] = digest
    if digest == PRIME_LOGO_SHA256 or PRIME_LOGO_URL_PART in source_url.lower():
        return {"verdict": "confirmed_placeholder", "reasons": ["known_amazon_prime_logo_asset"], "evidence": evidence}
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            fmt = image.format
    except (UnidentifiedImageError, OSError, ValueError):
        return {"verdict": "decode_failed", "reasons": ["image_decode_failed"], "evidence": evidence}
    evidence.update(dimensions=[width, height], format=fmt)
    if width < MIN_PRODUCT_EDGE or height < MIN_PRODUCT_EDGE:
        return {"verdict": "suspected_placeholder", "reasons": ["tiny_image_dimensions"], "evidence": evidence}
    if expected_asin and page_asin and expected_asin.upper() != page_asin.upper():
        return {"verdict": "wrong_product", "reasons": ["product_page_asin_mismatch"], "evidence": evidence}
    if expected_variant and selected_variant and expected_variant.casefold() != selected_variant.casefold():
        return {"verdict": "wrong_product", "reasons": ["selected_variant_mismatch"], "evidence": evidence}
    if expected_asin and not page_asin:
        return {"verdict": "identity_unverified", "reasons": ["page_asin_not_verified"], "evidence": evidence}
    return {"verdict": "valid_exact_product", "reasons": ["image_decodes", "page_asin_verified" if page_asin else "identity_evidence_not_supplied"], "evidence": evidence}


def validate_image_file(path: str, **kwargs) -> dict:
    try:
        with open(path, "rb") as source:
            data = source.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        return {"verdict": "unreachable", "reasons": ["local_file_missing_or_unreadable"], "evidence": {"path": path}}
    result = validate_image_bytes(data, **kwargs)
    result["evidence"]["path"] = path
    return result


def fetch_candidate_image(url: str) -> tuple[bytes, dict]:
    """Fetch one authorized Amazon CDN candidate with host, time and size bounds."""
    if not image_url_allowed(url):
        raise ValueError("candidate_host_not_allowlisted")
    chunks: list[bytes] = []
    total = 0
    timeout = httpx.Timeout(connect=4.0, read=6.0, write=4.0, pool=4.0)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        with client.stream("GET", url, headers={"Accept": "image/avif,image/webp,image/png,image/jpeg"}) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location", "")
                if not image_url_allowed(location):
                    raise ValueError("candidate_redirect_host_not_allowlisted")
                raise ValueError("candidate_redirect_requires_retry")
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
            if not content_type.startswith("image/"):
                raise ValueError("candidate_response_not_image")
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ValueError("candidate_image_over_limit")
                chunks.append(chunk)
    data = b"".join(chunks)
    verdict = validate_image_bytes(data, source_url=url, content_type=content_type)
    if verdict["verdict"] in {"decode_failed", "confirmed_placeholder", "suspected_placeholder"}:
        raise ValueError(verdict["verdict"])
    return data, verdict
