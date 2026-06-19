from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.models.enums import EbayPublishStatus, ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, User
from app.services.operator_command_service import (
    LIVE_EBAY_REPRICE_CONFIRMATION_PHRASE,
    OperatorCommandService,
)


def _seed_live_ebay_listing(db_session, *, user: User, title: str, price: float, posted_days_ago: int) -> Listing:
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.ready,
        title=title,
        description="Test listing",
        listing_price=price,
        suggested_price=price,
        condition="New",
        quantity=1,
        ebay_listing_id=f"ebay-{title.lower().replace(' ', '-')}",
        ebay_publish_status=EbayPublishStatus.POSTED,
    )
    db_session.add(listing)
    db_session.flush()
    row = MarketplaceListing(
        listing_id=listing.id,
        marketplace=MarketplaceName.ebay,
        marketplace_listing_id=listing.ebay_listing_id,
        status=MarketplaceListingStatus.PUBLISHED,
        raw_response={"status": "PUBLISHED"},
    )
    posted_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=posted_days_ago)
    row.created_at = posted_at
    row.updated_at = posted_at
    db_session.add(row)
    db_session.commit()
    db_session.refresh(listing)
    return listing


def test_operator_command_preview_finds_old_live_ebay_listings(db_session):
    user = User(email="operator-command-preview@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    _seed_live_ebay_listing(db_session, user=user, title="Old Listing", price=100.0, posted_days_ago=10)
    _seed_live_ebay_listing(db_session, user=user, title="Fresh Listing", price=80.0, posted_days_ago=2)

    result = asyncio.run(
        OperatorCommandService().handle_prompt(
            db_session,
            user=user,
            prompt="lower all item prices by ten percent if they have been posted for more than 1 week on ebay",
            dry_run=True,
            apply_live=False,
        )
    )

    assert result["parsed"] is True
    assert result["command_type"] == "ebay_reprice_by_listing_age"
    assert result["summary"]["eligible_count"] == 1
    assert result["listings"][0]["title"] == "Old Listing"
    assert result["listings"][0]["new_price"] == 90.0


def test_operator_command_live_apply_requires_confirmation(db_session):
    user = User(email="operator-command-confirm@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    _seed_live_ebay_listing(db_session, user=user, title="Needs Confirmation", price=120.0, posted_days_ago=14)

    result = asyncio.run(
        OperatorCommandService().handle_prompt(
            db_session,
            user=user,
            prompt="lower all item prices by 10 percent if they have been listed for more than 1 week on ebay",
            dry_run=False,
            apply_live=True,
            confirmation_phrase="wrong phrase",
        )
    )

    assert result["parsed"] is True
    assert result["summary"]["eligible_count"] == 1
    assert LIVE_EBAY_REPRICE_CONFIRMATION_PHRASE in (result["message"] or "")


def test_operator_command_live_apply_updates_listing_prices(db_session, monkeypatch):
    user = User(email="operator-command-live@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    listing = _seed_live_ebay_listing(db_session, user=user, title="Live Update", price=50.0, posted_days_ago=9)

    async def _fake_revise(listing_obj, db):  # noqa: ARG001
        db.commit()
        return {"status": "UPDATED", "listing_id": listing_obj.id}

    monkeypatch.setattr("app.services.operator_command_service.revise_ebay_listing", _fake_revise)

    result = asyncio.run(
        OperatorCommandService().handle_prompt(
            db_session,
            user=user,
            prompt="lower all item prices by 10 percent if they have been listed for more than 1 week on ebay",
            dry_run=False,
            apply_live=True,
            confirmation_phrase=LIVE_EBAY_REPRICE_CONFIRMATION_PHRASE,
        )
    )

    refreshed = db_session.get(Listing, listing.id)
    assert result["summary"]["updated_count"] == 1
    assert refreshed is not None
    assert refreshed.listing_price == 45.0
    assert refreshed.suggested_price == 45.0
