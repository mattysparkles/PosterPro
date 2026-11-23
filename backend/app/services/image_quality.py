"""Conservative product-image quality checks shared by Vine media intake paths.

Technical validity is deliberately separate from identity and redistribution rights.
Similarity or repeated use is evidence for review, never proof of a placeholder.
"""
from __future__ import annotations

import hashlib
import io
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

PRIME_LOGO_SHA256 = "0d319bf7425072bad431fd891c02ec8c9f954aa1a9a2e0a230a9049c3ed38105"
PRIME_LOGO_URL_PART = "/marketing/prime/2022primebrand/logos/prime_logo_rgb_prime_blue_master"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MIN_PRODUCT_EDGE = 96
ALLOWED_IMAGE_HOST_SUFFIXES = ("amazon.com", "media-amazon.com", "images-amazon.com", "amazonaws.com")


def image_url_allowed(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_IMAGE_HOST_SUFFIXES)


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
