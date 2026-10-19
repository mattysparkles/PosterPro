"""Conservative Facebook Marketplace category and fulfilment selection.

Facebook does not expose a stable public taxonomy API to this integration, so
we deliberately use a small, evidence-led set of human-readable paths.  The
browser adapter must still match the rendered Facebook choice.  In particular,
Furniture is never a generic fallback: it is both semantically high risk and
usually incompatible with shipping.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


FURNITURE_EVIDENCE = (
    "table", "chair", "sofa", "couch", "dresser", "desk", "bed frame",
    "cabinet", "bookshelf", "nightstand", "ottoman", "armoire", "recliner",
)


@dataclass(frozen=True)
class FacebookCategoryDecision:
    category: str | None
    confidence: str
    source: str
    supports_shipping: bool
    rationale: str


def _text(listing: Any) -> str:
    specifics = getattr(listing, "item_specifics", None)
    specifics_text = " ".join(f"{key} {value}" for key, value in (specifics or {}).items()) if isinstance(specifics, dict) else ""
    source = getattr(listing, "source_metadata", None)
    source_text = " ".join(str(value) for value in (source or {}).values() if isinstance(value, (str, int, float))) if isinstance(source, dict) else ""
    return " ".join((str(getattr(listing, "title", "") or ""), str(getattr(listing, "description", "") or ""), specifics_text, source_text)).casefold()


def _contains(text: str, *terms: str) -> bool:
    return any(term in text for term in terms)


def listing_is_explicitly_local_only(listing: Any) -> bool:
    data = getattr(listing, "marketplace_data", None)
    data = data if isinstance(data, dict) else {}
    channel = (data.get("channels") or {}).get("facebook") if isinstance(data.get("channels"), dict) else {}
    shipping = data.get("shipping") if isinstance(data.get("shipping"), dict) else {}
    profile = getattr(listing, "shipping_profile", None)
    profile = profile if isinstance(profile, dict) else {}
    return bool(channel.get("local_pickup_only") or shipping.get("local_pickup_only") or profile.get("local_pickup_only"))


def listing_is_shippable(listing: Any) -> bool:
    if listing_is_explicitly_local_only(listing):
        return False
    profile = getattr(listing, "shipping_profile", None)
    profile = profile if isinstance(profile, dict) else {}
    if profile.get("oversize") or profile.get("local_pickup_recommended"):
        return False
    return True


def resolve_facebook_category(listing: Any, *, prefer_shipping_capable: bool = True, allow_local_pickup_only: bool = False) -> FacebookCategoryDecision:
    text = _text(listing)
    title = str(getattr(listing, "title", "") or "").casefold()
    # Strong product evidence precedes title-overlap/category carry-over.
    if _contains(text, "anti-snore", "anti snore", "pillow", "pillowcase"):
        return FacebookCategoryDecision("Home / Bedding / Pillows / Position Adjust", "high", "semantic_product_rule", True, "bedding/pillow evidence")
    if _contains(text, "access control", "rfid", "fingerprint card reader", "door controller", "keypad lock", "tt-lock", "tt lock"):
        return FacebookCategoryDecision("Electronics / Security / Access Control", "high", "semantic_product_rule", True, "access-control/security evidence")
    if _contains(text, "bms", "battery management", "pcb protection", "motor controller", "electronic controller", "circuit board"):
        return FacebookCategoryDecision("Electronics / Electrical Components", "high", "semantic_product_rule", True, "electronic-component evidence")
    if _contains(text, "mattress topper", "mattress pad", "mattress protector"):
        return FacebookCategoryDecision("Home / Bedding / Mattresses", "high", "semantic_product_rule", True, "bedding/mattress product evidence")
    if _contains(text, "heated drinking water hose", "rv water hose", "rv water service panel", "water fill valve"):
        return FacebookCategoryDecision("Home Improvement / Plumbing", "high", "semantic_product_rule", True, "plumbing and RV water-system evidence")
    if _contains(text, "golf cart led", "golf cart light", "club car", "utv windshield wiper", "utv wiper kit", "t-top sun shade", "bimini top", "boat sun shade"):
        return FacebookCategoryDecision("Automotive / Parts & Accessories", "high", "semantic_product_rule", True, "vehicle or marine accessory evidence")
    if _contains(text, "rv light", "led rv", "dome light", "wall sconce", "ceiling light"):
        return FacebookCategoryDecision("Home Improvement / Lighting", "high", "semantic_product_rule", True, "lighting evidence")
    if _contains(text, "camera", "doorbell", "alarm", "sensor", "zigbee", "wifi", "bluetooth", "charger", "printer", "projector", "scanner"):
        return FacebookCategoryDecision("Electronics", "medium", "semantic_product_rule", True, "consumer-electronics evidence")
    # Descriptions often say "for furniture", "bedroom", or "cabinet" as a
    # use case (paint sprayers are a real example).  Only a product title can
    # supply the strong, primary-product evidence needed for this high-risk
    # non-shipping category.
    if _contains(title, *FURNITURE_EVIDENCE):
        return FacebookCategoryDecision("Furniture", "medium", "strong_furniture_evidence", False, "explicit furniture evidence")

    # A source/category hint may be used only when it is not a furniture-like
    # catchall.  This prevents home, room, door, RV, or bedroom wording from
    # smuggling unrelated products into Furniture.
    suggestion = str(getattr(listing, "category_suggestion", "") or "").strip()
    suggestion_lower = suggestion.casefold()
    suspicious_source_hint = any(token in suggestion_lower for token in (">", "amazon", "protection plan", "free 30-day", "details"))
    if suggestion and not suggestion.isdigit() and "furniture" not in suggestion_lower and not suspicious_source_hint:
        return FacebookCategoryDecision(suggestion, "low", "source_category", True, "non-furniture source category")
    if prefer_shipping_capable and not allow_local_pickup_only:
        return FacebookCategoryDecision(None, "none", "shipping_guardrail", False, "no evidence-backed shipping-capable category")
    return FacebookCategoryDecision(None, "none", "unresolved", False, "no evidence-backed category")
