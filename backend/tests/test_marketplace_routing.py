from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.models.enums import ListingStatus
from app.models.models import Listing, User
from app.services.marketplace_routing import MarketplaceRoutingRule, MarketplaceRoutingService
from app.services.listing_workspace import normalize_marketplace_data
from app.api import marketplace_jobs
from app.api.schemas import CrosspostQueueRequest
from app.api.marketplace_jobs import BulkCrosspostQueueRequest
from app.core import database as database_module
from app.models.models import MarketplaceCrosspostJob
from app.workers import tasks


def test_direct_store_sale_source_is_never_a_publish_destination():
    assert MarketplaceRoutingService.normalize_markets(["storefront_direct", "ebay"]) == ["ebay"]
    normalized = normalize_marketplace_data({"targets": ["storefront_direct", "facebook"]})
    assert "storefront_direct" not in normalized["targets"]
    with pytest.raises(ValueError, match="supported marketplace"):
        MarketplaceRoutingRule(id="bad-target", match_all=True, include_markets=["storefront_direct"])


def test_marketplace_routing_applies_matching_includes_and_global_exclusions(db_session):
    owner = User(email=f"routing-{uuid4()}@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(
        user_id=owner.id,
        title="Wool sweater",
        category_suggestion="Clothing > Sweaters",
        condition="Used - Very Good",
        listing_price=42.0,
        source_type="manual_intake",
        item_specifics={"Brand": "Northwind"},
    )
    db_session.add(listing)
    db_session.flush()

    result = MarketplaceRoutingService().resolve(
        listing,
        [
            {
                "id": "apparel",
                "priority": 10,
                "category_terms": ["clothing"],
                "brand_terms": ["northwind"],
                "condition_terms": ["used"],
                "min_price": 20,
                "max_price": 80,
                "include_markets": ["ebay", "poshmark", "vinted", "mercari"],
            },
            {
                "id": "exclude-mercari",
                "priority": 20,
                "match_all": True,
                "include_markets": ["facebook"],
                "exclude_markets": ["mercari"],
            },
            {
                "id": "wrong-type",
                "category_terms": ["electronics"],
                "include_markets": ["offerup"],
            },
        ],
    )
    assert result == {
        "marketplaces": ["ebay", "poshmark", "vinted", "facebook"],
        "source": "RULES",
        "matched_rule_ids": ["apparel", "exclude-mercari"],
    }


def test_manual_marketplace_selection_overrides_rules(db_session):
    owner = User(email=f"routing-manual-{uuid4()}@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(user_id=owner.id, title="Desk lamp")
    result = MarketplaceRoutingService().resolve(
        listing,
        [{"id": "all", "match_all": True, "include_markets": ["facebook"]}],
        manual_override=["mercari", "MERCARI", "unsupported"],
    )
    assert result == {"marketplaces": ["mercari"], "source": "MANUAL_OVERRIDE", "matched_rule_ids": []}


def test_routing_rule_requires_conditions_or_explicit_match_all():
    with pytest.raises(ValueError, match="match_all"):
        MarketplaceRoutingRule(id="empty", include_markets=["facebook"])


def test_crosspost_queue_uses_tenant_rules_when_destinations_are_not_manually_selected(db_session, monkeypatch):
    owner = User(
        email=f"routing-queue-{uuid4()}@example.com",
        password_hash="x",
        settings_json={
            "marketplace_routing": {
                "rules": [
                    {
                        "id": "home-goods",
                        "category_terms": ["home"],
                        "include_markets": ["facebook", "mercari"],
                    }
                ]
            }
        },
    )
    db_session.add(owner)
    db_session.flush()
    listing = Listing(
        user_id=owner.id,
        title="Desk lamp",
        description="A tested desk lamp in clean condition, with the original shade included.",
        listing_price=30.0,
        quantity=1,
        category_suggestion="Home > Lighting",
    )
    db_session.add(listing)
    db_session.flush()
    monkeypatch.setattr(marketplace_jobs, "_enqueue_priority", lambda *_args, **_kwargs: SimpleNamespace(id="task-routing-test"))

    result = marketplace_jobs.queue_crosspost_job(
        listing.id,
        CrosspostQueueRequest(marketplaces=[]),
        db_session,
        owner,
    )

    assert result["target_marketplaces"] == ["facebook", "mercari"]
    job = db_session.query(marketplace_jobs.MarketplaceCrosspostJob).filter_by(listing_id=listing.id).one()
    assert job.execution_plan["routing_decision"] == {
        "marketplaces": ["facebook", "mercari"],
        "source": "RULES",
        "matched_rule_ids": ["home-goods"],
    }


def test_bulk_crosspost_creates_owner_scoped_durable_jobs_only_for_ready_items(db_session, monkeypatch):
    owner = User(email=f"routing-bulk-{uuid4()}@example.com", password_hash="x")
    other = User(email=f"routing-bulk-other-{uuid4()}@example.com", password_hash="x")
    db_session.add_all([owner, other])
    db_session.flush()
    ready = Listing(
        user_id=owner.id,
        status=ListingStatus.ready,
        title="Desk lamp",
        description="A tested desk lamp, clean and ready for another workspace.",
        listing_price=25.0,
        quantity=1,
        category_suggestion="Home > Lighting",
    )
    draft = Listing(user_id=owner.id, status=ListingStatus.draft, title="Unfinished", description="Incomplete.")
    foreign = Listing(user_id=other.id, status=ListingStatus.ready, title="Private item", description="Other tenant item.")
    db_session.add_all([ready, draft, foreign])
    db_session.flush()
    monkeypatch.setattr(marketplace_jobs, "_enqueue_priority", lambda *_args, **_kwargs: SimpleNamespace(id="task-bulk-test"))

    result = marketplace_jobs.queue_bulk_crosspost_jobs(
        BulkCrosspostQueueRequest(listing_ids=[ready.id, draft.id, foreign.id, 999999], marketplaces=["facebook", "mercari"]),
        db_session,
        owner,
    )

    assert result["queued"] == 1
    result_by_listing = {row["listing_id"]: row["status"] for row in result["results"]}
    assert result_by_listing == {
        ready.id: "QUEUED",
        draft.id: "NOT_READY",
        foreign.id: "NOT_FOUND",
        999999: "NOT_FOUND",
    }
    job = db_session.query(marketplace_jobs.MarketplaceCrosspostJob).filter_by(listing_id=ready.id).one()
    assert job.user_id == owner.id
    assert job.target_marketplaces == ["facebook", "mercari"]
    assert job.task_id == "task-bulk-test"


def test_bulk_crosspost_never_defaults_to_live_ebay_without_destination_evidence(db_session, monkeypatch):
    owner = User(email=f"routing-no-default-{uuid4()}@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(
        user_id=owner.id,
        status=ListingStatus.ready,
        title="Unrouted lamp",
        description="A tested lamp with clean condition and working switch.",
        listing_price=20,
        quantity=1,
        marketplace_data={},
    )
    db_session.add(listing)
    db_session.flush()
    monkeypatch.setattr(marketplace_jobs, "_enqueue_priority", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch an implicit live target")))

    result = marketplace_jobs.queue_bulk_crosspost_jobs(
        BulkCrosspostQueueRequest(listing_ids=[listing.id]),
        db_session,
        owner,
    )

    assert result["queued"] == 0
    assert result["results"] == [{"listing_id": listing.id, "status": "NO_DESTINATIONS", "routing": {"marketplaces": [], "source": "LISTING_DEFAULT", "matched_rule_ids": []}}]
    assert db_session.query(marketplace_jobs.MarketplaceCrosspostJob).filter_by(listing_id=listing.id).count() == 0


def test_dispatch_worker_recovers_durable_queued_job_without_broker_task_id(db_session, monkeypatch):
    owner = User(email=f"routing-recovery-{uuid4()}@example.com", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(user_id=owner.id, title="Lamp", description="A useful lamp.")
    db_session.add(listing)
    db_session.flush()
    job = MarketplaceCrosspostJob(
        user_id=owner.id,
        listing_id=listing.id,
        target_marketplaces=["facebook"],
        status="queued",
        task_id=None,
    )
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(tasks, "SessionLocal", database_module.SessionLocal)
    monkeypatch.setattr(tasks.process_marketplace_crosspost_job_task, "apply_async", lambda **_kwargs: SimpleNamespace(id="recovered-task"))

    result = tasks.dispatch_queued_crosspost_jobs_task.run(limit=5)

    assert result["count"] == 1
    db_session.refresh(job)
    assert job.task_id == "recovered-task"
