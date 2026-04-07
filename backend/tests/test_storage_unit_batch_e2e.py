import io
import os
import zipfile
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite:///./test_storage_batch.db"

from fastapi.testclient import TestClient

from app.core.database import Base, SessionLocal, engine
from app.main import app
from app.models.models import StorageUnitBatch, User
import app.api.routes as routes_api


Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


def seed_user():
    db = SessionLocal()
    user = User(email=f"batch-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user.id


class DummyTask:
    id = "batch-task-1"


def test_storage_batch_from_urls(monkeypatch):
    user_id = seed_user()

    def fake_save_from_url(self, _url, prefix="batch_uploads"):
        return f"./storage/{prefix}/{uuid4()}.jpg"

    monkeypatch.setattr(routes_api.LocalStorage, "save_from_url", fake_save_from_url)
    monkeypatch.setattr(routes_api, "enqueue_storage_unit_batch_pipeline", lambda batch_id, listing_ids: DummyTask())

    client = TestClient(app)
    payload = {
        "image_urls": ["https://example.com/1.jpg", "https://example.com/2.jpg"],
        "user_id": user_id,
        "storage_unit_name": "Unit A",
        "overnight_mode": False,
    }
    response = client.post("/batch/storage-unit/from-urls", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PROCESSING"
    assert body["total_items"] == 2


def test_storage_batch_zip_queued_and_run(monkeypatch):
    user_id = seed_user()
    monkeypatch.setattr(routes_api, "enqueue_storage_unit_batch_pipeline", lambda batch_id, listing_ids: DummyTask())

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w") as zf:
        zf.writestr("photo1.jpg", b"fakejpg")
        zf.writestr("photo2.png", b"fakepng")
    archive.seek(0)

    client = TestClient(app)
    response = client.post(
        "/batch/storage-unit",
        data={"user_id": str(user_id), "storage_unit_name": "Unit B", "overnight_mode": "true"},
        files={"zip_file": ("photos.zip", archive.getvalue(), "application/zip")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "QUEUED"

    batch_id = body["id"]
    run_response = client.post(f"/batch/storage-unit/{batch_id}/run-overnight")
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "PROCESSING"

    db = SessionLocal()
    batch = db.get(StorageUnitBatch, batch_id)
    assert batch is not None
    assert batch.pipeline_task_id == "batch-task-1"
    db.close()
