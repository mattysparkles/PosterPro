from fastapi import HTTPException

from app.api.inventory import (
    create_package,
    create_tote,
    create_totes_bulk,
    move_inventory_stock,
    package_contents,
    package_detail,
    preview_package_label,
    queue_package_label,
    update_package,
)
from app.models.models import InventoryProduct, InventoryStock, InventoryPrintJob, User
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
