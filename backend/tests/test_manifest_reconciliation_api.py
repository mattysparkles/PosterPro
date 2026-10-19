import asyncio
from io import BytesIO

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api.inventory import (
    create_tote,
    import_manifest,
    manifest_detail,
    reconcile_manifest_entry,
    tote_detail,
)
from app.models.models import InventoryProduct, InventoryStock, User


def upload(name, content):
    return UploadFile(filename=name, file=BytesIO(content.encode()))


def test_manifest_conflict_can_move_stock_and_tote_contents_are_accurate(db_session):
    user = User(email="manifest-flow@example.com")
    db_session.add(user)
    db_session.flush()
    source = create_tote({"code": "AO1"}, db=db_session, current_user=user)
    destination = create_tote({"code": "AO2"}, db=db_session, current_user=user)
    product = InventoryProduct(user_id=user.id, product_key="product-1", title="Known scanner", identifiers_json={"barcode": "12345"})
    db_session.add(product)
    db_session.flush()
    db_session.add(InventoryStock(user_id=user.id, product_id=product.id, location_key=source["container_uuid"], on_hand=3, reserved=1))
    db_session.commit()

    result = asyncio.run(import_manifest("AO2", "quantity", "manifest-operation-1", upload("ao2.txt", "12345\n12345\n"), db_session, user))
    assert result["status"] == "pending_reconciliation"
    detail = manifest_detail(result["import_id"], db_session, user)
    entry = detail["entries"][0]
    assert entry["source_locations"] == [{"location_key": source["container_uuid"], "label": "AO1", "on_hand": 3, "reserved": 1, "available": 2}]

    decision = reconcile_manifest_entry(result["import_id"], entry["id"], {"action": "move", "quantity": 2, "source_location_key": source["container_uuid"]}, "decision-operation-1", db_session, user)
    assert decision["manifest_status"] == "applied"
    assert tote_detail("AO1", db_session, user)["summary"]["available"] == 0
    destination_detail = tote_detail("AO2", db_session, user)
    assert destination_detail["summary"] == {"product_count": 1, "on_hand": 2, "available": 2}

    replay = asyncio.run(import_manifest("AO2", "quantity", "manifest-operation-1", upload("ao2.txt", "12345\n12345\n"), db_session, user))
    assert replay["status"] == "replayed"


def test_move_requires_a_distinct_source_location(db_session):
    user = User(email="manifest-move@example.com")
    db_session.add(user)
    db_session.flush()
    tote = create_tote({"code": "AO9"}, db=db_session, current_user=user)
    product = InventoryProduct(user_id=user.id, product_key="product-9", identifiers_json={"barcode": "9"})
    db_session.add(product)
    db_session.flush()
    db_session.add(InventoryStock(user_id=user.id, product_id=product.id, location_key=tote["container_uuid"], on_hand=1))
    db_session.commit()
    result = asyncio.run(import_manifest("AO9", "dedupe", "manifest-operation-9", upload("ao9.txt", "9\n"), db_session, user))
    # The source is already this tote, so the normal import applies directly.
    assert result["status"] == "applied"
