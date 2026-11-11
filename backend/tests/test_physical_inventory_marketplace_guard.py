from uuid import uuid4

from app.models.models import InventoryEvent, InventoryProduct, InventoryStock, Listing, User
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.physical_inventory_availability import record_confirmed_sale_depletion


def _seed_linked_listing(db_session, *, on_hand=2, reserved=0, quantity=2):
    user = User(email=f"physical-market-{uuid4()}@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    product = InventoryProduct(user_id=user.id, product_key=str(uuid4()), title="Physical widget", identifiers_json={"barcode": "012345678905"})
    db_session.add(product)
    db_session.flush()
    stock = InventoryStock(user_id=user.id, product_id=product.id, location_key="tote-AO9", on_hand=on_hand, reserved=reserved)
    listing = Listing(
        user_id=user.id,
        title="Physical widget",
        description="A real physical widget with adequate buyer-facing detail.",
        quantity=quantity,
        listing_price=10,
        condition="New",
        category_id="123",
        listing_images=[{"url": "https://example.test/widget.jpg", "operator_state": "approved", "is_reference": False}],
        shipping_profile={"package_weight": "1 lb", "package_dimensions": {"length": 3, "width": 2, "height": 1}},
        marketplace_data={"physical_inventory": {"product_id": product.id}},
    )
    db_session.add_all([stock, listing])
    db_session.commit()
    return listing, stock


def test_preflight_blocks_linked_listing_when_physical_stock_is_reserved_or_short(db_session):
    listing, _stock = _seed_linked_listing(db_session, on_hand=2, reserved=1, quantity=2)
    report = MarketplacePreflightService().preflight_listing(db_session, listing, "mercari")
    codes = {row["code"] for row in report["blockers"]}
    assert "PHYSICAL_STOCK_INSUFFICIENT" in codes
    assert report["physical_inventory_summary"]["available_quantity"] == 1
    assert report["physical_inventory_summary"]["linkage"] == "explicit_product_id"


def test_confirmed_sale_consumes_linked_stock_and_is_idempotent(db_session):
    listing, stock = _seed_linked_listing(db_session, on_hand=3, reserved=1, quantity=3)
    first = record_confirmed_sale_depletion(db_session, listing, sale_id=101, quantity=2)
    db_session.commit()
    db_session.refresh(stock)
    assert first["status"] == "depleted"
    assert stock.on_hand == 1
    assert stock.reserved == 0
    assert stock.sold == 2

    replay = record_confirmed_sale_depletion(db_session, listing, sale_id=101, quantity=2)
    db_session.commit()
    db_session.refresh(stock)
    assert replay["status"] == "replayed"
    assert stock.on_hand == 1
    assert db_session.query(InventoryEvent).filter(InventoryEvent.event_type == "marketplace_sale_depleted").count() == 1


def test_unlinked_listing_warns_without_inventing_a_stock_match(db_session):
    user = User(email=f"unlinked-market-{uuid4()}@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    listing = Listing(user_id=user.id, title="Legacy draft", quantity=1)
    db_session.add(listing)
    db_session.commit()
    report = MarketplacePreflightService().preflight_listing(db_session, listing, "mercari")
    assert report["physical_inventory_summary"]["tracked"] is False
    assert "PHYSICAL_STOCK_UNLINKED" in {row["code"] for row in report["warnings"]}
