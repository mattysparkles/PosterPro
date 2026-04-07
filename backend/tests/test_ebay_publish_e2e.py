import os
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite:///./test_e2e.db"

from fastapi.testclient import TestClient

from app.core.database import Base, SessionLocal, engine
from app.main import app
from app.models.enums import EbayPublishStatus
from app.models.models import Cluster, Listing, User
import app.api.ebay as ebay_api
import app.services.ebay_service as ebay_service


Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


def seed_listing():
    db = SessionLocal()
    user = User(email=f"demo-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)

    cluster = Cluster(user_id=user.id, title_hint="Lamp")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    listing = Listing(user_id=user.id, cluster_id=cluster.id, title="Lamp", description="Desc")
    db.add(listing)
    db.commit()
    db.refresh(listing)
    db.close()
    return listing.id


def test_publish_success(monkeypatch):
    listing_id = seed_listing()

    async def fake_publish(listing, db):
        listing.ebay_publish_status = EbayPublishStatus.POSTED
        listing.ebay_listing_id = "12345"
        listing.marketplace_data = {"ebay_url": "https://www.ebay.com/itm/12345"}
        db.add(listing)
        db.commit()
        return {"listing_id": "12345", "status": "POSTED", "ebay_url": "https://www.ebay.com/itm/12345"}

    monkeypatch.setattr(ebay_service, "publish_listing_to_ebay", fake_publish)
    monkeypatch.setattr(ebay_api, "publish_listing_to_ebay", fake_publish)

    client = TestClient(app)
    response = client.post(f"/listings/{listing_id}/publish/ebay")
    assert response.status_code == 200
    assert response.json()["status"] == "POSTED"


def test_publish_failure(monkeypatch):
    listing_id = seed_listing()

    async def fake_publish(_listing, _db):
        raise ebay_service.EbayIntegrationError("retry exhausted")

    monkeypatch.setattr(ebay_service, "publish_listing_to_ebay", fake_publish)
    monkeypatch.setattr(ebay_api, "publish_listing_to_ebay", fake_publish)

    client = TestClient(app)
    response = client.post(f"/listings/{listing_id}/publish/ebay")
    assert response.status_code == 400
    assert "retry exhausted" in response.json()["detail"]
