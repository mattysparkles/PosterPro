from types import SimpleNamespace

from sqlalchemy import text

from app.services.ebay_taxonomy import (
    semantic_guard_flags, candidate_trees_for_listing, resolve_local_category,
    closest_local_category, category_by_id, listing_has_motors_semantics,
    validate_listing_category,
)
from app.services.vine_taxonomy_repair import validate_or_resolve_vine_category

def test_semantic_guard_rejects_rv_collectibles():
    assert 'vehicle_product_in_collectibles' in semantic_guard_flags('12V RV Wall Sconce LED', 'Root > Collectibles > Holiday & Seasonal > Lights')

def test_semantic_guard_accepts_rv_lighting():
    assert semantic_guard_flags('12V RV Wall Sconce LED', 'Root > eBay Motors > RV, Trailer & Camper Parts > Interior Lighting') == []


def test_semantic_guard_rejects_four_wheel_drive_actuator_in_license_plates():
    assert "4wd_actuator_in_license_plate_category" in semantic_guard_flags(
        "Dorman 4WD Actuator 600-103", "Root > Collectibles > Other US License Plates",
    )


def test_true_motors_semantics_can_use_specific_description_fitment():
    listing = SimpleNamespace(
        title="TYC 17-5195-00 Automotive Intake Head Slate",
        description="New right reflector designed for 1996-2000 Toyota RAV4 vehicles.",
        source_metadata={},
    )
    assert listing_has_motors_semantics(listing) is True


def test_semantic_guard_rejects_wrong_motors_subcategory_for_rv_generator_and_marker_lights():
    assert "rv_generator_exhaust_in_marine_engine_category" in semantic_guard_flags(
        "Cummins Onan RV Generator Exhaust Kit", "Root > eBay Motors > Boat Parts > Exhaust Systems"
    )


def test_semantic_guard_rejects_provider_observed_category_collisions():
    assert "rv_lighting_in_insect_control_category" in semantic_guard_flags(
        "fouwick RV Interior LED Light Fixtures 12V",
        "Root > Business & Industrial > Pest Control > Adhesive Insect Light Traps",
    )
    assert "scale_labels_in_stickers_category" in semantic_guard_flags(
        "Scale Labels 8040 Printing Scale Label",
        "Root > Crafts > Multi-Purpose Craft Supplies > Stickers",
    )
    assert "utv_windshield_in_snowmobile_category" in semantic_guard_flags(
        "StarknightMT Flip Windshield for Ranger 800 Fullsize 570",
        "Root > eBay Motors > Snowmobile Parts > Windshields",
    )
    assert "side_marker_lights_in_turn_signal_category" in semantic_guard_flags(
        "LED Side Marker/Clearance Lights", "Root > eBay Motors > Turn Signal Light Assemblies"
    )


def test_semantic_guard_rejects_failed_batch_25001_category_collisions():
    cases = [
        ("RV Water Service Panel, 4-Function Fill Valve with Heater Bypass", "Booster Water Heaters", "rv_water_service_part_in_commercial_booster_heaters"),
        ("Heated Drinking Water Hose for RV, 75FT, -45°F Freeze Protection", "Fire Hoses", "heated_rv_water_hose_in_fire_hoses"),
        ("ELECTOP Wheel Lock with Alarm for Cars", "Alarm Control Panels & Keypads", "vehicle_wheel_lock_in_alarm_control_panels"),
        ("Corner Cut RV Mattress Topper 73 x 50", "Right Angle Drills", "bedding_in_right_angle_drills"),
        ("Electric Nail Clippers for Seniors", "Flower Nails", "personal_nail_clipper_in_cake_decorating"),
        ("12V LED RV Tiffany Wall Sconce Lamp with ON/Off Switch", "Smart Wall Switches & In-Line Modules", "rv_light_fixture_in_smart_switches"),
        ("Club Car Tempo LED Deluxe Light Kit", "Adhesive Insect Light Traps", "golf_cart_light_kit_in_insect_control"),
    ]
    for title, category, expected in cases:
        assert expected in semantic_guard_flags(title, f"Root > {category}")

def test_boat_tubing_tow_rope_uses_non_motors_towables_leaf(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','71169','Tubing & Towables',null,true,'Root > Sporting Goods > Water Sports > Wakeboarding & Waterskiing > Tubing & Towables',5)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_MOTORS_US','100','83','123703','Marine Rope',null,true,'Root > eBay Motors > Parts & Accessories > Boat Parts > Marine Rope',4)"))
    db_session.commit()
    title = "3K 60FT Boat Tow Rope for Tubing Fits 1-3 Person Towable Tubes, Floating Tube"
    assert candidate_trees_for_listing(title)[0][1] == "0"
    candidates = resolve_local_category(db_session, title, limit=10)
    assert candidates and candidates[0]["category_id"] == "71169", candidates[:3]


def test_bms_board_and_greenworks_battery_use_non_motors_categories(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("EBAY_US", "0", "185140", "Printed Circuit Boards (PCBs)", "Root > Business & Industrial > Electrical Equipment & Supplies > Electronic Components & Semiconductors > Circuit Boards & Prototyping > Printed Circuit Boards (PCBs)"),
        ("EBAY_US", "0", "260204", "Power Tool Batteries", "Root > Home & Garden > Tools & Workshop Equipment > Power Tool & Air Tool Accessories > Power Tool Batteries"),
        ("EBAY_MOTORS_US", "100", "263256", "Battery Management Systems (BMS)", "Root > eBay Motors > Battery Management Systems (BMS)"),
    ]
    for market, tree, cid, name, path in rows:
        db_session.execute(text("insert into ebay_taxonomy_categories values (:m,:t,'134',:id,:name,null,true,:path,5)"), {"m": market, "t": tree, "id": cid, "name": name, "path": path})
    db_session.commit()
    bms = resolve_local_category(db_session, "8S-14S 150A Lithium BMS Protection Board Hardware Ternary Polymer", limit=10)
    tool_battery = resolve_local_category(db_session, "Compatible with GreenWorks G-MAX40V tools, 40V 6Ah battery with upgraded BMS", limit=10)
    assert bms and bms[0]["category_id"] == "185140", bms[:3]
    assert tool_battery and tool_battery[0]["category_id"] == "260204", tool_battery[:3]

def test_semantic_guard_rejects_cigarette_tubes_in_motors_tubing_leaf():
    assert "tobacco_related_product_in_motors" in semantic_guard_flags(
        "HotRod Cigarette Tubes Super Menthol", "Root > eBay Motors > Fork Legs & Tubes"
    )

def test_local_resolver_does_not_map_cigarette_tubes_by_generic_tube_overlap(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_MOTORS_US','100','83','178040','Fork Legs & Tubes','263032',true,'Root > Root > eBay Motors > Fork Legs & Tubes',4)"))
    db_session.commit()
    assert resolve_local_category(
        db_session,
        "HotRod Cigarette Tubes Super Menthol 2000 Count",
        ["Tobacco", "Cigarette Making Supplies", "Cigarette Tubes"],
    ) == []

def test_semantic_guard_rejects_monitor_mount_as_monitor():
    assert 'mount_misclassified_as_monitor' in semantic_guard_flags('Car Headrest VESA Monitor Mount', 'Root > Computers/Tablets & Networking > Monitors')

def test_semantic_guard_rejects_phone_display_wrong_branch():
    assert 'phone_display_outside_phone_parts' in semantic_guard_flags('iPhone 16 Screen Replacement LCD Digitizer', 'Root > Computers/Tablets & Networking > Monitors')

def test_vehicle_products_prioritize_motors_tree():
    # Application words alone are not fitment evidence.  This consumer RV
    # accessory must be considered in the Inventory-compatible tree first.
    assert candidate_trees_for_listing("RV grab handle")[0] == ("EBAY_US", "0")
    assert candidate_trees_for_listing("Marine alternator")[0] == ("EBAY_MOTORS_US", "100")

def test_contemporary_electronics_do_not_prioritize_motors_from_application_words():
    assert candidate_trees_for_listing("12V RV Dome LED Light") == [("EBAY_US", "0"), ("EBAY_MOTORS_US", "100")]
    assert candidate_trees_for_listing("Battery BMS controller for e-bike") == [("EBAY_US", "0"), ("EBAY_MOTORS_US", "100")]

def test_ordinary_products_prioritize_ebay_us_tree():
    assert candidate_trees_for_listing("Levoit humidifier")[0] == ("EBAY_US", "0")


def test_closet_organizer_fallback_cannot_choose_motors_storage(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','43503','Closet Organizers',null,true,'Root > Home & Garden > Home Organization > Closet Organizers',4)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_MOTORS_US','100','83','124103','Storage',null,true,'Root > eBay Motors > Boat Parts > Accessories & Gear > Storage',5)"))
    db_session.commit()
    result = closest_local_category(db_session, "Hanging Closet Organizer, Foldable Mesh Travel Storage Bag with Zipper Bag", [])
    assert result and result["category_id"] == "43503"
    assert result["marketplace"] == "EBAY_US" and result["category_tree_id"] == "0"


def test_numeric_category_collision_never_changes_qualified_tree_owner(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','42','Home Storage',null,true,'Root > Home > Storage',3)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_MOTORS_US','100','83','42','Boat Storage',null,true,'Root > Motors > Boat Parts > Storage',4)"))
    db_session.commit()
    assert category_by_id(db_session, "42", "0", "EBAY_US")["category_name"] == "Home Storage"
    assert category_by_id(db_session, "42", "100", "EBAY_MOTORS_US")["category_name"] == "Boat Storage"


def test_missing_tree_metadata_never_defaults_to_motors_on_id_hit(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_MOTORS_US','100','83','124103','Storage',null,true,'Root > Motors > Boat Parts > Storage',5)"))
    db_session.commit()
    listing = SimpleNamespace(category_id="124103", title="Hanging Closet Organizer", source_metadata={}, item_specifics={})
    result = validate_listing_category(db_session, listing)
    assert result["status"] == "category_revalidation_required"
    assert result["marketplace"] == "EBAY_US" and result["tree_id"] == "0"


def test_true_motors_requires_structured_fitment_corroboration():
    generic = SimpleNamespace(
        title="Hanging Closet Organizer", source_metadata={}, item_specifics={},
    )
    fitment = SimpleNamespace(
        title="UTV Turn Signal Kit", source_metadata={"amazon_product_facts": {"breadcrumbs": ["Automotive", "Motorcycle & Powersports"]}},
        item_specifics={"Compatible Vehicle": "UTV"},
    )
    assert not listing_has_motors_semantics(generic)
    assert listing_has_motors_semantics(fitment)


def test_semantically_bizarre_leaf_names_force_category_reconsideration():
    assert "electronic_transceiver_in_unrelated_chips_category" in semantic_guard_flags(
        "LONELY BINARY RS485 Transceiver Modules MAX485 & MAX3485 Chips",
        "Root > Home & Garden > Food & Beverages > Snacks > Chips",
    )
    assert "rv_light_in_memorabilia_color_category" in semantic_guard_flags(
        "Rvhouse 12V RV Dome Light Tri-Color Dimmable",
        "Root > Entertainment Memorabilia > Photographs > Color",
    )
    assert "measurement_gauge_in_memorabilia_color_category" in semantic_guard_flags(
        "YOWEXA Paint Thickness Gauge for Cars, Color Screen",
        "Root > Entertainment Memorabilia > Photographs > Color",
    )


def test_local_resolver_uses_concepts_for_transceiver_lamp_and_paint_gauge(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("EBAY_US", "0", "181905", "Interface ICs", "Root > Business & Industrial > Electronic Components > Integrated Circuits > Interface ICs"),
        ("EBAY_US", "0", "112581", "Lamps", "Root > Home & Garden > Lamps, Lighting & Ceiling Fans > Lamps"),
        ("EBAY_US", "0", "42632", "Other Measuring & Layout Tools", "Root > Home & Garden > Tools & Workshop Equipment > Measuring & Layout Tools > Other Measuring & Layout Tools"),
        ("EBAY_US", "0", "179179", "Chips", "Root > Home & Garden > Food & Beverages > Snacks > Chips"),
        ("EBAY_US", "0", "60409", "Color", "Root > Entertainment Memorabilia > Photographs > Color"),
    ]
    for market, tree, cid, name, path in rows:
        db_session.execute(text("insert into ebay_taxonomy_categories values (:m,:t,'134',:id,:name,null,true,:path,5)"), {"m": market, "t": tree, "id": cid, "name": name, "path": path})
    db_session.commit()
    examples = [
        ("LONELY BINARY 12-Pack RS485 Transceiver Modules MAX485 MAX3485 Chips", "181905"),
        ("Rvhouse 12V RV Dome Light 1000LM Tri-Color Dimmable", "112581"),
        ("YOWEXA Paint Thickness Gauge for Cars, Color Screen", "42632"),
    ]
    for title, expected in examples:
        candidates = resolve_local_category(db_session, title, limit=10)
        assert candidates and candidates[0]["category_id"] == expected, (title, candidates[:5])


def test_local_resolver_uses_exact_product_nouns_for_common_google_items(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("EBAY_US", "0", "134", "16204", "Toner Cartridges", "Root > Computers/Tablets & Networking > Printers, Scanners & Supplies > Printer Ink, Toner & Paper > Toner Cartridges"),
        ("EBAY_US", "0", "134", "117042", "Controllers & Attachments", "Root > Video Games & Consoles > Video Game Accessories > Controllers & Attachments"),
        ("EBAY_US", "0", "134", "115949", "Programmable Thermostats", "Root > Home & Garden > Home Improvement > Heating, Cooling & Air > Thermostats > Programmable Thermostats"),
        ("EBAY_MOTORS_US", "100", "83", "184732", "Thermostats", "Root > Root > eBay Motors > Parts & Accessories > Commercial Truck Parts > Engine Cooling > Thermostats"),
    ]
    for marketplace, tree, version, cid, name, path in rows:
        db_session.execute(
            text("insert into ebay_taxonomy_categories values (:m,:t,:v,:id,:name,null,true,:path,5)"),
            {"m": marketplace, "t": tree, "v": version, "id": cid, "name": name, "path": path},
        )
    db_session.commit()

    examples = [
        ("HP LaserJet Toner Cartridge", "16204"),
        ("Namco Video Game Controller", "117042"),
        ("Nest Learning Thermostat", "115949"),
    ]
    for title, expected_id in examples:
        candidates = resolve_local_category(db_session, title, limit=10)
        assert candidates
        assert candidates[0]["category_id"] == expected_id, (title, candidates[:3])


def test_local_resolver_prefers_specific_leaf_for_current_vine_category_blockers(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("EBAY_US", "0", "176973", "Cases, Covers, Keyboard Folios", "Root > Computers/Tablets & Networking > Tablet & eBook Reader Accs > Cases, Covers, Keyboard Folios"),
        ("EBAY_US", "0", "33963", "Keyboards & Keypads", "Root > Computers/Tablets & Networking > Keyboards, Mice & Pointers > Keyboards & Keypads"),
        ("EBAY_MOTORS_US", "100", "261484", "Satellite Mounting Plates", "Root > Root > eBay Motors > Parts & Accessories > RV, Trailer & Camper Parts & Accessories > Satellite & TV Technology > Satellite Mounting Plates"),
        ("EBAY_US", "0", "159878", "Emergency Alert Systems", "Root > Health & Beauty > Medical & Mobility > Daily Living Aids > Emergency Alert Systems"),
        ("EBAY_MOTORS_US", "100", "135895", "Other Water & Sanitary Systems", "Root > Root > eBay Motors > Parts & Accessories > RV, Trailer & Camper Parts & Accessories > Water & Sanitation Systems > Other Water & Sanitary Systems"),
        ("EBAY_US", "0", "29946", "Microphones & Wireless Systems", "Root > Musical Instruments & Gear > Pro Audio Equipment > Microphones & Wireless Systems"),
        ("EBAY_US", "0", "165969", "Plastic Action Games", "Root > Toys & Hobbies > Games > Plastic Action Games"),
    ]
    for marketplace, tree, cid, name, path in rows:
        db_session.execute(text("insert into ebay_taxonomy_categories values (:m,:t,'134',:id,:name,null,true,:path,5)"), {"m": marketplace, "t": tree, "id": cid, "name": name, "path": path})
    db_session.commit()

    examples = [
        ("Keyboard Case for Xiaomi Redmi Pad 2 Pro 12.1 inch tablet", "176973"),
        ("Full Set of Keycaps Replacement for Logitech G512 G513 RGB Gaming", "33963"),
        ("Clamp Mount for Starlink Mini Pole Clamp for RV Boat Pipe Adapters", "261484"),
        ("Smart Caregiver Wireless Pull Cord Call Button & Alert Monitor for Elderly", "159878"),
        ("SEAFLO SFVF-01K Holding Tank Vent Filter Kit with Cartridge", "135895"),
        ("SWIFF AUDIO UHF Wireless Guitar System Dual Interface", "29946"),
        ("Cyberpunk Reflex Drop Sticks Challenge Game with Adjustable Speed Modes", "165969"),
    ]
    for title, expected_id in examples:
        candidates = resolve_local_category(db_session, title, limit=10)
        assert candidates, title
        assert candidates[0]["category_id"] == expected_id, (title, candidates[:3])


def test_local_resolver_maps_well_pump_pressure_switch_to_pump_accessory_leaf(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','184106','Pump Pressure Switches',null,true,'Root > Business & Industrial > Hydraulics, Pneumatics, Pumps & Plumbing > Pumps & Pump Accessories > Pump Parts & Accessories > Pump Pressure Switches',7)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','260483','Pressure Switches',null,true,'Root > Home & Garden > Home Improvement > Heating, Cooling & Air > Heating & Cooling: Parts & Accessories > Pressure Switches',6)"))
    db_session.commit()

    result = resolve_local_category(db_session, "Smart Pressure Switch with Gauge for Well Pump", limit=10)
    assert result
    assert result[0]["category_id"] == "184106", result[:3]
    assert result[0]["_concept_match"] == "pump pressure switches"


def test_samsung_bn44_power_board_uses_verified_tv_boards_leaf(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','163768','TV Boards, Parts & Components',null,true,'Root > Consumer Electronics > TV, Video & Home Audio > TV, Video & Audio Parts > TV Boards, Parts & Components',4)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','181906','Power Management ICs',null,true,'Root > Business & Industrial > Electrical Equipment & Supplies > Electronic Components > Power Management ICs',6)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_MOTORS_US','100','83','169391','Power Inverters',null,true,'Root > Root > eBay Motors > Parts & Accessories > Electronic Accessories > Power Inverters',5)"))
    db_session.commit()

    candidates = resolve_local_category(
        db_session,
        "BN44-00932G Power Supply Board for Electronics",
        ["Model BN44-00932G", "Type Power Supply"],
        limit=10,
    )

    assert candidates
    assert candidates[0]["category_id"] == "163768", candidates[:3]
    assert candidates[0]["marketplace"] == "EBAY_US"
    assert candidates[0]["category_tree_id"] == "0"


def test_krups_home_coffee_spice_grinder_uses_consumer_coffee_grinders_leaf(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','32882','Coffee Grinders',null,true,'Root > Home & Garden > Kitchen, Dining & Bar > Small Kitchen Appliances > Coffee, Tea & Espresso Makers > Coffee Grinders',7)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','53305','Coffee & Espresso Grinders',null,true,'Root > Business & Industrial > Restaurant & Food Service > Commercial Coffee, Cocoa & Tea Equipment > Coffee & Espresso Grinders',6)"))
    db_session.commit()

    candidates = resolve_local_category(
        db_session,
        "KRUPS GX4100 Coffee / Spice Grinder",
        ["Brand KRUPS", "Model GX4100", "Type Coffee / Spice Grinder"],
        limit=10,
    )

    assert candidates
    assert candidates[0]["category_id"] == "32882", candidates[:3]
    assert candidates[0]["marketplace"] == "EBAY_US"


def test_product_tree_resolver_prefers_camera_leaf_over_camera_parts(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("48638", "Security Cameras", "Root > Consumer Electronics > Home Surveillance > Security Cameras", 4),
        ("48637", "Security Camera Cables & Adapters", "Root > Consumer Electronics > Home Surveillance Parts > Security Camera Cables & Adapters", 5),
    ]
    for cid, name, path, depth in rows:
        db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134',:id,:name,null,true,:path,:depth)"), {"id": cid, "name": name, "path": path, "depth": depth})
    db_session.commit()
    result = resolve_local_category(db_session, "Outdoor security camera wireless", ["Security Camera"], limit=5)
    assert result[0]["category_id"] == "48638"
    assert "security_camera_in_camera_accessories" not in semantic_guard_flags("Outdoor security camera", result[0]["full_path"])
    assert "security_camera_in_camera_accessories" in semantic_guard_flags("Outdoor security camera", rows[1][2])


def test_vehicle_glass_and_sleep_product_semantic_guards():
    assert "windshield_glass_in_wiper_parts" in semantic_guard_flags("UTV Windshield", "Root > Windshield Wipers & Washers")
    assert "sleep_pillow_in_adult_or_collectible" in semantic_guard_flags("Smart Anti-Snore Pillow", "Root > Sex Pillows & Wedges")
    assert candidate_trees_for_listing("UTV Ranger 800 windshield")[0] == ("EBAY_MOTORS_US", "100")
    assert "bathroom_grab_handle_in_motors" in semantic_guard_flags(
        "Stainless steel grab bars, 12 inch shower handle",
        "Root > eBay Motors > ATV Parts > Grab Bars & Handles",
    )
    assert semantic_guard_flags(
        "RV entry grab handle",
        "Root > eBay Motors > RV Parts > Interior > Door Handles",
    ) == []


def test_product_identity_guards_reject_modifier_collisions():
    assert "pool_cue_outside_billiards" in semantic_guard_flags("Carbon fiber pool cue stick", "Root > Home & Garden > Pools > Above-Ground Pools")
    assert "pool_cue_stick_in_cue_accessory" in semantic_guard_flags("Carbon fiber pool cue stick", "Root > Sporting Goods > Billiards > Cue Balls")
    assert "cooler_bag_in_golf_bag_tags" in semantic_guard_flags("Golf cooler bag", "Root > Sporting Goods > Golf > Golf Accessories > Bag Tags")
    assert "water_accumulator_in_boiler_parts" in semantic_guard_flags("Accumulator expansion tank reduces pump cycling", "Root > Home Improvement > Boiler Expansion Tanks")
    assert "pcb_holder_outside_electronics_tools" in semantic_guard_flags("Adjustable PCB holder and vise", "Root > Home & Garden > Lighting & Lamps")


def test_vine_category_repair_uses_local_tree_and_requires_clear_semantic_winner(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','48638','Security Cameras',null,true,'Root > Consumer Electronics > Surveillance & Smart Home Electronics > Home Surveillance > Security Cameras',5)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','162480','Other Camera & Photo Accs',null,true,'Root > Cameras & Photo > Camera, Drone & Photo Accessories > Other Camera & Photo Accs',4)"))
    db_session.commit()
    listing = SimpleNamespace(
        title="Dual Lens 3MP Window Camera for Home Security, No Drill",
        category_id="162480",
        category_suggestion="Other Camera & Photo Accs",
        source_metadata={},
        marketplace_data={},
        item_specifics={},
    )

    result = validate_or_resolve_vine_category(db_session, listing)

    assert result["status"] == "resolved"
    assert result["category_id"] == "48638"
    assert result["marketplace"] == "EBAY_US"
    assert result["tree_id"] == "0"
    assert result["confidence"] is None
    assert listing.category_id == "48638"
    assert listing.source_metadata["category_validation"]["status"] == "valid_local"
    assert listing.source_metadata["category_validation"]["confidence"] is None


def test_vine_category_change_invalidates_preflight_and_requeues_ready_listing(db_session):
    from app.models.enums import ListingStatus

    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','48638','Security Cameras',null,true,'Root > Consumer Electronics > Home Surveillance > Security Cameras',4)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','162480','Other Camera & Photo Accs',null,true,'Root > Cameras & Photo > Accessories > Other Camera & Photo Accs',4)"))
    db_session.commit()
    listing = SimpleNamespace(
        title="Dual Lens 3MP Window Camera for Home Security, No Drill",
        category_id="162480",
        category_suggestion="Other Camera & Photo Accs",
        source_metadata={},
        marketplace_data={"marketplace_preflight": {"by_marketplace": {"ebay": {"status": "pass"}}}},
        item_specifics={},
        status=ListingStatus.ready,
        needs_review=True,
        processing_state="needs_attention",
        processing_stage="preflight",
        processing_blocking_reason="category leaf needs correction",
        processing_error_stage="category",
        processing_next_retry_at="later",
    )

    result = validate_or_resolve_vine_category(db_session, listing)

    assert result["category_id"] == "48638"
    assert listing.status == ListingStatus.draft
    assert listing.needs_review is False
    assert listing.processing_state == "queued"
    assert listing.processing_blocking_reason is None
    assert listing.marketplace_data["marketplace_preflight"]["stale"] is True
    assert listing.marketplace_data["marketplace_preflight"]["by_marketplace"]["ebay"]["stale"] is True


def test_vine_category_repair_does_not_touch_archived_or_sold_rows(db_session):
    archived = SimpleNamespace(
        title="Smart Home Security Camera",
        category_id="162480",
        source_metadata={},
        marketplace_data={},
        custom_labels=["amazon_vine", "archived_vine"],
        sold_at=None,
    )
    sold = SimpleNamespace(
        title="Smart Home Security Camera",
        category_id="162480",
        source_metadata={},
        marketplace_data={},
        custom_labels=[],
        sold_at=object(),
    )

    assert validate_or_resolve_vine_category(db_session, archived)["status"] == "skipped_terminal"
    assert validate_or_resolve_vine_category(db_session, sold)["status"] == "skipped_terminal"
    assert archived.category_id == sold.category_id == "162480"
    assert archived.source_metadata == sold.source_metadata == {}


def test_vine_local_resolver_uses_product_noun_not_accidental_taxonomy_tokens(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("181855", "Wire Stripping Machines", "Root > Business & Industrial > Electrical Tools > Wire Stripping Machines", "0", 4),
        ("27414", "Other Fishing Equipment", "Root > Sporting Goods > Fishing > Fishing Equipment > Other Fishing Equipment", "0", 4),
        ("20445", "Bed Pillows", "Root > Home & Garden > Bedding > Bed Pillows", "0", 3),
        ("261718", "Bed Pillows", "Root > Collectibles > Linens & Textiles > Bed & Bath Linens > Bed Pillows", "0", 5),
        ("56172", "Cables & Adapters", "Root > Consumer Electronics > Portable Audio & Headphones > Portable Audio Accessories > Cables & Adapters", "0", 5),
        ("48618", "Battery Chargers", "Root > Consumer Electronics > Batteries & Power > Battery Chargers", "0", 3),
        ("107062", "Other", "Root > eBay Motors > Performance & Racing Parts > Electrical Components > Other", "100", 4),
    ]
    for cid, name, path, tree, depth in rows:
        market = "EBAY_MOTORS_US" if tree == "100" else "EBAY_US"
        db_session.execute(text("insert into ebay_taxonomy_categories values (:market,:tree,'134',:id,:name,null,true,:path,:depth)"), {"market": market, "tree": tree, "id": cid, "name": name, "path": path, "depth": depth})
    db_session.commit()

    cases = [
        ("Manual Wire Stripper Machine for Scrap Copper Recycling", "181855"),
        ("12V Cordless Electric Fillet Knife", "27414"),
        ("Smart AI Anti-Snore System Pillow", "20445"),
        ("Bluetooth Receiver Wireless Audio Adapter", "56172"),
    ]
    for title, expected in cases:
        candidates = resolve_local_category(db_session, title, limit=10)
        assert candidates[0]["category_id"] == expected, (title, candidates[:3])

    assert "wire_stripper_outside_wire_stripping_machines" in semantic_guard_flags("Manual wire stripper", "Root > Crafts > Die Cut Machines")
    assert "fishing_knife_in_automotive_parts" in semantic_guard_flags("12V fillet knife", "Root > eBay Motors > Auto Performance Parts")
    assert "sleep_pillow_in_collectible_or_unrelated_pillow_category" in semantic_guard_flags("Anti-snore pillow", "Root > Collectibles > Bed Pillows")


def test_vine_local_resolver_handles_ordinary_smart_home_computer_and_heating_products(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("185056", "Smart Door Locks", "Root > Home & Garden > Home Improvement > Smart Door Locks", 4),
        ("183833", "Lock Parts", "Root > Business & Industrial > Access Control > Lock Parts", 4),
        ("3709", "Laptop Docking Stations", "Root > Computers/Tablets & Networking > Laptop & Desktop Accessories > Laptop Docking Stations", 3),
        ("44932", "USB Cables, Hubs & Adapters", "Root > Computers/Tablets & Networking > Computer Cables & Connectors > USB Cables, Hubs & Adapters", 3),
        ("48638", "Security Cameras", "Root > Consumer Electronics > Home Surveillance > Security Cameras", 4),
        ("185050", "Security Camera Cases & Housings", "Root > Consumer Electronics > Home Surveillance Parts > Security Camera Cases & Housings", 5),
        ("20445", "Bed Pillows", "Root > Home & Garden > Bedding > Bed Pillows", 3),
        ("20613", "Space Heaters", "Root > Home & Garden > Home Improvement > Heating & Cooling Appliances > Space Heaters", 4),
        ("106402", "Patio Heaters", "Root > Home & Garden > Outdoor Heating > Patio Heaters", 4),
        ("179802", "Running Belts", "Root > Sporting Goods > Fitness, Running & Yoga > Running Belts", 5),
        ("43304", "Cell Phone & Smartphone Parts", "Root > Cell Phones & Accessories > Cell Phone & Smartphone Parts", 3),
        ("261986", "Body Jewelry", "Root > Jewelry & Watches > Body Jewelry", 2),
        ("181396", "Portable Showers", "Root > Sporting Goods > Camping & Hiking > Camping Hygiene & Sanitation > Portable Showers", 5),
    ]
    for cid, name, path, depth in rows:
        db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134',:id,:name,null,true,:path,:depth)"), {"id": cid, "name": name, "path": path, "depth": depth})
    db_session.commit()
    cases = [
        ("TT-Lock APP Smart Access Control System IP67 Waterproof RFID Fingerprint Card", ["Security"], "185056"),
        ("Surface Pro 7 Docking Station, 7-in-1 USB C Hub with 3 X USB 3.0 Ports", ["Wireless Hub"], "3709"),
        ("Dual Lens Window Camera for Home Security", ["Nail Drill"], "48638"),
        ("Smart AI Anti-Snore System Pillow", ["Pillow"], "20445"),
        ("PAULINN Propane Heater Indoor Outdoor Patio Heaters", ["Heater"], "106402"),
        ("Portable Electric Space Heater", ["Heater"], "20613"),
        ("No-Bounce Running Belt Waist Pack, Fits 6.7 In Phones", ["Belt"], "179802"),
        ("REVVL 7 5G LCD Display Screen Digitizer Assembly", ["Electronic Device Replacement Part"], "43304"),
        ("TOPGRILLZ Grillz for Men, Iced Out 10 Teeth Grills", ["Dental"], "261986"),
        ("Foldable Camping Shower Base and Basin", ["Shower"], "181396"),
    ]
    for title, source_terms, expected in cases:
        candidates = resolve_local_category(db_session, title, semantic_terms=source_terms, limit=10)
        assert candidates[0]["category_id"] == expected, (title, candidates[:3])


def test_handheld_inkjet_barcode_printer_prefers_label_printer_leaf(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("184274", "Label Printers & Applicators", "Root > Business & Industrial > Material Handling > Packing & Shipping > Shipping Labels & Tags > Label Printers & Applicators", 6),
        ("1245", "Printers", "Root > Computers/Tablets & Networking > Printers, Scanners & Supplies > Printers", 3),
        ("181883", "LED Bar Arrays", "Root > Business & Industrial > Electronic Components > LEDs > LED Bar Arrays", 5),
    ]
    for cid, name, path, depth in rows:
        db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134',:id,:name,null,true,:path,:depth)"), {"id": cid, "name": name, "path": path, "depth": depth})
    db_session.commit()

    title = "YUEHISY Handheld Inkjet Printer with HD LED Touch Screen for QR Code Barcode on Various Surfaces"
    candidates = resolve_local_category(db_session, title, limit=10)
    assert candidates[0]["category_id"] == "184274", candidates[:3]
    assert candidates[0]["_concept_match"] == "label printers"


def test_local_resolver_distinguishes_tool_workbench_and_vinyl_record_leaf(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("33088", "Work Benches", "Root > Home & Garden > Tools & Workshop Equipment > Workshop Equipment > Work Benches", 5),
        ("262020", "Jewelry Tools & Workbenches", "Root > Jewelry & Watches > Jewelry Care, Design & Repair > Jewelry Tools & Workbenches", 4),
        ("176985", "Vinyl Records", "Root > Music > Vinyl Records", 3),
        ("33006", "John, Elton", "Root > Entertainment Memorabilia > Music Memorabilia > Rock & Pop > Artists J > John, Elton", 7),
        ("117042", "Controllers & Attachments", "Root > Video Games & Consoles > Video Game Accessories > Controllers & Attachments", 5),
    ]
    for cid, name, path, depth in rows:
        db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134',:id,:name,null,true,:path,:depth)"), {"id": cid, "name": name, "path": path, "depth": depth})
    db_session.commit()

    workbench = resolve_local_category(db_session, "Black & Decker Workbench", ["Tools"], limit=10)
    record = resolve_local_category(db_session, "Elton John Vinyl Record", ["music"], limit=10)
    adaptive_kit = resolve_local_category(db_session, "Logitech Adaptive Gaming Kit", ["Gaming Accessories"], limit=10)
    assert workbench[0]["category_id"] == "33088", workbench[:3]
    assert record[0]["category_id"] == "176985", record[:3]
    assert adaptive_kit[0]["category_id"] == "117042", adaptive_kit[:3]


def test_local_resolver_matches_provider_category_punctuation_for_phone_parts_and_motors_shocks(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("43304", "Cell Phone & Smartphone Parts", "Root > Cell Phones & Accessories > Cell Phone & Smartphone Parts", "EBAY_US", "0"),
        ("33590", "Shocks, Struts & Assemblies", "Root > eBay Motors > Parts & Accessories > Car & Truck Parts > Steering & Suspension > Shocks, Struts & Assemblies", "EBAY_MOTORS_US", "100"),
    ]
    for cid, name, path, market, tree in rows:
        db_session.execute(text("insert into ebay_taxonomy_categories values (:market,:tree,'134',:id,:name,null,true,:path,5)"), {"market": market, "tree": tree, "id": cid, "name": name, "path": path})
    db_session.commit()

    phone = resolve_local_category(db_session, "iPhone XR Screen Replacement LCD Digitizer")
    shocks = resolve_local_category(db_session, "Rear Air Shocks Fits Chevy Tahoe Suburban 2015-2020")

    assert phone and phone[0]["category_id"] == "43304", phone[:3]
    assert shocks and shocks[0]["category_id"] == "33590", shocks[:3]


def test_resolver_reconsiders_recent_false_motors_categories_by_product_concept(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    rows = [
        ("261461", "Power Generator Parts", "Root > eBay Motors > RV, Trailer & Camper Parts > Electric Power Supplies > Power Generator Parts", "EBAY_MOTORS_US", "100"),
        ("50437", "Electrical & Lighting", "Root > eBay Motors > Boat Parts > Electrical & Lighting", "EBAY_MOTORS_US", "100"),
        ("33715", "Side Marker Light Assemblies", "Root > eBay Motors > Car & Truck Parts > Lighting > Side Marker Light Assemblies", "EBAY_MOTORS_US", "100"),
        ("135895", "Other Water & Sanitary Systems", "Root > eBay Motors > RV Parts > Water & Sanitation > Other Water & Sanitary Systems", "EBAY_MOTORS_US", "100"),
        ("261089", "Sewer & Winterizing Chemicals", "Root > eBay Motors > RV Parts > Water & Sanitation > Sewer & Winterizing Chemicals", "EBAY_MOTORS_US", "100"),
        ("20608", "Trash Cans & Wastebaskets", "Root > Home & Garden > Household Supplies > Trash Cans & Wastebaskets", "EBAY_US", "0"),
        ("101972", "Massage Pillows & Bolsters", "Root > Health & Beauty > Massage > Massage Pillows & Bolsters", "EBAY_US", "0"),
    ]
    for cid, name, path, market, tree in rows:
        db_session.execute(text("insert into ebay_taxonomy_categories values (:market,:tree,'134',:id,:name,null,true,:path,5)"), {"market": market, "tree": tree, "id": cid, "name": name, "path": path})
    db_session.commit()
    cases = [
        ("155-3481-02 Generator Exhaust Kit, Compatible with Cummins Onan Generator | RV", "261461"),
        ("Poiini Boat Navigation Lights, Red and Green Marine LED", "50437"),
        ("20Pcs LED Front Rear Side Marker/Clearance Lights", "33715"),
        ("Sewage Level Gauge, Marine Holding Tank Gauge for RVs", "135895"),
        ("Piufike RV Black Tank Treatment Pods", "261089"),
        ("Car Trash Can with Lid", "20608"),
        ("Face Down Massage Pillow for Sleeping", "101972"),
    ]
    for title, expected in cases:
        candidates = resolve_local_category(db_session, title, limit=100)
        assert candidates and candidates[0]["category_id"] == expected, (title, candidates[:3])
