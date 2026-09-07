from __future__ import annotations

import json
import re
import time
import hashlib
import threading
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.services.category_rules import suggest_category_from_text
from app.prompts.templates import LISTING_PROMPT_TEMPLATE, get_prompt_template
from app.models.models import AIRequestLedger
from app.services.ai_guard import reserve_durable, reconcile_durable, provider_circuit_open, open_provider_circuit, close_provider_circuit

logger = logging.getLogger(__name__)


_GENERIC_TITLE_MARKERS = {
    "item",
    "items",
    "product",
    "products",
    "part",
    "parts",
    "accessory",
    "accessories",
    "label",
    "miscellaneous",
    "unknown",
    "unknown item",
    "unknown product",
    "needs review",
    "review required",
}

_MARKETPLACE_RULES: dict[str, dict[str, Any]] = {
    "ebay": {
        "title_max": 80,
        "description_max": None,
        "condition_required": True,
        "category_required": True,
        "notes": "SEO-friendly title; rich description; category-aware item specifics.",
    },
    "facebook": {
        "title_max": 120,
        "description_max": None,
        "condition_required": True,
        "category_required": True,
        "notes": "Natural local-sale copy; concise but buyer-friendly.",
    },
    "mercari": {
        "title_max": 100,
        "description_max": 1000,
        "condition_required": True,
        "category_required": True,
        "notes": "Dense listing copy with concise description under 1000 characters.",
    },
    "poshmark": {
        "title_max": 100,
        "description_max": 2000,
        "condition_required": True,
        "category_required": True,
        "notes": "Fashion-style concise copy when applicable.",
    },
    "vinted": {
        "title_max": 100,
        "description_max": 1000,
        "condition_required": True,
        "category_required": True,
        "notes": "Clear condition and buyer-facing item summary.",
    },
}

_AI_GUARD_LOCK = threading.Lock()
_AI_SUCCESS_CACHE: dict[str, dict[str, Any]] = {}
_AI_ATTEMPTED: set[str] = set()
_AI_CIRCUIT_UNTIL = 0.0
_AI_CIRCUIT_REASON: str | None = None


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _text_words(value: Any) -> list[str]:
    text = _normalize_text(value).lower()
    return [word for word in re.split(r"[^a-z0-9]+", text) if word]


def _is_placeholder_text(value: Any) -> bool:
    text = _normalize_text(value).lower()
    return not text or text in {"unknown", "unknown item", "unknown product", "unknown part", "unknown component", "n/a", "na", "none", "needs review"}


_SPOKEN_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def extract_explicit_transcript_fields(text: Any) -> dict[str, Any]:
    """Small, deterministic resilience parser for explicit intake facts."""
    raw = _normalize_text(text)
    low = raw.lower()
    out: dict[str, Any] = {"provenance": "LOCAL_TRANSCRIPT_PARSER", "confidence": "medium"}
    number = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
    quantity = re.search(rf"(?:quantity\s*(?:is|of)?|have|there are|got)\s*{number}\b", low)
    if quantity:
        token = quantity.group(1); out["quantity"] = _SPOKEN_NUMBERS.get(token, int(token) if token.isdigit() else None)
    for pattern, value in ((r"\b(brand new|new open box|new in box|open box|brand new|new|pre[- ]owned|used|for parts|parts only|not working|damaged)\b", None),):
        match = re.search(pattern, low)
        if match:
            condition = match.group(1).replace("-", " ")
            out["condition"] = {"brand new": "New", "new in box": "New", "new open box": "New - Open Box", "open box": "New - Open Box", "new": "New", "pre owned": "Used", "used": "Used", "for parts": "For Parts", "parts only": "For Parts", "not working": "For Parts", "damaged": "Used - Damaged"}.get(condition, condition.title())
            break
    catalog = re.search(r"(?:catalog(?:ue)?(?:\s*(?:number|no\.?|#))?|cat\.?\s*(?:number|no\.?|#))\s*([A-Za-z0-9][A-Za-z0-9\-/]*)", raw, re.I)
    if catalog: out["catalog_number"] = catalog.group(1)
    part = re.search(r"(?:part(?:\s*number)?|mpn|model(?:\s*number)?)\s*(?:is|:|#)?\s*([A-Za-z0-9][A-Za-z0-9\-/]*)", raw, re.I)
    if part: out["model"] = part.group(1)
    price = re.search(r"(?:price|priced at|sell for)\s*\$?([0-9]+(?:\.[0-9]{1,2})?)", low)
    if price: out["price"] = float(price.group(1))
    weight = re.search(r"(?:weight|weighs)\s*(?:is|about|around)?\s*([0-9]+(?:\.[0-9]+)?)\s*(lb|lbs|pounds|oz|ounces|kg|g)\b", low)
    if weight: out["weight"] = f"{weight.group(1)} {weight.group(2)}"
    if re.search(r"sell (?:them )?(?:separately|individually)|each one separately", low): out.update(units_per_sale=1, bundle_strategy="individual")
    elif re.search(r"sell (?:them )?(?:all together|as a bundle)|one bundle", low): out["bundle_strategy"] = "bundle"
    out["marketplace_targets"] = [m for m in ("ebay", "facebook", "mercari", "poshmark", "vinted") if re.search(rf"\b{m}\b", low)]
    if re.search(r"(?:appears|seems|think).*complete|pretty complete", low): out.update(completeness_statement="Appears complete", verification_required=True)
    # Capture the common “This is a …” identity clause without swallowing later facts.
    identity = re.search(r"(?:this is|item name(?: is)?|product(?: name)?(?: is)?)\s+(?:a\s+|an\s+)?(.+?)(?=,?\s+(?:catalog|condition|it is|i have|quantity|sell|research|price)|\.|$)", raw, re.I)
    if identity:
        name = _normalize_text(identity.group(1)).strip(" ,")
        name = re.sub(r"\bbranded\b", "", name, flags=re.I).strip()
        out["product_name"] = name
    brand = re.search(r"\b([A-Z][A-Za-z0-9&'-]{2,}(?:\s+[A-Z][A-Za-z0-9&'-]{2,})?)\s+branded\b", raw)
    if brand: out["brand"] = brand.group(1)
    return out


def _recovery_payloads(source_metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    payloads: list[dict[str, Any]] = []
    for key in ("identity", "full_group_evidence_v3", "full_group_evidence_v2", "image_identity_v1"):
        payload = recovery.get(key)
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _best_recovery_identity(source_metadata: dict[str, Any] | None) -> dict[str, Any]:
    for payload in _recovery_payloads(source_metadata):
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        candidates = [identity, payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for field in ("title", "brand", "model", "mpn", "identifier", "product_name", "product_type", "packaging_identity", "category"):
                value = candidate.get(field)
                if not _is_placeholder_text(value):
                    merged = dict(payload)
                    if candidate is identity:
                        merged.update(identity)
                    else:
                        merged.update(candidate)
                    return merged
    return {}


def build_marketplace_title(
    *,
    title: str | None,
    item_specifics: dict[str, Any] | None = None,
    category_hint: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> str:
    title_text = " ".join(str(title or "").split()).strip()
    specifics = item_specifics if isinstance(item_specifics, dict) else {}
    parts: list[str] = []

    def add_part(value: Any) -> None:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return
        lower = text.lower()
        if lower in {"needs review", "unknown", "tbd", "n/a", "na", "none"}:
            return
        if text not in parts:
            parts.append(text)

    recovery_identity = _best_recovery_identity(source_metadata)
    recovery_title = _normalize_text(
        (recovery_identity.get("identity") or {}).get("title") if isinstance(recovery_identity.get("identity"), dict) else recovery_identity.get("title")
    )
    if title_text and title_text.lower() not in _GENERIC_TITLE_MARKERS and len(title_text.split()) > 2:
        if recovery_title:
            candidate_words = [word for word in _text_words(recovery_title) if len(word) > 2]
            if candidate_words:
                overlap = sum(1 for word in candidate_words if word in _text_words(title_text))
                if overlap / max(len(candidate_words), 1) < 0.5:
                    title_text = recovery_title
                else:
                    return title_text[:80]
            else:
                return title_text[:80]
        else:
            return title_text[:80]

    if recovery_title and (not title_text or title_text.lower() in _GENERIC_TITLE_MARKERS or _is_placeholder_text(title_text)):
        add_part(recovery_title)

    for field in ("Brand", "Model", "Type", "Product Type", "Color", "Capacity", "Voltage", "Wattage", "MPN", "UPC"):
        add_part(specifics.get(field))
    if not parts and recovery_identity:
        for field in ("brand", "model", "mpn", "identifier", "product_name", "product_type", "packaging_identity"):
            add_part(recovery_identity.get(field))

    if not parts and category_hint:
        leaf = str(category_hint).split(">")[-1].strip()
        add_part(leaf)

    if not parts and title_text:
        add_part(title_text)

    if not parts:
        return "Marketplace listing"

    title_out = " ".join(parts)
    return title_out[:80].strip() or "Marketplace listing"


def build_listing_description(
    *,
    title: str,
    item_specifics: dict[str, Any] | None = None,
    included: str | None = None,
    condition_notes: str | None = None,
    photo_notes: list[str] | None = None,
    source_label: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> str:
    title_text = " ".join(str(title or "").split()).strip() or "Item"
    specifics = item_specifics if isinstance(item_specifics, dict) else {}
    feature_bits: list[str] = []
    for field in ("Brand", "Model", "Type", "Color", "Capacity", "Voltage", "Wattage", "Compatible Capsule/Pad System", "MPN", "UPC"):
        value = specifics.get(field)
        if value is None:
            continue
        value_text = " ".join(str(value).split()).strip()
        if not value_text or value_text.lower() in {"needs review", "unknown", "n/a", "na", "tbd"}:
            continue
        feature_bits.append(f"{field.lower()}: {value_text}")
    recovery_identity = _best_recovery_identity(source_metadata)
    if recovery_identity:
        for field in ("brand", "model", "mpn", "identifier", "product_name", "product_type", "packaging_identity"):
            value = recovery_identity.get(field)
            if _is_placeholder_text(value):
                continue
            value_text = _normalize_text(value)
            if value_text and f"{field.lower()}: {value_text.lower()}" not in [bit.lower() for bit in feature_bits]:
                feature_bits.append(f"{field.lower()}: {value_text}")

    parts: list[str] = [f"{title_text} is a marketplace listing built from the item details available for this product."]
    if feature_bits:
        parts.append(f"Key details include {', '.join(feature_bits[:5])}.")
    if included:
        included_text = " ".join(str(included).split()).strip()
        if included_text:
            parts.append(f"Included components shown or identified: {included_text}.")
    if condition_notes:
        condition_text = " ".join(str(condition_notes).split()).strip()
        if condition_text:
            parts.append(f"Condition notes: {condition_text}.")
    if source_label:
        parts.append(f"Prepared from {source_label} signals for buyer review.")
    if photo_notes:
        notes = [str(note).strip() for note in photo_notes if str(note).strip()]
        if notes:
            parts.append(f"Photo evidence notes: {'; '.join(notes[:2])}.")
    parts.append("Confirm exact model, included parts, and compatibility from the attached evidence before publishing.")
    return " ".join(parts).strip()


class ListingAIService:
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model

    def generate(self, image_signals: dict[str, Any], *, db: Any = None, user_id: int | None = None, listing_id: int | None = None) -> dict[str, Any]:
        fallback = self._fallback_generation(image_signals)
        reservation_id = None
        durable_cached_result = None
        if db is not None and settings.ai_cost_mode == "COMPLIMENTARY_ONLY":
            key = hashlib.sha256(json.dumps({k: image_signals.get(k) for k in ("listing_id", "canonical_item_id", "voice_transcript", "voice_notes", "title_hint", "existing_specifics", "photo_keywords", "marketplace_targets")}, sort_keys=True, default=str).encode()).hexdigest()
            reservation = reserve_durable(db, key=key, pool="mini", estimated_tokens=8000, user_id=user_id, listing_id=listing_id, purpose="listing_intelligence")
            if reservation.get("status") == "DUPLICATE_SUPPRESSED":
                try:
                    from sqlalchemy import select
                    from app.models.models import AIRequestReservation
                    prior = db.execute(select(AIRequestReservation).where(AIRequestReservation.id == reservation.get("reservation_id"))).scalar_one_or_none()
                    durable_cached_result = prior.result_json if prior else None
                except Exception:
                    durable_cached_result = None
            if reservation.get("status") != "RESERVED":
                if not durable_cached_result:
                    image_signals = {**image_signals, "_provider_blocked": reservation}
            else:
                reservation_id = reservation.get("reservation_id")
        llm_bundle = {"result": durable_cached_result, "metadata": {"validation_status": "CACHE_REUSE", "response_provider": "openai"}} if durable_cached_result else self._llm_generation(image_signals, db=db)
        llm = llm_bundle.get("result") if isinstance(llm_bundle, dict) else None
        llm_metadata = llm_bundle.get("metadata") if isinstance(llm_bundle, dict) else {}
        merged = {**fallback, **(llm or {})}

        merged["title"] = str(merged.get("title") or fallback["title"])[:80]
        merged["description"] = str(merged.get("description") or fallback["description"]).strip()
        merged["category_suggestion"] = str(merged.get("category_suggestion") or fallback["category_suggestion"]).strip()
        merged["condition"] = str(merged.get("condition") or fallback["condition"]).strip()
        merged["item_specifics"] = merged.get("item_specifics") if isinstance(merged.get("item_specifics"), dict) else fallback["item_specifics"]
        merged["tags"] = self._normalize_string_list(merged.get("tags"), fallback["tags"])
        merged["missing_information"] = self._normalize_string_list(merged.get("missing_information"), fallback["missing_information"])
        merged["photo_notes"] = self._normalize_string_list(merged.get("photo_notes"), fallback["photo_notes"])
        merged["research_queries"] = self._normalize_string_list(merged.get("research_queries"), fallback["research_queries"])
        merged["estimated_value"] = self._safe_float(merged.get("estimated_value"), fallback["estimated_value"])
        merged["draft_quality"] = self._draft_quality(merged)
        merged["title"] = self._sanitize_claims(merged["title"], image_signals)
        merged["description"] = self._sanitize_claims(merged["description"], image_signals)
        merged["prompt_used"] = LISTING_PROMPT_TEMPLATE
        merged["intelligence_prompt"] = get_prompt_template("generate_listing_intelligence")
        merged["model_used"] = self.model if llm else "heuristic-fallback"
        merged["generation_source"] = "openai" if llm else "fallback"
        merged["schema_version"] = "posterpro_listing_intelligence_v1"
        merged["marketplace_targets"] = self._normalize_string_list(
            merged.get("marketplace_targets"),
            image_signals.get("marketplace_targets") or ["ebay", "facebook"],
        )
        merged["marketplace_rules"] = self._marketplace_rules()
        ai_marketplace_drafts = llm.get("marketplace_drafts") if isinstance(llm, dict) and isinstance(llm.get("marketplace_drafts"), dict) else {}
        heuristic_marketplace_drafts = self._build_marketplace_drafts(merged)
        merged["marketplace_drafts"] = {
            **heuristic_marketplace_drafts,
            **ai_marketplace_drafts,
        }
        merged["structured_listing_json"] = self._build_structured_listing_json(merged, image_signals)
        merged["ai_metadata"] = {
            "model": merged["model_used"],
            "generation_source": merged["generation_source"],
            "schema_version": merged["schema_version"],
            "request_id": llm_metadata.get("request_id"),
            "request_timestamp": llm_metadata.get("request_timestamp"),
            "latency_ms": llm_metadata.get("latency_ms"),
            "validation_status": llm_metadata.get("validation_status") or "fallback_only",
            "validation_errors": llm_metadata.get("validation_errors") or [],
            "response_provider": llm_metadata.get("response_provider") or ("openai" if llm else "fallback"),
            "service_tier": llm_metadata.get("service_tier"),
            "raw_request_preview": llm_metadata.get("raw_request_preview"),
            "error": llm_metadata.get("error"),
            "budget_status": image_signals.get("_provider_blocked", {}).get("status") if isinstance(image_signals.get("_provider_blocked"), dict) else ("RESERVED" if reservation_id else None),
        }
        merged["intelligence_state"] = {
            "voice_recorded": bool(image_signals.get("voice_transcript") or image_signals.get("voice_notes")),
            "transcript_sent": bool(image_signals.get("voice_transcript")),
            "enrichment_sent": bool(llm or fallback),
            "structured_json_received": bool(merged.get("structured_listing_json")),
            "fields_populated": bool(merged.get("title") or merged.get("item_specifics")),
        }
        if db is not None:
            try:
                sig = hashlib.sha256(json.dumps(image_signals, sort_keys=True, default=str).encode()).hexdigest()
                usage = (llm_bundle or {}).get("usage") if isinstance(llm_bundle, dict) else {}
                db.add(AIRequestLedger(user_id=user_id, listing_id=listing_id, purpose="listing_intelligence", provider=merged["ai_metadata"].get("response_provider"), model=merged["ai_metadata"].get("model"), endpoint="/v1/responses", input_signature=sig, status="success" if merged.get("generation_source") == "openai" else "fallback", image_count=int(image_signals.get("image_count") or 0), latency_ms=merged["ai_metadata"].get("latency_ms"), request_id=merged["ai_metadata"].get("request_id"), service_tier=merged["ai_metadata"].get("service_tier"), tokens_input=(usage or {}).get("input_tokens"), tokens_output=(usage or {}).get("output_tokens"), total_tokens=(usage or {}).get("total_tokens"), error=merged["ai_metadata"].get("error")))
                db.flush()
                if reservation_id:
                    reconcile_durable(db, int(reservation_id), actual_tokens=int((usage or {}).get("total_tokens") or 0), state="RECONCILED" if llm else "RELEASED", error=merged["ai_metadata"].get("error"))
                    if llm:
                        from sqlalchemy import select
                        from app.models.models import AIRequestReservation
                        reservation_row = db.execute(select(AIRequestReservation).where(AIRequestReservation.id == reservation_id)).scalar_one_or_none()
                        if reservation_row:
                            reservation_row.result_json = llm
                            db.commit()
            except Exception as exc:
                # Ledger persistence must never block field intake, but it must
                # remain observable for accounting remediation.
                logger.warning("ai_ledger_persist_failed listing_id=%s error=%s", listing_id, type(exc).__name__)
        return merged

    def _llm_generation(self, image_signals: dict[str, Any], db: Any = None) -> dict[str, Any] | None:
        global _AI_CIRCUIT_UNTIL, _AI_CIRCUIT_REASON
        if isinstance(image_signals.get("_provider_blocked"), dict):
            return {"result": None, "metadata": {"validation_status": "budget_blocked", "response_provider": "openai", "error": "AI dispatch withheld: " + str(image_signals["_provider_blocked"].get("status"))}}
        if not settings.openai_api_key:
            return None
        signature_payload = {k: image_signals.get(k) for k in ("listing_id", "canonical_item_id", "voice_transcript", "voice_notes", "title_hint", "existing_specifics", "photo_keywords", "marketplace_targets")}
        signature = hashlib.sha256(json.dumps(signature_payload, sort_keys=True, default=str).encode()).hexdigest()
        with _AI_GUARD_LOCK:
            if signature in _AI_SUCCESS_CACHE:
                cached = _AI_SUCCESS_CACHE[signature]
                return {"result": cached["result"], "metadata": {**cached["metadata"], "deduplicated": True}}
            if _AI_CIRCUIT_UNTIL > time.time():
                return {"result": None, "metadata": {"validation_status": "circuit_open", "response_provider": "openai", "error": _AI_CIRCUIT_REASON or "OpenAI provider circuit open"}}
            if db is not None:
                durable_circuit = provider_circuit_open(db)
                if durable_circuit:
                    return {"result": None, "metadata": {"validation_status": "circuit_open", "response_provider": "openai", "error": durable_circuit.get("reason") or "OpenAI provider circuit open", "retry_after": durable_circuit.get("retry_after")}}
            if signature in _AI_ATTEMPTED:
                return {"result": None, "metadata": {"validation_status": "duplicate_suppressed", "response_provider": "openai", "deduplicated": True, "error": "Identical AI input signature already attempted"}}
            _AI_ATTEMPTED.add(signature)

        prompt = get_prompt_template("generate_listing_intelligence")
        schema = self._listing_intelligence_schema()
        request_timestamp = datetime.now(tz=UTC).isoformat()
        raw_request_preview = _normalize_text(str(image_signals.get("voice_transcript") or image_signals.get("title_hint") or ""))[:240]
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": f"Item signals JSON: {json.dumps(image_signals, ensure_ascii=False)}",
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "posterpro_listing_intelligence_v1",
                    "schema": schema,
                    "strict": True,
                }
            },
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post("https://api.openai.com/v1/responses", json=payload, headers=headers)
                if response.is_error:
                    error_text = "OpenAI request failed: HTTP " + str(response.status_code) + " " + response.text[:240]
                    if response.status_code == 429 and any(token in response.text.lower() for token in ("insufficient_quota", "quota", "billing")):
                        with _AI_GUARD_LOCK:
                            _AI_CIRCUIT_UNTIL = time.time() + 900
                            _AI_CIRCUIT_REASON = error_text
                        if db is not None:
                            try:
                                open_provider_circuit(db, reason=error_text)
                            except Exception as exc:
                                logger.warning("ai_provider_circuit_persist_failed error=%s", type(exc).__name__)
                    return {"result": None, "metadata": {"request_timestamp": request_timestamp, "latency_ms": int((time.perf_counter() - started) * 1000), "validation_status": "provider_error", "response_provider": "openai", "error": error_text}}
                body = response.json()
                parsed = self._parse_responses_json(body)
                if not isinstance(parsed, dict):
                    return None
                result = {
                    "result": self._normalize_llm_result(parsed, image_signals),
                    "metadata": {
                        "request_id": body.get("id"),
                        "request_timestamp": request_timestamp,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "validation_status": "validated",
                        "validation_errors": [],
                        "response_provider": "openai",
                        "raw_request_preview": raw_request_preview,
                        "service_tier": body.get("service_tier"),
                    },
                    "usage": body.get("usage") or {},
                }
                with _AI_GUARD_LOCK:
                    _AI_SUCCESS_CACHE[signature] = result
                if db is not None:
                    try:
                        close_provider_circuit(db)
                    except Exception as exc:
                        logger.warning("ai_provider_circuit_close_failed error=%s", type(exc).__name__)
                return result
        except Exception as exc:  # noqa: BLE001
            return {"result": None, "metadata": {"request_timestamp": request_timestamp, "latency_ms": int((time.perf_counter() - started) * 1000), "validation_status": "provider_error", "response_provider": "openai", "error": f"OpenAI request failed: {type(exc).__name__}: {str(exc)[:240]}"}}

    @staticmethod
    def _parse_responses_json(body: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(body, dict):
            return None
        output_text = body.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            try:
                parsed = json.loads(output_text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:  # noqa: BLE001
                return None
        outputs = body.get("output")
        if isinstance(outputs, list):
            for item in outputs:
                if not isinstance(item, dict):
                    continue
                contents = item.get("content")
                if not isinstance(contents, list):
                    continue
                for content in contents:
                    if not isinstance(content, dict):
                        continue
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        try:
                            parsed = json.loads(text)
                            if isinstance(parsed, dict):
                                return parsed
                        except Exception:  # noqa: BLE001
                            continue
        return None

    @staticmethod
    def _listing_intelligence_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "identity",
                "inventory",
                "condition",
                "research",
                "canonical_listing",
                "pricing",
                "marketplace_targets",
                "marketplace_drafts",
                "evidence",
                "quality",
            ],
            "properties": {
                "schema_version": {"type": "string"},
                "identity": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["product_name", "brand", "model", "mpn", "part_number", "upc", "product_type", "function", "application", "confidence"],
                    "properties": {
                        "product_name": {"type": ["string", "null"]},
                        "brand": {"type": ["string", "null"]},
                        "model": {"type": ["string", "null"]},
                        "mpn": {"type": ["string", "null"]},
                        "part_number": {"type": ["string", "null"]},
                        "upc": {"type": ["string", "null"]},
                        "product_type": {"type": ["string", "null"]},
                        "function": {"type": ["string", "null"]},
                        "application": {"type": ["string", "null"]},
                        "confidence": {"type": "number"},
                    },
                },
                "inventory": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["quantity_on_hand", "pack_size", "units_per_sale", "listing_quantity", "bundle_strategy"],
                    "properties": {
                        "quantity_on_hand": {"type": ["integer", "number", "null"]},
                        "pack_size": {"type": ["integer", "number", "null"]},
                        "units_per_sale": {"type": ["integer", "number", "null"]},
                        "listing_quantity": {"type": ["integer", "number", "null"]},
                        "bundle_strategy": {"type": ["string", "null"]},
                    },
                },
                "condition": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["canonical_condition", "condition_notes", "defects", "included_items", "missing_items"],
                    "properties": {
                        "canonical_condition": {"type": ["string", "null"]},
                        "condition_notes": {"type": ["string", "null"]},
                        "defects": {"type": "array", "items": {"type": "string"}},
                        "included_items": {"type": "array", "items": {"type": "string"}},
                        "missing_items": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "research": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["photo_research_required", "identifiers_to_verify", "research_instructions", "pricing_instructions"],
                    "properties": {
                        "photo_research_required": {"type": "boolean"},
                        "identifiers_to_verify": {"type": "array", "items": {"type": "string"}},
                        "research_instructions": {"type": "array", "items": {"type": "string"}},
                        "pricing_instructions": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "canonical_listing": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["human_readable_name", "master_title", "master_description", "keywords", "features", "specifications", "category_candidates"],
                    "properties": {
                        "human_readable_name": {"type": ["string", "null"]},
                        "master_title": {"type": ["string", "null"]},
                        "master_description": {"type": ["string", "null"]},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "features": {"type": "array", "items": {"type": "string"}},
                        "specifications": {"type": "object", "additionalProperties": False, "properties": {"Brand": {"type": ["string", "null"]}, "Model": {"type": ["string", "null"]}, "MPN": {"type": ["string", "null"]}, "UPC": {"type": ["string", "null"]}, "Type": {"type": ["string", "null"]}}, "required": ["Brand", "Model", "MPN", "UPC", "Type"]},
                        "category_candidates": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "pricing": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["strategy", "price_hint", "minimum_price", "comparison_instruction"],
                    "properties": {
                        "strategy": {"type": ["string", "null"]},
                        "price_hint": {"type": ["number", "null"]},
                        "minimum_price": {"type": ["number", "null"]},
                        "comparison_instruction": {"type": ["string", "null"]},
                    },
                },
                "marketplace_targets": {"type": "array", "items": {"type": "string"}},
                "marketplace_drafts": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
                "evidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["facts_from_user", "facts_inferred", "facts_needing_verification", "contradictions"],
                    "properties": {
                        "facts_from_user": {"type": "array", "items": {"type": "string"}},
                        "facts_inferred": {"type": "array", "items": {"type": "string"}},
                        "facts_needing_verification": {"type": "array", "items": {"type": "string"}},
                        "contradictions": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "quality": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["overall_confidence", "ready_for_photo_enrichment", "ready_for_draft", "blocking_questions"],
                    "properties": {
                        "overall_confidence": {"type": "number"},
                        "ready_for_photo_enrichment": {"type": "boolean"},
                        "ready_for_draft": {"type": "boolean"},
                        "blocking_questions": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        }

    def _normalize_llm_result(self, parsed: dict[str, Any], image_signals: dict[str, Any]) -> dict[str, Any]:
        identity = parsed.get("identity") if isinstance(parsed.get("identity"), dict) else {}
        inventory = parsed.get("inventory") if isinstance(parsed.get("inventory"), dict) else {}
        condition = parsed.get("condition") if isinstance(parsed.get("condition"), dict) else {}
        research = parsed.get("research") if isinstance(parsed.get("research"), dict) else {}
        canonical_listing = parsed.get("canonical_listing") if isinstance(parsed.get("canonical_listing"), dict) else {}
        pricing = parsed.get("pricing") if isinstance(parsed.get("pricing"), dict) else {}
        marketplace_targets = parsed.get("marketplace_targets") if isinstance(parsed.get("marketplace_targets"), list) else []
        marketplace_drafts = parsed.get("marketplace_drafts") if isinstance(parsed.get("marketplace_drafts"), dict) else {}
        evidence = parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {}
        quality = parsed.get("quality") if isinstance(parsed.get("quality"), dict) else {}
        specifications = canonical_listing.get("specifications") if isinstance(canonical_listing.get("specifications"), dict) else {}
        item_specifics = {
            "Brand": identity.get("brand") or specifications.get("Brand"),
            "Model": identity.get("model") or specifications.get("Model"),
            "MPN": identity.get("mpn") or identity.get("part_number"),
            "UPC": identity.get("upc"),
            "Type": identity.get("product_type") or canonical_listing.get("human_readable_name"),
        }
        item_specifics = {key: value for key, value in item_specifics.items() if not _is_placeholder_text(value)}
        title = _normalize_text(canonical_listing.get("master_title") or identity.get("product_name") or image_signals.get("title_hint") or "Marketplace listing")
        description = _normalize_text(canonical_listing.get("master_description") or "")
        category_candidates = canonical_listing.get("category_candidates") if isinstance(canonical_listing.get("category_candidates"), list) else []
        category = category_candidates[0] if category_candidates else suggest_category_from_text(title, str(image_signals.get("source_type") or ""), " ".join(image_signals.get("photo_keywords") or []))[0]
        return {
            "schema_version": parsed.get("schema_version") or "posterpro_listing_intelligence_v1",
            "title": title[:80] or "Marketplace listing",
            "description": description or build_listing_description(
                title=title or "Marketplace listing",
                item_specifics=item_specifics,
                source_label=str(image_signals.get("source_type") or "").replace("_", " ") or None,
                source_metadata=image_signals.get("source_metadata") if isinstance(image_signals.get("source_metadata"), dict) else None,
            ),
            "category_suggestion": category,
            "condition": _normalize_text(condition.get("canonical_condition") or image_signals.get("existing_condition") or "Needs review"),
            "item_specifics": item_specifics or {"Brand": "Needs review", "Model": "Needs review", "Type": "Needs review"},
            "tags": [str(keyword).strip() for keyword in (canonical_listing.get("keywords") or []) if str(keyword).strip()],
            "estimated_value": self._safe_float(pricing.get("price_hint"), 24.0),
            "missing_information": [str(item).strip() for item in (research.get("facts_needing_verification") or []) if str(item).strip()],
            "photo_notes": [str(item).strip() for item in (condition.get("condition_notes") and [condition.get("condition_notes")] or []) if str(item).strip()],
            "research_queries": [str(item).strip() for item in (research.get("research_instructions") or []) if str(item).strip()],
            "draft_quality": "strong" if bool(quality.get("ready_for_draft")) else "partial",
            "marketplace_targets": [str(item).strip() for item in marketplace_targets if str(item).strip()],
            "marketplace_drafts": marketplace_drafts,
            "identity": identity,
            "inventory": inventory,
            "condition_details": condition,
            "research": research,
            "canonical_listing": canonical_listing,
            "pricing": pricing,
            "evidence": evidence,
            "quality": quality,
        }

    def _build_structured_listing_json(self, generated: dict[str, Any], image_signals: dict[str, Any]) -> dict[str, Any]:
        item_specifics = generated.get("item_specifics") if isinstance(generated.get("item_specifics"), dict) else {}
        title = str(generated.get("title") or "").strip()
        description = str(generated.get("description") or "").strip()
        category = str(generated.get("category_suggestion") or "").strip()
        condition = str(generated.get("condition") or "").strip()
        marketplace_targets = generated.get("marketplace_targets") if isinstance(generated.get("marketplace_targets"), list) else []
        return {
            "schema_version": generated.get("schema_version") or "posterpro_listing_intelligence_v1",
            "identity": {
                "product_name": title or None,
                "brand": item_specifics.get("Brand") or None,
                "model": item_specifics.get("Model") or None,
                "mpn": item_specifics.get("MPN") or None,
                "part_number": item_specifics.get("MPN") or None,
                "upc": item_specifics.get("UPC") or None,
                "product_type": item_specifics.get("Type") or None,
                "function": generated.get("identity", {}).get("function") if isinstance(generated.get("identity"), dict) else None,
                "application": generated.get("identity", {}).get("application") if isinstance(generated.get("identity"), dict) else None,
                "confidence": float(generated.get("confidence") or generated.get("quality", {}).get("overall_confidence") or 0.0),
            },
            "inventory": {
                "quantity_on_hand": self._safe_int(image_signals.get("quantity") or generated.get("quantity") or item_specifics.get("Quantity")),
                "pack_size": self._safe_int(item_specifics.get("Pack Size")),
                "units_per_sale": self._safe_int(image_signals.get("units_per_sale") or generated.get("inventory", {}).get("units_per_sale") if isinstance(generated.get("inventory"), dict) else None) or 1,
                "listing_quantity": self._safe_int(
                    image_signals.get("listing_quantity")
                    or (generated.get("inventory", {}).get("listing_quantity") if isinstance(generated.get("inventory"), dict) else None)
                    or generated.get("quantity")
                    or item_specifics.get("Quantity")
                    or (generated.get("inventory", {}).get("quantity_on_hand") if isinstance(generated.get("inventory"), dict) else None)
                ) or 1,
                "bundle_strategy": str(generated.get("inventory", {}).get("bundle_strategy") if isinstance(generated.get("inventory"), dict) and generated.get("inventory", {}).get("bundle_strategy") else ("individual" if self._safe_int(image_signals.get("quantity") or generated.get("quantity")) and self._safe_int(image_signals.get("quantity") or generated.get("quantity")) > 1 else "single")).strip(),
            },
            "condition": {
                "canonical_condition": condition or None,
                "condition_notes": generated.get("condition_details", {}).get("condition_notes") if isinstance(generated.get("condition_details"), dict) else None,
                "defects": generated.get("condition_details", {}).get("defects") if isinstance(generated.get("condition_details"), dict) and isinstance(generated.get("condition_details", {}).get("defects"), list) else [],
                "included_items": generated.get("condition_details", {}).get("included_items") if isinstance(generated.get("condition_details"), dict) and isinstance(generated.get("condition_details", {}).get("included_items"), list) else [],
                "missing_items": generated.get("condition_details", {}).get("missing_items") if isinstance(generated.get("condition_details"), dict) and isinstance(generated.get("condition_details", {}).get("missing_items"), list) else [],
            },
            "research": {
                "photo_research_required": bool((generated.get("research") or {}).get("photo_research_required", True)) if isinstance(generated.get("research"), dict) else True,
                "identifiers_to_verify": self._normalize_string_list((generated.get("research") or {}).get("identifiers_to_verify"), []) if isinstance(generated.get("research"), dict) else [],
                "research_instructions": self._normalize_string_list((generated.get("research") or {}).get("research_instructions"), generated.get("research_queries") or []) if isinstance(generated.get("research"), dict) else (generated.get("research_queries") or []),
                "pricing_instructions": self._normalize_string_list((generated.get("research") or {}).get("pricing_instructions"), [str(generated.get("pricing", {}).get("comparison_instruction") or "Research sold comps before pricing.")]) if isinstance(generated.get("research"), dict) else [str(generated.get("pricing", {}).get("comparison_instruction") or "Research sold comps before pricing.")],
            },
            "canonical_listing": {
                "human_readable_name": title or None,
                "master_title": title or None,
                "master_description": description or None,
                "keywords": self._normalize_string_list(generated.get("tags"), []),
                "features": self._normalize_string_list((generated.get("canonical_listing") or {}).get("features"), []) if isinstance(generated.get("canonical_listing"), dict) else [],
                "specifications": item_specifics,
                "category_candidates": [category] if category else [],
            },
            "pricing": {
                "strategy": str((generated.get("pricing") or {}).get("strategy") or "compare_sold_comps") if isinstance(generated.get("pricing"), dict) else "compare_sold_comps",
                "price_hint": generated.get("estimated_value"),
                "minimum_price": (generated.get("pricing") or {}).get("minimum_price") if isinstance(generated.get("pricing"), dict) else None,
                "comparison_instruction": str((generated.get("pricing") or {}).get("comparison_instruction") or "Research sold comps before pricing.") if isinstance(generated.get("pricing"), dict) else "Research sold comps before pricing.",
            },
            "marketplace_targets": [str(item).strip() for item in marketplace_targets if str(item).strip()],
            "marketplace_drafts": generated.get("marketplace_drafts") if isinstance(generated.get("marketplace_drafts"), dict) else self._build_marketplace_drafts(generated),
            "evidence": {
                "facts_from_user": self._normalize_string_list((generated.get("evidence") or {}).get("facts_from_user"), self._extract_user_facts(image_signals)),
                "facts_inferred": self._normalize_string_list((generated.get("evidence") or {}).get("facts_inferred"), []),
                "facts_needing_verification": self._normalize_string_list((generated.get("evidence") or {}).get("facts_needing_verification"), generated.get("missing_information") or []),
                "contradictions": self._normalize_string_list((generated.get("evidence") or {}).get("contradictions"), []),
            },
            "quality": {
                "overall_confidence": float((generated.get("quality") or {}).get("overall_confidence") or generated.get("confidence") or 0.0) if isinstance(generated.get("quality"), dict) else float(generated.get("confidence") or 0.0),
                "ready_for_photo_enrichment": bool((generated.get("quality") or {}).get("ready_for_photo_enrichment", True)) if isinstance(generated.get("quality"), dict) else True,
                "ready_for_draft": bool((generated.get("quality") or {}).get("ready_for_draft")) if isinstance(generated.get("quality"), dict) else bool(title and description),
                "blocking_questions": self._normalize_string_list((generated.get("quality") or {}).get("blocking_questions"), generated.get("missing_information") or []) if isinstance(generated.get("quality"), dict) else (generated.get("missing_information") or []),
            },
        }

    @staticmethod
    def _marketplace_rules() -> dict[str, dict[str, Any]]:
        return {name: dict(rule) for name, rule in _MARKETPLACE_RULES.items()}

    def _build_marketplace_drafts(self, generated: dict[str, Any]) -> dict[str, Any]:
        title = str(generated.get("title") or "Marketplace listing").strip()
        description = str(generated.get("description") or "").strip()
        item_specifics = generated.get("item_specifics") if isinstance(generated.get("item_specifics"), dict) else {}
        condition = str(generated.get("condition") or "Needs review").strip()
        category = str(generated.get("category_suggestion") or "").strip()
        sources = {
            "title": title,
            "description": description,
            "item_specifics": item_specifics,
            "condition": condition,
            "category": category,
        }
        return {
            "ebay": {
                "title": self._marketplace_title(title, item_specifics, category, max_length=_MARKETPLACE_RULES["ebay"]["title_max"]),
                "description": build_listing_description(title=title, item_specifics=item_specifics, condition_notes=condition, source_label="eBay", source_metadata=None),
                "sources": sources,
            },
            "facebook": {
                "title": self._marketplace_title(title, item_specifics, category, max_length=_MARKETPLACE_RULES["facebook"]["title_max"]),
                "description": build_listing_description(title=title, item_specifics=item_specifics, condition_notes=condition, source_label="Facebook", source_metadata=None),
                "sources": sources,
            },
            "mercari": {
                "title": self._marketplace_title(title, item_specifics, category, max_length=_MARKETPLACE_RULES["mercari"]["title_max"]),
                "description": self._mercari_description(title, item_specifics, condition, category),
                "sources": sources,
            },
            "poshmark": {
                "title": self._marketplace_title(title, item_specifics, category, max_length=_MARKETPLACE_RULES["poshmark"]["title_max"]),
                "description": build_listing_description(title=title, item_specifics=item_specifics, condition_notes=condition, source_label="Poshmark", source_metadata=None),
                "sources": sources,
            },
            "vinted": {
                "title": self._marketplace_title(title, item_specifics, category, max_length=_MARKETPLACE_RULES["vinted"]["title_max"]),
                "description": self._vinted_description(title, item_specifics, condition, category),
                "sources": sources,
            },
        }

    @staticmethod
    def _marketplace_title(title: str, item_specifics: dict[str, Any], category: str, *, max_length: int) -> str:
        pieces = [title]
        for field in ("Brand", "Model", "MPN", "Type"):
            value = item_specifics.get(field)
            if value:
                text = _normalize_text(value)
                if text and text.lower() not in {piece.lower() for piece in pieces}:
                    pieces.append(text)
        if category and category not in pieces:
            leaf = category.split(">")[-1].strip()
            if leaf:
                pieces.append(leaf)
        output = " ".join(piece for piece in pieces if piece)
        return " ".join(output.split())[:max_length].strip() or "Marketplace listing"

    @staticmethod
    def _mercari_description(title: str, item_specifics: dict[str, Any], condition: str, category: str) -> str:
        details = []
        for field in ("Brand", "Model", "MPN", "UPC", "Type"):
            value = item_specifics.get(field)
            if value:
                details.append(f"{field}: {_normalize_text(value)}")
        parts = [title, f"Condition: {condition or 'Needs review'}"]
        if category:
            parts.append(f"Category: {category}")
        if details:
            parts.append(" ".join(details[:5]))
        text = " | ".join(parts)
        return text[:1000].strip()

    @staticmethod
    def _vinted_description(title: str, item_specifics: dict[str, Any], condition: str, category: str) -> str:
        parts = [title, f"Condition: {condition or 'Needs review'}"]
        brand = item_specifics.get("Brand")
        model = item_specifics.get("Model")
        if brand:
            parts.append(f"Brand: {_normalize_text(brand)}")
        if model:
            parts.append(f"Model: {_normalize_text(model)}")
        if category:
            parts.append(f"Category: {category}")
        return " | ".join(parts)[:1000].strip()

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            parsed = int(float(str(value).strip()))
            return parsed if parsed > 0 else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _extract_user_facts(image_signals: dict[str, Any]) -> list[str]:
        facts: list[str] = []
        transcript = _normalize_text(image_signals.get("voice_transcript"))
        notes = _normalize_text(image_signals.get("voice_notes"))
        for value in (transcript, notes):
            if value:
                facts.append(value)
        session = image_signals.get("source_metadata", {}).get("session") if isinstance(image_signals.get("source_metadata"), dict) else {}
        if isinstance(session, dict):
            location = _normalize_text(session.get("default_location"))
            if location:
                facts.append(f"Session location: {location}")
        return facts

    def _fallback_generation(self, image_signals: dict[str, Any]) -> dict[str, Any]:
        transcript = image_signals.get("voice_transcript") or image_signals.get("voice_notes") or ""
        explicit = extract_explicit_transcript_fields(transcript)
        title_hint = str(explicit.get("product_name") or image_signals.get("title_hint") or "").strip()
        source_type = str(image_signals.get("source_type") or "").strip()
        image_count = int(image_signals.get("image_count") or 0)
        source_metadata = image_signals.get("source_metadata") if isinstance(image_signals.get("source_metadata"), dict) else {}
        category, _ = suggest_category_from_text(title_hint, source_type, " ".join(image_signals.get("photo_keywords") or []))
        existing_specifics = image_signals.get("existing_specifics") if isinstance(image_signals.get("existing_specifics"), dict) else {}
        item_specifics = dict(existing_specifics)
        recovery_identity = _best_recovery_identity(source_metadata)
        if recovery_identity:
            for field, target in (
                ("brand", "Brand"),
                ("model", "Model"),
                ("mpn", "MPN"),
                ("identifier", "MPN"),
                ("product_name", "Product Type"),
                ("product_type", "Type"),
                ("packaging_identity", "Product Type"),
            ):
                value = recovery_identity.get(field)
                if not _is_placeholder_text(value):
                    item_specifics[target] = _normalize_text(value)
        if image_signals.get("barcode_candidates") and not item_specifics.get("UPC"):
            item_specifics["UPC"] = str(image_signals["barcode_candidates"][0])
        if title_hint and not item_specifics.get("Type"):
            item_specifics["Type"] = title_hint
        if explicit.get("brand"): item_specifics["Brand"] = explicit["brand"]
        if explicit.get("model"): item_specifics["Model"] = explicit["model"]
        if explicit.get("catalog_number"): item_specifics["Catalog Number"] = explicit["catalog_number"]
        title = build_marketplace_title(title=title_hint, item_specifics=item_specifics, category_hint=category, source_metadata=source_metadata)
        photo_notes = []
        missing_information = [
            "Confirm exact brand or maker.",
            "Confirm dimensions or measurements.",
            "Confirm defects, wear, and completeness.",
        ]
        if image_count <= 1:
            photo_notes.append("Only one photo is attached, so condition and completeness are still uncertain.")
        if source_type:
            photo_notes.append(f"Draft was generated from {source_type.replace('_', ' ')} source signals.")
        return {
            "title": title[:80],
            "description": build_listing_description(
                title=title,
                item_specifics=item_specifics,
                condition_notes="; ".join(photo_notes[:2]) if photo_notes else None,
                photo_notes=photo_notes,
                source_label=source_type.replace("_", " ") if source_type else None,
                source_metadata=source_metadata,
            ),
            "category_suggestion": category,
            "condition": explicit.get("condition") or "Needs review",
            "item_specifics": item_specifics or {
                "Brand": "Needs review",
                "Model": "Needs review",
                "Type": "Needs review",
            },
            "tags": ["resale", "preowned", "review-required"],
            "quantity": explicit.get("quantity") or image_signals.get("quantity") or 1,
            "inventory": {"quantity_on_hand": explicit.get("quantity") or image_signals.get("quantity") or 1, "units_per_sale": explicit.get("units_per_sale") or 1, "listing_quantity": explicit.get("quantity") or image_signals.get("quantity") or 1, "bundle_strategy": explicit.get("bundle_strategy") or "single"},
            "explicit_transcript_fields": explicit,
            "estimated_value": 24.0,
            "missing_information": missing_information,
            "photo_notes": photo_notes or ["Photo review is still required before publish."],
            "research_queries": [title[:60], f"{title[:48]} sold", f"{category} sold comps"],
        }

    @staticmethod
    def _safe_float(value: Any, fallback: float) -> float:
        try:
            parsed = float(value)
            return round(parsed, 2) if parsed > 0 else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _normalize_string_list(value: Any, fallback: list[str]) -> list[str]:
        if not isinstance(value, list):
            return fallback
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized or fallback

    @staticmethod
    def _draft_quality(generated: dict[str, Any]) -> str:
        score = 0
        if generated.get("title"):
            score += 1
        if generated.get("description"):
            score += 1
        if generated.get("item_specifics"):
            score += 1
        missing = generated.get("missing_information") or []
        if len(missing) <= 1:
            score += 1
        if score >= 4:
            return "strong"
        if score >= 2:
            return "partial"
        return "weak"

    @staticmethod
    def _sanitize_claims(text: str, image_signals: dict[str, Any]) -> str:
        supported = json.dumps(image_signals).lower()
        output = text
        if "warranty" not in supported:
            output = output.replace("warranty", "coverage")
        if "authentic" not in supported:
            output = output.replace("Authentic", "").replace("authentic", "")
        if "oem" not in supported:
            output = output.replace("OEM", "").replace("oem", "")
        if "compatible" not in supported and "fitment" not in supported:
            output = output.replace("compatible with", "review compatibility for")
        return " ".join(output.split()).strip()
