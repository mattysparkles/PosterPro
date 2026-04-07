import os
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite:///./test_marketplace_api.db"

from fastapi.testclient import TestClient

from app.main import app
from app.core.database import Base, SessionLocal, engine
from app.models.models import Cluster, Listing, User
from app.workers import tasks

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


def seed_listing():
    db = SessionLocal()
    user = User(email=f"market-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    cluster = Cluster(user_id=user.id, title_hint="Shoes")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    listing = Listing(user_id=user.id, cluster_id=cluster.id, title="Shoes", description="Clean")
    db.add(listing)
    db.commit()
    db.refresh(listing)
    db.close()
    return listing.id


def test_marketplace_discovery_and_publish_queue(monkeypatch):
    listing_id = seed_listing()

    class DummyTask:
        id = "task-123"

    monkeypatch.setattr(tasks.publish_listing_to_marketplace_task, "delay", lambda *_args, **_kwargs: DummyTask())

    client = TestClient(app)
    m = client.get("/marketplaces")
    assert m.status_code == 200
    assert any(row["name"] == "ebay" for row in m.json()["marketplaces"])

    response = client.post(f"/listings/{listing_id}/publish", json={"marketplaces": ["ebay", "mercari"]})
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert all(item["status"] == "QUEUED" for item in results)
