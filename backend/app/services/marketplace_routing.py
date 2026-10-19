from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.enums import MARKETPLACE_DESTINATION_VALUES
from app.models.models import Listing


class MarketplaceRoutingRule(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    match_all: bool = False
    category_terms: list[str] = Field(default_factory=list, max_length=50)
    brand_terms: list[str] = Field(default_factory=list, max_length=50)
    condition_terms: list[str] = Field(default_factory=list, max_length=20)
    source_types: list[str] = Field(default_factory=list, max_length=20)
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    include_markets: list[str] = Field(default_factory=list, max_length=9)
    exclude_markets: list[str] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def validate_rule(self):
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot exceed max_price")
        allowed = set(MARKETPLACE_DESTINATION_VALUES)
        normalized_include = {str(value).strip().lower() for value in self.include_markets}
        normalized_exclude = {str(value).strip().lower() for value in self.exclude_markets}
        if not normalized_include or not normalized_include.issubset(allowed) or not normalized_exclude.issubset(allowed):
            raise ValueError("Routing rules require supported marketplace names")
        if not self.match_all and not any((self.category_terms, self.brand_terms, self.condition_terms, self.source_types, self.min_price is not None, self.max_price is not None)):
            raise ValueError("A routing rule needs at least one condition or explicit match_all=true")
        return self


class MarketplaceRoutingService:
    @staticmethod
    def normalize_markets(values: list[str] | None) -> list[str]:
        result: list[str] = []
        for value in values or []:
            market = str(value or "").strip().lower()
            if market in MARKETPLACE_DESTINATION_VALUES and market not in result:
                result.append(market)
        return result

    @staticmethod
    def _has_term(value: Any, terms: list[str]) -> bool:
        text = str(value or "").casefold()
        return not terms or any(str(term).strip().casefold() in text for term in terms if str(term).strip())

    def rule_matches(self, rule: MarketplaceRoutingRule, listing: Listing) -> bool:
        if not rule.enabled:
            return False
        specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
        brand = next((value for key, value in specifics.items() if str(key).casefold() == "brand"), None)
        price = listing.listing_price or listing.suggested_price or listing.buy_it_now_price or listing.estimated_value
        checks = [
            self._has_term(listing.category_suggestion, rule.category_terms),
            self._has_term(brand, rule.brand_terms),
            self._has_term(listing.condition, rule.condition_terms),
            self._has_term(listing.source_type, rule.source_types),
            rule.min_price is None or (price is not None and float(price) >= rule.min_price),
            rule.max_price is None or (price is not None and float(price) <= rule.max_price),
        ]
        return all(checks)

    def resolve(self, listing: Listing, rules: list[dict] | None, manual_override: list[str] | None = None) -> dict[str, Any]:
        if manual_override is not None:
            return {"marketplaces": self.normalize_markets(manual_override), "source": "MANUAL_OVERRIDE", "matched_rule_ids": []}
        parsed = [MarketplaceRoutingRule.model_validate(rule) for rule in (rules or [])]
        parsed.sort(key=lambda rule: (rule.priority, rule.id))
        included: list[str] = []
        excluded: set[str] = set()
        matched: list[str] = []
        for rule in parsed:
            if not self.rule_matches(rule, listing):
                continue
            matched.append(rule.id)
            for market in self.normalize_markets(rule.include_markets):
                if market not in included:
                    included.append(market)
            excluded.update(self.normalize_markets(rule.exclude_markets))
        return {
            "marketplaces": [market for market in included if market not in excluded],
            "source": "RULES" if matched else "NO_MATCH",
            "matched_rule_ids": matched,
        }
