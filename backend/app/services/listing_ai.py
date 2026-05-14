from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import settings
from app.prompts.templates import LISTING_PROMPT_TEMPLATE, get_prompt_template


class ListingAIService:
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model

    def generate(self, image_signals: dict[str, Any]) -> dict[str, Any]:
        fallback = self._fallback_generation(image_signals)
        llm = self._llm_generation(image_signals)
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
        merged["prompt_used"] = LISTING_PROMPT_TEMPLATE
        merged["intelligence_prompt"] = get_prompt_template("generate_listing_intelligence")
        merged["model_used"] = self.model if llm else "heuristic-fallback"
        merged["generation_source"] = "openai" if llm else "fallback"
        return merged

    def _llm_generation(self, image_signals: dict[str, Any]) -> dict[str, Any] | None:
        if not settings.openai_api_key:
            return None

        prompt = get_prompt_template("generate_listing_intelligence")
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Item signals JSON: {json.dumps(image_signals)}"},
            ],
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=45) as client:
                response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                return parsed if isinstance(parsed, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def _fallback_generation(self, image_signals: dict[str, Any]) -> dict[str, Any]:
        title_hint = str(image_signals.get("title_hint") or "").strip()
        source_type = str(image_signals.get("source_type") or "").strip()
        image_count = int(image_signals.get("image_count") or 0)
        category = "Collectibles" if "poster" in title_hint.lower() else "General resale"
        title = title_hint or "Pre-owned item lot"
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
            "description": "Pre-owned item. Review condition, included accessories, measurements, and visible wear before publishing.",
            "category_suggestion": category,
            "condition": "Used",
            "item_specifics": {
                "Brand": "Needs review",
                "Model": "Needs review",
                "Type": "Needs review",
            },
            "tags": ["resale", "preowned", "review-required"],
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
