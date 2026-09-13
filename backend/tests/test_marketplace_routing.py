from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.models.models import Listing, User
from app.services.marketplace_routing import MarketplaceRoutingRule, MarketplaceRoutingService
from app.api import marketplace_jobs
from app.api.schemas import CrosspostQueueRequest


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
