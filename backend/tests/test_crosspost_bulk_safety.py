from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core import database as database_module
from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceListing
from app.api import marketplace_jobs


@pytest.mark.anyio
async def test_bulk_crosspost_skips_live_destinations_per_listing(async_client, monkeypatch):
    registered = await async_client.post("/auth/register", json={
        "full_name": "Crosspost Safety",
        "email": f"crosspost-safety-{uuid4()}@example.test",
        "password": "test-password-123",
    })
    assert registered.status_code == 201
    user_id = registered.json()["user"]["id"]
    db = database_module.SessionLocal()
    try:
        first = Listing(user_id=user_id, status=ListingStatus.PUBLISHED, title="Published source one", description="A verified item description", quantity=1)
        second = Listing(user_id=user_id, status=ListingStatus.PUBLISHED, title="Published source two", description="Another verified item description", quantity=1)
        third = Listing(user_id=user_id, status=ListingStatus.ready, title="New destination state unknown", description="A third verified item description", quantity=1)
        db.add_all([first, second, third])
        db.flush()
        db.add_all([
            MarketplaceListing(listing_id=first.id, marketplace=MarketplaceName.ebay, marketplace_listing_id="EBAY-ONE", status=MarketplaceListingStatus.PUBLISHED),
            MarketplaceListing(listing_id=second.id, marketplace=MarketplaceName.ebay, marketplace_listing_id="EBAY-TWO", status=MarketplaceListingStatus.PUBLISHED),
            MarketplaceListing(listing_id=first.id, marketplace=MarketplaceName.mercari, marketplace_listing_id="MERCARI-ONE", status=MarketplaceListingStatus.PUBLISHED),
            MarketplaceListing(listing_id=first.id, marketplace=MarketplaceName.facebook, marketplace_listing_id="FACEBOOK-ONE", status=MarketplaceListingStatus.PUBLISHED),
            MarketplaceListing(listing_id=second.id, marketplace=MarketplaceName.mercari, marketplace_listing_id="MERCARI-ENDED", status=MarketplaceListingStatus.DELETED),
            MarketplaceListing(listing_id=second.id, marketplace=MarketplaceName.facebook, marketplace_listing_id="FACEBOOK-ENDED", status=MarketplaceListingStatus.DELETED),
        ])
        db.commit()
        first_id, second_id, third_id = first.id, second.id, third.id
        second_ebay = db.query(MarketplaceListing).filter(MarketplaceListing.listing_id == second_id, MarketplaceListing.marketplace == MarketplaceName.ebay).one()
        assert second_ebay.status == MarketplaceListingStatus.PUBLISHED
        assert second_ebay.marketplace_listing_id == "EBAY-TWO"
    finally:
        db.close()

    class ReadyPreflight:
        def preflight_listing(self, _db, _listing, market):
            return {"status": "ready", "blockers": [], "marketplace": market}

    monkeypatch.setattr(marketplace_jobs, "MarketplacePreflightService", ReadyPreflight)
    monkeypatch.setattr(marketplace_jobs, "build_marketplace_payload", lambda _listing, market: {"marketplace": market})
    monkeypatch.setattr(marketplace_jobs, "canonical_listing_readiness", lambda _listing, marketplace: {"status": "ready", "marketplace": marketplace})
    monkeypatch.setattr(marketplace_jobs, "_build_preview_for_marketplace", lambda *, listing, user, marketplace: SimpleNamespace(model_dump=lambda: {"marketplace": marketplace, "listing_id": listing.id}))
    monkeypatch.setattr(marketplace_jobs, "_enqueue_priority", lambda *_args, **_kwargs: SimpleNamespace(id="isolated-test-task"))

    response = await async_client.post("/marketplace-jobs/bulk-crosspost", json={
        "listing_ids": [first_id, second_id, third_id],
        "marketplaces": ["ebay", "mercari", "facebook"],
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["queued"] == 1
    results = {row["listing_id"]: row for row in payload["results"]}
    assert results[first_id]["status"] == "ALREADY_PUBLISHED"
    assert results[first_id]["destination_states"] == {"ebay": "ALREADY_PUBLISHED", "mercari": "ALREADY_PUBLISHED", "facebook": "ALREADY_PUBLISHED"}
    assert set(results[second_id]["target_marketplaces"]) == {"mercari", "facebook"}
    assert results[second_id]["destination_states"]["ebay"] == "ALREADY_PUBLISHED", results[second_id]
    assert results[second_id]["destination_states"]["mercari"] == "QUEUED"
    assert results[second_id]["destination_states"]["facebook"] == "QUEUED"
    assert results[third_id]["status"] == "REMOTE_RECONCILIATION_REQUIRED"
    assert results[third_id]["destination_states"]["mercari"] == "REMOTE_RECONCILIATION_REQUIRED"
    assert results[third_id]["destination_states"]["facebook"] == "REMOTE_RECONCILIATION_REQUIRED"

    db = database_module.SessionLocal()
    try:
        jobs = db.query(MarketplaceCrosspostJob).filter(MarketplaceCrosspostJob.user_id == user_id).all()
        assert len(jobs) == 1
        assert jobs[0].listing_id == second_id
        assert set(jobs[0].target_marketplaces) == {"mercari", "facebook"}
    finally:
        db.close()
