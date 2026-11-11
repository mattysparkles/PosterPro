from app.services.scanner_dispatcher import dispatch_scan
from app.api.inventory import _identifier_type
from uuid import uuid4

import pytest

def test_dispatcher_normalizes_hid_commands_and_preserves_barcodes():
    assert dispatch_scan("  next   tote\n") == {"kind": "command", "command": "next_tote", "display": "NEXT TOTE"}
    assert dispatch_scan("POSTERPRO:COMMAND:NEW_ITEM")["command"] == "new_item"
    assert dispatch_scan("next item")["command"] == "next_item"
    assert dispatch_scan("POSTERPRO:COMMAND:BUNDLE")["command"] == "start_bundle"
    assert dispatch_scan("ADD UNIT")["command"] == "add_unit"
    assert dispatch_scan("012345678905") == {"kind": "barcode", "barcode": "012345678905", "display": "012345678905"}


def test_identifier_family_detection_is_conservative_and_keeps_manufacturer_punctuation():
    assert _identifier_type("036000291452") == "upc_candidate"
    assert _identifier_type("0036000291452") == "ean13_candidate"
    assert _identifier_type("4006381333931") == "ean13_candidate"
    assert _identifier_type("4006381333932") == "invalid_gtin_check_digit"
    assert _identifier_type("0306406152") == "isbn10_candidate"
    assert _identifier_type("9780306406157") == "isbn13_candidate"
    assert _identifier_type("X0059099TF") == "amazon_fnsku"
    assert _identifier_type("B0DKXPX549") == "asin_candidate"
    assert _identifier_type("LPNRRJJ2497283") == "amazon_lpn"
    assert _identifier_type("MN: 9574-2SFP28") == "manufacturer_part_number_candidate"
    assert _identifier_type("sku 4006381333931") == "unknown_scanned_identifier"
    assert _identifier_type("`fX0059099TF") == "unknown_scanned_identifier"


@pytest.mark.anyio
async def test_scanner_endpoint_journals_and_idempotently_replays_hid_command(async_client):
    registered = await async_client.post("/auth/register", json={"full_name": "Scanner Owner", "email": f"scanner-{uuid4()}@example.com", "password": "supersecret123"})
    assert registered.status_code == 201
    key = str(uuid4())
    first = await async_client.post("/inventory/scanner/dispatch", json={"value": "next tote", "session_id": "packing-1", "operation_id": key, "workstation_state": {"tote": "AO9", "quantity": 2}})
    assert first.status_code == 200
    assert first.json()["command"] == "next_tote"
    replay = await async_client.post("/inventory/scanner/dispatch", json={"value": "next tote", "session_id": "packing-1", "operation_id": key})
    assert replay.status_code == 200
    assert replay.json()["status"] == "replayed"
    resumed = await async_client.get("/inventory/scanner/sessions/packing-1")
    assert resumed.status_code == 200
    assert resumed.json()["workstation_state"] == {"tote": "", "quantity": 2, "current": None}


@pytest.mark.anyio
async def test_next_item_command_clears_current_checkpoint(async_client):
    registered = await async_client.post("/auth/register", json={"full_name": "Scanner Next", "email": f"scanner-next-{uuid4()}@example.com", "password": "supersecret123"})
    assert registered.status_code == 201
    response = await async_client.post("/inventory/scanner/dispatch", json={
        "value": "NEXT ITEM", "session_id": "packing-next", "operation_id": str(uuid4()),
        "workstation_state": {"tote": "AO9", "quantity": 1, "current": {"barcode": "keep-out"}},
    })
    assert response.status_code == 200
    assert response.json()["command"] == "next_item"
    assert response.json()["workstation_state"]["current"] is None


@pytest.mark.anyio
async def test_new_package_scanner_command_is_durable_and_idempotent(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import InventoryPackage

    registered = await async_client.post("/auth/register", json={"full_name": "Scanner Package", "email": f"scanner-package-{uuid4()}@example.com", "password": "supersecret123"})
    assert registered.status_code == 201
    operation_id = str(uuid4())
    payload = {"value": "NEW PACKAGE", "session_id": "packing-package", "operation_id": operation_id, "workstation_state": {"tote": "", "quantity": 1}}
    first = await async_client.post("/inventory/scanner/dispatch", json=payload)
    assert first.status_code == 200
    code = first.json()["package"]["package_code"]
    assert first.json()["workstation_state"]["current_package_code"] == code
    replay = await async_client.post("/inventory/scanner/dispatch", json=payload)
    assert replay.status_code == 200 and replay.json()["status"] == "replayed"
    assert replay.json()["package"]["package_code"] == code
    db = SessionLocal()
    try:
        assert db.execute(select(InventoryPackage).where(InventoryPackage.package_code == code)).scalars().all().__len__() == 1
    finally:
        db.close()


@pytest.mark.anyio
async def test_print_label_command_queues_one_package_label_and_blocks_without_package(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import InventoryPrintJob

    registered = await async_client.post("/auth/register", json={"full_name": "Scanner Label", "email": f"scanner-label-{uuid4()}@example.com", "password": "supersecret123"})
    assert registered.status_code == 201
    package_op = str(uuid4())
    created = await async_client.post("/inventory/scanner/dispatch", json={"value": "NEW PACKAGE", "session_id": "packing-label", "operation_id": package_op, "workstation_state": {"tote": "", "quantity": 1}})
    package_code = created.json()["package"]["package_code"]
    print_op = str(uuid4())
    payload = {"value": "PRINT LABEL", "session_id": "packing-label", "operation_id": print_op, "workstation_state": {"tote": "", "quantity": 1, "current_package_code": package_code}}
    first = await async_client.post("/inventory/scanner/dispatch", json=payload)
    assert first.status_code == 200
    assert first.json()["label"]["printable_url"].startswith(f"/inventory/packages/{package_code}/label/printable")
    replay = await async_client.post("/inventory/scanner/dispatch", json=payload)
    assert replay.status_code == 200 and replay.json()["status"] == "replayed"
    reprint_op = str(uuid4())
    reprint_payload = {**payload, "value": "REPRINT LABEL", "operation_id": reprint_op}
    reprinted = await async_client.post("/inventory/scanner/dispatch", json=reprint_payload)
    assert reprinted.status_code == 200 and reprinted.json()["label"]["job_id"] != first.json()["label"]["job_id"]
    reprint_replay = await async_client.post("/inventory/scanner/dispatch", json=reprint_payload)
    assert reprint_replay.status_code == 200 and reprint_replay.json()["status"] == "replayed"
    blocked = await async_client.post("/inventory/scanner/dispatch", json={"value": "PRINT LABEL", "session_id": "packing-label", "operation_id": str(uuid4()), "workstation_state": {"tote": "", "quantity": 1}})
    assert blocked.json()["action_status"] == "blocked"
    db = SessionLocal()
    try:
        jobs = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.package_id.is_not(None))).scalars().all()
        assert sum(1 for job in jobs if job.payload_json.get("package_code") == package_code) == 2
    finally:
        db.close()


@pytest.mark.anyio
async def test_new_tote_arms_next_scan_without_creating_product_boundary(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import InventoryBin, InventoryEvent

    registered = await async_client.post("/auth/register", json={"full_name": "Scanner Tote", "email": f"scanner-tote-{uuid4()}@example.com", "password": "supersecret123"})
    assert registered.status_code == 201
    command = await async_client.post("/inventory/scanner/dispatch", json={
        "value": "NEW TOTE", "session_id": "packing-tote", "operation_id": str(uuid4()),
        "workstation_state": {"tote": "", "quantity": 1},
    })
    assert command.status_code == 200
    assert command.json()["workstation_state"]["awaiting_tote_code"] is True
    tote_op = str(uuid4())
    payload = {
        "value": "AO-TOTE-42", "session_id": "packing-tote", "operation_id": tote_op,
        "workstation_state": command.json()["workstation_state"],
    }
    first = await async_client.post("/inventory/scanner/dispatch", json=payload)
    assert first.status_code == 200
    assert first.json()["kind"] == "tote"
    assert first.json()["tote"]["created"] is True
    assert first.json()["workstation_state"]["tote"] == "AO-TOTE-42"
    assert first.json()["workstation_state"]["awaiting_tote_code"] is False
    replay = await async_client.post("/inventory/scanner/dispatch", json=payload)
    assert replay.status_code == 200 and replay.json()["status"] == "replayed"
    db = SessionLocal()
    try:
        assert len(db.execute(select(InventoryBin).where(InventoryBin.code == "AO-TOTE-42")).scalars().all()) == 1
        assert len(db.execute(select(InventoryEvent).where(InventoryEvent.operation_id == tote_op)).scalars().all()) == 1
    finally:
        db.close()


@pytest.mark.anyio
async def test_find_item_command_returns_owned_stock_location_without_creating_capture(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import InventoryBin, InventoryEvent, InventoryProduct, InventoryStock, User

    email = f"scanner-find-{uuid4()}@example.com"
    registered = await async_client.post("/auth/register", json={"full_name": "Scanner Find", "email": email, "password": "supersecret123"})
    assert registered.status_code == 201
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        tote = InventoryBin(user_id=user.id, code="AO-FIND-1", label="AO-FIND-1", nfc_token=f"nfc-{uuid4()}", container_uuid=str(uuid4()))
        product = InventoryProduct(user_id=user.id, product_key=str(uuid4()), title="Dorman 600-103 4WD Actuator", identifiers_json={"barcode": "8901234567890"}, enrichment_status="identified")
        db.add_all([tote, product]); db.flush()
        db.add(InventoryStock(user_id=user.id, product_id=product.id, location_key=tote.container_uuid, on_hand=2, reserved=1))
        db.commit()
    finally:
        db.close()

    command = await async_client.post("/inventory/scanner/dispatch", json={
        "value": "FIND ITEM", "session_id": "packing-find", "operation_id": str(uuid4()),
        "workstation_state": {"tote": "", "quantity": 1},
    })
    assert command.status_code == 200
    assert command.json()["workstation_state"]["awaiting_find_query"] is True
    lookup_op = str(uuid4())
    payload = {"value": "8901234567890", "session_id": "packing-find", "operation_id": lookup_op, "workstation_state": command.json()["workstation_state"]}
    found = await async_client.post("/inventory/scanner/dispatch", json=payload)
    assert found.status_code == 200
    assert found.json()["kind"] == "inventory_lookup"
    assert found.json()["lookup"]["products"][0]["title"] == "Dorman 600-103 4WD Actuator"
    assert found.json()["lookup"]["products"][0]["locations"][0]["label"] == "AO-FIND-1"
    assert found.json()["workstation_state"]["awaiting_find_query"] is False
    replay = await async_client.post("/inventory/scanner/dispatch", json=payload)
    assert replay.status_code == 200 and replay.json()["status"] == "replayed"
    db = SessionLocal()
    try:
        assert len(db.execute(select(InventoryEvent).where(InventoryEvent.operation_id == lookup_op)).scalars().all()) == 1
    finally:
        db.close()


@pytest.mark.anyio
async def test_add_unit_is_explicit_idempotent_and_uses_selected_tote(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import InventoryBin, InventoryEvent, InventoryProduct, InventoryStock, User

    email = f"scanner-add-unit-{uuid4()}@example.com"
    registered = await async_client.post("/auth/register", json={"full_name": "Scanner Add Unit", "email": email, "password": "supersecret123"})
    assert registered.status_code == 201
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        tote = InventoryBin(user_id=user.id, code="A01", label="A01", nfc_token=f"nfc-{uuid4()}", container_uuid=str(uuid4()))
        product = InventoryProduct(user_id=user.id, product_key=str(uuid4()), title="Existing Jade Plus", identifiers_json={"barcode": "198715192346"}, enrichment_status="identified")
        db.add_all([tote, product]); db.commit()
        tote_key, product_id, user_id = tote.container_uuid, product.id, user.id
    finally:
        db.close()

    armed = await async_client.post("/inventory/scanner/dispatch", json={"value": "ADD UNIT", "session_id": "add-unit", "operation_id": str(uuid4()), "workstation_state": {"tote": "A01", "quantity": 1}})
    assert armed.status_code == 200 and armed.json()["workstation_state"]["awaiting_add_unit"] is True
    operation_id = str(uuid4())
    scan = {"value": "198715192346", "session_id": "add-unit", "operation_id": operation_id, "workstation_state": armed.json()["workstation_state"]}
    first = await async_client.post("/inventory/scanner/dispatch", json=scan)
    replay = await async_client.post("/inventory/scanner/dispatch", json=scan)
    assert first.status_code == 200 and first.json()["kind"] == "inventory_unit_added"
    assert replay.status_code == 200 and replay.json()["status"] == "replayed"
    db = SessionLocal()
    try:
        stock = db.execute(select(InventoryStock).where(InventoryStock.user_id == user_id, InventoryStock.product_id == product_id, InventoryStock.location_key == tote_key)).scalar_one()
        assert stock.on_hand == 1
        assert db.execute(select(InventoryProduct).where(InventoryProduct.user_id == user_id)).scalars().all().__len__() == 1
        assert db.execute(select(InventoryEvent).where(InventoryEvent.user_id == user_id, InventoryEvent.operation_id == operation_id)).scalars().all().__len__() == 1
    finally:
        db.close()


@pytest.mark.anyio
async def test_add_unit_without_tote_does_not_mutate_stock(async_client):
    registered = await async_client.post("/auth/register", json={"full_name": "No Tote Add", "email": f"scanner-add-no-tote-{uuid4()}@example.com", "password": "supersecret123"})
    assert registered.status_code == 201
    response = await async_client.post("/inventory/scanner/dispatch", json={"value": "ADD UNIT", "session_id": "add-no-tote", "operation_id": str(uuid4()), "workstation_state": {"tote": "", "quantity": 1}})
    assert response.status_code == 200
    assert "Select a tote" in response.json()["blocker"]


@pytest.mark.anyio
async def test_add_unit_creates_one_unidentified_product_and_stock_row(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import InventoryBin, InventoryProduct, InventoryStock, User

    email = f"scanner-add-unknown-{uuid4()}@example.com"
    registered = await async_client.post("/auth/register", json={"full_name": "Unknown Unit", "email": email, "password": "supersecret123"})
    assert registered.status_code == 201
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        tote = InventoryBin(user_id=user.id, code="A02", label="A02", nfc_token=f"nfc-{uuid4()}", container_uuid=str(uuid4()))
        db.add(tote); db.commit()
        user_id, tote_key = user.id, tote.container_uuid
    finally:
        db.close()


    command = await async_client.post("/inventory/scanner/dispatch", json={"value": "ADD UNIT", "session_id": "unknown-add", "operation_id": str(uuid4()), "workstation_state": {"tote": "A02", "quantity": 1}})
    op = str(uuid4())
    scan = await async_client.post("/inventory/scanner/dispatch", json={"value": "LPNRRJJ2497283", "session_id": "unknown-add", "operation_id": op, "workstation_state": command.json()["workstation_state"]})
    assert scan.status_code == 200 and scan.json()["kind"] == "inventory_unit_added"
    assert scan.json()["product"]["enrichment_status"] == "provisional"
    db = SessionLocal()
    try:
        product = db.execute(select(InventoryProduct).where(InventoryProduct.user_id == user_id)).scalar_one()
        stock = db.execute(select(InventoryStock).where(InventoryStock.user_id == user_id, InventoryStock.product_id == product.id)).scalar_one()
        assert product.identifiers_json["barcode"] == "LPNRRJJ2497283"
        assert stock.location_key == tote_key and stock.on_hand == 1
    finally:
        db.close()


@pytest.mark.anyio
async def test_add_unit_reuses_exact_existing_listing_identity_instead_of_unrelated_product(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import InventoryBin, InventoryProduct, InventoryStock, Listing, User
    from app.services.physical_inventory_availability import availability_for_listing

    email = f"scanner-add-listing-match-{uuid4()}@example.com"
    registered = await async_client.post("/auth/register", json={"full_name": "Listing Match", "email": email, "password": "supersecret123"})
    assert registered.status_code == 201
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        tote = InventoryBin(user_id=user.id, code="A03", label="A03", nfc_token=f"nfc-{uuid4()}", container_uuid=str(uuid4()))
        listing = Listing(user_id=user.id, title="Known indexed product", item_specifics={"UPC": "036000291452"}, quantity=1)
        db.add_all([tote, listing]); db.commit()
        user_id, tote_key, listing_id = user.id, tote.container_uuid, listing.id
    finally:
        db.close()

    command = await async_client.post("/inventory/scanner/dispatch", json={"value": "ADD UNIT", "session_id": "listing-match", "operation_id": str(uuid4()), "workstation_state": {"tote": "A03", "quantity": 1}})
    scan = await async_client.post("/inventory/scanner/dispatch", json={"value": "036000291452", "session_id": "listing-match", "operation_id": str(uuid4()), "workstation_state": command.json()["workstation_state"]})
    assert scan.status_code == 200 and scan.json()["kind"] == "inventory_unit_added"
    assert scan.json()["product"]["title"] == "Known indexed product"
    assert scan.json()["product"]["enrichment_status"] == "identified"
    db = SessionLocal()
    try:
        listing = db.get(Listing, listing_id)
        assert availability_for_listing(db, listing)["tracked"] is True
        assert availability_for_listing(db, listing)["available_quantity"] == 1
        products = db.execute(select(InventoryProduct).where(InventoryProduct.user_id == user_id)).scalars().all()
        assert len(products) == 1
        stock = db.execute(select(InventoryStock).where(InventoryStock.user_id == user_id, InventoryStock.product_id == products[0].id, InventoryStock.location_key == tote_key)).scalar_one()
        assert stock.on_hand == 1
    finally:
        db.close()


@pytest.mark.anyio
async def test_add_unit_reuses_existing_asin_alias_and_preserves_distinct_mpn_punctuation(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import InventoryBin, InventoryProduct, InventoryStock, User

    email = f"scanner-add-alias-{uuid4()}@example.com"
    registered = await async_client.post("/auth/register", json={"full_name": "Alias Match", "email": email, "password": "supersecret123"})
    assert registered.status_code == 201
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        tote = InventoryBin(user_id=user.id, code="A04", label="A04", nfc_token=f"nfc-{uuid4()}", container_uuid=str(uuid4()))
        product = InventoryProduct(user_id=user.id, product_key=str(uuid4()), title="Girafit camera", identifiers_json={"asin": "B0DKXPX549", "mpn": "MN: 9574-2SFP28"}, enrichment_status="identified")
        db.add_all([tote, product]); db.commit()
        user_id, tote_key, product_id = user.id, tote.container_uuid, product.id
    finally:
        db.close()
    command = await async_client.post("/inventory/scanner/dispatch", json={"value": "ADD UNIT", "session_id": "alias-match", "operation_id": str(uuid4()), "workstation_state": {"tote": "A04", "quantity": 1}})
    scan = await async_client.post("/inventory/scanner/dispatch", json={"value": "B0DKXPX549", "session_id": "alias-match", "operation_id": str(uuid4()), "workstation_state": command.json()["workstation_state"]})
    assert scan.status_code == 200 and scan.json()["product"]["id"] == product_id
    db = SessionLocal()
    try:
        products = db.execute(select(InventoryProduct).where(InventoryProduct.user_id == user_id)).scalars().all()
        assert len(products) == 1
        stock = db.execute(select(InventoryStock).where(InventoryStock.user_id == user_id, InventoryStock.product_id == product_id, InventoryStock.location_key == tote_key)).scalar_one()
        assert stock.on_hand == 1
    finally:
        db.close()


@pytest.mark.anyio
async def test_move_item_command_arms_owned_stock_lookup_without_moving_or_capturing(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import InventoryBin, InventoryEvent, InventoryProduct, InventoryStock, User

    email = f"scanner-move-{uuid4()}@example.com"
    registered = await async_client.post("/auth/register", json={"full_name": "Scanner Move", "email": email, "password": "supersecret123"})
    assert registered.status_code == 201
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        tote = InventoryBin(user_id=user.id, code="AO-MOVE-1", label="AO-MOVE-1", nfc_token=f"nfc-{uuid4()}", container_uuid=str(uuid4()))
        product = InventoryProduct(user_id=user.id, product_key=str(uuid4()), title="Lens cap", identifiers_json={"barcode": "MOVE-123"})
        db.add_all([tote, product]); db.flush()
        stock = InventoryStock(user_id=user.id, product_id=product.id, location_key=tote.container_uuid, on_hand=3, reserved=1)
        db.add(stock); db.commit()
    finally:
        db.close()


    command = await async_client.post("/inventory/scanner/dispatch", json={
        "value": "MOVE ITEM", "session_id": "packing-move", "operation_id": str(uuid4()),
        "workstation_state": {"tote": "AO-MOVE-1", "quantity": 1},
    })
    assert command.status_code == 200
    assert command.json()["workstation_state"]["awaiting_move_query"] is True
    operation_id = str(uuid4())
    found = await async_client.post("/inventory/scanner/dispatch", json={
        "value": "MOVE-123", "session_id": "packing-move", "operation_id": operation_id,
        "workstation_state": command.json()["workstation_state"],
    })
    assert found.status_code == 200
    body = found.json()
    assert body["kind"] == "inventory_move_selection"
    assert body["lookup"]["products"][0]["locations"][0]["move_eligible"] is True
    assert body["workstation_state"]["last_inventory_lookup"]["move_mode"] is True
    assert body["workstation_state"]["awaiting_move_query"] is False
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        assert db.execute(select(InventoryStock).where(InventoryStock.user_id == user.id)).scalar_one().on_hand == 3
        assert db.execute(select(InventoryEvent).where(InventoryEvent.user_id == user.id, InventoryEvent.operation_id == operation_id)).scalar_one().event_type == "scanner_scan"
    finally:
        db.close()


@pytest.mark.anyio
async def test_bundle_capture_records_identified_components_for_review_without_reserving_stock(async_client):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.models import IntakeSlate, InventoryEvent, InventoryProduct, InventoryStock, User

    email = f"scanner-bundle-{uuid4()}@example.com"
    registered = await async_client.post("/auth/register", json={"full_name": "Scanner Bundle", "email": email, "password": "supersecret123"})
    assert registered.status_code == 201
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        product = InventoryProduct(user_id=user.id, product_key=str(uuid4()), title="Pairing Receiver", identifiers_json={"barcode": "BUNDLE-123"}, enrichment_status="identified")
        db.add(product); db.flush()
        db.add(InventoryStock(user_id=user.id, product_id=product.id, location_key="OUTSIDE-A", on_hand=3, reserved=1))
        db.commit()
        product_id = product.id
        product_key = product.product_key
    finally:
        db.close()

    session_id = "packing-bundle"
    started = await async_client.post("/inventory/scanner/dispatch", json={
        "value": "BUNDLE", "session_id": session_id, "operation_id": str(uuid4()),
        "workstation_state": {"quantity": 2, "current": None},
    })
    assert started.status_code == 200
    state = started.json()["workstation_state"]
    assert state["bundle_draft"]["status"] == "collecting"
    assert state["bundle_draft"]["stock_reserved"] is False

    arm = await async_client.post("/inventory/scanner/dispatch", json={
        "value": "ADD TO BUNDLE", "session_id": session_id, "operation_id": str(uuid4()),
        "workstation_state": state,
    })
    added = await async_client.post("/inventory/scanner/dispatch", json={
        "value": "BUNDLE-123", "session_id": session_id, "operation_id": str(uuid4()),
        "workstation_state": arm.json()["workstation_state"],
    })
    assert added.status_code == 200
    assert added.json()["kind"] == "bundle_component_selection"
    assert added.json()["bundle_draft"]["components"] == [{
        "product_id": product_id, "product_key": product_key,
        "title": "Pairing Receiver", "barcode": "BUNDLE-123", "quantity": 2, "available_at_capture": 2,
    }]
    finished = await async_client.post("/inventory/scanner/dispatch", json={
        "value": "FINISH BUNDLE", "session_id": session_id, "operation_id": str(uuid4()),
        "workstation_state": added.json()["workstation_state"],
    })
    assert finished.status_code == 200
    assert finished.json()["bundle_draft"]["status"] == "review_required"
    assert finished.json()["bundle_draft"]["listing_created"] is False
    assert "not a sellable" in finished.json()["warning"]
    resumed = await async_client.get(f"/inventory/scanner/sessions/{session_id}")
    assert resumed.status_code == 200
    assert resumed.json()["workstation_state"]["bundle_draft"]["status"] == "review_required"
    assert resumed.json()["workstation_state"]["bundle_draft"]["components"][0]["product_id"] == product_id
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        stock = db.execute(select(InventoryStock).where(InventoryStock.user_id == user.id, InventoryStock.product_id == product_id)).scalar_one()
        assert (stock.on_hand, stock.reserved) == (3, 1)
        assert db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user.id)).scalars().all() == []
        assert db.execute(select(InventoryEvent).where(InventoryEvent.user_id == user.id, InventoryEvent.entity_type == "bundle_draft")).scalars().all() == []
    finally:
        db.close()
