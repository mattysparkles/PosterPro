from types import SimpleNamespace

from app.api.marketplace_jobs import JobControlRequest, control_marketplace_jobs
from app.core import database as database_module
from app.models.models import Listing, MarketplaceCrosspostJob, User


def _seed_job(db, *, status="failed"):
    user = User(email="jobs-control@example.test", role="operator", settings_json={})
    db.add(user)
    db.flush()
    listing = Listing(user_id=user.id, title="Test product", description="Test description")
    db.add(listing)
    db.flush()
    job = MarketplaceCrosspostJob(
        user_id=user.id,
        listing_id=listing.id,
        status=status,
        target_marketplaces=["ebay"],
        execution_plan={"operation": "create"},
        result_summary={"previous_result": "preserved"},
    )
    db.add(job)
    db.commit()
    return user, job


def test_pause_and_resume_queue_persist_account_control(db_session):
    user, _job = _seed_job(db_session)
    paused = control_marketplace_jobs(JobControlRequest(action="pause_queue", job_types=["crosspost"]), db_session, user)
    assert paused["paused"] is True
    db_session.refresh(user)
    assert user.settings_json["marketplace_jobs_paused"] is True
    resumed = control_marketplace_jobs(JobControlRequest(action="resume_queue", job_types=["crosspost"]), db_session, user)
    assert resumed["paused"] is False
    db_session.refresh(user)
    assert user.settings_json["marketplace_jobs_paused"] is False


def test_restart_selected_keeps_history_and_dispatches_same_job(db_session, monkeypatch):
    user, job = _seed_job(db_session, status="failed")
    import app.api.marketplace_jobs as jobs_api
    monkeypatch.setattr(jobs_api, "_enqueue_priority", lambda task, job_id, priority: SimpleNamespace(id="retry-task"))
    result = control_marketplace_jobs(
        JobControlRequest(action="restart_selected", job_ids=[job.id], job_types=["crosspost"], confirm_restart=True),
        db_session,
        user,
    )
    db_session.refresh(job)
    assert result["jobs"][0]["status"] == "queued"
    assert job.status == "queued"
    assert job.task_id == "retry-task"
    assert job.result_summary["previous_result"] == "preserved"
    assert job.execution_plan["operator_control_history"][0]["action"] == "restart"


def test_pause_and_resume_selected_job(db_session, monkeypatch):
    user, job = _seed_job(db_session, status="queued")
    import app.api.marketplace_jobs as jobs_api
    monkeypatch.setattr(jobs_api.celery_app.control, "revoke", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobs_api, "_enqueue_priority", lambda task, job_id, priority: SimpleNamespace(id="resumed-task"))
    paused = control_marketplace_jobs(
        JobControlRequest(action="pause_selected", job_ids=[job.id], job_types=["crosspost"]),
        db_session,
        user,
    )
    db_session.refresh(job)
    assert paused["jobs"][0]["status"] == "paused"
    assert job.status == "paused"
    resumed = control_marketplace_jobs(
        JobControlRequest(action="resume_selected", job_ids=[job.id], job_types=["crosspost"]),
        db_session,
        user,
    )
    db_session.refresh(job)
    assert resumed["jobs"][0]["status"] == "queued"
    assert job.status == "queued"
    assert job.task_id == "resumed-task"


def test_completed_job_cannot_be_replayed_or_deleted(db_session, monkeypatch):
    user, job = _seed_job(db_session, status="completed")
    import app.api.marketplace_jobs as jobs_api
    monkeypatch.setattr(jobs_api, "_enqueue_priority", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")))
    restarted = control_marketplace_jobs(
        JobControlRequest(action="restart_selected", job_ids=[job.id], job_types=["crosspost"], confirm_restart=True),
        db_session,
        user,
    )
    db_session.refresh(job)
    assert restarted["jobs"][0]["skipped"]
    assert job.status == "completed"
    dismissed = control_marketplace_jobs(JobControlRequest(action="dismiss_selected", job_ids=[job.id], job_types=["crosspost"]), db_session, user)
    db_session.refresh(job)
    assert dismissed["jobs"][0]["status"] == "dismissed"
    assert job.result_summary["previous_result"] == "preserved"
    assert job.execution_plan["operator_control_history"][-1]["action"] == "dismiss_from_console"
