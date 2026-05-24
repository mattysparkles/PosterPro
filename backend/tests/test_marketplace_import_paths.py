from datetime import datetime, timedelta

from app.models.enums import ListingStatus
from app.models.models import Listing, MarketplaceAccount, MarketplaceImportJob, User
from app.workers import tasks


def test_ebay_import_job_creates_draft_listings(db_session, monkeypatch):
    user = User(email="ebay-import@example.com")
    db_session.add(user)
    db_session.flush()

    account = MarketplaceAccount(
        user_id=user.id,
        marketplace="ebay",
        external_account_id="ebay-user",
        access_token="token",
    )
    db_session.add(account)
    db_session.flush()

    job = MarketplaceImportJob(
        user_id=user.id,
        source_marketplace="ebay",
        import_mode="direct_api",
        status="queued",
        payload={"max_listings": 5},
    )
    db_session.add(job)
    db_session.commit()

    async def fake_get_active_ebay_listings(_user_id, _db, *, limit=50):
        assert limit == 5
        return [
            {
                "source_listing_reference": "https://www.ebay.com/itm/12345",
                "source_url": "https://www.ebay.com/itm/12345",
                "title": "Vintage Camera Body",
                "description": "Clean tested body only",
                "listing_price": 149.99,
                "price": 149.99,
                "quantity": 1,
                "image_urls": ["https://example.com/camera.jpg"],
                "item_specifics": {"Brand": "Canon"},
                "category_id": "30090",
                "condition": "Used",
                "tags": ["ebay", "imported"],
                "source_identifiers": {
                    "ebay_listing_id": "12345",
                    "offer_id": "offer-12345",
                    "sku": "sku-12345",
                },
            }
        ]

    monkeypatch.setattr(tasks, "get_active_ebay_listings", fake_get_active_ebay_listings)

    result = tasks.process_marketplace_import_job_task.run(job.id)

    assert result["new_listing_ids"]
    created_listing = db_session.get(Listing, result["new_listing_ids"][0])
    assert created_listing is not None
    assert created_listing.title == "Vintage Camera Body"
    assert created_listing.source_type == "ebay_import"
    assert created_listing.status == ListingStatus.draft
    assert created_listing.source_metadata["raw_payload"]["source_identifiers"]["ebay_listing_id"] == "12345"


def test_ebay_import_job_reuses_duplicate_listing_by_title_and_price(db_session, monkeypatch):
    user = User(email="ebay-duplicate@example.com")
    db_session.add(user)
    db_session.flush()

    account = MarketplaceAccount(
        user_id=user.id,
        marketplace="ebay",
        external_account_id="ebay-user",
        access_token="token",
    )
    db_session.add(account)
    db_session.flush()

    existing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Vintage Camera Body",
        description="Imported from Facebook earlier",
        listing_price=149.99,
        source_type="facebook_import",
        source_metadata={"source_listing_reference": "https://www.facebook.com/marketplace/item/abc"},
        needs_review=True,
    )
    db_session.add(existing)
    db_session.flush()

    job = MarketplaceImportJob(
        user_id=user.id,
        source_marketplace="ebay",
        import_mode="direct_api",
        status="queued",
        payload={"max_listings": 5},
    )
    db_session.add(job)
    db_session.commit()

    async def fake_get_active_ebay_listings(_user_id, _db, *, limit=50):
        return [
            {
                "source_listing_reference": "https://www.ebay.com/itm/12345",
                "source_url": "https://www.ebay.com/itm/12345",
                "title": "Vintage Camera Body",
                "description": "Same item from eBay",
                "listing_price": 149.99,
                "price": 149.99,
                "quantity": 1,
                "image_urls": ["https://example.com/camera.jpg"],
                "item_specifics": {"Brand": "Canon"},
                "category_id": "30090",
                "condition": "Used",
                "tags": ["ebay", "imported"],
                "source_identifiers": {
                    "ebay_listing_id": "12345",
                    "offer_id": "offer-12345",
                    "sku": "sku-12345",
                },
            }
        ]

    monkeypatch.setattr(tasks, "get_active_ebay_listings", fake_get_active_ebay_listings)

    result = tasks.process_marketplace_import_job_task.run(job.id)

    assert result["new_listing_ids"] == []
    assert result["reused_listing_ids"] == [existing.id]
    reused = db_session.get(Listing, existing.id)
    assert reused.source_metadata["raw_payload"]["source_identifiers"]["ebay_listing_id"] == "12345"
    assert any(item.get("source_marketplace") == "ebay" for item in (reused.source_metadata.get("import_sources") or []))
    assert "ebay" in ((reused.marketplace_data or {}).get("import_sources") or [])


def test_ebay_import_job_reuses_duplicate_listing_by_exact_identifier(db_session, monkeypatch):
    user = User(email="ebay-identifier@example.com")
    db_session.add(user)
    db_session.flush()

    account = MarketplaceAccount(
        user_id=user.id,
        marketplace="ebay",
        external_account_id="ebay-user",
        access_token="token",
    )
    db_session.add(account)
    db_session.flush()

    existing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Old Placeholder",
        description="Different source payload",
        listing_price=80.00,
        source_type="facebook_import",
        source_metadata={
            "source_listing_reference": "https://www.ebay.com/itm/99999",
            "raw_payload": {
                "source_identifiers": {
                    "sku": "sku-12345",
                }
            },
        },
        needs_review=True,
    )
    db_session.add(existing)
    db_session.flush()

    job = MarketplaceImportJob(
        user_id=user.id,
        source_marketplace="ebay",
        import_mode="direct_api",
        status="queued",
        payload={"max_listings": 5},
    )
    db_session.add(job)
    db_session.commit()

    async def fake_get_active_ebay_listings(_user_id, _db, *, limit=50):
        return [
            {
                "source_listing_reference": "https://www.ebay.com/itm/12345",
                "source_url": "https://www.ebay.com/itm/12345",
                "title": "Fresh Better Title",
                "description": "Same listing recovered from eBay",
                "listing_price": 149.99,
                "price": 149.99,
                "quantity": 1,
                "source_identifiers": {
                    "ebay_listing_id": "12345",
                    "sku": "sku-12345",
                },
            }
        ]

    monkeypatch.setattr(tasks, "get_active_ebay_listings", fake_get_active_ebay_listings)

    result = tasks.process_marketplace_import_job_task.run(job.id)

    assert result["new_listing_ids"] == []
    assert result["reused_listing_ids"] == [existing.id]
    reused = db_session.get(Listing, existing.id)
    assert reused.title == "Old Placeholder"
    assert any(item.get("source_marketplace") == "ebay" for item in (reused.source_metadata.get("import_sources") or []))


def test_import_job_reuses_duplicate_listing_by_image_overlap_and_repairs_placeholder_title(db_session, monkeypatch):
    user = User(email="image-dedupe@example.com")
    db_session.add(user)
    db_session.flush()

    account = MarketplaceAccount(
        user_id=user.id,
        marketplace="ebay",
        external_account_id="ebay-user",
        access_token="token",
    )
    db_session.add(account)
    db_session.flush()

    existing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Chats",
        description="Imported from Facebook earlier",
        listing_price=10.00,
        image_urls=["./storage/marketplace_imports/camera.jpg"],
        source_type="facebook_import",
        source_metadata={"source_listing_reference": "https://www.facebook.com/marketplace/item/abc"},
        needs_review=True,
    )
    db_session.add(existing)
    db_session.flush()

    job = MarketplaceImportJob(
        user_id=user.id,
        source_marketplace="ebay",
        import_mode="direct_api",
        status="queued",
        payload={"max_listings": 5},
    )
    db_session.add(job)
    db_session.commit()

    async def fake_get_active_ebay_listings(_user_id, _db, *, limit=50):
        return [
            {
                "source_listing_reference": "https://www.ebay.com/itm/12345",
                "source_url": "https://www.ebay.com/itm/12345",
                "title": "Vintage Camera Body",
                "description": "Same item from eBay",
                "listing_price": 149.99,
                "price": 149.99,
                "quantity": 1,
                "image_urls": ["https://example.com/camera.jpg"],
                "source_identifiers": {
                    "ebay_listing_id": "12345",
                },
            }
        ]

    monkeypatch.setattr(tasks, "get_active_ebay_listings", fake_get_active_ebay_listings)

    result = tasks.process_marketplace_import_job_task.run(job.id)

    assert result["new_listing_ids"] == []
    assert result["reused_listing_ids"] == [existing.id]
    reused = db_session.get(Listing, existing.id)
    assert reused.title == "Vintage Camera Body"


def test_import_job_stale_helper_marks_old_running_jobs(db_session):
    job = MarketplaceImportJob(
        user_id=1,
        source_marketplace="ebay",
        import_mode="direct_api",
        status="running",
        created_at=datetime.utcnow() - timedelta(minutes=45),
        updated_at=datetime.utcnow() - timedelta(minutes=30),
    )

    assert tasks._import_job_is_stale(job) is True
