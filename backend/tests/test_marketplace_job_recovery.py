from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.api.marketplace_jobs import retry_import_job
from app.models.models import MarketplaceImportJob, User
from app.workers import tasks


class DummyTask:
    id = "task-recovered"


def test_retry_import_job_recovers_stale_running_job(db_session, monkeypatch):
    user = User(email="owner-recovery@example.com")
    db_session.add(user)
    db_session.flush()

    job = MarketplaceImportJob(
        user_id=user.id,
        source_marketplace="ebay",
        import_mode="direct_api",
        status="running",
        payload={"max_listings": 25},
        task_id="stale-task",
        created_at=datetime.utcnow() - timedelta(minutes=40),
        updated_at=datetime.utcnow() - timedelta(minutes=25),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    monkeypatch.setattr(tasks.process_marketplace_import_job_task, "delay", lambda *_args, **_kwargs: DummyTask())
    monkeypatch.setattr("app.api.marketplace_jobs.celery_app.control.revoke", lambda *_args, **_kwargs: None)

    response = retry_import_job(job.id, db=db_session, current_user=user)

    assert response["status"] == "queued"
    assert response["can_cancel"] is True
    assert response["can_retry"] is False
    assert "Recovered by operator from stale running state" in response["last_error"]


def test_retry_import_job_rejects_fresh_running_job(db_session, monkeypatch):
    user = User(email="owner-fresh@example.com")
    db_session.add(user)
    db_session.flush()

    job = MarketplaceImportJob(
        user_id=user.id,
        source_marketplace="ebay",
        import_mode="direct_api",
        status="running",
        payload={"max_listings": 25},
        task_id="fresh-task",
        created_at=datetime.utcnow() - timedelta(minutes=4),
        updated_at=datetime.utcnow() - timedelta(minutes=2),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    monkeypatch.setattr(tasks.process_marketplace_import_job_task, "delay", lambda *_args, **_kwargs: DummyTask())

    with pytest.raises(HTTPException) as exc:
        retry_import_job(job.id, db=db_session, current_user=user)

    assert exc.value.status_code == 400
    assert "stale jobs can be retried" in exc.value.detail.lower()
