import os
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite:///./test_facebook_publish_visibility.db"

from sqlalchemy import select

from app.core.database import Base, SessionLocal, engine
from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Cluster, Listing, MarketplaceCrosspostJob, MarketplaceListing, User
from app.services.marketplace_execution import resolve_execution_mode
from app.workers import tasks


Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


def _create_user_and_listing(*, marketplace_data: dict | None = None):
    db = SessionLocal()
    user = User(email=f"fb-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    cluster = Cluster(user_id=user.id, title_hint="Demo")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    listing = Listing(
        user_id=user.id,
        cluster_id=cluster.id,
        title="Demo listing",
        description="Demo",
        marketplace_data=marketplace_data or {"channels": {"facebook": {"publish_mode": "manual_or_provider"}}},
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    user_id = user.id
    listing_id = listing.id
    db.close()
    return user_id, listing_id


def test_facebook_legacy_publish_mode_resolves_to_browser_assist():
    db = SessionLocal()
    user = User(email=f"fb-resolve-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    cluster = Cluster(user_id=user.id, title_hint="Demo")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    listing = Listing(
        user_id=user.id,
        cluster_id=cluster.id,
        title="Demo listing",
        description="Demo",
        marketplace_data={"channels": {"facebook": {"publish_mode": "manual_or_provider"}}},
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    assert resolve_execution_mode(listing=listing, user=user, marketplace="facebook") == "browser_assist"
    db.close()


def test_facebook_browser_crosspost_with_visible_listing_marks_published(monkeypatch):
    user_id, listing_id = _create_user_and_listing(
        marketplace_data={"channels": {"facebook": {"publish_mode": "manual_or_provider"}}}
    )
    db = SessionLocal()
    job = MarketplaceCrosspostJob(
        user_id=user_id,
        listing_id=listing_id,
        target_marketplaces=["facebook"],
        requested_mode="approved",
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    db.close()

    monkeypatch.setattr(
        tasks,
        "execute_secondary_marketplace_path",
        lambda **_kwargs: {
            "status": "BROWSER_AUTOMATION_READY",
            "submitted": True,
            "marketplace_listing_id": "1234567890",
            "listing_urls": ["https://www.facebook.com/marketplace/item/1234567890"],
            "bridge_submission": {"bridge_response": {"job_id": "bridge-1"}},
        },
    )
    monkeypatch.setattr(
        tasks,
        "wait_for_bridge_job",
        lambda **_kwargs: {
            "status": "completed",
            "result": {
                "status": "submitted_to_marketplace",
                "submitted": True,
                "marketplace_listing_id": "1234567890",
                "listing_urls": ["https://www.facebook.com/marketplace/item/1234567890"],
            },
        },
    )
    monkeypatch.setattr(
        tasks.MarketplacePreflightService,
        "preflight_listing",
        lambda self, db, listing, marketplace: {  # noqa: ARG005
            "status": "ready",
            "blockers": [],
            "warnings": [],
            "missing_fields": [],
            "invalid_fields": [],
            "required_operator_actions": [],
        },
    )

    result = tasks.process_marketplace_crosspost_job_task.run(job.id)
    assert result["status"] == "completed"

    db = SessionLocal()
    row = db.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.listing_id == listing_id,
            MarketplaceListing.marketplace == MarketplaceName.facebook,
        )
    ).scalar_one()
    assert row.status == MarketplaceListingStatus.PUBLISHED
    assert row.marketplace_listing_id == "1234567890"
    db.close()
