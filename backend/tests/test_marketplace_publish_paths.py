from app.models.enums import ListingStatus, MarketplaceListingStatus
from app.models.models import Listing, MarketplaceListing, User
from app.workers.tasks import publish_listing_to_marketplace_task


def test_legacy_non_ebay_publish_task_uses_assisted_handoff(db_session):
    user = User(
        email="assisted-publish@example.com",
        settings_json={
            "marketplace_connections": {
                "mercari": {
                    "display_name": "Mercari Test",
                    "account_handle": "mercari-test",
                    "workflow_state": "ready",
                    "publish_mode": "manual_review",
                }
            }
        },
    )
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Camera bundle",
        description="Clean tested bundle",
        listing_price=129.0,
        quantity=1,
        marketplace_data={"targets": ["mercari"]},
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    result = publish_listing_to_marketplace_task.run(listing.id, "mercari")

    assert result["marketplace"] == "mercari"
    assert result["execution_mode"] == "manual_only"
    assert result["status"] == "planned"
    assert result["response"]["status"] == "MANUAL_HANDOFF_READY"

    marketplace_listing = (
        db_session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.listing_id == listing.id,
            MarketplaceListing.marketplace == "mercari",
        )
        .one()
    )
    assert marketplace_listing.status == MarketplaceListingStatus.PENDING
    assert marketplace_listing.raw_response["status"] == "MANUAL_HANDOFF_READY"
