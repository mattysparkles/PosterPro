from uuid import uuid4

import pytest

from app.core import database as database_module
from app.models.enums import EbayPublishStatus
from app.models.models import Cluster, Listing
import app.api.ebay as ebay_api
import app.services.ebay_service as ebay_service

def seed_listing(user_id: int) -> int:
    db = database_module.SessionLocal()
    cluster = Cluster(user_id=user_id, title_hint="Lamp")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    listing = Listing(user_id=user_id, cluster_id=cluster.id, title="Lamp", description="Desc")
    db.add(listing)
    db.commit()
    db.refresh(listing)
    db.close()
    return listing.id


@pytest.mark.anyio
async def test_publish_success(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "eBay Owner",
            "email": f"demo-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    async def fake_publish(listing, db):
        listing.ebay_publish_status = EbayPublishStatus.POSTED
        listing.ebay_listing_id = "12345"
        listing.marketplace_data = {"ebay_url": "https://www.ebay.com/itm/12345"}
        db.add(listing)
        db.commit()
        return {"listing_id": "12345", "status": "POSTED", "ebay_url": "https://www.ebay.com/itm/12345"}

    monkeypatch.setattr(ebay_service, "publish_listing_to_ebay", fake_publish)
    monkeypatch.setattr(ebay_api, "publish_listing_to_ebay", fake_publish)

    response = await async_client.post(f"/listings/{listing_id}/publish/ebay")
    assert response.status_code == 200
    assert response.json()["status"] == "POSTED"


@pytest.mark.anyio
async def test_publish_failure(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "eBay Owner",
            "email": f"demo-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    async def fake_publish(_listing, _db):
        raise ebay_service.EbayIntegrationError("retry exhausted")

    monkeypatch.setattr(ebay_service, "publish_listing_to_ebay", fake_publish)
    monkeypatch.setattr(ebay_api, "publish_listing_to_ebay", fake_publish)

    response = await async_client.post(f"/listings/{listing_id}/publish/ebay")
    assert response.status_code == 400
    assert "retry exhausted" in response.json()["detail"]
