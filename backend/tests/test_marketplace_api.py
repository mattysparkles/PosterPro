from uuid import uuid4

import pytest

from app.core import database as database_module
from app.models.models import Cluster, Listing
from app.workers import tasks


def seed_listing(user_id: int) -> int:
    db = database_module.SessionLocal()
    cluster = Cluster(user_id=user_id, title_hint="Shoes")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    listing = Listing(user_id=user_id, cluster_id=cluster.id, title="Shoes", description="Clean")
    db.add(listing)
    db.commit()
    db.refresh(listing)
    db.close()
    return listing.id


@pytest.mark.anyio
async def test_marketplace_discovery_and_publish_queue(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Marketplace Owner",
            "email": f"market-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    listing_id = seed_listing(user_id)

    class DummyTask:
        id = "task-123"

    monkeypatch.setattr(tasks.publish_listing_to_marketplace_task, "delay", lambda *_args, **_kwargs: DummyTask())

    m = await async_client.get("/marketplaces")
    assert m.status_code == 200
    assert any(row["name"] == "ebay" for row in m.json()["marketplaces"])

    response = await async_client.post(f"/listings/{listing_id}/publish", json={"marketplaces": ["ebay", "mercari"]})
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert all(item["status"] == "QUEUED" for item in results)
