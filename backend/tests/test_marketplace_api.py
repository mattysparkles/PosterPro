from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core import database as database_module
from app.models.enums import EbayPublishStatus, ListingStatus, MarketplaceName
from app.models.models import Cluster, IntakePhoto, Listing, Sale
from app.api import routes as listings_routes
from app.api import marketplaces as marketplaces_api
from app.services.listing_specificity import classify_listing_reviewability, is_bare_identifier_title, is_caption_like_title
from app.workers import tasks
from app.models.models import ListingCorrectionJob


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


def seed_bucket_listing(user_id: int, **kwargs) -> int:
    db = database_module.SessionLocal()
    listing = Listing(user_id=user_id, **kwargs)
    db.add(listing)
    db.commit()
    db.refresh(listing)
    db.close()
    return listing.id


@pytest.mark.anyio
async def test_need_a_correction_api_creates_authenticated_durable_job(async_client, monkeypatch):
    register = await async_client.post("/auth/register", json={"full_name": "Correction Owner", "email": f"correction-{uuid4()}@example.com", "password": "supersecret123"})
    assert register.status_code == 201
    listing_id = seed_bucket_listing(register.json()["user"]["id"], status=ListingStatus.draft, title="Generic Product", description="Condition: New.", source_type="amazon_vine", source_metadata={"amazon_evidence": {"asin": "B000TEST", "title": "160-in-1 Electronic Learning Kit", "brand": "Example"}})
    monkeypatch.setattr(tasks.process_listing_correction_jobs_task, "delay", lambda **_kwargs: None)
    response = await async_client.post(f"/listings/{listing_id}/request-revision", json={"fields": ["title", "description", "category"], "note": "Use the actual source evidence. The title and description are too generic and the eBay category is incorrect.", "priority": 0})
    assert response.status_code == 200
    db = database_module.SessionLocal()
    job = db.query(ListingCorrectionJob).filter(ListingCorrectionJob.listing_id == listing_id).one()
    assert job.priority == 0
    assert job.fields == ["title", "description", "category"]
    assert "actual source evidence" in job.operator_note
    assert response.json()["source_metadata"]["correction_job_id"] == job.id
    assert response.json()["source_metadata"]["correction_status"] in {"QUEUED", "QUEUED - DRAFTING PAUSED"}
    db.close()


@pytest.mark.anyio
async def test_timeline_manual_classification_round_trip(async_client):
    register = await async_client.post("/auth/register", json={"full_name": "Timeline Owner", "email": f"timeline-{uuid4()}@example.com", "password": "supersecret123"})
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    db = database_module.SessionLocal()
    photo = IntakePhoto(user_id=user_id, source_provider="test", source_photo_id=f"photo-{uuid4()}", local_path="/tmp/photo.jpg", image_type="photo", is_slate=False, metadata_json={})
    db.add(photo); db.commit(); db.refresh(photo); photo_id = photo.id; db.close()
    marked = await async_client.post("/intake/timeline/classify", json={"photo_ids": [photo_id], "classification": "SLATE"})
    assert marked.status_code == 200 and marked.json()["updated"] == 1
    db = database_module.SessionLocal(); row = db.get(IntakePhoto, photo_id)
    assert row.is_slate is True and row.image_type == "slate" and row.metadata_json["classification_source"] == "MANUAL_OPERATOR"; db.close()
    reset = await async_client.post("/intake/timeline/classify", json={"photo_ids": [photo_id], "classification": "PHOTO"})
    assert reset.status_code == 200
    db = database_module.SessionLocal(); row = db.get(IntakePhoto, photo_id)
    assert row.is_slate is False and row.image_type == "photo" and row.metadata_json["classification"] == "PHOTO"; db.close()


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
async def test_listing_queue_filters_match_operator_ready_contract(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Queue Contract Owner",
            "email": f"queue-contract-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    review_ready_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Review Ready Item",
        description="Complete draft awaiting manual approval.",
        listing_price=19.99,
        condition="New",
        category_id="123",
        needs_review=True,
        source_type="amazon_vine",
        marketplace_data={
            "quality_summary": {"ready_for_publish_queue": True},
            "targets": ["ebay"],
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "ready"}}},
        },
        source_metadata={},
    )
    recovery_attention_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.draft,
        title="Recovered Inventory Needs Attention",
        description="Still being worked on automatically.",
        listing_price=29.99,
        condition="Needs review",
        category_id="456",
        needs_review=False,
        source_type="media_inventory_recovery",
        processing_state="needs_attention",
        processing_stage="quality_gate",
        processing_blocking_reason="needs_grouping_review",
        marketplace_data={
            "quality_summary": {"ready_for_publish_queue": False},
            "targets": ["ebay"],
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "blocked"}}},
        },
        source_metadata={},
    )
    ready_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Approved Ready Item",
        description="Manually approved and waiting for publish.",
        listing_price=39.99,
        condition="New",
        category_id="789",
        needs_review=False,
        source_type="amazon_vine",
        marketplace_data={
            "quality_summary": {"ready_for_publish_queue": True},
            "targets": ["ebay"],
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "ready"}}},
        },
        source_metadata={"operator_approved_at": "2026-08-12T12:00:00Z"},
    )
    blocked_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.draft,
        title="Blocked Draft Item",
        description="Missing key facts and still in progress.",
        listing_price=15.99,
        condition="Needs review",
        category_id="555",
        needs_review=False,
        source_type="amazon_vine",
        marketplace_data={
            "quality_summary": {"ready_for_publish_queue": False},
            "targets": ["ebay"],
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "blocked"}}},
        },
        source_metadata={},
    )
    sold_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Sold Item",
        description="This item has been sold and should only appear in Sold.",
        listing_price=49.99,
        condition="Used",
        category_id="999",
        needs_review=False,
        source_type="amazon_vine",
        sold_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        quantity=0,
        custom_labels=["sold"],
        marketplace_data={
            "quality_summary": {"ready_for_publish_queue": False},
            "targets": ["ebay"],
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "published"}}},
        },
        source_metadata={"operator_approved_at": "2026-08-12T12:00:00Z"},
    )
    archived_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Archived Item",
        description="This item is archived and should only appear in Archived.",
        listing_price=59.99,
        condition="Used",
        category_id="998",
        needs_review=False,
        source_type="amazon_vine",
        custom_labels=["archived_vine"],
        marketplace_data={
            "quality_summary": {"ready_for_publish_queue": False},
            "targets": ["ebay"],
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "ready"}}},
        },
        source_metadata={"operator_approved_at": "2026-08-12T12:00:00Z"},
    )

    review_resp = await async_client.get("/listings?queue=review&page_size=50")
    assert review_resp.status_code == 200
    review_payload = review_resp.json()
    review_ids = {row["id"] for row in review_payload["items"]}
    assert review_ids == {review_ready_id}
    assert review_payload["total"] == 1

    attention_resp = await async_client.get("/listings?queue=attention&page_size=50")
    assert attention_resp.status_code == 200
    attention_payload = attention_resp.json()
    attention_ids = {row["id"] for row in attention_payload["items"]}
    assert recovery_attention_id in attention_ids
    assert attention_payload["total"] >= 1

    ready_resp = await async_client.get("/listings?queue=ready&page_size=50")
    assert ready_resp.status_code == 200
    ready_payload = ready_resp.json()
    ready_ids = {row["id"] for row in ready_payload["items"]}
    assert ready_ids == {ready_id}
    assert ready_payload["total"] == 1

    drafts_resp = await async_client.get("/listings?queue=drafts&page_size=50")
    assert drafts_resp.status_code == 200
    drafts_payload = drafts_resp.json()
    draft_ids = {row["id"] for row in drafts_payload["items"]}
    assert blocked_id in draft_ids
    assert recovery_attention_id not in draft_ids

    all_resp = await async_client.get("/listings?page_size=50")
    assert all_resp.status_code == 200
    all_payload = all_resp.json()
    all_ids = {row["id"] for row in all_payload["items"]}
    assert sold_id not in all_ids
    assert archived_id not in all_ids

    vine_resp = await async_client.get("/listings?queue=vine&page_size=50")
    assert vine_resp.status_code == 200
    vine_payload = vine_resp.json()
    vine_ids = {row["id"] for row in vine_payload["items"]}
    assert sold_id not in vine_ids
    assert archived_id not in vine_ids

    published_resp = await async_client.get("/listings?queue=published&page_size=50")
    assert published_resp.status_code == 200
    published_payload = published_resp.json()
    published_ids = {row["id"] for row in published_payload["items"]}
    assert sold_id not in published_ids
    assert archived_id not in published_ids

    sold_resp = await async_client.get("/listings?queue=sold&page_size=50")
    assert sold_resp.status_code == 200
    sold_payload = sold_resp.json()
    sold_ids = {row["id"] for row in sold_payload["items"]}
    assert sold_ids == {sold_id}
    assert sold_payload["total"] == 1

    archived_resp = await async_client.get("/listings?queue=archived&page_size=50")
    assert archived_resp.status_code == 200
    archived_payload = archived_resp.json()
    archived_ids = {row["id"] for row in archived_payload["items"]}
    assert archived_ids == {archived_id}
    assert archived_payload["total"] == 1


@pytest.mark.anyio
async def test_listing_queue_hides_generic_caption_rows_and_merged_child_projections(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Generic Queue Owner",
            "email": f"generic-queue-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    generic_review_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Circuit Board",
        description="Bare caption only.",
        listing_price=19.99,
        condition="Needs review",
        category_id="",
        needs_review=True,
        source_type="media_inventory_recovery",
        processing_state="complete",
        processing_stage="quality_gate",
        source_metadata={"recovery": {"item_id": "REC-1"}},
    )
    merged_child_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Merged child listing",
        description="Should not appear as a standalone item.",
        listing_price=29.99,
        condition="Needs review",
        category_id="",
        needs_review=False,
        source_type="media_inventory_recovery",
        processing_state="complete",
        processing_stage="quality_gate",
        source_metadata={
            "recovery": {
                "item_id": "REC-2",
                "merged_into_recovery_item_id": "REC-1",
                "merged_into_recovery_group_id": 99,
            }
        },
    )

    review_resp = await async_client.get("/listings?queue=review&page_size=50")
    assert review_resp.status_code == 200
    review_payload = review_resp.json()
    review_ids = {row["id"] for row in review_payload["items"]}
    assert generic_review_id not in review_ids

    all_resp = await async_client.get("/listings?page_size=50")
    assert all_resp.status_code == 200
    all_payload = all_resp.json()
    all_ids = {row["id"] for row in all_payload["items"]}
    assert merged_child_id not in all_ids


def test_reviewability_classifier_is_consistent_across_example_titles():
    examples = [
        (
            "Circuit Board",
            "weak",
            {},
            {},
        ),
        (
            "Whirlpool W11478526 Washer Main Control Board OEM Replacement Part",
            "pass",
            {"Brand": "Whirlpool", "MPN": "W11478526", "Type": "Washer Main Control Board"},
            {},
        ),
        (
            "35541-10020",
            "weak",
            {},
            {},
        ),
        (
            "Volvo 106 213 G Pipe",
            "pass",
            {"Brand": "Volvo", "MPN": "106 213 G", "Type": "Pipe"},
            {"recovery": {"identity": {"title": "Volvo Pipe", "brand": "Volvo", "mpn": "106 213 G"}}},
        ),
        (
            "Product Packaging",
            "weak",
            {},
            {},
        ),
    ]
    for title, expected, item_specifics, source_metadata in examples:
        result = classify_listing_reviewability(title=title, description=None, category=None, item_specifics=item_specifics, source_metadata=source_metadata, has_images=True)
        assert result["reviewability_class"] == expected
        assert is_caption_like_title(title) == (expected == "weak" and title.lower() in {"circuit board", "product packaging"})
        assert is_bare_identifier_title(title) == (title == "35541-10020")
        if expected == "pass":
            assert result["is_specific_sellable_identity"] is True
        dummy = Listing(
            title=title,
            description="",
            source_type="media_inventory_recovery",
            category_suggestion="",
            item_specifics=item_specifics,
            source_metadata=source_metadata,
            image_urls=["/media/example.jpg"],
            status=ListingStatus.ready,
            needs_review=True,
            processing_state="complete",
        )
        bucket = listings_routes._listing_bucket(dummy)
        assert bucket == ("needs_attention" if expected == "weak" else "review")


@pytest.mark.anyio
async def test_listing_source_and_queue_filters_compose(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Composed Filter Owner",
            "email": f"composed-filter-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    vine_review_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.draft,
        title="Vine Review Item",
        description="Amazon Vine item awaiting review.",
        listing_price=19.99,
        condition="New",
        category_id="123",
        needs_review=True,
        source_type="amazon_vine",
        marketplace_data={
            "quality_summary": {"ready_for_publish_queue": True},
            "targets": ["ebay"],
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "ready"}}},
        },
    )
    vine_published_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Vine Published Item",
        description="Amazon Vine item already published.",
        listing_price=29.99,
        condition="New",
        category_id="124",
        needs_review=False,
        source_type="amazon_vine",
        ebay_publish_status=EbayPublishStatus.POSTED,
        ebay_listing_id="EBAY12345",
        marketplace_data={
            "quality_summary": {"ready_for_publish_queue": True},
            "targets": ["ebay"],
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "published"}}},
        },
    )
    photo_review_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.draft,
        title="Photo Review Item",
        description="Google Photos item awaiting review.",
        listing_price=9.99,
        condition="Used",
        category_id="125",
        needs_review=True,
        source_type="google_photos_album",
        marketplace_data={
            "quality_summary": {"ready_for_publish_queue": True},
            "targets": ["ebay"],
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "ready"}}},
        },
    )

    vine_review_resp = await async_client.get("/listings?queue=review&source_type=amazon_vine&page_size=50")
    assert vine_review_resp.status_code == 200
    vine_review_payload = vine_review_resp.json()
    vine_review_ids = {row["id"] for row in vine_review_payload["items"]}
    assert vine_review_ids == {vine_review_id}

    vine_published_resp = await async_client.get("/listings?queue=published&source_type=amazon_vine&page_size=50")
    assert vine_published_resp.status_code == 200
    vine_published_payload = vine_published_resp.json()
    vine_published_ids = {row["id"] for row in vine_published_payload["items"]}
    assert vine_published_ids == {vine_published_id}

    photo_review_resp = await async_client.get("/listings?queue=review&source_type=google_photos_album&page_size=50")
    assert photo_review_resp.status_code == 200
    photo_review_payload = photo_review_resp.json()
    photo_review_ids = {row["id"] for row in photo_review_payload["items"]}
    assert photo_review_ids == {photo_review_id}


@pytest.mark.anyio
async def test_published_ebay_listing_queues_facebook_only(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Marketplace Owner",
            "email": f"market-fb-crosspost-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]
    listing_id = seed_listing(user_id)

    class DummyTask:
        id = "task-facebook-only"

    monkeypatch.setattr(tasks.publish_listing_to_marketplace_task, "delay", lambda *_args, **_kwargs: DummyTask())
    monkeypatch.setattr(marketplaces_api, "queue_publish", lambda *_args, **_kwargs: [
        {"marketplace": "ebay", "status": "SKIPPED_ALREADY_PUBLISHED", "task_id": None},
        {"marketplace": "facebook", "status": "QUEUED", "task_id": "task-facebook-only"},
    ])

    response = await async_client.post(
        f"/listings/{listing_id}/publish",
        json={"marketplaces": ["ebay", "facebook"], "confirm_live_publish": True, "confirmation_phrase": "QUEUE LIVE EBAY READY LISTINGS"},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert any(item["marketplace"] == "ebay" and item["status"] == "SKIPPED_ALREADY_PUBLISHED" for item in results)
    assert any(item["marketplace"] == "facebook" and item["status"] == "QUEUED" for item in results)


@pytest.mark.anyio
async def test_marketplace_jobs_overview_includes_system_status_snapshot(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Status Snapshot Owner",
            "email": f"status-snapshot-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    seed_bucket_listing(
        user_id,
        status=ListingStatus.draft,
        title="Draft Item",
        description="A draft item.",
        listing_price=11.0,
        category_id="100",
        source_type="amazon_vine",
    )
    seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Ready Item",
        description="A ready item.",
        listing_price=12.0,
        category_id="101",
        source_type="amazon_vine",
        source_metadata={"operator_approved_at": "2026-08-12T12:00:00Z"},
    )
    seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Sold Item",
        description="A sold item.",
        listing_price=13.0,
        category_id="102",
        source_type="amazon_vine",
        sold_at=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc),
        quantity=0,
        custom_labels=["sold"],
    )
    seed_bucket_listing(
        user_id,
        status=ListingStatus.ready,
        title="Archived Item",
        description="An archived item.",
        listing_price=14.0,
        category_id="103",
        source_type="amazon_vine",
        custom_labels=["archived_vine"],
    )

    response = await async_client.get("/marketplace-jobs/overview?compact=true&limit=5")
    assert response.status_code == 200
    payload = response.json()
    status = payload["system_status"]
    assert status["catalog_total"] == 4
    assert status["catalog_sold"] == 1
    assert status["catalog_archived"] == 1
    assert status["catalog_visible"] == 2
    assert "status_message" in status


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
                    "payload_preview": {"sku": "posterprou1l1"},
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

    def fake_preflight(self, _db, listing, marketplace):
        return {
            "listing_id": listing.id,
            "marketplace": marketplace,
            "status": "ready",
            "blockers": [],
            "warnings": [],
        }

    class DummyTask:
        id = "approve-task-123"

    monkeypatch.setattr(marketplaces_api.MarketplacePreflightService, "preflight_listing", fake_preflight)
    monkeypatch.setattr(tasks.publish_listing_to_marketplace_task, "delay", lambda *_args, **_kwargs: DummyTask())

    response = await async_client.post(f"/listings/{listing_id}/approve")
    assert response.status_code == 200
    payload = response.json()
    assert payload["auto_publish_after_approval"] is True
    assert payload["approval_publishable"] is True
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

    def fake_preflight(self, _db, listing, marketplace):
        return {
            "listing_id": listing.id,
            "marketplace": marketplace,
            "status": "ready",
            "blockers": [],
            "warnings": [],
        }

    class DummyTask:
        id = "approve-bulk-task-123"

    monkeypatch.setattr(marketplaces_api.MarketplacePreflightService, "preflight_listing", fake_preflight)
    monkeypatch.setattr(tasks.publish_listing_to_marketplace_task, "delay", lambda *_args, **_kwargs: DummyTask())

    response = await async_client.post("/listings/approve-bulk", json={"listing_ids": [listing_a, listing_b]})
    assert response.status_code == 200
    payload = response.json()
    approvals = payload["approvals"]
    assert len(approvals) == 2
    for approval in approvals:
        assert approval["auto_publish_after_approval"] is False
        assert approval["approval_publishable"] is True
        assert approval["results"] == []
        assert approval["listing"]["needs_review"] is False
        assert approval["listing"]["status"] == "ready"


@pytest.mark.anyio
async def test_listing_approve_keeps_blocked_items_in_draft(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Blocked Approve Owner",
            "email": f"approve-blocked-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    listing_id = seed_listing(user_id)
    await async_client.patch(
        f"/listings/{listing_id}",
        json={"needs_review": True, "status": "draft", "marketplace_data": {"targets": ["ebay"]}},
    )

    def fake_preflight(self, _db, listing, marketplace):
        return {
            "listing_id": listing.id,
            "marketplace": marketplace,
            "status": "blocked",
            "blockers": [{"code": "EBAY_REQUIRED_ASPECT_MISSING", "message": "Type is required"}],
            "warnings": [],
        }

    monkeypatch.setattr(marketplaces_api.MarketplacePreflightService, "preflight_listing", fake_preflight)

    response = await async_client.post(f"/listings/{listing_id}/approve")
    assert response.status_code == 200
    payload = response.json()
    assert payload["approval_publishable"] is False
    assert payload["listing"]["status"] == "draft"
    assert payload["listing"]["needs_review"] is False
    assert payload["results"] == []


@pytest.mark.anyio
async def test_approve_and_queue_uses_crosspost_job_without_synchronous_preflight(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Queue Owner",
            "email": f"approve-queue-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    listing_id = seed_listing(user_id)
    await async_client.patch(
        f"/listings/{listing_id}",
        json={"needs_review": True, "status": "draft", "marketplace_data": {"targets": ["ebay", "facebook"]}},
    )

    def fail_preflight(*_args, **_kwargs):
        raise AssertionError("preflight should not run on the request thread")

    monkeypatch.setattr(listings_routes.MarketplacePreflightService, "preflight_listing", fail_preflight)
    monkeypatch.setattr(listings_routes, "enqueue_crosspost_job", lambda *_args, **_kwargs: {"status": "queued", "job_id": 77, "task_id": "crosspost-task-77"})

    response = await async_client.post(
        "/listings/approve-and-queue",
        json={
            "listing_ids": [listing_id],
            "marketplaces": ["ebay", "facebook"],
            "confirm_live_publish": True,
            "confirmation_phrase": "QUEUE LIVE EBAY READY LISTINGS",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["status"] == "queued"
    assert payload["results"][0]["job_id"] == 77


@pytest.mark.anyio
async def test_ebay_history_sync_endpoint_exists(async_client, monkeypatch):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "History Sync Owner",
            "email": f"ebay-history-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201

    async def fake_history(*_args, **_kwargs):
        return {"checked": 2, "matched": 1, "created": 1, "updated": 1, "unmatched": 0}

    monkeypatch.setattr(marketplaces_api, "sync_ebay_fulfillment_history", fake_history)

    response = await async_client.post("/marketplaces/ebay/sync/history", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["checked"] == 2


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


@pytest.mark.anyio
async def test_listings_and_sales_dashboard_default_to_authenticated_tenant(async_client):
    register_one = await async_client.post(
        "/auth/register",
        json={
            "full_name": "All Rows Owner",
            "email": f"all-rows-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register_one.status_code == 201
    first_user_id = register_one.json()["user"]["id"]
    first_listing_id = seed_listing(first_user_id)

    register_two = await async_client.post(
        "/auth/register",
        json={
            "full_name": "All Rows Owner Two",
            "email": f"all-rows-two-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register_two.status_code == 201
    second_user_id = register_two.json()["user"]["id"]
    second_listing_id = seed_listing(second_user_id)

    db = database_module.SessionLocal()
    try:
        first_sale = Sale(
            user_id=first_user_id,
            listing_id=first_listing_id,
            platform=MarketplaceName.ebay,
            marketplace_order_id="ORDER-ALL-1",
            marketplace_listing_id="LISTING-ALL-1",
            quantity=1,
            amount=45.0,
            currency="USD",
            fees_actual=4.0,
            shipping_cost=3.0,
            promotional_fees=1.0,
            marketplace_fees=5.0,
            profit=32.0,
            roi_percentage=71.0,
            status="DETECTED",
            details={"source": "unit-test"},
        )
        second_sale = Sale(
            user_id=second_user_id,
            listing_id=second_listing_id,
            platform=MarketplaceName.ebay,
            marketplace_order_id="ORDER-ALL-2",
            marketplace_listing_id="LISTING-ALL-2",
            quantity=1,
            amount=55.0,
            currency="USD",
            fees_actual=5.0,
            shipping_cost=4.0,
            promotional_fees=1.0,
            marketplace_fees=6.0,
            profit=39.0,
            roi_percentage=70.0,
            status="DETECTED",
            details={"source": "unit-test"},
        )
        db.add_all([first_sale, second_sale])
        db.commit()
    finally:
        db.close()

    listings_resp = await async_client.get("/listings", params={"page": 1, "page_size": 50, "queue": "all"})
    assert listings_resp.status_code == 200
    listings_payload = listings_resp.json()
    assert listings_payload["total"] == 1
    titles = {item["title"] for item in listings_payload["items"]}
    assert "Shoes" in titles
    assert second_listing_id in {item["id"] for item in listings_payload["items"]}
    assert first_listing_id not in {item["id"] for item in listings_payload["items"]}

    sales_resp = await async_client.get("/sales/dashboard", params={"limit": 50})
    assert sales_resp.status_code == 200
    sales_payload = sales_resp.json()
    assert sales_payload["summary"]["total_sales"] == 1
    sale_listing_ids = {sale["listing_id"] for sale in sales_payload["sales"]}
    assert sale_listing_ids == {second_listing_id}


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


@pytest.mark.anyio
async def test_public_storefront_lists_only_published_items(async_client):
    register = await async_client.post(
        "/auth/register",
        json={
            "full_name": "Storefront Owner",
            "email": f"storefront-{uuid4()}@example.com",
            "password": "supersecret123",
        },
    )
    assert register.status_code == 201
    user_id = register.json()["user"]["id"]

    published_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.PUBLISHED,
        title="Live Storefront Item",
        description="This item is publicly published.",
        listing_price=24.99,
        image_urls=["/media/storefront/live-item.jpg"],
        listing_images=[
            {
                "storage_path": "/media/storefront/live-item.jpg",
                "role": "primary",
                "operator_state": "approved",
                "is_reference": False,
                "display_order": 0,
            }
        ],
        ebay_listing_id="1234567890",
        ebay_publish_status=EbayPublishStatus.POSTED,
        source_type="amazon_vine",
    )
    draft_id = seed_bucket_listing(
        user_id,
        status=ListingStatus.draft,
        title="Hidden Draft Item",
        description="This draft should not appear on the storefront.",
        listing_price=19.99,
        source_type="amazon_vine",
    )

    response = await async_client.get("/public/storefront/listings?page_size=50")
    assert response.status_code == 200
    payload = response.json()
    item_ids = {row["id"] for row in payload["items"]}
    assert published_id in item_ids
    assert draft_id not in item_ids
    storefront_item = next(row for row in payload["items"] if row["id"] == published_id)
    assert storefront_item["title"] == "Live Storefront Item"
    assert storefront_item["price"] == 24.99
    assert storefront_item["thumbnail_url"]
    assert "ebay" in storefront_item["marketplaces"]
