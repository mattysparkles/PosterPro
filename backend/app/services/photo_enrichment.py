from __future__ import annotations

import base64
import json
import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.services.ai_entitlements import resolve_openai_key
from app.services.ai_guard import (
    AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE,
    allow as ai_allow,
    mark_completed as ai_mark_completed,
    signature as ai_signature,
    open_circuit as ai_open_circuit,
    provider_circuit_open,
    open_provider_circuit,
    classify_openai_error,
    format_openai_error,
    provider_circuit_seconds,
    reserve_durable,
    reconcile_durable,
    model_pool,
)
from app.services.listing_specificity import assess_listing_specificity
from app.prompts.templates import get_prompt_template

logger = logging.getLogger(__name__)

PHOTO_EVIDENCE_PIPELINE_VERSION = "recovery_photo_evidence_v2"
PHOTO_GROUP_EVIDENCE_PIPELINE_VERSION = "recovery_photo_group_evidence_v1"
# The October 2026 incident proved that 3k/photo is not a safe admission
# estimate. One bounded group call reserves a conservative historical maximum
# plus margin; no group may fan out into per-photo provider calls by default.
MAX_PAID_CALLS_PER_GROUP = 1
PHOTO_GROUP_RESERVATION_TOKENS = 50_000
# V3 preserves the prior V2 evidence records while recording the corrected
# field-by-field synthesis separately for audit and before/after comparison.
FULL_GROUP_EVIDENCE_PIPELINE_VERSION = "recovery_full_group_evidence_v3"
_IDENTIFIER_RE = re.compile(r"(?<!\d)(\d{12,14}|\d{9}[\dXx])(?!\d)")
_MODEL_RE = re.compile(r"\b(?:MODEL|MFG\s*PART|MPN|PART\s*(?:NO|NUMBER))\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,})", re.I)
_GENERIC_TITLE_PHRASES = {
    "packaging",
    "product information",
    "electronic components",
    "miscellaneous items",
    "shipping boxes",
    "art and decor",
    "box",
    "package",
    "packaged item",
    "miscellaneous",
}


def classify_barcode(value: str | None) -> str | None:
    value = re.sub(r"\D", "", str(value or ""))
    if len(value) == 12:
        return "UPC"
    if len(value) == 13:
        return "EAN"
    if len(value) == 14:
        return "GTIN"
    return None


def quality_gate(synthesis: dict[str, Any]) -> str:
    """Return the review state without pretending placeholder facts are complete."""
    if synthesis.get("group_kind") == "multiple_unrelated_products":
        return "needs_grouping_review"
    if not synthesis.get("usable_media_ids"):
        return "needs_identity_review"
    if not synthesis.get("identity") or synthesis.get("identity_confidence", 0) < 0.45:
        return "needs_identity_review"
    specificity = assess_listing_specificity(
        title=(synthesis.get("identity") or {}).get("title"),
        description=synthesis.get("description"),
        category=synthesis.get("category"),
        item_specifics=synthesis.get("item_specifics") if isinstance(synthesis.get("item_specifics"), dict) else {},
        source_metadata=synthesis,
        has_images=bool(synthesis.get("usable_media_ids")),
    )
    synthesis["specificity"] = specificity
    if specificity["status"] != "trusted_for_draft":
        return specificity["status"]
    placeholders = set(synthesis.get("placeholders") or [])
    if {"price", "weight", "dimensions"} & placeholders:
        return "blocked_placeholder_data"
    if "measurements" in (synthesis.get("review_flags") or []):
        return "needs_measurement_review"
    return "trusted_for_draft"


class PhotoEnrichmentService:
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model

    def enrich_photo(self, photo_path: str, *, db: Any = None, user_id: int | None = None) -> dict[str, Any]:
        guard_key = ai_signature(purpose="photo", payload={"path": photo_path, "model": self.model})
        if not ai_allow(guard_key):
            return {"status": "skipped", "reason": "duplicate_or_provider_circuit_open", "photo_path": photo_path}
        api_key, _mode = resolve_openai_key(db, user_id)
        if not api_key:
            raise RuntimeError("Connect an OpenAI account or enable an eligible PosterPro AI plan")

        image_b64 = base64.b64encode(Path(photo_path).read_bytes()).decode("utf-8")
        outputs = {
            "title": self._extract_json(photo_path, image_b64, "extract_poster_title", api_key=api_key, db=db).get("title"),
            "description": self._extract_json(photo_path, image_b64, "extract_description", api_key=api_key, db=db).get("description"),
        }
        category = self._extract_json(photo_path, image_b64, "detect_category", api_key=api_key, db=db)
        keywords = self._extract_json(photo_path, image_b64, "extract_keywords", api_key=api_key, db=db)

        outputs["category_id"] = category.get("category_id")
        outputs["category_suggestion"] = category.get("category_name")
        outputs["tags"] = keywords.get("keywords") or []
        outputs["item_specifics"] = keywords.get("item_specifics") or {}
        outputs["estimated_value"] = _safe_float(keywords.get("estimated_value"))
        ai_mark_completed(guard_key)
        return outputs

    def enrich_group(self, photo_paths: list[str], *, db: Any = None, user_id: int | None = None, lineage: dict[str, Any] | None = None, workload: str | None = None, work_item_id: str | None = None) -> dict[str, Any]:
        """Gather deterministic evidence per photo, then make at most one paid group call.

        This deliberately prevents the historic one-vision-request-per-image
        fan-out.  Local OCR/barcode/quality signals are retained for every
        image; AI sees a compact contact sheet only when those signals do not
        already establish a usable group identity.
        """
        guard_key = ai_signature(purpose="photo_group", payload={"paths": sorted(photo_paths), "model": self.model})
        # The durable reservation gate owns scoped timeline admission.  The
        # legacy in-memory guard remains authoritative for every other caller.
        if workload != AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE and not ai_allow(guard_key):
            return {"status": "skipped", "reason": "duplicate_or_provider_circuit_open", "photos_evaluated": 0, "group_synthesis": {}}
        lineage = dict(lineage or {})
        records = [self.extract_photo_evidence(path, media_id=index + 1, db=db, user_id=user_id, allow_vision=False) for index, path in enumerate(photo_paths)]
        synthesis = self.synthesize_group_evidence(records)
        group_calls = 0
        if db is not None and not self._group_has_deterministic_identity(synthesis):
            group_ai = self._group_evidence_vision(photo_paths, db=db, user_id=user_id, lineage=lineage, workload=workload, work_item_id=work_item_id)
            group_calls = int(bool(group_ai))
            if group_ai:
                identity = group_ai.get("identity") if isinstance(group_ai.get("identity"), dict) else {}
                if identity.get("product_name") and not identity.get("title"):
                    identity["title"] = " ".join(str(identity.get(key) or "").strip() for key in ("brand", "product_name", "model") if str(identity.get(key) or "").strip())
                synthesis["identity"] = {**dict(synthesis.get("identity") or {}), **identity}
                for field in ("description", "category", "item_specifics", "estimated_value", "condition", "confidence"):
                    if group_ai.get(field) not in (None, "", {}, []):
                        synthesis[field] = group_ai[field]
                synthesis["group_ai_evidence"] = group_ai
                synthesis["pipeline_version"] = PHOTO_GROUP_EVIDENCE_PIPELINE_VERSION
        result = {
            "title": synthesis.get("identity", {}).get("title"),
            "description": synthesis.get("description"),
            "category_suggestion": synthesis.get("category"),
            "item_specifics": synthesis.get("item_specifics") or {},
            "tags": synthesis.get("tags") or [],
            "estimated_value": synthesis.get("estimated_value"),
            "photo_evidence": records,
            "fact_sources": synthesis.get("supporting_media_ids") or {},
            "photos_evaluated": len(records),
            "photos_excluded": [item for item in records if item.get("error_status") or item.get("excluded_reason")],
            "group_synthesis": synthesis,
            "group_ai_calls": group_calls,
        }
        ai_mark_completed(guard_key)
        return result

    def extract_photo_evidence(self, photo_path: str, *, media_id: int | None = None, metadata: dict[str, Any] | None = None, db: Any = None, user_id: int | None = None, allow_vision: bool = True) -> dict[str, Any]:
        """Photo-local OCR/barcode/vision facts. Errors are durable evidence too."""
        result: dict[str, Any] = {"media_id": media_id, "photo_path": photo_path, "photo_role": "alternate_product_view",
            "barcode_attempts": [], "specifications": {}, "included_components": [], "damage": [], "confidence": 0.0,
            "extraction_method": "deterministic_ocr_barcode"}
        if not Path(photo_path).is_file():
            result.update(error_status="missing_file", excluded_reason="missing_file")
            return result
        try:
            import cv2
            image = cv2.imread(photo_path)
        except Exception:
            image = None
        if image is None:
            result.update(error_status="unreadable", excluded_reason="unreadable_image", photo_role="unusable")
            return result
        barcode_attempts, values = self._barcode_attempts(image)
        result["barcode_attempts"] = barcode_attempts
        if values:
            value = values[0]
            barcode_type = classify_barcode(value)
            result.update(decoded_barcode_value=value, decoded_barcode_type=barcode_type, photo_role="barcode", confidence=0.98)
            if barcode_type:
                result[barcode_type.lower()] = value
        text = self._targeted_ocr(image)
        result["ocr_text"] = text
        identifiers = [match.group(1) for match in _IDENTIFIER_RE.finditer(text)]
        if not result.get("decoded_barcode_value") and identifiers:
            value, barcode_type = identifiers[0], classify_barcode(identifiers[0])
            result.update(decoded_barcode_value=value, decoded_barcode_type=barcode_type, confidence=0.82, photo_role="barcode")
            if barcode_type:
                result[barcode_type.lower()] = value
        model = next((match.group(1).upper() for match in _MODEL_RE.finditer(text)), None)
        if model:
            result.update(model=model, mpn=model, photo_role="model_label", confidence=max(result["confidence"], 0.9))
        # Vision adds visual/packaging context but never outranks local identifier evidence.
        api_key, _mode = resolve_openai_key(db, user_id)
        durable_circuit = provider_circuit_open(db) if db is not None else None
        if allow_vision and api_key and not durable_circuit:
            try:
                result.update(self._photo_evidence_vision(photo_path, api_key=api_key, db=db))
                result["extraction_method"] = "deterministic_ocr_barcode+vision"
            except Exception as exc:
                result["vision_error"] = type(exc).__name__
        elif durable_circuit:
            result["vision_error"] = "provider_circuit_open"
            result["vision_retry_after"] = durable_circuit.get("retry_after")
        result["confidence"] = self._confidence_value(result.get("confidence"), default=0.45)
        return result

    @staticmethod
    def _group_has_deterministic_identity(synthesis: dict[str, Any]) -> bool:
        identity = synthesis.get("identity") if isinstance(synthesis.get("identity"), dict) else {}
        return bool(identity.get("upc") or identity.get("ean") or identity.get("gtin") or identity.get("mpn") or identity.get("model"))

    def _group_evidence_vision(self, photo_paths: list[str], *, db: Any, user_id: int | None, lineage: dict[str, Any], workload: str | None = None, work_item_id: str | None = None) -> dict[str, Any]:
        """One bounded contact-sheet request for a coherent photo group."""
        api_key, _mode = resolve_openai_key(db, user_id)
        if not api_key:
            return {}
        usable = [Path(path) for path in photo_paths if Path(path).is_file()][:6]
        if not usable:
            return {}
        try:
            from PIL import Image, ImageOps, ImageDraw
            tiles = []
            for index, path in enumerate(usable):
                with Image.open(path) as raw:
                    image = ImageOps.exif_transpose(raw).convert("RGB")
                    image.thumbnail((480, 480))
                    tile = Image.new("RGB", (500, 520), "white")
                    tile.paste(image, ((500-image.width)//2, 20 + (480-image.height)//2))
                    ImageDraw.Draw(tile).text((8, 4), f"Photo {index + 1}", fill="black")
                    tiles.append(tile)
            sheet = Image.new("RGB", (1000, ((len(tiles)+1)//2)*520), "white")
            for index, tile in enumerate(tiles): sheet.paste(tile, ((index % 2)*500, (index // 2)*520))
            buffer = BytesIO(); sheet.save(buffer, format="JPEG", quality=82)
            image_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return {}
        signature = ai_signature(purpose="photo_group_evidence", payload={"paths": sorted(str(path) for path in usable), "model": self.model, "version": PHOTO_GROUP_EVIDENCE_PIPELINE_VERSION})
        reservation = reserve_durable(
            db, key=signature, pool=model_pool(self.model) or "mini", model=self.model,
            estimated_tokens=PHOTO_GROUP_RESERVATION_TOKENS, user_id=user_id,
            listing_id=lineage.get("listing_id"), purpose="photo_group_evidence",
            lineage={**lineage, "operation": lineage.get("operation") or "newest_intake"},
            workload=workload,
            work_item_id=work_item_id,
        )
        if reservation.get("status") != "RESERVED":
            return {}
        prompt = ("Return strict JSON for this coherent inventory photo group. Identify one saleable item only from visible evidence. "
                  "Return identity {brand,product_name,product_type,model,mpn,color,material}, description, category, item_specifics, "
                  "condition, confidence. For automotive parts, transcribe visible part numbers and explicitly printed vehicle year/make/model/engine "
                  "fitment into item_specifics (including a compatibility field); never infer fitment from shape or brand alone. "
                  "Do not invent identifiers; report group_conflict=true if photos show different products.")
        payload = {"model": self.model, "response_format": {"type":"json_object"}, "messages": [{"role":"system","content":prompt}, {"role":"user","content":[{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_b64}"}}]}], "temperature":0, "max_tokens":1400}
        try:
            with httpx.Client(timeout=90) as client:
                response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers={"Authorization":f"Bearer {api_key}", "Content-Type":"application/json"})
            if response.is_error:
                reconcile_durable(db, int(reservation["reservation_id"]), state="RELEASED", error=f"group evidence HTTP {response.status_code}")
                return {}
            body = response.json(); usage = body.get("usage") or {}
            # Chat Completions reports prompt/completion tokens, while the
            # durable ledger uses input/output names. Normalize at the source
            # so scoped workload accounting retains the provider's full split.
            normalized_usage = {
                "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
                "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
                "total_tokens": usage.get("total_tokens"),
            }
            reconcile_durable(db, int(reservation["reservation_id"]), actual_tokens=int(normalized_usage.get("total_tokens") or 0), usage=normalized_usage, request_id=body.get("id"))
            parsed = json.loads(body["choices"][0]["message"]["content"])
            if not isinstance(parsed, dict):
                return {}
            # Timeline correction needs to durably classify a conflict as
            # ambiguous; legacy enrichment retains its previous conservative
            # behavior of declining conflicted evidence.
            if parsed.get("group_conflict") and workload != AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE:
                return {}
            return parsed
        except Exception as exc:
            reconcile_durable(db, int(reservation["reservation_id"]), state="RELEASED", error=type(exc).__name__)
            return {}

    def synthesize_group_evidence(self, evidence_records: list[dict[str, Any]]) -> dict[str, Any]:
        """Synthesize *all* photo evidence with identifier-first precedence.

        This deliberately never selects a lead image. Every fact is voted on
        independently, retaining its supporting media ids and confidence. A
        barcode/model found late therefore beats an early visual guess.
        """
        usable = [record for record in evidence_records if not record.get("error_status") and not record.get("excluded_reason")]
        media_ids = [record.get("media_id") for record in usable if record.get("media_id") is not None]
        barcode_values = self._distinct_values(usable, "decoded_barcode_value")
        brands = self._distinct_values(usable, "brand")
        models = self._distinct_values(usable, "model", fallback="mpn")
        product_types = self._distinct_values(usable, "product_type")
        intentional_set = bool(usable) and all(bool(record.get("intentional_set")) for record in usable)
        has_product_title = any(str(record.get("product_name") or "").strip() for record in usable)
        has_visual_title = any(str(record.get("visual_title") or "").strip() for record in usable)
        has_packaging_identity = any(str(record.get("packaging_identity") or "").strip() for record in usable)
        complementary_label_context = (
            not intentional_set
            and (has_product_title or has_visual_title)
            and has_packaging_identity
            and len(barcode_values) <= 1
            and len(models) <= 1
        )
        title_candidates = self._title_candidates(usable)
        conflicts: list[str] = []
        review_flags: list[str] = []
        if len(barcode_values) > 1 and not intentional_set:
            conflicts.append("multiple_incompatible_barcodes")
        if len(models) > 1 and not intentional_set:
            conflicts.append("multiple_model_labels")
        # Brand/type disagreement is a strong warning only when there is no
        # shared model/barcode evidence that would explain accessories/views.
        if len(brands) > 1 and not intentional_set and not complementary_label_context and not (len(barcode_values) == 1 or len(models) == 1):
            conflicts.append("multiple_brands")
        if len(product_types) > 1 and not intentional_set and not complementary_label_context and not (len(barcode_values) == 1 or len(models) == 1):
            conflicts.append("multiple_product_types")
        if len(title_candidates) > 1 and not intentional_set and self._title_candidates_conflict(title_candidates):
            review_flags.append("multiple_title_candidates")
        group_kind = "intentional_set" if intentional_set else ("multiple_unrelated_products" if conflicts else "one_item")

        selected_facts = {
            field: self._select_fact(usable, field, fallback=fallback)
            for field, fallback in (
                ("decoded_barcode_value", None), ("brand", None), ("model", "mpn"), ("mpn", "model"),
                ("product_name", None), ("visual_title", None), ("packaging_identity", None),
                ("category", None), ("condition_evidence", None), ("testing_evidence", None),
            )
        }
        identifier_fact = selected_facts["decoded_barcode_value"]
        model_fact = selected_facts["model"]
        name_fact = selected_facts["product_name"]
        visual_fact = selected_facts["visual_title"]
        packaging_fact = selected_facts["packaging_identity"]
        brand_fact = selected_facts["brand"]
        title = self._best_identity_title(
            name_fact=name_fact,
            visual_fact=visual_fact,
            packaging_fact=packaging_fact,
            brand_fact=brand_fact,
            model_fact=model_fact,
            product_types=product_types,
        )
        identifier = identifier_fact["value"] or model_fact["value"] or selected_facts["mpn"]["value"]
        supporting = {field: fact["media_ids"] for field, fact in selected_facts.items()}
        field_confidence = {field: fact["confidence"] for field, fact in selected_facts.items()}
        identity_confidence = max(
            identifier_fact["confidence"],
            model_fact["confidence"],
            name_fact["confidence"],
            visual_fact["confidence"],
            packaging_fact["confidence"],
        )
        specifics = self._merge_specifications(usable)
        placeholders = []
        if not any(record.get("price_research") for record in usable): placeholders.append("price")
        if not any(record.get("measurement_evidence") for record in usable): placeholders.append("dimensions")
        if not any(record.get("shipping_weight") for record in usable): placeholders.append("weight")
        synthesis = {
            "pipeline_version": FULL_GROUP_EVIDENCE_PIPELINE_VERSION, "group_kind": group_kind,
            "identity": {"title": title, "brand": brand_fact["value"], "model": model_fact["value"], "mpn": selected_facts["mpn"]["value"], "identifier": identifier},
            "identity_confidence": round(min(identity_confidence, 1.0), 3), "category": selected_facts["category"]["value"],
            "item_specifics": specifics, "included_parts": self._unique_values(usable, "included_components"),
            "condition": selected_facts["condition_evidence"]["value"], "damage": self._unique_values(usable, "damage"),
            "testing_status": selected_facts["testing_evidence"]["value"], "conflicting_evidence": conflicts,
            "supporting_media_ids": supporting, "usable_media_ids": media_ids, "photo_count": len(evidence_records),
            "field_confidence": field_confidence,
            "reason_selected": "field-by-field evidence synthesis: decoded barcode, readable model/MPN, packaging identity, corroborated facts, then visual evidence",
            "identity_candidates": self._identity_candidates(sorted(usable, key=self._evidence_rank, reverse=True)),
            "placeholders": placeholders, "review_flags": (["measurements"] if "dimensions" in placeholders else []) + review_flags,
        }
        synthesis["quality_gate"] = quality_gate(synthesis)
        return synthesis

    @staticmethod
    def _distinct_values(records: list[dict[str, Any]], field: str, *, fallback: str | None = None) -> list[str]:
        values: list[str] = []
        for record in records:
            value = record.get(field) or (record.get(fallback) if fallback else None)
            text = str(value or "").strip()
            if text and text.lower() not in {entry.lower() for entry in values}:
                values.append(text)
        return values

    def _select_fact(self, records: list[dict[str, Any]], field: str, *, fallback: str | None = None) -> dict[str, Any]:
        candidates: dict[str, dict[str, Any]] = {}
        for record in records:
            value = record.get(field) or (record.get(fallback) if fallback else None)
            if value is None or str(value).strip() == "":
                continue
            key = str(value).strip().casefold()
            rank = self._evidence_rank(record)
            confidence = self._confidence_value(record.get("confidence"), default=0.45)
            if field in {"product_name", "packaging_identity"} and self._is_generic_title(value):
                rank = max(5, int(rank * 0.6))
                confidence = min(confidence, 0.55)
            item = candidates.setdefault(key, {"value": value, "score": 0.0, "media_ids": [], "best_rank": 0, "confidence": 0.0})
            item["score"] += rank * max(confidence, 0.25)
            item["best_rank"] = max(item["best_rank"], rank)
            item["confidence"] = max(item["confidence"], confidence)
            if record.get("media_id") is not None and record["media_id"] not in item["media_ids"]:
                item["media_ids"].append(record["media_id"])
        if not candidates:
            return {"value": None, "media_ids": [], "confidence": 0.0, "score": 0.0}
        winner = max(candidates.values(), key=lambda item: (item["score"], item["best_rank"], len(item["media_ids"])))
        corroboration = min(0.12 * max(0, len(winner["media_ids"]) - 1), 0.24)
        winner["confidence"] = round(min(1.0, winner["confidence"] + corroboration), 3)
        return winner

    @staticmethod
    def _is_generic_title(value: Any) -> bool:
        text = re.sub(r"\s+", " ", str(value or "").strip().lower())
        if not text:
            return True
        if text in _GENERIC_TITLE_PHRASES:
            return True
        if len(text) <= 3:
            return True
        return any(token in text for token in ("packaging", "miscellaneous", "product information", "shipping boxes", "art and decor", "electronic components"))

    def _best_identity_title(
        self,
        *,
        name_fact: dict[str, Any],
        visual_fact: dict[str, Any],
        packaging_fact: dict[str, Any],
        brand_fact: dict[str, Any],
        model_fact: dict[str, Any],
        product_types: list[str],
    ) -> str | None:
        name_value = str(name_fact.get("value") or "").strip()
        if name_value and not self._is_generic_title(name_value):
            return name_value
        visual_value = str(visual_fact.get("value") or "").strip()
        if visual_value and not self._is_generic_title(visual_value):
            return visual_value
        packaging_value = str(packaging_fact.get("value") or "").strip()
        if packaging_value and not self._is_generic_title(packaging_value):
            return packaging_value
        brand = str(brand_fact.get("value") or "").strip()
        model = str(model_fact.get("value") or "").strip()
        product_type = str(product_types[0]).strip() if product_types else ""
        if brand and model:
            return f"{brand} {model}"
        if model:
            return model
        if brand and product_type:
            return f"{brand} {product_type}"
        if product_type:
            return product_type
        if name_value:
            return name_value
        if packaging_value:
            return packaging_value
        return None

    @staticmethod
    def _merge_specifications(records: list[dict[str, Any]]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for record in sorted(records, key=PhotoEnrichmentService._evidence_rank, reverse=True):
            raw = record.get("specifications")
            facts = raw if isinstance(raw, dict) else {}
            for key, value in facts.items():
                if value not in (None, "") and key not in merged:
                    merged[str(key)] = value
        return merged

    @staticmethod
    def _evidence_rank(record: dict[str, Any]) -> int:
        if record.get("decoded_barcode_value"): return 100
        if record.get("model") or record.get("mpn"): return 90
        if record.get("packaging_identity"): return 80
        if record.get("brand") and record.get("specifications"): return 70
        if record.get("product_name"): return 55
        return 20

    @staticmethod
    def _confidence_value(value: Any, *, default: float) -> float:
        labels = {"high": 0.85, "medium": 0.6, "low": 0.35}
        try:
            parsed = labels.get(str(value).strip().lower(), float(value))
        except (TypeError, ValueError):
            parsed = default
        return max(0.0, min(float(parsed), 1.0))

    @staticmethod
    def _first_value(records: list[dict[str, Any]], key: str) -> Any:
        return next((record.get(key) for record in records if record.get(key)), None)

    @staticmethod
    def _unique_values(records: list[dict[str, Any]], key: str) -> list[Any]:
        values: list[Any] = []
        for record in records:
            value = record.get(key)
            for item in value if isinstance(value, list) else [value]:
                if item and item not in values: values.append(item)
        return values

    @staticmethod
    def _identity_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates = []
        for record in records:
            title = record.get("product_name") or record.get("packaging_identity") or record.get("visual_title")
            if title and all(candidate["title"] != title for candidate in candidates):
                candidates.append({"title": title, "brand": record.get("brand"), "model": record.get("model"), "confidence": min(PhotoEnrichmentService._evidence_rank(record) / 100, 1.0), "media_id": record.get("media_id")})
        return candidates[:3]

    @staticmethod
    def _title_candidates(records: list[dict[str, Any]]) -> list[str]:
        candidates: list[str] = []
        for record in records:
            for field in ("product_name", "visual_title", "packaging_identity"):
                value = str(record.get(field) or "").strip()
                if value and value not in candidates and not PhotoEnrichmentService._is_generic_title(value):
                    candidates.append(value)
                    break
        return candidates

    @staticmethod
    def _title_candidates_conflict(candidates: list[str]) -> bool:
        if len(candidates) < 2:
            return False
        normalized = [re.sub(r"[^a-z0-9]+", " ", candidate.lower()).split() for candidate in candidates]
        if not normalized:
            return False
        leader = normalized[0]
        for tokens in normalized[1:]:
            shared = len(set(leader) & set(tokens))
            total = max(len(set(leader) | set(tokens)), 1)
            if shared / total < 0.45:
                return True
        return False

    def _barcode_attempts(self, image: Any) -> tuple[list[dict[str, Any]], list[str]]:
        try:
            import cv2
        except Exception:
            return [{"variant": "unavailable", "status": "opencv_unavailable"}], []
        variants = [("full", image)]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        variants.extend([("gray", gray), ("contrast", cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)), ("otsu", cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])])
        height, width = gray.shape[:2]
        variants.append(("lower_half", image[height // 2:, :]))
        attempts, values = [], []
        for name, variant in variants:
            for turns in range(4):
                frame = variant if turns == 0 else cv2.rotate(variant, (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE)[turns - 1])
                decoded = []
                try:
                    if hasattr(cv2, "barcode_BarcodeDetector"):
                        ok, decoded_info, *_ = cv2.barcode_BarcodeDetector().detectAndDecode(frame)
                        decoded = [str(value).strip() for value in (decoded_info or []) if str(value).strip()] if ok else []
                except Exception:
                    decoded = []
                attempts.append({"variant": name, "rotation": turns * 90, "decoded": decoded, "status": "decoded" if decoded else "no_decode"})
                for value in decoded:
                    if value not in values: values.append(value)
        return attempts, values

    @staticmethod
    def _targeted_ocr(image: Any) -> str:
        try:
            import cv2, pytesseract
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            height, width = gray.shape[:2]
            half = max(1, height // 2)
            frames = [
                gray,
                cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC),
                cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
                cv2.Canny(gray, 50, 150),
                gray[:half, :],
                gray[half:, :],
                gray[:, : max(1, width // 2)],
                gray[:, max(1, width // 2):],
            ]
            chunks = []
            for frame in frames:
                for psm in (6, 11, 12):
                    text = str(pytesseract.image_to_string(frame, config=f"--oem 3 --psm {psm}") or "").strip()
                    if text and text not in chunks:
                        chunks.append(text)
            return "\n".join(chunks)
        except Exception:
            return ""

    def _photo_evidence_vision(self, photo_path: str, *, api_key: str, db: Any = None) -> dict[str, Any]:
        if db is not None:
            durable_circuit = provider_circuit_open(db)
            if durable_circuit:
                raise RuntimeError("AI provider circuit open")
        image_b64 = base64.b64encode(Path(photo_path).read_bytes()).decode("utf-8")
        prompt = ("Return strict JSON for this one inventory photograph: photo_role, brand, product_name, product_type, model, mpn, manufacturer_sku, "
                  "packaging_identity, specifications, included_components, damage, condition_evidence, measurement_evidence, testing_evidence, category, visual_title, confidence. "
                  "Inspect labels, UPCs, barcodes, etched or embossed text, compatibility clues, and packaging identity. "
                  "Do not invent identifiers. State only visible facts.")
        payload = {"model": self.model, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]}], "temperature": 0, "max_tokens": 1400}
        reservation_id = None
        if db is not None:
            reservation = reserve_durable(db, key=ai_signature(purpose="photo_evidence_vision", payload={"path": photo_path, "model": self.model}), pool=model_pool(self.model) or "mini", model=self.model, estimated_tokens=3000, purpose="photo_evidence")
            if reservation.get("status") != "RESERVED":
                raise RuntimeError("OpenAI complimentary budget unavailable; photo evidence is scheduled for retry")
            reservation_id = reservation.get("reservation_id")
        with httpx.Client(timeout=90) as client:
            response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
            if response.is_error:
                try:
                    body = response.json()
                except Exception:
                    body = {"error": {"message": response.text[:2000]}}
                details = classify_openai_error(response.status_code, body, response.headers)
                if response.status_code == 429 and db is not None:
                    open_provider_circuit(db, reason=format_openai_error(details), seconds=provider_circuit_seconds(details.get("category")))
                if reservation_id:
                    reconcile_durable(db, reservation_id, actual_tokens=0, state="RELEASED", error=format_openai_error(details))
                raise RuntimeError(format_openai_error(details))
            body = response.json()
            if reservation_id:
                usage = body.get("usage") or {}
                reconcile_durable(db, reservation_id, actual_tokens=int(usage.get("total_tokens") or 0), state="RECONCILED")
        parsed = json.loads(body["choices"][0]["message"]["content"])
        return parsed if isinstance(parsed, dict) else {}

    def analyze_recovery_sequence(self, contact_sheet_path: str, *, image_count: int, db: Any = None, user_id: int | None = None) -> dict[str, Any]:
        """Return ordered product boundaries for a contact sheet of originals.

        The sheet is a temporary derivative only; it never replaces source media.
        Indices in the response are zero-based and must cover the photo sequence.
        """
        api_key, _mode = resolve_openai_key(db, user_id)
        if not api_key:
            raise RuntimeError("Connect an OpenAI account or enable an eligible PosterPro AI plan")
        if db is not None:
            durable_circuit = provider_circuit_open(db)
            if durable_circuit:
                raise RuntimeError("AI provider circuit open")
        image_b64 = base64.b64encode(Path(contact_sheet_path).read_bytes()).decode("utf-8")
        prompt = (
            "You are splitting a chronological inventory photo sequence. Each tile is labelled [0] through "
            f"[{max(0, image_count - 1)}]. Identify real product/set boundaries from visual changes, labels, "
            "brands, packaging, barcode/model changes, and slate-like screens. Do not infer historical assignments. "
            "Return strict JSON: {groups:[{start,end,title,description,category,specifics,tags,estimated_value,condition,confidence,boundary_reason,review_required}], "
            "slate_indices:[integer], unresolved_boundaries:[{after,reason}]}. Groups must be chronological, non-overlapping, and cover every non-slate tile. "
            "Use a specific title only when visible evidence supports it; otherwise use a careful generic physical description and set review_required true."
        )
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]},
            ],
            "temperature": 0.0,
            "max_tokens": 1800,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        reservation_id = None
        if db is not None:
            reservation = reserve_durable(db, key=ai_signature(purpose="recovery_sequence", payload={"path": contact_sheet_path, "model": self.model, "count": image_count}), pool=model_pool(self.model) or "mini", model=self.model, estimated_tokens=6000, user_id=user_id, purpose="recovery_sequence")
            if reservation.get("status") != "RESERVED":
                raise RuntimeError("OpenAI complimentary budget unavailable; sequence analysis is scheduled for retry")
            reservation_id = reservation.get("reservation_id")
        with httpx.Client(timeout=90) as client:
            response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            if response.status_code == 429:
                ai_open_circuit()
            if response.is_error:
                try:
                    body = response.json()
                except Exception:
                    body = {"error": {"message": response.text[:2000]}}
                details = classify_openai_error(response.status_code, body, response.headers)
                if response.status_code == 429 and db is not None:
                    try:
                        open_provider_circuit(db, reason=format_openai_error(details), seconds=provider_circuit_seconds(details.get("category")))
                    except Exception as exc:  # pragma: no cover - diagnostics must not hide provider failure
                        logger.warning("ai_provider_circuit_persist_failed error=%s", type(exc).__name__)
                if reservation_id:
                    reconcile_durable(db, reservation_id, actual_tokens=0, state="RELEASED", error=format_openai_error(details))
                raise RuntimeError(format_openai_error(details))
            body = response.json()
            if reservation_id:
                usage = body.get("usage") or {}
                reconcile_durable(db, reservation_id, actual_tokens=int(usage.get("total_tokens") or 0), state="RECONCILED")
        try:
            parsed = json.loads(body["choices"][0]["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("sequence analysis returned invalid JSON") from exc
        return parsed if isinstance(parsed, dict) else {}

    def _extract_json(self, photo_path: str, image_b64: str, template_name: str, *, api_key: str, db: Any = None) -> dict[str, Any]:
        guard_key = ai_signature(purpose=f"photo_extract:{template_name}", payload={"path": photo_path, "model": self.model})
        if not ai_allow(guard_key):
            raise RuntimeError("AI provider circuit open or duplicate photo extraction")
        if db is not None:
            durable_circuit = provider_circuit_open(db)
            if durable_circuit:
                raise RuntimeError("AI provider circuit open")
        prompt = get_prompt_template(template_name)
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Analyze photo: {photo_path}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    ],
                },
            ],
            "temperature": 0.2,
            "max_tokens": 1400,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        reservation_id = None
        if db is not None:
            reservation = reserve_durable(db, key=guard_key, pool=model_pool(self.model) or "mini", model=self.model, estimated_tokens=3000, purpose=f"photo_extract:{template_name}")
            if reservation.get("status") != "RESERVED":
                raise RuntimeError("OpenAI complimentary budget unavailable; photo extraction is scheduled for retry")
            reservation_id = reservation.get("reservation_id")
        with httpx.Client(timeout=60) as client:
            response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            if response.status_code == 429:
                ai_open_circuit()
            if response.is_error:
                try:
                    body = response.json()
                except Exception:
                    body = {"error": {"message": response.text[:2000]}}
                details = classify_openai_error(response.status_code, body, response.headers)
                if response.status_code == 429 and db is not None:
                    open_provider_circuit(db, reason=format_openai_error(details), seconds=provider_circuit_seconds(details.get("category")))
                if reservation_id:
                    reconcile_durable(db, reservation_id, actual_tokens=0, state="RELEASED", error=format_openai_error(details))
                raise RuntimeError(format_openai_error(details))
            data = response.json()
            if reservation_id:
                usage = data.get("usage") or {}
                reconcile_durable(db, reservation_id, actual_tokens=int(usage.get("total_tokens") or 0), state="RECONCILED")

        content = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                ai_mark_completed(guard_key)
                return parsed
            return {}
        except json.JSONDecodeError:
            logger.warning("Failed to parse OpenAI JSON response", extra={"template": template_name})
            return {}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
