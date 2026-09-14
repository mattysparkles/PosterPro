from uuid import uuid4
import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.core import database as database_module
from app.models.enums import MarketplaceName
from app.models.enums import MarketplaceListingStatus
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceExtensionDevice, MarketplaceExtensionJob, MarketplaceListing, User
from app.workers import tasks
from app.services.sale_detection_service import SaleDetectionService


def _seed_marketplace_listing(user_id: int) -> int:
    db = database_module.SessionLocal()
    try:
        row = Listing(
            user_id=user_id,
            title="Test jacket",
            description="A clean test jacket with verified measurements and condition.",
            listing_price=35.0,
            suggested_price=35.0,
            condition="Pre-owned - Excellent",
            quantity=1,
            image_urls=["https://media.example.test/jacket.jpg"],
            category_suggestion="Clothing",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


async def _register(client, label: str) -> dict:
    response = await client.post(
        "/auth/register",
        json={"full_name": f"{label} Owner", "email": f"{label.lower()}-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert response.status_code == 201
    return response.json()


async def _pair(client, device_name: str) -> tuple[str, int]:
    code_response = await client.post("/browser-extension/pairing-codes", json={"device_name": device_name})
    assert code_response.status_code == 200
    paired = await client.post(
        "/browser-extension/pair",
        json={"pairing_code": code_response.json()["pairing_code"], "device_name": device_name, "browser": "Chrome", "extension_version": "0.3.1"},
    )
    assert paired.status_code == 200
    body = paired.json()
    return body["device_token"], body["device"]["id"]


@pytest.mark.anyio
async def test_extension_download_serves_current_installable_archive(async_client):
    response = await async_client.get("/browser-extension/download")
    assert response.status_code == 200
    assert "application/zip" in response.headers["content-type"]
    archive = ZipFile(BytesIO(response.content))
    names = set(archive.namelist())
    assert "posterpro-extension/manifest.json" in names
    assert "posterpro-extension/background.js" in names
    assert "posterpro-extension/content.js" in names
    assert "posterpro-extension/posterpro-link.js" in names
    assert "/opt/apps/" not in "\n".join(names)


@pytest.mark.anyio
async def test_extension_job_pair_claim_review_complete_and_update_identity(async_client):
    owner = await _register(async_client, "Extension")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    token, _device_id = await _pair(async_client, "Operator Chrome")

    queued = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "facebook", "action": "CREATE"},
    )
    assert queued.status_code == 200
    job_id = queued.json()["id"]
    payload = queued.json()["payload"]["marketplace_payload"]
    assert payload["start_url"] == "https://www.facebook.com/marketplace/create/item"
    assert payload["title"] == "Test jacket"
    assert payload["image_urls"] == ["https://media.example.test/jacket.jpg"]

    repeated = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "facebook", "action": "CREATE"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == job_id
    assert repeated.json()["deduplicated"] is True

    claimed = await async_client.post("/browser-extension/jobs/claim", headers={"Authorization": f"Bearer {token}"})
    assert claimed.status_code == 200
    assert claimed.json()["job"]["id"] == job_id
    assert claimed.json()["job"]["status"] == "CLAIMED"
    for state in ("NAVIGATING", "FORM_FILLING", "AWAITING_OPERATOR_REVIEW"):
        response = await async_client.post(
            f"/browser-extension/jobs/{job_id}/state",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": state},
        )
        assert response.status_code == 200
        assert response.json()["status"] == state

    listing_rows = (await async_client.get("/assisted-marketplace-jobs")).json()
    assert listing_rows[0]["id"] == job_id
    assert listing_rows[0]["status"] == "AWAITING_OPERATOR_REVIEW"
    assert not (await async_client.get("/browser-extension/devices")).json()["devices"][0]["revoked"]

    for state, extra in (
        ("SUBMITTING", {}),
        ("SUBMITTED", {"external_listing_id": "FB-12345", "external_url": "https://www.facebook.com/marketplace/item/12345"}),
        ("COMPLETED", {"external_listing_id": "FB-12345", "external_url": "https://www.facebook.com/marketplace/item/12345", "result": {"verified": True}}),
    ):
        response = await async_client.post(
            f"/browser-extension/jobs/{job_id}/state",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": state, **extra},
        )
        assert response.status_code == 200
    db = database_module.SessionLocal()
    try:
        marketplace_row = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.facebook).one()
        assert marketplace_row.marketplace_listing_id == "FB-12345"
        assert marketplace_row.status.value == "PUBLISHED"
        assert db.query(MarketplaceExtensionJob).filter_by(listing_id=listing_id).count() == 1
    finally:
        db.close()
    update = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "facebook", "action": "UPDATE"},
    )
    assert update.status_code == 200
    update_id = update.json()["id"]
    assert update.json()["action"] == "UPDATE"
    assert update.json()["payload"]["marketplace_payload"]["start_url"] == "https://www.facebook.com/marketplace/item/12345"
    assert update.json()["external_listing_id"] == "FB-12345"
    assert (await async_client.post("/browser-extension/jobs/claim", headers={"Authorization": f"Bearer {token}"})).json()["job"]["id"] == update_id
    for state, extra in (("NAVIGATING", {}), ("FORM_FILLING", {}), ("AWAITING_OPERATOR_REVIEW", {}), ("SUBMITTING", {}), ("SUBMITTED", {"external_listing_id": "FB-12345", "external_url": "https://www.facebook.com/marketplace/item/12345"}), ("COMPLETED", {"external_listing_id": "FB-12345", "external_url": "https://www.facebook.com/marketplace/item/12345"})):
        response = await async_client.post(f"/browser-extension/jobs/{update_id}/state", headers={"Authorization": f"Bearer {token}"}, json={"status": state, **extra})
        assert response.status_code == 200
    db = database_module.SessionLocal()
    try:
        marketplace_row = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.facebook).one()
        assert marketplace_row.marketplace_listing_id == "FB-12345"
        assert marketplace_row.status == MarketplaceListingStatus.UPDATED
    finally:
        db.close()

    duplicate_create = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "facebook", "action": "CREATE"},
    )
    assert duplicate_create.status_code == 409
    assert duplicate_create.json()["detail"]["code"] == "EXTERNAL_IDENTITY_EXISTS"

    end = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "facebook", "action": "END"},
    )
    assert end.status_code == 200
    end_id = end.json()["id"]
    assert end.json()["payload"]["marketplace_payload"]["start_url"] == "https://www.facebook.com/marketplace/item/12345"
    assert (await async_client.post("/browser-extension/jobs/claim", headers={"Authorization": f"Bearer {token}"})).json()["job"]["id"] == end_id
    for state in ("NAVIGATING", "AWAITING_OPERATOR_REVIEW", "SUBMITTING"):
        response = await async_client.post(
            f"/browser-extension/jobs/{end_id}/state",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": state},
        )
        assert response.status_code == 200
    wrong_url = await async_client.post(
        f"/browser-extension/jobs/{end_id}/state",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "SUBMITTED", "external_listing_id": "FB-12345", "external_url": "https://evil.example/item"},
    )
    assert wrong_url.status_code == 422
    for state in ("SUBMITTED", "COMPLETED"):
        response = await async_client.post(
            f"/browser-extension/jobs/{end_id}/state",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "status": state,
                "external_listing_id": "FB-12345",
                "external_url": "https://www.facebook.com/marketplace/item/12345",
                "result": {"operator_confirmed_end": True},
            },
        )
        assert response.status_code == 200
    db = database_module.SessionLocal()
    try:
        marketplace_row = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.facebook).one()
        assert marketplace_row.marketplace_listing_id == "FB-12345"
        assert marketplace_row.status == MarketplaceListingStatus.DELETED
    finally:
        db.close()


@pytest.mark.anyio
async def test_marketplace_diagnostic_is_tenant_scoped_and_persists_field_results(async_client):
    owner = await _register(async_client, "Diagnostic")
    token, _device_id = await _pair(async_client, "Diagnostic Chrome")

    queued = await async_client.post("/browser-extension/diagnostics/facebook", json={})
    assert queued.status_code == 200
    job = queued.json()
    assert job["action"] == "DIAGNOSTIC"
    assert job["listing_id"] is None
    assert job["payload"]["diagnostic"] is True
    assert job["payload"]["marketplace_payload"]["title"].startswith("PosterPro Safe Form Test")
    assert job["payload"]["marketplace_payload"]["image_urls"] == []

    claimed = await async_client.post("/browser-extension/jobs/claim", headers={"Authorization": f"Bearer {token}"})
    assert claimed.status_code == 200
    assert claimed.json()["job"]["id"] == job["id"]
    result = {
        "marketplace": "facebook",
        "action": "DIAGNOSTIC",
        "login_state": "LOGGED_IN",
        "page_state": "FORM_AVAILABLE",
        "capability_ready": True,
        "submission_performed": False,
        "cookies": "must not be persisted",
        "password": "must not be persisted",
        "field_results": [{"field": "category", "required": True, "detected": False, "attempted": True, "filled": False, "verified_value": None, "error_code": "FIELD_NOT_FOUND", "selector_diagnostic": {"field": "category", "page_path": "/marketplace/create/item?access_token=private", "selectors_tried": ["select[name=category]"], "controls": [{"tag": "button", "role": "button", "aria_label": "Category", "nearby_label": "Category", "name": "category", "placeholder": "", "option_labels": ["Clothing", "access_token=should-not-leak", "Collectibles"], "value": "private account data"}, {"tag": "input", "aria_label": "owner@example.com", "name": "access_token_secret"}]}}],
    }
    for status in ("NAVIGATING", "FORM_DETECTED", "TESTING_FIELDS", "COMPLETED"):
        response = await async_client.post(
            f"/browser-extension/jobs/{job['id']}/state",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": status, "error_code": "REQUIRED_FIELDS_UNRESOLVED" if status == "COMPLETED" else None, "result": result},
        )
        assert response.status_code == 200
    latest = await async_client.get("/browser-extension/diagnostics/latest/facebook")
    assert latest.status_code == 200
    assert latest.json()["current_version"] == "0.3.1"
    assert latest.json()["minimum_version"] == "0.3.1"
    assert latest.json()["result"]["field_results"][0]["error_code"] == "FIELD_NOT_FOUND"
    assert latest.json()["result"]["capability_ready"] is False
    assert "cookies" not in latest.json()["result"]
    assert "password" not in latest.json()["result"]
    diagnostic_field = latest.json()["result"]["field_results"][0]["selector_diagnostic"]
    assert diagnostic_field["page_path"] == "/marketplace/create/item"
    assert diagnostic_field["controls"][0] == {"tag": "button", "role": "button", "aria_label": "Category", "name": "category", "nearby_label": "Category", "option_labels": ["Clothing", "Collectibles"]}
    assert diagnostic_field["controls"][1] == {"tag": "input"}
    history = await async_client.get("/browser-extension/diagnostics/history/facebook")
    assert history.status_code == 200 and len(history.json()) == 1
    assert history.json()[0]["device"]["browser"] == "Chrome"
    assert history.json()[0]["minimum_version"] == "0.3.1"

    foreign = await _register(async_client, "ForeignDiagnostic")
    foreign_detail = await async_client.get(f"/browser-extension/diagnostics/{job['id']}")
    assert foreign_detail.status_code == 404
    db = database_module.SessionLocal()
    try:
        row = db.get(MarketplaceExtensionJob, job["id"])
        assert row.user_id == owner["user"]["id"]
        assert row.listing_id is None
        assert db.query(MarketplaceListing).filter_by(marketplace=MarketplaceName.facebook).count() == 0
    finally:
        db.close()


@pytest.mark.anyio
async def test_extension_version_status_updates_in_place_without_repairing_pairing(async_client):
    await _register(async_client, "ExtensionVersion")
    token, device_id = await _pair(async_client, "Existing Chrome Profile")
    headers = {"Authorization": f"Bearer {token}"}
    old = await async_client.post("/browser-extension/heartbeat", headers=headers, json={"browser": "Chrome", "extension_version": "0.2.0"})
    assert old.status_code == 200
    state = await async_client.get("/browser-extension/devices")
    device = next(item for item in state.json()["devices"] if item["id"] == device_id)
    assert state.json()["current_version"] == "0.3.1"
    assert state.json()["minimum_version"] == "0.3.1"
    assert device["update_required"] is True
    refreshed = await async_client.post("/browser-extension/heartbeat", headers=headers, json={"browser": "Chrome", "extension_version": "0.3.1"})
    assert refreshed.status_code == 200
    state = await async_client.get("/browser-extension/devices")
    device = next(item for item in state.json()["devices"] if item["id"] == device_id)
    assert device["update_required"] is False


@pytest.mark.anyio
async def test_operator_confirms_assisted_marketplace_result_from_posterpro_jobs(async_client):
    owner = await _register(async_client, "JobsReview")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    token, _device_id = await _pair(async_client, "Review Chrome")
    queued = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "mercari", "action": "CREATE"},
    )
    job_id = queued.json()["id"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await async_client.post("/browser-extension/jobs/claim", headers=headers)).json()["job"]["id"] == job_id
    for status in ("NAVIGATING", "FORM_FILLING", "AWAITING_OPERATOR_REVIEW"):
        response = await async_client.post(f"/browser-extension/jobs/{job_id}/state", headers=headers, json={"status": status, "result": {"page_url": "https://www.mercari.com/sell/"}})
        assert response.status_code == 200
    confirmed = await async_client.post(
        f"/assisted-marketplace-jobs/{job_id}/confirm-result",
        json={"confirmed": True, "external_listing_id": "MRC-913", "external_url": "https://www.mercari.com/item/m913/"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "COMPLETED"
    assert confirmed.json()["result"]["completion_source"] == "posterpro_jobs"
    db = database_module.SessionLocal()
    try:
        marketplace_row = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.mercari).one()
        assert marketplace_row.marketplace_listing_id == "MRC-913"
        assert marketplace_row.status == MarketplaceListingStatus.PUBLISHED
    finally:
        db.close()


@pytest.mark.anyio
async def test_extension_device_cannot_claim_or_update_another_tenants_job(async_client):
    first = await _register(async_client, "FirstTenant")
    first_listing = _seed_marketplace_listing(first["user"]["id"])
    first_token, _ = await _pair(async_client, "First device")
    queued = await async_client.post(
        f"/listings/{first_listing}/assisted-marketplace-jobs",
        json={"marketplace": "mercari", "action": "CREATE"},
    )
    assert queued.status_code == 200
    first_job_id = queued.json()["id"]

    second = await _register(async_client, "SecondTenant")
    assert second["user"]["id"] != first["user"]["id"]
    second_token, _ = await _pair(async_client, "Second device")
    assert (await async_client.post("/browser-extension/jobs/claim", headers={"Authorization": f"Bearer {second_token}"})).json() == {"job": None}
    denied = await async_client.post(
        f"/browser-extension/jobs/{first_job_id}/state",
        headers={"Authorization": f"Bearer {second_token}"},
        json={"status": "FAILED", "error_code": "IDOR_TEST"},
    )
    assert denied.status_code == 404
    # The original device can still claim its tenant-owned work.
    assert (await async_client.post("/browser-extension/jobs/claim", headers={"Authorization": f"Bearer {first_token}"})).json()["job"]["id"] == first_job_id


@pytest.mark.anyio
async def test_unknown_create_submission_is_not_automatically_retried(async_client):
    owner = await _register(async_client, "UnknownCreate")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    token, _device_id = await _pair(async_client, "Review device")
    queued = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "facebook", "action": "CREATE"},
    )
    assert queued.status_code == 200
    job_id = queued.json()["id"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await async_client.post("/browser-extension/jobs/claim", headers=headers)).json()["job"]["id"] == job_id
    for state in ("NAVIGATING", "FORM_FILLING", "AWAITING_OPERATOR_REVIEW", "SUBMITTING", "RETRYABLE"):
        response = await async_client.post(
            f"/browser-extension/jobs/{job_id}/state",
            headers=headers,
            json={"status": state, "error_detail": "Browser lost confirmation" if state == "RETRYABLE" else None},
        )
        assert response.status_code == 200
    final = response.json()
    assert final["status"] == "RETRYABLE"
    assert final["error_code"] == "SUBMISSION_OUTCOME_UNKNOWN"
    # The item cannot be reclaimed into a duplicate CREATE without reconciliation.
    assert (await async_client.post("/browser-extension/jobs/claim", headers=headers)).json() == {"job": None}
    duplicate = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "facebook", "action": "CREATE"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "OPERATOR_RECONCILIATION_REQUIRED"


@pytest.mark.anyio
async def test_failed_assisted_update_does_not_change_published_listing_lifecycle(async_client):
    owner = await _register(async_client, "UpdateFailure")
    user_id = owner["user"]["id"]
    listing_id = _seed_marketplace_listing(user_id)
    token, device_id = await _pair(async_client, "Update device")
    db = database_module.SessionLocal()
    try:
        remote = MarketplaceListing(
            listing_id=listing_id,
            marketplace=MarketplaceName.mercari,
            marketplace_listing_id="M-EXISTING-42",
            status=MarketplaceListingStatus.PUBLISHED,
            raw_response={"external_url": "https://www.mercari.com/us/item/m42/"},
        )
        db.add(remote)
        db.flush()
        job = MarketplaceExtensionJob(
            user_id=user_id,
            listing_id=listing_id,
            marketplace="mercari",
            action="UPDATE",
            status="CLAIMED",
            device_id=device_id,
            external_listing_id="M-EXISTING-42",
            external_url="https://www.mercari.com/us/item/m42/",
            payload_snapshot={
                "external_listing_id": "M-EXISTING-42",
                "external_url": "https://www.mercari.com/us/item/m42/",
            },
        )
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    response = await async_client.post(
        f"/browser-extension/jobs/{job_id}/state",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "FAILED", "error_code": "EDIT_FORM_NOT_FOUND", "error_detail": "The marketplace edit form was not available."},
    )
    assert response.status_code == 200
    db = database_module.SessionLocal()
    try:
        remote = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.mercari).one()
        assert remote.status == MarketplaceListingStatus.PUBLISHED
        assert remote.marketplace_listing_id == "M-EXISTING-42"
        assert remote.raw_response["error_code"] == "EDIT_FORM_NOT_FOUND"
        assert remote.raw_response["extension_state"] == "FAILED"
    finally:
        db.close()


@pytest.mark.anyio
async def test_extension_claim_is_exclusive_and_expired_lease_recovers(async_client):
    owner = await _register(async_client, "LeaseOwner")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    first_token, first_device = await _pair(async_client, "First worker")
    second_token, second_device = await _pair(async_client, "Second worker")
    queued = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "facebook", "action": "CREATE"},
    )
    job_id = queued.json()["id"]
    first_headers = {"Authorization": f"Bearer {first_token}"}
    second_headers = {"Authorization": f"Bearer {second_token}"}
    assert (await async_client.post("/browser-extension/jobs/claim", headers=first_headers)).json()["job"]["id"] == job_id
    assert (await async_client.post("/browser-extension/jobs/claim", headers=second_headers)).json() == {"job": None}
    assert (await async_client.post(
        f"/browser-extension/jobs/{job_id}/state",
        headers=first_headers,
        json={"status": "NAVIGATING"},
    )).status_code == 200
    renewed = await async_client.post(f"/browser-extension/jobs/{job_id}/lease", headers=first_headers, json={})
    assert renewed.status_code == 200
    assert datetime.fromisoformat(renewed.json()["lease_expires_at"]) > datetime.now(UTC) + timedelta(minutes=4)
    assert (await async_client.post(f"/browser-extension/jobs/{job_id}/lease", headers=second_headers, json={})).status_code == 404
    status = await async_client.get(f"/browser-extension/jobs/{job_id}/status", headers=first_headers)
    assert status.status_code == 200 and status.json()["status"] == "NAVIGATING"
    db = database_module.SessionLocal()
    try:
        job = db.get(MarketplaceExtensionJob, job_id)
        job.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert (await async_client.post(f"/browser-extension/jobs/{job_id}/lease", headers=first_headers, json={})).status_code == 409
    recovered = (await async_client.post("/browser-extension/jobs/claim", headers=second_headers)).json()["job"]
    assert recovered["id"] == job_id
    assert recovered["attempt_count"] == 2
    assert (await async_client.get(f"/browser-extension/jobs/{job_id}/status", headers=first_headers)).status_code == 404
    db = database_module.SessionLocal()
    try:
        job = db.get(MarketplaceExtensionJob, job_id)
        assert job.device_id == second_device
        assert job.device_id != first_device
    finally:
        db.close()


@pytest.mark.anyio
@pytest.mark.parametrize("publish_mode", ["browser_assist", "provider_assist"])
async def test_normal_crosspost_worker_creates_durable_assisted_job_and_stays_running(async_client, monkeypatch, publish_mode):
    owner = await _register(async_client, "CrosspostWorker")
    await _pair(async_client, "Online crosspost agent")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    db = database_module.SessionLocal()
    try:
        listing = db.get(Listing, listing_id)
        listing.marketplace_data = {"channels": {"facebook": {"publish_mode": publish_mode}}}
        parent = MarketplaceCrosspostJob(
            user_id=owner["user"]["id"],
            listing_id=listing_id,
            target_marketplaces=["facebook"],
            status="queued",
            execution_plan={"operation": "create"},
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        parent_id = parent.id
    finally:
        db.close()
    monkeypatch.setattr(tasks, "SessionLocal", database_module.SessionLocal)
    monkeypatch.setattr(
        tasks.MarketplacePreflightService,
        "preflight_listing",
        lambda _self, _db, _listing, marketplace: {"marketplace": marketplace, "blockers": [], "warnings": []},
    )
    result = tasks.process_marketplace_crosspost_job_task.run(parent_id)
    assert result["status"] == "running"
    detail = await async_client.get(f"/marketplace-crosspost-jobs/{parent_id}")
    assert detail.status_code == 200
    assert len(detail.json()["assisted_jobs"]) == 1
    assert detail.json()["assisted_jobs"][0]["status"] == "QUEUED"
    db = database_module.SessionLocal()
    try:
        child = db.query(MarketplaceExtensionJob).filter_by(crosspost_job_id=parent_id).one()
        parent = db.get(MarketplaceCrosspostJob, parent_id)
        assert child.marketplace == "facebook"
        assert child.action == "CREATE"
        assert child.status == "QUEUED"
        assert parent.status == "running"
        assert parent.result_summary["results"][0]["extension_job_id"] == child.id
    finally:
        db.close()


def test_sold_cross_market_listing_queues_operator_end_without_false_deleted_state(db_session):
    owner = User(email=f"sale-{uuid4()}@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(
        user_id=owner.id,
        title="Test item",
        description="Detailed test item description.",
        listing_price=20.0,
        condition="New",
        quantity=1,
        image_urls=["https://media.example.test/item.jpg"],
        category_suggestion="Home",
        ebay_listing_id="E-456",
        marketplace_data={"offer": {"offerId": "offer-456"}},
    )
    db_session.add(listing)
    db_session.flush()
    remote = MarketplaceListing(
        listing_id=listing.id,
        marketplace=MarketplaceName.mercari,
        marketplace_listing_id="M-123",
        status=MarketplaceListingStatus.PUBLISHED,
        raw_response={"external_url": "https://www.mercari.com/us/item/m123/"},
    )
    db_session.add(remote)
    ebay_remote = MarketplaceListing(
        listing_id=listing.id,
        marketplace=MarketplaceName.ebay,
        marketplace_listing_id="E-456",
        status=MarketplaceListingStatus.PUBLISHED,
    )
    db_session.add(ebay_remote)
    db_session.commit()

    result = asyncio.run(
        SaleDetectionService()._fanout_quantity_adjustment(
            db_session,
            listing,
            owner,
            sold_platform="facebook",
            quantity_sold=1,
            dry_run=False,
        )
    )
    db_session.commit()
    db_session.refresh(remote)
    child = db_session.query(MarketplaceExtensionJob).filter_by(listing_id=listing.id, marketplace="mercari", action="END").one()
    assert child.status == "QUEUED"
    assert child.external_listing_id == "M-123"
    assert remote.status == MarketplaceListingStatus.PUBLISHED
    assert result["mercari"]["response"]["extension_job_id"] == child.id
    assert result["mercari"]["response"]["status"] == "QUEUED_FOR_OPERATOR_REVIEW"
    ebay_end = db_session.query(MarketplaceCrosspostJob).filter_by(listing_id=listing.id, requested_mode="sale_reconciliation_end").one()
    assert ebay_end.execution_plan["operation"] == "end"
    assert ebay_end.execution_plan["external_listing_id"] == "E-456"
    assert result["ebay"]["response"]["status"] == "QUEUED_DIRECT_END"


def test_standard_publish_worker_queues_browser_assist_in_durable_transport(monkeypatch):
    owner = User(email=f"standard-publish-{uuid4()}@example.com", password_hash="x")
    db = database_module.SessionLocal()
    try:
        db.add(owner)
        db.flush()
        now = datetime.now(UTC).replace(tzinfo=None)
        db.add(MarketplaceExtensionDevice(
            user_id=owner.id,
            device_key=f"standard-device-{uuid4()}",
            token_hash=f"standard-token-{uuid4()}",
            extension_version="0.2.2",
            last_seen_at=now,
        ))
        listing = Listing(
            user_id=owner.id,
            title="Standard worker listing",
            description="Detailed item description for a worker contract test.",
            listing_price=22.0,
            condition="New",
            quantity=1,
            image_urls=["https://media.example.test/item.jpg"],
            category_suggestion="Home",
            marketplace_data={"channels": {"facebook": {"publish_mode": "browser_assist"}}},
        )
        db.add(listing)
        db.commit()
        listing_id = listing.id
    finally:
        db.close()

    monkeypatch.setattr(tasks, "SessionLocal", database_module.SessionLocal)
    monkeypatch.setattr(
        tasks.MarketplacePreflightService,
        "preflight_listing",
        lambda _self, _db, _listing, marketplace: {"marketplace": marketplace, "blockers": [], "warnings": []},
    )
    result = tasks.publish_listing_to_marketplace_task.run(listing_id, "facebook")
    assert result["execution_mode"] == "browser_extension"
    assert result["status"] == "QUEUED"
    db = database_module.SessionLocal()
    try:
        child = db.query(MarketplaceExtensionJob).filter_by(listing_id=listing_id, marketplace="facebook", action="CREATE").one()
        market_row = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.facebook).one()
        assert child.status == "QUEUED"
        assert market_row.status == MarketplaceListingStatus.PENDING
        assert market_row.raw_response["extension_job_id"] == child.id
    finally:
        db.close()
