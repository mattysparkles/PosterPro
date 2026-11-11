from uuid import uuid4

from app.models.models import InventoryProduct, Listing, User
from app.services.physical_inventory_availability import linked_product


def test_uppercase_marketplace_upc_links_to_exact_inventory_product(db_session):
    user = User(email=f"inventory-link-{uuid4()}@example.com")
    db_session.add(user)
    db_session.flush()
    product = InventoryProduct(user_id=user.id, product_key=str(uuid4()), title="Jade Plus", identifiers_json={"barcode": "198715192346"}, enrichment_status="provisional")
    listing = Listing(user_id=user.id, title="Jade Plus", item_specifics={"UPC": "198715192346"})
    db_session.add_all([product, listing])
    db_session.commit()

    linked, provenance = linked_product(db_session, listing)
    assert linked.id == product.id
    assert provenance == "exact_identifier"


def test_conflicting_exact_identifiers_do_not_choose_a_product(db_session):
    user = User(email=f"inventory-link-conflict-{uuid4()}@example.com")
    db_session.add(user)
    db_session.flush()
    first = InventoryProduct(user_id=user.id, product_key=str(uuid4()), title="Product A", identifiers_json={"barcode": "198715192346"}, enrichment_status="identified")
    second = InventoryProduct(user_id=user.id, product_key=str(uuid4()), title="Product B", identifiers_json={"asin": "B0DS46KDHM"}, enrichment_status="identified")
    listing = Listing(user_id=user.id, title="Conflicting product", item_specifics={"UPC": "198715192346"}, source_metadata={"ASIN": "B0DS46KDHM"})
    db_session.add_all([first, second, listing])
    db_session.commit()

    linked, provenance = linked_product(db_session, listing)
    assert linked is None
    assert provenance == "conflicting_exact_identifiers"
