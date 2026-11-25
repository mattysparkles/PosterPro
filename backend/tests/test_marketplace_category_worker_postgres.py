"""PostgreSQL-only delivery/locking tests for durable category jobs.

CI supplies a disposable `posterpro_category_test` database. A unique schema
per test prevents these tests from dropping or modifying any other tables.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import make_url

from app.core.database import Base
from app.models.enums import ListingStatus
from app.models.models import BulkJob, Listing, User


@pytest.fixture
def postgres_category_db(monkeypatch):
    raw_url = os.getenv("POSTERPRO_TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("POSTERPRO_TEST_POSTGRES_URL is only provided by isolated PostgreSQL CI")
    url = make_url(raw_url)
    if url.database != "posterpro_category_test" or url.host not in {"localhost", "127.0.0.1"}:
        pytest.fail("Refusing PostgreSQL worker test outside the dedicated localhost test database")

    admin_engine = create_engine(raw_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    schema = f"category_worker_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(raw_url, pool_pre_ping=True,
                           connect_args={"connect_timeout": 5, "options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    from app.workers import tasks
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    try:
        yield engine, factory
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _seed_job(factory, job_id: str):
    with factory() as db:
        user = User(email=f"{job_id}@example.test")
        db.add(user)
        db.flush()
        listing = Listing(user_id=user.id, status=ListingStatus.draft,
                          title="Acme Wireless Headphones Model X",
                          source_metadata={"asin": "B012345678"}, marketplace_data={"keep": "existing"})
        db.add(listing)
        db.flush()
        job = BulkJob(id=job_id, user_id=user.id, action="suggest_marketplace_categories",
                      status="queued", total_items=1, processed_items=0, error_count=0, errors=[],
                      payload={"listing_ids": [listing.id], "marketplaces": ["facebook", "ebay", "mercari"],
                               "cursor": 0, "batch_size": 25})
        db.add(job)
        db.commit()
        return listing.id


def test_postgres_simultaneous_delivery_claims_job_once(postgres_category_db, monkeypatch):
    engine, factory = postgres_category_db
    listing_id = _seed_job(factory, "category-pg-concurrency")
    from app.services import marketplace_category_suggestions as suggestions
    from app.workers.tasks import process_marketplace_taxonomy_suggestion_job as task

    entered_build = threading.Event()
    allow_commit = threading.Event()
    calls = []
    result = {"version": 1, "product_identity": "id:asin:b012345678",
              "marketplaces": {name: {"state": "review_required", "candidates": []}
                               for name in ("facebook", "ebay", "mercari")},
              "published_or_queued": False}

    def blocking_build(*args, **kwargs):
        calls.append(listing_id)
        entered_build.set()
        assert allow_commit.wait(10), "concurrent test did not release first worker"
        return result

    monkeypatch.setattr(suggestions, "build_suggestions", blocking_build)
    first_result = []
    first = threading.Thread(target=lambda: first_result.append(task.run("category-pg-concurrency")))
    first.start()
    assert entered_build.wait(10), "first worker did not reach the locked processing section"

    duplicate_delivery = task.run("category-pg-concurrency")
    assert duplicate_delivery["status"] == "not_claimed"
    allow_commit.set()
    first.join(15)
    assert not first.is_alive(), "first worker did not finish"
    assert len(first_result) == 1 and first_result[0]["has_more"] is False
    assert calls == [listing_id]

    with factory() as db:
        job = db.get(BulkJob, "category-pg-concurrency")
        listing = db.get(Listing, listing_id)
        assert job.status == "completed"
        assert job.processed_items == 1
        assert listing.marketplace_data["keep"] == "existing"
        assert listing.marketplace_data["marketplace_category_suggestions"]["published_or_queued"] is False
    engine.dispose()


def test_postgres_retry_survives_connection_restart_and_savepoint_rollback(postgres_category_db, monkeypatch):
    engine, factory = postgres_category_db
    listing_id = _seed_job(factory, "category-pg-restart")
    from app.services import marketplace_category_suggestions as suggestions
    from app.workers.tasks import process_marketplace_taxonomy_suggestion_job as task

    result = {"version": 1, "product_identity": "id:asin:b012345678",
              "marketplaces": {"facebook": {"state": "review_required", "candidates": []}},
              "published_or_queued": False}
    monkeypatch.setattr(suggestions, "build_suggestions", lambda *args, **kwargs: result)
    real_persist = suggestions.persist_suggestions
    failed_once = [False]

    def fail_inside_savepoint(listing, value):
        if not failed_once[0]:
            failed_once[0] = True
            listing.marketplace_data = {"partial_write_must_rollback": True}
            raise RuntimeError("simulated transient persistence fault")
        real_persist(listing, value)

    monkeypatch.setattr(suggestions, "persist_suggestions", fail_inside_savepoint)
    first = task.run("category-pg-restart")
    assert first["has_more"] is True
    with factory() as db:
        job = db.get(BulkJob, "category-pg-restart")
        listing = db.get(Listing, listing_id)
        assert job.status == "queued"
        assert job.payload["retry_items"][0]["attempt"] == 1
        assert "partial_write_must_rollback" not in listing.marketplace_data
        payload = dict(job.payload)
        payload["retry_items"] = [dict(job.payload["retry_items"][0],
                                       next_attempt_at=(datetime.utcnow() - timedelta(seconds=1)).isoformat())]
        job.payload = payload
        db.commit()

    # Simulate a worker process restart: dispose all pooled connections before
    # the retry reads its durable cursor and retry item from PostgreSQL.
    engine.dispose()
    second = task.run("category-pg-restart")
    assert second["has_more"] is False
    with factory() as db:
        job = db.get(BulkJob, "category-pg-restart")
        listing = db.get(Listing, listing_id)
        assert job.status == "completed"
        assert job.processed_items == 1
        assert job.error_count == 0
        assert listing.marketplace_data["keep"] == "existing"
        assert listing.marketplace_data["marketplace_category_suggestions"]["published_or_queued"] is False
