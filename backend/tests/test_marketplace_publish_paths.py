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


def test_non_ebay_payload_does_not_reuse_ebay_category_id(db_session):
    user = User(email="marketplace-category-map@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Wool sweater",
        description="Warm wool sweater.",
        listing_price=40.0,
        quantity=1,
        category_id="57988",
        category_suggestion="Clothing, Shoes & Accessories > Sweaters",
        item_specifics={"Type": "Sweater"},
    )

    mercari = build_marketplace_payload(listing, "mercari")
    ebay = build_marketplace_payload(listing, "ebay")

    assert mercari["category_hint"] == "Clothing, Shoes & Accessories > Sweaters"
    assert mercari["category_hint"] != listing.category_id
    assert ebay["category_id"] == "57988"


def test_assisted_marketplace_payloads_keep_canonical_evidence_without_fabricating_etsy_fields(db_session):
    user = User(email="marketplace-fields@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        title="Vintage wool coat",
        description="A lined wool coat in very good condition.",
        listing_price=68.0,
        quantity=1,
        category_id="12345",
        category_suggestion="Clothing > Coats",
        condition="Used - Very Good",
        item_specifics={
            "brand": "Northwind",
            "Apparel Size": "M",
            "Colour": "Navy",
            "Materials": "Wool",
            "Item Weight": "1.2 kg",
            "Item Length": "32 in",
        },
        shipping_profile={
            "shipping_charge_mode": "flat",
            "parcel_size": "medium",
            "parcel_weight": "1.2 kg",
            "shipping_payer": "buyer",
        },
    )

    facebook = build_marketplace_payload(listing, "facebook")
    mercari = build_marketplace_payload(listing, "mercari")
    poshmark = build_marketplace_payload(listing, "poshmark")
    vinted = build_marketplace_payload(listing, "vinted")
    etsy = build_marketplace_payload(listing, "etsy")
    offerup = build_marketplace_payload(listing, "offerup")

    for payload in (facebook, mercari, poshmark, vinted, etsy, offerup):
        assert payload["category_hint"] == "Clothing > Coats"
        assert payload.get("brand") == "Northwind"
        assert payload.get("size") == "M"
        assert payload.get("color") == "Navy"
        assert payload.get("weight") == "1.2 kg"
        assert "category_id" not in payload
    assert mercari["shipping"]["parcel_size"] == "medium"
    assert vinted["shipping"]["parcel_weight"] == "1.2 kg"
    assert offerup["shipping"]["shipping_payer"] == "buyer"
    assert etsy["who_made"] is None
    assert etsy["when_made"] is None
