from app.api.routes import _serialize_listing_response
from app.models.enums import ListingStatus
from app.models.models import Listing, MarketplaceImportJob, User
from app.services.listing_review import (
    derive_condition_data,
    derive_shipping_profile,
    normalize_listing_images,
    sync_listing_review_state,
    summarize_listing_readiness,
)
from app.workers import tasks


def test_listing_review_metadata_persists_on_listing_model(db_session):
    user = User(email="review-persist@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Open Box Router",
        image_urls=["/media/uploads/router-front.jpg"],
        listing_images=normalize_listing_images(
            image_urls=["/media/uploads/router-front.jpg"],
            source_platform="upload",
            approved=True,
        ),
        condition="Needs review",
        condition_data=derive_condition_data(
            listing={"condition": None, "source_type": "amazon_vine"},
            source_type="amazon_vine",
            existing={"condition_source": "import"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Open Box Router"},
            existing={"estimated": True, "manual_measurement_needed": True},
        ),
        needs_review=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    assert listing.listing_images[0]["source_platform"] == "upload"
    assert listing.condition_data["condition_bucket"] == "open_box_or_used_unknown"
    assert listing.shipping_profile["manual_measurement_needed"] is True


def test_imported_reference_images_are_labeled_and_failed_imports_are_preserved(db_session, monkeypatch):
    user = User(email="import-images@example.com")
    db_session.add(user)
    db_session.flush()

    job = MarketplaceImportJob(
        user_id=user.id,
        source_marketplace="ebay",
        import_mode="direct_api",
        status="queued",
        payload={},
    )
    db_session.add(job)
    db_session.flush()

    monkeypatch.setattr(tasks, "_best_effort_localize_bridge_assets", lambda assets: ([], []))
    monkeypatch.setattr(
        tasks,
        "_best_effort_localize_import_images",
        lambda urls: (["/media/imports/camera-front.jpg"], [{"source_url": "https://bad.example/img.jpg", "reason": "download_failed"}]),
    )

    listing, created = tasks._create_imported_listing(
        db=db_session,
        user_id=user.id,
        source_marketplace="ebay",
        import_job_id=job.id,
        import_mode="direct_api",
        source_listing_reference="ref-1",
        raw_payload={"source_url": "https://www.ebay.com/itm/123"},
        normalized={
          "title": "Imported Camera",
          "description": "Imported description",
          "image_urls": ["https://example.com/camera-front.jpg"],
          "item_specifics": {"Brand": "Canon"},
          "condition": "Used",
          "quantity": 1,
        },
    )

    assert created is True
    assert listing.listing_images[0]["source_platform"] == "ebay"
    assert listing.listing_images[0]["is_reference"] is True
    assert listing.source_metadata["image_import_failures"][0]["reason"] == "download_failed"


def test_shipping_estimate_structure_and_readiness_blockers():
    shipping = derive_shipping_profile(
        listing={"title": "Glass lamp with lithium battery", "description": "Fragile collectible lamp"},
        existing={"estimated": True, "manual_measurement_needed": True},
    )
    condition = derive_condition_data(
        listing={"condition": None, "source_type": "amazon_vine"},
        source_type="amazon_vine",
        existing={},
    )
    readiness = summarize_listing_readiness(
        listing_images=normalize_listing_images(
            image_urls=["/media/reference/lamp.jpg"],
            source_platform="amazon",
            default_is_reference=True,
            approved=False,
        ),
        condition_data=condition,
        shipping_profile=shipping,
        listing={"listing_price": 25, "category_suggestion": "Home"},
    )

    assert shipping["fragile"] is True
    assert shipping["battery"] is True
    assert readiness["blocked_for_publish"] is True
    assert "Only source/reference images attached" in readiness["blockers"]
    assert readiness["shipping_checklist"]["manual_measurement_needed"] is True


def test_manual_override_behavior_survives_serialization(db_session):
    user = User(email="override@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.ready,
        title="Keurig Coffee Maker",
        description="Used machine with accessories included.",
        listing_price=49.99,
        condition="Used",
        condition_data={
            "condition_bucket": "used",
            "condition_source": "operator",
            "condition_confidence": 0.95,
            "operator_review_required": False,
            "included_accessories": ["drip tray", "water tank"],
        },
        shipping_profile={
            "package_weight": 8.5,
            "package_dimensions": {"length": 18, "width": 12, "height": 14},
            "shipping_class_suggestion": "ups_ground",
            "manual_measurement_needed": False,
        },
        listing_images=[
            {
                "storage_path": "/media/uploads/keurig-front.jpg",
                "source_platform": "upload",
                "role": "primary",
                "operator_state": "approved",
                "display_order": 0,
                "is_reference": False,
                "confidence": 1.0,
            }
        ],
        image_urls=["/media/uploads/keurig-front.jpg"],
    )
    db_session.add(listing)
    db_session.flush()
    payload = _serialize_listing_response(listing)

    assert payload["condition_data"]["condition_source"] == "operator"
    assert payload["shipping_profile"]["shipping_class_suggestion"] == "ups_ground"
    assert payload["readiness_summary"]["ready_for_publish"] is True


def test_sync_listing_review_state_keeps_vine_source_images_as_reference(db_session):
    user = User(email="vine-reference@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        title="Amazon Vine Test Item",
        image_urls=["storage/vine-search/test-image.jpg"],
        source_type="amazon_vine",
        source_metadata={"amazon_source_page_url": "https://www.amazon.com/dp/B000TEST"},
    )

    sync_listing_review_state(listing=listing)

    assert listing.listing_images[0]["is_reference"] is True
    assert listing.listing_images[0]["operator_state"] == "suggested"
