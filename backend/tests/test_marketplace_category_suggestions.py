from types import SimpleNamespace

from app.services import marketplace_category_suggestions as suggestions
from app.api.taxonomy_registry import CategorySuggestionJobRequest, create_category_suggestion_job
from app.models.models import BulkJob, Listing, User
from app.models.enums import ListingStatus


def test_bulk_suggestions_are_separate_review_candidates_and_preserve_confirmed_mapping(monkeypatch):
    listing = SimpleNamespace(
        id=17, user_id=2, title="Acme Wireless Headphones Model X Size Large Black Pack of 2",
        suggested_title=None, description="", category_suggestion="Consumer Electronics",
        item_specifics={"Brand": "Acme", "Model": "X", "Size": "Large", "Color": "Black"},
        source_metadata={"asin": "B012345678", "amazon_product_facts": {"brand": "Acme", "model": "X"}},
        marketplace_data={"marketplace_category_mappings": {"ebay": {
            "mapping_id": "confirmed-ebay", "verification_status": "operator_confirmed",
            "category_path": "Electronics > Headphones", "taxonomy_version_id": "ebay-v1",
        }}},
    )
    def ranked(db, **kwargs):
        return {"marketplaces": [{
            "marketplace": "facebook", "status": "review_required", "taxonomy_versions": [{"version_id": "fb-v2"}],
            "candidates": [{
                "category_id": "reference-11", "category_key": "fb:11", "full_path": "Electronics > Headphones",
                "registry_version_id": "fb-v2", "taxonomy_version": "reference-v2", "data_source": "meta_reference",
                "verification_status": "imported_unverified", "staleness_status": "unknown",
                "terminal_status": "unknown", "publishability_status": "reference_only",
                "candidate_retrieval_score": 0.3, "mapping_confidence": 0.21,
                "required_aspects": [{"localizedAspectName": "Brand"}, {"localizedAspectName": "Condition"}],
            }],
        }]}
    monkeypatch.setattr(suggestions, "rank_taxonomy_candidates", ranked)
    result = suggestions.build_suggestions(object(), listing, owner_user_id=2, marketplaces=["facebook"])
    assert set(result["marketplaces"]) == {"facebook"}
    candidate = result["marketplaces"]["facebook"]["candidates"][0]
    assert candidate["category_id"] == "reference-11"
    assert candidate["reference_only"] is True
    assert candidate["selectable_for_confirmation"] is False
    assert candidate["missing_attributes"] == ["Condition"]
    assert candidate["confidence_kind"] == "uncalibrated_retrieval_score"
    assert result["assignment_state"] == "suggestions_only_operator_confirmation_required"
    assert result["published_or_queued"] is False

    suggestions.persist_suggestions(listing, result)
    saved = listing.marketplace_data
    assert saved["marketplace_category_mappings"]["ebay"]["mapping_id"] == "confirmed-ebay"
    assert saved["marketplace_category_suggestions"]["marketplaces"]["facebook"]["candidates"][0]["category_id"] == "reference-11"


def test_suggestion_worker_refuses_cross_owner_listing():
    listing = SimpleNamespace(id=4, user_id=3, title="Acme headphones", source_metadata={}, item_specifics={})
    try:
        suggestions.build_suggestions(object(), listing, owner_user_id=2)
    except ValueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("cross-owner suggestions must be rejected")


def test_category_suggestion_job_is_owner_scoped_persistent_and_never_publishes(db_session, monkeypatch):
    from app.workers.tasks import process_marketplace_taxonomy_suggestion_job
    queued = []
    monkeypatch.setattr(process_marketplace_taxonomy_suggestion_job, "delay", lambda job_id: queued.append(job_id))
    user = User(email="category-job-owner@example.com")
    other = User(email="category-job-other@example.com")
    db_session.add_all([user, other]); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.draft, title="Acme Headphones",
                      source_metadata={"asin": "B012345678"}, marketplace_data={})
    foreign = Listing(user_id=other.id, status=ListingStatus.draft, title="Acme Headphones",
                      source_metadata={"asin": "B012345678"}, marketplace_data={})
    db_session.add_all([listing, foreign]); db_session.commit()
    response = create_category_suggestion_job(CategorySuggestionJobRequest(
        listing_ids=[listing.id], marketplaces=["facebook", "ebay"]), db_session, user)
    job = db_session.get(BulkJob, response["job_id"])
    assert response["status"] == "queued"
    assert response["publishing_performed"] is False
    assert job.payload["listing_ids"] == [listing.id]
    assert job.payload["cursor"] == 0
    assert job.payload["marketplaces"] == ["facebook", "ebay"]
    assert queued == [job.id]

    from fastapi import HTTPException
    try:
        create_category_suggestion_job(CategorySuggestionJobRequest(
            listing_ids=[foreign.id], marketplaces=["mercari"]), db_session, user)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("cross-owner listing IDs must not enter a suggestion job")


def test_suggestion_candidate_persists_in_listing_json_without_assigning_category(db_session):
    user = User(email="category-suggestion-persistence@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.draft, title="Acme headphones",
                      category_id=None, category_suggestion="Electronics",
                      marketplace_data={"marketplace_category_mappings": {"ebay": {"mapping_id": "manual", "category_id": "old"}}})
    db_session.add(listing); db_session.commit()
    result = {"version": 1, "marketplaces": {"facebook": {
        "state": "review_required", "candidates": [{"category_key": "fb:leaf", "taxonomy_version_id": "fb:v1"}],
    }}, "assignment_state": "suggestions_only_operator_confirmation_required", "published_or_queued": False}
    suggestions.persist_suggestions(listing, result)
    db_session.commit(); db_session.refresh(listing)
    assert listing.marketplace_data["marketplace_category_mappings"]["ebay"]["mapping_id"] == "manual"
    assert listing.marketplace_data["marketplace_category_suggestions"]["marketplaces"]["facebook"]["candidates"][0]["category_key"] == "fb:leaf"
    assert listing.category_id is None


def test_suggestion_worker_retries_item_failure_with_backoff_and_finishes(db_session, monkeypatch):
    from datetime import datetime, timedelta
    from app.workers.tasks import process_marketplace_taxonomy_suggestion_job as task

    user = User(email="category-worker-retry@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.draft, title="Acme Headphones",
                      source_metadata={"asin": "B012345678"}, marketplace_data={})
    db_session.add(listing); db_session.flush()
    job = BulkJob(id="category-worker-retry", user_id=user.id, action="suggest_marketplace_categories",
                  status="queued", total_items=1, processed_items=0, error_count=0, errors=[],
                  payload={"listing_ids": [listing.id], "marketplaces": ["facebook"], "cursor": 0, "batch_size": 25})
    db_session.add(job); db_session.commit()

    attempts = []
    def build(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary registry connection failure")
        return {"marketplaces": {"facebook": {"state": "review_required", "candidates": []}},
                "product_identity": "id:asin:b012345678", "published_or_queued": False}
    monkeypatch.setattr(suggestions, "build_suggestions", build)
    enqueued = []
    monkeypatch.setattr(task, "delay", lambda job_id: enqueued.append(job_id))

    first = task.run(job.id)
    db_session.expire_all()
    saved = db_session.get(BulkJob, job.id)
    assert first["has_more"] is True
    assert saved.status == "queued"
    assert saved.processed_items == 0
    assert saved.error_count == 0
    assert saved.errors == []
    retry = saved.payload["retry_items"][0]
    assert retry["listing_id"] == listing.id
    assert retry["attempt"] == 1
    assert datetime.fromisoformat(retry["next_attempt_at"]) > datetime.utcnow()
    waiting = task.run(job.id)
    assert waiting["retry_wait"] is True
    assert len(attempts) == 1, "a retry must not recompute before its persisted backoff expires"

    # Simulate the watchdog resuming after the durable backoff interval.
    payload = dict(saved.payload)
    payload["retry_items"] = [dict(saved.payload["retry_items"][0],
                                   next_attempt_at=(datetime.utcnow() - timedelta(seconds=1)).isoformat())]
    saved.payload = payload
    saved.updated_at = datetime.utcnow() - timedelta(minutes=3)
    db_session.commit()
    second = task.run(job.id)
    assert "error" not in second, second
    db_session.expire_all()
    saved = db_session.get(BulkJob, job.id)
    assert second["has_more"] is False
    assert saved.status == "completed"
    assert saved.processed_items == 1
    assert saved.error_count == 0
    assert saved.payload["retry_items"] == []
    assert len(attempts) == 2

    # Re-delivery after completion is idempotent.
    assert task.run(job.id)["status"] == "completed"
    assert len(attempts) == 2


def test_suggestion_worker_keeps_exact_error_count_when_detail_list_is_capped(db_session, monkeypatch):
    from datetime import datetime, timedelta
    from app.workers.tasks import process_marketplace_taxonomy_suggestion_job as task

    user = User(email="category-worker-error-count@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.draft, title="Acme Headphones",
                      source_metadata={"asin": "B012345678"}, marketplace_data={})
    db_session.add(listing); db_session.flush()
    prior_errors = [{"listing_id": n, "error": "prior terminal failure"} for n in range(500)]
    job = BulkJob(id="category-worker-error-count", user_id=user.id, action="suggest_marketplace_categories",
                  status="queued", total_items=1, processed_items=0, error_count=500, errors=prior_errors,
                  payload={"listing_ids": [listing.id], "marketplaces": ["facebook"], "cursor": 1, "batch_size": 25,
                           "retry_items": [{"listing_id": listing.id, "attempt": 2,
                                            "next_attempt_at": (datetime.utcnow() - timedelta(seconds=1)).isoformat()}]})
    db_session.add(job); db_session.commit()
    monkeypatch.setattr(suggestions, "build_suggestions", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("persistent registry error")))

    result = task.run(job.id)
    assert "error" not in result, result
    db_session.expire_all()
    saved = db_session.get(BulkJob, job.id)
    assert result["has_more"] is False
    assert saved.status == "completed_with_errors"
    assert saved.error_count == 501
    assert len(saved.errors) == 500
    assert saved.processed_items == 1


def test_category_suggestion_worker_and_watchdog_are_registered():
    from app.workers.celery_app import celery_app
    from app.workers.publish_dispatcher import celery_app as dispatcher_app
    assert "process_marketplace_taxonomy_suggestion_job" in celery_app.tasks
    assert "resume_marketplace_category_suggestion_jobs" in celery_app.tasks
    assert any(item.get("task") == "resume_marketplace_category_suggestion_jobs"
               for item in celery_app.conf.beat_schedule.values())
    assert celery_app.conf.task_routes["process_marketplace_taxonomy_suggestion_job"]["queue"] == "posterpro-category"
    assert celery_app.conf.task_routes["resume_marketplace_category_suggestion_jobs"]["queue"] == "posterpro-category"
    assert any(item.get("task") == "resume_marketplace_category_suggestion_jobs"
               for item in dispatcher_app.conf.beat_schedule.values())


def test_category_worker_recovers_system_error_without_counting_it_as_terminal(db_session, monkeypatch):
    from app.workers import tasks
    from app.workers.tasks import process_marketplace_taxonomy_suggestion_job as task

    user = User(email="category-worker-system-retry@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.draft, title="Acme Headphones",
                      source_metadata={"asin": "B012345678"}, marketplace_data={})
    db_session.add(listing); db_session.flush()
    job = BulkJob(id="category-worker-system-retry", user_id=user.id, action="suggest_marketplace_categories",
                  status="queued", total_items=1, processed_items=0, error_count=0, errors=[],
                  payload={"listing_ids": [listing.id], "marketplaces": ["facebook"], "cursor": 0, "batch_size": 25})
    db_session.add(job); db_session.commit()

    original_factory = tasks.SessionLocal
    fail_once = [True]
    class BrokenQuerySession:
        def __init__(self, session): self.session = session
        def query(self, *args, **kwargs): raise RuntimeError("temporary database interruption")
        def __getattr__(self, key): return getattr(self.session, key)
    class SessionContext:
        def __init__(self): self.session = original_factory()
        def __enter__(self):
            if fail_once[0]:
                fail_once[0] = False
                return BrokenQuerySession(self.session)
            return self.session
        def __exit__(self, exc_type, exc, traceback): self.session.close()
    monkeypatch.setattr(tasks, "SessionLocal", SessionContext)

    first = task.run(job.id)
    assert first["status"] == "queued"
    db_session.expire_all()
    saved = db_session.get(BulkJob, job.id)
    assert saved.payload["system_retry_count"] == 1
    assert saved.processed_items == 0
    assert saved.error_count == 0

    monkeypatch.setattr(tasks, "SessionLocal", original_factory)
    monkeypatch.setattr(suggestions, "build_suggestions", lambda *args, **kwargs: {
        "product_identity": "id:asin:b012345678", "marketplaces": {
            "facebook": {"state": "review_required", "candidates": []}},
        "published_or_queued": False,
    })
    completed = task.run(job.id)
    db_session.expire_all()
    saved = db_session.get(BulkJob, job.id)
    assert completed["has_more"] is False
    assert saved.status == "completed"
    assert saved.payload["system_retry_count"] == 0
    assert saved.processed_items == 1
    assert saved.error_count == 0
