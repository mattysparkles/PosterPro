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


@pytest.mark.anyio
async def test_listing_approve_auto_queues_when_enabled(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Approve Owner",
            "email": f"approve-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    settings_resp = await async_client.patch(
        "/auth/me",
        json={"auto_publish_after_approval": True},
    )
    assert settings_resp.status_code == 200

    listing_id = seed_listing(user_id)
    await async_client.patch(
        f"/listings/{listing_id}",
        json={"needs_review": True, "status": "draft", "marketplace_data": {"targets": ["ebay"]}},
    )

    class DummyTask:
        id = "approve-task-123"

    monkeypatch.setattr(tasks.publish_listing_to_marketplace_task, "delay", lambda *_args, **_kwargs: DummyTask())

    response = await async_client.post(f"/listings/{listing_id}/approve")
    assert response.status_code == 200
    payload = response.json()
    assert payload["auto_publish_after_approval"] is True
    assert len(payload["results"]) == 2
    targets = sorted([item["marketplace"] for item in payload["results"]])
    assert targets == ["ebay", "facebook"]
    assert payload["listing"]["needs_review"] is False
    assert payload["listing"]["status"] == "ready"


@pytest.mark.anyio
async def test_listing_bulk_approve_uses_same_contract(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Bulk Approve Owner",
            "email": f"approve-bulk-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    listing_a = seed_listing(user_id)
    listing_b = seed_listing(user_id)
    for listing_id in (listing_a, listing_b):
        update_resp = await async_client.patch(
            f"/listings/{listing_id}",
            json={"needs_review": True, "status": "draft", "marketplace_data": {"targets": ["ebay"]}},
        )
        assert update_resp.status_code == 200

    class DummyTask:
        id = "approve-bulk-task-123"

    monkeypatch.setattr(tasks.publish_listing_to_marketplace_task, "delay", lambda *_args, **_kwargs: DummyTask())

    response = await async_client.post("/listings/approve-bulk", json={"listing_ids": [listing_a, listing_b]})
    assert response.status_code == 200
    payload = response.json()
    approvals = payload["approvals"]
    assert len(approvals) == 2
    for approval in approvals:
        assert approval["auto_publish_after_approval"] is False
        assert approval["results"] == []
        assert approval["listing"]["needs_review"] is False
        assert approval["listing"]["status"] == "ready"
