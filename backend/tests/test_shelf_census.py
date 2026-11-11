from fastapi import HTTPException

from app.api.inventory import census_confirm, census_search, census_session, save_census_location, census_item_label, reprint_census_item, undo_census_item, move_census_item, update_census_label_template, census_label_template_test, create_print_agent_pairing_code, pair_print_agent, _auth_print_agent, list_print_agents, update_census_print_settings, claim_print_agent_job, update_agent_print_job_state, revoke_print_agent
from app.models.models import InventoryCensusRecord, InventoryEvent, InventoryPrintJob, InventoryPrintAgent, InventoryStock, Listing, User
from app.services.package_labels import code128_values, code128_layout, normalize_inventory_template, render_inventory_label, PAPER_PROFILES


def test_census_matches_existing_listing_and_preserves_sku_listing_data(db_session):
    user = User(email="census-match@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, title="Blue ceramic lamp", description="Vintage bedside lamp", quantity=7, source_type="amazon_vine", image_urls=["/media/lamp.jpg"], marketplace_data={"keep": True})
    db_session.add(listing); db_session.commit()

    result = census_search("bedside lamp", db_session, user)
    assert result["items"][0]["listing_id"] == listing.id
    location = save_census_location({"unit_id":"01175", "section_code":"A", "shelf_level":"01"}, db_session, user)
    saved = census_confirm({"operation_id":"census-match-op", "session_key":"session-a", "location_key":location["location_key"], "listing_id":listing.id}, db_session, user)
    db_session.refresh(listing)
    assert location["location_key"] == "01175/A/01"
    assert saved["label_payload"]["sku"] == saved["sku"]
    assert listing.quantity == 7 and listing.image_urls == ["/media/lamp.jpg"] and listing.marketplace_data == {"keep": True}
    assert db_session.query(InventoryCensusRecord).filter_by(item_uuid=saved["item_uuid"]).count() == 1


def test_census_duplicate_guard_retry_and_audited_undo(db_session):
    user = User(email="census-duplicate@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, title="Blue lamp", quantity=1)
    db_session.add(listing); db_session.commit()
    location = save_census_location({"unit_id":"01173", "section_code":"B", "shelf_level":"02"}, db_session, user)
    payload = {"operation_id":"census-duplicate-1", "session_key":"session-b", "location_key":location["location_key"], "listing_id":listing.id}
    first = census_confirm(payload, db_session, user)
    assert census_confirm(payload, db_session, user)["status"] == "replayed"
    try:
        census_confirm({**payload, "location_key":"01173/B/03"}, db_session, user)
        assert False, "An operation key cannot be reused for a different location"
    except HTTPException as error:
        assert error.status_code == 409
    try:
        census_confirm({**payload, "operation_id":"census-duplicate-2"}, db_session, user)
        assert False, "Expected duplicate guard"
    except HTTPException as error:
        assert error.status_code == 409
    assert census_session("session-b", db_session, user)["duplicate_warnings"] == 1
    undone = undo_census_item(first["item_uuid"], db_session, user)
    assert undone["status"] == "reversed"
    assert db_session.query(InventoryPrintJob).filter_by(job_type="census_item_label").one().status == "CANCELLED"
    assert db_session.query(InventoryEvent).filter_by(event_type="census_item_undo").count() == 1


def test_census_label_has_stable_lookup_and_valid_code128(db_session):
    user = User(email="census-label@example.com")
    db_session.add(user); db_session.commit()
    location = save_census_location({"unit_id":"01174", "section_code":"A", "shelf_level":"03"}, db_session, user)
    item = census_confirm({"operation_id":"census-label-op", "session_key":"session-c", "location_key":location["location_key"], "provisional_title":"Unmatched ceramic lamp"}, db_session, user)
    label = census_item_label(item["item_uuid"], "qr_code128", db_session, user)
    assert label["image_data_url"].startswith("data:image/png;base64,")
    assert item["item_uuid"] in label["payload"]["lookup_url"]
    assert label["payload"]["location_key"] == "01174/A/03"
    assert len(label["payload"]["barcode_token"]) == 13
    assert label["dpi"] == 203 and (label["pixel_width"], label["pixel_height"]) == (464, 320)
    token_scan = census_search(label["payload"]["barcode_token"], db_session, user)
    assert token_scan["scanned_item"]["item_uuid"] == item["item_uuid"]
    scanned = census_search(item["item_uuid"].replace("-", ""), db_session, user)
    assert scanned["scanned_item"]["item_uuid"] == item["item_uuid"]
    assert code128_values("A")[0] == 104 and code128_values("A")[-1] == 106
    assert db_session.query(InventoryPrintJob).filter_by(job_type="census_item_label").count() == 1
    reprint = reprint_census_item(item["item_uuid"], {"operation_id":"reprint-op-1"}, db_session, user)
    assert reprint["status"] == "queued"
    assert db_session.query(InventoryStock).filter_by(user_id=user.id).one().on_hand == 1
    assert db_session.query(InventoryPrintJob).filter_by(job_type="census_item_label").count() == 2


def test_label_template_saves_valid_positions_and_has_calibration_artifact(db_session):
    user = User(email="census-template@example.com")
    db_session.add(user); db_session.commit()
    updated = update_census_label_template({"version":2, "qr_x_mm":40, "qr_y_mm":2, "qr_size_mm":15, "print_scale_percent":98}, db_session, user)
    assert updated["template"]["qr_size_mm"] == 15
    assert updated["width_mm"] == 58 and updated["height_mm"] == 40
    assert updated["dpi"] == 203 and updated["pixel_width"] == 464
    test = census_label_template_test(user)
    assert test["image_data_url"].startswith("data:image/png;base64,")
    try:
        update_census_label_template({"version":2, "qr_x_mm":55, "qr_size_mm":10}, db_session, user)
        assert False, "Expected out of bounds QR rejection"
    except HTTPException as error:
        assert error.status_code == 422


def test_scanned_census_move_updates_current_label_location_and_keeps_event_history(db_session):
    user = User(email="census-move@example.com")
    db_session.add(user); db_session.commit()
    source = save_census_location({"unit_id":"01173", "section_code":"A", "shelf_level":"01"}, db_session, user)
    destination = save_census_location({"unit_id":"01176", "section_code":"C", "shelf_level":"04"}, db_session, user)
    item = census_confirm({"operation_id":"census-move-create", "session_key":"session-move", "location_key":source["location_key"], "provisional_title":"Small lamp"}, db_session, user)
    moved = move_census_item(item["item_uuid"], {"operation_id":"census-move-op", "destination_location_key":destination["location_key"]}, db_session, user)
    assert moved["from_location_key"] == source["location_key"]
    label = census_item_label(item["item_uuid"], "qr_code128", db_session, user)
    assert label["payload"]["location_key"] == destination["location_key"]
    event = db_session.query(InventoryEvent).filter_by(event_type="census_item_moved").one()
    assert event.payload_json["from_location_key"] == source["location_key"]


def test_203dpi_canvas_versioned_legacy_template_and_qr_independent_decode():
    import base64, cv2, numpy as np
    from PIL import Image
    import io
    assert PAPER_PROFILES["thermal_58x40mm"][:2] == (464, 320)
    legacy = normalize_inventory_template({"title_x": 20, "title_y": 60, "qr_x": 516, "qr_y": 302, "qr_size": 150, "print_scale_percent": 100})
    assert legacy["version"] == 2 and legacy["coordinate_unit"] == "mm" and legacy["template_migrated_from"] == 1
    assert legacy["title_x_mm"] == round(20 * 25.4 / 300, 3)
    token = "ABCDEFGHJKLMN"  # 13 Base32 symbols; excludes ambiguous I/O/0/1.
    payload = {"title":"Test item", "sku":"SKU-1", "item_uuid":"12345678-1234-5678-1234-567812345678", "barcode_token":token,
              "lookup_url":"https://posterpro.sparkleserver.site/inventory/census?item=12345678-1234-5678-1234-567812345678", "unit_id":"01175", "shelf_code":"A01"}
    png = render_inventory_label(payload, layout="qr")
    image = Image.open(io.BytesIO(png))
    assert image.size == (464, 320) and image.mode == "1"
    assert tuple(round(x) for x in image.info["dpi"]) == (203, 203)
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(np.asarray(image.convert("RGB")))
    assert decoded == payload["lookup_url"]
    barcode_png = render_inventory_label(payload, layout="code128")
    barcode_image = Image.open(io.BytesIO(barcode_png))
    assert barcode_image.size == (464, 320)
    values = code128_values(token)
    expected_checksum = (104 + sum((ord(ch) - 32) * index for index, ch in enumerate(token, 1))) % 103
    assert values[0] == 104 and values[-2] == expected_checksum and values[-1] == 106
    _, module_dots, total_width = code128_layout(token, 424, quiet_zone=10, minimum_module_dots=2)
    assert module_dots >= 2 and total_width <= 424
    # Independent raster decoder for this known Code 128-B fixture. Patterns
    # below are the published symbol widths for Start B, A-H, J-N, checksum 79,
    # and Stop; they are deliberately not imported from the production encoder.
    expected_patterns = ["211214", "111323", "131123", "131321", "112313", "132113", "132311", "211313", "231113",
                         "112133", "112331", "132131", "113123", "113321", "134111", "2331112"]
    y = round(33 * 203 / 25.4)
    row = [1 if barcode_image.getpixel((x, y)) == 0 else 0 for x in range(barcode_image.width)]
    first, last = row.index(1), len(row) - 1 - row[::-1].index(1)
    runs, run, state = [], 0, 1
    for pixel in row[first:last + 1]:
        if pixel == state:
            run += 1
        else:
            runs.append(run); run = 1; state = pixel
    runs.append(run)
    ratios = [str(width // 2) for width in runs]
    decoded_values, cursor = [], 0
    for pattern in expected_patterns:
        chunk = "".join(ratios[cursor:cursor + len(pattern)])
        assert chunk == pattern
        decoded_values.append(pattern); cursor += len(pattern)
    data_values = [ord(ch) - 32 for ch in token]
    checksum = (104 + sum(value * index for index, value in enumerate(data_values, 1))) % 103
    assert checksum == 79 and values[-2] == checksum
    assert "".join(chr(value + 32) for value in data_values) == token
    try:
        code128_layout(token, 100, quiet_zone=10, minimum_module_dots=2)
        assert False, "Narrow barcode must be rejected instead of compressed"
    except ValueError:
        pass
    try:
        render_inventory_label(payload, layout="qr", template={**legacy, "qr_x_mm":55, "qr_size_mm":10})
        assert False, "Out-of-bounds QR must be rejected"
    except ValueError:
        pass


def test_print_agent_pairing_tenant_scope_and_single_claim_state_machine(db_session):
    import hashlib
    user = User(email="census-agent@example.com", settings_json={"inventory_print_mode":"automatic_agent"})
    other = User(email="census-agent-other@example.com")
    db_session.add_all([user, other]); db_session.commit()
    code_result = create_print_agent_pairing_code({"display_name":"Acer WT20"}, db_session, user)
    pair = pair_print_agent({"pairing_code":code_result["pairing_code"], "printer_name":"Sparkles SKU Printer", "display_name":"Acer WT20"}, db_session)
    agent = _auth_print_agent(f"Bearer {pair['access_token']}", db_session)
    assert agent.user_id == user.id and agent.token_hash == hashlib.sha256(pair["access_token"].encode()).hexdigest()
    assert list_print_agents(db_session, other)["items"] == []
    try:
        pair_print_agent({"pairing_code":code_result["pairing_code"], "printer_name":"Sparkles SKU Printer"}, db_session)
        assert False, "A one-time pairing code cannot be replayed"
    except HTTPException as error:
        assert error.status_code == 401
    agent.enabled = True; agent.hardware_tested = True; agent.last_status = "online"; db_session.commit()
    location = save_census_location({"unit_id":"01175", "section_code":"A", "shelf_level":"01"}, db_session, user)
    item = census_confirm({"operation_id":"agent-print-op", "session_key":"agent-session", "location_key":location["location_key"], "provisional_title":"Agent label item"}, db_session, user)
    auth = f"Bearer {pair['access_token']}"
    claim = claim_print_agent_job(auth, db_session)
    assert claim["job"]["job_id"] and claim["job"]["pixel_width"] == 464
    assert claim["job"]["printer_name"] == "Sparkles SKU Printer"
    second_claim = claim_print_agent_job(auth, db_session)
    assert second_claim["job"] is None
    job_uuid, lease = claim["job"]["job_id"], claim["job"]["lease_token"]
    assert update_agent_print_job_state(job_uuid, {"lease_token":lease,"state":"SUBMITTING"}, auth, db_session)["state"] == "SUBMITTING"
    assert update_agent_print_job_state(job_uuid, {"lease_token":lease,"state":"SPOOLER_ACCEPTED","spooler_job_id":"42"}, auth, db_session)["physical_print_verified"] is False
    assert db_session.query(InventoryStock).filter_by(user_id=user.id).one().on_hand == 1
    # Auto mode can never open browser dispatch for the same saved item.
    try:
        from app.api.inventory import mark_browser_label_dispatched
        mark_browser_label_dispatched(item["print_job_id"], db_session, user)
        assert False, "Automatic and browser modes must remain exclusive"
    except HTTPException as error:
        assert error.status_code == 409
    revoke_print_agent(agent.id, db_session, user)
    try:
        _auth_print_agent(auth, db_session)
        assert False, "Revoked credentials must stop authenticating"
    except HTTPException as error:
        assert error.status_code == 401


def test_disabled_print_mode_and_replayed_confirm_never_duplicate_stock_or_jobs(db_session):
    user = User(email="census-print-disabled@example.com", settings_json={"inventory_print_mode":"disabled"})
    db_session.add(user); db_session.commit()
    location = save_census_location({"unit_id":"01176", "section_code":"B", "shelf_level":"06"}, db_session, user)
    intent = {"operation_id":"disabled-print-op", "session_key":"disabled-session", "location_key":location["location_key"], "provisional_title":"One physical unit"}
    first = census_confirm(intent, db_session, user)
    replay = census_confirm(intent, db_session, user)
    assert first["print_job_id"] is None and replay["print_job_id"] is None and replay["replayed"] is True
    assert db_session.query(InventoryStock).filter_by(user_id=user.id).one().on_hand == 1
    assert db_session.query(InventoryPrintJob).filter_by(user_id=user.id, job_type="census_item_label").count() == 0
