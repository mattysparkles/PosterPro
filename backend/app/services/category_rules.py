from __future__ import annotations

import re
from typing import Any


_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("portable toilet", "camping toilet", "rv toilet", "waste tank"), "Sporting Goods > Camping & Hiking > Camping Hygiene & Sanitation > Portable Toilets"),
    (("groin protector", "boxing cup", "boxing protective", "mma cup"), "Sporting Goods > Boxing & MMA > Protective Gear > Groin Protectors"),
    (("boxing headgear", "boxing gloves", "chest protector", "body protector", "shin guards"), "Sporting Goods > Boxing & MMA > Protective Gear"),
    (("pulse oximeter", "oxygen monitor", "spo2 monitor"), "Health & Beauty > Health Care > Medical & Mobility > Pulse Oximeters"),
    (("soldering station", "soldering iron", "hot air rework"), "Consumer Electronics > Electrical Equipment & Supplies > Soldering Equipment"),
    (("varsity jacket", "letterman jacket", "bomber jacket"), "Clothing, Shoes & Accessories > Men's Clothing > Coats, Jackets & Vests"),
    (("power bank", "portable charger", "battery pack"), "Consumer Electronics > Multipurpose Batteries & Power > Portable Chargers & Power Banks"),
    (("rv cover", "travel trailer cover", "motorhome cover"), "Automotive > RV, Trailer & Camper Parts & Accessories > Covers"),
    (("heated vest battery", "heated clothing", "heated jacket battery"), "Consumer Electronics > Multipurpose Batteries & Power > Portable Chargers & Power Banks"),
    (("dog house heater", "chicken coop heater"), "Pet Supplies > Dog Supplies > Other Dog Supplies"),
    (("posture corrector", "back brace", "shoulder brace"), "Health & Beauty > Health Care > Braces & Supports"),
    (("golf simulator impact screen", "golf impact screen"), "Sporting Goods > Golf > Training Aids"),
    (("selfie stick", "selfie pole"), "Cell Phones & Accessories > Cell Phone Accessories > Selfie Sticks"),
    (("windshield curtain", "rv privacy curtain"), "Automotive > RV, Trailer & Camper Parts & Accessories > Interior Accessories"),
    (("hot water recirculating pump", "water recirculation pump"), "Home & Garden > Plumbing & Fixtures > Pumps"),
    (("breakaway charger cable", "rocksmith cable"), "Video Game Accessories > Cables & Adapters"),
    (("misting nozzle", "terrarium misting"), "Pet Supplies > Reptile Supplies > Terrarium Accessories"),
    (("wallet tracker card", "find my tracker"), "Consumer Electronics > GPS & Accessories > GPS Trackers"),
    (("surge protector", "rv surge protector"), "Consumer Electronics > Power Protection > Surge Protectors"),
    (("cold therapy machine", "ice compression therapy"), "Health & Beauty > Health Care > Other Health Care"),
    (("laptop stand", "laptop riser", "notebook stand"), "Computers/Tablets & Networking > Laptop/Notebook Accessories > Stands & Risers"),
    (("motorized roller shade", "roller shades", "roller shade", "window shade"), "Home & Garden > Window Treatments > Blinds & Shades"),
    (("poe splitter", "ethernet poe", "gigabit poe"), "Computers/Tablets & Networking > Enterprise Networking, Servers > Power over Ethernet"),
    (("ottoman", "foot stool", "footstool"), "Home & Garden > Furniture > Living Room Furniture > Ottomans"),
    (("kvm switch",), "Computers/Tablets & Networking > KVM Switches"),
    (("bird house", "birdhouse", "martin house"), "Home & Garden > Yard, Garden & Outdoor Living > Bird Houses"),
    (("cpu cooler", "liquid cpu cooler", "liquid cooling", "computer cooler"), "Computers/Tablets & Networking > Computer Components > Fans, Heatsinks & Cooling Systems"),
    (("drip irrigation", "irrigation kit", "plant waterer"), "Home & Garden > Watering Equipment > Drip Irrigation"),
    (("wall mirror", "framed mirror", "bathroom mirror", "mirror"), "Home & Garden > Home Décor > Mirrors"),
    (("lithium battery charger", "battery charger", "battery chargers"), "Consumer Electronics > Multipurpose Batteries & Power > Battery Chargers"),
    (("ironing machine", "automatic ironing", "clothing steamer", "garment steamer"), "Home & Garden > Household Supplies & Cleaning > Irons & Garment Steamers"),
    (("rv bumper", "tote tank carrier", "waste tank holder", "rv tote tank"), "Automotive > RV, Trailer & Camper Parts & Accessories > Exterior Accessories > Cargo Racks"),
    (("pool pump", "spa pump", "pool filter pump", "pool booster pump"), "Home & Garden > Yard, Garden & Outdoor Living > Pools & Spas > Pool Pumps"),
    (("coffee maker", "espresso machine", "keurig", "coffee machine"), "Home & Garden > Kitchen, Dining & Bar > Small Kitchen Appliances > Coffee Machines"),
    (("vacuum", "robot vacuum"), "Home & Garden > Household Supplies & Cleaning > Vacuum Cleaners"),
    (("grow light",), "Home & Garden > Lamps, Lighting & Ceiling Fans > Grow Light Kits"),
    (("pressure washer",), "Home & Garden > Yard, Garden & Outdoor Living > Outdoor Power Equipment > Pressure Washers"),
    (("air purifier",), "Home & Garden > Household Supplies & Cleaning > Air Purifiers"),
    (("fishing net", "landing net"), "Sporting Goods > Fishing > Fishing Accessories > Landing Nets"),
    (("propane gas detector", "rv gas detector"), "Automotive > RV, Trailer & Camper Parts & Accessories > Safety & Security"),
    (("rv seat cover", "rv seat covers", "motorhome seat cover", "captains chair"), "Automotive > Parts & Accessories > Interior Parts & Accessories > Seat Covers"),
    (("marker organizer", "pen organizer", "desktop storage rack"), "Office & School Supplies > Office Supplies > Desk Organization"),
    (("dog", "cat", "pet"), "Pet Supplies"),
    (("shampoo", "serum", "skin care", "skincare", "hair dryer", "hair styling"), "Health & Beauty"),
    (("keyboard", "mouse", "usb hub", "charger", "adapter", "router"), "Computers/Tablets & Networking"),
    (("toy", "dinosaur", "puzzle", "remote control"), "Toys & Hobbies"),
    (("camera", "camcorder", "lens"), "Cameras & Photo"),
)

# Verified eBay leaf IDs observed from the active taxonomy cache.  These are
# intentionally keyed by the canonical path (never by scraped source text),
# so deterministic Vine repairs can persist a real marketplace category ID
# without inventing one.  Unknown paths remain unresolved and are escalated to
# taxonomy search/AI rather than silently using a made-up ID.
_VERIFIED_CATEGORY_IDS: dict[str, str] = {
    "Sporting Goods > Camping & Hiking > Camping Hygiene & Sanitation > Portable Toilets": "181397",
    "Sporting Goods > Boxing & MMA > Protective Gear > Groin Protectors": "179778",
    "Sporting Goods > Boxing & MMA > Protective Gear": "36317",
    "Health & Beauty > Health Care > Medical & Mobility > Pulse Oximeters": "31465",
    "Clothing, Shoes & Accessories > Men's Clothing > Coats, Jackets & Vests": "57988",
    "Consumer Electronics > Electrical Equipment & Supplies > Soldering Equipment": "258278",
    "Consumer Electronics > Power Protection > Surge Protectors": "30",
    "Sporting Goods > Golf > Training Aids": "20580",
    "Home & Garden > Furniture > Living Room Furniture > Ottomans": "20490",
    "Home & Garden > Household Supplies & Cleaning > Irons & Garment Steamers": "43513",
    "Home & Garden > Watering Equipment > Drip Irrigation": "139909",
    "Computers/Tablets & Networking > KVM Switches": "182096",
    "Computers/Tablets & Networking > Laptop/Notebook Accessories > Stands & Risers": "116346",
    "Home & Garden > Yard, Garden & Outdoor Living > Bird Houses": "20502",
    "Home & Garden > Window Treatments > Blinds & Shades": "20585",
    # Top-level fallback leaves are also retained only where the live catalog
    # already has a verified ID for that exact canonical value.
    "Pet Supplies": "1284",
    "Home & Garden": "36956",
    "Computers/Tablets & Networking": "48618",
    "Health & Beauty": "1277",
    "Toys & Hobbies": "234",
    "Automotive": "11808",
    "Consumer Electronics > Multipurpose Batteries & Power > Battery Chargers": "48618",
    "Pet Supplies > Dog Supplies > Other Dog Supplies": "1283",
}


def verified_category_id(value: str | None) -> str | None:
    """Return an ID only for a canonical path already validated in taxonomy."""
    return _VERIFIED_CATEGORY_IDS.get(" ".join(str(value or "").split()))


def resolve_taxonomy_leaf(tree: dict[str, Any] | None, *facts: str | None) -> dict[str, str] | None:
    """Choose a likely real leaf from an eBay taxonomy tree without inventing IDs.

    This is deliberately conservative: source/policy noise and eBay test nodes
    are excluded, and weak matches return ``None`` for an AI/manual fallback.
    """
    if not isinstance(tree, dict):
        return None
    tokens = set(re.findall(r"[a-z0-9]+", " ".join(str(v or "") for v in facts).lower()))
    if not tokens:
        return None
    candidates: list[tuple[int, int, str, str]] = []

    def walk(node: dict[str, Any], path: str = "") -> None:
        category = node.get("category") if isinstance(node.get("category"), dict) else {}
        name = str(category.get("categoryName") or "").strip()
        category_id = str(category.get("categoryId") or "").strip()
        current_path = f"{path} > {name}" if path and name else name or path
        children = node.get("childCategoryTreeNodes") or []
        if category_id and name and not children:
            lowered = current_path.lower()
            if "test category" not in lowered and not is_source_noise_category(current_path):
                path_tokens = set(re.findall(r"[a-z0-9]+", lowered))
                overlap = sum(1 for token in tokens if any(
                    token == candidate or token.rstrip("s") == candidate.rstrip("s")
                    for candidate in path_tokens
                ))
                # Prefer specific leaves, while requiring at least two facts to
                # agree so a generic word such as "charger" cannot misclassify.
                if overlap >= 2:
                    candidates.append((overlap, len(path_tokens), category_id, current_path))
        for child in children:
            if isinstance(child, dict):
                walk(child, current_path)

    root = tree.get("rootCategoryNode") or tree
    if isinstance(root, dict):
        walk(root)
    if not candidates:
        return None
    _, _, category_id, path = max(candidates, key=lambda row: (row[0], row[1]))
    return {"category_id": category_id, "category_path": path.removeprefix("Root > "), "method": "taxonomy_search"}


def suggest_category_from_text(*values: str | None) -> tuple[str, str]:
    searchable = " ".join(str(value or "") for value in values).lower()
    if not searchable.strip():
        return "Other > Needs category review", "needs_review"
    for keywords, category in _CATEGORY_RULES:
        if any(re.search(rf"\b{re.escape(keyword.strip())}\b", searchable) for keyword in keywords):
            return category, "keyword_rules"
    return "Other > Needs category review", "needs_review"


def is_source_noise_category(value: str | None) -> bool:
    """Reject scraped navigation/policy text as a marketplace category hint."""
    text = " ".join(str(value or "").split()).lower()
    if not text:
        return True
    markers = ("amazon", "refund", "replacement", "free return", "delivery", "seller", "return policy", "read full", "update location", "see exceptions", "report an issue", "secure transaction")
    return any(marker in text for marker in markers) or text.startswith(("other", "read the full"))
