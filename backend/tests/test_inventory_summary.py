from datetime import datetime
from uuid import uuid4

import pytest

from app.core import database as database_module
from app.api.inventory import get_inventory_summary
from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import CanonicalItem, InventoryProduct, InventoryStock, Listing, MarketplaceListing, User


def test_inventory_summary_counts_owner_scoped_listing_and_stock_concepts(db_session):
    owner = User(email="inventory-summary-owner@example.test")
    other = User(email="inventory-summary-other@example.test")
    db_session.add_all([owner, other])
    db_session.flush()

    active = Listing(user_id=owner.id, status=ListingStatus.ready, quantity=1)
    draft = Listing(user_id=owner.id, status=ListingStatus.draft, quantity=2)
    sold = Listing(user_id=owner.id, status=ListingStatus.posted, quantity=0, sold_at=datetime.utcnow())
    archived = Listing(user_id=owner.id, status=ListingStatus.archived, quantity=1)
    foreign = Listing(user_id=other.id, status=ListingStatus.ready, quantity=1)
    db_session.add_all([active, draft, sold, archived, foreign])
    db_session.flush()
    db_session.add_all([
        MarketplaceListing(listing_id=active.id, marketplace=MarketplaceName.ebay,
                          marketplace_listing_id="EBAY-1", status=MarketplaceListingStatus.PUBLISHED),
        MarketplaceListing(listing_id=active.id, marketplace=MarketplaceName.facebook,
                          marketplace_listing_id="FB-1", status=MarketplaceListingStatus.PUBLISHED),
        MarketplaceListing(listing_id=draft.id, marketplace=MarketplaceName.mercari,
                          marketplace_listing_id=None, status=MarketplaceListingStatus.PUBLISHED),
        MarketplaceListing(listing_id=foreign.id, marketplace=MarketplaceName.ebay,
                          marketplace_listing_id="OTHER-1", status=MarketplaceListingStatus.PUBLISHED),
    ])
    canonical = CanonicalItem(user_id=owner.id, item_id="CANONICAL-1")
    product = InventoryProduct(user_id=owner.id, product_key="stock-product-1")
    db_session.add_all([canonical, product])
    db_session.flush()
    db_session.add(InventoryStock(user_id=owner.id, product_id=product.id, location_key="TOTE-1", on_hand=3))
    db_session.commit()

    result = get_inventory_summary(db_session, owner)

    assert result == {
        "all_listings": 4,
        "active_listings": 2,
        "draft_listings": 1,
        "sold_listings": 1,
        "archived_listings": 1,
        "intake_listings": 0,
        "stale_listings": 0,
        "multi_quantity_listings": 1,
        "published_marketplace_placements": 2,
        "canonical_inventory_items": 1,
        "physical_stock_products": 1,
        "physical_stock_records": 1,
        "physical_units_on_hand": 3,
    }


@pytest.mark.anyio
async def test_inventory_summary_is_available_through_authenticated_api(async_client):
    response = await async_client.post("/auth/register", json={
        "full_name": "Inventory Summary API",
        "email": f"inventory-summary-api-{uuid4()}@example.test",
        "password": "test-password-123",
    })
    assert response.status_code == 201
    user_id = response.json()["user"]["id"]

    db = database_module.SessionLocal()
    try:
        db.add(Listing(user_id=user_id, status=ListingStatus.ready, quantity=1))
        db.add(Listing(user_id=user_id, status=ListingStatus.archived, quantity=1))
        db.commit()
    finally:
        db.close()

    result = await async_client.get("/inventory/summary")
    assert result.status_code == 200
    assert result.json()["all_listings"] == 2
    assert result.json()["active_listings"] == 1
    assert result.json()["archived_listings"] == 1
    assert result.json()["published_marketplace_placements"] == 0
