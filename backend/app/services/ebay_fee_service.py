"""Versioned, category-aware eBay final-value fee estimation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


EBAY_FEE_RULE_VERSION = "2026-09-25"
STORE_PLANS = {"NONE", "STARTER", "BASIC", "PREMIUM", "ANCHOR", "ENTERPRISE"}
STORE_PLANS_WITH_DISCOUNT = {"BASIC", "PREMIUM", "ANCHOR", "ENTERPRISE"}


@dataclass(frozen=True)
class EbayFeeRule:
    id: str
    label: str
    plan_group: str
    calculation_type: str
    tiers: tuple[tuple[float | None, float], ...] = ()
    threshold: float | None = None
    whole_rate: float | None = None
    whole_tiers: tuple[tuple[float | None, float], ...] = ()
    order_fee_exempt: bool = False
    category_ids: frozenset[str] = frozenset()
    category_terms: tuple[str, ...] = ()
    priority: int = 0


def normalize_store_plan(value: Any) -> str:
    plan = str(value or "NONE").strip().upper()
    return plan if plan in STORE_PLANS else "NONE"


def seller_plan_group(value: Any) -> str:
    return "STORE" if normalize_store_plan(value) in STORE_PLANS_WITH_DISCOUNT else "STANDARD"


def _progressive(rule_id: str, label: str, group: str, first_limit: float, first_rate: float, above_rate: float, *, terms=(), ids=frozenset(), priority=0, tiers=None, order_fee_exempt=False):
    progressive_tiers = tuple(tiers) if tiers is not None else ((first_limit, first_rate), (None, above_rate))
    return EbayFeeRule(rule_id, label, group, "PROGRESSIVE", progressive_tiers, order_fee_exempt=order_fee_exempt, category_ids=ids, category_terms=terms, priority=priority)


def _whole(rule_id: str, label: str, group: str, threshold: float, below_rate: float, above_rate: float, *, terms=(), ids=frozenset(), priority=0, whole_tiers=None):
    thresholds = tuple(whole_tiers) if whole_tiers is not None else ((threshold, below_rate), (None, above_rate))
    return EbayFeeRule(rule_id, label, group, "WHOLE_SALE_THRESHOLD", threshold=threshold, whole_rate=below_rate, tiers=((None, above_rate),), whole_tiers=thresholds, category_ids=ids, category_terms=terms, priority=priority)


def _rules() -> tuple[EbayFeeRule, ...]:
    rules: list[EbayFeeRule] = []
    # Exact/specific category rules are deliberately listed before broad rules.
    for group, prefix, limits in (("STANDARD", "standard", (7500.0,)), ("STORE", "store", (2500.0,))):
        limit = limits[0]
        rules.extend([
            _progressive(f"{prefix}-athletic-shoes", "Athletic Shoes", group, limit, .08 if group == "STANDARD" else .07, .08 if group == "STANDARD" else .07, terms=("athletic shoes",), priority=90, order_fee_exempt=True),
            _progressive(f"{prefix}-heavy-equipment", "Qualifying Heavy Equipment", group, 15000, .03 if group == "STANDARD" else .025, .005, terms=("heavy equipment", "commercial printing press", "food truck", "food trailer", "food cart"), priority=85),
            _whole(f"{prefix}-jewelry", "Jewelry", group, 5000, .15 if group == "STANDARD" else .13, .09 if group == "STANDARD" else .07, terms=("jewelry",), priority=80),
            _whole(f"{prefix}-womens-bags", "Women's Bags & Handbags", group, 2000, .15 if group == "STANDARD" else .13, .09 if group == "STANDARD" else .07, terms=("women's bags", "handbags", "womens bags"), priority=85),
            _whole(f"{prefix}-bullion", "Bullion", group, 7500 if group == "STANDARD" else 1500, .136 if group == "STANDARD" else .075, .07 if group == "STANDARD" else .05, terms=("bullion",), priority=88, whole_tiers=((7500, .136), (None, .07)) if group == "STANDARD" else ((1500, .075), (10000, .05), (None, .045))),
            EbayFeeRule(f"{prefix}-nft", "NFTs", group, "WHOLE_SALE_THRESHOLD", whole_rate=.05, tiers=((None, .05),), category_terms=("nft",), priority=80),
        ])
    # Standard/non-Store tables.
    rules += [
        _progressive("standard-default", "Default / No Store", "STANDARD", 7500, .136, .0235),
        _progressive("standard-books-movies-music", "Books, Movies & most Music", "STANDARD", 7500, .153, .0235, terms=("books", "movies", "music"), priority=30),
        _progressive("standard-coins", "Coins & Paper Money", "STANDARD", 7500, .1325, .0235, terms=("coins", "paper money"), priority=35),
        _progressive("standard-cards", "Trading Cards", "STANDARD", 7500, .1325, .0235, terms=("trading cards", "collectible card games", "sports trading cards"), priority=45),
        _progressive("standard-watches", "Watches", "STANDARD", 1000, .15, .03, terms=("watches",), priority=46, tiers=((1000, .15), (7500, .065), (None, .03))),
        _progressive("standard-guitars", "Guitars & Basses", "STANDARD", 7500, .067, .0235, terms=("guitars", "basses"), priority=45),
    ]
    # Store tables.
    rules += [
        _progressive("store-default", "Default / Store", "STORE", 2500, .127, .0235),
        _progressive("store-books-movies-music", "Books, Movies & most Music", "STORE", 2500, .153, .0235, terms=("books", "movies", "music"), priority=30),
        _progressive("store-cameras-default", "Cameras & Photo", "STORE", 2500, .0935, .0235, terms=("cameras & photo", "cameras and photo"), priority=30),
        _progressive("store-cameras-accessory", "Camera Accessories & Parts", "STORE", 2500, .127, .0235, terms=("camera accessories", "camera parts", "tripods", "camera support"), priority=50),
        _progressive("store-cell-default", "Cell Phones & Accessories", "STORE", 2500, .0935, .0235, terms=("cell phones & accessories", "cell phones and accessories"), priority=30),
        _progressive("store-cell-accessory", "Cell Phone Accessories", "STORE", 2500, .127, .0235, terms=("cell phone accessories", "phone accessories", "cellular accessories"), priority=50),
        _progressive("store-computers-default", "Computers / Tablets / Networking", "STORE", 2500, .0935, .0235, terms=("computers/tablets/networking", "computers / tablets / networking"), priority=30),
        _progressive("store-computer-qualifying", "Qualifying Computer Category", "STORE", 2500, .0735, .0235, terms=("desktops", "all-in-ones", "laptops", "netbooks", "tablets", "processors", "ram", "motherboards", "hard drives", "ssd", "nas", "monitors", "printers"), priority=45),
        _progressive("store-computer-accessory", "Computer Accessories", "STORE", 2500, .127, .0235, terms=("cables", "connectors", "keyboards", "mice", "laptop accessories", "desktop accessories"), priority=50),
        _progressive("store-consumer-electronics", "Consumer Electronics", "STORE", 2500, .0935, .0235, terms=("consumer electronics",), priority=25),
        _progressive("store-consumer-electronics-accessory", "Consumer Electronics Accessories", "STORE", 2500, .127, .0235, terms=("electronics accessories", "electronics parts", "electronics cables", "electronics batteries"), priority=50),
        _progressive("store-coins", "Coins & Paper Money", "STORE", 4000, .09, .0235, terms=("coins", "paper money"), priority=35),
        _progressive("store-cards", "Trading Cards", "STORE", 2500, .1235, .0235, terms=("trading cards", "collectible card games", "sports trading cards", "comic books & memorabilia"), priority=45),
        _progressive("store-motors-parts", "Motors Parts & Accessories", "STORE", 1000, .115, .0235, terms=("motors parts", "parts & accessories", "tools", "safety"), priority=35),
        _progressive("store-tires", "Tires", "STORE", 1000, .095, .0235, terms=("tires",), priority=45),
        _progressive("store-motors-apparel", "Motors Apparel", "STORE", 1000, .127, .0235, terms=("motors apparel", "protective gear"), priority=35),
        _progressive("store-in-car-tech", "In-Car Technology", "STORE", 1000, .0935, .0235, terms=("in-car technology", "gps", "security"), priority=35),
        _progressive("store-music", "Music", "STORE", 2500, .153, .0235, terms=("music",), priority=25),
        _progressive("store-vinyl", "Vinyl Records", "STORE", 2500, .127, .0235, terms=("vinyl records",), priority=45),
        _progressive("store-musical-instruments", "Musical Instruments", "STORE", 2500, .1035, .0235, terms=("musical instruments",), priority=25),
        _progressive("store-dj", "DJ Equipment / Pro Audio", "STORE", 2500, .0935, .0235, terms=("dj equipment", "pro audio"), priority=45),
        _progressive("store-guitars", "Guitars & Basses", "STORE", 2500, .067, .0235, terms=("guitars", "basses"), priority=50),
        _progressive("store-video-consoles", "Video Game Consoles", "STORE", 2500, .0735, .0235, terms=("video game consoles",), priority=45),
        _progressive("store-video", "Video Games & Consoles", "STORE", 2500, .0935, .0235, terms=("video games & consoles",), priority=25),
        _progressive("store-video-accessories", "Video Game Accessories", "STORE", 2500, .127, .0235, terms=("video game accessories", "video games > accessories", "replacement parts"), priority=40),
        _progressive("store-watches", "Watches", "STORE", 1000, .125, .03, terms=("watches",), priority=46, tiers=((1000, .125), (5000, .04), (None, .03))),
    ]
    return tuple(rules)


EBAY_FEE_RULES = _rules()


def _category_text(listing: Any) -> str:
    # Category fields are authoritative.  Title text is only a last-resort
    # fallback for legacy rows that have no category path at all.
    parts = [str(getattr(listing, key, "") or "") for key in ("category_suggestion", "detected_category_guess")]
    if not any(part.strip() for part in parts):
        parts.append(str(getattr(listing, "title", "") or ""))
    return " ".join(parts).lower()


def select_rule(listing: Any, seller_plan: Any = "NONE", fee_base: float | None = None) -> EbayFeeRule:
    group = seller_plan_group(seller_plan)
    category_id = str(getattr(listing, "category_id", "") or "").strip()
    text = _category_text(listing)
    candidates = [r for r in EBAY_FEE_RULES if r.plan_group == group and (not r.category_ids or category_id in r.category_ids) and (not r.category_terms or any(term in text for term in r.category_terms))]
    if fee_base is not None and fee_base < 150:
        candidates = [r for r in candidates if "athletic-shoes" not in r.id]
    exact = [r for r in candidates if category_id and r.category_ids and category_id in r.category_ids]
    if exact:
        return max(exact, key=lambda r: r.priority)
    named = [r for r in candidates if r.category_terms]
    return max(named, key=lambda r: r.priority) if named else next(r for r in EBAY_FEE_RULES if r.id == ("store-default" if group == "STORE" else "standard-default"))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _buyer_shipping(listing: Any) -> float:
    shipping = getattr(listing, "shipping_profile", None) if isinstance(getattr(listing, "shipping_profile", None), dict) else {}
    data = getattr(listing, "marketplace_data", None) if isinstance(getattr(listing, "marketplace_data", None), dict) else {}
    for value in (shipping.get("buyer_paid_shipping"), shipping.get("buyer_shipping"), shipping.get("shipping_charge"), shipping.get("shipping_price"), shipping.get("shipping_charge_amount"), shipping.get("shipping_flat_cost"), shipping.get("flat_rate"), (data.get("shipping") or {}).get("buyer_paid_shipping"), (data.get("shipping") or {}).get("shipping_price"), (data.get("shipping") or {}).get("shipping_charge")):
        if value not in (None, ""):
            return _number(value)
    return 0.0


def calculate_expected_ebay_fee(listing: Any, seller_config: dict[str, Any] | None = None) -> dict[str, Any]:
    seller_config = seller_config if isinstance(seller_config, dict) else {}
    plan = normalize_store_plan(seller_config.get("store_plan") or seller_config.get("subscription_level") or "NONE")
    price = _number(getattr(listing, "sale_price", None) or getattr(listing, "listing_price", None) or getattr(listing, "suggested_price", None))
    shipping = _buyer_shipping(listing)
    data = getattr(listing, "marketplace_data", None) if isinstance(getattr(listing, "marketplace_data", None), dict) else {}
    tax_rate = _number(seller_config.get("estimated_sales_tax_rate", data.get("estimated_sales_tax_rate", 0.0)))
    # Settings UI accepts a human percentage (for example 8.25), while
    # stored/imported configuration may already be a decimal (0.0825).
    if tax_rate > 1:
        tax_rate /= 100.0
    tax_data = data.get("sales_tax") if isinstance(data.get("sales_tax"), dict) else {}
    explicit_tax = data.get("estimated_sales_tax_amount", tax_data.get("amount", data.get("sales_tax_amount")))
    tax_amount = _number(explicit_tax) if explicit_tax is not None else round((price + shipping) * tax_rate, 2)
    fee_base = round(price + shipping + tax_amount + _number(seller_config.get("other_buyer_paid_amount", data.get("other_buyer_paid_amount"))), 2)
    rule = select_rule(listing, plan, fee_base=fee_base)
    if rule.calculation_type == "WHOLE_SALE_THRESHOLD":
        whole_tiers = rule.whole_tiers or ((rule.threshold, rule.whole_rate or .136), (None, rule.tiers[0][1] if rule.tiers else .136))
        percentage_fee = next((fee_base * rate for threshold, rate in whole_tiers if threshold is None or fee_base <= threshold), fee_base * (whole_tiers[-1][1]))
    else:
        remaining = fee_base
        prior_limit = 0.0
        percentage_fee = 0.0
        for limit, rate in rule.tiers:
            portion = remaining if limit is None else min(remaining, max(0.0, limit - prior_limit))
            percentage_fee += portion * rate
            remaining -= portion
            if limit is not None:
                prior_limit = limit
            if remaining <= 0:
                break
    exempt = rule.order_fee_exempt and fee_base >= 150
    order_fee = 0.0 if exempt else (0.30 if fee_base <= 10 else 0.40)
    total = round(percentage_fee + order_fee, 2)
    return {"feeBase": fee_base, "percentageFee": round(percentage_fee, 2), "orderFee": round(order_fee, 2), "totalExpectedFee": total, "effectiveRate": round((percentage_fee / fee_base) * 100, 4) if fee_base else 0.0, "ruleId": rule.id, "ruleLabel": rule.label, "sellerPlan": plan, "sellerPlanGroup": rule.plan_group, "calculationType": rule.calculation_type, "taxAmount": tax_amount, "buyerPaidShipping": shipping, "version": EBAY_FEE_RULE_VERSION}
