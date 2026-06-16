from uuid import uuid4

import pytest

from app.core import database as database_module
from app.models.enums import MarketplaceName
from app.models.models import Cluster, Listing, Sale
from app.api import marketplaces as marketplaces_api
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
    monkeypatch.setattr(marketplaces_api, "queue_publish", lambda *_args, **_kwargs: [
        {"marketplace": "ebay", "status": "QUEUED", "task_id": "task-123"},
        {"marketplace": "mercari", "status": "MANUAL_HANDOFF_READY", "task_id": None},
    ])

    m = await async_client.get("/marketplaces")
    assert m.status_code == 200
    assert any(row["name"] == "ebay" for row in m.json()["marketplaces"])

    response = await async_client.post(
        f"/listings/{listing_id}/publish",
        json={
            "marketplaces": ["ebay", "mercari"],
            "confirm_live_publish": True,
            "confirmation_phrase": "QUEUE LIVE EBAY READY LISTINGS",
        },
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert results[0]["status"] == "QUEUED"
    assert results[1]["status"] == "MANUAL_HANDOFF_READY"


@pytest.mark.anyio
async def test_marketplace_publish_multi_requires_explicit_live_confirmation(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Marketplace Owner",
            "email": f"market-multi-confirm-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    called = []

    def fake_queue_publish(*args, **kwargs):
        called.append((args, kwargs))
        return [{"marketplace": "ebay", "status": "QUEUED", "task_id": "task-1"}]

    monkeypatch.setattr(marketplaces_api, "queue_publish", fake_queue_publish)

    response = await async_client.post(
        f"/listings/{listing_id}/publish",
        json={"marketplaces": ["ebay"], "confirm_live_publish": False},
    )
    assert response.status_code == 400
    assert "QUEUE LIVE EBAY READY LISTINGS" in response.json()["detail"]
    assert called == []


@pytest.mark.anyio
async def test_bulk_publish_ready_requires_explicit_live_confirmation(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Marketplace Owner",
            "email": f"market-confirm-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    called = []

    def fake_bulk_publish_ready(*args, **kwargs):
        called.append((args, kwargs))
        return {"items": [], "summary": {"queued": 0}, "markets": ["ebay"], "dry_run": False, "allow_warnings": False, "skip_already_queued": True}

    monkeypatch.setattr(marketplaces_api, "bulk_publish_ready", fake_bulk_publish_ready)

    response = await async_client.post(
        "/marketplaces/publish-ready/bulk",
        json={
            "listing_ids": [listing_id],
            "marketplaces": ["ebay"],
            "dry_run": False,
        },
    )
    assert response.status_code == 400
    assert "QUEUE LIVE EBAY READY LISTINGS" in response.json()["detail"]
    assert called == []


@pytest.mark.anyio
async def test_bulk_publish_ready_dry_run_does_not_require_confirmation(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Marketplace Owner",
            "email": f"market-dryrun-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    called = []

    def fake_bulk_publish_ready(*args, **kwargs):
        called.append((args, kwargs))
        return {
            "items": [],
            "summary": {"queued": 0, "dry_run_ready": 1, "dry_run_blocked": 0},
            "markets": ["ebay"],
            "dry_run": True,
            "allow_warnings": False,
            "skip_already_queued": True,
        }

    monkeypatch.setattr(marketplaces_api, "bulk_publish_ready", fake_bulk_publish_ready)

    response = await async_client.post(
        "/marketplaces/publish-ready/bulk",
        json={
            "listing_ids": [listing_id],
            "marketplaces": ["ebay"],
        },
    )
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert called


@pytest.mark.anyio
async def test_launch_drill_dry_run_is_safe_and_itemized(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Launch Drill Owner",
            "email": f"market-drill-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    def fake_launch_drill(*args, **kwargs):
        return {
            "marketplace": "ebay",
            "generated_at": None,
            "summary": {"checked": 1, "ready": 1, "blocked": 0, "warnings": 0},
            "items": [
                {
                    "listing_id": listing_id,
                    "title": "Launch Drill Item",
                    "status": "ready",
                    "preflight": {"status": "ready"},
                    "payload_preview": {"sku": "posterpro-1-1"},
                    "blockers": [],
                    "warnings": [],
                    "launch_checklist": [{"label": "Preflight ready", "ok": True}],
                    "reason": None,
                }
            ],
        }

    monkeypatch.setattr(marketplaces_api.MarketplacePreflightService, "launch_drill_dry_run", fake_launch_drill)

    response = await async_client.post(
        "/marketplaces/launch-drill/dry-run",
        json={"listing_ids": [listing_id], "marketplace": "ebay", "max_items": 5, "require_ready": True, "include_payload_preview": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["ready"] == 1
    assert payload["items"][0]["status"] == "ready"


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


@pytest.mark.anyio
async def test_sales_dashboard_reports_profit_and_cost_metrics(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Sales Metrics Owner",
            "email": f"sales-metrics-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    db = database_module.SessionLocal()
    try:
        sale = Sale(
            user_id=user_id,
            listing_id=listing_id,
            platform=MarketplaceName.ebay,
            marketplace_order_id="ORDER-1",
            marketplace_listing_id="LISTING-1",
            quantity=1,
            amount=80.0,
            currency="USD",
            fees_actual=12.0,
            shipping_cost=8.0,
            promotional_fees=3.0,
            marketplace_fees=7.0,
            profit=50.0,
            roi_percentage=100.0,
            status="DETECTED",
            details={"source": "unit-test"},
        )
        db.add(sale)
        db.commit()
    finally:
        db.close()

    response = await async_client.get("/sales/dashboard", params={"user_id": user_id, "limit": 50, "search": "Shoes", "sort_by": "profit", "sort_dir": "desc"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total_profit"] == 50.0
    assert payload["sales"][0]["fees_actual"] == 12.0
    assert payload["sales"][0]["marketplace_fees"] == 7.0
    assert payload["sales"][0]["listing_title"] == "Shoes"


@pytest.mark.anyio
async def test_ebay_revision_endpoint_requires_confirmation_and_calls_revision(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "eBay Revision Owner",
            "email": f"ebay-revision-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    db = database_module.SessionLocal()
    try:
        listing = db.get(Listing, listing_id)
        listing.ebay_listing_id = "12345"
        listing.marketplace_data = {"offer": {"offerId": "offer-1"}}
        db.add(listing)
        db.commit()
    finally:
        db.close()

    called = []

    async def fake_revise(listing, db):
        called.append(listing.id)
        return {"status": "UPDATED", "offer_id": "offer-1"}

    monkeypatch.setattr(marketplaces_api, "revise_ebay_listing", fake_revise)

    response = await async_client.post(
        f"/marketplaces/ebay/listings/{listing_id}/sync",
        json={"confirm_live_publish": True, "confirmation_phrase": "QUEUE LIVE EBAY READY LISTINGS"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "UPDATED"
    assert called == [listing_id]


@pytest.mark.anyio
async def test_ebay_inventory_sync_alias_uses_active_listing_sync(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "eBay Sync Owner",
            "email": f"ebay-sync-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201

    called = []

    async def fake_sync(user_id, db, *, limit=100):
        called.append((user_id, limit))
        return {"marketplace": "ebay", "checked": 1, "matched": 1, "updated": 0, "unmatched": 0}

    monkeypatch.setattr(marketplaces_api, "sync_ebay_active_listings", fake_sync)

    response = await async_client.post("/marketplaces/ebay/sync?limit=25")
    assert response.status_code == 200
    assert response.json()["checked"] == 1
    assert called and called[0][1] == 25
