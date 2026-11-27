"""Isolated acceptance tests for the durable Vine image recovery state machine."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image as PILImage
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models.models import Image, Listing, MarketplaceListing, User, VineImageRecoveryJob, VineImportBatch, VineImportItem
from app.workers import tasks


def _jpeg_bytes() -> bytes:
    output = BytesIO()
    PILImage.new("RGB", (320, 320), "white").save(output, format="JPEG")
    return output.getvalue()


def test_vine_recovery_worker_submission_poll_review_and_duplicate_delivery(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    user = User(email=f"vine-worker-{uuid4()}@example.test", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    approved_photo_path = "/media/operator/approved.jpg"
    listing = Listing(
        user_id=user.id, source_type="amazon_vine", title="Sealed product",
        image_urls=[approved_photo_path], listing_images=[{
            "storage_path": approved_photo_path, "source_platform": "actual_upload",
            "operator_state": "approved", "operator_approved": True,
            "is_reference": False, "role": "primary", "display_order": 0,
        }],
    )
    db_session.add(listing)
    db_session.flush()
    job = VineImageRecoveryJob(
        user_id=user.id, listing_id=listing.id, asin="B000000042",
        status="submitting", attempts=1,
    )
    db_session.add(job)
    db_session.commit()

    scheduled = []
    monkeypatch.setattr(tasks.poll_vine_image_recovery_job, "apply_async", lambda **kwargs: scheduled.append(kwargs))
    bridge_requests = []
    monkeypatch.setattr(tasks, "submit_bridge_job", lambda **kwargs: (
        bridge_requests.append(kwargs) or {"bridge_response": {"job_id": "bridge-vine-42"}}
    ))
    submitted = tasks.submit_vine_image_recovery_request.run(job.id)
    assert submitted["status"] == "collecting"
    assert bridge_requests[0]["payload"]["operation"] == "vine_image_gallery_recovery"
    assert bridge_requests[0]["payload"]["acquisition_request_key"] == f"vine-image-recovery:{job.id}"
    assert tasks.submit_vine_image_recovery_request.run(job.id)["status"] == "not_claimed"

    candidate_url = "https://m.media-amazon.com/images/I/test-product.jpg"
    monkeypatch.setattr(tasks, "get_bridge_job", lambda _id: {
        "status": "completed",
        "result": {"imported_listings": [{
            "asin": "B000000042", "page_asin": "B000000042",
            "source_url": "https://www.amazon.com/dp/B000000042",
            "selected_variant": "1 pack", "image_urls": [candidate_url],
            "gallery_evidence": [{"source_url": candidate_url, "gallery_index": 0}],
        }]},
    })
    monkeypatch.setattr(tasks, "fetch_candidate_image", lambda _url: (_jpeg_bytes(), {
        "evidence": {"format": "JPEG", "dimensions": [320, 320], "content_type": "image/jpeg"},
    }))
    result = tasks.poll_vine_image_recovery_job.run(job.id)
    assert result == {"status": "review_required", "candidate_count": 1}
    assert tasks.poll_vine_image_recovery_job.run(job.id)["status"] == "not_collecting"
    db_session.expire_all()
    refreshed = db_session.get(VineImageRecoveryJob, job.id)
    assert refreshed.status == "review_required"
    assert refreshed.page_evidence_json["page_asin"] == "B000000042"
    assert refreshed.candidates_json[0]["operator_state"] == "pending_review"
    assert db_session.query(Image).filter(Image.image_metadata["asin"].as_string() == "B000000042").count() == 1
    recovered_listing = db_session.get(Listing, listing.id)
    assert len(recovered_listing.listing_images) == 2
    recovered_gallery_image = next(row for row in recovered_listing.listing_images if row["storage_path"] == refreshed.candidates_json[0]["storage_path"])
    assert recovered_gallery_image["is_reference"] is True
    assert recovered_gallery_image["seller_photographed"] is False
    assert recovered_gallery_image["marketplace_approved"] is False
    assert recovered_gallery_image["metadata"]["rights_state"] == "unverified"

    from app.api.vine_imports import attach_vine_image_recovery_candidate
    from app.api.routes import approve_listing_photos, set_listing_photo_primary
    from app.api.schemas import ListingPhotoActionRequest, ListingPhotoSetPrimaryRequest
    candidate_path = refreshed.candidates_json[0]["storage_path"]
    attached = attach_vine_image_recovery_candidate(job.id, 0, db_session, user)
    assert attached["image_review_state"] == "suggested"
    assert attached["rights_state"] == "unverified"
    listing = db_session.get(Listing, listing.id)
    assert approved_photo_path in listing.image_urls
    recovered = next(row for row in listing.listing_images if row["storage_path"] == candidate_path)
    assert recovered["is_reference"] is True and recovered["operator_state"] == "suggested"

    approve_listing_photos(listing.id, ListingPhotoActionRequest(storage_paths=[candidate_path]), db_session, user)
    set_listing_photo_primary(listing.id, ListingPhotoSetPrimaryRequest(storage_path=candidate_path), db_session, user)
    db_session.refresh(listing)
    assert listing.image_urls[0] == candidate_path
    assert approved_photo_path in listing.image_urls
    selected = listing.listing_images[0]
    assert selected["is_reference"] is True
    assert selected["operator_state"] == "approved"
    assert selected["metadata"]["rights_state"] == "unverified"
    assert db_session.query(MarketplaceListing).filter(MarketplaceListing.listing_id == listing.id).count() == 0


def test_title_verified_recovery_accepts_public_result_asin_mismatch(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    user = User(email=f"vine-title-identity-{uuid4()}@example.test", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    title = "Siftary 100 Pack 16.9 oz Clear PET Cups with Aluminum Lids"
    listing = Listing(user_id=user.id, source_type="amazon_vine", title=title, image_urls=[], listing_images=[])
    db_session.add(listing)
    db_session.flush()
    job = VineImageRecoveryJob(user_id=user.id, listing_id=listing.id, asin="B000000042", title_hint="Siftary 100 Pack 16.9 oz Clear PET Cups", status="collecting", bridge_job_id="bridge-title-match")
    db_session.add(job)
    db_session.commit()
    candidate_url = "https://m.media-amazon.com/images/I/title-matched-product.jpg"
    monkeypatch.setattr(tasks, "get_bridge_job", lambda _id: {
        "status": "completed",
        "result": {"imported_listings": [{
            "asin": "B000000042", "page_asin": "B000000099",
            "identity_status": "title_tokens_verified",
            "source_url": "https://www.amazon.com/dp/B000000099",
            "title": title, "image_urls": [candidate_url],
            "gallery_evidence": [{"source_url": candidate_url, "gallery_region": "product_detail_gallery"}],
        }]},
    })
    monkeypatch.setattr(tasks, "fetch_candidate_image", lambda _url: (_jpeg_bytes(), {
        "evidence": {"format": "JPEG", "dimensions": [320, 320], "content_type": "image/jpeg"},
    }))

    result = tasks.poll_vine_image_recovery_job.run(job.id)
    db_session.expire_all()
    refreshed = db_session.get(VineImageRecoveryJob, job.id)
    media = db_session.query(Image).filter(Image.image_metadata["asin"].as_string() == "B000000042").one()
    assert result == {"status": "review_required", "candidate_count": 1}
    assert refreshed.page_evidence_json["identity_method"] == "product_title_token_match"
    assert refreshed.page_evidence_json["page_asin"] == "B000000099"
    assert media.image_metadata["identity_state"] == "title_verified_review_required"
    assert media.image_metadata["rights_state"] == "unverified"


def test_recovery_rejects_matching_asin_when_selected_variant_conflicts(db_session, monkeypatch):
    user = User(email=f"vine-wrong-variant-{uuid4()}@example.test", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    title = "Brand A123 100 Pack 16.9 oz Clear PET Cups"
    listing = Listing(user_id=user.id, source_type="amazon_vine", title=title, image_urls=[], listing_images=[])
    db_session.add(listing)
    db_session.flush()
    job = VineImageRecoveryJob(user_id=user.id, listing_id=listing.id, asin="B000000043", title_hint=title, status="collecting", bridge_job_id="bridge-wrong-variant")
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(tasks, "get_bridge_job", lambda _id: {
        "status": "completed",
        "result": {"imported_listings": [{
            "asin": "B000000043", "page_asin": "B000000043",
            "identity_status": "title_tokens_verified",
            "source_url": "https://www.amazon.com/dp/B000000043",
            "title": "Brand A123 Beverage Cups, 100 count, red PET, 12 ounces",
            "selected_variant": "Red, 12 oz",
            "image_urls": ["https://m.media-amazon.com/images/I/wrong-variant.jpg"],
        }]},
    })

    result = tasks.poll_vine_image_recovery_job.run(job.id)
    db_session.refresh(job)
    assert result["status"] == "pending"
    assert "neither matching ASIN" in result["error"]
    assert job.candidates_json in (None, [])


def test_vine_recovery_lost_bridge_response_retries_with_same_idempotency_key(db_session, monkeypatch):
    user = User(email=f"vine-lost-bridge-{uuid4()}@example.test", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    job = VineImageRecoveryJob(user_id=user.id, asin="B000000043", status="submitting", attempts=1)
    db_session.add(job)
    db_session.commit()
    keys = []
    remote_created = {"job_id": "bridge-vine-43"}

    def submit(*, payload, **_kwargs):
        keys.append(payload["acquisition_request_key"])
        if len(keys) == 1:
            # Simulates remote acceptance followed by a lost HTTP response.
            raise RuntimeError("bridge response was lost after submission")
        return {"bridge_response": remote_created}

    monkeypatch.setattr(tasks, "submit_bridge_job", submit)
    assert tasks.submit_vine_image_recovery_request.run(job.id)["status"] == "pending"
    db_session.expire_all()
    refreshed = db_session.get(VineImageRecoveryJob, job.id)
    refreshed.next_attempt_at = datetime.utcnow()
    db_session.commit()
    queued = []
    monkeypatch.setattr(tasks.submit_vine_image_recovery_request, "apply_async", lambda **kwargs: queued.append(kwargs))
    assert tasks.dispatch_vine_image_recovery_jobs.run()["dispatched"] == 1
    assert len(queued) == 1
    assert tasks.submit_vine_image_recovery_request.run(job.id)["status"] == "collecting"
    assert keys == [f"vine-image-recovery:{job.id}", f"vine-image-recovery:{job.id}"]


def test_vine_recovery_keeps_remote_job_when_poll_queue_is_temporarily_unavailable(db_session, monkeypatch):
    user = User(email=f"vine-poll-queue-{uuid4()}@example.test", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    job = VineImageRecoveryJob(user_id=user.id, asin="B000000046", status="submitting", attempts=1)
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(tasks, "submit_bridge_job", lambda **_kwargs: {
        "bridge_response": {"job_id": "bridge-vine-46"}
    })

    def broker_unavailable(**_kwargs):
        raise RuntimeError("temporary Redis outage")

    monkeypatch.setattr(tasks.poll_vine_image_recovery_job, "apply_async", broker_unavailable)
    result = tasks.submit_vine_image_recovery_request.run(job.id)

    db_session.expire_all()
    persisted = db_session.get(VineImageRecoveryJob, job.id)
    assert result == {"status": "collecting", "bridge_job_id": "bridge-vine-46"}
    assert persisted.status == "collecting"
    assert persisted.bridge_job_id == "bridge-vine-46"
    assert persisted.last_error is None


def test_vine_dispatch_recovers_worker_crash_and_stops_at_attempt_cap(db_session, monkeypatch):
    user = User(email=f"vine-crash-recovery-{uuid4()}@example.test", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    crashed = VineImageRecoveryJob(
        user_id=user.id, asin="B000000044", status="submitting_remote", attempts=1,
        updated_at=datetime.utcnow() - timedelta(minutes=10),
    )
    exhausted = VineImageRecoveryJob(
        user_id=user.id, asin="B000000045", status="pending_authorization", attempts=4,
    )
    db_session.add_all([crashed, exhausted])
    db_session.commit()
    queued = []
    monkeypatch.setattr(tasks.submit_vine_image_recovery_request, "apply_async", lambda **kwargs: queued.append(kwargs))
    result = tasks.dispatch_vine_image_recovery_jobs.run()
    assert result["dispatched"] == 1
    assert queued == [{"args": [crashed.id], "countdown": 0}]
    db_session.expire_all()
    assert db_session.get(VineImageRecoveryJob, crashed.id).attempts == 1
    assert db_session.get(VineImageRecoveryJob, crashed.id).status == "submitting"
    assert db_session.get(VineImageRecoveryJob, exhausted.id).status == "pending_authorization"


def test_recent_vine_reconciliation_enqueues_all_unlinked_rows_idempotently(db_session, monkeypatch):
    user = User(email=f"vine-reconcile-{uuid4()}@example.test", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    batch = VineImportBatch(user_id=user.id, filename="recent-vine.csv", source_type="csv", status="imported")
    db_session.add(batch)
    db_session.flush()
    for index in range(66):
        asin = f"B{index:09d}"
        listing = Listing(
            user_id=user.id, source_type="amazon_vine", title=f"Vine product {index}",
            image_urls=[], listing_images=[], source_metadata={"asin": asin},
        )
        db_session.add(listing)
        db_session.flush()
        item = VineImportItem(
            batch_id=batch.id, user_id=user.id, asin=asin, product_name=listing.title,
            order_date=datetime(2026, 11, 1).date(), eligibility_status="eligible",
            listing_id=listing.id, inventory_item_id=listing.id,
        )
        db_session.add(item)
        if index < 10:
            db_session.add(VineImageRecoveryJob(
                user_id=user.id, listing_id=listing.id, asin=asin,
                title_hint=listing.title, status="manual_review_required", attempts=1,
            ))
    db_session.commit()

    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    first = tasks.repair_recent_vine_images_task.run(user.id, chunk_size=25)
    assert first["eligible"] == 66
    assert first["jobs_created"] == 56
    assert first["already_queued"] == 10
    assert db_session.query(VineImageRecoveryJob).filter_by(user_id=user.id).count() == 66

    second = tasks.repair_recent_vine_images_task.run(user.id, chunk_size=25)
    assert second["jobs_created"] == 0
    assert second["already_queued"] == 66
    assert db_session.query(VineImageRecoveryJob).filter_by(user_id=user.id).count() == 66


@pytest.fixture
def postgres_vine_recovery_db(monkeypatch):
    raw_url = os.getenv("POSTERPRO_TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("POSTERPRO_TEST_POSTGRES_URL is only provided by isolated PostgreSQL CI")
    url = make_url(raw_url)
    if url.database not in {"posterpro_category_test", "posterpro_p0_test"} or url.host not in {"localhost", "127.0.0.1"}:
        pytest.fail("Refusing Vine recovery worker test outside a disposable localhost PostgreSQL database")
    admin_engine = create_engine(raw_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    schema = f"vine_recovery_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(raw_url, pool_pre_ping=True,
                           connect_args={"connect_timeout": 5, "options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    try:
        yield engine, factory
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_postgres_vine_dispatchers_share_three_job_cap(postgres_vine_recovery_db, monkeypatch):
    _engine, factory = postgres_vine_recovery_db
    with factory() as db:
        user = User(email=f"vine-concurrency-{uuid4()}@example.test", role="owner", is_admin=True)
        db.add(user)
        db.flush()
        for index in range(7):
            db.add(VineImageRecoveryJob(user_id=user.id, asin=f"B0000000{index:02d}", status="pending_authorization"))
        db.commit()
    submitted = []
    submitted_lock = threading.Lock()

    def record_submit(*args, **kwargs):
        with submitted_lock:
            submitted.append((args, kwargs))

    monkeypatch.setattr(tasks.submit_vine_image_recovery_request, "apply_async", record_submit)
    barrier = threading.Barrier(2)

    def dispatch():
        barrier.wait(timeout=10)
        return tasks.dispatch_vine_image_recovery_jobs.run()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: dispatch(), range(2)))
    with factory() as db:
        active = db.execute(select(func.count(VineImageRecoveryJob.id)).where(
            VineImageRecoveryJob.status.in_(["submitting", "submitting_remote", "collecting", "processing_review"])
        )).scalar_one()
    assert sum(result["dispatched"] for result in results) == 3
    assert len(submitted) == 3
    assert active == 3
