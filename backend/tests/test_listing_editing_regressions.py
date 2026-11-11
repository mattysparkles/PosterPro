from uuid import uuid4

import pytest

from app.api import routes as listings_routes
from app.models.enums import ListingStatus
from app.models.models import Listing, User
from app.services.listing_processing import ListingProcessingService


@pytest.mark.anyio
async def test_category_and_listing_edits_persist_without_publish(async_client, monkeypatch):
    queued = []
    if hasattr(listings_routes, "queue_listing_marketplace_preflight"):
        monkeypatch.setattr(
            listings_routes,
            "queue_listing_marketplace_preflight",
            lambda *args, **kwargs: queued.append((args, kwargs)),
        )

    registered = await async_client.post(
        "/auth/register",
        json={"full_name": "Editing Regression", "email": f"edit-{uuid4()}@example.com", "password": "supersecret123"},
    )
    assert registered.status_code == 201
    created = await async_client.post(
        "/listings",
        json={
            "title": "Original test item",
            "description": "Original operator description",
            "category_id": "111",
            "category_suggestion": "Original category",
            "listing_price": 31.5,
            "image_urls": ["https://example.test/original.jpg"],
            "item_specifics": {"Brand": "Operator brand"},
            "marketplace_data": {"targets": []},
        },
    )
    assert created.status_code == 200
    listing_id = created.json()["id"]

    saved = await async_client.patch(
        f"/listings/{listing_id}",
        json={
            "category_id": "222",
            "category_suggestion": "Operator selected category",
            "title": "Edited operator title",
            "description": "Edited operator description",
        },
    )
    assert saved.status_code == 200, saved.text

    reloaded = await async_client.get(f"/listings/{listing_id}")
    assert reloaded.status_code == 200
    row = reloaded.json()
    assert row["category_id"] == "222"
    assert row["category_suggestion"] == "Operator selected category"
    assert row["title"] == "Edited operator title"
    assert row["description"] == "Edited operator description"
    assert row["listing_price"] == 31.5
    assert row["image_urls"] == ["https://example.test/original.jpg"]
    assert row["item_specifics"] == {"Brand": "Operator brand"}
    assert row["marketplace_data"].get("publish_jobs") is None
    assert row["marketplace_statuses"] == []
    if hasattr(listings_routes, "queue_listing_marketplace_preflight"):
        assert len(queued) == 1


def test_background_listing_processing_preserves_operator_category_and_copy(db_session):
    user = User(email=f"edit-worker-{uuid4()}@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Operator saved title",
        description="Operator saved copy.",
        category_id="987654",
        category_suggestion="Operator > Chosen Category",
        listing_price=42.0,
        suggested_price=42.0,
        condition="Used",
        item_specifics={"Brand": "Operator brand"},
        image_urls=["/media/uploads/operator-item.jpg"],
        listing_images=[{"storage_path": "/media/uploads/operator-item.jpg", "operator_state": "approved", "role": "primary"}],
        source_metadata={
            "recovery": {
                "field_provenance": {
                    "title": "human_operator",
                    "description": "human_operator",
                    "category_id": "human_operator",
                    "category_suggestion": "human_operator",
                    "item_specifics": "human_operator",
                },
                "operator_locked_fields": ["title", "description", "category_id", "category_suggestion", "item_specifics"],
            }
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    ListingProcessingService().resume_backlog(db_session, user_id=user.id, limit=5, worker_id="editing-regression")
    db_session.refresh(listing)
    assert listing.title == "Operator saved title"
    assert listing.description == "Operator saved copy."
    assert listing.category_id == "987654"
    assert listing.category_suggestion == "Operator > Chosen Category"
    assert listing.item_specifics == {"Brand": "Operator brand"}
