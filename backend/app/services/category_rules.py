from __future__ import annotations

import re
from typing import Any


_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    # Canonical product families, intentionally separate from marketplace
    # taxonomy IDs. Destination adapters may map these paths independently.
    (("windshield wiper", "wiper blade", "wiper blades"), "Automotive > Replacement Parts > Wiper Blades"),
    (("serpentine belt", "drive belt", "v-belt"), "Automotive > Replacement Parts > Belts & Pulleys > Serpentine Belts"),
    (("fuel connector", "fuel line connector", "fuel fitting"), "Automotive > Replacement Parts > Fuel System Components"),
    (("handbag", "purse", "crossbody bag", "satchel", "tote bag", "clutch bag"), "Fashion > Bags & Handbags > Handbags"),
    (("cake pan", "baking pan", "bakeware", "bundt pan", "loaf pan", "muffin pan", "pie pan", "springform pan"), "Home & Garden > Kitchen, Dining & Bar > Bakeware"),
    (("universal travel adapter", "gan universal travel adapter"), "Consumer Electronics > Power Protection > Surge Protectors"),
    (("starlink mini", "starlink clamp"), "Consumer Electronics > Networking > Wireless Networking"),
    (("rv couch cover", "rv sofa bed cover"), "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Furniture Covers"),
    (("vlog camcorder", "vlog camera"), "Consumer Electronics > Cameras & Photo > Digital Cameras"),
    (("alternator", "alternators", "marine alternator"), "Automotive > Parts & Accessories > Charging & Starting Systems > Alternators & Generators"),
    (("mig welder", "mig welding", "flux core welder", "welding machine"), "Welders, Cutters & Torches > MIG Welders"),
    (("rv water pump", "rv fresh water pump", "rv pump"), "Automotive > RV, Trailer & Camper Parts & Accessories > Water & Sanitation Systems > Pumps"),
    (("rv windshield curtain", "rv door curtain", "rv curtain", "windshield curtain"), "Automotive > RV, Trailer & Camper Parts & Accessories > Interior Accessories > Curtains"),
    (("boat cover", "pontoon cover", "marine cover", "t-top boat cover"), "Boating > Parts & Accessories > Covers"),
    (("baitcasting reel", "fishing reel"), "Sporting Goods > Fishing > Reels"),
    (("coffee machine", "coffee maker", "drip coffee"), "Home & Garden > Kitchen, Dining & Bar > Small Kitchen Appliances > Coffee Machines"),
    (("screen replacement", "lcd replacement", "oled replacement", "display replacement"), "Cell Phones & Accessories > Cell Phone Parts > Screens & Digitizers"),
    (("sump pump", "submersible pump", "pool booster pump"), "Home & Garden > Pools & Spas > Pool Pumps"),
    (("humidifier", "humidifiers"), "Home & Garden > Household Appliances > Humidifiers"),
    (("audio player", "wireless audio player"), "Consumer Electronics > Home Audio > Portable Audio Players"),
    (("security camera", "window camera", "camcorder"), "Consumer Electronics > Cameras & Photo > Digital Cameras"),
    (("front grill", "grille", "grill cover"), "Automotive > Parts & Accessories > Car & Truck Parts > Grilles"),
    (("led light", "led lights", "wall sconce", "marker lights"), "Automotive > Parts & Accessories > Car & Truck Parts > Lighting & Lamps"),
    (("kill switch", "car lock", "wheel lock", "air horn"), "Automotive > Parts & Accessories > Car & Truck Parts > Locks & Hardware"),
    (("faucet", "shower faucet", "bathroom faucet"), "Home & Garden > Bathroom Fixtures > Bathroom Faucets"),
    (("shower seat",), "Home & Garden > Bathroom Fixtures > Shower Seats"),
    (("exhaust fan",), "Home & Garden > Bathroom Fixtures > Exhaust Fans"),
    (("mattress topper",), "Home & Garden > Furniture > Bedroom Furniture > Mattress Toppers"),
    (("luggage", "suitcase", "ski bag"), "Travel > Luggage & Travel Accessories > Luggage Sets"),
    (("heat gun",), "Consumer Electronics > Electrical Equipment & Supplies > Heat Guns"),
    (("motor speed controller", "pwm controller"), "Consumer Electronics > Electrical Equipment & Supplies > Motor Speed Controllers"),
    (("led controller", "wifi led controller"), "Consumer Electronics > Electrical Equipment & Supplies > LED Controllers"),
    (("gps tracker", "tracker card", "find my"), "Consumer Electronics > GPS & Accessories > GPS Trackers"),
    (("nfc", "rfid tag"), "Consumer Electronics > Other Consumer Electronics > NFC & RFID Tags"),
    (("key lock box", "key cabinet"), "Business & Industrial > Material Handling > Key Cabinets"),
    (("inkjet printer", "handheld printer"), "Business & Industrial > Printing & Graphic Arts > Printers"),
    (("fountain pump", "rain barrel pump", "pond pump"), "Home & Garden > Yard, Garden & Outdoor Living > Fountains & Ponds > Pumps"),
    (("well pump pressure switch", "pressure switch for well pump", "pressure switch with gauge for well pump", "well pump switch"), "Business & Industrial > Hydraulics, Pneumatics, Pumps & Plumbing > Pumps & Pump Accessories > Pump Parts & Accessories > Pump Pressure Switches"),
    (("golf mat",), "Sporting Goods > Golf > Training Aids > Golf Mats"),
    (("basketball",), "Sporting Goods > Basketball > Basketballs"),
    (("wakesurf rope", "tow rope"), "Sporting Goods > Water Sports > Tow Ropes"),
    (("fishing line", "monofilament"), "Sporting Goods > Fishing > Fishing Accessories > Fishing Line"),
    (("rod holder",), "Sporting Goods > Fishing > Fishing Accessories > Rod Holders"),
    (("fishing net", "foldable fishing net"), "Sporting Goods > Fishing > Fishing Accessories > Fishing Nets"),
    (("terrarium", "snail climbing wall", "misting nozzle"), "Pet Supplies > Reptile Supplies > Terrarium Accessories"),
    (("nail drill",), "Health & Beauty > Nail Care, Manicure & Pedicure > Nail Drills"),
    (("trimmer",), "Health & Beauty > Personal Care > Hair Removal > Trimmers"),
    (("tank top", "camisole", "thong", "slippers"), "Clothing, Shoes & Accessories > Women's Clothing > Tops"),
    (("band ring", "rings"), "Jewelry & Watches > Fashion Jewelry > Rings"),
    (("towel rack",), "Home & Garden > Bathroom Fixtures > Towel Racks"),
    (("transfer switch",), "Automotive > Parts & Accessories > Car & Truck Parts > Electrical > Transfer Switches"),
    (("trailer chain",), "Automotive > Parts & Accessories > Towing > Trailer Accessories"),
    (("fuel meter",), "Automotive > Parts & Accessories > Tools & Supplies"),
    (("kayak rack",), "Automotive > Parts & Accessories > Towing > Trailer Accessories"),
    (("garden shade", "shade cloth"), "Home & Garden > Window Treatments > Shades"),
    (("water service panel", "sink drain", "drain kit", "heated drinking water hose"), "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Water Systems"),
    (("curtain switch", "roller shutter switch", "smart curtain"), "Consumer Electronics > Home Automation > Smart Switches"),
    (("shark fin antenna", "antenna"), "Automotive > Parts & Accessories > Car & Truck Parts > Antennas"),
    (("headset microphone", "microphone flex", "audio converter", "rca av switch"), "Consumer Electronics > Home Audio > Audio Components"),
    (("keycaps", "replacement keycaps"), "Computers/Tablets & Networking > Keyboards, Mice & Pointers > Keyboards & Keypads"),
    (("keyboard case", "ipad keyboard", "tablet keyboard case"), "Computers/Tablets & Networking > Tablet & eBook Reader Accs > Cases, Covers, Keyboard Folios"),
    (("roof vent",), "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Roof Vents"),
    (("wifi extender",), "Consumer Electronics > Networking > Network Extenders"),
    (("solar wifi extender", "wifi extender outdoor", "wifi repeater"), "Computers/Tablets & Networking > Home Networking & Connectivity > Boosters, Extenders & Antennas"),
    (("rv dinette cushion", "rv cushion cover", "camper cushion cover"), "Automotive > RV, Trailer & Camper Parts & Accessories > Other RV, Trailer & Camper Parts & Accessories"),
    (("back glass replacement", "iphone back glass", "phone back glass"), "Cell Phones & Accessories > Cell Phone & Smartphone Parts"),
    (("in-ground well light", "landscape well light", "outdoor well light"), "Home & Garden > Yard, Garden & Outdoor Living > Outdoor Lighting > Landscape & Walkway Lights"),
    (("smart wall touch switch", "smart wall switch", "sonoff switch"), "Home & Garden > Home Improvement > Electrical Supplies > Electrical Outlets, Switches & Accessories > Smart Outlets & Smart Wall Switches > Smart Wall Switches & In-Line Modules"),
    (("rv door lock", "rv door latch", "camper door lock"), "Automotive > RV, Trailer & Camper Parts & Accessories > Doors & Door Hardware > Door Hardware"),
    (("makeup brush cleaner", "make up brush cleaner"), "Health & Beauty > Makeup > Makeup Tools & Accessories > Brush Cleaners"),
    (("post hole digger", "post hole auger"), "Home & Garden > Yard, Garden & Outdoor Living > Garden Hand Tools & Equipment > Post Hole Diggers & Augers"),
    (("ergonomic keyboard", "wireless keyboard", "usb keyboard"), "Computers/Tablets & Networking > Keyboards, Mice & Pointers > Keyboards & Keypads"),
    (("bitcoin hardware wallet", "crypto hardware wallet", "hardware wallet"), "Consumer Electronics > Other Consumer Electronics"),
    (("digital camera", "6k camera", "84mp camera"), "Cameras & Photo > Digital Cameras"),
    (("porch goose", "statue", "placemats"), "Home & Garden > Home Décor > Decorative Statues"),
    (("lumbar support pillow",), "Home & Garden > Furniture > Pillows"),
    (("bilge pump",), "Automotive > Parts & Accessories > Car & Truck Parts > Bilge Pumps"),
    (("crabjaw trap", "crab trap"), "Sporting Goods > Fishing > Fishing Accessories > Crab Traps"),
    (("skeg protector", "boat hardware"), "Automotive > Parts & Accessories > Car & Truck Parts > Marine Hardware"),
    (("wakesurf rope",), "Sporting Goods > Water Sports > Waterskiing & Wakeboarding"),
    (("monitor", "monitor mount"), "Consumer Electronics > Computers/Tablets & Networking > Monitors"),
    (("boat trash can", "car trash can"), "Home & Garden > Kitchen, Dining & Bar > Barware"),
    (("water leak detector",), "Home & Garden > Household Supplies & Cleaning > Water Leak Detectors"),
    (("bms", "battery management"), "Consumer Electronics > Multipurpose Batteries & Power > Battery Management Systems"),
    (("costume", "bodysuit"), "Clothing, Shoes & Accessories > Women's Clothing > Costumes"),
    (("couch cover",), "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Furniture Covers"),
    (("r12", "r134a", "can tap"), "Automotive > Parts & Accessories > Air Conditioning & Heating"),
    (("black tank", "tank treatment", "sewer treatment"), "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Sewer & Drain"),
    (("doorbell chime",), "Consumer Electronics > Safety & Security > Home Alarms & Security"),
    (("barcode scanner", "finger scanner", "scanner replacement"), "Business & Industrial > Material Handling > Barcode Scanners"),
    (("terminal block", "pcb mount", "connector"), "Consumer Electronics > Electrical Equipment & Supplies > Connectors"),
    (("sprayer wand", "ryobi"), "Home & Garden > Outdoor Power Equipment > Pressure Washer Parts"),
    (("lora", "llcc68", "development kit"), "Consumer Electronics > Radio Communication > Radio Modules"),
    (("water table",), "Toys & Hobbies > Outdoor Toys & Activities > Water Tables"),
    (("rv lights", "rv interior", "rv wall sconce"), "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Lighting"),
    (("door handle grab", "grab bar"), "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Hardware"),
    (("desoldering", "solder sucker"), "Consumer Electronics > Electrical Equipment & Supplies > Soldering Equipment"),
    (("car heater", "defroster"), "Automotive > Parts & Accessories > Car & Truck Parts > Air Conditioning & Heating"),
    (("cigarette tubes",), "Tobacco > Cigarette Making Supplies"),
    (("shark deterrent",), "Sporting Goods > Water Sports > Other Water Sports"),
    (("scope ring",), "Sporting Goods > Hunting > Scopes, Sights & Optics > Scope Rings"),
    (("sink pump", "fuel transfer pump"), "Home & Garden > Home Improvement > Plumbing & Fixtures > Water Pumps"),
    (("grill brush",), "Home & Garden > Household Supplies & Cleaning > Grill & BBQ Tools"),
    (("thermostat",), "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Thermostats"),
    (("beacon", "ibeacon"), "Consumer Electronics > GPS & Accessories > Asset Trackers"),
    (("lockout tool", "button master"), "Automotive > Parts & Accessories > Car & Truck Parts > Exterior Accessories"),
    (("club car", "golf cart light"), "Automotive > Parts & Accessories > Golf Cart Parts > Lighting"),
    (("grab bars", "shower handle"), "Home & Garden > Bathroom Fixtures > Shower Seats"),
    (("couch cover", "couch covers", "sofa bed cover", "sofa bed covers"), "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Furniture Covers"),
    (("interior car lights", "t10", "marker lights"), "Automotive > Parts & Accessories > Car & Truck Parts > Lighting & Lamps"),
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
    (("posture corrector", "back brace", "shoulder brace"), "Health & Beauty > Medical & Mobility > Orthopedics & Supports > Orthotics, Braces & Sleeves"),
    (("golf simulator impact screen", "golf impact screen"), "Sporting Goods > Golf > Training Aids"),
    (("selfie stick", "selfie pole"), "Cell Phones & Accessories > Cell Phone Accessories > Selfie Sticks"),
    (("windshield curtain", "rv privacy curtain"), "Automotive > RV, Trailer & Camper Parts & Accessories > Interior Accessories"),
    (("hot water recirculating pump", "water recirculation pump"), "Home & Garden > Home Improvement > Plumbing & Fixtures > Pumps & Water Filtration Systems > Pumps"),
    (("breakaway charger cable", "rocksmith cable"), "Video Games & Consoles > Video Game Accessories > Cables & Adapters"),
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
    (("light meter", "lux meter", "illuminance meter", "photometer"), "Cameras & Photo > Lighting & Studio > Light Meters"),
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
    "Computers/Tablets & Networking > Home Networking & Connectivity > Boosters, Extenders & Antennas": "61816",
    "Automotive > RV, Trailer & Camper Parts & Accessories > Other RV, Trailer & Camper Parts & Accessories": "25621",
    "Cell Phones & Accessories > Cell Phone & Smartphone Parts": "43304",
    "Home & Garden > Yard, Garden & Outdoor Living > Outdoor Lighting > Landscape & Walkway Lights": "94940",
    "Home & Garden > Home Improvement > Electrical Supplies > Electrical Outlets, Switches & Accessories > Smart Outlets & Smart Wall Switches > Smart Wall Switches & In-Line Modules": "185059",
    "Automotive > RV, Trailer & Camper Parts & Accessories > Doors & Door Hardware > Door Hardware": "261116",
    "Health & Beauty > Makeup > Makeup Tools & Accessories > Brush Cleaners": "159805",
    "Home & Garden > Yard, Garden & Outdoor Living > Garden Hand Tools & Equipment > Post Hole Diggers & Augers": "178981",
    "Computers/Tablets & Networking > Keyboards, Mice & Pointers > Keyboards & Keypads": "33963",
    "Consumer Electronics > Other Consumer Electronics": "175837",
    "Cameras & Photo > Digital Cameras": "31388",
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
    # 48618 is the generic Computers/Tablets node; the local taxonomy cache
    # has the charger leaf at 260205.
    "Consumer Electronics > Multipurpose Batteries & Power > Battery Chargers": "260205",
    "Consumer Electronics > Multipurpose Batteries & Power > Portable Chargers & Power Banks": "31510",
    "Pet Supplies > Dog Supplies > Other Dog Supplies": "1283",
    "Health & Beauty > Medical & Mobility > Orthopedics & Supports > Orthotics, Braces & Sleeves": "19264",
    "Home & Garden > Home Improvement > Plumbing & Fixtures > Pumps & Water Filtration Systems > Pumps": "42132",
    "Business & Industrial > Hydraulics, Pneumatics, Pumps & Plumbing > Pumps & Pump Accessories > Pump Parts & Accessories > Pump Pressure Switches": "184106",
    "Video Games & Consoles > Video Game Accessories > Cables & Adapters": "171814",
    "Cell Phones & Accessories > Cell Phone Accessories > Other Cell Phone Accessories": "42425",
    "Cameras & Photo > Lighting & Studio > Light Meters": "88668",
    "Cell Phones & Accessories > Cell Phone Accessories > Selfie Sticks": "30093",
    # Semantic family mappings verified against the local category/aspect
    # evidence index and public eBay category pages while the taxonomy API is
    # throttled.  These are intentionally family-level leaves/categories;
    # the resolver still ranks more specific rules first.
    "Automotive > Parts & Accessories > Charging & Starting Systems > Alternators & Generators": "177697",
    "Welders, Cutters & Torches > MIG Welders": "124822",
    "Automotive > RV, Trailer & Camper Parts & Accessories > Water & Sanitation Systems > Pumps": "261425",
    "Automotive > RV, Trailer & Camper Parts & Accessories > Interior Accessories > Curtains": "261183",
    "Boating > Parts & Accessories > Covers": "26447",
    "Sporting Goods > Fishing > Reels": "261030",
    "Sporting Goods > Fishing > Reels > Baitcasting Reels": "178885",
    "Business & Industrial > Electrical Equipment & Supplies > Drives & Motor Controls > DC Machinery Speed Controls": "71393",
    "Business & Industrial > Fuel & Energy > Flow Controllers & Meters > Flow Meters": "67070",
    "eBay Motors > Golf Cart Parts & Accessories > Light Kit": "75208",
    "eBay Motors > Boat Parts > Plumbing & Ventilation > Bilge Pumps": "26449",
    "eBay Motors > RV, Trailer & Camper Parts & Accessories > Water & Sanitation Systems > Hoses & Pipes": "261427",
    "Sporting Goods > Fishing > Fishing Equipment > Rod Rests & Holders": "72665",
    "Home & Garden > Home Improvement > Electrical Supplies > Transfer Switches": "56995",
    "Home & Garden > Home Improvement > Power Tools > Heat Guns": "66797",
    "Travel > Luggage > Luggage Sets": "16080",
    "Home & Garden > Kitchen, Dining & Bar > Coffee, Tea & Espresso Makers > Filter Coffee Machines": "184665",
    "Cell Phones & Accessories > Cell Phone & Smartphone Parts > Screens & Digitizers": "43304",
    "Home & Garden > Home Improvement > Heating, Cooling & Air > Exhaust Fans & Ventilators": "122909",
    "Sporting Goods > Winter Sports > Skiing, Snowboarding & Snowshoeing > Ski & Snowboard Bags": "21229",
    "Home & Garden > Kitchen, Dining & Bar > Small Kitchen Appliances > Coffee Machines": "38250",
    "Cell Phones & Accessories > Cell Phone Parts > Screens & Digitizers": "176982",
    "Home & Garden > Pools & Spas > Pool Pumps": "184084",
    "Home & Garden > Household Appliances > Humidifiers": "36956",
    "Consumer Electronics > Home Audio > Portable Audio Players": "182096",
    "Consumer Electronics > Cameras & Photo > Digital Cameras": "162480",
    "Automotive > Parts & Accessories > Car & Truck Parts > Grilles": "177793",
    "Automotive > Parts & Accessories > Car & Truck Parts > Lighting & Lamps": "185046",
    "Automotive > Parts & Accessories > Car & Truck Parts > Locks & Hardware": "11808",
    "Automotive > Parts & Accessories > Car & Truck Parts > Horns": "11808",
    "Home & Garden > Bathroom Fixtures > Bathroom Faucets": "1284",
    "Home & Garden > Bathroom Fixtures > Shower Seats": "1284",
    "Home & Garden > Bathroom Fixtures > Exhaust Fans": "1284",
    "Home & Garden > Furniture > Bedroom Furniture > Mattress Toppers": "36956",
    "Travel > Luggage & Travel Accessories > Luggage Sets": "261030",
    "Travel > Luggage & Travel Accessories > Luggage Covers & Bags": "261030",
    "Consumer Electronics > Electrical Equipment & Supplies > Heat Guns": "11704",
    "Consumer Electronics > Electrical Equipment & Supplies > Motor Speed Controllers": "71393",
    "Consumer Electronics > Electrical Equipment & Supplies > LED Controllers": "234",
    "Consumer Electronics > GPS & Accessories > GPS Trackers": "73362",
    "Consumer Electronics > Other Consumer Electronics > NFC & RFID Tags": "184488",
    "Business & Industrial > Material Handling > Key Cabinets": "48618",
    "Business & Industrial > Printing & Graphic Arts > Printers": "2208",
    "Home & Garden > Yard, Garden & Outdoor Living > Fountains & Ponds > Pumps": "261022",
    "Home & Garden > Yard, Garden & Outdoor Living > Watering Equipment > Pumps": "261022",
    "Sporting Goods > Golf > Training Aids > Golf Mats": "20580",
    "Sporting Goods > Basketball > Basketballs": "261022",
    "Sporting Goods > Water Sports > Tow Ropes": "261030",
    "Sporting Goods > Fishing > Fishing Accessories > Fishing Line": "261030",
    "Sporting Goods > Fishing > Fishing Accessories > Rod Holders": "261030",
    "Sporting Goods > Fishing > Fishing Accessories > Fishing Nets": "261030",
    "Pet Supplies > Reptile Supplies > Terrarium Accessories": "1284",
    "Health & Beauty > Personal Care > Hair Removal > Trimmers": "1277",
    "Health & Beauty > Nail Care, Manicure & Pedicure > Nail Drills": "1277",
    "Clothing, Shoes & Accessories > Women's Clothing > Tops": "57988",
    "Clothing, Shoes & Accessories > Women's Clothing > Lingerie": "57988",
    "Clothing, Shoes & Accessories > Men's Clothing > Slippers": "57988",
    "Jewelry & Watches > Fashion Jewelry > Rings": "1823",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Lighting": "185046",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Plumbing": "261425",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Hardware": "181068",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Covers": "181068",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Accessories": "181068",
    "Home & Garden > Bathroom Fixtures > Towel Racks": "20490",
    "Automotive > Parts & Accessories > Car & Truck Parts > Electrical > Transfer Switches": "56995",
    "Automotive > Parts & Accessories > Towing > Trailer Accessories": "11808",
    "Automotive > Parts & Accessories > Air Conditioning & Heating": "11808",
    "Automotive > Parts & Accessories > Tools & Supplies": "11808",
    "Home & Garden > Window Treatments > Shades": "20585",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Water Systems": "261425",
    "Consumer Electronics > Home Automation > Smart Switches": "125063",
    "Automotive > Parts & Accessories > Car & Truck Parts > Antennas": "11808",
    "Consumer Electronics > Home Audio > Audio Components": "182096",
    "Consumer Electronics > Computers & Accessories > Tablet Accessories > Keyboard Cases": "176973",
    "Computers/Tablets & Networking > Tablet & eBook Reader Accs > Cases, Covers, Keyboard Folios": "176973",
    "Consumer Electronics > Video Games & Consoles > Controllers & Accessories": "171814",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Roof Vents": "181068",
    "Consumer Electronics > Electrical Equipment & Supplies > Audio/Video Converters": "182096",
    "Consumer Electronics > Networking > Wireless Networking": "48618",
    "Consumer Electronics > Networking > Network Extenders": "48618",
    "Home & Garden > Home Décor > Decorative Statues": "36956",
    "Home & Garden > Home Décor > Placemats": "36956",
    "Home & Garden > Furniture > Pillows": "36956",
    "Automotive > Parts & Accessories > Car & Truck Parts > Horns": "11808",
    "Automotive > Parts & Accessories > Car & Truck Parts > Bilge Pumps": "184085",
    "Automotive > Parts & Accessories > Car & Truck Parts > Marine Hardware": "11808",
    "Sporting Goods > Fishing > Fishing Accessories > Crab Traps": "261030",
    "Sporting Goods > Hunting > Hunting Accessories": "261030",
    "Sporting Goods > Water Sports > Waterskiing & Wakeboarding": "261030",
    "Home & Garden > Pools & Spas > Pool Accessories": "184084",
    "Consumer Electronics > Computers/Tablets & Networking > Monitors": "182096",
    "Home & Garden > Kitchen, Dining & Bar > Barware": "36956",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Covers": "181068",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Sewer & Drain": "261425",
    "Consumer Electronics > Computers/Tablets & Networking > Computer Components": "48618",
    "Consumer Electronics > Computers/Tablets & Networking > Tablet Accessories": "42425",
    "Consumer Electronics > Computers/Tablets & Networking > Computer Cables & Connectors": "48618",
    "Consumer Electronics > GPS & Accessories > Asset Trackers": "73362",
    "Consumer Electronics > Safety & Security > Home Alarms & Security": "1284",
    "Automotive > Parts & Accessories > Car & Truck Parts > Exterior Accessories": "11808",
    "Health & Beauty > Medical & Mobility > Daily Living Aids": "1277",
    "Home & Garden > Home Improvement > Plumbing & Fixtures > Drainage": "42132",
    "Home & Garden > Home Improvement > Plumbing & Fixtures > Water Pumps": "42132",
    "Home & Garden > Home Improvement > Plumbing & Fixtures > Water Filters": "42132",
    "Consumer Electronics > Multipurpose Batteries & Power > Battery Management Systems": "31510",
    "Consumer Electronics > Other Consumer Electronics > RFID Tags": "184488",
    "Clothing, Shoes & Accessories > Women's Clothing > Costumes": "57988",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Furniture Covers": "181068",
    "Consumer Electronics > Other Consumer Electronics > Smart Sensors": "1284",
    "Home & Garden > Household Supplies & Cleaning > Grill & BBQ Tools": "36956",
    "Home & Garden > Household Supplies & Cleaning > Water Leak Detectors": "1284",
    "Sporting Goods > Fishing > Fishing Accessories > Fishing Traps": "261030",
    "Automotive > Parts & Accessories > Air Conditioning & Heating": "11808",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Sewer & Drain": "261425",
    "Consumer Electronics > Safety & Security > Home Alarms & Security": "1284",
    "Business & Industrial > Material Handling > Barcode Scanners": "48618",
    "Consumer Electronics > Electrical Equipment & Supplies > Connectors": "48618",
    "Home & Garden > Outdoor Power Equipment > Pressure Washer Parts": "11704",
    "Consumer Electronics > Radio Communication > Radio Modules": "48618",
    "Toys & Hobbies > Outdoor Toys & Activities > Water Tables": "234",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Lighting": "185046",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Hardware": "181068",
    "Automotive > Parts & Accessories > Car & Truck Parts > Air Conditioning & Heating": "11808",
    "Tobacco > Cigarette Making Supplies": "36956",
    "Sporting Goods > Water Sports > Other Water Sports": "261030",
    "Sporting Goods > Hunting > Scopes, Sights & Optics > Scope Rings": "261030",
    "Automotive > Parts & Accessories > RV, Trailer & Camper Parts > RV Thermostats": "181068",
    "Automotive > Parts & Accessories > Golf Cart Parts > Lighting": "11808",
}

# A few historical Google/Slate syntheses stored a semantically equivalent
# short label instead of the canonical taxonomy path.  These aliases are
# deliberately limited to paths whose destination ID is already verified;
# they do not invent IDs or turn an unknown category into a publishable one.
_VERIFIED_CATEGORY_ALIASES: dict[str, str] = {
    "toys": "Toys & Hobbies",
    "games & puzzles": "Toys & Hobbies",
    "toy & game": "Toys & Hobbies",
    "games": "Toys & Hobbies",
}

# Numeric IDs that were previously reused as a generic fallback for unrelated
# families are not acceptable evidence.  They remain in the historical map for
# diagnostics, but the resolver must return no ID for a new semantic path until
# that path has an exact public/local category verification.
_AUDITED_EXTERNAL_CATEGORY_IDS: dict[str, str] = {
    "Business & Industrial > Electrical Equipment & Supplies > Lighting Components > LED-RGB Controllers": "20705",
    "Welders, Cutters & Torches > MIG Welders": "124822",
    "Automotive > RV, Trailer & Camper Parts & Accessories > Interior Accessories > Curtains": "261183",
    "Automotive > RV, Trailer & Camper Parts & Accessories > Interior > Interior Lighting": "261086",
    "Automotive > Parts & Accessories > Charging & Starting Systems > Alternators & Generators": "177697",
    "Business & Industrial > Facility Maintenance & Safety > Access Control > Key Cabinets": "31586",
    "eBay Motors > Car & Truck Parts & Accessories > Wheels, Tires & Parts > Wheel Locks": "33749",
    "Sporting Goods > Team Sports > Basketball > Basketballs": "21208",
    "Home & Garden > Home Improvement > Heating, Cooling & Air > Humidifiers": "71240",
    "Home & Garden > Yard, Garden & Outdoor Living > Pools & Spas > Pool Pumps": "181485",
    "Home & Garden > Kitchen, Dining & Bar > Table Linens > Placemats": "20660",
    "Home & Garden > Kitchen, Dining & Bar > Kitchen Tools & Gadgets > Cooking Thermometers": "43421",
    "Health & Beauty > Medical & Mobility > Daily Living Aids > Medical Alert Systems": "159878",
    "Consumer Electronics > Multipurpose Batteries & Power > Battery Management Systems > Protection Boards": "262095",
    "Sporting Goods > Hunting > Scopes, Optics & Lasers > Scope Rings & Mounts": "52510",
    "eBay Motors > Boat Parts > Electrical & Lighting > Marine LED Lighting": "50437",
    "eBay Motors > Boat Parts > Engines & Components > Alternators & Charging Systems": "50440",
    "eBay Motors > RV, Trailer & Camper Parts & Accessories > Interior > Interior Lighting": "261086",
    "eBay Motors > Boat Parts > Covers > Boat Covers": "26447",
    "Sporting Goods > Fishing > Reels > Baitcasting Reels": "178885",
    # Operator-supplied paths use a few legacy breadcrumb spellings.  Keep
    # aliases only where the numeric category has been independently checked.
    "eBay Motors > RV, Trailer & Camper Parts & Accessories > Interior > Curtains": "261183",
    "eBay Motors > RV, Trailer & Camper Parts & Accessories > Hatches, Windows, Vents & Accessories > Window Blinds, Curtains & Covers": "261183",
    "eBay Motors > Car & Truck Parts & Accessories > Starters, Alternators, ECUs & Wiring > Alternators & Generators": "177697",
    "eBay Motors > Boat Parts > Electrical & Lighting > Marine LED Lighting": "50437",
    "eBay Motors > Boat Parts > Engines & Components > Alternators & Charging Systems": "50440",
    "Sporting Goods > Team Sports > Basketballs": "21208",
    "Home & Garden > Kitchen, Dining & Bar > Table Linens > Placemats": "20660",
    "Home & Garden > Kitchen, Dining & Bar > Kitchen Tools & Gadgets > Cooking Thermometers": "43421",
    "Home & Garden > Home Improvement > Heating, Cooling & Air > Humidifiers": "71240",
    "Health & Beauty > Medical & Mobility > Daily Living Aids > Medical Alert Systems": "159878",
    "Sporting Goods > Hunting > Scopes, Optics & Lasers > Scope Rings & Mounts": "52510",
    "Consumer Electronics > Multipurpose Batteries & Power > Battery Management Systems > Protection Boards": "262095",
}

_UNTRUSTED_REUSED_IDS = {
    "11808", "1284", "36956", "48618", "182096", "261030", "261022",
    "181068", "57988", "1277", "261425", "42132", "234", "42425",
    "11704", "171814", "184084", "184488", "185046", "20490", "20580",
    "20585", "31510", "73362", "1823",
}


def verified_category_id(value: str | None) -> str | None:
    """Return an ID only for a canonical path already validated in taxonomy."""
    normalized = " ".join(str(value or "").split())
    canonical = _VERIFIED_CATEGORY_ALIASES.get(normalized.casefold(), normalized)
    audited = _AUDITED_EXTERNAL_CATEGORY_IDS.get(canonical)
    if audited:
        return audited
    direct = _VERIFIED_CATEGORY_IDS.get(canonical)
    # Preserve the legacy, explicitly named aliases for non-Vine callers. Vine
    # readiness separately rejects generic/top-level IDs; this compatibility
    # branch does not make those IDs valid evidence for a Vine leaf.
    if normalized.casefold() in _VERIFIED_CATEGORY_ALIASES and direct:
        return direct
    if direct in _UNTRUSTED_REUSED_IDS:
        return None
    if direct:
        return direct
    # Historical group synthesis sometimes lower-cased a canonical path. Use
    # case-insensitive matching only against the verified map; this still
    # cannot manufacture a taxonomy ID.
    folded = canonical.casefold()
    for known_path, category_id in _VERIFIED_CATEGORY_IDS.items():
        if known_path.casefold() == folded:
            return None if category_id in _UNTRUSTED_REUSED_IDS else category_id
    return None


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
    # A legitimate taxonomy leaf may begin with “Other” (for example,
    # “Other Protective Gear”).  Only treat the generic unresolved placeholder
    # and source-page instruction as noise; do not discard valid marketplace
    # categories merely because their display name starts with Other.
    generic_other = text in {"other", "other > needs category review", "other needs category review"}
    return any(marker in text for marker in markers) or generic_other or text.startswith("read the full")
