from unittest.mock import Mock

from app.models.models import (
    CanonicalItem,
    IntakePhoto,
    IntakePhotoBatch,
    IntakeSlate,
    IntakeSlateRecoveryCandidate,
    Listing,
    SlateObservation,
    User,
)
from app.services.intake_slate import IntakeSlateService, SLATE_RECOVERY_PIPELINE_VERSION, SLATE_RECOVERY_PIPELINE_VERSION_V2


def _user(db_session, email="slate-recovery@example.com"):
    user = User(email=email)
    db_session.add(user)
    db_session.commit()
    return user


def _photo(db_session, user, *, source_id="recovery-photo", **kwargs):
    photo = IntakePhoto(
        user_id=user.id,
        source_provider="google_photos",
        source_photo_id=source_id,
        local_path="/tmp/recovery.jpg",
        **kwargs,
    )
    db_session.add(photo)
    db_session.commit()
    return photo


def test_confirmed_qr_candidate_matches_existing_records_without_mutating_assignments(db_session, monkeypatch):
    user = _user(db_session)
    listing = Listing(user_id=user.id, title="Existing draft")
    db_session.add(listing)
    db_session.flush()
    slate = IntakeSlate(user_id=user.id, item_id="SP-20260708-0006", listing_id=listing.id)
    db_session.add(slate)
    db_session.flush()
    batch = IntakePhotoBatch(user_id=user.id, item_id=slate.item_id, slate_id=slate.id, draft_listing_id=listing.id)
    canonical = CanonicalItem(user_id=user.id, item_id=slate.item_id, current_slate_id=slate.id, current_listing_id=listing.id)
    db_session.add_all([batch, canonical])
    db_session.commit()
    photo = _photo(
        db_session, user, source_id="confirmed", is_slate=True, item_id="UNCHANGED-ITEM", batch_id=batch.id,
        metadata_json={"qr_payload": {"item_id": "SP-20260708-0006", "box_id": "BX-1", "location": "A-1", "quantity": 2, "condition": "Used", "notes": "stored"}},
    )
    service = IntakeSlateService()
    listing_mutation = Mock(side_effect=AssertionError("listing mutation called"))
    draft_creation = Mock(side_effect=AssertionError("draft creation called"))
    monkeypatch.setattr(service, "_create_or_update_listing_from_batch", listing_mutation)
    monkeypatch.setattr(service, "create_drafts_for_ready_batches", draft_creation)

    result = service.run_slate_recovery_candidates(db_session, user_id=user.id, photo_ids=[photo.id])
    candidate = db_session.query(IntakeSlateRecoveryCandidate).one()
    db_session.refresh(photo)

    assert result == {"evaluated": 1, "confirmed_slates": 1, "probable_slates": 0, "not_slates": 0, "unresolved": 0, "valid_item_ids": 1, "invalid_item_ids": 0, "exact_existing_item_matches": 1, "no_matches": 0, "errors": 0}
    assert candidate.classification == "confirmed_slate"
    assert candidate.normalized_item_id == slate.item_id
    assert candidate.matched_canonical_item_id == canonical.id
    assert candidate.matched_intake_slate_id == slate.id
    assert candidate.matched_batch_id == batch.id
    assert candidate.box_id == "BX-1" and candidate.quantity == "2"
    assert photo.item_id == "UNCHANGED-ITEM" and photo.batch_id == batch.id and photo.is_slate is True
    assert db_session.query(CanonicalItem).count() == 1
    assert db_session.query(IntakePhotoBatch).count() == 1
    assert db_session.query(Listing).count() == 1
    listing_mutation.assert_not_called()
    draft_creation.assert_not_called()


def test_ocr_variations_and_safe_segment_substitution_are_normalized(db_session):
    user = _user(db_session, "ocr-recovery@example.com")
    service = IntakeSlateService()
    spacing = _photo(db_session, user, source_id="spacing", metadata_json={"ocr_text": "label: sp – 20260708 — 0006"})
    corrected = _photo(db_session, user, source_id="corrected", metadata_json={"ocr_text": "SP-2O26O7O8-OOO6"})

    service.run_slate_recovery_candidates(db_session, user_id=user.id, photo_ids=[spacing.id, corrected.id])
    candidates = {row.intake_photo_id: row for row in db_session.query(IntakeSlateRecoveryCandidate).all()}

    assert candidates[spacing.id].classification == "probable_slate"
    assert candidates[spacing.id].normalized_item_id == "SP-20260708-0006"
    assert candidates[corrected.id].normalized_item_id == "SP-20260708-0006"
    assert service.normalize_recovery_item_id("XP-2O26O7O8-OOO6") is None
    assert service.normalize_recovery_item_id("SP-2026070X-0006") is None


def test_invalid_item_id_is_rejected_and_unresolved_evidence_is_retained(db_session):
    user = _user(db_session, "invalid-recovery@example.com")
    photo = _photo(db_session, user, metadata_json={"ocr_text": "SP-2026070X-0006"})
    result = IntakeSlateService().run_slate_recovery_candidates(db_session, user_id=user.id, photo_ids=[photo.id])
    candidate = db_session.query(IntakeSlateRecoveryCandidate).one()

    assert result["unresolved"] == 1 and result["valid_item_ids"] == 0
    assert candidate.classification == "unresolved"
    assert candidate.raw_item_id == "SP-2026070X-0006"
    assert candidate.normalized_item_id is None and candidate.match_status == "unresolved"
    assert result["invalid_item_ids"] == 1


def test_candidate_upsert_is_idempotent_and_pipeline_versions_are_distinct(db_session):
    user = _user(db_session, "versions-recovery@example.com")
    photo = _photo(db_session, user, metadata_json={"qr_payload": {"item_id": "SP-20260708-0006"}})
    service = IntakeSlateService()

    service.run_slate_recovery_candidates(db_session, user_id=user.id, photo_ids=[photo.id])
    first = db_session.query(IntakeSlateRecoveryCandidate).one()
    service.run_slate_recovery_candidates(db_session, user_id=user.id, photo_ids=[photo.id])
    assert db_session.query(IntakeSlateRecoveryCandidate).count() == 1
    assert db_session.query(IntakeSlateRecoveryCandidate).one().id == first.id
    service.run_slate_recovery_candidates(db_session, user_id=user.id, photo_ids=[photo.id], pipeline_version="deterministic_slate_recovery_v2")
    assert db_session.query(IntakeSlateRecoveryCandidate).count() == 2


def test_v2_rejects_photo_and_batch_assignment_as_slate_or_item_id_evidence(db_session):
    user = _user(db_session, "v2-circular@example.com")
    item_id = "SP-20260708-0006"
    slate = IntakeSlate(user_id=user.id, item_id=item_id)
    canonical = CanonicalItem(user_id=user.id, item_id=item_id)
    db_session.add_all([slate, canonical])
    db_session.flush()
    batch = IntakePhotoBatch(user_id=user.id, item_id=item_id, slate_id=slate.id)
    db_session.add(batch)
    db_session.commit()
    product = _photo(db_session, user, source_id="assigned-product", item_id=item_id, batch_id=batch.id, metadata_json={"item_id": item_id})

    result = IntakeSlateService().run_slate_recovery_candidates(
        db_session, user_id=user.id, photo_ids=[product.id], pipeline_version=SLATE_RECOVERY_PIPELINE_VERSION_V2
    )
    candidate = db_session.query(IntakeSlateRecoveryCandidate).one()

    assert result["not_slates"] == 1 and result["valid_item_ids"] == 0
    assert candidate.classification == "not_slate"
    assert candidate.normalized_item_id is None and candidate.raw_item_id is None
    assert candidate.matched_canonical_item_id is None and candidate.matched_batch_id is None
    assert set(candidate.evidence_json["circular_evidence_rejected"]) >= {"photo.item_id", "photo.batch_id", "metadata.item_id"}
    assert db_session.query(CanonicalItem).count() == 1
    assert db_session.query(IntakePhotoBatch).count() == 1


def test_v2_independent_qr_ocr_slate_state_and_observation_evidence(db_session):
    user = _user(db_session, "v2-independent@example.com")
    service = IntakeSlateService()
    qr_photo = _photo(db_session, user, source_id="qr", metadata_json={"qr_payload": {"item_id": "SP-20260708-0006"}})
    ocr_photo = _photo(db_session, user, source_id="ocr", metadata_json={"ocr_text": "SP-20260708-0007", "slate_detection": {"is_qr_candidate": True}})
    marked_photo = _photo(db_session, user, source_id="marked", is_slate=True)
    observed_photo = _photo(db_session, user, source_id="observed")
    db_session.add(SlateObservation(user_id=user.id, media_id=observed_photo.id, item_id="SP-20260708-0008"))
    db_session.commit()

    service.run_slate_recovery_candidates(db_session, user_id=user.id, photo_ids=[qr_photo.id, ocr_photo.id, marked_photo.id, observed_photo.id], pipeline_version=SLATE_RECOVERY_PIPELINE_VERSION_V2)
    candidates = {row.intake_photo_id: row for row in db_session.query(IntakeSlateRecoveryCandidate).all()}

    assert candidates[qr_photo.id].classification == "confirmed_slate"
    assert candidates[qr_photo.id].evidence_json["item_id_evidence_source"] == "stored_qr_payload"
    assert candidates[ocr_photo.id].classification == "probable_slate"
    assert candidates[ocr_photo.id].evidence_json["item_id_evidence_source"] == "stored_ocr_text"
    assert candidates[marked_photo.id].classification == "confirmed_slate"
    assert candidates[observed_photo.id].classification == "confirmed_slate"
    assert candidates[observed_photo.id].normalized_item_id == "SP-20260708-0008"


def test_v2_large_corrupted_batch_keeps_product_photos_not_slate_and_is_idempotent(db_session):
    user = _user(db_session, "v2-large-batch@example.com")
    item_id = "SP-20260708-0006"
    batch = IntakePhotoBatch(user_id=user.id, item_id=item_id)
    db_session.add(batch)
    db_session.flush()
    products = [
        IntakePhoto(user_id=user.id, source_provider="google_photos", source_photo_id=f"product-{index}", local_path="/tmp/product.jpg", item_id=item_id, batch_id=batch.id)
        for index in range(2001)
    ]
    true_slate = IntakePhoto(user_id=user.id, source_provider="google_photos", source_photo_id="true-slate", local_path="/tmp/slate.jpg", item_id=item_id, batch_id=batch.id, is_slate=True, metadata_json={"qr_payload": {"item_id": item_id}})
    db_session.add_all([*products, true_slate])
    db_session.commit()
    photo_ids = [photo.id for photo in products] + [true_slate.id]
    service = IntakeSlateService()

    first = service.run_slate_recovery_candidates(db_session, user_id=user.id, photo_ids=photo_ids, pipeline_version=SLATE_RECOVERY_PIPELINE_VERSION_V2)
    before = db_session.query(IntakeSlateRecoveryCandidate).count()
    second = service.run_slate_recovery_candidates(db_session, user_id=user.id, photo_ids=photo_ids, pipeline_version=SLATE_RECOVERY_PIPELINE_VERSION_V2)

    assert first["confirmed_slates"] == 1 and first["probable_slates"] == 0 and first["not_slates"] == 2001
    assert second["evaluated"] == 2002 and db_session.query(IntakeSlateRecoveryCandidate).count() == before == 2002
    db_session.refresh(products[0])
    assert products[0].item_id == item_id and products[0].batch_id == batch.id
