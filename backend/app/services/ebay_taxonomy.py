"""Cache-first EBAY_US taxonomy search over the normalized local tree."""
import re
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.models.models import Listing

def category_by_id(db: Session, category_id: str, tree_id: str = "0", marketplace: str = "EBAY_US"):
    from sqlalchemy import text
    return db.execute(text("select category_id,category_name,parent_category_id,leaf,full_path,depth,category_tree_version,marketplace,category_tree_id from ebay_taxonomy_categories where marketplace=:m and category_tree_id=:t and category_id=:c"), {"m": marketplace, "t": tree_id, "c": str(category_id)}).mappings().first()

def candidate_trees_for_listing(listing_or_title) -> list[tuple[str, str]]:
    """Return taxonomy domains in priority order for a product."""
    if isinstance(listing_or_title, str):
        title = str(listing_or_title or "").lower()
    else:
        source = getattr(listing_or_title, "source_metadata", None)
        source = source if isinstance(source, dict) else {}
        facts = source.get("amazon_product_facts") if isinstance(source.get("amazon_product_facts"), dict) else source.get("source_facts") or {}
        title = " ".join(str(value or "") for value in (
            getattr(listing_or_title, "title", ""), getattr(listing_or_title, "description", ""),
            facts.get("product_type") if isinstance(facts, dict) else None,
            facts.get("category") if isinstance(facts, dict) else None,
        )).lower()
    return [("EBAY_MOTORS_US", "100"), ("EBAY_US", "0")] if motors_path_is_semantically_required(title) else [("EBAY_US", "0"), ("EBAY_MOTORS_US", "100")]


def motors_path_is_semantically_required(title: str | None) -> bool:
    """Whether the evidence says this is a real fitment/vehicle part.

    Words such as RV, boat, car, or automotive are not enough.  Many ordinary
    consumer products mention an application (RV lights, cleaning guns, solar
    connectors) and must not be stranded in the Motors tree merely because the
    Inventory publisher cannot send tree-100 offers.
    """
    text = str(title or "").lower()
    # Require a vehicle-specific *product*, not an application word by itself.
    # Generic RV/boat/car mentions and generic nouns such as "storage" must
    # never select tree 100. Structured fitment evidence is checked separately
    # by listing_has_motors_semantics before preflight may call something true
    # Motors.
    product_contexts = (
        ("rv ", ("stabilizer jack", "ac soft starter", "wiper", "water pump", "vent", "awning", "sewer", "mattress", "dinette cushion", "cushion cover", "door lock", "generator exhaust", "black tank treatment", "holding tank gauge")),
        ("utv ", ("turn signal", "windshield wiper", "windshield", "jack", "brake", "alternator", "engine", "transmission")),
        ("trailer", ("circuit tester", "stabilizer jack", "light tester", "hitch", "jack", "brake", "wiring")),
        ("boat", ("bimini", "t-top", "outboard", "sterndrive", "fender", "bilge pump", "impeller", "anchor light", "navigation lights")),
        ("car roof", ("ski rack", "snowboard rack", "cargo carrier")),
    )
    if any(vehicle in text and any(product in text for product in products) for vehicle, products in product_contexts):
        return True
    specific_parts = (
        "mercruiser", "outboard", "sterndrive", "oem part", "vehicle specific fit", "side marker light assembly", "side marker/clearance lights", "clearance marker lights", "generator exhaust kit", "navigation lights", "holding tank gauge", "sewage level gauge", "fuel injector cleaner tester", "right reflector", "tpms valve stem kit", "valve stems & caps", "ignition coil", "windshield wiper blade",
        "fits ford", "fits chevy", "fits toyota", "fits honda", "transmission",
        "power steering pump", "water pump impeller", "shock absorber", "brake line",
        "alternator", "fuel injector", "marine engine", "ranger 800", "side-by-side",
        "f150", "chevy tahoe", "suburban",
    )
    explicit_vehicle_components = (
        ("reflector", ("automotive", "toyota", "rav4", "vehicle")),
        ("valve stems & caps", ("automotive", "tpms", "tire pressure")),
        ("ignition coil", ("automotive", "motorcraft", "engine")),
        ("wiper blade", ("automotive", "windshield", "trico")),
    )
    return any(term in text for term in specific_parts) or any(
        product in text and any(context in text for context in contexts)
        for product, contexts in explicit_vehicle_components
    )


def listing_has_motors_semantics(listing) -> bool:
    """Require specific automotive product semantics, not generic application words.

    The caller separately verifies the exact category marketplace/tree/leaf.
    This predicate accepts only a concrete vehicle-product pairing or
    automotive-specific product noun (e.g. RV stabilizer jack, UTV turn
    signal, alternator), not a bare ``RV``, ``boat``, ``car`` or ``storage``.
    Structured fitment and source breadcrumbs are also accepted corroboration.
    """
    source = getattr(listing, "source_metadata", None)
    source = source if isinstance(source, dict) else {}
    facts = source.get("amazon_product_facts") if isinstance(source.get("amazon_product_facts"), dict) else source.get("source_facts") or {}
    title_and_facts = " ".join(str(value or "") for value in (
        getattr(listing, "title", ""), getattr(listing, "description", ""),
        facts.get("product_type") if isinstance(facts, dict) else None,
        facts.get("category") if isinstance(facts, dict) else None,
    ))
    return motors_path_is_semantically_required(title_and_facts)


def search_local_leaves(db: Session, terms: list[str], tree_id: str = "0", limit: int = 25, marketplace: str = "EBAY_US"):
    from sqlalchemy import text
    terms = [str(t).strip().lower() for t in terms if str(t).strip()]
    if not terms: return []
    # Taxonomy search is local and repeat-heavy (a Vine batch resolves many
    # products against the same two trees). Loading each tree once per DB
    # session avoids hundreds of `%term%` table scans while preserving exact
    # leaf evidence and deterministic ranking.
    cache_key = f"posterpro.ebay_taxonomy.leaves:{marketplace}:{tree_id}"
    cached = db.info.get(cache_key)
    if cached is None:
        db.flush()
        cached = db.execute(text("select category_id,category_name,parent_category_id,leaf,full_path,depth,category_tree_version,marketplace,category_tree_id from ebay_taxonomy_categories where marketplace=:marketplace and category_tree_id=:tree and leaf=true"), {"marketplace": marketplace, "tree": tree_id}).mappings().all()
        cached = [dict(row) for row in cached]
        db.info[cache_key] = cached
    # Candidate generation is intentionally OR-based. Requiring every title
    # token to appear in a breadcrumb drops valid leaves such as RV grab
    # handles whose leaf is simply "Door Handles". Rank exact/direct leaf
    # names first, then semantic scoring chooses among these candidates.
    phrase = terms[0]
    matches = []
    for row in cached:
        name = str(row.get("category_name") or "").lower()
        path = str(row.get("full_path") or "").lower()
        if not any(term in name or term in path for term in terms):
            continue
        rank = 0 if name in {phrase, phrase + "s"} else 1 if name.startswith(phrase) else 2
        matches.append((rank, -int(row.get("depth") or 0), row))
    matches.sort(key=lambda value: (value[0], value[1], str(value[2].get("category_name") or "")))
    return [row for _rank, _depth, row in matches[:max(1, int(limit or 25))]]

def taxonomy_metadata(db: Session, tree_id: str = "0", marketplace: str | None = None):
    from sqlalchemy import text
    marketplace = marketplace or ("EBAY_MOTORS_US" if str(tree_id) == "100" else "EBAY_US")
    return db.execute(text("select category_tree_version,count(*) as nodes,sum(case when leaf then 1 else 0 end) as leaves,max(depth) as max_depth from ebay_taxonomy_categories where marketplace=:marketplace and category_tree_id=:tree group by category_tree_version"), {"tree": tree_id, "marketplace": marketplace}).mappings().first()


def semantic_guard_flags(title: str | None, category_path: str | None) -> list[str]:
    """Conservative post-ranking checks for obvious taxonomy collisions."""
    t = (title or "").lower()
    p = (category_path or "").lower()
    flags: list[str] = []
    # Category leaf names such as "Chips", "Color", and "Backpacks & Bags"
    # are common incidental title tokens. They are not product identity. These
    # hard contradictions prevent a low-information exact-word fallback from
    # being treated as a valid category and feed the normal second-pass repair.
    if any(term in t for term in ("rs485 transceiver", "max485", "max3485")) and any(term in p for term in ("snacks", "casino", "collectibles")):
        flags.append("electronic_transceiver_in_unrelated_chips_category")
    if "4wd actuator" in t and any(term in p for term in ("license plates", "collectible plates", "plate frames")):
        flags.append("4wd_actuator_in_license_plate_category")
    if any(term in t for term in ("rv dome light", "rvhouse", "12v rv light")) and any(term in p for term in ("photographs", "television memorabilia", "color")):
        flags.append("rv_light_in_memorabilia_color_category")
    if "paint thickness gauge" in t and any(term in p for term in ("photographs", "television memorabilia", "color")):
        flags.append("measurement_gauge_in_memorabilia_color_category")
    if "generator exhaust kit" in t and "boat parts" in p:
        flags.append("rv_generator_exhaust_in_marine_engine_category")
    if "black tank treatment" in t and "pumps" in p:
        flags.append("rv_tank_treatment_in_pumps_category")
    if any(term in t for term in ("side marker/clearance lights", "side marker light", "clearance marker lights")) and "turn signal light assemblies" in p:
        flags.append("side_marker_lights_in_turn_signal_category")
    if any(term in t for term in ("t-top sun shade", "bimini top extension", "boat sun shade")) and "backpacks & bags" in p:
        flags.append("marine_sun_shade_in_bags_category")
    # "Cigarette tubes" is a tobacco-related product phrase, not vehicle
    # suspension tubing. Keep this explicit because generic taxonomy token
    # overlap otherwise ranks Motors leaves such as Fork Legs & Tubes highly.
    if any(term in t for term in ("cigarette tube", "cigarette paper", "tobacco")) and "ebay motors" in p:
        flags.append("tobacco_related_product_in_motors")
    if any(x in t for x in ("rv ", "rv/", "motorhome", "camper")) and "collectibles" in p:
        flags.append("vehicle_product_in_collectibles")
    if any(x in t for x in ("boat", "marine", "pontoon")) and "collectibles" in p:
        flags.append("marine_product_in_collectibles")
    if any(x in t for x in ("car ", "vehicle", "f150", "ford", "trailer")) and any(x in p for x in ("collectibles", "jewelry")):
        flags.append("vehicle_product_in_unrelated_branch")
    if any(x in t for x in ("screen replacement", "digitizer", "oled")) and "cell phone" not in p:
        flags.append("phone_display_outside_phone_parts")
    if "welder" in t and "welding" not in p:
        flags.append("welder_outside_welding")
    if "monitor mount" in t and "monitor mounts" not in p:
        flags.append("mount_misclassified_as_monitor")
    bathroom_grab_product = any(token in t for token in ("shower handle", "shower grab", "bathroom grab", "bath grab"))
    if "grab bar" in t and "grab" not in p and not bathroom_grab_product:
        flags.append("grab_bar_mismatch")
    if bathroom_grab_product and "ebay motors" in p:
        flags.append("bathroom_grab_handle_in_motors")
    if "heat gun" in t and "heat gun" not in p:
        flags.append("heat_gun_mismatch")
    if "sprayer wand" in t and "wand" not in p:
        flags.append("sprayer_wand_mismatch")
    if "pool cue" in t and any(x in p for x in ("pool pumps", "pool filters", "pool chemicals")):
        flags.append("pool_cue_in_pool_maintenance")
    if "pool cue" in t and "pool" in p and "billiard" not in p:
        flags.append("pool_cue_outside_billiards")
    if "pool cue stick" in t and any(x in p for x in ("cue balls", "cue racks", "cue care", "cue tips")):
        flags.append("pool_cue_stick_in_cue_accessory")
    security_camera_product = "security camera" in t or ("camera" in t and any(x in t for x in ("home security", "home surveillance", "indoor security", "outdoor security")))
    if security_camera_product and any(x in p for x in ("camera cables", "camera brackets", "camera microphones", "camera lenses", "camera power supplies", "camera controllers", "other camera & photo accs")):
        flags.append("security_camera_in_camera_accessories")
    if "windshield" in t and "wiper" not in t and any(x in p for x in ("windshield wipers", "wiper arms", "wiper fluid", "wiper washers")):
        flags.append("windshield_glass_in_wiper_parts")
    # Recovered and Vine-style titles can share very generic taxonomy tokens
    # ("light", "label", "windshield").  These are hard product-family
    # contradictions observed in provider rejections, not ranking preferences:
    # do not publish until a human or current taxonomy resolver selects a
    # credible leaf.
    if any(term in t for term in ("rv interior led", "rv dome light", "rvhouse")) and any(term in p for term in ("insect trap", "pest control")):
        flags.append("rv_lighting_in_insect_control_category")
    # Additional live-confirmed category collisions from the failed eBay
    # publication batch. These are current leaf IDs, but the product families
    # are contradictory; leaf validity alone must not clear a retry.
    if any(term in t for term in ("rv water service panel", "rv fill valve", "heater bypass")) and "booster water heaters" in p:
        flags.append("rv_water_service_part_in_commercial_booster_heaters")
    if any(term in t for term in ("heated drinking water hose", "heated rv hose", "freeze protection hose")) and "fire hoses" in p:
        flags.append("heated_rv_water_hose_in_fire_hoses")
    if any(term in t for term in ("wheel lock", "car trailer lock")) and "alarm control panels" in p:
        flags.append("vehicle_wheel_lock_in_alarm_control_panels")
    if any(term in t for term in ("mattress topper", "mattress pad")) and "right angle drills" in p:
        flags.append("bedding_in_right_angle_drills")
    if any(term in t for term in ("nail clipper", "nail trimmer")) and "flower nails" in p:
        flags.append("personal_nail_clipper_in_cake_decorating")
    if any(term in t for term in ("rv ", "motorhome", "camper")) and any(term in t for term in ("wall sconce", "wall lamp", "light fixture")) and "smart wall switches" in p:
        flags.append("rv_light_fixture_in_smart_switches")
    if any(term in t for term in ("club car", "golf cart")) and any(term in t for term in ("light kit", "headlight")) and ("pest control" in p or ("insect" in p and "trap" in p)):
        flags.append("golf_cart_light_kit_in_insect_control")
    if any(term in t for term in ("scale label", "printing scale label")) and "stickers" in p:
        flags.append("scale_labels_in_stickers_category")
    if "windshield" in t and any(term in t for term in ("utv", "ranger 800", "fullsize 570")) and "snowmobile" in p:
        flags.append("utv_windshield_in_snowmobile_category")
    if "under sink organizer" in t and any(x in p for x in ("baby locks", "automotive", "eBay motors", "collectibles")):
        flags.append("under_sink_organizer_in_unrelated_category")
    if any(x in t for x in ("air shocks", "air shock", "shock absorber", "shock absorbers")) and not any(x in p for x in ("shocks, struts", "shocks & coil overs", "air suspension")):
        flags.append("vehicle_shock_absorber_outside_suspension")
    if "anti-snore pillow" in t and any(x in p for x in ("sex pillows", "pillow pals")):
        flags.append("sleep_pillow_in_adult_or_collectible")
    if "chicken coop door" in t and any(x in p for x in ("smart door lock", "dishwashers", "chickens")):
        flags.append("coop_door_in_unrelated_door_or_collectible")
    if "chicken coop door" in t and any(x in p for x in ("memorabilia", "collectibles", "closet & sliding")):
        flags.append("coop_door_in_unrelated_household_or_collectible")
    if "fillet knife" in t and any(x in p for x in ("knife blocks", "cutting machines", "knife stones")):
        flags.append("fillet_knife_in_unrelated_knife_category")
    if "accumulator expansion tank" in t and "boiler expansion tanks" in p:
        flags.append("water_accumulator_in_boiler_parts")
    if "cooler bag" in t and "bag tags" in p:
        flags.append("cooler_bag_in_golf_bag_tags")
    if "pcb holder" in t and not any(x in p for x in ("circuit board", "soldering", "electronics repair")):
        flags.append("pcb_holder_outside_electronics_tools")
    if "fillet knife" in t and ("eBay motors" in p or "auto performance parts" in p or "automotive" in p):
        flags.append("fishing_knife_in_automotive_parts")
    if "wire stripper" in t and "wire stripping machines" not in p:
        flags.append("wire_stripper_outside_wire_stripping_machines")
    if "anti-snore" in t and any(x in p for x in ("collectibles", "sex pillows", "pillow pals", "holiday & seasonal")):
        flags.append("sleep_pillow_in_collectible_or_unrelated_pillow_category")
    if "anti-snore" in t and "baby > nursery bedding" in p:
        flags.append("adult_sleep_pillow_in_baby_bedding")
    if any(term in t for term in ("smart access control", "access control system", "fingerprint door lock", "smart door lock")) and not any(term in p for term in ("smart door locks", "door locks", "access control equipment", "locksets")):
        flags.append("smart_lock_outside_lock_or_access_control")
    if any(term in t for term in ("usb hub", "usb-c hub", "usb c hub", "docking station")) and any(term in p for term in ("eBay motors", "motorcycle & powersports", "auto performance parts")):
        flags.append("computer_hub_in_vehicle_or_unrelated_branch")
    if "patio heater" in t and not any(term in p for term in ("patio heaters", "outdoor heating")):
        flags.append("patio_heater_outside_outdoor_heating")
    if "space heater" in t and not any(term in p for term in ("space heaters", "heating & cooling appliances")):
        flags.append("space_heater_outside_heating_appliances")
    if "running belt" in t and "toning belts" in p:
        flags.append("running_waist_belt_in_toning_equipment")
    if any(term in t for term in ("screen replacement", "digitizer assembly", "replacement display")) and "screen protectors" in p:
        flags.append("replacement_screen_in_screen_protectors")
    if "grillz" in t and "shark teeth" in p:
        flags.append("tooth_jewelry_in_fossil_collectibles")
    if "camping shower" in t and any(term in p for term in ("shower panels", "shower massagers", "steam shower")):
        flags.append("portable_camping_shower_in_bathroom_fixture")
    if "camera" in t and any(term in t for term in ("24/7 recording", "home security", "dual lens solar", "surveillance")) and any(term in p for term in ("pool heaters", "fossils", "pet supplies", "lighting & ceiling fans")):
        flags.append("security_camera_outside_surveillance")
    if "bluetooth" in t and any(x in t for x in ("audio receiver", "audio adapter")) and "battery chargers" in p:
        flags.append("audio_receiver_in_battery_chargers")
    if "audio adapter" in t and "computers/tablets & networking > computer cables" in p:
        flags.append("consumer_audio_adapter_in_computer_cables")
    return flags


def validate_listing_category(db: Session, listing, *, tree_id: str | None = None, tree_version: str | None = None) -> dict:
    """Validate a listing's assigned EBAY_US leaf against the local tree.

    This is the canonical validator used by repair jobs and diagnostics.  It
    never treats a non-null category ID as proof of correctness.
    """
    cid = str(getattr(listing, "category_id", None) or "").strip()
    metadata = getattr(listing, "source_metadata", None) if isinstance(getattr(listing, "source_metadata", None), dict) else {}
    validation = metadata.get("category_validation") if isinstance(metadata.get("category_validation"), dict) else {}
    validation_id = str(validation.get("category_id") or "").strip()
    top_tree = str(metadata.get("taxonomy_tree_id") or "").strip()
    top_market = str(metadata.get("taxonomy_marketplace") or "").strip().upper()
    nested_tree = str(validation.get("tree_id") or "").strip() if validation_id == cid else ""
    nested_market = str(validation.get("marketplace") or "").strip().upper() if validation_id == cid else ""
    if top_tree and nested_tree and top_tree != nested_tree:
        return {"status": "ownership_conflict", "category_id": cid, "flags": ["taxonomy_ownership_conflict"], "marketplace": top_market or nested_market, "tree_id": top_tree}
    if top_market and nested_market and top_market != nested_market:
        return {"status": "ownership_conflict", "category_id": cid, "flags": ["taxonomy_ownership_conflict"], "marketplace": top_market, "tree_id": top_tree or nested_tree}
    tree_id = str(tree_id or top_tree or nested_tree or "0").strip()
    marketplace = str(top_market or nested_market or "").upper()
    if not marketplace and tree_id == "0":
        marketplace = "EBAY_US"
    if not marketplace:
        return {"status": "ownership_missing", "category_id": cid, "flags": ["taxonomy_marketplace_missing"], "tree_id": tree_id}
    if (marketplace, tree_id) not in {("EBAY_US", "0"), ("EBAY_MOTORS_US", "100")}:
        return {"status": "ownership_missing", "category_id": cid, "flags": ["unsupported_or_unverified_taxonomy_identity"], "marketplace": marketplace, "tree_id": tree_id}
    tree_version = tree_version or ("83" if str(tree_id) == "100" else "134")
    title = str(getattr(listing, "title", None) or "")
    if not cid:
        return {"status": "missing", "category_id": None, "flags": ["missing_category_id"], "tree_id": tree_id, "tree_version": tree_version}
    row = category_by_id(db, cid, tree_id, marketplace)
    if not row:
        # A missing tree-qualified ID is not permission to try another tree.
        # The caller must resolve the category from provenance/live taxonomy.
        return {"status": "category_revalidation_required", "category_id": cid, "flags": ["category_not_in_declared_taxonomy_tree"], "marketplace": marketplace, "tree_id": tree_id, "tree_version": tree_version}
    flags = semantic_guard_flags(title, row["full_path"])
    if str(row.get("category_tree_id") or "") == "100" and not listing_has_motors_semantics(listing):
        flags.append("motors_tree_without_correlated_product_evidence")
    status = "semantic_flag" if flags else ("valid_local" if bool(row["leaf"]) else "nonleaf")
    return {"status": status, "category_id": cid, "category_name": row["category_name"], "full_path": row["full_path"], "leaf": bool(row["leaf"]), "flags": flags, "marketplace": row["marketplace"], "tree_id": row["category_tree_id"], "tree_version": str(row["category_tree_version"] or tree_version)}


def resolve_local_category(db: Session, title: str, semantic_terms: list[str] | None = None, *, limit: int = 20) -> list[dict]:
    """Rank local leaf candidates from product identity, then application.

    ``semantic_terms`` are facts such as Amazon product type/category and are
    deliberately given more weight than title modifiers (brand, color, bundle,
    connectivity). Search remains local and returns leaves only.
    """
    primary = " ".join(str(term or "") for term in (semantic_terms or []) if str(term or "").strip()).lower()
    title_text = str(title or "").lower()
    # Do not let generic "tube" overlap select industrial or automotive
    # tubing for a cigarette-related product. Vine policy already marks these
    # rows for marketplace-specific human review, and the synchronized tree
    # has no verified cigarette-tube leaf.
    if any(term in title_text for term in ("cigarette tube", "cigarette paper")):
        return []
    focused_identity = (
        ("rs485 transceiver", "interface ics"), ("max485", "interface ics"),
        ("max3485", "interface ics"),
        ("paint thickness gauge", "other measuring layout tools"),
        ("rv dome light", "chandeliers ceiling fixtures"), ("rvhouse", "chandeliers ceiling fixtures"),
        ("t-top sun shade", "boat covers"), ("bimini top extension", "boat covers"),
        ("rv door cover", "curtains & drapes"), ("magnetic rv door cover", "curtains & drapes"),
        ("security camera", "security camera"), ("chicken coop door", "chicken coop door"),
        ("window camera", "security camera"), ("camera for home security", "security camera"),
        ("home security", "security camera"),
        ("smart access control", "smart door locks"),
        ("ipad keyboard", "cases covers keyboard folios"),
        ("access control system", "smart door locks"),
        ("fingerprint card", "smart door locks"),
        ("shower grab", "shower & bathtub accessories"),
        ("shower handle", "shower & bathtub accessories"),
        ("bathroom grab", "shower & bathtub accessories"),
        ("surface pro", "laptop docking stations"),
        ("usb c hub", "usb cables hubs adapters"),
        ("usb-c hub", "usb cables hubs adapters"),
        ("patio heater", "patio heaters"),
        ("space heater", "space heaters"),
        ("bed pillow", "bed pillows"),
        ("running belt", "running belts"),
        ("digitizer assembly", "smartphone parts"),
        ("screen replacement", "smartphone parts"),
        ("workbench", "work benches"), ("work bench", "work benches"),
        ("vinyl record", "vinyl records"),
        ("adaptive gaming kit", "controllers & attachments"),
        ("grillz", "body jewelry"),
        ("camping shower", "portable showers"),
        # This portable marker prints QR/barcode/date codes directly onto
        # packaging and product surfaces. In the synchronized US tree the
        # closest product leaf is Label Printers & Applicators, not LED
        # displays (a title modifier) or generic computer printers.
        ("handheld inkjet printer", "label printers"),
        ("pool cue", "pool cue"), ("fillet knife", "fillet knife"),
        # A tow rope explicitly sold for tubing is a recreational watersport
        # accessory, not an eBay Motors vehicle/boat part.
        ("tow rope for tubing", "tubing & towables"),
        # Battery-protection controller boards are electrical PCBs, not
        # vehicle-specific battery parts. Tool replacement batteries belong
        # under Power Tool Batteries rather than Motors or solar categories.
        ("bms protection board", "printed circuit boards"),
        ("battery management board", "printed circuit boards"),
        ("greenworks g-max40v battery", "power tool batteries"),
        ("car trash can", "trash cans wastebaskets"), ("car trash bags", "trash bags"),
        ("massage pillow", "massage pillows bolsters"),
        ("anti-snore pillow", "bed pillows"), ("anti-snore", "bed pillows"), ("windshield", "windshield"),
        ("car trash can", "garbage can"), ("accumulator expansion tank", "expansion tank"),
        ("thermal printer sticker paper", "printer paper"), ("printer sticker paper", "printer paper"),
        ("golf cooler bag", "golf accessories"), ("wire stripper", "wire stripping machines"),
        ("audio adapter", "cables & adapters"), ("bluetooth receiver", "cables & adapters"),
        ("tablet keyboard case", "cases covers keyboard folios"),
        ("ipad keyboard case", "cases covers keyboard folios"),
        ("keyboard case", "cases covers keyboard folios"),
        ("keycaps", "keyboards keypads"),
        ("under sink organizer", "racks & holders"), ("pull-out under sink organizer", "racks & holders"),
        ("air shocks", "shocks, struts"), ("rear air shocks", "shocks, struts"),
        ("well pump pressure switch", "pump pressure switches"),
        ("laserjet toner cartridge", "toner cartridges"),
        ("toner cartridge", "toner cartridges"),
        ("video game controller", "controllers & attachments"),
        ("game controller", "controllers & attachments"),
        ("video game", "video games"),
        ("learning thermostat", "programmable thermostats"),
        ("programmable thermostat", "programmable thermostats"),
        # This is an exact product type, not a pressure-washer accessory or
        # merely the broad outdoor-power-equipment category.
        ("electric pressure washer", "electric pressure washers"),
        ("electric paint sprayer", "paint sprayers"),
        ("right reflector", "reflectors"), ("tpms valve stem kit", "valve stems & caps"),
        ("ignition coil", "ignition coils"), ("windshield wiper blade", "wiper blades & refills"),
        ("jigsaw puzzle", "jigsaw"), ("women's wallet", "wallets"),
        ("clutch handbag", "women's bags & handbags"), ("tote handbag", "women's bags & handbags"),
        ("crossbody handbag", "women's bags & handbags"), ("evening handbag", "women's bags & handbags"),
    )
    identity_override = False
    for phrase, identity in focused_identity:
        if phrase in title_text or phrase in primary:
            primary = identity
            identity_override = True
            break
    # Amazon product_type is occasionally broad or plainly wrong (e.g. a
    # windshield called "Switch"). Do not let a source label dominate if it
    # does not describe words present in the product title.
    if not identity_override and primary and not any(token in title_text for token in re.findall(r"[a-z]+", primary) if len(token) > 3):
        primary = ""
    text_value = " ".join(part for part in (primary, title_text) if part)
    stopwords = {
        "the", "for", "and", "with", "from", "new", "inch", "inches", "pack", "pcs", "piece", "pieces",
        "adult", "adults", "kids", "children", "women", "womens", "men", "mens", "size", "one", "set", "count",
        "professional", "portable", "premium", "upgrade", "upgraded", "automatic", "electric", "wireless", "smart",
        "gold", "black", "white", "green", "blue", "red", "gray", "grey", "large", "small", "heavy", "duty",
        # Amazon sometimes fills product_type with a generic container rather
        # than the product identity. These are modifiers, not category nouns.
        "mount", "mounted", "mounting", "kit", "kits", "switch", "game", "tool", "tools",
        "parts", "accessory", "accessories", "product", "device", "equipment", "weapon", "adjacent", "bag",
    }
    primary_tokens = [t for t in re.findall(r"[a-z]+", primary) if len(t) > 2 and t not in stopwords]
    if "chicken coop door" in title_text:
        primary_tokens.append("poultry")
    title_tokens = [t for t in re.findall(r"[a-z]+", title_text) if len(t) > 2 and t not in stopwords]
    def singular(token: str) -> str:
        return token[:-1] if token.endswith("s") and not token.endswith("ss") and len(token) > 4 else token
    primary_normalized = {singular(token) for token in primary_tokens}
    title_normalized = {singular(token) for token in title_tokens}
    tokens = list(dict.fromkeys(primary_tokens + title_tokens))
    primary_phrase = " ".join(primary_tokens[:5])
    rows = []
    # Domain selection must see source product type/category and verified fact
    # terms, not just the display title (which often omits RV/marine fitment).
    tree_priority = candidate_trees_for_listing(text_value)
    synonyms = {"grab": "handle", "bar": "handle", "sprayer": "wand", "digitizer": "screen", "rv": "camper", "motorhome": "camper", "marker": "clearance", "chicken": "poultry", "coop": "poultry", "fillet": "fish", "cue": "billiards", "windshield": "windshields", "camera": "cameras", "garbage": "trash", "workbench": "work benches", "vinyl": "records"}
    for priority, (marketplace, domain_tree) in enumerate(tree_priority):
        # Query each supported tree once. The old resolver ran a second full
        # taxonomy scan for synonym terms, which multiplied latency on every
        # Vine row and made a 200-item recovery batch take far too long. Merge
        # synonyms into the OR candidate set; semantic scoring below still
        # decides which leaf wins.
        name_queries = ([primary_phrase] if primary_phrase else []) + tokens[:8]
        name_queries.extend(synonyms[token] for token in tokens[:8] if token in synonyms)
        name_queries = list(dict.fromkeys(name_queries))
        found = search_local_leaves(db, name_queries, tree_id=domain_tree, marketplace=marketplace, limit=180) if tokens else []
        rows.extend({**dict(row), "_tree_priority": priority} for row in found)
    concepts = []
    if "chicken coop door" in title_text:
        concepts.append(("backyard poultry", "EBAY_US", "0"))
    if "pool cue" in title_text:
        concepts.append(("billiards", "EBAY_US", "0"))
    if "anti-snore" in title_text:
        concepts.append(("bed pillows", "EBAY_US", "0"))
    if "fillet knife" in title_text:
        concepts.append(("fishing knife", "EBAY_US", "0"))
    if any(term in title_text for term in ("rs485 transceiver", "max485", "max3485")):
        concepts.append(("interface ics", "EBAY_US", "0"))
    if "paint thickness gauge" in title_text:
        concepts.append(("other measuring & layout tools", "EBAY_US", "0"))
    if any(term in title_text for term in ("rv dome light", "rvhouse")):
        concepts.append(("chandeliers & ceiling fixtures", "EBAY_US", "0"))
    if "generator exhaust kit" in title_text and any(term in title_text for term in ("cummins onan", "rv")):
        concepts.append(("power generator parts", "EBAY_MOTORS_US", "100"))
    if "navigation lights" in title_text and any(term in title_text for term in ("boat", "marine")):
        concepts.append(("electrical & lighting", "EBAY_MOTORS_US", "100"))
    if "marker" in title_text and any(term in title_text for term in ("clearance light", "side marker", "marker/clearance")):
        concepts.append(("side marker light assemblies", "EBAY_MOTORS_US", "100"))
    if "holding tank gauge" in title_text or ("sewage level gauge" in title_text and "rv" in title_text):
        concepts.append(("other water & sanitary systems", "EBAY_MOTORS_US", "100"))
    if "black tank treatment" in title_text:
        concepts.append(("sewer & winterizing chemicals", "EBAY_MOTORS_US", "100"))
    if "car trash can" in title_text:
        concepts.append(("trash cans & wastebaskets", "EBAY_US", "0"))
    if "car trash bags" in title_text:
        concepts.append(("trash bags", "EBAY_US", "0"))
    if "massage pillow" in title_text:
        concepts.append(("massage pillows & bolsters", "EBAY_US", "0"))
    if "power steering pump" in title_text and "mercruiser" in title_text:
        concepts.append(("power steering pumps", "EBAY_MOTORS_US", "100"))
    if "fuel injector cleaner tester" in title_text:
        concepts.append(("electrical testers", "EBAY_MOTORS_US", "100"))
    semantic_evidence = " ".join(str(term or "").lower() for term in (semantic_terms or []))
    if "right reflector" in semantic_evidence or ("reflectors" in primary and any(term in text_value for term in ("automotive", "rav4", "toyota"))):
        concepts.append(("reflectors", "EBAY_MOTORS_US", "100"))
    if "tpms valve stem kit" in semantic_evidence or "valve stems & caps" in primary:
        concepts.append(("valve stems & caps", "EBAY_MOTORS_US", "100"))
    if "ignition coil" in semantic_evidence or "ignition coil" in text_value:
        concepts.append(("ignition coils", "EBAY_MOTORS_US", "100"))
    if "windshield wiper blade" in text_value or "wiper blade" in title_text:
        concepts.append(("wiper blades & refills", "EBAY_MOTORS_US", "100"))
    if "jigsaw puzzle" in semantic_evidence or ("jigsaw" in primary and "puzzle" in semantic_evidence):
        concepts.append(("jigsaw", "EBAY_US", "0"))
    if "wallet" in title_text and any(term in title_text for term in ("kate spade", "dooney", "coach")):
        concepts.append(("wallets", "EBAY_US", "0"))
    if any(term in title_text for term in ("clutch", "tote bag", "crossbody bag", "evening bag")):
        concepts.append(("women's bags & handbags", "EBAY_US", "0"))
    if any(term in title_text for term in ("t-top sun shade", "bimini top extension", "boat sun shade")):
        concepts.append(("covers", "EBAY_MOTORS_US", "100"))
    if "rv door cover" in title_text or "rv door curtain" in title_text:
        concepts.append(("curtains & drapes", "EBAY_US", "0"))
    if "accumulator expansion tank" in title_text:
        concepts.append(("pressure tanks", "EBAY_US", "0"))
    if "printer sticker paper" in title_text:
        concepts.append(("printer paper", "EBAY_US", "0"))
    if "handheld inkjet printer" in title_text and any(term in title_text for term in ("barcode", "qr code", "various surfaces", "marking")):
        concepts.append(("label printers", "EBAY_US", "0"))
    if "golf cooler bag" in title_text:
        concepts.append(("other golf accessories", "EBAY_US", "0"))
    if "tow rope" in title_text and any(term in title_text for term in ("for tubing", "towable tube", "towable tubes")):
        concepts.append(("tubing & towables", "EBAY_US", "0"))
    if "bms" in title_text and any(term in title_text for term in ("protection board", "battery management board")):
        concepts.append(("printed circuit boards (pcbs)", "EBAY_US", "0"))
    if "greenworks" in title_text and "battery" in title_text and any(term in title_text for term in ("g-max40v", "g max40v", "g-max 40v")):
        concepts.append(("power tool batteries", "EBAY_US", "0"))
    if ("keyboard case" in title_text and any(term in title_text for term in ("ipad", "tablet", "surface", "redmi pad", "poco pad"))) or "ipad keyboard" in title_text:
        concepts.append(("cases, covers, keyboard folios", "EBAY_US", "0"))
    if "keycaps" in title_text:
        concepts.append(("keyboards & keypads", "EBAY_US", "0"))
    if "starlink" in title_text and any(term in title_text for term in ("clamp", "mount", "pole")):
        concepts.append(("satellite mounting plates", "EBAY_MOTORS_US", "100"))
    if any(term in title_text for term in ("caregiver", "pull cord call button", "emergency alert button")):
        concepts.append(("emergency alert systems", "EBAY_US", "0"))
    if "holding tank" in title_text and "vent filter" in title_text:
        concepts.append(("other water & sanitary systems", "EBAY_MOTORS_US", "100"))
    if "wireless guitar system" in title_text:
        concepts.append(("microphones & wireless systems", "EBAY_US", "0"))
    if "reflex drop sticks" in title_text:
        concepts.append(("plastic action games", "EBAY_US", "0"))
    if "pressure switch" in title_text and any(term in title_text for term in ("well pump", "water pump", "pump pressure")):
        concepts.append(("pump pressure switches", "EBAY_US", "0"))
    # Samsung BN44-series identifiers are used on television power-supply
    # boards. When the title also identifies a power-supply/board part, route
    # to the synchronized TV-board leaf instead of generic power electronics
    # or an incidental Motors power category.
    if "bn44-" in title_text and any(term in title_text for term in ("power supply", "power board", "vss-pd board")):
        concepts.append(("tv boards, parts & components", "EBAY_US", "0"))
    # Household countertop grinders resolve ambiguously against commercial
    # coffee equipment because both leaves share the same product noun. A
    # named home appliance/model plus coffee-grinder title belongs to the
    # consumer Small Kitchen Appliances branch.
    if "coffee" in title_text and "grinder" in title_text and any(term in title_text for term in ("krups", "gx4100", "coffee / spice grinder", "coffee spice grinder")):
        concepts.append(("coffee grinders", "EBAY_US", "0"))
    if "under sink organizer" in title_text:
        concepts.append(("racks & holders", "EBAY_US", "0"))
    if "toner cartridge" in title_text:
        concepts.append(("toner cartridges", "EBAY_US", "0"))
    if "game controller" in title_text or "video game controller" in title_text:
        concepts.append(("controllers & attachments", "EBAY_US", "0"))
    if "learning thermostat" in title_text or "programmable thermostat" in title_text:
        concepts.append(("programmable thermostats", "EBAY_US", "0"))
    if any(term in title_text for term in ("air shocks", "air shock", "shock absorber", "shock absorbers")):
        concepts.append(("shocks, struts", "EBAY_MOTORS_US", "100"))
    if any(term in title_text for term in ("shower handle", "shower grab", "bathroom grab", "bath grab")):
        concepts.append(("other shower & bathtub accessories", "EBAY_US", "0"))
    # Common Vine products have clear leaves in the local tree, but Amazon's
    # broad product_type values (for example "Security" or "Wireless Hub")
    # can otherwise outrank the actual product noun in the title.
    if "home security" in title_text and "camera" in title_text:
        concepts.append(("security cameras", "EBAY_US", "0"))
    if any(term in title_text for term in ("smart access control", "access control system", "fingerprint card")):
        concepts.append(("smart door locks", "EBAY_US", "0"))
    if "surface pro" in title_text and any(term in title_text for term in ("docking station", "usb c hub", "usb-c hub")):
        concepts.append(("laptop docking stations", "EBAY_US", "0"))
    if "patio heater" in title_text:
        concepts.append(("patio heaters", "EBAY_US", "0"))
    elif "space heater" in title_text:
        concepts.append(("space heaters", "EBAY_US", "0"))
    if "pillow" in title_text and any(term in title_text for term in ("anti-snore", "anti snore", "bed pillow")):
        concepts.append(("bed pillows", "EBAY_US", "0"))
    if any(term in title_text for term in ("closet organizer", "closet storage organizer", "hanging closet organizer")):
        concepts.append(("closet organizers", "EBAY_US", "0"))
    if "luggage tag" in title_text or "suitcase tag" in title_text:
        concepts.append(("luggage tags", "EBAY_US", "0"))
    if "barcode scanner" in title_text or "qr code scanner" in title_text:
        concepts.append(("barcode scanners", "EBAY_US", "0"))
    if "air blow gun" in title_text or "car cleaning gun" in title_text or "car detailing gun" in title_text or ("air gun" in title_text and "car detailing" in title_text):
        concepts.append(("air blow guns", "EBAY_US", "0"))
    if "esd meter" in title_text or ("wrist strap tester" in title_text and "anti static" in title_text):
        concepts.append(("electrical testers", "EBAY_US", "0"))
    if "soft starter" in title_text:
        concepts.append(("reduced voltage/soft starters", "EBAY_US", "0"))
    if "windshield curtain" in title_text or "door curtain" in title_text:
        concepts.append(("curtains & drapes", "EBAY_US", "0"))
    if "iphone" in title_text and any(term in title_text for term in ("speaker replacement", "ringer buzzer", "loud speaker")):
        concepts.append(("cell phone & smartphone parts", "EBAY_US", "0"))
    if "sourdough starter" in title_text and any(term in title_text for term in ("warmer mat", "heating mat", "proofing mat")):
        concepts.append(("other baking accessories", "EBAY_US", "0"))
    if "rv door lock" in title_text or "rv door locks" in title_text:
        concepts.append(("door locks & lock mechanisms", "EBAY_US", "0"))
    if "boat fender" in title_text or "boat fenders" in title_text:
        concepts.append(("dock rails & bumpers", "EBAY_US", "0"))
    if "running belt" in title_text:
        concepts.append(("running belts", "EBAY_US", "0"))
    if any(term in title_text for term in ("screen replacement", "digitizer assembly", "replacement display")):
        concepts.append(("smartphone parts", "EBAY_US", "0"))
    if "grillz" in title_text or "grillz" in primary:
        concepts.append(("body jewelry", "EBAY_US", "0"))
    if "camping shower" in title_text or "foldable shower base" in title_text:
        concepts.append(("portable showers", "EBAY_US", "0"))
    if any(term in title_text for term in ("24/7 recording", "dual lens solar", "home security")) and "camera" in title_text:
        concepts.append(("security cameras", "EBAY_US", "0"))
    if "fillet knife" in title_text:
        # The synchronized eBay tree has no dedicated fillet-knife leaf; its
        # closest specific in-domain leaf is Other Fishing Equipment. Do not
        # let the battery-powered qualifier route a fishing tool into Motors.
        concepts.append(("other fishing equipment", "EBAY_US", "0"))
    for phrase, marketplace, domain_tree in concepts:
        found = search_local_leaves(db, [phrase], tree_id=domain_tree, marketplace=marketplace, limit=100)
        rows.extend({**dict(row), "_tree_priority": 0, "_concept_match": phrase} for row in found)
    # Numeric category IDs are only unique within their owning taxonomy tree.
    # Collapsing by ID alone could silently discard a valid Motors candidate
    # when tree 0 contains the same numeric ID.
    dedup = {
        (str(row.get("marketplace") or ""), str(row.get("category_tree_id") or ""), str(row["category_id"])): row
        for row in rows
    }
    rows = list(dedup.values())
    # Keep the semantic concept evidence when a row was also discovered by a
    # broad token pass (the latter must not overwrite the more specific pass).
    for row in rows:
        path = str(row.get("full_path") or "").lower()
        for trigger, target in (("pool cue", "billiards"), ("chicken coop door", "backyard poultry"),
                                ("accumulator expansion tank", "pressure tanks"),
                                ("printer sticker paper", "printer paper"),
                                ("handheld inkjet printer", "label printers"),
                                ("home security", "security cameras"),
                                ("smart access control", "smart door locks"),
                                ("access control system", "smart door locks"),
                                ("surface pro", "laptop docking stations"),
                                ("patio heater", "patio heaters"),
                                ("space heater", "space heaters"),
                                ("anti-snore", "bed pillows"),
                                ("closet organizer", "closet organizers"),
                                ("hanging closet organizer", "closet organizers"),
                                ("luggage tag", "luggage tags"),
                                ("suitcase tag", "luggage tags"),
                                ("barcode scanner", "barcode scanners"),
                                ("qr code scanner", "barcode scanners"),
                                ("air blow gun", "air blow guns"),
                                ("car cleaning gun", "air blow guns"),
                                ("car detailing gun", "air blow guns"),
                                ("esd meter", "electrical testers"),
                                ("wrist strap tester", "electrical testers"),
                                ("soft starter", "reduced voltage/soft starters"),
                                ("windshield curtain", "curtains & drapes"),
                                ("door curtain", "curtains & drapes"),
                                ("ringer buzzer", "cell phone & smartphone parts"),
                                ("loud speaker replacement", "cell phone & smartphone parts"),
                                ("rv door lock", "door locks & lock mechanisms"),
                                ("boat fender", "dock rails & bumpers"),
                                ("rv door cover", "curtains & drapes"),
                                ("rv door curtain", "curtains & drapes"),
                                ("sourdough starter", "other baking accessories"),
                                ("running belt", "running belts"),
                                ("digitizer assembly", "smartphone parts"),
                                ("screen replacement", "smartphone parts"),
                                ("grillz", "body jewelry"),
                                ("camping shower", "portable showers"),
                                ("golf cooler bag", "other golf accessories"),
                                ("keyboard case", "cases, covers, keyboard folios"),
                                ("ipad keyboard case", "cases, covers, keyboard folios"),
                                ("ipad keyboard", "cases, covers, keyboard folios"),
                                ("keycaps", "keyboards & keypads"),
                                ("starlink", "satellite mounting plates"),
                                ("caregiver", "emergency alert systems"),
                                ("pull cord call button", "emergency alert systems"),
                                ("holding tank", "other water & sanitary systems"),
                                ("wireless guitar system", "microphones & wireless systems"),
                                ("reflex drop sticks", "plastic action games"),
                                ("well pump", "pump pressure switches"),
                                ("under sink organizer", "racks & holders"),
                                ("air shocks", "shocks, struts"),
                                ("air shock", "shocks, struts"),
                                ("shower handle", "other shower & bathtub accessories"),
                                ("shower grab", "other shower & bathtub accessories"),
                                ("bathroom grab", "other shower & bathtub accessories"),
                                ("bath grab", "other shower & bathtub accessories")):
            if trigger in title_text and target in path:
                row["_concept_match"] = target
    scored = []
    title_set = set(title_tokens)
    for row in rows:
        path = str(row["full_path"] or "").lower()
        name = str(row.get("category_name") or "").lower()
        concept_match = str(row.get("_concept_match") or "")
        name_tokens = set(re.findall(r"[a-z]+", name))
        name_normalized = {singular(token) for token in name_tokens}
        path_tokens = {singular(token) for token in re.findall(r"[a-z]+", path)}
        primary_hits = primary_normalized & name_normalized
        title_leaf_hits = title_normalized & name_normalized
        score = (6 * len(primary_hits)) + (2 * len(title_normalized & name_normalized)) + len({singular(token) for token in tokens} & path_tokens)
        if str(row.get("category_tree_id") or "") == "100" and not motors_path_is_semantically_required(text_value):
            # Publisher compatibility is category quality evidence.  A weak
            # title overlap must never choose a Motors leaf for a general
            # consumer item when a regular EBAY_US candidate exists.
            score -= 75
        if primary_phrase and primary_phrase in name:
            score += 12
        if primary_tokens and " ".join(primary_tokens) in name.replace("s ", " "):
            score += 16
        # A product noun appearing in the leaf name is materially stronger
        # than incidental overlap with a distant breadcrumb.
        if len(primary_hits) >= 2:
            score += 5
        accessory_terms = {"parts", "accessories", "cables", "adapters", "mounts", "brackets", "power", "supplies", "lenses", "microphones", "cases", "housings", "controllers", "joysticks", "arms", "blades", "fluid", "repair", "tools", "shams", "blocks", "storage"}
        if len(primary_hits) >= 2 and (name_tokens - set(primary_tokens)) & accessory_terms:
            score -= 18
        if any(x in text_value for x in ("rv", "motorhome", "camper")) and any(x in path for x in ("collectibles", "holiday")):
            score -= 20
        if any(x in text_value for x in ("boat", "marine", "pontoon")) and "collectibles" in path:
            score -= 20
        if any(x in text_value for x in ("screen replacement", "digitizer", "oled", "lcd")) and "cell phone" in path:
            score += 10
        contradiction_pairs = (
            (("pulse oximeter", "oximeter", "spo2"), ("monitors", "computers/tablets", "forklift")),
            (("skimboard", "tumbling mat", "gymnastics"), ("forklift", "automotive", "motors")),
            (("hair shears", "thinning shears", "hair cutting"), ("basketry", "forklift", "automotive")),
            (("first aid", "supplement", "toothpaste", "vaginal"), ("motors", "forklift", "industrial")),
            (("butt plug", "anal dilator", "intimate"), ("restaurant", "dish, glass", "industrial")),
            (("fishing knife", "fillet knife"), ("knockout", "punch sets", "industrial")),
            (("heated blanket",), ("moving blankets", "forklift", "industrial")),
        )
        for product_terms, bad_branches in contradiction_pairs:
            if any(term in text_value for term in product_terms) and any(branch in path for branch in bad_branches):
                score -= 50
        if "keycaps" in title_text:
            if "computers/tablets & networking > keyboards, mice & pointers > keyboards & keypads" in path:
                score += 24
            if "video games & consoles" in path:
                score -= 50
        priority = int(row.get("_tree_priority") or 0)
        if any(branch in path for branch in ("collectibles", "jewelry", "holiday decorations")):
            score -= 20
        if concept_match and (
            ("generator exhaust kit" in title_text and name == "power generator parts")
            or ("navigation lights" in title_text and name == "electrical & lighting")
            or ("marker/clearance lights" in title_text and name == "side marker light assemblies")
            or ("holding tank gauge" in title_text and name == "other water & sanitary systems")
            or ("sewage level gauge" in title_text and name == "other water & sanitary systems")
            or ("black tank treatment" in title_text and name == "sewer & winterizing chemicals")
            or ("power steering pump" in title_text and "mercruiser" in title_text and name == "power steering pumps")
            or ("fuel injector cleaner tester" in title_text and name == "electrical testers, test leads")
            or ("car trash can" in title_text and name == "trash cans & wastebaskets")
            or ("car trash bags" in title_text and name == "trash bags")
            or ("massage pillow" in title_text and name == "massage pillows & bolsters")
        ):
            # A title-specific concept is a deliberate semantic correction,
            # not incidental token overlap. Restore it above the candidate
            # threshold even when stale source breadcrumbs disagree.
            score += 120
        if ("jigsaw puzzle" in text_value or ("jigsaw" in primary and "puzzle" in " ".join(str(term or "").lower() for term in (semantic_terms or [])))) and name == "jigsaw":
            score += 100
        if "wallet" in title_text and any(term in title_text for term in ("kate spade", "dooney", "coach", "clutch", "strap")):
            if name == "wallets" and "women > women's accessories > wallets" in path:
                score += 100
        if str(row.get("category_tree_id") or "") == "100" and any(term in text_value for term in ("toyota", "rav4", "motorcraft", "ranger", "f150")):
            if "car & truck parts" in path:
                score += 48
            if any(branch in path for branch in ("atv, side-by-side", "motorcycle & scooter", "commercial truck parts")):
                score -= 35
        flags = semantic_guard_flags(title, path)
        score -= 24 * len(flags)
        # A candidate under an explicitly contradictory semantic guard is not
        # merely a weak match. Keep it out of category results so callers must
        # re-resolve rather than persisting a "best guess".
        if flags:
            score -= 120
        if "security camera" in text_value and "security cameras" in name:
            score += 18
        if "windshield" in text_value and "windshield" in name and "wiper" not in name:
            score += 15
        if "anti-snore pillow" in text_value and name == "pillows":
            score += 15
        if "anti-snore pillow" in text_value and "home & garden > bedding > bed pillows" in path:
            score += 22
        if any(term in title_text for term in ("closet organizer", "closet storage organizer", "hanging closet organizer")):
            if "closet organizers" in name:
                score += 35
            elif any(term in name for term in ("bathroom storage", "storage bags", "storage organizers")):
                score -= 12
        focused_category_terms = (
            (("luggage tag", "suitcase tag"), "luggage tags"),
            (("barcode scanner", "qr code scanner"), "barcode scanners"),
            (("air blow gun", "car cleaning gun", "car detailing gun", "air gun"), "air blow guns"),
            (("esd meter", "wrist strap tester"), "electrical testers"),
            (("soft starter",), "reduced voltage/soft starters"),
            (("windshield curtain", "door curtain"), "curtains & drapes"),
            (("ringer buzzer", "loud speaker replacement"), "cell phone & smartphone parts"),
            (("rv door lock",), "door locks & lock mechanisms"),
            (("boat fender",), "dock rails & bumpers"),
            (("sourdough starter",), "other baking accessories"),
        )
        for triggers, expected in focused_category_terms:
            if any(trigger in title_text for trigger in triggers) and expected in name:
                score += 35
        if any(term in title_text for term in ("rv dome light", "rvhouse")):
            if name == "chandeliers & ceiling fixtures" and "home & garden > lamps, lighting & ceiling fans > chandeliers & ceiling fixtures" in path:
                score += 75
            if any(branch in path for branch in ("billiards", "collectibles", "radio control", "facility maintenance")):
                score -= 80
        if any(term in title_text for term in ("t-top sun shade", "bimini top extension", "boat sun shade")):
            if "boat parts > accessories & gear > covers" in path:
                score += 90
            if "atv & utv covers" in path or "backpacks & bags" in path:
                score -= 45
        if "chicken coop door" in text_value and "backyard poultry supplies" in path:
            score += 18
        if "pool cue stick" in text_value and name == "other billiards":
            score += 20
        if "golf cooler bag" in text_value and name == "other golf accessories":
            score += 24
        if any(term in title_text for term in ("shower handle", "shower grab", "bathroom grab", "bath grab")) and name == "other shower & bathtub accessories":
            # A household shower safety handle is not an ATV grab bar. The
            # local US hierarchy has no dedicated bath grab-bar leaf, so this
            # is the closest product-function category.
            score += 30
        if concept_match and concept_match in path:
            score += 24
        scored.append((score, -priority, row, len(primary_hits), len(title_leaf_hits)))
    scored.sort(key=lambda item: (item[0], item[3], item[4], item[1], int(item[2].get("depth") or 0)), reverse=True)
    return [{**dict(row), "score": score, "tree_priority": -priority, "primary_name_matches": primary_hits, "title_name_matches": title_matches}
            for score, priority, row, primary_hits, title_matches in scored[:limit]]


def closest_local_category(db: Session, title: str, semantic_terms: list[str] | None = None) -> dict | None:
    """Return the closest real local taxonomy leaf, even for weak matches.

    Vine imports must carry a concrete eBay leaf ID into review. The normal
    resolver remains confidence-gated; this fallback is only for selecting a
    best guess from the synchronized local tree when that resolver cannot
    produce a confident candidate. It never invents IDs or selects a parent.
    """
    title_text = str(title or "").lower()
    primary_text = " ".join(str(value or "") for value in (semantic_terms or []) if str(value or "").strip()).lower()
    if not title_text.strip() and not primary_text.strip():
        return None
    trees = candidate_trees_for_listing(" ".join((primary_text, title_text)))
    # This last-resort resolver must not use an exact generic leaf name (for
    # example "Storage") to jump into tree 100. Search Motors only when the
    # product identity itself is a Motors product and source evidence backs
    # that classification; otherwise the fallback domain is EBAY_US only.
    motors_allowed = motors_path_is_semantically_required(title_text) and any(
        marker in primary_text for marker in ("automotive", "motorcycle", "powersports", "rv", "boat", "marine", "fitment")
    )
    if not motors_allowed:
        trees = [("EBAY_US", "0")]
    stop = {
        "the", "for", "and", "with", "from", "new", "inch", "inches", "pack", "piece", "pieces",
        "adult", "adults", "kids", "children", "women", "men", "size", "one", "set", "count",
        "professional", "portable", "premium", "upgrade", "automatic", "electric", "wireless", "smart",
        "black", "white", "green", "blue", "red", "gray", "grey", "large", "small", "heavy", "duty",
        "product", "device", "equipment", "accessory", "accessories", "parts", "tool", "tools", "kit", "kits",
    }
    def tokens(value: str) -> set[str]:
        found = {token for token in re.findall(r"[a-z0-9]+", value) if len(token) > 2 and token not in stop}
        return {token[:-1] if token.endswith("s") and not token.endswith("ss") and len(token) > 4 else token for token in found}
    title_tokens = tokens(title_text)
    primary_tokens = tokens(primary_text)
    cache = db.info.setdefault("posterpro.ebay_taxonomy.all_leaves", {})
    scored: list[tuple[tuple, dict]] = []
    from sqlalchemy import text
    db.flush()
    for priority, (marketplace, tree_id) in enumerate(trees):
        key = (marketplace, tree_id)
        leaves = cache.get(key)
        if leaves is None:
            leaves = [dict(row) for row in db.execute(text(
                "select category_id,category_name,parent_category_id,leaf,full_path,depth,category_tree_version,marketplace,category_tree_id "
                "from ebay_taxonomy_categories where marketplace=:marketplace and category_tree_id=:tree and leaf=true"
            ), {"marketplace": marketplace, "tree": tree_id}).mappings().all()]
            cache[key] = leaves
        for row in leaves:
            name = str(row.get("category_name") or "")
            path = str(row.get("full_path") or name)
            name_tokens = tokens(name.lower())
            path_tokens = tokens(path.lower())
            primary_hits = len(primary_tokens & name_tokens)
            title_hits = len(title_tokens & name_tokens)
            path_hits = len((primary_tokens | title_tokens) & path_tokens)
            score = (primary_hits * 14) + (title_hits * 8) + (path_hits * 2)
            if name.lower() in primary_text:
                score += 30
            if name.lower() in title_text:
                score += 20
            flags = semantic_guard_flags(title, path)
            score -= 30 * len(flags)
            # Prefer a specific leaf and the product's likely taxonomy domain
            # after semantic fit; stable text/ID tie-breaks make imports repeatable.
            rank = (score, title_hits + primary_hits, path_hits, int(row.get("depth") or 0), -priority,
                    name.lower(), str(row.get("category_id") or ""))
            scored.append((rank, {**row, "score": score, "title_name_matches": title_hits,
                                  "primary_name_matches": primary_hits, "semantic_flags": flags,
                                  "selection": "closest_local_leaf_fallback"}))
    # A "closest" leaf still needs some product/category evidence. Selecting
    # an arbitrary row from a sparse or mismatched tree would create a valid
    # looking but false eBay category assignment.
    meaningful = [entry for entry in scored if entry[1].get("title_name_matches", 0)
                  or entry[1].get("primary_name_matches", 0)]
    if not meaningful:
        return None
    return max(meaningful, key=lambda entry: entry[0])[1]
