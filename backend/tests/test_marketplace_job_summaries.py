from app.api.marketplace_jobs import _serialize_crosspost_job, _serialize_import_job
from app.models.enums import ListingStatus, MarketplaceListingStatus
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceImportJob, MarketplaceListing, User
from app.workers import tasks


def test_crosspost_job_waits_for_bridge_completion_and_exposes_review_summary(db_session, monkeypatch):
    user = User(
        email="crosspost-review@example.com",
        settings_json={
            "marketplace_connections": {
                "mercari": {
                    "display_name": "Mercari Browser",
                    "account_handle": "mercari-browser",
                    "workflow_state": "ready",
                    "publish_mode": "browser_assist",
                }
            }
        },
    )
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Vintage receiver",
        description="Fully tested stereo receiver",
        listing_price=249.0,
        quantity=1,
        marketplace_data={"targets": ["mercari"]},
    )
    db_session.add(listing)
    db_session.flush()

    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["mercari"],
        requested_mode="auto",
        status="queued",
        execution_plan={
            "targets": [
                {
                    "marketplace": "mercari",
                    "execution_mode": "browser_assist",
                }
            ]
        },
    )
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(
        tasks,
        "execute_secondary_marketplace_path",
        lambda **_kwargs: {
            "status": "BROWSER_AUTOMATION_READY",
            "bridge_submission": {
                "status": "SUBMITTED_TO_BRIDGE",
                "bridge_response": {"job_id": "bridge-crosspost-1"},
            },
        },
    )
    monkeypatch.setattr(
        tasks,
        "wait_for_bridge_job",
        lambda **_kwargs: {
            "status": "completed",
            "result": {"status": "draft_form_filled"},
        },
    )

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    assert result["status"] == "completed"
    assert result["results"][0]["status"] == "draft_form_filled"

    db_session.refresh(job)
    serialized = _serialize_crosspost_job(job)
    assert serialized["review_required_count"] == 1
    assert serialized["submitted_count"] == 0
    assert serialized["failed_target_count"] == 0
    assert serialized["target_outcomes"][0]["requires_review"] is True

    marketplace_listing = (
        db_session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.listing_id == listing.id,
            MarketplaceListing.marketplace == "mercari",
        )
        .one()
    )
    assert marketplace_listing.status == MarketplaceListingStatus.PENDING
    assert marketplace_listing.raw_response["bridge_completion"]["result"]["status"] == "draft_form_filled"


def test_import_job_serialization_includes_review_items(db_session):
    user = User(email="import-review@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Needs title cleanup",
        needs_review=True,
    )
    db_session.add(listing)
    db_session.flush()

    job = MarketplaceImportJob(
        user_id=user.id,
        source_marketplace="facebook",
        import_mode="browser_assist",
        status="completed",
        normalized_preview={"created_listing_ids": [listing.id]},
        created_listing_id=listing.id,
    )
    db_session.add(job)
    db_session.commit()

    serialized = _serialize_import_job(job, db=db_session)

    assert serialized["review_required_count"] == 1
    assert serialized["review_items"] == [
        {
            "listing_id": listing.id,
            "title": "Needs title cleanup",
            "status": "draft",
            "needs_review": True,
        }
    ]


def test_crosspost_job_marks_failed_target_when_bridge_completion_fails(db_session, monkeypatch):
    user = User(
        email="crosspost-failure@example.com",
        settings_json={
            "marketplace_connections": {
                "mercari": {
                    "display_name": "Mercari Browser",
                    "account_handle": "mercari-browser",
                    "workflow_state": "ready",
                    "publish_mode": "browser_assist",
                }
            }
        },
    )
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Vintage amplifier",
        description="Recently serviced",
        listing_price=189.0,
        quantity=1,
        marketplace_data={"targets": ["mercari"]},
    )
    db_session.add(listing)
    db_session.flush()

    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["mercari"],
        requested_mode="auto",
        status="queued",
        execution_plan={
            "targets": [
                {
                    "marketplace": "mercari",
                    "execution_mode": "browser_assist",
                }
            ]
        },
    )
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(
        tasks,
        "execute_secondary_marketplace_path",
        lambda **_kwargs: {
            "status": "BROWSER_AUTOMATION_READY",
            "bridge_submission": {
                "status": "SUBMITTED_TO_BRIDGE",
                "bridge_response": {"job_id": "bridge-crosspost-fail-1"},
            },
        },
    )
    monkeypatch.setattr(
        tasks,
        "wait_for_bridge_job",
        lambda **_kwargs: {
            "status": "failed",
            "error": "Marketplace blocked the draft submission",
        },
    )

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    assert result["status"] == "failed"
    assert result["results"][0]["status"] == "failed"
    assert "blocked the draft submission" in result["results"][0]["error"]

    db_session.refresh(job)
    serialized = _serialize_crosspost_job(job)
    assert serialized["failed_target_count"] == 1
    assert serialized["review_required_count"] == 0
    assert serialized["submitted_count"] == 0
    assert "mercari" in (serialized["last_error"] or "").lower()

    marketplace_listing = (
        db_session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.listing_id == listing.id,
            MarketplaceListing.marketplace == "mercari",
        )
        .one()
    )
    assert marketplace_listing.status == MarketplaceListingStatus.FAILED
    assert marketplace_listing.raw_response["bridge_completion"]["status"] == "failed"
    assert "blocked the draft submission" in marketplace_listing.raw_response["error"]
