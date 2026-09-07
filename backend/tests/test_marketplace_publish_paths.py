from app.models.enums import ListingStatus, MarketplaceListingStatus
from app.models.models import Listing, MarketplaceListing, User
from app.services.marketplace_field_mapper import build_marketplace_payload
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


def test_mercari_payload_trims_description_to_word_limit(db_session):
    user = User(email="mercari-trim@example.com")
    db_session.add(user)
    db_session.flush()

    description = " ".join(f"word{i}" for i in range(1205))
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Mercari test listing",
        description=description,
        listing_price=55.0,
        quantity=1,
        marketplace_data={"targets": ["mercari"]},
    )
    db_session.add(listing)
    db_session.flush()

    payload = build_marketplace_payload(listing, "mercari")
    assert payload["marketplace"] == "mercari"
    assert payload["description"] is not None
    assert len(str(payload["description"]).split()) == 1000
