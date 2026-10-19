from app.api.inventory import create_package, create_tote, find_inventory_item, suggest_freeform_qr_associations
from app.models.models import InventoryProduct, InventoryStock, User


def test_find_inventory_returns_owned_product_and_physical_location(db_session):
    user = User(email="find-owner@example.com")
    db_session.add(user); db_session.flush()
    tote = create_tote({"code": "AO9"}, db=db_session, current_user=user)
    product = InventoryProduct(user_id=user.id, product_key="find-product", title="Test scanner", identifiers_json={"barcode": "012345678901"})
    db_session.add(product); db_session.flush()
    db_session.add(InventoryStock(user_id=user.id, product_id=product.id, location_key=tote["container_uuid"], on_hand=2, reserved=1))
    db_session.commit()

    result = find_inventory_item("012345678901", db=db_session, current_user=user)
    assert result["found"] is True
    assert result["match_type"] == "product"
    assert result["products"][0]["locations"] == [{"location_key": tote["container_uuid"], "label": "AO9", "on_hand": 2, "reserved": 1, "available": 1}]
    tote_result = find_inventory_item("AO9", db=db_session, current_user=user)
    assert tote_result["match_type"] == "tote"
    assert tote_result["tote"]["contents"][0]["barcode"] == "012345678901"


def test_find_inventory_includes_package_and_has_explicit_negative_state(db_session):
    user = User(email="find-package@example.com")
    db_session.add(user); db_session.flush()
    package = create_package({"package_code": "PKG-FIND-1"}, db=db_session, current_user=user)["package"]
    found = find_inventory_item("PKG-FIND-1", db=db_session, current_user=user)
    assert found["match_type"] == "package"
    assert found["package"]["package_uuid"] == package["package_uuid"]
    missing = find_inventory_item("nothing-here", db=db_session, current_user=user)
    assert missing == {"found": False, "match_type": None, "query": "nothing-here", "message": "No owned tote, package, or product matched this scan."}


def test_freeform_qr_lookup_only_suggests_owned_products(db_session):
    owner = User(email="freeform-owner@example.com")
    other = User(email="freeform-other@example.com")
    db_session.add_all([owner, other]); db_session.flush()
    db_session.add_all([
        InventoryProduct(user_id=owner.id, product_key="bag", title="Black Leather Shoulder Bag"),
        InventoryProduct(user_id=other.id, product_key="other", title="Black Leather Shoulder Bag"),
    ])
    db_session.commit()
    result = suggest_freeform_qr_associations("Box A04 black shoulder bag", db=db_session, current_user=owner)
    assert result["requires_operator_confirmation"] is True
    assert result["automatic_merge_performed"] is False
    assert [(row["product_key"], row["title"]) for row in result["suggestions"]] == [("bag", "Black Leather Shoulder Bag")]
