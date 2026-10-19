from types import SimpleNamespace

from app.api.marketplace_jobs import _serialize_crosspost_job, _serialize_import_job
from app.models.enums import ListingStatus, MarketplaceListingStatus
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceImportJob, MarketplaceListing, MarketplacePublishBatch, User
from app.services.automation_bridge import AutomationBridgeError
from app.services.ebay_service import EbayIntegrationError, EbaySellerRestrictedError
from app.services.marketplace_preflight import MarketplacePreflightService
from app.workers import tasks
from app.services.marketplace_orchestrator import finalize_unpublish_archive_listing


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


def test_ebay_crosspost_error_retains_provider_http_method():
    error = EbayIntegrationError(
        "temporary provider error", provider_call_attempted=True,
        method="GET", endpoint="/sell/inventory/v1/offer/offer-1", http_status=500,
        provider_response={"errors": [{"errorId": 25001, "message": "A system error has occurred."}]},
    )
    payload = tasks._ebay_crosspost_error_payload(error)
    assert payload["provider_method"] == "GET"
    assert payload["provider_endpoint"] == "/sell/inventory/v1/offer/offer-1"
    assert payload["remote_outcome"] == "REMOTE_STATE_UNKNOWN"


def _archive_fixture(db_session, markets=("ebay",), statuses=None):
    user = User(email=f"archive-{id(markets)}-{len(markets)}@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.PUBLISHED, title="Archive candidate", description="item", listing_price=20)
    db_session.add(listing); db_session.flush()
    batch = MarketplacePublishBatch(id=f"archive-test-{listing.id}", user_id=user.id, operation="unpublish_and_archive", selected_count=1, selected_listing_ids=[listing.id], requested_marketplaces=list(markets), status="queued", summary={})
    db_session.add(batch); db_session.flush()
    for index, market in enumerate(markets):
        status = (statuses or ["completed"] * len(markets))[index]
        result_status = "ENDED" if status == "completed" else None
        job = MarketplaceCrosspostJob(user_id=user.id, listing_id=listing.id, target_marketplaces=[market], requested_mode="unpublish_and_archive", status=status, batch_id=batch.id, execution_plan={"operation": "end", "marketplace": market, "external_listing_id": f"R-{market}"}, result_summary={"results": [{"marketplace": market, "status": result_status}] if result_status else []})
        db_session.add(job)
    db_session.commit()
    return listing, batch


def test_unpublish_archive_finalizer_waits_for_all_destinations(db_session):
    listing, batch = _archive_fixture(db_session, ("ebay", "facebook"), ["completed", "running"])
    state = finalize_unpublish_archive_listing(db_session, batch.id, listing.id)
    db_session.commit(); db_session.refresh(listing)
    assert state["status"] == "UNPUBLISHING"
    assert listing.status != ListingStatus.archived
    second = db_session.query(MarketplaceCrosspostJob).filter_by(batch_id=batch.id, listing_id=listing.id, target_marketplaces=["facebook"]).one()
    second.status = "failed"; second.result_summary = {"results": [{"marketplace": "facebook", "status": "failed", "error": "manual end required"}]}; db_session.commit()
    state = finalize_unpublish_archive_listing(db_session, batch.id, listing.id)
    db_session.commit(); db_session.refresh(listing)
    assert state["status"] == "PARTIALLY_UNPUBLISHED"
    assert listing.status != ListingStatus.archived


def test_unpublish_archive_finalizer_archives_only_after_all_end_confirmations(db_session):
    listing, batch = _archive_fixture(db_session, ("ebay", "facebook"), ["completed", "completed"])
    state = finalize_unpublish_archive_listing(db_session, batch.id, listing.id)
    db_session.commit(); db_session.refresh(listing)
    assert state["status"] == "ARCHIVED"
    assert listing.status == ListingStatus.archived
    assert "archived_vine" in (listing.custom_labels or [])


def test_unpublish_archive_generic_completed_is_not_remote_confirmation(db_session):
    listing, batch = _archive_fixture(db_session, ("ebay",), ["completed"])
    job = db_session.query(MarketplaceCrosspostJob).filter_by(batch_id=batch.id, listing_id=listing.id).one()
    job.result_summary = {"results": [{"marketplace": "ebay", "status": "completed"}]}
    db_session.commit()
    state = finalize_unpublish_archive_listing(db_session, batch.id, listing.id)
    db_session.commit(); db_session.refresh(listing)
    assert state["status"] == "REMOTE_STATE_UNKNOWN"
    assert listing.status != ListingStatus.archived


def test_unpublish_archive_unknown_identity_never_archives(db_session):
    user = User(email="archive-unknown@example.com"); db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.PUBLISHED, title="Unknown", description="item")
    db_session.add(listing); db_session.flush()
    batch = MarketplacePublishBatch(id="archive-unknown-batch", user_id=user.id, operation="unpublish_and_archive", selected_count=1, selected_listing_ids=[listing.id], requested_marketplaces=["ebay"], status="blocked", summary={})
    db_session.add(batch); db_session.flush()
    job = MarketplaceCrosspostJob(user_id=user.id, listing_id=listing.id, target_marketplaces=["ebay"], requested_mode="unpublish_and_archive", status="blocked", batch_id=batch.id, execution_plan={"operation": "end", "marketplace": "ebay", "unpublish_and_archive": True, "blocker_code": "REMOTE_STATE_UNKNOWN"}, result_summary={"results": [{"marketplace": "ebay", "status": "REMOTE_STATE_UNKNOWN"}]})
    db_session.add(job); db_session.commit()
    state = finalize_unpublish_archive_listing(db_session, batch.id, listing.id)
    db_session.commit(); db_session.refresh(listing)
    assert state["status"] == "REMOTE_STATE_UNKNOWN"
    assert listing.status != ListingStatus.archived


def test_unpublish_archive_preserves_local_media_fields(db_session):
    listing, batch = _archive_fixture(db_session)
    listing.image_urls = ["/media/original.jpg"]
    listing.listing_images = [{"url": "/media/original.jpg", "primary": True}]
    db_session.commit()
    state = finalize_unpublish_archive_listing(db_session, batch.id, listing.id)
    db_session.commit(); db_session.refresh(listing)
    assert state["status"] == "ARCHIVED"
    assert listing.image_urls == ["/media/original.jpg"]
    assert listing.listing_images == [{"url": "/media/original.jpg", "primary": True}]


def test_crosspost_worker_persists_ebay_provider_rejection_without_celery_retry(db_session, monkeypatch):
    user = User(email="ebay-provider-rejection@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.ready, title="Specific desk lamp", description="Working lamp with intact shade.", listing_price=42, quantity=1)
    db_session.add(listing); db_session.flush()
    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["ebay"],
        status="queued",
        execution_plan={"preflight": {"marketplace": "ebay", "status": "ready", "blockers": [], "warnings": []}},
    )
    db_session.add(job); db_session.commit()

    def reject_publish(*_args, **_kwargs):
        raise EbaySellerRestrictedError(
            "eBay publishOffer rejected: seller cannot create listings until account issues are resolved",
            provider_call_attempted=True,
            http_status=400,
            endpoint="/sell/inventory/v1/offer/OFFER-1/publish",
            provider_response={"errors": [{"errorId": 25019, "message": "You can't currently create new listings or revise existing listings."}]},
        )

    monkeypatch.setattr(tasks, "resolve_execution_mode", lambda **_kwargs: "direct_api")
    monkeypatch.setattr(tasks.multi_platform_publisher, "publish", reject_publish)
    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    db_session.refresh(job)
    row = db_session.query(MarketplaceListing).filter_by(listing_id=listing.id, marketplace="ebay").one()
    assert result["status"] == "failed"
    assert job.status == "failed"
    assert row.raw_response["error_code"] == "EBAY_SELLER_RESTRICTED"
    assert row.raw_response["provider_error_id"] == "25019"
    assert row.raw_response["provider_call_attempted"] is True
    assert row.raw_response["remote_outcome"] == "NOT_PUBLISHED"
    assert not getattr(tasks.process_marketplace_crosspost_job_task, "autoretry_for", ())


def test_hosted_crosspost_job_waits_for_bridge_completion_and_exposes_review_summary(db_session, monkeypatch):
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
    monkeypatch.setattr(tasks, "resolve_execution_mode", lambda **_kwargs: "browser_assist")
    monkeypatch.setattr(tasks, "has_online_compatible_extension", lambda *_args, **_kwargs: False)
    bridge_modes = []
    monkeypatch.setattr(
        tasks,
        "execute_secondary_marketplace_path",
        lambda **kwargs: (
            bridge_modes.append(kwargs["execution_mode"])
            or {
                "status": "BROWSER_AUTOMATION_READY",
                "bridge_submission": {
                    "status": "SUBMITTED_TO_BRIDGE",
                    "bridge_response": {"job_id": "bridge-crosspost-1"},
                },
            }
        ),
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
    assert bridge_modes == ["hosted_browser_assist"]

    db_session.refresh(job)
    serialized = _serialize_crosspost_job(job)
    assert serialized["review_required_count"] == 1
    assert serialized["submitted_count"] == 0
    assert serialized["failed_target_count"] == 0
    assert serialized["target_outcomes"][0]["requires_review"] is True
    assert serialized["ui_primary_action"] == "Complete handoff"
    assert serialized["ui_state_tone"] == "warning"
    # The worker completed its bridge handoff. Replaying a completed job could
    # duplicate a marketplace submission; complete the existing operator handoff instead.
    assert "Retry" not in serialized["ui_secondary_actions"]

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


def test_ebay_end_crosspost_worker_targets_exact_identity_and_completes(db_session, monkeypatch):
    user = User(email="exact-ebay-end@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.posted,
        title="Exact eBay item",
        description="A confirmed active listing",
        listing_price=30,
        quantity=1,
        ebay_listing_id="123456789012",
        marketplace_data={"offer": {"offerId": "offer-123"}},
    )
    db_session.add(listing); db_session.flush()
    db_session.add(MarketplaceListing(listing_id=listing.id, marketplace="ebay", marketplace_listing_id="123456789012", status=MarketplaceListingStatus.PUBLISHED))
    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["ebay"],
        requested_mode="manual_end",
        status="queued",
        execution_plan={"operation": "end", "external_listing_id": "123456789012"},
    )
    db_session.add(job); db_session.commit()
    calls = []

    async def fake_end(_listing, _db, *, expected_external_listing_id):
        calls.append(expected_external_listing_id)
        return {"status": "ENDED", "listing_id": expected_external_listing_id, "offer_id": "offer-123"}

    monkeypatch.setattr(tasks, "end_ebay_listing", fake_end)
    result = tasks.process_marketplace_crosspost_job_task.run(job.id)
    assert calls == ["123456789012"]
    assert result["status"] == "completed"
    assert result["results"][0]["operation"] == "END"
    assert result["results"][0]["status"] == "ENDED"


def test_ebay_update_worker_marks_exact_marketplace_state_updated(db_session, monkeypatch):
    user = User(email="exact-ebay-update@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.posted,
        title="Update item",
        description="A confirmed active listing",
        listing_price=29.99,
        quantity=1,
        ebay_listing_id="123456789012",
        marketplace_data={},
    )
    db_session.add(listing); db_session.flush()
    marketplace_listing = MarketplaceListing(
        listing_id=listing.id,
        marketplace="ebay",
        marketplace_listing_id="123456789012",
        status=MarketplaceListingStatus.PUBLISHED,
    )
    db_session.add(marketplace_listing)
    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["ebay"],
        requested_mode="manual_update",
        status="queued",
        execution_plan={"operation": "update", "external_listing_id": "123456789012"},
    )
    db_session.add(job); db_session.commit()

    async def fake_revise(_listing, _db, *, expected_external_listing_id):
        assert expected_external_listing_id == "123456789012"
        return {"status": "UPDATED", "listing_id": expected_external_listing_id, "offer_id": "offer-123"}

    monkeypatch.setattr(tasks, "revise_ebay_listing", fake_revise)
    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    assert result["status"] == "completed"
    db_session.refresh(marketplace_listing)
    assert marketplace_listing.status == MarketplaceListingStatus.UPDATED
    assert marketplace_listing.raw_response["status"] == "UPDATED"


def test_ebay_update_worker_persists_failure_without_creating_replacement(db_session, monkeypatch):
    user = User(email="exact-ebay-update-failure@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.posted, title="Update failure", listing_price=29.99, ebay_listing_id="123456789012")
    db_session.add(listing); db_session.flush()
    marketplace_listing = MarketplaceListing(listing_id=listing.id, marketplace="ebay", marketplace_listing_id="123456789012", status=MarketplaceListingStatus.PUBLISHED)
    db_session.add(marketplace_listing)
    job = MarketplaceCrosspostJob(user_id=user.id, listing_id=listing.id, target_marketplaces=["ebay"], requested_mode="manual_update", status="queued", execution_plan={"operation": "update", "external_listing_id": "123456789012"})
    db_session.add(job); db_session.commit()

    async def fail_revise(*_args, **_kwargs):
        raise RuntimeError("eBay account verification required")

    monkeypatch.setattr(tasks, "revise_ebay_listing", fail_revise)
    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    assert result["status"] == "failed"
    db_session.refresh(marketplace_listing)
    assert marketplace_listing.status == MarketplaceListingStatus.FAILED
    assert marketplace_listing.marketplace_listing_id == "123456789012"
    assert "verification required" in marketplace_listing.raw_response["error"]

def test_hosted_facebook_assist_requires_visible_listing_before_marking_published(db_session, monkeypatch):
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
    monkeypatch.setattr(tasks, "resolve_execution_mode", lambda **_kwargs: "hosted_browser_assist")
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
    monkeypatch.setattr(tasks.poll_hosted_bridge_publish_task, "apply_async", lambda **_kwargs: None)
    monkeypatch.setattr(tasks, "get_bridge_job", lambda _job_id: {"status": "completed", "result": {"status": "submitted_to_marketplace", "submitted": True, "listing_urls": []}})

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)
    assert result["status"] == "running"
    assert result["results"][0]["status"] == "browser_automation_pending"
    tasks.poll_hosted_bridge_publish_task.run(job.id, listing.id, "facebook", "bridge-facebook-1", "hosted_browser_assist")

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


def test_hosted_facebook_assist_marks_published_only_with_visible_listing(db_session, monkeypatch):
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
    monkeypatch.setattr(tasks, "resolve_execution_mode", lambda **_kwargs: "hosted_browser_assist")
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
    monkeypatch.setattr(tasks.poll_hosted_bridge_publish_task, "apply_async", lambda **_kwargs: None)
    monkeypatch.setattr(tasks, "get_bridge_job", lambda _job_id: {"status": "completed", "result": {"status": "submitted_to_marketplace", "submitted": True, "marketplace_listing_id": "1234567890", "listing_urls": ["https://www.facebook.com/marketplace/item/1234567890"]}})

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)
    assert result["status"] == "running"
    assert result["results"][0]["status"] == "browser_automation_pending"
    tasks.poll_hosted_bridge_publish_task.run(job.id, listing.id, "facebook", "bridge-facebook-2", "hosted_browser_assist")

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


def test_hosted_crosspost_job_marks_failed_target_when_bridge_completion_fails(db_session, monkeypatch):
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
    monkeypatch.setattr(tasks, "resolve_execution_mode", lambda **_kwargs: "hosted_browser_assist")
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


def test_hosted_crosspost_job_keeps_browser_assist_pending_when_bridge_fetch_times_out(db_session, monkeypatch):
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
    monkeypatch.setattr(tasks, "resolve_execution_mode", lambda **_kwargs: "hosted_browser_assist")
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
    monkeypatch.setattr(tasks.poll_hosted_bridge_publish_task, "apply_async", lambda **_kwargs: None)

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)

    assert result["status"] == "running"
    assert result["results"][0]["status"] == "browser_automation_pending"
    assert result["results"][0]["response"]["bridge_poll_status"] == "pending"

    db_session.refresh(job)
    serialized = _serialize_crosspost_job(job)
    assert serialized["failed_target_count"] == 0
    assert serialized["review_required_count"] == 0
    assert serialized["submitted_count"] == 0
    assert serialized["ui_primary_action"] == "Monitor"
    assert serialized["ui_state_tone"] == "info"

    marketplace_listing = (
        db_session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.listing_id == listing.id,
            MarketplaceListing.marketplace == "facebook",
        )
        .one()
    )
    assert marketplace_listing.status == MarketplaceListingStatus.PENDING
    assert marketplace_listing.raw_response["bridge_poll_status"] == "pending"


def test_blocked_facebook_category_target_is_refreshed_and_queued_without_replaying_provider_work(db_session, monkeypatch):
    user = User(email="facebook-auto-recovery@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="RV mattress topper with corner cut",
        description="Foam topper for RV mattress",
        listing_price=40,
        quantity=1,
    )
    db_session.add(listing)
    db_session.flush()
    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["facebook"],
        status="blocked",
        attempt_count=0,
        result_summary={"results": [{
            "marketplace": "facebook",
            "status": "blocked",
            "response": {"error": [{"code": "FACEBOOK_CATEGORY_MISSING"}]},
        }]},
    )
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(
        MarketplacePreflightService,
        "preflight_listing",
        lambda _self, _db, _listing, marketplace: {"marketplace": marketplace, "status": "ready", "blockers": [], "warnings": []},
    )
    dispatched = []
    monkeypatch.setattr(
        tasks.process_marketplace_crosspost_job_task,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs) or SimpleNamespace(id="auto-repaired-facebook-task"),
    )

    result = tasks.repair_blocked_facebook_publish_targets_task.run(10)

    db_session.refresh(job)
    assert result["repaired_and_queued"] == [job.id]
    assert job.status == "queued"
    assert job.task_id == "auto-repaired-facebook-task"
    assert job.execution_plan["automatic_preflight_repair_history"][0]["prior_blocker_codes"] == ["FACEBOOK_CATEGORY_MISSING"]
    assert dispatched and dispatched[0]["args"] == [job.id]


def test_repair_splits_safe_facebook_target_from_ambiguous_combined_job(db_session, monkeypatch):
    user = User(email="split-facebook-recovery@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="RV mattress topper",
        description="Shippable foam mattress topper",
        listing_price=40,
        quantity=1,
    )
    db_session.add(listing)
    db_session.flush()
    parent = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        target_marketplaces=["ebay", "facebook"],
        status="failed",
        attempt_count=1,
        batch_id="combined-parent-batch",
        last_error="eBay remote state requires reconciliation",
        execution_plan={"approved_execution_payload": {"ebay": {"old": "snapshot"}}},
        result_summary={"results": [
            {"marketplace": "ebay", "status": "failed", "response": {"remote_outcome": "REMOTE_STATE_UNKNOWN"}},
            {"marketplace": "facebook", "status": "blocked", "response": {"error": [{"code": "FACEBOOK_CATEGORY_MISSING"}]}},
        ]},
    )
    db_session.add(parent)
    db_session.commit()
    parent_result_before = parent.result_summary
    monkeypatch.setattr(
        MarketplacePreflightService,
        "preflight_listing",
        lambda _self, _db, _listing, marketplace: {"marketplace": marketplace, "status": "ready", "blockers": [], "warnings": []},
    )
    dispatched = []
    monkeypatch.setattr(
        tasks.process_marketplace_crosspost_job_task,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs) or SimpleNamespace(id="facebook-only-recovery-task"),
    )

    result = tasks.repair_blocked_facebook_publish_targets_task.run(10)

    db_session.refresh(parent)
    children = db_session.query(MarketplaceCrosspostJob).filter(
        MarketplaceCrosspostJob.id != parent.id,
        MarketplaceCrosspostJob.listing_id == listing.id,
    ).all()
    assert len(children) == 1
    child = children[0]
    assert result["repaired_and_queued"] == [child.id]
    assert child.target_marketplaces == ["facebook"]
    assert child.status == "queued"
    assert child.requested_mode == "operator_approved_facebook_preflight_recovery"
    assert child.result_summary["automatic_preflight_repair"]["source_job_id"] == parent.id
    assert child.task_id == "facebook-only-recovery-task"
    assert dispatched[0]["args"] == [child.id]
    assert parent.status == "failed"
    assert parent.attempt_count == 1
    assert parent.result_summary == parent_result_before


def test_facebook_preflight_recovery_scans_past_unrelated_old_failures(db_session, monkeypatch):
    user = User(email="facebook-recovery-starvation@example.com")
    db_session.add(user)
    db_session.flush()
    for index in range(30):
        listing = Listing(user_id=user.id, status=ListingStatus.PROCESSED, title=f"Old failed {index}", description="item", listing_price=10, quantity=1)
        db_session.add(listing)
        db_session.flush()
        db_session.add(MarketplaceCrosspostJob(
            user_id=user.id,
            listing_id=listing.id,
            target_marketplaces=["facebook"],
            status="failed",
            attempt_count=1,
            result_summary={"results": [{"marketplace": "facebook", "status": "failed", "response": {"error": [{"code": "FACEBOOK_LOGIN_REQUIRED"}]}}]},
        ))
    target = Listing(user_id=user.id, status=ListingStatus.PROCESSED, title="Recently repairable", description="item", listing_price=10, quantity=1)
    db_session.add(target)
    db_session.flush()
    repairable = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=target.id,
        target_marketplaces=["facebook"],
        status="blocked",
        attempt_count=0,
        result_summary={"results": [{"marketplace": "facebook", "status": "blocked", "response": {"error": [{"code": "FACEBOOK_CATEGORY_MISSING"}]}}]},
    )
    db_session.add(repairable)
    db_session.commit()
    monkeypatch.setattr(
        MarketplacePreflightService,
        "preflight_listing",
        lambda _self, _db, listing, marketplace: {"listing_id": listing.id, "marketplace": marketplace, "status": "ready", "blockers": [], "warnings": []},
    )
    monkeypatch.setattr(tasks.process_marketplace_crosspost_job_task, "apply_async", lambda **kwargs: SimpleNamespace(id="recovered"))

    result = tasks.repair_blocked_facebook_publish_targets_task.run(1)

    db_session.refresh(repairable)
    assert result["repaired_and_queued"] == [repairable.id]
    assert repairable.status == "queued"
