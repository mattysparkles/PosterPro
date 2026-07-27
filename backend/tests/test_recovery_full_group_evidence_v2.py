from pathlib import Path

import pytest

from app.models.models import Listing, MediaRecoveryItemGroup, MediaRecoveryMedia, MediaRecoveryPhotoEvidence, MediaRecoveryRun, User
from scripts.reprocess_recovery_validation_sample_v2 import _upsert_photo_evidence
from app.services.media_recovery import MediaRecoveryService
from app.services.photo_enrichment import FULL_GROUP_EVIDENCE_PIPELINE_VERSION, PHOTO_EVIDENCE_PIPELINE_VERSION, PhotoEnrichmentService, quality_gate


def evidence(media_id, **values):
    return {"media_id": media_id, "photo_role": "alternate_product_view", "barcode_attempts": [], "specifications": {}, "included_components": [], "damage": [], "confidence": .5, "extraction_method": "test", **values}


def test_every_photo_contributes_to_synthesis():
    service = PhotoEnrichmentService()
    output = service.synthesize_group_evidence([evidence(1, product_name="Widget"), evidence(2, brand="Acme", product_type="Widget")])
    assert output["usable_media_ids"] == [1, 2]


def test_upc_in_final_photo_overrides_first_visual_guess():
    output = PhotoEnrichmentService().synthesize_group_evidence([
        evidence(1, visual_title="Wrong visual guess", product_name="Wrong visual guess"),
        evidence(2, decoded_barcode_value="012345678905", decoded_barcode_type="UPC", upc="012345678905", product_name="Verified package"),
    ])
    assert output["identity"]["identifier"] == "012345678905"
    assert output["identity"]["title"] == "Verified package"


def test_later_model_label_outweighs_visual_guess():
    output = PhotoEnrichmentService().synthesize_group_evidence([evidence(1, visual_title="Unknown tool"), evidence(2, model="P5231", mpn="P5231", product_name="RYOBI tool")])
    assert output["identity"]["model"] == "P5231"


def test_conflicting_barcodes_or_brands_marks_mixed():
    service = PhotoEnrichmentService()
    assert service.synthesize_group_evidence([evidence(1, decoded_barcode_value="012345678905"), evidence(2, decoded_barcode_value="012345678912")])["group_kind"] == "multiple_unrelated_products"
    assert service.synthesize_group_evidence([evidence(1, brand="Acme"), evidence(2, brand="Other")])["group_kind"] == "multiple_unrelated_products"


def test_intentional_set_and_alternate_views_remain_one_group():
    output = PhotoEnrichmentService().synthesize_group_evidence([evidence(1, brand="Acme", intentional_set=True, product_name="Tool set"), evidence(2, brand="Other", intentional_set=True, product_name="Tool set")])
    assert output["group_kind"] == "intentional_set"


def test_placeholder_facts_block_trusted_status():
    result = {"group_kind": "one_item", "usable_media_ids": [1], "identity": {"title": "Widget"}, "identity_confidence": .9, "placeholders": ["price", "weight"], "review_flags": []}
    assert quality_gate(result) == "blocked_placeholder_data"


def test_photo_evidence_upsert_is_idempotent(db_session):
    user = User(email="evidence@example.com"); db_session.add(user); db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="evidence-run", pipeline_version="test"); db_session.add(run); db_session.flush()
    group = MediaRecoveryItemGroup(run_id=run.id, recovery_item_id="REC-1", grouping_status="confirmed", media_paths_json=[]); db_session.add(group)
    media = MediaRecoveryMedia(run_id=run.id, absolute_path="/tmp/a.jpg", relative_path="a.jpg", sha256="a" * 64); db_session.add(media)
    listing = Listing(user_id=user.id, title="Existing", source_type="media_inventory_recovery"); db_session.add(listing); db_session.flush()
    payload = evidence(media.id, product_name="Widget")
    _upsert_photo_evidence(db_session, run=run, group=group, listing=listing, media=media, payload=payload)
    db_session.flush()
    _upsert_photo_evidence(db_session, run=run, group=group, listing=listing, media=media, payload=payload)
    db_session.commit()
    assert db_session.query(MediaRecoveryPhotoEvidence).count() == 1


def test_recovery_freeze_does_not_block_normal_listing(db_session):
    user = User(email="ordinary@example.com"); db_session.add(user); db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="frozen-run", pipeline_version="test", draft_creation_state="frozen_for_quality_audit"); db_session.add(run); db_session.flush()
    group = MediaRecoveryItemGroup(run_id=run.id, recovery_item_id="REC-2", grouping_status="confirmed", media_paths_json=[]); db_session.add(group); db_session.flush()
    db_session.add(Listing(user_id=user.id, title="Ordinary non-recovery draft", source_type="manual")); db_session.commit()
    with pytest.raises(RuntimeError):
        MediaRecoveryService().create_draft(db_session, user=user, group=group, facts={})
    assert db_session.query(Listing).filter_by(source_type="manual").count() == 1
