from app.api.marketplace_jobs import _serialize_crosspost_job, _serialize_import_job
from app.models.enums import ListingStatus, MarketplaceListingStatus
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceImportJob, MarketplaceListing, User
from app.services.automation_bridge import AutomationBridgeError
from app.services.marketplace_preflight import MarketplacePreflightService
from app.workers import tasks


def _ready_preflight(_self, _db, listing, marketplace):
    return {
        "listing_id": listing.id,
        "marketplace": marketplace,
        "status": "ready",
        "blockers": [],
        "warnings": [],
        "missing_fields": [],
        "invalid_fields": [],
        "payload_preview": {"payload": {}},
        "policy_summary": {},
        "category_summary": {},
        "shipping_summary": {"manual_measurement_needed": False},
        "image_summary": {"attached_count": 1, "actual_count": 1, "reference_only": False, "actual_image_present": True, "public_image_ready": True},
        "pricing_summary": {"current_price": 42, "price_confidence": 0.9},
        "condition_summary": {},
        "quality_summary": {"ready_for_publish_queue": True},
        "readiness_summary": {"actual_image_count": 1, "manual_photo_needed": False},
        "last_checked_at": None,
        "source_version": "test",
    }


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

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", _ready_preflight)
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
    assert serialized["ui_primary_action"] == "Complete handoff"
    assert serialized["ui_state_tone"] == "warning"
    assert "Retry" in serialized["ui_secondary_actions"]

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


def test_facebook_browser_assist_requires_visible_listing_before_marking_published(db_session, monkeypatch):
    user = User(
        email="facebook-visibility@example.com",
        settings_json={
            "marketplace_connections": {
                "facebook": {
                    "display_name": "Facebook Marketplace",
                    "account_handle": "facebook-main",
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
        title="Outdoor chair",
        description="Patio chair",
        listing_price=39.0,
        quantity=1,
        marketplace_data={"targets": ["facebook"]},
    )
    db_session.add(listing)
    db_session.flush()

    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["facebook"],
        requested_mode="auto",
        status="queued",
        execution_plan={
            "targets": [
                {
                    "marketplace": "facebook",
                    "execution_mode": "browser_assist",
                }
            ]
        },
    )
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", _ready_preflight)
    monkeypatch.setattr(
        tasks,
        "execute_secondary_marketplace_path",
        lambda **_kwargs: {
            "status": "BROWSER_AUTOMATION_READY",
            "bridge_submission": {
                "status": "SUBMITTED_TO_BRIDGE",
                "bridge_response": {"job_id": "bridge-facebook-1"},
            },
        },
    )
    monkeypatch.setattr(
        tasks,
        "wait_for_bridge_job",
        lambda **_kwargs: {
            "status": "completed",
            "result": {"status": "submitted_to_marketplace", "submitted": True, "listing_urls": []},
        },
    )

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    assert result["status"] == "completed"
    assert result["results"][0]["status"] == "submitted_to_marketplace"

    db_session.refresh(job)
    serialized = _serialize_crosspost_job(job)
    assert serialized["review_required_count"] == 1
    assert serialized["submitted_count"] == 0
    assert serialized["target_outcomes"][0]["requires_review"] is True
    assert "visible seller listing" in serialized["target_outcomes"][0]["operator_note"]

    marketplace_listing = (
        db_session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.listing_id == listing.id,
            MarketplaceListing.marketplace == "facebook",
        )
        .one()
    )
    assert marketplace_listing.status == MarketplaceListingStatus.PENDING
    assert marketplace_listing.raw_response["bridge_confirmation_status"] == "submitted_without_visible_listing"


def test_facebook_browser_assist_marks_published_only_with_visible_listing(db_session, monkeypatch):
    user = User(
        email="facebook-published@example.com",
        settings_json={
            "marketplace_connections": {
                "facebook": {
                    "display_name": "Facebook Marketplace",
                    "account_handle": "facebook-main",
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
        title="Outdoor table",
        description="Patio table",
        listing_price=89.0,
        quantity=1,
        marketplace_data={"targets": ["facebook"]},
    )
    db_session.add(listing)
    db_session.flush()

    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["facebook"],
        requested_mode="auto",
        status="queued",
        execution_plan={
            "targets": [
                {
                    "marketplace": "facebook",
                    "execution_mode": "browser_assist",
                }
            ]
        },
    )
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", _ready_preflight)
    monkeypatch.setattr(
        tasks,
        "execute_secondary_marketplace_path",
        lambda **_kwargs: {
            "status": "BROWSER_AUTOMATION_READY",
            "bridge_submission": {
                "status": "SUBMITTED_TO_BRIDGE",
                "bridge_response": {"job_id": "bridge-facebook-2"},
            },
        },
    )
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

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    assert result["status"] == "completed"
    assert result["results"][0]["status"] == "submitted_to_marketplace"

    db_session.refresh(job)
    serialized = _serialize_crosspost_job(job)
    assert serialized["submitted_count"] == 1
    assert serialized["review_required_count"] == 0

    marketplace_listing = (
        db_session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.listing_id == listing.id,
            MarketplaceListing.marketplace == "facebook",
        )
        .one()
    )
    assert marketplace_listing.status == MarketplaceListingStatus.PUBLISHED
    assert marketplace_listing.marketplace_listing_id == "1234567890"
    assert marketplace_listing.raw_response["marketplace_listing_id"] == "1234567890"


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
    assert serialized["ui_primary_action"] == "Review imports"
    assert serialized["ui_state_tone"] == "warning"


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

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", _ready_preflight)
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
    assert serialized["ui_primary_action"] == "Retry"
    assert serialized["ui_state_tone"] == "danger"

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


def test_crosspost_job_keeps_browser_assist_pending_when_bridge_fetch_times_out(db_session, monkeypatch):
    user = User(
        email="crosspost-timeout@example.com",
        settings_json={
            "marketplace_connections": {
                "facebook": {
                    "display_name": "Facebook Marketplace",
                    "account_handle": "facebook-browser",
                    "workflow_state": "ready",
                    "publish_mode": "browser_assist",
                    "bridge_account_key": "facebook-main",
                }
            }
        },
    )
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Timeout test item",
        description="Browser assist timeout should remain pending",
        listing_price=39.0,
        quantity=1,
        marketplace_data={
            "targets": ["facebook"],
            "channels": {"facebook": {"publish_mode": "browser_assist"}},
        },
    )
    db_session.add(listing)
    db_session.flush()

    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["facebook"],
        requested_mode="auto",
        status="queued",
        execution_plan={
            "targets": [
                {
                    "marketplace": "facebook",
                    "execution_mode": "browser_assist",
                }
            ]
        },
    )
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", _ready_preflight)
    monkeypatch.setattr(
        tasks,
        "execute_secondary_marketplace_path",
        lambda **_kwargs: {
            "status": "BROWSER_AUTOMATION_READY",
            "bridge_submission": {
                "status": "SUBMITTED_TO_BRIDGE",
                "bridge_response": {"job_id": "bridge-crosspost-timeout-1"},
            },
        },
    )
    monkeypatch.setattr(
        tasks,
        "wait_for_bridge_job",
        lambda **_kwargs: (_ for _ in ()).throw(AutomationBridgeError("Automation bridge job fetch failed: timed out")),
    )

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    assert result["status"] == "completed"
    assert result["results"][0]["status"] == "BROWSER_AUTOMATION_READY"
    assert "bridge_fetch_warning" in result["results"][0]["response"]

    db_session.refresh(job)
    serialized = _serialize_crosspost_job(job)
    assert serialized["failed_target_count"] == 0
    assert serialized["review_required_count"] == 1
    assert serialized["submitted_count"] == 0
    assert serialized["ui_primary_action"] == "Complete handoff"
    assert serialized["ui_state_tone"] == "warning"

    marketplace_listing = (
        db_session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.listing_id == listing.id,
            MarketplaceListing.marketplace == "facebook",
        )
        .one()
    )
    assert marketplace_listing.status == MarketplaceListingStatus.PENDING
    assert "bridge_fetch_warning" in marketplace_listing.raw_response
