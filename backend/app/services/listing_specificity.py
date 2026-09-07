from __future__ import annotations

import re
from typing import Any

GENERIC_CAPTION_TITLES = {
    "unknown",
    "unknown item",
    "unknown product",
    "unknown part",
    "unknown component",
    "miscellaneous",
    "miscellaneous items",
    "product label",
    "parts label",
    "camera accessories",
    "automotive parts",
    "automotive part",
    "auto part",
    "miscellaneous item",
    "circuit board",
    "pcb",
    "electronics board",
    "control board",
    "cardboard box",
    "plastic wrap",
    "not specified",
    "auto parts label",
    "auto parts packaging",
    "label",
    "replacement part",
    "electronic component",
    "desktop game",
    "posterpro slate",
    "slate",
    "art and decor",
    "packaging",
    "package",
    "box",
    "general resale",
    "other > needs category review",
    "needs category review",
    "product information",
    "electronics",
    "component",
    "components",
    "accessories",
    "accessory",
    "album",
    "vinyl album",
    "presented as a product listing",
    "built from the visible label details",
    "product listing built from the item details",
    "marketplace listing built from the item details",
    "review the attached photos",
    "confirm exact model",
    "confirm exact brand",
}

_BARE_IDENTIFIER_RE = re.compile(r"^(?:[a-z0-9]+(?:[._/-][a-z0-9]+)*)$|^(?=.*\d)[a-z0-9][a-z0-9._/-]{2,}$", re.I)

_SPECIFIC_ITEM_SPEC_KEYS = {
    "brand",
    "model",
    "mpn",
    "upc",
    "ean",
    "gtin",
    "isbn",
    "manufacturer part number",
    "manufacturer sku",
    "part number",
    "product type",
    "sku",
}

_IDENTIFIER_RE = re.compile(r"\b(?=[A-Z0-9._/-]*[\d/-])[A-Z0-9][A-Z0-9._/-]{2,}\b", re.I)


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _is_placeholder_value(value: Any) -> bool:
    text = _normalize_text(value)
    return not text or text in {"unknown", "n/a", "na", "none", "does not apply", "not applicable"}


def is_caption_like_title(title: str | None) -> bool:
    normalized = _normalize_text(title)
    if not normalized:
        return False
    if normalized in GENERIC_CAPTION_TITLES:
        return True
    if normalized in {"label", "box", "packaging", "product packaging"}:
        return True
    if re.fullmatch(r"item\s+[a-z0-9]{4,}", normalized):
        return True
    return False


def is_bare_identifier_title(title: str | None) -> bool:
    normalized = _normalize_text(title)
    if not normalized:
        return False
    if is_caption_like_title(normalized):
        return False
    if len(normalized.split()) > 4:
        return False
    if _BARE_IDENTIFIER_RE.fullmatch(normalized):
        return True
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9 _./-]{2,}", normalized) and any(ch.isdigit() for ch in normalized))


def classify_listing_reviewability(
    *,
    title: str | None,
    description: str | None = None,
    category: str | None = None,
    item_specifics: dict[str, Any] | None = None,
    source_metadata: dict[str, Any] | None = None,
    has_images: bool = False,
) -> dict[str, Any]:
    specificity = assess_listing_specificity(
        title=title,
        description=description,
        category=category,
        item_specifics=item_specifics,
        source_metadata=source_metadata,
        has_images=has_images,
    )
    title_text = _normalize_text(title)
    caption_like_title = is_caption_like_title(title)
    bare_identifier_title = is_bare_identifier_title(title)
    return {
        **specificity,
        "caption_like_title": caption_like_title,
        "bare_identifier_title": bare_identifier_title,
        "is_specific_sellable_identity": specificity["status"] == "trusted_for_draft"
        and not caption_like_title
        and not bare_identifier_title,
        "reviewability_class": (
            "pass"
            if specificity["status"] == "trusted_for_draft" and not caption_like_title and not bare_identifier_title
            else "weak"
            if caption_like_title or bare_identifier_title
            else "other_blocker"
        ),
        "title_text": title_text,
    }


def assess_listing_specificity(
    *,
    title: str | None,
    description: str | None = None,
    category: str | None = None,
    item_specifics: dict[str, Any] | None = None,
    source_metadata: dict[str, Any] | None = None,
    has_images: bool = False,
) -> dict[str, Any]:
    item_specifics = item_specifics if isinstance(item_specifics, dict) else {}
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    title_text = _normalize_text(title)
    description_text = _normalize_text(description)
    category_text = _normalize_text(category)
    generic_hits = sorted(
        phrase
        for phrase in GENERIC_CAPTION_TITLES
        if phrase in title_text or phrase in description_text or phrase in category_text
    )

    specificity_evidence: list[str] = []
    for key, value in item_specifics.items():
        key_text = _normalize_text(key)
        if key_text in _SPECIFIC_ITEM_SPEC_KEYS and not _is_placeholder_value(value):
            specificity_evidence.append(f"{key}: {value}")

    def _text_looks_specific(value: Any) -> bool:
        if _is_placeholder_value(value):
            return False
        value_text = _normalize_text(value)
        if not value_text or value_text in GENERIC_CAPTION_TITLES:
            return False
        if is_caption_like_title(value_text):
            return False
        if _IDENTIFIER_RE.search(str(value or "")):
            return True
        words = [part for part in value_text.split(" ") if part]
        if len(words) > 3:
            return True
        return any(char.isdigit() for char in value_text)

    def _add_specificity(source_prefix: str, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        for key in ("identifier", "asin", "model", "mpn", "upc", "ean", "gtin", "isbn", "brand", "product_name", "product_type", "packaging_identity", "title"):
            value = payload.get(key)
            if not _is_placeholder_value(value):
                value_text = _normalize_text(value)
                if key == "title" and (not value_text or value_text in GENERIC_CAPTION_TITLES):
                    continue
                specificity_evidence.append(f"{source_prefix}.{key}: {value}")
        specifications = payload.get("specifications") if isinstance(payload.get("specifications"), dict) else {}
        for key, value in specifications.items():
            key_text = _normalize_text(key)
            if key_text in {"brand", "model", "mpn", "upc", "ean", "gtin", "isbn", "type", "product type", "product_name", "product name", "packaging identity"} and not _is_placeholder_value(value):
                specificity_evidence.append(f"{source_prefix}.specifications.{key}: {value}")
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        if identity:
            _add_specificity(f"{source_prefix}.identity", identity)
        item_specifics_payload = payload.get("item_specifics") if isinstance(payload.get("item_specifics"), dict) else {}
        for key, value in item_specifics_payload.items():
            key_text = _normalize_text(key)
            if key_text in _SPECIFIC_ITEM_SPEC_KEYS and not _is_placeholder_value(value):
                specificity_evidence.append(f"{source_prefix}.item_specifics.{key}: {value}")

    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    amazon_facts = source_metadata.get("amazon_product_facts") if isinstance(source_metadata.get("amazon_product_facts"), dict) else {}
    if _text_looks_specific(title):
        _add_specificity("listing", {"title": title})
    if _text_looks_specific(description):
        _add_specificity("listing", {"description": description})
    _add_specificity("recovery", recovery)
    _add_specificity("recovery.identity", recovery.get("identity") if isinstance(recovery.get("identity"), dict) else {})
    _add_specificity("recovery.full_group_evidence_v2", recovery.get("full_group_evidence_v2") if isinstance(recovery.get("full_group_evidence_v2"), dict) else {})
    _add_specificity("recovery.image_identity_v1", recovery.get("image_identity_v1") if isinstance(recovery.get("image_identity_v1"), dict) else {})
    _add_specificity("recovery.full_group_evidence_v3", recovery.get("full_group_evidence_v3") if isinstance(recovery.get("full_group_evidence_v3"), dict) else {})
    _add_specificity("amazon_product_facts", amazon_facts)
    _add_specificity("source_metadata", source_metadata)

    identifier_like = bool(_IDENTIFIER_RE.search(title or "") or _IDENTIFIER_RE.search(description or ""))
    if identifier_like and specificity_evidence:
        specificity_evidence.append("identifier-like token in title or description")

    title_words = [part for part in title_text.split(" ") if part]
    short_title = len(title_words) <= 4
    generic_title = is_caption_like_title(title) or (short_title and not specificity_evidence)
    generic_category = category_text in {"", "general resale", "other > needs category review", "needs category review"} or any(
        marker in category_text
        for marker in (
            "miscellaneous",
            "needs category review",
            "general resale",
            "collectibles > cameras",
            "product listing built from the item details",
            "marketplace listing built from the item details",
        )
    )
    generic_description = bool(description_text) and any(
        marker in description_text
        for marker in (
            "recovered from preserved inventory photos",
            "review the attached photos",
            "image shows",
            "photo shows",
            "presented as a product listing",
            "built from the visible label details",
            "marketplace listing built from the item details",
        )
    )

    blockers: list[str] = []
    warnings: list[str] = []
    if generic_title:
        if specificity_evidence:
            warnings.append("generic_or_underspecified_title")
        else:
            blockers.append("generic_or_underspecified_title")
    if generic_category:
        if specificity_evidence:
            warnings.append("generic_or_placeholder_category")
        else:
            blockers.append("generic_or_placeholder_category")
    if generic_description and not specificity_evidence:
        if specificity_evidence:
            warnings.append("caption_like_description")
        else:
            blockers.append("caption_like_description")
    if not specificity_evidence:
        blockers.append("missing_specific_identifier_evidence")

    status = "trusted_for_draft"
    if blockers:
        status = "blocked_placeholder_data" if {"generic_or_underspecified_title", "generic_or_placeholder_category", "caption_like_description"} & set(blockers) else "needs_identity_review"

    score = 0.0
    if specificity_evidence:
        score += min(0.35 + 0.12 * len(specificity_evidence), 0.8)
    if identifier_like:
        score += 0.15
    if not generic_title:
        score += 0.15
    if not generic_category:
        score += 0.1
    if short_title and not specificity_evidence:
        score -= 0.35
    if generic_description and not specificity_evidence:
        score -= 0.15
    score = max(0.0, min(score, 1.0))

    return {
        "status": status,
        "score": round(score, 3),
        "blockers": blockers,
        "warnings": warnings,
        "generic_hits": generic_hits,
        "specificity_evidence": specificity_evidence,
        "title_words": title_words,
        "has_identifier_like_token": identifier_like,
    }
