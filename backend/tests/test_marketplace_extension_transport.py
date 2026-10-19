from uuid import uuid4
import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
from zipfile import ZipFile
import json

import pytest

from app.core import database as database_module
from app.models.enums import MarketplaceName
from app.models.enums import MarketplaceListingStatus
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceExtensionDevice, MarketplaceExtensionJob, MarketplaceListing, MarketplacePublishBatch, User
from app.workers import tasks
from app.services.sale_detection_service import SaleDetectionService
from app.services.marketplace_extension_jobs import build_extension_marketplace_payload
from app.services.marketplace_extension_jobs import MarketplaceExtensionJobError, queue_extension_marketplace_action
from app.connectors.registry import get_connector
from app.api.marketplace_extension import CURRENT_EXTENSION_VERSION, FACEBOOK_DESCRIPTION_REPAIR_BATCH_ID, _crosspost_parent_status, _diagnostic_external_block_detail, _facebook_description_fingerprint, _queue_facebook_description_repair, _safe_diagnostic_result
from app.api.marketplace_jobs import FACEBOOK_EBAY_ROLLOUT_BATCH_ID


def test_mercari_connector_exposes_no_unapproved_extension_mutations():
    assert get_connector("mercari").get_capabilities() == {
        "supports_direct_create": False,
        "supports_direct_update": False,
        "supports_direct_end": False,
        "supports_extension_create": False,
        "supports_extension_update": False,
        "supports_extension_end": False,
        "supports_sale_polling": False,
        "supports_external_status": False,
    }
    with pytest.raises(MarketplaceExtensionJobError) as error:
        queue_extension_marketplace_action(None, user_id=1, listing=None, marketplace="mercari", action="CREATE")
    assert error.value.code == "MERCARI_AUTHORIZED_PROVIDER_REQUIRED"


def test_diagnostic_capability_requires_matching_background_and_content_script_builds():
    base = {
        "marketplace": "mercari", "action": "DIAGNOSTIC", "login_state": "LOGGED_IN",
        "page_state": "FORM_AVAILABLE", "submission_performed": False,
        "background_version": CURRENT_EXTENSION_VERSION, "content_script_version": CURRENT_EXTENSION_VERSION,
        "extension_version": CURRENT_EXTENSION_VERSION, "content_script_build_id": f"posterpro-{CURRENT_EXTENSION_VERSION}-facebook-submit-identity-binding",
        "field_results": [
            {"field": name, "required": True, "detected": True, "attempted": True, "filled": True}
            for name in ("title", "price", "description", "photos", "category", "condition", "shipping")
        ],
    }
    assert _safe_diagnostic_result(base, "mercari")["capability_ready"] is True
    stale = {**base, "content_script_version": "0.3.10"}
    assert _safe_diagnostic_result(stale, "mercari")["capability_ready"] is False


def test_crosspost_parent_status_preserves_partial_success_when_ebay_is_external_block():
    parent = MarketplaceCrosspostJob(
        target_marketplaces=["facebook", "mercari", "ebay"],
        result_summary={"results": [{"marketplace": "ebay", "status": "failed", "response": {"provider_error_id": 25019}}]},
    )
    children = [
        MarketplaceExtensionJob(marketplace="facebook", status="COMPLETED"),
        MarketplaceExtensionJob(marketplace="mercari", status="COMPLETED"),
    ]
    assert _crosspost_parent_status(parent, children) == "partial"
    children[1].status = "QUEUED"
    assert _crosspost_parent_status(parent, children) == "running"
    children[:] = [MarketplaceExtensionJob(marketplace="facebook", status="SUBMITTED_PENDING_REVIEW")]
    clean_parent = MarketplaceCrosspostJob(target_marketplaces=["facebook"])
    assert _crosspost_parent_status(clean_parent, children) == "submitted_pending_review"


@pytest.mark.anyio
async def test_facebook_publish_without_immediate_identity_becomes_pending_review_not_retryable(async_client):
    owner = await _register(async_client, "FacebookPendingReview")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    token, device_id = await _pair(async_client, "Facebook pending review browser")
    now = datetime.now(UTC).replace(tzinfo=None)
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(
            user_id=owner["user"]["id"], listing_id=listing_id,
            target_marketplaces=["facebook"], status="running",
            execution_plan={"operation": "create", "operator_live_confirmed": True},
        )
        db.add(parent); db.flush()
        child = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
                marketplace="facebook", action="CREATE", status="SUBMITTED", attempt_count=1,
                device_id=device_id, started_at=now - timedelta(seconds=1), lease_expires_at=now + timedelta(minutes=5),
            result={
                "submission_performed": True, "confirmation": "NO_VERIFIABLE_POST_SUBMIT_STATE",
                "background_version": "0.3.68", "extension_version": "0.3.68",
                "content_script_version": "0.3.68",
                "content_script_build_id": "posterpro-0.3.68-facebook-submit-identity-binding",
            },
            payload_snapshot={"marketplace_payload": {"title": "Test jacket", "price": 35.0}},
        )
        db.add(child); db.commit()
        child_id, parent_id = child.id, parent.id
    finally:
        db.close()
    headers = {"Authorization": f"Bearer {token}"}
    response = await async_client.post(f"/browser-extension/jobs/{child_id}/state", headers=headers, json={
        "status": "FAILED", "error_code": "SUBMISSION_OUTCOME_UNKNOWN",
        "result": {"submission_performed": True, "remote_reconciliation": {"status": "AMBIGUOUS", "source": "authenticated_facebook_seller_listings"}},
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "SUBMITTED_PENDING_REVIEW"
    assert response.json()["error_code"] is None
    assert response.json()["result"]["submission_state"] == "SUBMITTED_PENDING_REVIEW"
    assert response.json()["result"]["remote_reconciliation"]["safe_same_child_retry"] is False
    assert datetime.fromisoformat(response.json()["result"]["remote_reconciliation"]["next_reconciliation_at"]) > datetime.now(UTC)
    resumed = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=facebook")
    assert resumed.status_code == 409
    duplicate = await async_client.post(f"/listings/{listing_id}/assisted-marketplace-jobs", json={"marketplace": "facebook", "action": "CREATE"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "OPERATOR_RECONCILIATION_REQUIRED"
    claim = await async_client.post("/browser-extension/facebook-reconciliation/claim", headers=headers)
    assert claim.status_code == 200 and claim.json()["task"] is None  # first check is deferred
    db = database_module.SessionLocal()
    try:
        row = db.get(MarketplaceExtensionJob, child_id)
        assert row.status == "SUBMITTED_PENDING_REVIEW"
        assert row.result["submit_transition_evidence"]["content_script_build_id"].startswith("posterpro-0.3.68-")
        assert row.result["submitted_at"]
        assert row.result["submission_duration_seconds"] >= 0
    finally:
        db.close()


@pytest.mark.anyio
async def test_facebook_form_progress_is_durable_and_safe_retry_preserves_checkpoints(async_client):
    owner = await _register(async_client, "FacebookFormProgress")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    token, device_id = await _pair(async_client, "Facebook progress browser", extension_version="0.3.68")
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(
            user_id=owner["user"]["id"], listing_id=listing_id,
            target_marketplaces=["facebook"], status="running",
            execution_plan={"operation": "create", "operator_live_confirmed": True},
        )
        db.add(parent); db.flush()
        job = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="facebook", action="CREATE", status="FORM_FILLING", attempt_count=1,
            device_id=device_id, started_at=datetime.now(UTC).replace(tzinfo=None),
            lease_expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
            payload_snapshot={"marketplace_payload": {"title": "Progress test", "price": 20.0}},
        )
        db.add(job); db.commit(); job_id = job.id
    finally:
        db.close()
    headers = {"Authorization": f"Bearer {token}"}
    checkpoint = await async_client.post(f"/browser-extension/jobs/{job_id}/progress", headers=headers, json={
        "stage": "CLEAN_FORM_VERIFIED", "phase": "completed",
        "extension_version": "0.3.68",
        "content_build_id": "posterpro-0.3.68-facebook-submit-identity-binding",
        "page_url": "https://www.facebook.com/marketplace/create/item",
        "detail": {"field": "category", "internal_payload": "must not persist"},
    })
    assert checkpoint.status_code == 200, checkpoint.text
    state = await async_client.post(f"/browser-extension/jobs/{job_id}/state", headers=headers, json={
        "status": "RETRYABLE", "error_code": "FACEBOOK_CATEGORY_RESOLUTION_TIMEOUT",
        "result": {"submission_performed": False, "safe_pre_submit_retry": True, "last_progress_stage": "CLEAN_FORM_VERIFIED"},
    })
    assert state.status_code == 200, state.text
    assert state.json()["status"] == "RETRYABLE"
    assert state.json()["result"]["safe_pre_submit_retry"] is True
    progress = state.json()["result"]["form_progress"]
    assert progress["last_progress_stage"] == "CLEAN_FORM_VERIFIED"
    assert progress["history"][-1]["detail"] == {"field": "category"}
    assert progress["history"][-1]["page_url"] == "https://www.facebook.com/marketplace/create/item"
    db = database_module.SessionLocal()
    try:
        row = db.get(MarketplaceExtensionJob, job_id)
        assert row.priority == 10
        assert row.result["form_progress"]["last_progress_at"]
    finally:
        db.close()
    queued_listing_id = _seed_marketplace_listing(owner["user"]["id"])
    db = database_module.SessionLocal()
    try:
        queued = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=queued_listing_id,
            marketplace="mercari", action="CREATE", status="QUEUED",
            payload_snapshot={"marketplace_payload": {"title": "Independent queue item", "price": 5.0}},
        )
        db.add(queued); db.commit(); queued_id = queued.id
    finally:
        db.close()
    next_claim = await async_client.post("/browser-extension/jobs/claim", headers=headers)
    assert next_claim.status_code == 200
    assert next_claim.json()["job"] is None
    db = database_module.SessionLocal()
    try:
        suppressed = db.get(MarketplaceExtensionJob, queued_id)
        assert suppressed.status == "BLOCKED_EXTERNAL"
        assert suppressed.error_code == "MERCARI_AUTHORIZED_PROVIDER_REQUIRED"
        assert "No submission was performed" in suppressed.error_detail
    finally:
        db.close()


@pytest.mark.anyio
async def test_mercari_live_form_diagnostic_is_blocked_without_authorized_provider(async_client):
    owner = await _register(async_client, "MercariPolicyGuard")
    response = await async_client.post("/browser-extension/diagnostics/mercari", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MERCARI_AUTHORIZED_PROVIDER_REQUIRED"
    assert response.json()["detail"]["policy_url"].startswith("https://www.mercari.com/")


@pytest.mark.anyio
async def test_facebook_progress_rejects_wrong_build_and_submitting_state(async_client):
    owner = await _register(async_client, "FacebookProgressGuard")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    token, device_id = await _pair(async_client, "Facebook progress guard browser")
    db = database_module.SessionLocal()
    try:
        job = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id,
            marketplace="facebook", action="CREATE", status="FORM_FILLING", attempt_count=1,
            device_id=device_id, lease_expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
            payload_snapshot={"marketplace_payload": {"title": "Guard test", "price": 20.0}},
        )
        db.add(job); db.commit(); job_id = job.id
    finally:
        db.close()
    headers = {"Authorization": f"Bearer {token}"}
    body = {"stage": "TITLE_SET", "phase": "checkpoint", "extension_version": "0.3.68", "content_build_id": "posterpro-0.3.68-facebook-submit-identity-binding"}
    wrong_build = await async_client.post(f"/browser-extension/jobs/{job_id}/progress", headers=headers, json={**body, "content_build_id": "posterpro-0.3.64-old"})
    assert wrong_build.status_code == 409
    submit = await async_client.post(f"/browser-extension/jobs/{job_id}/state", headers=headers, json={"status": "SUBMITTING", "result": {"submission_performed": False}})
    assert submit.status_code == 200
    submitted_retry = await async_client.post(f"/browser-extension/jobs/{job_id}/state", headers=headers, json={
        "status": "RETRYABLE", "error_code": "CONTENT_SCRIPT_RESPONSE_TIMEOUT",
        "result": {"submission_performed": False, "safe_pre_submit_retry": True},
    })
    assert submitted_retry.status_code == 409
    after_submit = await async_client.post(f"/browser-extension/jobs/{job_id}/progress", headers=headers, json=body)
    assert after_submit.status_code == 409
    db = database_module.SessionLocal()
    try:
        row = db.get(MarketplaceExtensionJob, job_id)
        assert row.status == "SUBMITTING"
    finally:
        db.close()
@pytest.mark.anyio
async def test_facebook_rollout_release_reuses_only_unsubmitted_paused_children(async_client):
    owner = await _register(async_client, "FacebookFleetRelease")
    listing_ids = [_seed_marketplace_listing(owner["user"]["id"]) for _ in range(2)]
    db = database_module.SessionLocal()
    try:
        batch = MarketplacePublishBatch(
            id=FACEBOOK_EBAY_ROLLOUT_BATCH_ID, user_id=owner["user"]["id"], operation="bulk_publish",
            selected_count=2, selected_listing_ids=listing_ids, requested_marketplaces=["facebook"],
            status="paused", summary={"rollout_control": {"state": "PAUSED_CANARY_DESCRIPTION_GUARD"}, "items": []},
        )
        db.add(batch); db.flush()
        children = []
        batch_items = []
        for listing_id in listing_ids:
            parent = MarketplaceCrosspostJob(
                user_id=owner["user"]["id"], listing_id=listing_id, target_marketplaces=["facebook"],
                status="paused", batch_id=batch.id, execution_plan={"operation": "create", "operator_live_confirmed": True},
            )
            db.add(parent); db.flush()
            child = MarketplaceExtensionJob(
                user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
                marketplace="facebook", action="CREATE", status="FAILED", attempt_count=0,
                error_code="ROLLOUT_PAUSED_FOR_STABILIZATION", result={"submission_performed": False},
                payload_snapshot={"marketplace_payload": {"title": "Test jacket", "price": 35.0}},
            )
            db.add(child); db.flush(); children.append((parent, child))
            batch_items.append({"status": "PAUSED", "listing_id": listing_id, "crosspost_job_id": parent.id, "extension_job_id": child.id})
        batch.summary = {**batch.summary, "items": batch_items}
        db.commit()
        original_created = {child.id: child.created_at for _, child in children}
    finally:
        db.close()
    response = await async_client.post(f"/marketplace-publish-batches/{FACEBOOK_EBAY_ROLLOUT_BATCH_ID}/release-facebook-paused")
    assert response.status_code == 200, response.text
    assert response.json()["released_count"] == 2
    assert response.json()["created_at_preserved"] is True
    db = database_module.SessionLocal()
    try:
        for _parent, child in children:
            reloaded = db.get(MarketplaceExtensionJob, child.id)
            assert reloaded.status == "QUEUED"
            assert reloaded.created_at == original_created[child.id]
            assert reloaded.payload_snapshot["execution_history"][-1]["error_code"] == "ROLLOUT_PAUSED_FOR_STABILIZATION"
        batch = db.get(MarketplacePublishBatch, FACEBOOK_EBAY_ROLLOUT_BATCH_ID)
        assert batch.summary["rollout_control"]["state"] == "RELEASED"
        assert batch.summary["pending"] == 2
    finally:
        db.close()


def test_mercari_interaction_evidence_is_persisted_for_operator_report():
    result = _safe_diagnostic_result({
        "login_state": "LOGGED_IN", "page_state": "FORM_AVAILABLE",
            "background_version": "0.3.68", "content_script_version": "0.3.68",
            "extension_version": "0.3.68", "submission_performed": False,
        "field_results": [{
            "field": "category", "required": True, "detected": True,
            "attempted": True, "filled": False, "error_code": "DESTINATION_OPTION_NOT_FOUND",
            "interaction_diagnostic": {
                "stage": "OPTIONS_NOT_FOUND", "label": "Category",
                "first_visible_options": ["Women", "Men"],
                "dom_menu_count_before": 2, "dom_menu_count_after": 3,
                "trigger_clicked": "Select category",
            },
        }],
    }, "mercari")
    category = result["field_results"][0]
    assert category["interaction_diagnostic"]["stage"] == "OPTIONS_NOT_FOUND"
    assert category["interaction_diagnostic"]["first_visible_options"] == ["Women", "Men"]


def test_mercari_form_hydration_timeout_evidence_is_persisted_without_claiming_form_ready():
    result = _safe_diagnostic_result({
        "login_state": "LOGGED_IN", "page_state": "FORM_HYDRATION_TIMEOUT",
        "stage": "FORM_HYDRATION_TIMEOUT", "submission_performed": False,
        "background_version": "0.3.56", "content_script_version": "0.3.56",
        "extension_version": "0.3.56", "content_script_build_id": "posterpro-0.3.56-facebook-offers-disabled",
        "form_readiness": {
            "form_ready_wait_started": True, "form_ready_wait_ms": 12000,
            "form_ready_anchors": {"title": False, "price": False, "description": False, "photos": False, "visible_form": False, "core_anchor_count": 0},
        },
        "page_evidence": {
            "current_url": "https://www.mercari.com/sell/",
            "page_title": "Sell an item", "document_ready_state": "complete",
            "body_text_summary": "List an item Loading your form",
            "loading_indicators": True, "sell_shell_visible": True,
            "login_state": "LOGGED_IN", "captcha_or_checkpoint_text": False,
            "counts": {"inputs": 0, "textareas": 0, "buttons": 1, "file_inputs": 0, "visible_forms": 0},
            "form_anchors": {"title": False, "price": False, "description": False, "photos": False, "visible_form": False, "core_anchor_count": 0},
        },
        "field_results": [], "missing_required_fields": [],
    }, "mercari")
    assert result["page_state"] == "FORM_HYDRATION_TIMEOUT"
    assert result["form_detected"] is False
    assert result["capability_ready"] is False
    assert result["form_readiness"]["form_ready_wait_ms"] == 12000
    assert result["page_evidence"]["loading_indicators"] is True
    assert result["page_evidence"]["form_anchors"]["core_anchor_count"] == 0


def test_mercari_category_action_and_preselected_shipping_evidence_survive_sanitization():
    result = _safe_diagnostic_result({
        "login_state": "LOGGED_IN", "page_state": "FORM_AVAILABLE",
            "background_version": "0.3.68", "content_script_version": "0.3.68",
            "extension_version": "0.3.68", "submission_performed": False,
        "field_results": [{
            "field": "category", "required": True, "detected": True,
            "attempted": True, "filled": False,
            "interaction_diagnostic": {
                "stage": "SELECTION_NOT_PERSISTED", "selected_option": "All Tops & blouses",
                "clicked_element_structure": {
                    "element": {"tag": "span", "text": "All Tops & blouses"},
                    "actionable_ancestor": {"tag": "button", "role": "button", "tabindex": "0", "text": "All Tops & blouses"},
                },
                "dialog_visible_before": True, "dialog_visible_after": True,
            },
        }, {
            "field": "shipping", "required": True, "detected": True,
            "attempted": True, "filled": True, "verified_value": "Prepaid label We'll email you a label",
            "interaction_diagnostic": {
                "stage": "SELECTION_VERIFIED_ALREADY_SELECTED",
                "selected_input": {"value": "MERCARI", "checked": True, "aria_label": "mercariShipping", "card_text": "Prepaid label We'll email you a label"},
            },
        }],
    }, "mercari")
    category = result["field_results"][0]["interaction_diagnostic"]
    assert category["clicked_element_structure"]["actionable_ancestor"]["tag"] == "button"
    assert category["dialog_visible_before"] is True
    shipping = result["field_results"][1]["interaction_diagnostic"]
    assert shipping["selected_input"]["value"] == "MERCARI"
    assert shipping["selected_input"]["checked"] is True


def test_mercari_selected_shipping_without_card_copy_survives_sanitization():
    result = _safe_diagnostic_result({
        "login_state": "LOGGED_IN", "page_state": "FORM_AVAILABLE",
        "background_version": "0.3.56", "content_script_version": "0.3.56",
        "extension_version": "0.3.56", "submission_performed": False,
        "field_results": [{
            "field": "shipping", "required": True, "detected": True,
            "attempted": True, "filled": True, "verified_value": "MERCARI",
            "interaction_diagnostic": {
                "stage": "SELECTION_VERIFIED_ALREADY_SELECTED",
                "selected_input": {"value": "MERCARI", "checked": True, "aria_label": "mercariShipping", "card_text": None},
            },
        }],
    }, "mercari")
    shipping = result["field_results"][0]
    assert shipping["filled"] is True
    assert shipping["verified_value"] == "MERCARI"
    assert shipping["interaction_diagnostic"]["stage"] == "SELECTION_VERIFIED_ALREADY_SELECTED"
    assert shipping["interaction_diagnostic"]["selected_input"] == {
        "value": "MERCARI", "checked": True, "aria_label": "mercariShipping",
    }


def test_facebook_location_staged_dom_evidence_is_structured_and_sanitized():
    result = _safe_diagnostic_result({
        "login_state": "LOGGED_IN", "page_state": "FORM_AVAILABLE",
        "background_version": "0.3.56", "content_script_version": "0.3.56",
        "extension_version": "0.3.56", "submission_performed": False,
        "field_results": [{
            "field": "location", "required": True, "detected": False,
            "attempted": True, "filled": False, "error_code": "FIELD_NOT_FOUND",
            "interaction_diagnostic": {
                "stage": "LOCATION_NOT_VERIFIED_SAFE_FLOW_INCOMPLETE",
                "verified_mode": None, "next_clicked": True,
                "location_requirement_satisfied": False, "location_value_verified": False,
                "stages": [
                    {"phase": "BEFORE_INTERACTION", "step": 0, "page_path": "/marketplace/create/item", "headings": ["Create listing"], "button_labels": ["Next"], "location_related_labels": [], "final_publish_boundary": False, "location_validation_error": False, "user_presence_challenge": False, "field_labels": [{"type": "input", "label": "Title", "placeholder": "Enter title"}], "semantic_controls": [], "selected_controls": []},
                    {"phase": "AFTER_SAFE_CONTINUE", "step": 1, "page_path": "/marketplace/create/item/review", "headings": ["Review listing"], "button_labels": ["Publish"], "location_related_labels": ["Huntington Station, NY 11746"], "final_publish_boundary": True, "final_publish_labels": ["Publish"], "location_validation_error": False, "user_presence_challenge": False, "seller_private_text": "do-not-persist"},
                ],
            },
        }],
    }, "facebook")
    location = result["field_results"][0]
    interaction = location["interaction_diagnostic"]
    assert interaction["next_clicked"] is True
    assert interaction["stages"][1]["final_publish_boundary"] is True
    assert interaction["stages"][1]["location_related_labels"] == ["Huntington Station, NY 11746"]
    assert "seller_private_text" not in interaction["stages"][1]
    assert result["submission_performed"] is False


def test_facebook_inherited_location_can_pass_safe_requirement_without_claiming_value_verified():
    result = _safe_diagnostic_result({
        "login_state": "LOGGED_IN", "page_state": "FORM_AVAILABLE",
        "background_version": "0.3.68", "content_script_version": "0.3.68",
        "extension_version": "0.3.68", "submission_performed": False,
        "location_requirement_satisfied": True, "location_value_verified": False,
        "location_verified_mode": "FACEBOOK_INHERITED_LOCATION",
        "field_results": [
            {"field": field, "required": True, "detected": True, "attempted": True, "filled": True, "verified_value": "ready"}
            for field in ("title", "price", "description", "category", "condition", "photos")
        ] + [{
            "field": "location", "required": True, "detected": True, "attempted": True,
            "filled": True, "verified_value": "LOCATION_INHERITED_BY_FACEBOOK",
            "interaction_diagnostic": {
                "stage": "LOCATION_INHERITED_BY_FACEBOOK", "verified_mode": "FACEBOOK_INHERITED_LOCATION",
                "location_requirement_satisfied": True, "location_value_verified": False,
                "next_clicked": True,
                "stages": [{"phase": "BEFORE_INTERACTION", "step": 2, "page_path": "/marketplace/create/item/review", "headings": ["Review listing"], "button_labels": ["Publish"], "buttons": [{"label": "Publish", "enabled": True}], "location_related_labels": [], "final_publish_boundary": True, "final_publish_control_present": True, "location_validation_error": False, "user_presence_challenge": False}],
            },
        }],
    }, "facebook")
    assert result["capability_ready"] is True
    assert result["location_requirement_satisfied"] is True
    assert result["location_value_verified"] is False
    assert result["location_verified_mode"] == "FACEBOOK_INHERITED_LOCATION"
    assert result["submission_performed"] is False


def test_extension_execution_failure_stage_and_safe_chrome_error_are_persisted():
    result = _safe_diagnostic_result({
        "error_code": "AUTOMATION_TAB_EDIT_FAILED",
        "execution_diagnostic": {
        "background_version": "0.3.56", "stage": "AUTOMATION_TAB_UPDATE",
            "last_successful_stage": "AUTOMATION_TAB_ID_LOOKUP", "tab_id": 88,
            "requested_url": {"origin": "https://www.mercari.com", "path": "/sell/?secret=redacted"},
            "error_name": "Error", "error_message": "Tabs cannot be edited right now (user may be dragging a tab).",
            "events": [{"stage": "AUTOMATION_TAB_UPDATE", "status": "failed", "at": "2026-10-02T10:00:00.000Z", "error_message": "Tabs cannot be edited right now (user may be dragging a tab)."}],
        },
    }, "mercari")
    execution = result["execution_diagnostic"]
    assert result["error_code"] == "AUTOMATION_TAB_EDIT_FAILED"
    assert execution["stage"] == "AUTOMATION_TAB_UPDATE"
    assert execution["last_successful_stage"] == "AUTOMATION_TAB_ID_LOOKUP"
    assert execution["error_message"].startswith("Tabs cannot be edited")
    assert execution["requested_url"]["path"] == "/sell/"
    assert "secret" not in execution["requested_url"]["path"]


def test_blocked_external_diagnostic_explains_no_submission_and_recovery_action():
    detail = _diagnostic_external_block_detail(
        marketplace="mercari",
        result={"login_state": "UNKNOWN", "page_state": "UNKNOWN", "form_detected": False},
    )
    assert "could not see a live Mercari page" in detail
    assert "No listing was submitted" in detail
    assert "paired Chrome profile" in detail


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


async def _pair(client, device_name: str, *, extension_version: str = CURRENT_EXTENSION_VERSION) -> tuple[str, int]:
    code_response = await client.post("/browser-extension/pairing-codes", json={"device_name": device_name})
    assert code_response.status_code == 200
    paired = await client.post(
        "/browser-extension/pair",
        json={"pairing_code": code_response.json()["pairing_code"], "device_name": device_name, "browser": "Chrome", "extension_version": extension_version},
    )
    assert paired.status_code == 200
    body = paired.json()
    return body["device_token"], body["device"]["id"]


def _seed_facebook_unknown_reconciliation(owner_id: int, listing_id: int, *, known_remote: bool = False):
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(
            user_id=owner_id, listing_id=listing_id, target_marketplaces=["facebook"],
            status="awaiting_operator_review", execution_plan={"operation": "create", "operator_live_confirmed": True},
        )
        db.add(parent)
        db.flush()
        result = {"submission_performed": True}
        if known_remote:
            result["derived_classification"] = "REMOTE_PRESENT_RECONCILIATION_REQUIRED"
        child = MarketplaceExtensionJob(
            user_id=owner_id, listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="facebook", action="CREATE", status="FAILED", attempt_count=1,
            error_code="SUBMISSION_OUTCOME_UNKNOWN", result=result,
            payload_snapshot={"marketplace_payload": {"title": "Exact reconciliation item", "price": 29.99, "description": "Canonical test description for this exact listing."}},
        )
        db.add(child)
        db.commit()
        return parent.id, child.id
    finally:
        db.close()


@pytest.mark.anyio
async def test_new_pairing_retires_previous_same_browser_install(async_client):
    owner = await _register(async_client, "ExtensionReplacement")
    old_token, old_device_id = await _pair(async_client, "PosterPro browser")
    code_response = await async_client.post("/browser-extension/pairing-codes", json={"device_name": "PosterPro browser"})
    paired = await async_client.post(
        "/browser-extension/pair",
        json={"pairing_code": code_response.json()["pairing_code"], "device_name": "PosterPro browser", "browser": "Chrome", "extension_version": "0.3.68"},
    )
    assert paired.status_code == 200
    paired_body = paired.json()
    new_token, new_device_id = paired_body["device_token"], paired_body["device"]["id"]

    assert new_device_id != old_device_id
    assert paired_body["stale_connections_removed"] == 1
    devices = (await async_client.get("/browser-extension/devices")).json()["devices"]
    old = next(device for device in devices if device["id"] == old_device_id)
    new = next(device for device in devices if device["id"] == new_device_id)
    assert old["revoked"] is True
    assert new["revoked"] is False
    stale_request = await async_client.post(
        "/browser-extension/jobs/claim",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert stale_request.status_code == 401
    fresh_request = await async_client.post(
        "/browser-extension/jobs/claim",
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert fresh_request.status_code == 200


@pytest.mark.anyio
async def test_facebook_authenticated_reconciliation_found_persists_remote_identity_without_create(async_client):
    owner = await _register(async_client, "FacebookReconFound")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    _parent_id, child_id = _seed_facebook_unknown_reconciliation(owner["user"]["id"], listing_id)
    token, device_id = await _pair(async_client, "Facebook reconciliation browser")
    headers = {"Authorization": f"Bearer {token}"}
    claim = await async_client.post("/browser-extension/facebook-reconciliation/claim", headers=headers)
    assert claim.status_code == 200, claim.text
    task = claim.json()["task"]
    assert task["id"] == child_id and task["reconciliation_only"] is True
    assert task["marketplace"] == "facebook" and task["action"] == "CREATE"
    title = task["payload"]["marketplace_payload"]["title"]
    expected_description = task["payload"]["marketplace_payload"]["description"]
    description_hash, description_length = _facebook_description_fingerprint(expected_description)
    response = await async_client.post(f"/browser-extension/facebook-reconciliation/{child_id}/result", headers=headers, json={
        "state": "FOUND", "external_listing_id": "99887766",
        "external_url": "https://www.facebook.com/marketplace/item/99887766/",
        "evidence": {
            "source": "authenticated_facebook_seller_listings",
            "page_url": "https://www.facebook.com/marketplace/you/selling/",
            "authenticated_account_context": True,
            "exact_title_match": True, "observed_title": title,
            "exact_price_match": True, "observed_price": "29.99",
            "description_verification": {
                "status": "VERIFIED", "matches": True,
                "expected_hash": description_hash, "observed_hash": description_hash,
                "expected_length": description_length, "observed_length": description_length,
            },
        },
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["remote_exists_verified"] is True
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, child_id)
        assert child.device_id == device_id
        assert child.external_listing_id == "99887766"
        assert child.external_url == "https://www.facebook.com/marketplace/item/99887766/"
        assert child.result["remote_reconciliation"]["status"] == "FOUND"
        assert child.result["submission_performed"] is True
        assert db.query(MarketplaceExtensionJob).filter_by(listing_id=listing_id, marketplace="facebook", action="CREATE").count() == 1
        listing = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.facebook).one()
        assert listing.marketplace_listing_id == "99887766"
        assert listing.status == MarketplaceListingStatus.PUBLISHED
    finally:
        db.close()


@pytest.mark.anyio
async def test_facebook_remote_found_without_canonical_description_proof_is_repair_required_not_success(async_client):
    owner = await _register(async_client, "FacebookDescriptionRepairGate")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    _parent_id, child_id = _seed_facebook_unknown_reconciliation(owner["user"]["id"], listing_id)
    token, _device_id = await _pair(async_client, "Facebook description repair browser")
    headers = {"Authorization": f"Bearer {token}"}
    claim = await async_client.post("/browser-extension/facebook-reconciliation/claim", headers=headers)
    title = claim.json()["task"]["payload"]["marketplace_payload"]["title"]
    response = await async_client.post(f"/browser-extension/facebook-reconciliation/{child_id}/result", headers=headers, json={
        "state": "FOUND", "external_listing_id": "99887767",
        "external_url": "https://www.facebook.com/marketplace/item/99887767/",
        "evidence": {
            "source": "authenticated_facebook_seller_listings",
            "page_url": "https://www.facebook.com/marketplace/you/selling/",
            "authenticated_account_context": True,
            "exact_title_match": True, "observed_title": title,
            "exact_price_match": True, "observed_price": "29.99",
        },
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "SUBMITTED_PENDING_REVIEW"
    assert response.json()["error_code"] is None
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, child_id)
        assert child.external_listing_id == "99887767"
        assert child.result["remote_reconciliation"]["remote_exists_verified"] is True
        assert child.result["remote_reconciliation"]["remote_description_verified"] is False
        assert child.result["remote_reconciliation"]["status"] == "FOUND_CONTENT_PENDING"
        assert db.query(MarketplaceExtensionJob).filter_by(listing_id=listing_id, marketplace="facebook", action="CREATE").count() == 1
        row = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.facebook).one()
        assert row.status == MarketplaceListingStatus.PUBLISHED
    finally:
        db.close()


@pytest.mark.anyio
async def test_facebook_exact_authenticated_absence_stays_pending_review_and_never_retries(async_client):
    owner = await _register(async_client, "FacebookReconAbsent")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    parent_id, child_id = _seed_facebook_unknown_reconciliation(owner["user"]["id"], listing_id)
    token, _device_id = await _pair(async_client, "Facebook no-result reconciliation browser")
    headers = {"Authorization": f"Bearer {token}"}
    claim = await async_client.post("/browser-extension/facebook-reconciliation/claim", headers=headers)
    task = claim.json()["task"]
    title = task["payload"]["marketplace_payload"]["title"]
    response = await async_client.post(f"/browser-extension/facebook-reconciliation/{child_id}/result", headers=headers, json={
        "state": "NOT_FOUND", "evidence": {
            "source": "authenticated_facebook_seller_listings",
            "page_url": "https://www.facebook.com/marketplace/you/selling/",
            "authenticated_account_context": True, "exact_search_used": True,
            "exact_title_search": True, "account_search_complete": True,
            "empty_state_verified": True, "result_count": 0, "searched_title": title,
        },
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "SUBMITTED_PENDING_REVIEW"
    assert response.json()["error_code"] is None
    assert response.json()["safe_same_child_retry"] is False
    assert (await async_client.post("/browser-extension/jobs/claim", headers=headers)).json() == {"job": None}
    resumed = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=facebook")
    assert resumed.status_code == 409, resumed.text
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, child_id)
        assert child.status == "SUBMITTED_PENDING_REVIEW"
        assert child.result["remote_reconciliation"]["last_observation_status"] == "NOT_FOUND"
        assert child.result["remote_reconciliation"]["safe_same_child_retry"] is False
    finally:
        db.close()


@pytest.mark.anyio
async def test_facebook_publish_without_immediate_identity_transitions_to_pending_review_and_is_not_retryable(async_client):
    owner = await _register(async_client, "FacebookSubmitPendingReview")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    token, _device_id = await _pair(async_client, "Facebook pending-review browser")
    headers = {"Authorization": f"Bearer {token}"}
    queued = await async_client.post(f"/listings/{listing_id}/assisted-marketplace-jobs", json={"marketplace": "facebook", "action": "CREATE"})
    job_id = queued.json()["id"]
    db = database_module.SessionLocal()
    try:
        parent_id = db.get(MarketplaceExtensionJob, job_id).crosspost_job_id
    finally:
        db.close()
    claim = await async_client.post("/browser-extension/jobs/claim", headers=headers)
    assert claim.json()["job"]["id"] == job_id
    proof = {
        "submission_performed": True,
        "confirmation": "FACEBOOK_PUBLISH_ACTIVATED_ONCE",
        "background_version": "0.3.68",
        "extension_version": "0.3.68",
        "content_script_version": "0.3.68",
        "content_script_build_id": "posterpro-0.3.68-facebook-submit-identity-binding",
    }
    for state, result in (
        ("NAVIGATING", None),
        ("FORM_FILLING", None),
        ("SUBMITTING", {"submission_performed": False}),
        ("SUBMITTED", proof),
        ("SUBMITTED_PENDING_REVIEW", proof),
    ):
        response = await async_client.post(f"/browser-extension/jobs/{job_id}/state", headers=headers, json={"status": state, "result": result})
        assert response.status_code == 200, response.text
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, job_id)
        assert child.status == "SUBMITTED_PENDING_REVIEW"
        assert child.result["submission_performed"] is True
        assert child.external_listing_id is None
        assert child.external_url is None
        assert child.lease_expires_at is None
        assert child.completed_at is not None
    finally:
        db.close()
    no_identity = await async_client.post(f"/browser-extension/jobs/{job_id}/state", headers=headers, json={"status": "AWAITING_OPERATOR_REVIEW", "result": proof})
    assert no_identity.status_code == 409
    assert (await async_client.post("/browser-extension/jobs/claim", headers=headers)).json() == {"job": None}
    resume = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=facebook")
    assert resume.status_code in {409, 422}


@pytest.mark.anyio
async def test_operator_confirmed_facebook_remote_presence_cannot_be_reconciled_away(async_client):
    owner = await _register(async_client, "FacebookKnownPresent")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    _parent_id, child_id = _seed_facebook_unknown_reconciliation(owner["user"]["id"], listing_id, known_remote=True)
    token, _device_id = await _pair(async_client, "Facebook known-present browser")
    headers = {"Authorization": f"Bearer {token}"}
    claim = await async_client.post("/browser-extension/facebook-reconciliation/claim", headers=headers)
    assert claim.status_code == 200
    assert claim.json()["task"]["known_remote_presence"] is True
    title = claim.json()["task"]["payload"]["marketplace_payload"]["title"]
    response = await async_client.post(f"/browser-extension/facebook-reconciliation/{child_id}/result", headers=headers, json={
        "state": "NOT_FOUND", "evidence": {
            "source": "authenticated_facebook_seller_listings",
            "page_url": "https://www.facebook.com/marketplace/you/selling/",
            "authenticated_account_context": True, "exact_search_used": True,
            "exact_title_search": True, "account_search_complete": True,
            "empty_state_verified": True, "result_count": 0, "searched_title": title,
        },
    })
    assert response.status_code == 409
    assert "confirmed remote presence" in response.text


@pytest.mark.anyio
async def test_ambiguous_facebook_match_requires_review_but_does_not_block_other_reconciliation(async_client):
    owner = await _register(async_client, "FacebookReconAmbiguous")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    parent_id, first_child = _seed_facebook_unknown_reconciliation(owner["user"]["id"], listing_id)
    _other_parent_id, second_child = _seed_facebook_unknown_reconciliation(owner["user"]["id"], listing_id)
    token, _device_id = await _pair(async_client, "Facebook ambiguous reconciliation browser")
    headers = {"Authorization": f"Bearer {token}"}
    claim = await async_client.post("/browser-extension/facebook-reconciliation/claim", headers=headers)
    assert claim.status_code == 200
    first_task = claim.json()["task"]
    assert first_task["id"] == first_child
    ambiguous = await async_client.post(f"/browser-extension/facebook-reconciliation/{first_child}/result", headers=headers, json={
        "state": "AMBIGUOUS", "evidence": {
            "source": "authenticated_facebook_seller_listings",
            "page_url": "https://www.facebook.com/marketplace/you/selling/",
            "reason": "MULTIPLE_EXACT_TITLE_PRICE_MATCHES", "candidate_count": 2,
        },
    })
    assert ambiguous.status_code == 200, ambiguous.text
    assert ambiguous.json()["status"] == "SUBMITTED_PENDING_REVIEW"
    assert ambiguous.json()["error_code"] is None
    resume = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=facebook")
    assert resume.status_code == 409
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, first_child)
        assert child.result["remote_reconciliation"]["status"] == "PENDING_REVIEW"
        assert child.result["remote_reconciliation"]["last_observation_status"] == "AMBIGUOUS"
        assert child.result["remote_reconciliation"]["safe_same_child_retry"] is False
        assert child.error_code is None
    finally:
        db.close()
    # The result becomes terminal for this reconciliation claim, so the next
    # independent Facebook child can be claimed without replaying the first.
    next_claim = await async_client.post("/browser-extension/facebook-reconciliation/claim", headers=headers)
    assert next_claim.status_code == 200
    assert next_claim.json()["task"]["id"] != first_child


@pytest.mark.anyio
async def test_post_submit_exact_empty_reconciliation_is_durable_and_requires_guarded_resume(async_client):
    owner = await _register(async_client, "FacebookLostResponseAbsent")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    parent_id, child_id = _seed_facebook_unknown_reconciliation(owner["user"]["id"], listing_id)
    token, device_id = await _pair(async_client, "Facebook lost-response browser")
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, child_id)
        child.status = "SUBMITTING"
        child.device_id = device_id
        child.result = {
            "submission_performed": False,
            "background_version": "0.3.68", "extension_version": "0.3.68",
            "content_script_version": "0.3.68",
            "content_script_build_id": "posterpro-0.3.68-facebook-submit-identity-binding",
        }
        db.commit()
        title = child.payload_snapshot["marketplace_payload"]["title"]
    finally:
        db.close()
    evidence = {
        "status": "NOT_FOUND", "source": "authenticated_facebook_seller_listings",
        "confirmation": "REMOTE_RECONCILIATION_NOT_FOUND",
        "evidence": {
            "page_url": "https://www.facebook.com/marketplace/you/selling/",
            "authenticated_account_context": True, "exact_search_used": True,
            "exact_title_search": True, "account_search_complete": True,
            "empty_state_verified": True, "result_count": 0, "searched_title": title,
        },
    }
    headers = {"Authorization": f"Bearer {token}"}
    failed = await async_client.post(f"/browser-extension/jobs/{child_id}/state", headers=headers, json={
        "status": "FAILED", "error_code": "REMOTE_RECONCILIATION_NOT_FOUND",
        "error_detail": "Complete authenticated seller listing search returned an empty result.",
        "result": {"submission_performed": True, "remote_reconciliation": evidence},
    })
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "SUBMITTED_PENDING_REVIEW"
    assert failed.json()["error_code"] is None
    assert failed.json()["result"]["remote_reconciliation"]["safe_same_child_retry"] is False
    resumed = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=facebook")
    assert resumed.status_code == 409, resumed.text


@pytest.mark.anyio
async def test_facebook_automated_not_found_retry_is_bounded_to_one_same_child_replay(async_client):
    owner = await _register(async_client, "FacebookBoundedRetry")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    parent_id, child_id = _seed_facebook_unknown_reconciliation(owner["user"]["id"], listing_id)
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, child_id)
        first_no_found = {
            "submission_performed": True,
            "remote_reconciliation": {
                "status": "NOT_FOUND", "confirmation": "REMOTE_RECONCILIATION_NOT_FOUND",
                "source": "authenticated_facebook_seller_listings", "verified_by_device_id": 1,
                "safe_same_child_retry": True,
            },
        }
        child.payload_snapshot = {"marketplace_payload": {"title": "Exact reconciliation item", "price": 29.99}, "execution_history": [
            {"status": "FAILED", "error_code": "REMOTE_RECONCILIATION_NOT_FOUND", "result": first_no_found},
        ]}
        child.result = {
            "submission_performed": True,
            "remote_reconciliation": {
                "status": "NOT_FOUND", "confirmation": "REMOTE_RECONCILIATION_NOT_FOUND",
                "source": "authenticated_facebook_seller_listings", "verified_by_device_id": 1,
                "safe_same_child_retry": True,
            },
        }
        child.error_code = "REMOTE_RECONCILIATION_NOT_FOUND"
        db.commit()
    finally:
        db.close()
    response = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=facebook")
    assert response.status_code == 409
    assert "remote or uncertain CREATE outcome" in response.text

@pytest.mark.anyio
async def test_extension_download_serves_current_installable_archive(async_client):
    response = await async_client.get("/browser-extension/download")
    assert response.status_code == 200
    assert "application/zip" in response.headers["content-type"]
    assert response.headers["cache-control"].startswith("no-store")
    assert response.headers["x-posterpro-extension-version"] == "0.3.68"
    assert "posterpro-marketplace-extension-v0.3.68.zip" in response.headers["content-disposition"]
    archive = ZipFile(BytesIO(response.content))
    names = set(archive.namelist())
    assert "posterpro-extension/manifest.json" in names
    assert "posterpro-extension/background.js" in names
    assert "posterpro-extension/content.js" in names
    assert "posterpro-extension/posterpro-link.js" in names
    assert "/opt/apps/" not in "\n".join(names)
    manifest = json.loads(archive.read("posterpro-extension/manifest.json"))
    assert manifest["version"] == "0.3.68"


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
        ("SUBMITTED", {"external_listing_id": "12345", "external_url": "https://www.facebook.com/marketplace/item/12345"}),
        ("COMPLETED", {
            "external_listing_id": "12345",
            "external_url": "https://www.facebook.com/marketplace/item/12345",
            "result": {
                "verified": True,
                "description_verification": {
                    "matches": True,
                    "expected_hash": _facebook_description_fingerprint(payload["description"])[0],
                    "observed_hash": _facebook_description_fingerprint(payload["description"])[0],
                    "expected_length": _facebook_description_fingerprint(payload["description"])[1],
                    "observed_length": _facebook_description_fingerprint(payload["description"])[1],
                },
                "remote_description_verification": {
                    "matches": True,
                    "expected_hash": _facebook_description_fingerprint(payload["description"])[0],
                    "observed_hash": _facebook_description_fingerprint(payload["description"])[0],
                    "expected_length": _facebook_description_fingerprint(payload["description"])[1],
                    "observed_length": _facebook_description_fingerprint(payload["description"])[1],
                },
            },
        }),
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
        assert marketplace_row.marketplace_listing_id == "12345"
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
    assert update.json()["external_listing_id"] == "12345"
    assert (await async_client.post("/browser-extension/jobs/claim", headers={"Authorization": f"Bearer {token}"})).json()["job"]["id"] == update_id
    for state, extra in (("NAVIGATING", {}), ("FORM_FILLING", {}), ("AWAITING_OPERATOR_REVIEW", {}), ("SUBMITTING", {}), ("SUBMITTED", {"external_listing_id": "12345", "external_url": "https://www.facebook.com/marketplace/item/12345"}), ("COMPLETED", {"external_listing_id": "12345", "external_url": "https://www.facebook.com/marketplace/item/12345"})):
        response = await async_client.post(f"/browser-extension/jobs/{update_id}/state", headers={"Authorization": f"Bearer {token}"}, json={"status": state, **extra})
        assert response.status_code == 200
    db = database_module.SessionLocal()
    try:
        marketplace_row = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.facebook).one()
        assert marketplace_row.marketplace_listing_id == "12345"
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
                "external_listing_id": "12345",
                "external_url": "https://www.facebook.com/marketplace/item/12345",
                "result": {"operator_confirmed_end": True},
            },
        )
        assert response.status_code == 200
    db = database_module.SessionLocal()
    try:
        marketplace_row = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.facebook).one()
        assert marketplace_row.marketplace_listing_id == "12345"
        assert marketplace_row.status == MarketplaceListingStatus.DELETED
    finally:
        db.close()
@pytest.mark.anyio
async def test_marketplace_diagnostic_is_tenant_scoped_and_persists_field_results(async_client):
    owner = await _register(async_client, "Diagnostic")
    token, _device_id = await _pair(async_client, "Diagnostic Chrome")

    selected = await async_client.put("/onboarding/selection", json={"marketplaces": ["facebook"]})
    assert selected.status_code == 200
    blocked = await async_client.post("/browser-extension/diagnostics/facebook", json={})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "FACEBOOK_LOCATION_CONFIRMATION_REQUIRED"
    confirmed = await async_client.put("/onboarding/facebook-location", json={"location": "Farmingdale, NY 11735"})
    assert confirmed.status_code == 200

    queued = await async_client.post("/browser-extension/diagnostics/facebook", json={})
    assert queued.status_code == 200
    job = queued.json()
    assert job["action"] == "DIAGNOSTIC"
    assert job["listing_id"] is None
    assert job["payload"]["diagnostic"] is True
    assert job["payload"]["marketplace_payload"]["title"].startswith("PosterPro Safe Form Test")
    assert job["payload"]["marketplace_payload"]["category"] == "Household"
    assert "Household Item" in job["payload"]["marketplace_payload"]["title"]
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
        "background_version": CURRENT_EXTENSION_VERSION,
        "content_script_version": CURRENT_EXTENSION_VERSION,
        "extension_version": CURRENT_EXTENSION_VERSION,
        "content_script_build_id": f"posterpro-{CURRENT_EXTENSION_VERSION}-facebook-submit-identity-binding",
        "cookies": "must not be persisted",
        "password": "must not be persisted",
        "field_results": [{"field": "category", "required": True, "detected": False, "attempted": True, "filled": False, "verified_value": None, "error_code": "FIELD_NOT_FOUND", "selector_diagnostic": {"field": "category", "page_path": "/marketplace/create/item?access_token=private", "selectors_tried": ["select[name=category]"], "visible_option_labels": ["Clothing", "access_token=should-not-leak", "Collectibles"], "controls": [{"tag": "button", "role": "button", "aria_label": "Category", "nearby_label": "Category", "name": "category", "placeholder": "", "option_labels": ["Clothing", "access_token=should-not-leak", "Collectibles"], "value": "private account data"}, {"tag": "input", "aria_label": "owner@example.com", "name": "access_token_secret"}]}}],
    }
    for status in ("NAVIGATING", "FORM_DETECTED", "TESTING_FIELDS", "COMPLETED"):
        response = await async_client.post(
            f"/browser-extension/jobs/{job['id']}/state",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": status, "error_code": "REQUIRED_FIELDS_UNRESOLVED" if status == "COMPLETED" else None, "result": result},
        )
        assert response.status_code == 200
    assert response.json()["status"] == "BLOCKED_EXTERNAL"
    assert response.json()["error_code"] == "REQUIRED_FIELDS_UNRESOLVED"
    latest = await async_client.get("/browser-extension/diagnostics/latest/facebook")
    assert latest.status_code == 200
    assert latest.json()["current_version"] == CURRENT_EXTENSION_VERSION
    assert latest.json()["minimum_version"] == "0.3.59"
    assert latest.json()["result"]["field_results"][0]["error_code"] == "FIELD_NOT_FOUND"
    assert latest.json()["result"]["capability_ready"] is False
    assert latest.json()["result"]["missing_required_fields"] == ["category"]
    assert latest.json()["result"]["verification_stage"] == "FORM_OPENED_FIELD_CHECK_INCOMPLETE"
    assert "cookies" not in latest.json()["result"]
    assert "password" not in latest.json()["result"]
    diagnostic_field = latest.json()["result"]["field_results"][0]["selector_diagnostic"]
    assert diagnostic_field["page_path"] == "/marketplace/create/item"
    assert diagnostic_field["visible_option_labels"] == ["Clothing", "Collectibles"]
    assert diagnostic_field["controls"][0] == {"tag": "button", "role": "button", "aria_label": "Category", "name": "category", "nearby_label": "Category", "option_labels": ["Clothing", "Collectibles"]}
    assert diagnostic_field["controls"][1] == {"tag": "input"}
    history = await async_client.get("/browser-extension/diagnostics/history/facebook")
    assert history.status_code == 200 and len(history.json()) == 1
    assert history.json()[0]["device"]["browser"] == "Chrome"
    assert history.json()[0]["minimum_version"] == "0.3.59"

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
    assert state.json()["current_version"] == CURRENT_EXTENSION_VERSION
    assert state.json()["minimum_version"] == "0.3.59"
    assert device["update_required"] is True
    refreshed = await async_client.post("/browser-extension/heartbeat", headers=headers, json={"browser": "Chrome", "extension_version": "0.3.1"})
    assert refreshed.status_code == 200
    state = await async_client.get("/browser-extension/devices")
    device = next(item for item in state.json()["devices"] if item["id"] == device_id)
    assert device["update_required"] is True
    refreshed = await async_client.post("/browser-extension/heartbeat", headers=headers, json={"browser": "Chrome", "extension_version": CURRENT_EXTENSION_VERSION})
    assert refreshed.status_code == 200
    state = await async_client.get("/browser-extension/devices")
    device = next(item for item in state.json()["devices"] if item["id"] == device_id)
    assert device["update_required"] is False


@pytest.mark.anyio
async def test_pre_submit_extension_update_does_not_let_old_build_claim_live_create(async_client):
    owner = await _register(async_client, "OldBuildCreateGate")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    token, _device_id = await _pair(async_client, "Old build Chrome")
    headers = {"Authorization": f"Bearer {token}"}
    heartbeat = await async_client.post("/browser-extension/heartbeat", headers=headers, json={"browser": "Chrome", "extension_version": "0.3.23"})
    assert heartbeat.status_code == 200
    queued = await async_client.post(f"/listings/{listing_id}/assisted-marketplace-jobs", json={"marketplace": "mercari", "action": "CREATE"})
    assert queued.status_code == 200
    claim = await async_client.post("/browser-extension/jobs/claim", headers=headers)
    assert claim.status_code == 426
    assert claim.json()["detail"]["code"] == "EXTENSION_UPDATE_REQUIRED"


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
async def test_mercari_create_requires_authorized_provider_and_creates_no_extension_job(async_client):
    owner = await _register(async_client, "MercariAutonomousSubmit")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    queued = await async_client.post(
        f"/listings/{listing_id}/assisted-marketplace-jobs",
        json={"marketplace": "mercari", "action": "CREATE"},
    )
    assert queued.status_code == 400
    assert queued.json()["detail"]["code"] == "MERCARI_AUTHORIZED_PROVIDER_REQUIRED"
    db = database_module.SessionLocal()
    try:
        assert db.query(MarketplaceExtensionJob).filter_by(
            listing_id=listing_id, marketplace="mercari", action="CREATE",
        ).count() == 0
        assert db.query(MarketplaceListing).filter_by(
            listing_id=listing_id, marketplace=MarketplaceName.mercari,
        ).count() == 0
    finally:
        db.close()


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
async def test_create_failure_with_explicit_no_submit_evidence_is_not_rewritten_as_unknown(async_client):
    owner = await _register(async_client, "ExplicitNoSubmit")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    token, _device_id = await _pair(async_client, "No-submit evidence device")
    queued = await async_client.post(f"/listings/{listing_id}/assisted-marketplace-jobs", json={"marketplace": "facebook", "action": "CREATE"})
    job_id = queued.json()["id"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await async_client.post("/browser-extension/jobs/claim", headers=headers)).json()["job"]["id"] == job_id
    for status in ("NAVIGATING", "FORM_FILLING"):
        assert (await async_client.post(f"/browser-extension/jobs/{job_id}/state", headers=headers, json={"status": status})).status_code == 200
    assert (await async_client.post(f"/browser-extension/jobs/{job_id}/state", headers=headers, json={"status": "SUBMITTING", "result": {"submission_performed": False, "submit_guard": "all_required_fields_verified"}})).status_code == 200
    failed = await async_client.post(f"/browser-extension/jobs/{job_id}/state", headers=headers, json={
        "status": "FAILED", "error_code": "FACEBOOK_REQUIRED_DELIVERY_CONFIGURATION",
        "error_detail": "No final submit was attempted.",
        "result": {"submission_performed": False, "error_code": "FACEBOOK_REQUIRED_DELIVERY_CONFIGURATION"},
    })
    assert failed.status_code == 200, failed.text
    assert failed.json()["error_code"] == "FACEBOOK_REQUIRED_DELIVERY_CONFIGURATION"
    assert failed.json()["result"]["submission_performed"] is False


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
        lambda _self, _db, _listing, marketplace: {"marketplace": marketplace, "status": "ready", "blockers": [], "warnings": []},
    )
    result = tasks.process_marketplace_crosspost_job_task.run(parent_id)
    assert result["status"] == "running", result["results"][0].get("response")
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


@pytest.mark.anyio
async def test_crosspost_details_expose_independent_provider_and_extension_failures(async_client):
    owner = await _register(async_client, "CrosspostDetailEvidence")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(
            user_id=owner["user"]["id"], listing_id=listing_id,
            target_marketplaces=["ebay", "facebook", "mercari"],
            status="awaiting_operator_review",
            result_summary={"results": [{
                "marketplace": "ebay", "execution_mode": "direct_api", "operation": "CREATE", "status": "failed",
                "response": {"error_code": "EBAY_SELLER_RESTRICTED", "provider_error_id": 25019,
                             "error": "Seller account restriction", "provider_endpoint": "/sell/inventory/v1/offer/publish",
                             "provider_call_attempted": True, "remote_outcome": "NOT_PUBLISHED"},
            }]},
        )
        db.add(parent)
        db.flush()
        db.add(MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="facebook", action="CREATE", status="AWAITING_OPERATOR_REVIEW",
            result={"stage": "BLOCKED_BEFORE_SUBMIT", "error_code": "REQUIRED_FIELDS_UNRESOLVED",
                    "error_detail": "location", "submission_performed": False},
        ))
        db.add(MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="mercari", action="CREATE", status="QUEUED", attempt_count=0,
        ))
        db.commit()
        parent_id = parent.id
    finally:
        db.close()

    response = await async_client.get(f"/marketplace-crosspost-jobs/{parent_id}")
    assert response.status_code == 200
    targets = {item["marketplace"]: item for item in response.json()["destination_operations"]}
    assert targets["ebay"]["status"] == "BLOCKED_EXTERNAL"
    assert targets["ebay"]["error_code"] == "EBAY_SELLER_RESTRICTED"
    assert targets["ebay"]["failure_stage"] == "/sell/inventory/v1/offer/publish"
    assert targets["ebay"]["provider_error_id"] == 25019
    assert targets["ebay"]["attempt_count"] == 1
    assert targets["facebook"]["status"] == "AWAITING_OPERATOR_REVIEW"
    assert targets["facebook"]["error_code"] == "REQUIRED_FIELDS_UNRESOLVED"
    assert targets["facebook"]["failure_stage"] == "BLOCKED_BEFORE_SUBMIT"
    assert targets["facebook"]["submission_performed"] is False
    assert targets["mercari"]["status"] == "QUEUED"
    assert targets["mercari"]["attempt_count"] == 0


@pytest.mark.anyio
async def test_resume_unsubmitted_crosspost_reuses_children_and_never_requeues_ebay(async_client):
    owner = await _register(async_client, "ResumeJob1043Shape")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(
            user_id=owner["user"]["id"], listing_id=listing_id,
            target_marketplaces=["facebook", "mercari", "ebay"], status="awaiting_operator_review",
            execution_plan={"operation": "create", "operator_live_confirmed": True},
        )
        db.add(parent)
        db.flush()
        facebook = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="facebook", action="CREATE", status="AWAITING_OPERATOR_REVIEW", attempt_count=1,
            result={"submission_performed": False, "error_code": "REQUIRED_FIELDS_UNRESOLVED", "stage": "BLOCKED_BEFORE_SUBMIT"},
        )
        mercari = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="mercari", action="CREATE", status="QUEUED", attempt_count=0,
        )
        db.add_all([facebook, mercari])
        db.commit()
        parent_id, facebook_id, mercari_id = parent.id, facebook.id, mercari.id
    finally:
        db.close()

    response = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=facebook")
    assert response.status_code == 200, response.text
    rows = {item["marketplace"]: item for item in response.json()["assisted_jobs"]}
    assert rows["facebook"]["id"] == facebook_id and rows["facebook"]["status"] == "QUEUED"
    db = database_module.SessionLocal()
    try:
        assert db.get(MarketplaceExtensionJob, mercari_id).status == "QUEUED"
        parent = db.get(MarketplaceCrosspostJob, parent_id)
        assert parent.execution_plan["resume_history"][-1]["resumed_extension_job_ids"] == [facebook_id]
    finally:
        db.close()

    # Replaying a queued child with prior attempt evidence is not necessary for
    # the stale recovery path and remains fail-closed against duplicate CREATE.
    response = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted")
    assert response.status_code == 409
    assert "prior execution evidence" in response.json()["detail"]


@pytest.mark.anyio
async def test_resume_unsubmitted_crosspost_refuses_uncertain_remote_create(async_client):
    owner = await _register(async_client, "ResumeUnknownCreate")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(user_id=owner["user"]["id"], listing_id=listing_id, target_marketplaces=["facebook"], status="failed", execution_plan={"operation": "create"})
        db.add(parent)
        db.flush()
        child = MarketplaceExtensionJob(user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id, marketplace="facebook", action="CREATE", status="FAILED", attempt_count=1, error_code="SUBMISSION_OUTCOME_UNKNOWN", result={"submission_performed": True})
        db.add(child)
        db.commit()
        parent_id, child_id = parent.id, child.id
    finally:
        db.close()

    response = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted")
    assert response.status_code == 409
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, child_id)
        assert child.status == "FAILED"
        assert child.error_code == "SUBMISSION_OUTCOME_UNKNOWN"
    finally:
        db.close()

    response = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted")
    assert response.status_code == 409
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, child_id)
        assert child.status == "FAILED"
        assert child.error_code == "SUBMISSION_OUTCOME_UNKNOWN"
    finally:
        db.close()


@pytest.mark.anyio
async def test_operator_confirmed_remote_not_found_is_durable_before_same_child_retry(async_client):
    owner = await _register(async_client, "ResumeAfterReconciliation")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(
            user_id=owner["user"]["id"], listing_id=listing_id, target_marketplaces=["mercari"],
            status="awaiting_operator_review", execution_plan={"operation": "create", "operator_live_confirmed": True},
        )
        db.add(parent); db.flush()
        child = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="mercari", action="CREATE", status="FAILED", attempt_count=1,
            error_code="SUBMISSION_OUTCOME_UNKNOWN", result={"submission_performed": True},
        )
        db.add(child); db.commit()
        parent_id, child_id = parent.id, child.id
    finally:
        db.close()

    reconciled = await async_client.post(
        f"/marketplace-crosspost-jobs/{parent_id}/destinations/mercari/reconcile-not-found",
        json={"confirmation": "REMOTE_RECONCILIATION_NOT_FOUND", "evidence_summary": "Checked authenticated Mercari active listings; no title or photo match found."},
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["extension_job_id"] == child_id
    assert reconciled.json()["retry_authorized_by_original_job"] is True

    resumed = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=mercari")
    assert resumed.status_code == 200, resumed.text
    child_state = next(item for item in resumed.json()["assisted_jobs"] if item["id"] == child_id)
    assert child_state["status"] == "QUEUED"
    db = database_module.SessionLocal()
    try:
        child = db.get(MarketplaceExtensionJob, child_id)
        prior = child.payload_snapshot["execution_history"][-1]
        assert prior["result"]["submission_performed"] is True
        assert prior["result"]["remote_reconciliation"]["status"] == "NOT_FOUND"
    finally:
        db.close()


@pytest.mark.anyio
async def test_resume_unsubmitted_crosspost_trusts_explicit_no_submit_result_even_if_old_error_code_was_unknown(async_client):
    owner = await _register(async_client, "ResumeExplicitNoSubmit")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(user_id=owner["user"]["id"], listing_id=listing_id, target_marketplaces=["facebook"], status="failed", execution_plan={"operation": "create", "operator_live_confirmed": True})
        db.add(parent)
        db.flush()
        child = MarketplaceExtensionJob(user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id, marketplace="facebook", action="CREATE", status="FAILED", attempt_count=1, error_code="SUBMISSION_OUTCOME_UNKNOWN", result={"submission_performed": False, "error_code": "FINAL_PUBLISH_BOUNDARY_NOT_REACHED"})
        db.add(child)
        db.commit()
        parent_id, child_id = parent.id, child.id
    finally:
        db.close()
    response = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted")
    assert response.status_code == 200, response.text
    child_result = next(item for item in response.json()["assisted_jobs"] if item["id"] == child_id)
    assert child_result["status"] == "QUEUED"


@pytest.mark.anyio
async def test_destination_scoped_resume_keeps_unknown_sibling_untouched(async_client):
    owner = await _register(async_client, "ResumeOneDestinationOnly")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(
            user_id=owner["user"]["id"], listing_id=listing_id,
            target_marketplaces=["facebook", "mercari", "ebay"], status="failed",
            execution_plan={"operation": "create", "operator_live_confirmed": True},
        )
        db.add(parent)
        db.flush()
        facebook = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="facebook", action="CREATE", status="FAILED", attempt_count=2,
            error_code="FACEBOOK_SHIPPING_SERVICE_NOT_RENDERED", result={"submission_performed": False},
        )
        mercari = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="mercari", action="CREATE", status="FAILED", attempt_count=2,
            error_code="SUBMISSION_OUTCOME_UNKNOWN", result={"submission_performed": True},
        )
        db.add_all([facebook, mercari])
        db.commit()
        parent_id, facebook_id, mercari_id = parent.id, facebook.id, mercari.id
    finally:
        db.close()

    response = await async_client.post(f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=facebook")
    assert response.status_code == 200, response.text
    resumed = [item for item in response.json()["assisted_jobs"] if item["id"] == facebook_id]
    assert len(resumed) == 1 and resumed[0]["status"] == "QUEUED"
    db = database_module.SessionLocal()
    try:
        assert db.get(MarketplaceExtensionJob, mercari_id).status == "FAILED"
        assert db.get(MarketplaceExtensionJob, mercari_id).result["submission_performed"] is True
        parent = db.get(MarketplaceCrosspostJob, parent_id)
        assert parent.execution_plan["resume_history"][-1]["resumed_extension_job_ids"] == [facebook_id]
    finally:
        db.close()


@pytest.mark.anyio
async def test_recent_explicit_resume_claims_same_stale_child_without_rewriting_created_at(async_client):
    owner = await _register(async_client, "ResumeStaleFacebook")
    token, _device_id = await _pair(async_client, "Resume Stale Facebook Device")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    old_created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=30)
    db = database_module.SessionLocal()
    try:
        parent = MarketplaceCrosspostJob(
            user_id=owner["user"]["id"], listing_id=listing_id,
            target_marketplaces=["facebook", "mercari"], status="failed",
            execution_plan={"operation": "create", "operator_live_confirmed": True},
        )
        db.add(parent)
        db.flush()
        facebook = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="facebook", action="CREATE", status="CANCELLED", attempt_count=25,
            created_at=old_created_at, error_code="STALE_JOB_SUPPRESSED",
            result={"submission_performed": False},
        )
        mercari = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="mercari", action="CREATE", status="QUEUED", attempt_count=0,
            created_at=old_created_at,
        )
        db.add_all([facebook, mercari])
        db.commit()
        parent_id, facebook_id, mercari_id = parent.id, facebook.id, mercari.id
    finally:
        db.close()

    resumed = await async_client.post(
        f"/marketplace-crosspost-jobs/{parent_id}/resume-unsubmitted?marketplace=facebook"
    )
    assert resumed.status_code == 200, resumed.text
    assert next(row for row in resumed.json()["assisted_jobs"] if row["id"] == facebook_id)["status"] == "QUEUED"
    claimed = await async_client.post(
        "/browser-extension/jobs/claim", headers={"Authorization": f"Bearer {token}"}
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["job"]["id"] == facebook_id

    db = database_module.SessionLocal()
    try:
        facebook = db.get(MarketplaceExtensionJob, facebook_id)
        mercari = db.get(MarketplaceExtensionJob, mercari_id)
        assert facebook.status == "CLAIMED"
        assert facebook.attempt_count == 26
        assert facebook.created_at == old_created_at
        assert mercari.status == "CANCELLED"
        assert mercari.error_code == "STALE_JOB_SUPPRESSED"
    finally:
        db.close()

def test_facebook_extension_payload_carries_canonical_description_and_existing_shipping_choice(db_session):
    owner = User(email=f"shipping-{uuid4()}@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(
        user_id=owner.id, title="Reflex Game", description="Legacy compatibility description.",
        canonical_description="Authoritative canonical Facebook description with all current details.",
        marketplace_descriptions={"facebook": "Stale generated short variant."},
        listing_price=29.99, condition="New", category_suggestion="Toys & Games",
        marketplace_data={"shipping": {"mode": "included", "domestic_service": "usps_ground_advantage", "free_shipping": True, "buyer_pays_shipping": False, "local_pickup_enabled": False, "handling_time_days": 2}},
        shipping_profile={"package_weight": 2.0, "package_dimensions": {"length": 12, "width": 10, "height": 6}, "estimated": True, "estimated_fields": ["package_weight", "package_dimensions"], "shipping_notes": "Verify the packed item before final publish."},
    )
    db_session.add(listing)
    db_session.flush()
    payload = build_extension_marketplace_payload(db_session, user=owner, listing=listing, marketplace="facebook", action="CREATE")
    assert payload["delivery_method"] == "shipping"
    assert payload["shipping"]["domestic_service"] == "usps_ground_advantage"
    assert payload["shipping"]["free_shipping"] is True
    assert payload["shipping"]["estimated"] is True
    assert payload["shipping"]["estimated_fields"] == ["package_weight", "package_dimensions"]
    assert payload["price"] == 29.99
    assert payload["description"] == listing.canonical_description
    assert payload["description_source"] == "canonical_description"
    assert payload["facebook_offers_enabled"] is False
    assert "minimum_offer_price" not in payload


def test_facebook_extension_payload_requires_explicit_offer_opt_in_and_separate_floor(db_session):
    owner = User(email=f"offers-{uuid4()}@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(
        user_id=owner.id, title="Reflex Game", description="A reflex game with verified product details.",
        listing_price=29.99, min_acceptable_offer=24.0, condition="New", category_suggestion="Toys & Games",
        marketplace_data={"channels": {"facebook": {"accept_offers": True, "minimum_offer_price": 24.0}}},
    )
    db_session.add(listing)
    db_session.flush()
    payload = build_extension_marketplace_payload(db_session, user=owner, listing=listing, marketplace="facebook", action="CREATE")
    assert payload["price"] == 29.99
    assert payload["facebook_offers_enabled"] is True
    assert payload["minimum_offer_price"] == 24.0


def test_verified_facebook_description_repair_queues_idempotent_update_only(db_session):
    owner = User(email=f"description-repair-{uuid4()}@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(
        user_id=owner.id, title="Blank description remote item", description="Legacy fallback.",
        canonical_description="Canonical complete product description for the exact remote listing.",
        listing_price=29.99, condition="New", quantity=1,
        image_urls=["https://media.example.test/existing.jpg"], category_suggestion="Home",
    )
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(
        id=FACEBOOK_DESCRIPTION_REPAIR_BATCH_ID, user_id=owner.id, selected_count=1,
        selected_listing_ids=[listing.id], requested_marketplaces=["facebook"], status="paused", summary={"items": []},
    )
    parent = MarketplaceCrosspostJob(
        user_id=owner.id, listing_id=listing.id, source_marketplace="ebay",
        target_marketplaces=["facebook"], status="awaiting_operator_review",
        batch_id=FACEBOOK_DESCRIPTION_REPAIR_BATCH_ID,
    )
    db_session.add_all([batch, parent])
    db_session.flush()
    remote_id = "fb-description-123"
    remote_url = f"https://www.facebook.com/marketplace/item/{remote_id}/"
    remote = MarketplaceListing(
        listing_id=listing.id, marketplace=MarketplaceName.facebook,
        marketplace_listing_id=remote_id, status=MarketplaceListingStatus.PUBLISHED,
        raw_response={"external_url": remote_url},
    )
    create = MarketplaceExtensionJob(
        user_id=owner.id, listing_id=listing.id, crosspost_job_id=parent.id,
        marketplace="facebook", action="CREATE", status="AWAITING_OPERATOR_REVIEW",
        error_code="FACEBOOK_DESCRIPTION_REPAIR_REQUIRED", external_listing_id=remote_id,
        external_url=remote_url,
        payload_snapshot={"marketplace_payload": {"title": listing.title, "price": 29.99, "description": listing.canonical_description}},
        result={"submission_performed": True, "remote_exists_verified": True},
    )
    db_session.add_all([remote, create])
    db_session.flush()

    repair = _queue_facebook_description_repair(db_session, create, listing, parent)
    assert repair is not None
    assert repair.action == "UPDATE"
    assert repair.external_listing_id == remote_id
    assert repair.external_url == remote_url
    repair_payload = repair.payload_snapshot["marketplace_payload"]
    assert repair_payload["repair_scope"] == "facebook_description_only"
    assert repair_payload["description"] == listing.canonical_description
    assert repair_payload["title"] == listing.title
    assert repair_payload["price"] == 29.99
    assert repair_payload["repair_expected_remote_id"] == remote_id
    assert repair_payload["repair_expected_remote_url"] == remote_url
    assert remote.status == MarketplaceListingStatus.PUBLISHED
    assert remote.marketplace_listing_id == remote_id

    again = _queue_facebook_description_repair(db_session, create, listing, parent)
    assert again.id == repair.id
    assert db_session.query(MarketplaceExtensionJob).filter_by(listing_id=listing.id, action="CREATE").count() == 1
    assert db_session.query(MarketplaceExtensionJob).filter_by(listing_id=listing.id, action="UPDATE").count() == 1

    ambiguous_listing = Listing(
        user_id=owner.id, title="Ambiguous remote", description="Canonical description.",
        canonical_description="Canonical description.", listing_price=18.0,
    )
    db_session.add(ambiguous_listing)
    db_session.flush()
    batch.selected_listing_ids = [listing.id, ambiguous_listing.id]
    ambiguous_parent = MarketplaceCrosspostJob(
        user_id=owner.id, listing_id=ambiguous_listing.id, target_marketplaces=["facebook"],
        status="awaiting_operator_review", batch_id=FACEBOOK_DESCRIPTION_REPAIR_BATCH_ID,
    )
    ambiguous_remote = MarketplaceListing(
        listing_id=ambiguous_listing.id, marketplace=MarketplaceName.facebook,
        marketplace_listing_id="fb-known-id", status=MarketplaceListingStatus.PUBLISHED,
        raw_response={"external_url": "https://www.facebook.com/marketplace/item/fb-different-id/"},
    )
    ambiguous_create = MarketplaceExtensionJob(
        user_id=owner.id, listing_id=ambiguous_listing.id, crosspost_job_id=ambiguous_parent.id,
        marketplace="facebook", action="CREATE", status="AWAITING_OPERATOR_REVIEW",
        error_code="FACEBOOK_DESCRIPTION_REPAIR_REQUIRED", external_listing_id="fb-known-id",
        external_url="https://www.facebook.com/marketplace/item/fb-known-id/",
        payload_snapshot={"marketplace_payload": {"title": ambiguous_listing.title, "price": 18.0, "description": ambiguous_listing.canonical_description}},
    )
    db_session.add_all([ambiguous_parent, ambiguous_remote, ambiguous_create])
    db_session.flush()
    assert _queue_facebook_description_repair(db_session, ambiguous_create, ambiguous_listing, ambiguous_parent) is None
    assert db_session.query(MarketplaceExtensionJob).filter_by(listing_id=ambiguous_listing.id, action="UPDATE").count() == 0


@pytest.mark.anyio
async def test_facebook_description_repair_completion_requires_same_published_identity_and_remote_proof(async_client):
    owner = await _register(async_client, "FacebookDescriptionRepairCompletion")
    token, device_id = await _pair(async_client, "Description repair Chrome")
    listing_id = _seed_marketplace_listing(owner["user"]["id"])
    remote_id = "fb-repair-complete-77"
    remote_url = f"https://www.facebook.com/marketplace/item/{remote_id}/"
    canonical = "Canonical description required on the same Facebook listing."
    db = database_module.SessionLocal()
    try:
        listing = db.get(Listing, listing_id)
        listing.canonical_description = canonical
        batch = MarketplacePublishBatch(
            id=FACEBOOK_DESCRIPTION_REPAIR_BATCH_ID, user_id=owner["user"]["id"],
            selected_count=1, selected_listing_ids=[listing_id], requested_marketplaces=["facebook"],
            status="paused", summary={"items": [{"listing_id": listing_id, "extension_job_id": -1, "status": "DESCRIPTION_REPAIR_REQUIRED"}]},
        )
        parent = MarketplaceCrosspostJob(
            user_id=owner["user"]["id"], listing_id=listing_id, target_marketplaces=["facebook"],
            status="awaiting_operator_review", batch_id=FACEBOOK_DESCRIPTION_REPAIR_BATCH_ID,
        )
        db.add_all([batch, parent])
        db.flush()
        create = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="facebook", action="CREATE", status="AWAITING_OPERATOR_REVIEW",
            error_code="FACEBOOK_DESCRIPTION_REPAIR_REQUIRED", external_listing_id=remote_id,
            external_url=remote_url, payload_snapshot={"marketplace_payload": {"title": "Test item", "price": 25.0, "description": canonical}},
            result={"submission_performed": True, "remote_exists_verified": True, "remote_reconciliation": {"status": "FOUND_DESCRIPTION_REPAIR_REQUIRED"}},
        )
        update = MarketplaceExtensionJob(
            user_id=owner["user"]["id"], listing_id=listing_id, crosspost_job_id=parent.id,
            marketplace="facebook", action="UPDATE", status="SUBMITTING",
            device_id=device_id,
            external_listing_id=remote_id, external_url=remote_url,
            payload_snapshot={
                "description_repair": {},
                "marketplace_payload": {
                    "repair_scope": "facebook_description_only", "repair_expected_remote_id": remote_id,
                    "repair_expected_remote_url": remote_url, "repair_expected_title": "Test jacket",
                    "repair_expected_price": 35.0, "title": "Test jacket", "price": 35.0,
                    "description": canonical,
                },
            },
        )
        remote = MarketplaceListing(
            listing_id=listing_id, marketplace=MarketplaceName.facebook,
            marketplace_listing_id=remote_id, status=MarketplaceListingStatus.PUBLISHED,
            raw_response={"external_url": remote_url},
        )
        db.add_all([create, update, remote])
        db.flush()
        update.payload_snapshot = {
            **update.payload_snapshot,
            "description_repair": {"source_create_job_id": create.id},
        }
        db.add(update)
        batch.summary = {
            **batch.summary,
            "items": [{"listing_id": listing_id, "extension_job_id": create.id, "status": "DESCRIPTION_REPAIR_REQUIRED"}],
        }
        db.commit()
        update_id, create_id = update.id, create.id
    finally:
        db.close()

    expected_hash, expected_length = _facebook_description_fingerprint(canonical)
    good_result = {
        "description_only": True, "save_activation_count": 1,
        "external_listing_id": remote_id, "external_url": remote_url,
        "title_unchanged": True, "price_unchanged": True,
        "post_save_reconciliation": {
            "state": "FOUND", "exact_title_match": True, "exact_price_match": True,
            "observed_title": "Test jacket", "observed_price": "35.00",
        },
        "remote_description_verification": {
            "matches": True, "expected_hash": expected_hash, "observed_hash": expected_hash,
            "expected_length": expected_length, "observed_length": expected_length,
        },
    }
    headers = {"Authorization": f"Bearer {token}"}
    rejected = await async_client.post(
        f"/browser-extension/jobs/{update_id}/state", headers=headers,
        json={"status": "COMPLETED", "external_listing_id": remote_id, "external_url": remote_url,
              "result": {**good_result, "save_activation_count": 2}},
    )
    assert rejected.status_code == 422
    changed_identity = await async_client.post(
        f"/browser-extension/jobs/{update_id}/state", headers=headers,
        json={"status": "COMPLETED", "external_listing_id": remote_id, "external_url": remote_url,
              "result": {**good_result, "post_save_reconciliation": {**good_result["post_save_reconciliation"], "observed_title": "Different title"}}},
    )
    assert changed_identity.status_code == 422

    response = await async_client.post(
        f"/browser-extension/jobs/{update_id}/state", headers=headers,
        json={"status": "COMPLETED", "external_listing_id": remote_id, "external_url": remote_url, "result": good_result},
    )
    assert response.status_code == 200, response.text
    db = database_module.SessionLocal()
    try:
        repair = db.get(MarketplaceExtensionJob, update_id)
        create = db.get(MarketplaceExtensionJob, create_id)
        remote = db.query(MarketplaceListing).filter_by(listing_id=listing_id, marketplace=MarketplaceName.facebook).one()
        batch = db.get(MarketplacePublishBatch, FACEBOOK_DESCRIPTION_REPAIR_BATCH_ID)
        assert repair.status == "COMPLETED"
        assert repair.action == "UPDATE"
        assert repair.external_listing_id == remote_id and repair.external_url == remote_url
        assert create.status == "COMPLETED" and create.external_listing_id == remote_id and create.external_url == remote_url
        assert remote.status == MarketplaceListingStatus.PUBLISHED
        assert remote.marketplace_listing_id == remote_id and remote.raw_response["external_url"] == remote_url
        assert batch.summary["items"][0]["status"] == "SUCCESS"
        assert db.query(MarketplaceExtensionJob).filter_by(listing_id=listing_id, action="CREATE").count() == 1
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


def test_partial_sale_queues_exact_quantity_updates_for_other_live_destinations(db_session, monkeypatch):
    owner = User(email=f"partial-sale-{uuid4()}@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(
        user_id=owner.id,
        title="Two unit item",
        description="Detailed test item description.",
        listing_price=20.0,
        condition="New",
        quantity=2,
        platform_quantities={"ebay": 2, "mercari": 2},
    )
    db_session.add(listing)
    db_session.flush()
    db_session.add_all([
        MarketplaceListing(listing_id=listing.id, marketplace=MarketplaceName.ebay, marketplace_listing_id="E-789", status=MarketplaceListingStatus.PUBLISHED),
        MarketplaceListing(listing_id=listing.id, marketplace=MarketplaceName.mercari, marketplace_listing_id="M-789", status=MarketplaceListingStatus.PUBLISHED),
    ])
    db_session.commit()

    class DummyTask:
        id = "quantity-task-789"

    monkeypatch.setattr("app.api.marketplace_jobs._enqueue_priority", lambda *_args, **_kwargs: DummyTask())
    result = asyncio.run(SaleDetectionService()._fanout_quantity_adjustment(db_session, listing, owner, sold_platform="ebay", quantity_sold=1, dry_run=False))
    db_session.commit()

    job = db_session.query(MarketplaceCrosspostJob).filter_by(listing_id=listing.id, requested_mode="sale_reconciliation_quantity").one()
    assert job.target_marketplaces == ["mercari"]
    assert job.execution_plan["operation"] == "update"
    assert job.execution_plan["changed_fields"]["quantity"] == {"before": 2, "after": 1}
    assert result["mercari"]["response"]["status"] == "QUEUED_QUANTITY_UPDATE"
    assert listing.quantity == 1
    assert listing.platform_quantities["mercari"] == 1


def test_standard_publish_worker_queues_browser_assist_in_durable_transport(monkeypatch, db_session):
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
            canonical_description="Detailed canonical item description for the standard browser worker test.",
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
