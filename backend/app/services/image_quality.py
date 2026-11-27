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
_COLOR_TOKENS = {"black", "white", "gray", "grey", "silver", "gold", "blue", "red", "green", "pink", "purple", "orange", "brown", "beige", "clear"}
_MEASURE_RE = re.compile(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|\"|ft|feet|ounces|ounce|oz|lbs?|lb|kg|g|mah|ah|v|w|gb|tb|pcs?|pieces?|pack|count|awg|khz|mhz|ghz)(?![a-z])", re.I)
_MODEL_RE = re.compile(r"(?<![a-z0-9])(?=[a-z0-9-]*\d)[a-z0-9]+(?:-[a-z0-9]+)*(?![a-z0-9])", re.I)
_NON_PRODUCT_MEDIA_PATH_MARKERS = (
    "/logo", "logo_", "_logo", "-logo", "/banner", "banner_", "_banner",
    "/advert", "/promo", "/placeholder", "/no-image", "/no_image",
    "/tracking", "/pixel", "/spacer", "/marketing/prime/",
)


def title_identity_tokens_match(expected_title: str | None, observed_title: str | None) -> bool:
    """Compare product titles on identity-bearing facts, not marketing wording.

    Model/MPN-like tokens, measurements, pack counts, and colors are protected.
    For a match without an exact identifier, the product-type/brand overlap must
    still be meaningful. This function only screens candidates: callers retain
    ambiguous matches for operator review and must not infer image rights.
    """
    return product_title_identity(expected_title, observed_title)["matched"]


def product_title_identity(expected_title: str | None, observed_title: str | None,
                           expected_facts: dict | None = None,
                           observed_facts: dict | None = None) -> dict:
    expected = str(expected_title or "")
    observed = str(observed_title or "")
    normalize = lambda value: re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()
    a, b = normalize(expected), normalize(observed)
    if not a or not b:
        return {"matched": False, "confidence": "low", "reasons": ["title_missing"]}
    expected_facts, observed_facts = expected_facts or {}, observed_facts or {}

    def first_fact(facts: dict, keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = facts.get(key)
            if value:
                return str(value).strip().casefold()
        return None

    # Strong catalog identifiers support identity, but variant conflicts still
    # need to be evaluated below (a model family can span colors/capacities).
    strong_identifier_match = None
    for keys, label in ((('upc', 'ean', 'gtin'), 'gtin'), (('mpn', 'model', 'model_number', 'manufacturer_part_number'), 'model')):
        left, right = first_fact(expected_facts, keys), first_fact(observed_facts, keys)
        if left and right:
            compact = lambda value: re.sub(r"[^a-z0-9]", "", value.casefold())
            if compact(left) != compact(right):
                return {"matched": False, "confidence": "high", "reasons": [f"{label}_conflict"], "expected": left, "observed": right}
            strong_identifier_match = f"exact_{label}_match"

    reasons: list[str] = [strong_identifier_match] if strong_identifier_match else []
    # A source may spell the model in the title but omit it from catalog facts.
    model_a = {x.casefold() for x in _MODEL_RE.findall(a) if any(c.isdigit() for c in x) and len(x) >= 4}
    model_b = {x.casefold() for x in _MODEL_RE.findall(b) if any(c.isdigit() for c in x) and len(x) >= 4}
    if model_a and model_b and not (model_a & model_b):
        return {"matched": False, "confidence": "high", "reasons": ["model_token_conflict"], "expected_models": sorted(model_a), "observed_models": sorted(model_b)}
    if model_a & model_b:
        reasons.append("model_token_match")

    measures = lambda value: {(num.rstrip("0").rstrip(".") if "." in num else num, "count" if unit.lower() in {"pc", "pcs", "piece", "pieces", "pack", "count"} else unit.lower().replace("inches", "in").replace("inch", "in").replace('"', "in").replace("feet", "ft").replace("ounces", "oz").replace("ounce", "oz").replace("lb", "lbs")) for num, unit in _MEASURE_RE.findall(value)}
    # Preserve decimal punctuation for capacity and package-size comparisons;
    # token normalization above intentionally removes punctuation for words.
    ma, mb = measures(expected), measures(observed)
    # Compare like-unit groups only: a different capacity/size/count is a real conflict.
    unit_groups = {"in": "length", "mm": "length", "cm": "length", "ft": "length", "oz": "weight_or_volume", "g": "weight_or_volume", "kg": "weight_or_volume", "lb": "weight_or_volume", "lbs": "weight_or_volume", "ah": "capacity", "mah": "capacity", "v": "voltage", "w": "power", "gb": "storage", "tb": "storage", "count": "count", "awg": "wire_gauge", "khz": "frequency", "mhz": "frequency", "ghz": "frequency"}
    for group in set(unit_groups.get(unit) for _, unit in ma) & set(unit_groups.get(unit) for _, unit in mb):
        left = {(num, unit) for num, unit in ma if unit_groups.get(unit) == group}
        right = {(num, unit) for num, unit in mb if unit_groups.get(unit) == group}
        if left and right and not left & right:
            return {"matched": False, "confidence": "high", "reasons": ["variant_measurement_conflict"], "expected": sorted(left), "observed": sorted(right)}
    if ma & mb:
        reasons.append("measurement_match")

    tokens_a = {x for x in a.split() if x not in _TITLE_CONNECTIVES and not x.isdigit()}
    tokens_b = {x for x in b.split() if x not in _TITLE_CONNECTIVES and not x.isdigit()}
    colors_a, colors_b = tokens_a & _COLOR_TOKENS, tokens_b & _COLOR_TOKENS
    if colors_a and colors_b and not colors_a & colors_b:
        return {"matched": False, "confidence": "high", "reasons": ["color_conflict"], "expected_colors": sorted(colors_a), "observed_colors": sorted(colors_b)}
    if colors_a & colors_b:
        reasons.append("color_match")

    overlap = tokens_a & tokens_b
    recall = len(overlap) / max(1, len(tokens_a))
    precision = len(overlap) / max(1, len(tokens_b))
    score = (0.65 * recall) + (0.35 * precision)
    if strong_identifier_match or model_a & model_b:
        matched, confidence = True, "high"
    elif score >= 0.36 and len(overlap) >= 2:
        matched, confidence = True, "high" if score >= 0.65 and (ma == mb or not ma or not mb) else "review"
        reasons.append("product_type_and_title_overlap")
    else:
        matched, confidence = False, "low"
        reasons.append("insufficient_identity_evidence")
    return {"matched": matched, "confidence": confidence, "score": round(score, 3), "reasons": reasons, "expected_title": expected, "observed_title": observed}


def image_url_allowed(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_IMAGE_HOST_SUFFIXES)


def is_known_prime_placeholder(url: str, sha256: str | None = None) -> bool:
    return PRIME_LOGO_URL_PART in str(url or "").lower() or str(sha256 or "").lower() == PRIME_LOGO_SHA256


def is_suspected_non_product_asset(url: str) -> bool:
    """Flag common logo/ad/tracking paths for rejection or human review."""
    path = urlparse(str(url or "")).path.lower()
    return any(marker in path for marker in _NON_PRODUCT_MEDIA_PATH_MARKERS)


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
    if is_suspected_non_product_asset(source_url):
        return {"verdict": "suspected_placeholder", "reasons": ["source_path_looks_like_logo_ad_or_placeholder"], "evidence": evidence}
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
