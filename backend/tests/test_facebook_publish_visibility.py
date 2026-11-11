import pytest
from uuid import uuid4
from types import SimpleNamespace


from sqlalchemy import select

from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Cluster, Listing, MarketplaceCrosspostJob, MarketplaceListing, User
from app.services.marketplace_execution import resolve_execution_mode
from app.workers import tasks



@pytest.fixture(autouse=True)
def _assert_isolated_test_database(db_session):
    bind = db_session.get_bind()
    assert bind.dialect.name == "sqlite"
    assert "posterpro-test-" in str(bind.url)


def _create_user_and_listing(db, *, marketplace_data: dict | None = None):
    user = User(email=f"fb-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    cluster = Cluster(user_id=user.id, title_hint="Demo")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    listing = Listing(
        user_id=user.id,
        cluster_id=cluster.id,
        title="Demo listing",
        description="Demo",
        listing_price=20.0,
        image_urls=["https://images.example.test/demo.jpg"],
        marketplace_data=marketplace_data or {"channels": {"facebook": {"publish_mode": "manual_or_provider"}}},
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    user_id = user.id
    listing_id = listing.id
    return user_id, listing_id


def test_facebook_legacy_publish_mode_resolves_to_browser_assist(db_session):
    db = db_session
    user = User(email=f"fb-resolve-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    cluster = Cluster(user_id=user.id, title_hint="Demo")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    listing = Listing(
        user_id=user.id,
        cluster_id=cluster.id,
        title="Demo listing",
        description="Demo",
        marketplace_data={"channels": {"facebook": {"publish_mode": "manual_or_provider"}}},
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    assert resolve_execution_mode(listing=listing, user=user, marketplace="facebook") == "browser_assist"


def test_facebook_browser_crosspost_with_visible_listing_marks_published(monkeypatch, db_session):
    user_id, listing_id = _create_user_and_listing(db_session,
        marketplace_data={"channels": {"facebook": {"publish_mode": "manual_or_provider"}}}
    )
    db = db_session
    job = MarketplaceCrosspostJob(
        user_id=user_id,
        listing_id=listing_id,
        target_marketplaces=["facebook"],
        requested_mode="approved",
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    monkeypatch.setattr(
        tasks,
        "execute_secondary_marketplace_path",
        lambda **_kwargs: {
            "status": "BROWSER_AUTOMATION_READY",
            "submitted": True,
            "marketplace_listing_id": "1234567890",
            "listing_urls": ["https://www.facebook.com/marketplace/item/1234567890"],
            "bridge_submission": {"bridge_response": {"job_id": "bridge-1"}},
        },
    )
    monkeypatch.setattr(tasks, "resolve_execution_mode", lambda **_kwargs: "hosted_browser_assist")
    monkeypatch.setattr(
        tasks,
        "wait_for_bridge_job",
        lambda **_kwargs: {
            "status": "completed",
            "result": {
                "status": "submitted_to_marketplace",
                "submitted": True,
                "marketplace_listing_id": "1234567890",
                "listing_urls": ["https://www.facebook.com/marketplace/item/1234567890"],
            },
        },
    )
    monkeypatch.setattr(tasks.poll_hosted_bridge_publish_task, "apply_async", lambda **_kwargs: None)
    monkeypatch.setattr(
        tasks,
        "get_bridge_job",
        lambda _job_id: {
            "status": "completed",
            "result": {
                "status": "submitted_to_marketplace",
                "submitted": True,
                "marketplace_listing_id": "1234567890",
                "listing_urls": ["https://www.facebook.com/marketplace/item/1234567890"],
            },
        },
    )
    monkeypatch.setattr(
        tasks.MarketplacePreflightService,
        "preflight_listing",
        lambda self, db, listing, marketplace: {  # noqa: ARG005
            "status": "ready",
            "blockers": [],
            "warnings": [],
            "missing_fields": [],
            "invalid_fields": [],
            "required_operator_actions": [],
        },
    )

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)
    assert result["status"] == "running", result
    # Exercise the poller against the durable representation it consumes.
    # The dispatch worker's separate extension/hosted-bridge selection is not
    # part of this poll-state test.
    job.result_summary = {"results": [{
        "marketplace": "facebook",
        "execution_mode": "hosted_browser_assist",
        "status": "browser_automation_pending",
        "response": {"bridge_job_id": "bridge-1", "bridge_poll_status": "pending"},
    }]}
    job.status = "running"
    db.commit()
    poll = tasks.poll_hosted_bridge_publish_task.run(job.id, listing_id, "facebook", "bridge-1", "hosted_browser_assist")
    assert poll["status"] == "submitted_to_marketplace", poll

    db = db_session
    row = db.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.listing_id == listing_id,
            MarketplaceListing.marketplace == MarketplaceName.facebook,
        )
    ).scalar_one()
    assert row.status == MarketplaceListingStatus.PUBLISHED
    assert row.marketplace_listing_id == "1234567890"


def test_blocked_facebook_does_not_prevent_ebay_in_same_queued_job(monkeypatch, db_session):
    user_id, listing_id = _create_user_and_listing(db_session,
        marketplace_data={"targets": ["ebay", "facebook"], "channels": {"facebook": {"publish_mode": "manual_or_provider"}}}
    )
    db = db_session
    listing = db.get(Listing, listing_id)
    listing.status = ListingStatus.ready
    job = MarketplaceCrosspostJob(
        user_id=user_id,
        listing_id=listing_id,
        target_marketplaces=["ebay", "facebook"],
        requested_mode="operator_approved_live_queue",
        requested_by=user_id,
        status="queued",
        execution_plan={
            "operator_live_confirmed": True,
            "approved_by_user_id": user_id,
            "preflight_by_marketplace": {"ebay": {"marketplace": "ebay", "status": "ready", "blockers": [], "warnings": [], "execution_contract": {"publisher_path": "INVENTORY_API"}}},
            "approved_execution_payload_by_marketplace": {"ebay": {"title": "Demo listing"}},
            "preflight_payload_hash_by_marketplace": {"ebay": tasks.compute_payload_hash({"title": "Demo listing"})},
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    def mixed_preflight(_self, _db, listing, marketplace):
        if marketplace == "ebay":
            return {"listing_id": listing.id, "marketplace": marketplace, "status": "ready", "blockers": [], "warnings": []}
        return {
            "listing_id": listing.id,
            "marketplace": marketplace,
            "status": "blocked",
            "blockers": [{"code": "FACEBOOK_LIVE_PUBLISH_UNVERIFIED", "message": "Facebook is not connected", "retryable": True}],
            "warnings": [],
        }

    monkeypatch.setattr(tasks.MarketplacePreflightService, "preflight_listing", mixed_preflight)
    monkeypatch.setattr(tasks, "resolve_execution_mode", lambda **_kwargs: "direct_api")
    monkeypatch.setattr(tasks, "build_marketplace_payload", lambda *_args, **_kwargs: {"title": "Demo listing"})
    monkeypatch.setattr(tasks, "_is_already_published_to_marketplace", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        tasks.multi_platform_publisher,
        "publish",
        lambda _db, _listing, marketplace, **_kwargs: SimpleNamespace(
            marketplace=marketplace,
            status=MarketplaceListingStatus.PUBLISHED,
            response={"listing_id": "ebay-123"},
        ),
    )

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    assert {row["marketplace"]: row["status"] for row in result["results"]} == {
        "ebay": "PUBLISHED",
        "facebook": "blocked",
    }
    db = db_session
    ebay_row = db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing_id, MarketplaceListing.marketplace == MarketplaceName.ebay)).scalar_one()
    facebook_row = db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing_id, MarketplaceListing.marketplace == MarketplaceName.facebook)).scalar_one()
    assert ebay_row.status == MarketplaceListingStatus.PUBLISHED
    assert facebook_row.status == MarketplaceListingStatus.FAILED


def _create_pending_bridge_failure_job(db, *, user_id: int, listing_id: int, bridge_id: str) -> int:
    job = MarketplaceCrosspostJob(
        user_id=user_id,
        listing_id=listing_id,
        target_marketplaces=["facebook"],
        requested_mode="approved",
        status="running",
        result_summary={"results": [{
            "marketplace": "facebook",
            "status": "browser_automation_pending",
            "response": {"bridge_job_id": bridge_id, "bridge_poll_status": "pending"},
        }]},
    )
    db.add(job)
    db.commit()
    job_id = job.id
    return job_id


def test_failed_bridge_without_explicit_no_submit_evidence_requires_reconciliation(monkeypatch, db_session):
    user_id, listing_id = _create_user_and_listing(db_session)
    job_id = _create_pending_bridge_failure_job(db_session, user_id=user_id, listing_id=listing_id, bridge_id="bridge-ambiguous")
    monkeypatch.setattr(tasks, "get_bridge_job", lambda _bridge_id: {"status": "failed", "error": "browser timed out"})

    result = tasks.poll_hosted_bridge_publish_task.run(job_id, listing_id, "facebook", "bridge-ambiguous", "hosted_browser_assist")

    db = db_session
    job = db.get(MarketplaceCrosspostJob, job_id)
    row = db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing_id, MarketplaceListing.marketplace == MarketplaceName.facebook)).scalar_one()
    assert result["status"] == "REMOTE_STATE_UNKNOWN"
    assert job.result_summary["results"][0]["status"] == "REMOTE_STATE_UNKNOWN"
    assert job.result_summary["results"][0]["response"]["remote_outcome"] == "REMOTE_STATE_UNKNOWN"
    assert job.result_summary["results"][0]["response"]["bridge_poll_status"] == "reconciliation_required"
    assert row.status == MarketplaceListingStatus.PENDING


def test_bridge_failure_with_explicit_pre_submit_evidence_is_definitely_not_published(monkeypatch, db_session):
    user_id, listing_id = _create_user_and_listing(db_session)
    job_id = _create_pending_bridge_failure_job(db_session, user_id=user_id, listing_id=listing_id, bridge_id="bridge-no-submit")
    error = 'Could not find a publish action | context={"submission_state":"NOT_SUBMITTED","submission_performed":false,"stage":"pre_submit_action_missing"}'
    monkeypatch.setattr(tasks, "get_bridge_job", lambda _bridge_id: {"status": "failed", "error": error})

    result = tasks.poll_hosted_bridge_publish_task.run(job_id, listing_id, "facebook", "bridge-no-submit", "hosted_browser_assist")

    db = db_session
    job = db.get(MarketplaceCrosspostJob, job_id)
    row = db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing_id, MarketplaceListing.marketplace == MarketplaceName.facebook)).scalar_one()
    response = job.result_summary["results"][0]["response"]
    assert result["status"] == "failed"
    assert job.result_summary["results"][0]["status"] == "failed"
    assert response["submission_performed"] is False
    assert response["remote_outcome"] == "NOT_PUBLISHED"
    assert row.status == MarketplaceListingStatus.FAILED
