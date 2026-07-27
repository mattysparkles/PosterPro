from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.prompts.templates import get_prompt_template

logger = logging.getLogger(__name__)

PHOTO_EVIDENCE_PIPELINE_VERSION = "recovery_photo_evidence_v2"
# V3 preserves the prior V2 evidence records while recording the corrected
# field-by-field synthesis separately for audit and before/after comparison.
FULL_GROUP_EVIDENCE_PIPELINE_VERSION = "recovery_full_group_evidence_v3"
_IDENTIFIER_RE = re.compile(r"(?<!\d)(\d{12,14}|\d{9}[\dXx])(?!\d)")
_MODEL_RE = re.compile(r"\b(?:MODEL|MFG\s*PART|MPN|PART\s*(?:NO|NUMBER))\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,})", re.I)


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
    placeholders = set(synthesis.get("placeholders") or [])
    if {"price", "weight", "dimensions"} & placeholders:
        return "blocked_placeholder_data"
    if "measurements" in (synthesis.get("review_flags") or []):
        return "needs_measurement_review"
    return "trusted_for_draft"


class PhotoEnrichmentService:
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model

    def enrich_photo(self, photo_path: str) -> dict[str, Any]:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        image_b64 = base64.b64encode(Path(photo_path).read_bytes()).decode("utf-8")
        outputs = {
            "title": self._extract_json(photo_path, image_b64, "extract_poster_title").get("title"),
            "description": self._extract_json(photo_path, image_b64, "extract_description").get("description"),
        }
        category = self._extract_json(photo_path, image_b64, "detect_category")
        keywords = self._extract_json(photo_path, image_b64, "extract_keywords")

        outputs["category_id"] = category.get("category_id")
        outputs["category_suggestion"] = category.get("category_name")
        outputs["tags"] = keywords.get("keywords") or []
        outputs["item_specifics"] = keywords.get("item_specifics") or {}
        outputs["estimated_value"] = _safe_float(keywords.get("estimated_value"))
        return outputs

    def enrich_group(self, photo_paths: list[str]) -> dict[str, Any]:
        """Compatibility wrapper: evaluate all photos and synthesize, never select a lead photo."""
        records = [self.extract_photo_evidence(path, media_id=index + 1) for index, path in enumerate(photo_paths)]
        synthesis = self.synthesize_group_evidence(records)
        return {
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
        }

    def extract_photo_evidence(self, photo_path: str, *, media_id: int | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
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
        if settings.openai_api_key:
            try:
                result.update(self._photo_evidence_vision(photo_path))
                result["extraction_method"] = "deterministic_ocr_barcode+vision"
            except Exception as exc:
                result["vision_error"] = type(exc).__name__
        result["confidence"] = self._confidence_value(result.get("confidence"), default=0.45)
        return result

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
        conflicts: list[str] = []
        if len(barcode_values) > 1 and not intentional_set:
            conflicts.append("multiple_incompatible_barcodes")
        if len(models) > 1 and not intentional_set:
            conflicts.append("multiple_model_labels")
        # Brand/type disagreement is a strong warning only when there is no
        # shared model/barcode evidence that would explain accessories/views.
        if len(brands) > 1 and not intentional_set and not (len(barcode_values) == 1 or len(models) == 1):
            conflicts.append("multiple_brands")
        if len(product_types) > 1 and not intentional_set and not (len(barcode_values) == 1 or len(models) == 1):
            conflicts.append("multiple_product_types")
        group_kind = "intentional_set" if intentional_set else ("multiple_unrelated_products" if conflicts else "one_item")

        selected_facts = {
            field: self._select_fact(usable, field, fallback=fallback)
            for field, fallback in (
                ("decoded_barcode_value", None), ("brand", None), ("model", "mpn"), ("mpn", "model"),
                ("product_name", "packaging_identity"), ("packaging_identity", "product_name"),
                ("category", None), ("condition_evidence", None), ("testing_evidence", None),
            )
        }
        identifier_fact = selected_facts["decoded_barcode_value"]
        model_fact = selected_facts["model"]
        name_fact = selected_facts["product_name"]
        packaging_fact = selected_facts["packaging_identity"]
        brand_fact = selected_facts["brand"]
        title = name_fact["value"] or packaging_fact["value"]
        if not title and brand_fact["value"] and product_types:
            title = f"{brand_fact['value']} {product_types[0]}"
        identifier = identifier_fact["value"] or model_fact["value"] or selected_facts["mpn"]["value"]
        supporting = {field: fact["media_ids"] for field, fact in selected_facts.items()}
        field_confidence = {field: fact["confidence"] for field, fact in selected_facts.items()}
        identity_confidence = max(identifier_fact["confidence"], model_fact["confidence"], name_fact["confidence"], packaging_fact["confidence"])
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
            "placeholders": placeholders, "review_flags": ["measurements"] if "dimensions" in placeholders else [],
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
            frames = [gray, cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC), cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]]
            chunks = []
            for frame in frames:
                text = str(pytesseract.image_to_string(frame, config="--oem 3 --psm 6") or "").strip()
                if text and text not in chunks: chunks.append(text)
            return "\n".join(chunks)
        except Exception:
            return ""

    def _photo_evidence_vision(self, photo_path: str) -> dict[str, Any]:
        image_b64 = base64.b64encode(Path(photo_path).read_bytes()).decode("utf-8")
        prompt = ("Return strict JSON for this one inventory photograph: photo_role, brand, product_name, product_type, model, mpn, manufacturer_sku, "
                  "packaging_identity, specifications, included_components, damage, condition_evidence, measurement_evidence, testing_evidence, category, visual_title, confidence. "
                  "Do not invent identifiers. State only visible facts.")
        payload = {"model": self.model, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]}], "temperature": 0}
        with httpx.Client(timeout=90) as client:
            response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"})
            response.raise_for_status()
        parsed = json.loads(response.json()["choices"][0]["message"]["content"])
        return parsed if isinstance(parsed, dict) else {}

    def analyze_recovery_sequence(self, contact_sheet_path: str, *, image_count: int) -> dict[str, Any]:
        """Return ordered product boundaries for a contact sheet of originals.

        The sheet is a temporary derivative only; it never replaces source media.
        Indices in the response are zero-based and must cover the photo sequence.
        """
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
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
        }
        headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=90) as client:
            response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
        try:
            parsed = json.loads(response.json()["choices"][0]["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("sequence analysis returned invalid JSON") from exc
        return parsed if isinstance(parsed, dict) else {}

    def _extract_json(self, photo_path: str, image_b64: str, template_name: str) -> dict[str, Any]:
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
        }
        headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=60) as client:
            response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
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
