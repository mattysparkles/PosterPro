from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Listing
from app.services.listing_review import summarize_listing_readiness

STALE_PRICING_DAYS = 14
UNSUPPORTED_CLAIM_PATTERNS = [
    re.compile(r"\bauthentic\b", re.IGNORECASE),
    re.compile(r"\boem\b", re.IGNORECASE),
    re.compile(r"\bbrand new\b", re.IGNORECASE),
]


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
        return round(parsed, 2)
    except (TypeError, ValueError):
        return None


def _tokens(*values: str | None) -> set[str]:
    output: set[str] = set()
    for value in values:
        if not value:
            continue
        for token in re.split(r"[^a-zA-Z0-9]+", str(value).lower()):
            if token and len(token) > 1:
                output.add(token)
    return output


def _normalized_title(title: str | None) -> str:
    return " ".join(sorted(_tokens(title)))


def _extract_identifiers(listing: Listing) -> dict[str, str]:
    specifics = listing.item_specifics or {}
    source = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    identifiers = {
        "brand": str(specifics.get("Brand") or source.get("brand") or "").strip(),
        "model": str(specifics.get("Model") or specifics.get("MPN") or source.get("model") or "").strip(),
        "upc": str(specifics.get("UPC") or source.get("upc") or "").strip(),
        "asin": str(source.get("asin") or source.get("amazon_match_asin") or "").strip(),
    }
    return {key: value for key, value in identifiers.items() if value}


def _condition_bucket(listing: Listing) -> str:
    condition_data = listing.condition_data if isinstance(listing.condition_data, dict) else {}
    return str(condition_data.get("condition_bucket") or listing.condition or "needs_review").strip().lower()


def _source_price_comp(listing: Listing) -> dict | None:
    source = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    price = (
        _safe_float(listing.estimated_value)
        or _safe_float(listing.purchase_cost)
        or _safe_float(source.get("estimated_tax_value"))
        or _safe_float(source.get("source_price"))
        or _safe_float(source.get("msrp"))
    )
    if not price:
        return None
    return {
        "source_marketplace": str(source.get("source_marketplace") or listing.source_type or "source"),
        "comp_type": "source_price",
        "title": str(listing.title or source.get("product_name") or "Source price").strip(),
        "price": price,
        "shipping_price": None,
        "total_price": price,
        "condition": listing.condition,
        "sold_date": None,
        "listing_date": None,
        "source_url": str(source.get("item_url") or source.get("source_url") or "").strip() or None,
        "image_url": (listing.image_urls or [None])[0],
        "category": listing.category_suggestion or listing.category_id,
        "matched_identifiers": _extract_identifiers(listing),
        "normalized_title_tokens": sorted(_tokens(listing.title)),
        "manual": False,
        "source_label": "Imported source / MSRP",
    }


def _manual_comps(listing: Listing) -> list[dict]:
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    comps = marketplace_data.get("pricing_manual_comps")
    if not isinstance(comps, list):
        return []
    normalized: list[dict] = []
    for row in comps:
        if not isinstance(row, dict):
            continue
        price = _safe_float(row.get("price"))
        if not price or price <= 0:
            continue
        shipping = _safe_float(row.get("shipping_price")) or 0.0
        normalized.append(
            {
                "source_marketplace": str(row.get("source_marketplace") or "manual").strip() or "manual",
                "comp_type": str(row.get("comp_type") or "manual").strip() or "manual",
                "title": str(row.get("title") or "Manual comp").strip() or "Manual comp",
                "price": price,
                "shipping_price": shipping,
                "total_price": round(price + shipping, 2),
                "condition": str(row.get("condition") or "").strip() or None,
                "sold_date": row.get("sold_date"),
                "listing_date": row.get("listing_date"),
                "source_url": str(row.get("source_url") or "").strip() or None,
                "image_url": str(row.get("image_url") or "").strip() or None,
                "category": str(row.get("category") or "").strip() or None,
                "matched_identifiers": row.get("matched_identifiers") if isinstance(row.get("matched_identifiers"), dict) else {},
                "normalized_title_tokens": sorted(_tokens(row.get("title"))),
                "manual": True,
                "source_label": "Manual comp",
            }
        )
    return normalized


def _local_historical_comps(db: Session, listing: Listing) -> list[dict]:
    rows = db.execute(
        select(Listing).where(
            Listing.user_id == listing.user_id,
            Listing.id != listing.id,
            Listing.sale_price.is_not(None),
        )
    ).scalars().all()
    out: list[dict] = []
    for row in rows:
        price = _safe_float(row.sale_price)
        if not price:
            continue
        out.append(
            {
                "source_marketplace": "posterpro",
                "comp_type": "sold",
                "title": str(row.title or f"Listing {row.id}").strip(),
                "price": price,
                "shipping_price": _safe_float(row.shipping_cost) or 0.0,
                "total_price": round(price + (_safe_float(row.shipping_cost) or 0.0), 2),
                "condition": row.condition,
                "sold_date": row.sold_at.isoformat() if row.sold_at else None,
                "listing_date": row.created_at.isoformat() if row.created_at else None,
                "source_url": row.ebay_listing_id and f"https://www.ebay.com/itm/{row.ebay_listing_id}" or None,
                "image_url": (row.image_urls or [None])[0],
                "category": row.category_suggestion or row.category_id,
                "matched_identifiers": _extract_identifiers(row),
                "normalized_title_tokens": sorted(_tokens(row.title)),
                "manual": False,
                "source_label": "PosterPro sold history",
            }
        )
    return out


def _local_active_comps(db: Session, listing: Listing) -> list[dict]:
    rows = db.execute(
        select(Listing).where(
            Listing.user_id == listing.user_id,
            Listing.id != listing.id,
            Listing.listing_price.is_not(None),
            Listing.sale_price.is_(None),
        )
    ).scalars().all()
    out: list[dict] = []
    for row in rows[:50]:
        price = _safe_float(row.listing_price or row.suggested_price)
        if not price:
            continue
        out.append(
            {
                "source_marketplace": "posterpro",
                "comp_type": "active",
                "title": str(row.title or f"Listing {row.id}").strip(),
                "price": price,
                "shipping_price": _safe_float(row.shipping_cost) or 0.0,
                "total_price": round(price + (_safe_float(row.shipping_cost) or 0.0), 2),
                "condition": row.condition,
                "sold_date": None,
                "listing_date": row.created_at.isoformat() if row.created_at else None,
                "source_url": row.ebay_listing_id and f"https://www.ebay.com/itm/{row.ebay_listing_id}" or None,
                "image_url": (row.image_urls or [None])[0],
                "category": row.category_suggestion or row.category_id,
                "matched_identifiers": _extract_identifiers(row),
                "normalized_title_tokens": sorted(_tokens(row.title)),
                "manual": False,
                "source_label": "PosterPro active listing",
            }
        )
    return out


def _external_comps(external: list[dict] | None) -> list[dict]:
    rows: list[dict] = []
    for row in external or []:
        if not isinstance(row, dict):
            continue
        price = _safe_float(row.get("price"))
        if not price or price <= 0:
            continue
        shipping = _safe_float(row.get("shipping_price")) or 0.0
        comp_type = str(row.get("comp_type") or row.get("state") or "active").strip().lower()
        rows.append(
            {
                "source_marketplace": str(row.get("source_marketplace") or "ebay").strip() or "ebay",
                "comp_type": comp_type if comp_type in {"active", "sold", "completed", "manual", "source_price"} else "active",
                "title": str(row.get("title") or "Comparable").strip(),
                "price": price,
                "shipping_price": shipping,
                "total_price": round(price + shipping, 2),
                "condition": str(row.get("condition") or "").strip() or None,
                "sold_date": row.get("sold_date"),
                "listing_date": row.get("listing_date"),
                "source_url": str(row.get("source_url") or "").strip() or None,
                "image_url": str(row.get("image_url") or "").strip() or None,
                "category": str(row.get("category") or "").strip() or None,
                "matched_identifiers": row.get("matched_identifiers") if isinstance(row.get("matched_identifiers"), dict) else {},
                "normalized_title_tokens": sorted(_tokens(row.get("title"))),
                "manual": False,
                "source_label": str(row.get("source_label") or "eBay provider").strip() or "eBay provider",
            }
        )
    return rows


class PricingResearchService:
    def build_research(
        self,
        db: Session,
        listing: Listing,
        *,
        external_comparables: list[dict] | None = None,
        estimated_value_override: float | None = None,
        preserve_manual_override: bool = True,
    ) -> dict:
        listing_tokens = _tokens(listing.title, listing.description, listing.category_suggestion, listing.category_id)
        identifiers = _extract_identifiers(listing)
        comps = []
        source_price = _source_price_comp(listing)
        if source_price:
            comps.append(source_price)
        comps.extend(_manual_comps(listing))
        comps.extend(_local_historical_comps(db, listing))
        comps.extend(_local_active_comps(db, listing))
        comps.extend(_external_comps(external_comparables))

        included: list[dict] = []
        excluded: list[dict] = []
        for comp in comps:
            scored = self._score_comp(listing, comp, listing_tokens=listing_tokens, identifiers=identifiers)
            if scored["include"]:
                included.append(scored)
            else:
                excluded.append(scored)

        sold = [comp for comp in included if comp["comp_type"] in {"sold", "completed"}]
        active = [comp for comp in included if comp["comp_type"] == "active"]
        manual = [comp for comp in included if comp["comp_type"] == "manual" or comp.get("manual")]
        source_only = [comp for comp in included if comp["comp_type"] == "source_price"]
        sold_prices = [comp["total_price"] for comp in sold if comp.get("total_price")]
        active_prices = [comp["total_price"] for comp in active if comp.get("total_price")]
        manual_prices = [comp["total_price"] for comp in manual if comp.get("total_price")]
        source_prices = [comp["total_price"] for comp in source_only if comp.get("total_price")]

        anchor_prices = sold_prices or manual_prices or active_prices or source_prices
        weak_pricing = not anchor_prices
        baseline = estimated_value_override or listing.estimated_value or listing.listing_price or listing.suggested_price or 25.0
        median_price = self._median(anchor_prices) if anchor_prices else float(baseline)
        condition_adjustment = self._condition_adjustment(listing)
        adjusted = max(4.99, median_price * condition_adjustment)
        shipping_profile = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
        shipping_note = self._shipping_note(shipping_profile)
        list_price = round(adjusted, 2)
        quick_sale = round(max(4.99, adjusted * 0.88), 2)
        floor_price = round(max(4.99, adjusted * 0.74), 2)
        stretch_price = round(max(list_price, adjusted * 1.12), 2)

        confidence = self._confidence(included, sold_count=len(sold), active_count=len(active), weak_pricing=weak_pricing)
        warning = None
        if weak_pricing:
            warning = "Weak pricing confidence: no strong sold comps available."
        elif not sold:
            warning = "Sold comps unavailable. Recommendation leans on active/manual/source comps."

        recommended_marketplace_priority = "ebay"
        if shipping_profile.get("oversize") or shipping_profile.get("local_pickup_recommended"):
            recommended_marketplace_priority = "facebook"

        marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        existing_analysis = marketplace_data.get("pricing_analysis") if isinstance(marketplace_data.get("pricing_analysis"), dict) else {}
        manual_override_reason = str(marketplace_data.get("manual_price_override_reason") or "").strip() or None
        manual_override_price = _safe_float(marketplace_data.get("manual_price_override"))
        generated_at = datetime.utcnow().isoformat()
        stale_after = (datetime.utcnow() + timedelta(days=STALE_PRICING_DAYS)).isoformat()
        current_price = listing.listing_price or listing.suggested_price or list_price
        if preserve_manual_override and manual_override_price:
            current_price = manual_override_price

        explanation = self._build_explanation(
            included=included,
            sold=sold,
            active=active,
            condition_adjustment=condition_adjustment,
            shipping_note=shipping_note,
            warning=warning,
        )

        return {
            "listing_id": listing.id,
            "current_price": round(float(current_price), 2),
            "recommended_price": list_price,
            "quick_sale_price": quick_sale,
            "floor_price": floor_price,
            "stretch_price": stretch_price,
            "price_confidence": confidence,
            "confidence": confidence,
            "estimated_sell_through_confidence": round(min(0.97, 0.45 + (confidence * 0.45)), 2),
            "recommended_marketplace_priority": recommended_marketplace_priority,
            "pricing_explanation": explanation,
            "reasoning": explanation,
            "comp_count_used": len(included),
            "sold_comp_count_used": len(sold),
            "active_comp_count_used": len(active),
            "condition_adjustment_explanation": f"Condition multiplier {condition_adjustment:.2f} applied for {_condition_bucket(listing)} state.",
            "shipping_price_interaction_note": shipping_note,
            "warning": warning,
            "sold_comps_available": bool(sold),
            "sold_comps_unavailable": not bool(sold),
            "included_comps": included[:20],
            "excluded_comps": excluded[:20],
            "comparables": included[:20],
            "comparable_titles": [comp["title"] for comp in included[:5]],
            "historical_comparable_count": len(sold),
            "external_comparable_count": len(active),
            "external_market_avg_sold": round(self._median(sold_prices), 2) if sold_prices else None,
            "market_avg_sold": round(self._median(sold_prices or active_prices or source_prices), 2) if (sold_prices or active_prices or source_prices) else None,
            "manual_override_reason": manual_override_reason,
            "manual_override_price": manual_override_price,
            "generated_at": generated_at,
            "stale_after": stale_after,
            "stale": self._is_stale(existing_analysis),
            "provider_summary": {
                "local_sold": len([comp for comp in included if comp["source_marketplace"] == "posterpro" and comp["comp_type"] in {"sold", "completed"}]),
                "local_active": len([comp for comp in included if comp["source_marketplace"] == "posterpro" and comp["comp_type"] == "active"]),
                "manual": len([comp for comp in included if comp.get("manual")]),
                "source_price": len([comp for comp in included if comp["comp_type"] == "source_price"]),
                "external": len([comp for comp in included if comp["source_marketplace"] != "posterpro" and not comp.get("manual")]),
            },
        }

    def _score_comp(self, listing: Listing, comp: dict, *, listing_tokens: set[str], identifiers: dict[str, str]) -> dict:
        comp_tokens = set(comp.get("normalized_title_tokens") or [])
        comp_identifiers = comp.get("matched_identifiers") if isinstance(comp.get("matched_identifiers"), dict) else {}
        score = 0.18
        reasons: list[str] = []
        mismatch_flags: list[str] = []
        if comp.get("manual"):
            score += 0.28
            reasons.append("Manual operator comp")

        if identifiers.get("upc") and identifiers["upc"] == str(comp_identifiers.get("upc") or "").strip():
            score += 0.38
            reasons.append("Exact UPC match")
        if identifiers.get("asin") and identifiers["asin"] == str(comp_identifiers.get("asin") or "").strip():
            score += 0.36
            reasons.append("Exact ASIN match")
        if identifiers.get("brand") and identifiers["brand"].lower() in str(comp.get("title") or "").lower():
            score += 0.15
            reasons.append("Brand match")
        if identifiers.get("model") and identifiers["model"].lower() in str(comp.get("title") or "").lower():
            score += 0.2
            reasons.append("Model match")

        overlap = len(listing_tokens & comp_tokens)
        token_score = overlap / max(len(listing_tokens) or 1, 4)
        score += min(0.22, token_score * 0.25)
        if overlap:
            reasons.append(f"Title token overlap {overlap}")

        listing_condition = _condition_bucket(listing)
        comp_condition = str(comp.get("condition") or "").lower()
        if listing_condition and comp_condition and listing_condition.split("_")[0] in comp_condition:
            score += 0.1
            reasons.append("Condition aligned")
        if "parts" in comp_condition and "parts" not in listing_condition:
            score -= 0.3
            mismatch_flags.append("parts_only_mismatch")
        if any(token in comp_tokens for token in {"lot", "bundle", "set"}) and not any(token in listing_tokens for token in {"lot", "bundle", "set"}):
            score -= 0.18
            mismatch_flags.append("bundle_mismatch")
        if any(token in comp_tokens for token in {"pickup", "freight"}) or str(comp.get("source_url") or "").lower().find("pickup") >= 0:
            score -= 0.12
            mismatch_flags.append("pickup_or_freight")

        total_price = _safe_float(comp.get("total_price")) or _safe_float(comp.get("price")) or 0.0
        if total_price and listing.listing_price:
            ratio = total_price / max(float(listing.listing_price), 1.0)
            if ratio > 3.5 or ratio < 0.25:
                score -= 0.15
                mismatch_flags.append("price_outlier")

        include = score >= 0.34 and "parts_only_mismatch" not in mismatch_flags
        exclusion_reason = None
        if not include:
            exclusion_reason = ", ".join(mismatch_flags or ["low_relevance"])

        return {
            **comp,
            "relevance_score": round(max(0.0, min(score, 0.99)), 3),
            "confidence": round(max(0.0, min(score, 0.99)), 3),
            "include": include,
            "included": include,
            "excluded": not include,
            "reason_included": "; ".join(reasons) or None,
            "reason_excluded": exclusion_reason,
            "mismatch_flags": mismatch_flags,
        }

    @staticmethod
    def _condition_adjustment(listing: Listing) -> float:
        bucket = _condition_bucket(listing)
        if "new" in bucket:
            return 1.06
        if "open_box" in bucket:
            return 0.96
        if "used" in bucket:
            return 0.88
        if "parts" in bucket:
            return 0.55
        return 0.9

    @staticmethod
    def _shipping_note(shipping_profile: dict) -> str:
        if shipping_profile.get("local_pickup_recommended"):
            return "Local pickup may outperform ship-to-home because the item is oversized or fragile."
        if shipping_profile.get("oversize"):
            return "Oversized shipping likely compresses margin; price should absorb packaging and carrier surcharges."
        if shipping_profile.get("fragile"):
            return "Fragile handling may require stronger packaging cost assumptions."
        if not shipping_profile.get("package_weight"):
            return "Shipping cost still needs review because package weight is missing."
        return "Shipping profile looks usable for standard parcel pricing."

    @staticmethod
    def _median(values: list[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2

    @staticmethod
    def _confidence(included: list[dict], *, sold_count: int, active_count: int, weak_pricing: bool) -> float:
        if weak_pricing:
            return 0.28
        base = 0.45
        if sold_count:
            base += min(0.28, sold_count * 0.05)
        if active_count:
            base += min(0.14, active_count * 0.03)
        if included:
            avg = sum(comp.get("relevance_score") or 0 for comp in included) / len(included)
            base += min(0.18, avg * 0.2)
        return round(min(0.96, base), 2)

    @staticmethod
    def _is_stale(existing_analysis: dict) -> bool:
        generated_at = existing_analysis.get("generated_at")
        if not generated_at:
            return True
        try:
            ts = datetime.fromisoformat(str(generated_at))
        except ValueError:
            return True
        return ts < datetime.utcnow() - timedelta(days=STALE_PRICING_DAYS)

    @staticmethod
    def _build_explanation(*, included: list[dict], sold: list[dict], active: list[dict], condition_adjustment: float, shipping_note: str, warning: str | None) -> str:
        parts = [
            f"{len(included)} comps considered",
            f"{len(sold)} sold/completed",
            f"{len(active)} active",
            f"condition multiplier {condition_adjustment:.2f}",
            shipping_note,
        ]
        if warning:
            parts.append(warning)
        return ". ".join(parts) + "."


def compute_listing_quality_summary(listing: Listing, pricing_analysis: dict | None = None) -> dict:
    pricing_analysis = pricing_analysis if isinstance(pricing_analysis, dict) else {}
    readiness = getattr(listing, "readiness_summary", None) or {}
    if not isinstance(readiness, dict):
        readiness = {}
    condition_data = listing.condition_data if isinstance(listing.condition_data, dict) else {}
    shipping_profile = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
    score = 0
    blockers = list(readiness.get("blockers") or [])
    warnings = list(readiness.get("warnings") or [])

    if listing.title:
        score += 10
    if listing.description and len(listing.description.strip()) >= 30:
        score += 10
    if listing.category_id or listing.category_suggestion:
        score += 8
    if listing.item_specifics:
        score += 8
    if pricing_analysis.get("current_price"):
        score += 10
    if (pricing_analysis.get("price_confidence") or 0) >= 0.6:
        score += 10
    if readiness.get("actual_image_count"):
        score += 12
    if readiness.get("primary_image_present"):
        score += 6
    if not condition_data.get("operator_review_required", True):
        score += 8
    if not shipping_profile.get("manual_measurement_needed", True):
        score += 8
    if listing.status == "ready":
        score += 5
    if listing.ebay_listing_id:
        score = 100

    if listing.ebay_listing_id:
        status = "published"
    elif blockers:
        status = "blocked"
    elif listing.needs_review or listing.restricted_review_required:
        status = "needs_review"
    elif (pricing_analysis.get("price_confidence") or 0) < 0.45:
        status = "research_partial"
    elif not shipping_profile.get("manual_measurement_needed", True):
        status = "ready_for_ebay"
    else:
        status = "ready_for_facebook"

    ready_for_publish_queue = status in {"ready_for_ebay", "ready_for_facebook"} and not blockers
    return {
        "score": max(0, min(score, 100)),
        "status": status if not ready_for_publish_queue else "ready_for_publish_queue",
        "blockers": blockers,
        "warnings": warnings,
        "ready_for_ebay": not blockers and not shipping_profile.get("manual_measurement_needed", True) and bool(pricing_analysis.get("current_price")),
        "ready_for_facebook": not blockers and bool(pricing_analysis.get("current_price")) and bool(listing.description) and bool((listing.image_urls or [])),
        "ready_for_publish_queue": ready_for_publish_queue,
    }


def validate_marketplace_readiness(*, listing: Listing, marketplace: str, pricing_analysis: dict | None = None) -> list[str]:
    pricing_analysis = pricing_analysis if isinstance(pricing_analysis, dict) else {}
    readiness = getattr(listing, "readiness_summary", None) or summarize_listing_readiness(
        listing_images=listing.listing_images,
        condition_data=listing.condition_data,
        shipping_profile=listing.shipping_profile,
        listing={
            "category_id": listing.category_id,
            "category_suggestion": listing.category_suggestion,
            "listing_price": listing.listing_price,
            "suggested_price": listing.suggested_price,
        },
    )
    if not isinstance(readiness, dict):
        readiness = {}
    blockers = list(readiness.get("blockers") or [])
    shipping = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}

    if not listing.title or len(listing.title.strip()) < 8:
        blockers.append("Title missing or too short")
    if len(str(listing.title or "")) > 80 and marketplace == "ebay":
        blockers.append("eBay title exceeds 80 characters")
    if not (listing.listing_price or listing.suggested_price or pricing_analysis.get("recommended_price")):
        blockers.append("Price missing")
    if (listing.quantity or 0) <= 0:
        blockers.append("Quantity must be at least 1")
    if not listing.condition:
        blockers.append("Condition missing")
    if not (listing.image_urls or []):
        blockers.append("Photos missing")
    if marketplace == "ebay":
        if not (listing.category_id or listing.category_suggestion):
            blockers.append("eBay category missing")
        if not listing.item_specifics:
            blockers.append("eBay item specifics missing")
        if shipping.get("manual_measurement_needed", True):
            blockers.append("Shipping weight or dimensions still need review")
    if marketplace == "facebook":
        if not listing.description:
            blockers.append("Facebook description missing")
    return sorted(set(blockers))
