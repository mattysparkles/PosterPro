from fastapi import HTTPException

from app.api.inventory import (
    create_package,
    create_tote,
    create_totes_bulk,
    ensure_permanent_tote_pages,
    move_inventory_stock,
    package_contents,
    package_detail,
    preview_package_label,
    queue_package_label,
    update_package,
)
from app.models.models import InventoryBin, InventoryProduct, InventoryStock, InventoryPrintJob, User
from app.services.package_labels import code128_values


def test_package_labels_use_real_code128_set_b_with_checksum_and_stop():
    # Start-B 104, "A" is 33, checksum (104 + 33) % 103 = 34, STOP 106.
    assert code128_values("A") == [104, 33, 34, 106]


def test_package_move_is_idempotent_and_label_job_has_stable_lookup(db_session):
    user = User(email="package-flow@example.com")
    db_session.add(user); db_session.flush()
    tote = create_tote({"code": "AO9"}, db=db_session, current_user=user)
    product = InventoryProduct(user_id=user.id, product_key="pkg-product", title="Boxed scanner", identifiers_json={"barcode": "001234"})
    db_session.add(product); db_session.flush()
    db_session.add(InventoryStock(user_id=user.id, product_id=product.id, location_key=tote["container_uuid"], on_hand=3))
    db_session.commit()

    created = create_package({"package_code": "PKG-001", "parent_location_key": tote["container_uuid"], "metadata": {"title": "Scanner bundle"}}, db=db_session, current_user=user)
    package = created["package"]
    assert created["created"] is True
    applied = package_contents("PKG-001", {"action": "move", "product_id": product.id, "quantity": 2, "source_location_key": tote["container_uuid"]}, "pkg-move-1", db_session, user)
    assert applied["status"] == "applied"
    replay = package_contents("PKG-001", {"action": "move", "product_id": product.id, "quantity": 2, "source_location_key": tote["container_uuid"]}, "pkg-move-1", db_session, user)
    assert replay["status"] == "replayed"
    assert package_detail("PKG-001", db_session, user)["package"]["quantity"] == 2

    preview = preview_package_label("PKG-001", "thermal_54x100mm", db_session, user)
    assert preview["payload"]["lookup_url"] == "/inventory/packages/PKG-001"
    assert preview["image_data_url"].startswith("data:image/png;base64,")
    queued = queue_package_label("PKG-001", {"copies": 2, "paper_profile": "thermal_54x100mm"}, db_session, user)
    job = db_session.get(InventoryPrintJob, queued["job_id"])
    assert job.package_id == package["id"]
    assert job.payload_json["lookup_url"] == "/inventory/packages/PKG-001"


def test_package_nesting_rejects_cycle_and_unknown_parent(db_session):
    user = User(email="package-cycle@example.com")
    db_session.add(user); db_session.flush()
    first = create_package({"package_code": "PKG-A"}, db=db_session, current_user=user)["package"]
    second = create_package({"package_code": "PKG-B", "parent_location_key": first["package_uuid"]}, db=db_session, current_user=user)["package"]
    assert second["parent_location_key"] == first["package_uuid"]
    try:
        update_package("PKG-A", {"parent_location_key": second["package_uuid"]}, db=db_session, current_user=user)
        assert False, "Expected cycle rejection"
    except HTTPException as error:
        assert error.status_code == 422
    try:
        create_package({"package_code": "PKG-C", "parent_location_key": "not-my-container"}, db=db_session, current_user=user)
        assert False, "Expected owner validation"
    except HTTPException as error:
        assert error.status_code == 422


def test_stock_move_conserves_units_and_replays_idempotently(db_session):
    user = User(email="stock-move@example.com")
    db_session.add(user); db_session.flush()
    source = create_tote({"code": "MOVE-SOURCE"}, db=db_session, current_user=user)
    destination = create_tote({"code": "MOVE-DEST"}, db=db_session, current_user=user)
    product = InventoryProduct(user_id=user.id, product_key="move-product", title="Camera lens", identifiers_json={"barcode": "LENS-1"})
    db_session.add(product); db_session.flush()
    db_session.add(InventoryStock(user_id=user.id, product_id=product.id, location_key=source["container_uuid"], on_hand=4, reserved=1))
    db_session.commit()
    request = {"product_id": product.id, "quantity": 2, "source_location_key": source["container_uuid"], "destination_location_key": destination["container_uuid"]}

    applied = move_inventory_stock(request, "move-op-1", db_session, user)
    assert applied["status"] == "applied"
    assert applied["source_on_hand"] == 2
    assert applied["destination_on_hand"] == 2
    replay = move_inventory_stock(request, "move-op-1", db_session, user)
    assert replay["status"] == "replayed"
    rows = db_session.query(InventoryStock).filter_by(user_id=user.id, product_id=product.id).all()
    assert sum(row.on_hand for row in rows) == 4
    assert sum(row.reserved for row in rows) == 1


def test_stock_move_blocks_reserved_units_and_non_owned_locations(db_session):
    user = User(email="stock-move-block@example.com")
    other = User(email="stock-move-other@example.com")
    db_session.add_all([user, other]); db_session.flush()
    source = create_tote({"code": "MOVE-BLOCK-SOURCE"}, db=db_session, current_user=user)
    foreign = create_tote({"code": "FOREIGN-DEST"}, db=db_session, current_user=other)
    product = InventoryProduct(user_id=user.id, product_key="move-block-product", title="Reserved item")
    db_session.add(product); db_session.flush()
    db_session.add(InventoryStock(user_id=user.id, product_id=product.id, location_key=source["container_uuid"], on_hand=2, reserved=1))
    db_session.commit()
    request = {"product_id": product.id, "quantity": 2, "source_location_key": source["container_uuid"], "destination_location_key": foreign["container_uuid"]}
    try:
        move_inventory_stock(request, "move-op-reserved", db_session, user)
        assert False, "Reserved quantity must not be movable"
    except HTTPException as error:
        assert error.status_code == 404  # destination is deliberately not in this tenant
    request["destination_location_key"] = create_tote({"code": "MOVE-OWN-DEST"}, db=db_session, current_user=user)["container_uuid"]
    try:
        move_inventory_stock(request, "move-op-reserved", db_session, user)
        assert False, "Reserved quantity must not be movable"
    except HTTPException as error:
        assert error.status_code == 409


def test_stock_move_refuses_removing_units_from_sealed_package(db_session):
    user = User(email="stock-move-sealed@example.com")
    db_session.add(user); db_session.flush()
    package = create_package({"package_code": "SEALED-PKG", "status": "sealed"}, db=db_session, current_user=user)["package"]
    destination = create_tote({"code": "SEALED-DEST"}, db=db_session, current_user=user)
    product = InventoryProduct(user_id=user.id, product_key="sealed-package-stock", title="Sealed item")
    db_session.add(product); db_session.flush()
    db_session.add(InventoryStock(user_id=user.id, product_id=product.id, location_key=package["package_uuid"], on_hand=1))
    db_session.commit()
    try:
        move_inventory_stock({"product_id": product.id, "quantity": 1, "source_location_key": package["package_uuid"], "destination_location_key": destination["container_uuid"]}, "move-from-sealed", db_session, user)
        assert False, "A sealed package must not be opened by a stock movement"
    except HTTPException as error:
        assert error.status_code == 409


def test_bulk_totes_return_nfc_instructions_without_claiming_written_tags(db_session):
    user = User(email="package-nfc@example.com")
    db_session.add(user); db_session.flush()
    result = create_totes_bulk({"codes": ["AO1", "AO2"]}, db=db_session, current_user=user)
    assert result["created_count"] == 2
    assert all(row["nfc_url"].startswith("/scan/bin/") for row in result["totes"])
    assert {row["programming_status"] for row in result["totes"]} == {"not_programmed"}
    repeated = create_totes_bulk({"codes": ["AO1", "AO2"]}, db=db_session, current_user=user)
    assert repeated["created_count"] == 0
    assert repeated["existing_count"] == 2


def test_permanent_nfc_totes_are_idempotent_and_use_canonical_inventory_records(db_session):
    from app.api.inventory import tote_detail
    from app.models.models import User

    user = User(email="permanent-totes@example.com")
    db_session.add(user)
    db_session.flush()
    first = ensure_permanent_tote_pages(db=db_session, current_user=user)
    assert first["created_count"] == 8
    assert [row["code"] for row in first["totes"]] == [f"A{i:02d}" for i in range(1, 9)]
    assert first["totes"][0]["permanent_url"] == "https://posterpro.sparkleserver.site/inventory/tote-a01"
    repeated = ensure_permanent_tote_pages(db=db_session, current_user=user)
    assert repeated["created_count"] == 0
    assert repeated["existing_count"] == 8
    assert tote_detail("tote-a01", db=db_session, current_user=user)["tote"]["code"] == "A01"


def test_tote_manifest_exposes_unresolved_barcode_claims_without_counting_them_as_stock(db_session):
    from app.api.inventory import create_tote, tote_detail
    from app.models.models import InventoryManifestEntry, InventoryManifestImport

    user = User(email="tote-unresolved@example.com")
    db_session.add(user); db_session.flush()
    create_tote({"code": "A03"}, db=db_session, current_user=user)
    tote = db_session.query(InventoryBin).filter_by(user_id=user.id, code="A03").one()
    product = InventoryProduct(user_id=user.id, product_key="provisional-cross-tote", title="Unidentified item (X00556MVCF)", identifiers_json={"barcode": "X00556MVCF"}, enrichment_status="provisional")
    db_session.add(product); db_session.flush()
    manifest = InventoryManifestImport(user_id=user.id, tote_id=tote.id, filename="seed.txt", checksum="a" * 64, duplicate_policy="historical_dedupe", raw_scan_count=1, source_text="X00556MVCF\n", operation_id="seed-A03", status="applied")
    db_session.add(manifest); db_session.flush()
    db_session.add(InventoryManifestEntry(manifest_import_id=manifest.id, barcode="X00556MVCF", scan_count=1, applied_quantity=0, product_id=product.id, resolution="pending_reconciliation"))
    db_session.commit()

    result = tote_detail("A03", db=db_session, current_user=user)
    assert len(result["items"]) == 1
    assert result["items"][0]["barcode"] == "X00556MVCF"
    assert result["items"][0]["status"] == "pending_reconciliation"
    assert result["items"][0]["on_hand"] == 0
    assert result["summary"]["unresolved_location_claims"] == 1
    assert result["summary"]["on_hand"] == 0


def test_barcode_lookup_matches_upc_ean_gtin_and_prevents_ambiguous_manifest_merge(db_session):
    import asyncio
    from io import BytesIO
    from fastapi import HTTPException
    from starlette.datastructures import UploadFile
    from app.api.inventory import create_tote, import_manifest
    from app.models.models import InventoryProduct, InventoryStock, User

    user = User(email="universal-barcode@example.com")
    db_session.add(user)
    db_session.flush()
    tote = create_tote({"code": "BARCODE-TOTE"}, db=db_session, current_user=user)
    product = InventoryProduct(user_id=user.id, product_key="ean-product", title="EAN matched product", identifiers_json={"ean": "0036000291452"})
    db_session.add(product)
    db_session.commit()
    result = asyncio.run(import_manifest("BARCODE-TOTE", "dedupe", "upc-ean-op", UploadFile(filename="upc.txt", file=BytesIO(b"036000291452\n")), db_session, user))
    assert result["preview"]["known_products"][0]["product_id"] == product.id
    stock = db_session.query(InventoryStock).filter_by(user_id=user.id, product_id=product.id, location_key=tote["container_uuid"]).one()
    assert stock.on_hand == 1
    assert db_session.query(InventoryProduct).filter_by(user_id=user.id).count() == 1

    duplicate = InventoryProduct(user_id=user.id, product_key="upc-duplicate", title="Ambiguous duplicate", identifiers_json={"upc": "036000291452"})
    db_session.add(duplicate)
    db_session.commit()
    try:
        asyncio.run(import_manifest("BARCODE-TOTE", "dedupe", "upc-ean-ambiguous", UploadFile(filename="ambiguous.txt", file=BytesIO(b"0036000291452\n")), db_session, user))
        assert False, "ambiguous cross-format identifiers must require reconciliation"
    except HTTPException as error:
        assert error.status_code == 409
        assert error.detail["preview"]["ambiguous_matches"]


def test_scanned_identifier_family_detection_preserves_raw_values_and_unknown_formats():
    from app.api.inventory import _identifier_type

    assert _identifier_type("X00556MVCF") == "amazon_fnsku"
    assert _identifier_type("B0DKXPX549") == "asin_candidate"
    assert _identifier_type("LPNRRJJ2497283") == "amazon_lpn"
    assert _identifier_type("0036000291452") == "ean13_candidate"
    assert _identifier_type("9780306406157") == "isbn13_candidate"
    assert _identifier_type("MN: 9574-2SFP28") == "manufacturer_part_number_candidate"
    assert _identifier_type("DC97-15459H") == "manufacturer_part_number_candidate"
    assert _identifier_type("868022038535618") == "unknown_scanned_identifier"
