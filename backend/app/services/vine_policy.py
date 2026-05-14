from __future__ import annotations

from dataclasses import dataclass


RESTRICTION_RULES = {
    "adult": ["adult", "sex toy", "intimate", "vibrator", "lubricant"],
    "supplement": ["supplement", "vitamin", "gummy", "protein powder", "pre-workout"],
    "medical": ["medical", "cpap", "blood pressure", "diagnostic", "therapy", "brace"],
    "rfid-security": ["rfid", "copier", "signal jammer", "key fob programmer", "badge cloner"],
    "surveillance": ["spy", "hidden camera", "surveillance", "nanny cam", "gps tracker"],
    "weapon-adjacent": ["airsoft", "bb gun", "tactical", "knife", "holster", "crossbow"],
    "tobacco": ["cigarette", "rolling tray", "ashtray", "tobacco", "hookah"],
    "explosive-language": ["blast", "explosion", "detonator", "firework"],
    "battery-hazmat": ["lithium", "battery", "power bank", "hazmat", "flammable"],
}

CATEGORY_GUESSES = {
    "adult": "Adult",
    "supplement": "Supplement",
    "medical": "Medical",
    "rfid-security": "Security",
    "surveillance": "Surveillance",
    "weapon-adjacent": "Weapon Adjacent",
    "tobacco": "Tobacco",
    "explosive-language": "Hazmat",
    "battery-hazmat": "Battery",
}


@dataclass
class VinePolicyResult:
    restricted_review_required: bool
    restricted_reasons: list[str]
    detected_category_guess: str | None
    marketplace_allowed_status: str


def review_vine_product(product_name: str | None) -> VinePolicyResult:
    lowered = (product_name or "").strip().lower()
    reasons: list[str] = []
    category: str | None = None

    for reason, keywords in RESTRICTION_RULES.items():
        if any(keyword in lowered for keyword in keywords):
            reasons.append(reason)
            category = category or CATEGORY_GUESSES.get(reason)

    restricted = bool(reasons)
    return VinePolicyResult(
        restricted_review_required=restricted,
        restricted_reasons=reasons,
        detected_category_guess=category,
        marketplace_allowed_status="manual_review_required" if restricted else "allowed",
    )
