from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.prompts.templates import get_prompt_template

logger = logging.getLogger(__name__)


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
