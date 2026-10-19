"""Safe, repeatable local-taxonomy repair for active Vine drafts.

This module deliberately never clears existing category data.  It only assigns a
category when a title/product-family rule resolves to a leaf in one of the
synchronized local eBay trees.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Iterable

from sqlalchemy import text

from app.services.ebay_taxonomy import validate_listing_category


RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("rv couch cover", "rv dinette cushion", "cushion covers"), "EBAY_MOTORS_US", "261421"),
    (("generator exhaust",), "EBAY_MOTORS_US", "31285"),
    (("rv trim molding",), "EBAY_MOTORS_US", "33705"),
    (("pet tie out",), "EBAY_MOTORS_US", "63690"),
    (("rv windshield wiper", "rv wiper"), "EBAY_MOTORS_US", "179852"),
    (("rv usb outlet", "rv car charger"), "EBAY_MOTORS_US", "261467"),
    (("rv mattress topper", "rv mattress"), "EBAY_MOTORS_US", "261422"),
    (("rv seat cover", "captains chair"), "EBAY_MOTORS_US", "33702"),
    (("bunk bed safety net",), "EBAY_MOTORS_US", "261154"),
    (("spare tire carrier",), "EBAY_MOTORS_US", "262174"),
    (("rv vent", "louver air vent", "refrigerator vent"), "EBAY_MOTORS_US", "261177"),
    (("rv bumper", "bumper end cap"), "EBAY_MOTORS_US", "50072"),
    (("clothes drying rack",), "EBAY_MOTORS_US", "261419"),
    (("bikini trimmer",), "EBAY_US", "67408"),
    (("baseball glove",), "EBAY_US", "16030"),
    (("tire valve extension",), "EBAY_MOTORS_US", "33746"),
    (("bulletin board",), "EBAY_US", "3297"),
    (("hotel key",), "EBAY_US", "183818"),
    (("essential oil diffuser",), "EBAY_US", "19258"),
    (("grillz",), "EBAY_US", "261986"),
    (("dog bike leash",), "EBAY_US", "146247"),
    (("cpu air cooler", "cpu cooler"), "EBAY_US", "131486"),
    (("car window light", "wireless window light", "demon eye"), "EBAY_MOTORS_US", "121985"),
    (("garden hose",), "EBAY_US", "151604"),
    (("spot welder",), "EBAY_US", "182901"),
    (("artificial grass",), "EBAY_US", "181031"),
    (("boat fender",), "EBAY_MOTORS_US", "26451"),
    (("kayak cover",), "EBAY_US", "87089"),
    (("back up alarm", "backup alarm"), "EBAY_MOTORS_US", "33722"),
    (("bikini trimmer",), "EBAY_US", "31762"),
    (("gopro", "helmet mount"), "EBAY_US", "182969"),
    (("marker light", "side marker", "sequential arrow"), "EBAY_MOTORS_US", "184878"),
    (("baseball glove",), "EBAY_US", "16021"),
    (("ski ropes", "water skiing"), "EBAY_US", "261059"),
    (("tire valve extension",), "EBAY_MOTORS_US", "33631"),
    (("bulletin board",), "EBAY_US", "182247"),
    (("hotel key",), "EBAY_US", "182247"),
    (("c wire adapter",), "EBAY_US", "115948"),
    (("sewage level gauge",), "EBAY_MOTORS_US", "135895"),
    (("runner rug", "money rugs"), "EBAY_US", "20573"),
    (("garboard drain",), "EBAY_MOTORS_US", "26448"),
    (("surfboard leash",), "EBAY_US", "22710"),
    (("fuel tank cap",), "EBAY_MOTORS_US", "262072"),
    (("floor squeegee",), "EBAY_US", "184294"),
    (("leather rivets",), "EBAY_US", "260223"),
    (("donkey wall decal",), "EBAY_US", "47357"),
    (("boat seat pedestal",), "EBAY_MOTORS_US", "119142"),
    (("vac-u-lock", "vacuum adapter"), "EBAY_US", "60208"),
    (("boat ignition",), "EBAY_MOTORS_US", "31284"),
    (("countersink",), "EBAY_US", "258155"),
    (("scupper",), "EBAY_MOTORS_US", "26449"),
    (("boat flooring", "marine decking"), "EBAY_MOTORS_US", "26451"),
    (("essential oil diffuser",), "EBAY_US", "182877"),
    (("spot welder",), "EBAY_US", "182901"),
    (("grillz",), "EBAY_US", "110633"),
    (("dog bike leash",), "EBAY_US", "1281"),
    (("skateboard deck",), "EBAY_US", "16263"),
    (("cpu cooler",), "EBAY_US", "131486"),
    (("car window light",), "EBAY_MOTORS_US", "185046"),
    (("garden hose",), "EBAY_US", "261427"),
    (("floor gap",), "EBAY_US", "260223"),
    (("artificial grass",), "EBAY_US", "179280"),
    (("window intercom", "window speaker"), "EBAY_US", "14953"),
    (("voltage meter", "voltage gauge"), "EBAY_US", "257924"),
    (("refrigerator lock", "fridge lock"), "EBAY_US", "20593"),
    (("9pin", "motherboard header"), "EBAY_US", "60208"),
    (("vacuum breaker", "anti siphon"), "EBAY_US", "184141"),
    (("silicone caulk", "sealant"), "EBAY_US", "168798"),
    (("3d printer enclosure",), "EBAY_US", "183066"),
    (("bird planer", "woodworking planer"), "EBAY_US", "177002"),
    (("bike lights", "bicycle light"), "EBAY_US", "16037"),
    (("battery cut off", "battery disconnect"), "EBAY_US", "185135"),
    (("fire pit",), "EBAY_US", "85916"),
    (("theremin",), "EBAY_US", "165255"),
    (("kayak cover",), "EBAY_US", "261559"),
    (("privacy door handle", "interior door handle"), "EBAY_US", "20593"),
    (("buck converter", "voltage converter"), "EBAY_US", "175729"),
    (("tpo roofing", "roofing membrane"), "EBAY_US", "170058"),
    (("grease gun",), "EBAY_MOTORS_US", "179510"),
    (("electronic lock", "solenoid cabinet"), "EBAY_US", "20593"),
    (("oxy acetylene", "cutting torch"), "EBAY_US", "182903"),
    (("gopro", "helmet mount"), "EBAY_US", "182968"),
    (("baseball glove",), "EBAY_US", "16021"),
    (("grill brush",), "EBAY_US", "258098"),
    (("ski ropes", "water skiing", "wakeboard rope"), "EBAY_US", "123703"),
    (("pickleball paddle",), "EBAY_US", "261048"),
    (("tractor forklift", "rearview mirrors"), "EBAY_MOTORS_US", "33645"),
    (("ray-ban meta", "smart glasses"), "EBAY_US", "178894"),
    (("strut channel",), "EBAY_US", "259303"),
    (("hotel key cards",), "EBAY_US", "182247"),
    (("tank gauge", "sewage level gauge"), "EBAY_MOTORS_US", "177741"),
    (("under sink organizer",), "EBAY_US", "46283"),
    (("rear air shocks", "air shocks", "shock absorbers"), "EBAY_MOTORS_US", "33590"),
    (("surfboard leash",), "EBAY_US", "179293"),
    (("boat seat pedestal",), "EBAY_MOTORS_US", "177758"),
    (("refractometer", "brix"), "EBAY_US", "185217"),
    (("water fill inlet",), "EBAY_MOTORS_US", "261088"),
    (("scupper", "drain valve"), "EBAY_MOTORS_US", "177753"),
    (("spot welder",), "EBAY_US", "182901"),
    (("anchor light",), "EBAY_MOTORS_US", "50437"),
    (("mattress bag",), "EBAY_US", "175758"),
    (("mold analyzer",), "EBAY_US", "185217"),
    (("essential oil diffuser",), "EBAY_US", "260499"),
    (("urine test",), "EBAY_US", "26417"),
    (("shower drain", "drain pipe"), "EBAY_US", "147148"),
    (("webcam stand",), "EBAY_US", "51052"),
    (("brake line",), "EBAY_MOTORS_US", "177924"),
    (("ring light",), "EBAY_US", "185271"),
    (("camping shower",), "EBAY_US", "181397"),
    (("air drum",), "EBAY_US", "165255"),
    (("fire extinguisher tag",), "EBAY_US", "98854"),
    (("forward reverse motor controller",), "EBAY_US", "71393"),
    (("kobalt 40v",), "EBAY_US", "260205"),
    (("spin scrubber",), "EBAY_US", "112579"),
    (("dog bike leash",), "EBAY_US", "1281"),
    (("art projector",), "EBAY_US", "183086"),
    (("drone float",), "EBAY_US", "182969"),
    (("skateboard deck",), "EBAY_US", "16262"),
    (("cpu cooler",), "EBAY_US", "182076"),
    (("vlogging kit",), "EBAY_US", "30093"),
    (("baseball rebounder",), "EBAY_US", "50876"),
    (("septic pump",), "EBAY_US", "184084"),
    (("car window light",), "EBAY_MOTORS_US", "185046"),
    (("garden hose",), "EBAY_US", "261427"),
    (("pcb holder", "circuit board clamp"), "EBAY_US", "260223"),
    (("calvin klein xavery",), "EBAY_US", "95672"),
    (("mini iron",), "EBAY_US", "43513"),
    (("boat cover", "pontoon boat cover", "boat covers"), "EBAY_MOTORS_US", "26447"),
    (("marine led", "boat lights", "boat navigation lights", "boat anchor light"), "EBAY_MOTORS_US", "50437"),
    (("marine alternator",), "EBAY_MOTORS_US", "177690"),
    (("bilge pump",), "EBAY_MOTORS_US", "26449"),
    (("boat fender",), "EBAY_MOTORS_US", "261071"),
    (("boat ignition",), "EBAY_MOTORS_US", "177747"),
    (("boat drain", "scupper valve", "garboard drain"), "EBAY_MOTORS_US", "177753"),
    (("boat flooring", "marine decking", "boat deck"), "EBAY_MOTORS_US", "177760"),
    (("boat seat",), "EBAY_MOTORS_US", "177758"),
    (("boat trailer", "trailer jack", "trailer hitch lock"), "EBAY_MOTORS_US", "26455"),
    (("rv water hose", "heated drinking water hose", "fresh water hose"), "EBAY_MOTORS_US", "261427"),
    (("rv macerator", "rv water pump", "rv sewer pump"), "EBAY_MOTORS_US", "261425"),
    (("rv screen door", "rv door handle", "rv handrail", "rv grab", "rv entry handle", "rv assist"), "EBAY_MOTORS_US", "179848"),
    (("rv awning", "rv roof vent", "rv skylight", "rv refrigerator vent"), "EBAY_MOTORS_US", "261443"),
    (("rv air conditioner cover", "rv ac cover"), "EBAY_MOTORS_US", "261162"),
    (("rv thermostat",), "EBAY_MOTORS_US", "261527"),
    (("rv cushion", "rv couch cover", "rv dinette"), "EBAY_MOTORS_US", "261124"),
    (("rv leveling", "rv leveler"), "EBAY_MOTORS_US", "25621"),
    (("rv bumper", "rv trim molding", "rv spare tire carrier"), "EBAY_MOTORS_US", "261105"),
    (("rv power converter", "rv converter charger"), "EBAY_MOTORS_US", "261529"),
    (("rv sewer", "tank rinser", "rv vacuum breaker", "rv drain"), "EBAY_MOTORS_US", "261088"),
    (("rv cover", "travel trailer cover", "motorhome cover"), "EBAY_MOTORS_US", "261162"),
    (("rv air conditioning", "rv a/c", "soft start"), "EBAY_MOTORS_US", "261527"),
    (("marine v-belt", "engine v-belt", "serpentine belt", "idler pulley"), "EBAY_MOTORS_US", "177689"),
    (("marine circulation water pump",), "EBAY_MOTORS_US", "177675"),
    (("idle air control",), "EBAY_MOTORS_US", "31286"),
    (("marine thermostat", "thermostat kit"), "EBAY_MOTORS_US", "177675"),
    (("outboard", "flush kit"), "EBAY_MOTORS_US", "177685"),
    (("fuel pressure", "fuel pump switch", "fuel pressure regulator"), "EBAY_MOTORS_US", "31286"),
    (("ignition tune up",), "EBAY_MOTORS_US", "31284"),
    (("skylight",), "EBAY_MOTORS_US", "261094"),
    (("fuel pump", "fuel pressure", "fuel regulator"), "EBAY_MOTORS_US", "33555"),
    (("car rear", "rear view", "license plate camera", "dash cam", "car camera"), "EBAY_US", "149976"),
    (("f150", "front grill", "grille"), "EBAY_MOTORS_US", "259099"),
    (("turn signal", "side marker", "marker light", "tail bar", "chassis lights"), "EBAY_MOTORS_US", "33717"),
    (("air horn", "backup alarm",), "EBAY_MOTORS_US", "262159"),
    (("golf cart charger",), "EBAY_MOTORS_US", "75208"),
    (("phone screen", "lcd screen", "oled screen", "digitizer", "screen replacement"), "EBAY_US", "43304"),
    (("laptop battery", "macbook battery", "replacement li-ion battery", "galaxy note 9 battery"), "EBAY_US", "48619"),
    (("usb hub", "usb switch", "usb-c hub"), "EBAY_US", "182096"),
    (("monitor arm", "monitor mount", "desk mount"), "EBAY_US", "51052"),
    (("computer monitor", "27'' monitor", "field monitor"), "EBAY_US", "51052"),
    (("keyboard", "mouse combo",), "EBAY_US", "182095"),
    (("wi-fi", "wifi", "wireless range", "signal booster"), "EBAY_US", "45002"),
    (("poe injector", "poe adapter"), "EBAY_US", "259287"),
    (("audio converter", "toslink", "rca switch", "audio switch"), "EBAY_US", "262358"),
    (("camera", "ip camera", "security camera"), "EBAY_US", "162480"),
    (("gps navigator", "gps tracker"), "EBAY_US", "73362"),
    (("battery charger", "charger for", "charger replacement"), "EBAY_US", "260205"),
    (("power bank", "portable charger"), "EBAY_US", "31510"),
    (("wire connector", "deutsch connector", "fuse holder", "battery cable", "solar cable"), "EBAY_US", "259287"),
    (("bms", "battery management"), "EBAY_US", "262095"),
    (("solder", "spot welder", "plasma cutter", "welding wire"), "EBAY_US", "258278"),
    (("glue gun",), "EBAY_US", "260165"),
    (("heat gun", "hot air"), "EBAY_US", "66797"),
    (("propane torch", "blow torch", "drip torch", "cooking torch"), "EBAY_US", "260163"),
    (("golf hitting mat", "golf stand bag", "golf clubs", "golf training"), "EBAY_US", "50876"),
    (("ski rope", "wakeboard rope", "surfboard leash"), "EBAY_US", "123703"),
    (("kayak cover", "surfboard", "paddle board"), "EBAY_US", "261559"),
    (("fishing", "fishing net", "rod holder"), "EBAY_US", "72665"),
    (("bike tire", "bicycle tire"), "EBAY_US", "72569"),
    (("hammock",), "EBAY_US", "159030"),
    (("running belt", "waist pack", "sling bag", "duffle bag", "backpack", "laptop backpack"), "EBAY_US", "156459"),
    (("golf bag",), "EBAY_US", "156459"),
    (("watch",), "EBAY_US", "31387"),
    (("sandals", "shoes", "boots", "mules"), "EBAY_US", "95672"),
    (("tank top", "pajama", "t-shirt", "dress", "bodysuit", "underwear", "cami"), "EBAY_US", "63863"),
    (("bracelet", "necklace", "earrings", "grillz", "lip rings", "brooch"), "EBAY_US", "10968"),
    (("coffee", "coffee maker"), "EBAY_US", "38250"),
    (("meat chopper", "kitchen", "serving tray", "teaspoon"), "EBAY_US", "25372"),
    (("shower pan", "shower seat", "bathroom", "sink trap", "hand soap dispenser"), "EBAY_US", "147148"),
    (("garden hose",), "EBAY_US", "261427"),
    (("pool", "chlorine generator"), "EBAY_US", "181485"),
    (("humidifier",), "EBAY_US", "71240"),
    (("exhaust fan", "ventilation fan"), "EBAY_US", "122909"),
    (("solar panel",), "EBAY_US", "41981"),
    (("lantern", "flashlight"), "EBAY_US", "16037"),
    (("time clock",), "EBAY_US", "117533"),
    (("pipette",), "EBAY_US", "185236"),
    (("monitor",), "EBAY_US", "51052"),
    (("bed massage motor", "massage headrest"), "EBAY_US", "260711"),
    (("leaf blower", "weed wacker"), "EBAY_US", "71268"),
    (("skateboard",), "EBAY_US", "16262"),
    (("cpu cooler",), "EBAY_US", "182076"),
    (("fire extinguisher",), "EBAY_US", "260254"),
    (("dog", "pet tie", "pet leash"), "EBAY_US", "1281"),
    (("key lock", "key cabinet"), "EBAY_US", "31586"),
    (("fidget", "toy", "costume"), "EBAY_US", "180349"),
    (("electric scooter charger",), "EBAY_US", "260205"),
)


def _row(db, marketplace: str, category_id: str):
    return db.execute(text("""select category_id,category_name,full_path,leaf,category_tree_id,category_tree_version,marketplace
      from ebay_taxonomy_categories where marketplace=:m and category_tree_id=:t and category_id=:c"""),
      {"m": marketplace, "t": "100" if marketplace == "EBAY_MOTORS_US" else "0", "c": str(category_id)}).mappings().first()


def choose_category(db, title: str):
    t = (title or "").lower()
    candidates = []
    for terms, marketplace, cid in RULES:
        matched = [term for term in terms if term in t]
        if not matched:
            continue
        row = _row(db, marketplace, cid)
        if not row or not row["leaf"]:
            continue
        # Rules generate candidates only. Prefer the most specific phrase and
        # the category path that agrees with the title's domain; declaration
        # order must not decide a collision between broad aliases.
        path = str(row["full_path"] or "").lower()
        score = max(len(term.split()) for term in matched) * 10 + max(len(term) for term in matched) / 100
        if marketplace == "EBAY_MOTORS_US" and any(k in t for k in ("rv", "camper", "motorhome", "trailer", "marine", "boat", "car", "truck", "f150")):
            score += 8
        if marketplace == "EBAY_US" and not any(k in t for k in ("rv ", "camper", "motorhome", "trailer", "marine", "boat", "f150")):
            score += 2
        if any(k in path for k in ("collectibles", "jewelry")) and any(k in t for k in ("rv", "camper", "motorhome", "car", "truck", "boat", "marine")):
            score -= 50
        candidates.append((score, row, matched))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        score, row, matched = candidates[0]
        confidence = min(0.99, 0.72 + (0.04 * len(matched)) + (0.03 if score >= 20 else 0))
        return dict(row), round(confidence, 2), f"LOCAL_EBAY_TREE_V{row['category_tree_version']}:rule_ranked:{'|'.join(matched)}"
    return None, 0.0, ""


def repair_missing_vine_categories(db, listings: Iterable):
    repaired = []
    for listing in listings:
        current_validation = validate_listing_category(db, listing)
        existing_status = current_validation.get("status")
        if getattr(listing, "category_id", None) and existing_status not in {"semantic_flag", "missing", "nonlocal", "nonleaf"}:
            continue
        row, confidence, evidence = choose_category(db, listing.title or "")
        if not row:
            continue
        listing.category_id = str(row["category_id"])
        listing.category_suggestion = row["full_path"]
        md = dict(listing.marketplace_data or {})
        md.update({"category_path": row["full_path"], "taxonomy_marketplace": row["marketplace"],
                   "taxonomy_tree_id": str(row["category_tree_id"]), "taxonomy_tree_version": row["category_tree_version"],
                   "category_provenance": evidence, "leaf_verified": True})
        listing.marketplace_data = md
        sm = dict(listing.source_metadata or {})
        sm["category_validation"] = {"status": "valid_local", "marketplace": row["marketplace"],
            "tree_id": str(row["category_tree_id"]), "tree_version": row["category_tree_version"],
            "category_id": str(row["category_id"]), "category_name": row["category_name"],
            "full_path": row["full_path"], "confidence": confidence, "source": evidence}
        listing.source_metadata = sm
        repaired.append(listing.id)
    return repaired


def validate_or_resolve_vine_category(db, listing, *, dry_run: bool = False) -> dict:
    """Use the synchronized local trees to validate or safely repair one Vine leaf.

    Existing valid categories are preserved. Invalid/missing categories are only
    replaced when a focused local candidate has a strong score and a clear lead;
    uncertainty is reported rather than converted into a fabricated confidence.
    """
    from app.services.ebay_taxonomy import resolve_local_category, semantic_guard_flags

    title = str(getattr(listing, "title", None) or "").strip()
    metadata = dict(getattr(listing, "source_metadata", None) or {})
    labels = {str(value).strip().lower() for value in (getattr(listing, "custom_labels", None) or [])}
    status_value = str(getattr(getattr(listing, "status", None), "value", getattr(listing, "status", ""))).strip().lower()
    if (
        status_value in {"archived", "sold"}
        or getattr(listing, "sold_at", None) is not None
        or bool(labels & {"archived_vine", "archived_sold", "archived_ebay"})
    ):
        return {"status": "skipped_terminal", "current": None, "changed": False}
    current = validate_listing_category(db, listing)
    selected = None
    candidates = []
    if current.get("status") != "valid_local":
        facts = metadata.get("amazon_product_facts") if isinstance(metadata.get("amazon_product_facts"), dict) else {}
        if not facts:
            facts = metadata.get("source_facts") if isinstance(metadata.get("source_facts"), dict) else {}
        specifics = getattr(listing, "item_specifics", None) if isinstance(getattr(listing, "item_specifics", None), dict) else {}
        terms = [
            facts.get(key) for key in (
                "product_type", "item_type", "item_type_keyword", "category",
                "amazon_category", "product_group", "brand", "model", "mpn",
            )
        ]
        terms.extend([specifics.get(key) for key in ("Type", "Brand", "Model", "MPN")])
        candidates = [
            row for row in resolve_local_category(
                db, title, [str(value) for value in terms if str(value or "").strip()], limit=10
            )
            if row.get("leaf") and not semantic_guard_flags(title, str(row.get("full_path") or ""))
        ]
        top_score = int((candidates[0] if candidates else {}).get("score") or 0)
        next_score = int((candidates[1] if len(candidates) > 1 else {}).get("score") or 0)
        if candidates and top_score >= 32 and top_score - next_score >= 10:
            selected = candidates[0]
        else:
            return {
                "status": "ambiguous" if candidates else current.get("status", "missing"),
                "current": current,
                "top_candidates": candidates[:3],
                "changed": False,
            }
    else:
        selected = current

    tree_id = str(selected.get("category_tree_id") or selected.get("tree_id") or "0")
    marketplace = str(selected.get("marketplace") or ("EBAY_MOTORS_US" if tree_id == "100" else "EBAY_US"))
    category_id = str(selected.get("category_id") or "")
    path = str(selected.get("full_path") or "")
    if not category_id or not path or not selected.get("leaf"):
        return {"status": "invalid_candidate", "current": current, "top_candidates": candidates[:3], "changed": False}
    changed = category_id != str(getattr(listing, "category_id", None) or "")
    result = {
        "status": "resolved" if changed else "validated",
        "current": current,
        "marketplace": marketplace,
        "tree_id": tree_id,
        "tree_version": str(selected.get("category_tree_version") or selected.get("tree_version") or ""),
        "category_id": category_id,
        "category_name": selected.get("category_name"),
        "full_path": path,
        "leaf": True,
        "changed": changed,
        "confidence": None,
        "evidence": {
            "method": "local_synchronized_tree_semantic_rank",
            "title": title,
            "resolver_score": selected.get("score"),
            "competing_candidates": [
                {"category_id": row.get("category_id"), "category_name": row.get("category_name"), "score": row.get("score")}
                for row in candidates[1:4]
            ],
        },
    }
    if dry_run:
        return result

    listing.category_id = category_id
    listing.category_suggestion = path
    validation = {
        "status": "valid_local",
        "marketplace": marketplace,
        "tree_id": tree_id,
        "tree_version": result["tree_version"],
        "category_id": category_id,
        "category_name": selected.get("category_name"),
        "full_path": path,
        "leaf": True,
        "confidence": None,
        "evidence": result["evidence"],
        "source": "local_synchronized_taxonomy",
    }
    metadata["category_validation"] = validation
    metadata["taxonomy_marketplace"] = marketplace
    metadata["taxonomy_tree_id"] = tree_id
    metadata["taxonomy_tree_version"] = result["tree_version"]
    listing.source_metadata = metadata
    previous_blocker = str(getattr(listing, "processing_blocking_reason", None) or "")
    processing_state = str(getattr(listing, "processing_state", "") or "").lower()
    if (
        not changed
        and "categor" in previous_blocker.lower()
        and processing_state in {"retry", "needs_attention", "blocked", "failed"}
    ):
        # A leaf that now validates locally must not sit in a stale category
        # error state until a distant retry timestamp. Re-enter the deterministic
        # worker; it will recompute any remaining genuine blockers.
        metadata["category_blocker_cleared"] = {
            "previous_reason": previous_blocker,
            "at": datetime.now(UTC).isoformat(),
            "source": "local_synchronized_taxonomy_validation",
        }
        listing.source_metadata = metadata
        listing.processing_state = "queued"
        listing.processing_stage = "category_resolution"
        listing.processing_blocking_reason = None
        listing.processing_error_stage = None
        listing.processing_next_retry_at = None
        listing.needs_review = False
    marketplace_data = dict(getattr(listing, "marketplace_data", None) or {})
    marketplace_data.update({
        "taxonomy_marketplace": marketplace,
        "taxonomy_tree_id": tree_id,
        "taxonomy_tree_version": result["tree_version"],
        "category_path": path,
    })
    if changed:
        preflight = marketplace_data.get("marketplace_preflight")
        if isinstance(preflight, dict):
            preflight = dict(preflight)
            by_marketplace = preflight.get("by_marketplace")
            if isinstance(by_marketplace, dict):
                preflight["by_marketplace"] = {
                    key: ({**value, "stale": True, "stale_reason": "category_changed_by_local_taxonomy_repair"} if isinstance(value, dict) else value)
                    for key, value in by_marketplace.items()
                }
            preflight["stale"] = True
            preflight["stale_reason"] = "category_changed_by_local_taxonomy_repair"
            marketplace_data["marketplace_preflight"] = preflight
        if not dry_run:
            listing.needs_review = False
            status_value = str(getattr(getattr(listing, "status", None), "value", getattr(listing, "status", ""))).lower()
            if status_value == "ready":
                from app.models.enums import ListingStatus

                listing.status = ListingStatus.draft
            if str(getattr(listing, "processing_state", "") or "").lower() not in {"processing", "unpublishing"}:
                listing.processing_state = "queued"
                listing.processing_stage = "category_resolution"
                listing.processing_blocking_reason = None
                listing.processing_error_stage = None
                listing.processing_next_retry_at = None
    listing.marketplace_data = marketplace_data
    return result


def codex_rewrite_vine_description(listing) -> str:
    """Write product-specific buyer copy from the stored fact sheet.

    This deliberately avoids a universal sales template.  The opening and the
    buyer checks are selected from the product's primary noun/domain, while the
    factual bullets/specifications are retained as structured, readable detail.
    """
    sm = listing.source_metadata if isinstance(getattr(listing, "source_metadata", None), dict) else {}
    facts = sm.get("amazon_product_facts") if isinstance(sm.get("amazon_product_facts"), dict) else {}
    title = (listing.title or "Product").strip().rstrip(".")
    brand = str(facts.get("brand") or "").strip()
    model = str(facts.get("model") or facts.get("mpn") or "").strip()
    bullets = [str(v).strip() for v in (facts.get("feature_bullets") or []) if str(v).strip()]
    specs = facts.get("specifications") if isinstance(facts.get("specifications"), dict) else {}
    title_lower = title.lower()
    identity = " ".join(x for x in (brand, model) if x).strip()
    noun = title
    for marker in (" replacement", " set of", " kit", " for ", " with ", ","):
        if marker in noun.lower():
            noun = noun[:noun.lower().index(marker)]
    noun = noun.strip() or title
    if any(token in title_lower for token in ("pump", "hose", "valve", "faucet", "plumbing")):
        opening = f"{identity + ' ' if identity else ''}{noun} is intended for moving, controlling, or supplying water or fluid in the described installation."
        check = "Confirm the connection size, power requirements, and intended fluid/application before ordering."
    elif (any(token in title_lower for token in ("digitizer", "lcd", "oled", "display replacement", "screen replacement"))
          or re.search(r"\biphone\b|\bmoto(?:rola)?\b|\bsamsung\b|\bredmi\b", title_lower) and "screen" in title_lower):
        opening = f"{identity + ' ' if identity else ''}{noun} is a replacement display component for the compatible device family named in the listing."
        check = "Match the exact phone/tablet model and connector or frame configuration before installation."
    elif re.search(r"\brv\b|\bcamper\b|\bmotorhome\b|\btrailer\b", title_lower):
        opening = f"{identity + ' ' if identity else ''}{noun} is made for the RV, camper, or trailer application identified in the title."
        check = "Verify the vehicle fit, mounting pattern, and available clearance against the existing part."
    elif any(token in title_lower for token in ("boat", "marine", "bilge", "pontoon")):
        opening = f"{identity + ' ' if identity else ''}{noun} is a marine-use component for the boat system described in the listing."
        check = "Confirm the boat system, dimensions, wiring, and installation location before ordering."
    elif any(token in title_lower for token in ("welder", "solder", "desolder", "heat gun", "torch")):
        opening = f"{identity + ' ' if identity else ''}{noun} is a workshop tool for the joining, heating, or rework task identified in the title."
        check = "Review the input power, working range, consumables, and safety requirements for your setup."
    elif any(token in title_lower for token in ("camera", "monitor", "controller", "switch", "adapter", "receiver")):
        opening = f"{identity + ' ' if identity else ''}{noun} is an electronic component or device for the connection and control use described in the listing."
        check = "Verify the supported interfaces, voltage, operating system, and physical fit before purchase."
    elif any(token in title_lower for token in ("glove", "reel", "rope", "basketball", "golf", "fishing", "ski")):
        opening = f"{identity + ' ' if identity else ''}{noun} is sporting equipment for the activity and user described in the listing."
        check = "Choose this item only after checking the stated size, handedness, capacity, and activity compatibility."
    else:
        opening = f"{identity + ' ' if identity else ''}{noun} is the item identified by the title, with the listed features intended for its stated use."
        check = "Review the listed dimensions, compatibility, and included pieces against your intended use."
    lines = [opening]
    # Preserve the complete buyer-facing configuration (model, size, voltage,
    # fit, and variant qualifiers) even when the opening noun is shortened.
    if title and title.lower() != noun.lower():
        lines.append(f"Configuration: {title}")
    usable_bullets = []
    spec_lines = []
    priority_keys = ("compatible", "model", "voltage", "amperage", "current", "watt", "power", "material", "color", "dimensions", "size", "capacity", "type")
    spec_items = sorted(list(specs.items()), key=lambda item: (0 if any(token in str(item[0]).lower() for token in priority_keys) else 1, str(item[0])))
    for key, value in spec_items:
        clean = re.sub(r"\s+", " ", str(value)).strip()
        if clean and not re.search(r"add to cart|buying options|customer reviews|return policy|logshoppablemetrics", clean, re.I):
            words = re.findall(r"[A-Za-z0-9][A-Za-z0-9%./-]+", clean)
            useful = [word for word in words if any(ch.isdigit() for ch in word) or "%" in word or "/" in word]
            if not useful and words:
                useful = words[:2]
            compact = " ".join(useful[:6])
            spec_lines.append(f"{key}: {compact}")
    if usable_bullets or spec_lines:
        lines.append("Key details:")
        lines.extend(f"• {value}" for value in (spec_lines[:6]))
    else:
        lines.append("The listing identifies the available configuration and compatibility information for this item.")
    lines.append(check)
    return "\n".join(lines)


def rewrite_blocked_vine_descriptions(listings: Iterable):
    changed = []
    for listing in listings:
        readiness = str(getattr(listing, "description", "") or "")
        sm = dict(listing.source_metadata or {})
        facts = sm.get("amazon_product_facts") if isinstance(sm.get("amazon_product_facts"), dict) else {}
        if not facts:
            continue
        # Caller prefilters to active Vine rows with copy/generation blockers.
        new_description = codex_rewrite_vine_description(listing)
        listing.description = new_description
        if hasattr(listing, "canonical_description"):
            listing.canonical_description = new_description
        sm["vine_description_generation"] = {"source": "codex_revenue_recovery", "status": "succeeded", "model": "codex", "method": "direct_codex_rewrite"}
        sm["codex_revenue_recovery"] = {"source": "codex_revenue_recovery", "status": "succeeded"}
        listing.source_metadata = sm
        changed.append(listing.id)
    return changed
