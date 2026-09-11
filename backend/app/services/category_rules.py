from __future__ import annotations

import re


_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
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


def suggest_category_from_text(*values: str | None) -> tuple[str, str]:
    searchable = " ".join(str(value or "") for value in values).lower()
    if not searchable.strip():
        return "Other > Needs category review", "needs_review"
    for keywords, category in _CATEGORY_RULES:
        if any(re.search(rf"\b{re.escape(keyword.strip())}\b", searchable) for keyword in keywords):
            return category, "keyword_rules"
    return "Other > Needs category review", "needs_review"
