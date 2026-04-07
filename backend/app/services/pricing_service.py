from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Listing
from app.prompts.templates import get_prompt_template

logger = logging.getLogger(__name__)


class PricingServiceError(RuntimeError):
    """Raised when pricing generation fails in a non-recoverable way."""


class PricingService:
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model

    def generate_pricing(self, db: Session, listing_id: int) -> dict[str, float]:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")

        rule_based = self._rule_based_pricing(listing)
        llm = self._llm_pricing_fallback(listing)
        pricing = self._merge_pricing(rule_based, llm)

        listing.start_price = pricing["start_price"]
        listing.buy_it_now_price = pricing["buy_it_now_price"]
        listing.min_acceptable_offer = pricing["min_acceptable_offer"]
        db.add(listing)
        db.commit()
        db.refresh(listing)

        logger.info(
            "Auto pricing complete",
            extra={
                "listing_id": listing.id,
                "start_price": listing.start_price,
                "buy_it_now_price": listing.buy_it_now_price,
                "min_acceptable_offer": listing.min_acceptable_offer,
            },
        )
        return pricing

    def get_pricing(self, db: Session, listing_id: int) -> dict[str, Any]:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")
        return {
            "listing_id": listing.id,
            "start_price": listing.start_price,
            "buy_it_now_price": listing.buy_it_now_price,
            "min_acceptable_offer": listing.min_acceptable_offer,
            "estimated_value": listing.estimated_value,
            "pricing_source": "auto",
        }

    def adjust_price_based_on_comps(self, db: Session, listing_id: int) -> dict[str, Any]:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")

        logger.info("Comps adjustment placeholder run", extra={"listing_id": listing_id})
        return {
            "listing_id": listing_id,
            "status": "noop",
            "message": "Placeholder comps adjustment executed",
        }

    def _rule_based_pricing(self, listing: Listing) -> dict[str, float]:
        estimated_value = float(listing.estimated_value or 24.99)
        anchor = max(estimated_value, 9.99)

        if listing.category_suggestion and "collect" in listing.category_suggestion.lower():
            start_multiplier = 0.62
            bin_multiplier = 1.08
        else:
            start_multiplier = 0.55
            bin_multiplier = 1.0

        start_price = round(max(4.99, anchor * start_multiplier), 2)
        buy_it_now_price = round(max(start_price + 2.0, anchor * bin_multiplier), 2)
        min_acceptable_offer = round(max(start_price, buy_it_now_price * 0.82), 2)

        return {
            "start_price": start_price,
            "buy_it_now_price": buy_it_now_price,
            "min_acceptable_offer": min_acceptable_offer,
        }

    def _llm_pricing_fallback(self, listing: Listing) -> dict[str, float] | None:
        if not settings.openai_api_key:
            logger.info("Skipping LLM pricing fallback; OPENAI_API_KEY not configured", extra={"listing_id": listing.id})
            return None

        prompt = get_prompt_template("generate_pricing_recommendation")
        signals = {
            "title": listing.title,
            "keywords": listing.tags or [],
            "category": listing.category_suggestion or listing.category_id,
            "estimated_value": listing.estimated_value,
        }
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"Pricing signals (JSON): {json.dumps(signals)}",
                },
            ],
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}

        backoff_seconds = 0.5
        with httpx.Client(timeout=45) as client:
            data = None
            for attempt in range(1, 4):
                response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    logger.warning(
                        "Transient pricing LLM error",
                        extra={"listing_id": listing.id, "status_code": response.status_code, "attempt": attempt},
                    )
                    time.sleep(backoff_seconds)
                    backoff_seconds *= 2
                    continue
                response.raise_for_status()
                data = response.json()
                break

        if data is None:
            raise PricingServiceError(f"Pricing LLM retry exhaustion for listing {listing.id}")

        content = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            return {
                "start_price": float(parsed.get("start_price")),
                "buy_it_now_price": float(parsed.get("buy_it_now_price")),
                "min_acceptable_offer": float(parsed.get("min_acceptable_offer")),
            }
        except Exception:
            logger.warning("Failed to parse LLM pricing fallback", extra={"listing_id": listing.id})
            return None

    @staticmethod
    def _merge_pricing(rule_based: dict[str, float], llm: dict[str, float] | None) -> dict[str, float]:
        if not llm:
            return rule_based

        start_price = _safe_price(llm.get("start_price")) or rule_based["start_price"]
        buy_it_now_price = _safe_price(llm.get("buy_it_now_price")) or rule_based["buy_it_now_price"]
        min_offer = _safe_price(llm.get("min_acceptable_offer")) or rule_based["min_acceptable_offer"]

        buy_it_now_price = max(buy_it_now_price, start_price)
        min_offer = min(max(min_offer, start_price), buy_it_now_price)

        return {
            "start_price": round(start_price, 2),
            "buy_it_now_price": round(buy_it_now_price, 2),
            "min_acceptable_offer": round(min_offer, 2),
        }


def _safe_price(value: Any) -> float | None:
    try:
        parsed = float(value)
        if parsed <= 0:
            return None
        return parsed
    except (TypeError, ValueError):
        return None
