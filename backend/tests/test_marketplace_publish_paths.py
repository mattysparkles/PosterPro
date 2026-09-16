from app.models.enums import ListingStatus, MarketplaceListingStatus
from app.models.models import Listing, MarketplaceListing, User
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.marketplace_field_mapper import build_marketplace_payload, marketplace_description_variants, persist_marketplace_description_variants
from app.workers import tasks
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


def test_single_listing_publish_uses_hosted_fallback_when_extension_is_offline(db_session, monkeypatch):
    user = User(
        email="hosted-fallback@example.com",
        settings_json={"marketplace_connections": {"facebook": {"publish_mode": "browser_assist"}}},
    )
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Small side table",
        description="Wood side table in clean condition.",
        listing_price=30.0,
        quantity=1,
        marketplace_data={"targets": ["facebook"]},
    )
    db_session.add(listing)
    db_session.commit()
    execution_modes = []
    monkeypatch.setattr(tasks, "execute_secondary_marketplace_path", lambda **kwargs: execution_modes.append(kwargs["execution_mode"]) or {"status": "BROWSER_AUTOMATION_READY"})
    monkeypatch.setattr(MarketplacePreflightService, "preflight_listing", lambda _self, _db, row, market: {"listing_id": row.id, "marketplace": market, "status": "ready", "blockers": [], "warnings": []})

    result = publish_listing_to_marketplace_task.run(listing.id, "facebook")

    assert result["execution_mode"] == "hosted_browser_assist"
    assert execution_modes == ["hosted_browser_assist"]


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
    assert len(str(payload["description"])) <= 1000


def test_marketplace_descriptions_keep_rich_canonical_copy_separate(db_session):
    user = User(email="marketplace-description-variants@example.com")
    db_session.add(user); db_session.flush()
    canonical = "Portable camping toilet with a sealed waste tank, fresh-water flush reservoir, level indicators, rotating spout, carry handle, and removable components for RV travel. " * 8
    listing = Listing(user_id=user.id, title="Portable Camping Toilet", description=canonical, canonical_description=canonical, listing_price=89, quantity=1)
    variants = marketplace_description_variants(listing)
    assert len(variants["canonical"]) > 1000
    assert len(variants["ebay"]) > 1000
    assert len(variants["facebook"]) > 1000
    assert len(variants["mercari"]) <= 1000
    assert "sealed waste tank" in variants["mercari"]
    assert build_marketplace_payload(listing, "ebay")["description"] == variants["ebay"]
    assert build_marketplace_payload(listing, "facebook")["description"] == variants["facebook"]
    assert build_marketplace_payload(listing, "mercari")["description"] == variants["mercari"]


def test_marketplace_variant_persistence_preserves_operator_override(db_session):
    user = User(email="marketplace-description-override@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, title="Test product", description="Rich canonical copy with supported product facts and buyer context.", marketplace_descriptions={"mercari": "Operator edited Mercari copy."})
    rendered = persist_marketplace_description_variants(listing)
    assert rendered["mercari"] == "Operator edited Mercari copy."
    assert rendered["ebay"] == listing.description
    assert rendered["facebook"] == listing.description


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
