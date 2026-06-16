from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.enums import ListingStatus
from app.models.models import Listing, User
from app.services.marketplace_preflight import MarketplacePreflightService


def _seed_listing(
    db_session,
    *,
    title: str = "Camera bag",
    description: str = "Used item",
    price: float = 25.0,
    category_suggestion: str | None = "Bags & Cases",
    listing_images: list[dict] | None = None,
    image_urls: list[str] | None = None,
):
    user = User(email=f"{uuid4()}@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title=title,
        description=description,
        listing_price=price,
        category_suggestion=category_suggestion,
        condition="Used",
        condition_data={"condition_bucket": "used", "condition_source": "operator", "condition_confidence": 0.95, "operator_review_required": False},
        shipping_profile={"package_weight": 2.0, "package_dimensions": {"length": 12, "width": 10, "height": 6}, "manual_measurement_needed": False},
        listing_images=listing_images or [],
        image_urls=image_urls or [],
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)
    return user, listing


@pytest.mark.anyio
async def test_actual_photo_upload_rejects_zero_byte_file(async_client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    register = await async_client.post(
        "/auth/register",
        json={"full_name": "Photo Upload", "email": f"photo-zero-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert register.status_code == 201
    listing_response = await async_client.post(
        "/listings",
        json={"title": "Router", "description": "Used router", "listing_price": 19.99, "category_suggestion": "Networking"},
    )
    listing_id = listing_response.json()["id"]

    response = await async_client.post(
        f"/listings/{listing_id}/photos/upload",
        files=[("photos", ("empty.jpg", BytesIO(b""), "image/jpeg"))],
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_actual_photo_upload_rejects_unsupported_mime(async_client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    register = await async_client.post(
        "/auth/register",
        json={"full_name": "Photo Upload", "email": f"photo-mime-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert register.status_code == 201
    listing_response = await async_client.post(
        "/listings",
        json={"title": "Router", "description": "Used router", "listing_price": 19.99, "category_suggestion": "Networking"},
    )
    listing_id = listing_response.json()["id"]

    response = await async_client.post(
        f"/listings/{listing_id}/photos/upload",
        files=[("photos", ("fake.gif", BytesIO(b"GIF89a"), "image/gif"))],
    )
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_actual_photo_upload_attaches_as_non_reference_and_can_be_approved(async_client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    register = await async_client.post(
        "/auth/register",
        json={"full_name": "Photo Upload", "email": f"photo-approve-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert register.status_code == 201
    listing_response = await async_client.post(
        "/listings",
        json={"title": "Tripod", "description": "Used tripod", "listing_price": 29.99, "category_suggestion": "Tripods"},
    )
    listing_id = listing_response.json()["id"]

    upload = await async_client.post(
        f"/listings/{listing_id}/photos/upload",
        data={"operator_state": "suggested", "source": "actual_upload"},
        files=[("photos", ("tripod.jpg", BytesIO(b"\xff\xd8\xff\xdbjpeg"), "image/jpeg"))],
    )
    assert upload.status_code == 200
    payload = upload.json()["listing"]
    image = payload["listing_images"][0]
    assert image["is_reference"] is False
    assert image["operator_state"] == "suggested"

    approve = await async_client.post(
        f"/listings/{listing_id}/photos/approve",
        json={"storage_paths": [image["storage_path"]]},
    )
    assert approve.status_code == 200
    approved_image = approve.json()["listing"]["listing_images"][0]
    assert approved_image["operator_state"] == "approved"
    assert approved_image["is_reference"] is False


@pytest.mark.anyio
async def test_actual_photo_upload_prevents_path_traversal(async_client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    register = await async_client.post(
        "/auth/register",
        json={"full_name": "Photo Upload", "email": f"photo-path-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert register.status_code == 201
    listing_response = await async_client.post(
        "/listings",
        json={"title": "Camera", "description": "Used camera", "listing_price": 39.99, "category_suggestion": "Cameras"},
    )
    listing_id = listing_response.json()["id"]

    upload = await async_client.post(
        f"/listings/{listing_id}/photos/upload",
        data={"operator_state": "suggested", "source": "actual_upload"},
        files=[("photos", ("../../evil.jpg", BytesIO(b"\xff\xd8\xff\xdbjpeg"), "image/jpeg"))],
    )
    assert upload.status_code == 200
    stored_path = upload.json()["listing"]["listing_images"][0]["storage_path"]
    resolved = Path(stored_path).resolve()
    assert str(resolved).startswith(str(tmp_path.resolve()))
    assert ".." not in str(resolved)


def test_repair_queue_classifies_reference_pending_approved_and_invalid_images(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    actual_file = tmp_path / "uploads" / "actual.jpg"
    actual_file.parent.mkdir(parents=True, exist_ok=True)
    actual_file.write_bytes(b"\xff\xd8\xff\xdbjpeg")
    invalid_file = tmp_path / "uploads" / "broken.jpg"
    invalid_file.parent.mkdir(parents=True, exist_ok=True)
    invalid_file.write_bytes(b"")

    _, no_images = _seed_listing(db_session, title="No photos", listing_images=[], image_urls=[], category_suggestion=None)
    _, reference_only = _seed_listing(
        db_session,
        title="Reference only",
        listing_images=[{"storage_path": "/media/reference/ref.jpg", "source_platform": "amazon_vine", "operator_state": "suggested", "is_reference": True, "role": "primary"}],
        image_urls=["/media/reference/ref.jpg"],
        category_suggestion=None,
    )
    _, pending_actual = _seed_listing(
        db_session,
        title="Pending actual",
        listing_images=[{"storage_path": str(actual_file), "source_platform": "actual_upload", "operator_state": "suggested", "is_reference": False, "role": "primary"}],
        image_urls=[str(actual_file)],
        category_suggestion=None,
    )
    _, approved_actual = _seed_listing(
        db_session,
        title="Approved actual",
        listing_images=[{"storage_path": str(actual_file), "source_platform": "actual_upload", "operator_state": "approved", "is_reference": False, "role": "primary"}],
        image_urls=[str(actual_file)],
        category_suggestion=None,
    )
    _, invalid_actual = _seed_listing(
        db_session,
        title="Invalid actual",
        listing_images=[{"storage_path": str(invalid_file), "source_platform": "actual_upload", "operator_state": "approved", "is_reference": False, "role": "primary"}],
        image_urls=[str(invalid_file)],
        category_suggestion=None,
    )

    report = MarketplacePreflightService().launch_repair_queue(
        db_session,
        [no_images, reference_only, pending_actual, approved_actual, invalid_actual],
        marketplace="ebay",
        max_items=10,
        max_price=50,
    )
    by_title = {item["title"]: item for item in report["items"]}

    assert by_title["No photos"]["image_status"] == "no_images"
    assert by_title["Reference only"]["image_status"] == "reference_only"
    assert by_title["Pending actual"]["image_status"] == "actual_pending_review"
    assert by_title["Approved actual"]["image_status"] == "actual_approved"
    assert by_title["Invalid actual"]["image_status"] == "actual_file_invalid"


def test_repair_queue_requires_approved_actual_photos_for_ready_candidates(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    actual_file = tmp_path / "uploads" / "camera.jpg"
    actual_file.parent.mkdir(parents=True, exist_ok=True)
    actual_file.write_bytes(b"\xff\xd8\xff\xdbjpeg")

    _, listing = _seed_listing(
        db_session,
        title="Pending actual launch candidate",
        listing_images=[{"storage_path": str(actual_file), "source_platform": "actual_upload", "operator_state": "suggested", "is_reference": False, "role": "primary"}],
        image_urls=[str(actual_file)],
    )
    report = MarketplacePreflightService().launch_candidates(
        db_session,
        [listing],
        marketplace="ebay",
        max_items=10,
        max_price=50,
        include_warning_only=False,
        include_local_pickup=False,
        include_risky_shipping=False,
    )
    assert report["candidates"] == []
