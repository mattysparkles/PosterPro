from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.enums import ListingStatus
from app.models.models import Listing, User, VineImportBatch, VineImportItem
from app.services.listing_processing import ListingProcessingService
from app.services.photo_enrichment import PhotoEnrichmentService
from app.services.listing_review import derive_condition_data, derive_shipping_profile, normalize_listing_images
from app.services.pricing_research_service import compute_listing_quality_summary


def _approved_images(*paths: str) -> list[dict]:
    return normalize_listing_images(
        listing_images=[{"storage_path": path, "operator_state": "approved", "is_reference": False} for path in paths],
        approved=True,
        default_is_reference=False,
        source_platform="amazon",
    )


def test_resume_backlog_promotes_ready_vine_listing_to_needs_review(db_session):
    user = User(email="backfill-vine@example.com")
    db_session.add(user)
    db_session.flush()

    batch = VineImportBatch(user_id=user.id, filename="vine.csv", source_type="xlsx")
    db_session.add(batch)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="amazon_vine",
        title="D-Lumina Magnetic RV Screen Door 30 inch x 80 inch",
        description="A specific magnetic RV screen door listing with product details and accessory notes.",
        category_suggestion="Automotive > RV, Trailer & Camper Parts & Accessories > Exterior Parts & Accessories",
        listing_price=21.0,
        suggested_price=21.0,
        condition="New",
        condition_data=derive_condition_data(
            listing={"condition": "New", "source_type": "amazon_vine"},
            source_type="amazon_vine",
            existing={"operator_review_required": False, "condition_bucket": "new_in_box", "new_in_box": True},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "D-Lumina Magnetic RV Screen Door 30 inch x 80 inch", "description": "Specific listing", "listing_price": 21.0},
            existing={
                "package_weight": "4 lb",
                "package_dimensions": {"length": 14, "width": 10, "height": 4},
                "manual_measurement_needed": False,
                "estimated": True,
            },
        ),
        listing_images=_approved_images("/media/uploads/vine-rv-screen-door.jpg"),
        image_urls=["/media/uploads/vine-rv-screen-door.jpg"],
        marketplace_data={
            "pricing_analysis": {"current_price": 21.0, "price_confidence": 0.91},
        },
        source_metadata={
            "amazon_product_facts": {
                "title": "D-Lumina Magnetic RV Screen Door 30 inch x 80 inch",
                "current_price": 21.0,
                "specifications": {
                    "Brand": "D-Lumina",
                    "Type": "RV Screen Door",
                    "Size": "30 inch x 80 inch",
                },
                "breadcrumbs": ["Automotive", "RV, Trailer & Camper Parts & Accessories"],
            }
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.flush()
    db_session.add(
        VineImportItem(
            batch_id=batch.id,
            user_id=user.id,
            asin="B0TESTVINE1",
            product_name="D-Lumina Magnetic RV Screen Door",
            estimated_tax_value=21.0,
            eligibility_status="eligible",
            media_status="cached",
            inventory_item_id=listing.id,
            listing_id=listing.id,
            source_confidence="high",
        )
    )
    db_session.commit()

    service = ListingProcessingService()
    result = service.resume_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.needs_review is True
    assert listing.processing_state == "complete"
    assert listing.processing_last_success_at is not None
    assert (listing.marketplace_data or {}).get("quality_summary", {}).get("ready_for_publish_queue") is True


def test_vine_source_metadata_can_resolve_specificity_without_upc(db_session):
    user = User(email="vine-metadata-evidence@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="amazon_vine",
        title="Item B0GT3S6CYV",
        description="Recovered retail item with generic placeholder copy.",
        category_suggestion="Other > Needs category review",
        listing_price=18.0,
        suggested_price=18.0,
        condition="New",
        condition_data=derive_condition_data(
            listing={"condition": "New", "source_type": "amazon_vine"},
            source_type="amazon_vine",
            existing={"operator_review_required": False, "condition_bucket": "new_in_box", "new_in_box": True},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Item B0GT3S6CYV", "description": "Recovered retail item", "listing_price": 18.0},
            existing={"package_weight": "2 lb", "package_dimensions": {"length": 12, "width": 10, "height": 4}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/vine-item.jpg"),
        image_urls=["/media/uploads/vine-item.jpg"],
        source_metadata={
            "amazon_product_facts": {
                "asin": "B0GT3S6CYV",
                "title": "Gliztech Led Boat Lights, Marine Pontoon Led Lights with App (22FT)",
                "brand": "Gliztech",
                "product_name": "Gliztech Led Boat Lights",
                "model": "22FT",
                "specifications": {"Brand": "Gliztech", "Type": "Boat Lights"},
            }
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    pricing = {"current_price": 18.0, "price_confidence": 0.91}
    quality = compute_listing_quality_summary(listing, pricing_analysis=pricing)

    assert quality["specificity_status"] == "trusted_for_draft"
    assert quality["ready_for_publish_queue"] is True
    assert "generic_or_placeholder_category" not in quality["blockers"]


def test_recovery_image_identity_can_resolve_generic_photographed_item(db_session, monkeypatch):
    user = User(email="recovery-image-identity@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Recovered photographed inventory item requiring identity review",
        description="Recovered from preserved inventory photos. Review the attached photos for condition.",
        category_suggestion="General resale > Needs category review",
        listing_price=21.0,
        suggested_price=21.0,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Recovered photographed inventory item requiring identity review", "description": "Recovered from preserved inventory photos.", "listing_price": 21.0},
            existing={"package_weight": "4 lb", "package_dimensions": {"length": 12, "width": 10, "height": 8}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/ryobi-link.jpg"),
        image_urls=["/media/uploads/ryobi-link.jpg"],
        source_metadata={},
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    service = ListingProcessingService()
    monkeypatch.setattr(
        service.photo_enrichment,
        "enrich_group",
        lambda paths: {
            "title": "RYOBI LINK Modular Wall Storage Accessory Black Green",
            "description": "RYOBI LINK modular wall storage accessory for garage organization.",
            "category_suggestion": "Home & Garden > Home Organization > Garage Storage & Organization",
            "item_specifics": {"Brand": "RYOBI", "Product Type": "Wall Storage Accessory"},
            "tags": ["ryobi", "link"],
            "estimated_value": 24.99,
            "photo_evidence": [{"media_id": 1, "product_name": "RYOBI LINK Modular Wall Storage Accessory Black Green"}],
            "group_synthesis": {
                "identity": {
                    "title": "RYOBI LINK Modular Wall Storage Accessory Black Green",
                    "brand": "RYOBI",
                    "model": "LINK",
                    "identifier": None,
                },
                "identity_confidence": 0.91,
                "description": "RYOBI LINK modular wall storage accessory for garage organization.",
                "category": "Home & Garden > Home Organization > Garage Storage & Organization",
                "item_specifics": {"Brand": "RYOBI", "Product Type": "Wall Storage Accessory"},
                "tags": ["ryobi", "link"],
                "usable_media_ids": [1],
                "quality_gate": "trusted_for_draft",
            },
            "photos_evaluated": len(paths),
            "photos_excluded": [],
        },
    )
    monkeypatch.setattr(
        service.listing_ai,
        "generate",
        lambda signals: {
            "title": "RYOBI LINK Modular Wall Storage Accessory Black Green",
            "description": "RYOBI LINK modular wall storage accessory for garage organization.",
            "item_specifics": {"Brand": "RYOBI", "Product Type": "Wall Storage Accessory"},
            "tags": ["ryobi", "link"],
            "estimated_value": 24.99,
        },
    )

    listing.processing_state = "needs_attention"
    listing.processing_stage = "image_identification"
    listing.processing_blocking_reason = "needs_image_identification"
    db_session.add(listing)
    db_session.commit()

    result = service.resume_image_identification_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.needs_review is True
    assert listing.processing_state == "complete"
    assert "RYOBI" in (listing.title or "")
    assert "LINK" in (listing.title or "")
    assert "image_identity_v1" in (listing.source_metadata or {}).get("recovery", {})


def test_recovery_listing_can_inherit_sibling_merged_group_evidence(db_session, monkeypatch):
    user = User(email="recovery-merged-sibling@example.com")
    db_session.add(user)
    db_session.flush()

    canonical = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Pennyfarthing Galleries, Torquay, England",
        description="A framed silhouette artwork by Enid Elliott Linder.",
        category_suggestion="Art",
        listing_price=19.99,
        suggested_price=19.99,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Pennyfarthing Galleries, Torquay, England", "description": "A framed silhouette artwork by Enid Elliott Linder.", "listing_price": 19.99},
            existing={"package_weight": "4 lb", "package_dimensions": {"length": 18, "width": 14, "height": 4}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/art-back.jpg"),
        image_urls=["/media/uploads/art-back.jpg"],
        source_metadata={
            "recovery": {
                "merged_into_recovery_group_id": 908,
                "merged_into_recovery_item_id": "RECOVERY-REVIEW-0073-I010",
                "full_group_evidence_v3": {
                    "pipeline_version": "recovery_full_group_evidence_v3",
                    "group_kind": "one_item",
                    "identity": {"title": "Pennyfarthing Galleries, Torquay, England", "brand": "Pennyfarthing Galleries", "model": "N/A", "mpn": "N/A", "identifier": "N/A"},
                    "identity_confidence": 0.9,
                    "category": "art",
                    "item_specifics": {"artist": "Enid Elliott Linder", "type": "silhouette", "proof": "artist's proof"},
                    "included_parts": ["framed silhouette artwork"],
                    "condition": "good condition",
                    "damage": ["none visible"],
                    "testing_status": "N/A",
                    "conflicting_evidence": [],
                    "supporting_media_ids": {},
                    "usable_media_ids": [1, 2],
                    "photo_count": 2,
                    "field_confidence": {},
                    "reason_selected": "test",
                    "identity_candidates": [{"title": "A Signed Silhouette", "brand": "Pennyfarthing Galleries", "model": "N/A", "confidence": 0.9, "media_id": 1}],
                    "placeholders": ["price", "weight"],
                    "review_flags": [],
                    "quality_gate": "blocked_placeholder_data",
                },
            }
        },
        needs_review=False,
    )
    sibling = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Unknown Item",
        description="Recovered from preserved inventory photos.",
        category_suggestion="General resale > Needs category review",
        listing_price=19.99,
        suggested_price=19.99,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Unknown Item", "description": "Recovered from preserved inventory photos.", "listing_price": 19.99},
            existing={"package_weight": "4 lb", "package_dimensions": {"length": 18, "width": 14, "height": 4}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/art-front.jpg"),
        image_urls=["/media/uploads/art-front.jpg"],
        source_metadata={
            "recovery": {
                "merged_into_recovery_group_id": 908,
                "merged_into_recovery_item_id": "RECOVERY-REVIEW-0073-I010",
            }
        },
        needs_review=False,
    )
    db_session.add_all([canonical, sibling])
    db_session.commit()

    service = ListingProcessingService()
    monkeypatch.setattr(
        service.listing_ai,
        "generate",
        lambda signals: {
            "title": "Pennyfarthing Galleries, Torquay, England",
            "description": "A framed silhouette artwork by Enid Elliott Linder.",
            "item_specifics": {"Artist": "Enid Elliott Linder", "Type": "Silhouette", "Proof": "Artist's proof"},
            "tags": ["art"],
            "estimated_value": 19.99,
        },
    )

    result = service.resume_listing(db_session, listing=sibling, worker_id="test-worker", dry_run=False, allow_image_identification=False)
    db_session.refresh(sibling)

    assert result["status"] == "complete"
    assert sibling.needs_review is True
    assert sibling.processing_state == "complete"
    assert sibling.title == "Pennyfarthing Galleries, Torquay, England"
    assert sibling.category_suggestion == "art"


def test_recovery_image_identity_grouping_review_becomes_needs_attention(db_session, monkeypatch):
    user = User(email="recovery-grouping-review@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="unknown",
        description="Recovered from preserved inventory photos.",
        category_suggestion="General resale > Needs category review",
        listing_price=19.99,
        suggested_price=19.99,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "unknown", "description": "Recovered from preserved inventory photos.", "listing_price": 19.99},
            existing={"package_weight": "3 lb", "package_dimensions": {"length": 12, "width": 10, "height": 8}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/ryobi-link.jpg"),
        image_urls=["/media/uploads/ryobi-link.jpg"],
        source_metadata={
            "recovery": {
                "image_identity_v1": {
                    "identity": {"title": "Slate", "brand": "Unknown", "model": "Unknown", "identifier": "Unknown"},
                    "quality_gate": "needs_grouping_review",
                    "group_kind": "multiple_unrelated_products",
                    "usable_media_ids": [1, 2, 3],
                    "item_specifics": {"Session": "2026-06-26-STORAGE-A", "Location": "A-04"},
                }
            }
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    service = ListingProcessingService()
    service.resume_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert listing.processing_state == "needs_attention"
    assert listing.processing_blocking_reason == "photo_grouping_review_required"
    assert listing.needs_review is False


def test_resume_backlog_repairs_generic_recovery_listing_copy(db_session):
    user = User(email="backfill-recovery@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Recovered photographed inventory item requiring identity review",
        description="Recovered from preserved inventory photos. Review the attached photos for condition.",
        category_suggestion="General resale > Needs category review",
        listing_price=34.99,
        suggested_price=34.99,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Recovered photographed inventory item requiring identity review", "description": "Recovered from preserved inventory photos.", "listing_price": 34.99},
            existing={"package_weight": "5 lb", "package_dimensions": {"length": 12, "width": 10, "height": 8}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/keurig-front.jpg"),
        image_urls=["/media/uploads/keurig-front.jpg"],
        item_specifics={"Brand": "Keurig", "Model": "K-Compact", "Type": "Coffee Maker"},
        source_metadata={
            "recovery": {
                "full_group_evidence_v3": {
                    "identity": {"title": "Keurig K-Compact Single Serve K-Cup Coffee Maker Black", "brand": "Keurig", "model": "K-Compact"},
                    "item_specifics": {"Brand": "Keurig", "Model": "K-Compact", "Type": "Coffee Maker"},
                }
            }
        },
        marketplace_data={
            "pricing_analysis": {"current_price": 34.99, "price_confidence": 0.91},
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    result = ListingProcessingService().resume_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.needs_review is True
    assert listing.processing_state == "complete"
    assert listing.processing_last_success_at is not None
    assert "Recovered photographed inventory item" not in (listing.title or "")
    assert "General resale" not in (listing.category_suggestion or "")
    assert "Keurig" in (listing.title or "")


def test_resume_backlog_uses_v2_recovery_evidence_to_replace_wrong_title(db_session):
    user = User(email="backfill-recovery-v2@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Logitech G Gaming Headset",
        description="Recovered from preserved inventory photos. Review the attached photos for condition.",
        category_suggestion="Electronics > Headphones",
        listing_price=49.99,
        suggested_price=49.99,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Logitech G Gaming Headset", "description": "Recovered from preserved inventory photos.", "listing_price": 49.99},
            existing={"package_weight": "3 lb", "package_dimensions": {"length": 12, "width": 10, "height": 8}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/logitech-adaptive-kit.jpg"),
        image_urls=["/media/uploads/logitech-adaptive-kit.jpg"],
        source_metadata={
            "recovery": {
                "field_provenance": {
                    "title": "legacy_ai_generated",
                    "description": "legacy_ai_generated",
                    "category_suggestion": "legacy_ai_generated",
                    "item_specifics": "legacy_ai_generated",
                },
                "full_group_evidence_v2": {
                    "identity": {
                        "title": "Logitech Adaptive Gaming Kit for Xbox Adaptive Controller",
                        "brand": "Logitech",
                        "model": "Adaptive Gaming Kit",
                        "product_name": "Adaptive Gaming Kit",
                    },
                    "category": "Video Games & Consoles > Video Game Accessories > Controllers & Attachments",
                    "item_specifics": {"Brand": "Logitech", "Model": "Adaptive Gaming Kit", "Type": "Adaptive Gaming Kit"},
                    "description": "Logitech Adaptive Gaming Kit for Xbox Adaptive Controller.",
                    "quality_gate": "trusted_for_draft",
                    "group_kind": "one_item",
                }
            }
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    result = ListingProcessingService().resume_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.needs_review is True
    assert listing.processing_state == "complete"
    assert "Logitech Adaptive Gaming Kit" in (listing.title or "")
    assert "Gaming Headset" not in (listing.title or "")
    assert "Electronics > Headphones" not in (listing.category_suggestion or "")
    assert "Logitech" in (listing.description or "")
    assert (listing.item_specifics or {}).get("Type") == "Adaptive Gaming Kit"
    assert (listing.item_specifics or {}).get("Model") == "Adaptive Gaming Kit"
    assert (listing.item_specifics or {}).get("Brand") == "Logitech"
    provenance = ((listing.source_metadata or {}).get("recovery") or {}).get("field_provenance") or {}
    assert provenance.get("title") == "full_group_evidence_v2"
    assert provenance.get("category_suggestion") == "full_group_evidence_v2"
    assert provenance.get("description") == "full_group_evidence_v2"
    assert provenance.get("item_specifics") == "full_group_evidence_v2"


def test_resume_backlog_preserves_human_owned_recovery_fields(db_session):
    user = User(email="backfill-human-owned@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Manual human title",
        description="Manual human description.",
        category_suggestion="Manual > Category",
        listing_price=49.99,
        suggested_price=49.99,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Manual human title", "description": "Manual human description.", "listing_price": 49.99},
            existing={"package_weight": "3 lb", "package_dimensions": {"length": 12, "width": 10, "height": 8}, "manual_measurement_needed": False},
        ),
        item_specifics={"Brand": "ManualBrand", "Type": "Manual Type", "Model": "Manual Model"},
        listing_images=_approved_images("/media/uploads/logitech-adaptive-kit.jpg"),
        image_urls=["/media/uploads/logitech-adaptive-kit.jpg"],
        source_metadata={
            "recovery": {
                "field_provenance": {
                    "title": "human_operator",
                    "description": "human_operator",
                    "category_suggestion": "human_operator",
                    "item_specifics": "human_operator",
                },
                "full_group_evidence_v2": {
                    "identity": {
                        "title": "Logitech Adaptive Gaming Kit for Xbox Adaptive Controller",
                        "brand": "Logitech",
                        "model": "Adaptive Gaming Kit",
                        "product_name": "Adaptive Gaming Kit",
                    },
                    "category": "Video Games & Consoles > Video Game Accessories > Controllers & Attachments",
                    "item_specifics": {"Brand": "Logitech", "Model": "Adaptive Gaming Kit", "Type": "Adaptive Gaming Kit"},
                    "description": "Logitech Adaptive Gaming Kit for Xbox Adaptive Controller.",
                    "quality_gate": "trusted_for_draft",
                    "group_kind": "one_item",
                },
            }
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    result = ListingProcessingService().resume_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.title == "Manual human title"
    assert listing.description == "Manual human description."
    assert listing.category_suggestion == "Manual > Category"
    assert (listing.item_specifics or {}).get("Brand") == "ManualBrand"
    assert (listing.item_specifics or {}).get("Type") == "Manual Type"
    assert (listing.item_specifics or {}).get("Model") == "Manual Model"
    provenance = ((listing.source_metadata or {}).get("recovery") or {}).get("field_provenance") or {}
    assert provenance.get("title") == "human_operator"
    assert provenance.get("item_specifics") == "human_operator"


def test_recovery_backfill_sets_needs_image_identification_when_identity_remains_generic(db_session):
    user = User(email="backfill-needs-image-id@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Unknown Item",
        description="Recovered from preserved inventory photos.",
        category_suggestion="Unknown",
        listing_price=12.0,
        suggested_price=12.0,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Unknown Item", "description": "Recovered from preserved inventory photos.", "listing_price": 12.0},
            existing={"package_weight": "2 lb", "package_dimensions": {"length": 8, "width": 6, "height": 4}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/recovery-unknown.jpg"),
        image_urls=["/media/uploads/recovery-unknown.jpg"],
        source_metadata={
            "recovery": {
                "image_identity_v1": {
                    "identity": {"title": "Unknown", "brand": "Unknown", "model": "Unknown", "identifier": "Unknown"},
                    "quality_gate": "needs_identity_review",
                    "group_kind": "one_item",
                },
                "field_confidence": {"identity": 0.45},
            }
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    result = ListingProcessingService().resume_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.processing_state == "needs_attention"
    assert listing.processing_stage == "image_identification"
    assert listing.processing_blocking_reason == "needs_image_identification"
    assert listing.processing_next_retry_at is None


def test_recovery_backfill_blocks_generic_caption_identity_even_when_image_id_is_allowed(db_session):
    user = User(email="backfill-generic-caption-blocked@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Circuit Board",
        description="Recovered from preserved inventory photos.",
        category_suggestion="Consumer Electronics",
        listing_price=12.0,
        suggested_price=12.0,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Circuit Board", "description": "Recovered from preserved inventory photos.", "listing_price": 12.0},
            existing={"package_weight": "2 lb", "package_dimensions": {"length": 8, "width": 6, "height": 4}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/recovery-generic-caption.jpg"),
        image_urls=["/media/uploads/recovery-generic-caption.jpg"],
        source_metadata={
            "recovery": {
                "image_identity_v1": {
                    "identity": {"title": "Unknown", "brand": "Unknown", "model": "Unknown", "identifier": "Unknown"},
                    "quality_gate": "needs_identity_review",
                    "group_kind": "one_item",
                }
            }
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    result = ListingProcessingService().resume_listing(
        db_session,
        listing=listing,
        worker_id="test-worker",
        dry_run=False,
        allow_image_identification=True,
    )
    db_session.refresh(listing)

    assert result["status"] == "needs_attention"
    assert result["ready_for_review"] is False
    assert listing.processing_state == "needs_attention"
    assert listing.processing_stage == "image_identification"
    assert listing.processing_blocking_reason == "generic_or_caption_identity"
    assert listing.needs_review is False


def test_product_research_backlog_processes_weak_review_item_with_image_evidence(db_session, monkeypatch):
    user = User(email="product-research@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Circuit Board",
        description="Bare caption only.",
        category_suggestion="Other > Needs category review",
        listing_price=19.99,
        suggested_price=19.99,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Circuit Board", "description": "Bare caption only.", "listing_price": 19.99},
            existing={"package_weight": "1 lb", "package_dimensions": {"length": 8, "width": 6, "height": 3}, "manual_measurement_needed": False, "estimated": True},
        ),
        listing_images=_approved_images("/media/uploads/circuit-board.jpg"),
        image_urls=["/media/uploads/circuit-board.jpg"],
        source_metadata={"recovery": {"item_id": "REC-RESEARCH-1"}},
        needs_review=True,
    )
    db_session.add(listing)
    db_session.commit()

    def _enrich_group(self, photo_paths):
        return {
            "photos_evaluated": len(photo_paths),
            "photos_excluded": [],
            "group_synthesis": {
                "identity": {
                    "title": "Whirlpool W11478526 Washer Main Control Board OEM Replacement Part",
                    "brand": "Whirlpool",
                    "model": "W11478526",
                    "mpn": "W11478526",
                },
                "identity_candidates": [
                    {"title": "Whirlpool Main Control Board", "brand": "Whirlpool", "model": "W11478526", "confidence": 0.97, "media_id": 1}
                ],
                "title": "Whirlpool W11478526 Washer Main Control Board OEM Replacement Part",
                "brand": "Whirlpool",
                "model": "W11478526",
                "mpn": "W11478526",
                "product_name": "Whirlpool W11478526 Washer Main Control Board",
                "product_type": "Washer Main Control Board",
                "category": "Appliances > Appliance Parts & Accessories > Washer Parts",
                "item_specifics": {"Brand": "Whirlpool", "MPN": "W11478526", "Type": "Washer Main Control Board"},
                "quality_gate": "trusted_for_draft",
                "identity_confidence": 0.96,
                "field_confidence": {"brand": 0.96, "model": 0.96, "mpn": 0.96, "product_name": 0.96, "category": 0.96},
                "review_flags": [],
                "placeholders": [],
            },
        }

    monkeypatch.setattr(PhotoEnrichmentService, "enrich_group", _enrich_group)

    service = ListingProcessingService()
    monkeypatch.setattr(
        service.listing_ai,
        "generate",
        lambda signals: {
            "title": "Whirlpool W11478526 Washer Main Control Board OEM Replacement Part",
            "description": "Whirlpool washer main control board with visible OEM part number W11478526.",
            "item_specifics": {"Brand": "Whirlpool", "MPN": "W11478526", "Type": "Washer Main Control Board"},
            "tags": ["whirlpool", "washer", "control board"],
            "estimated_value": 89.99,
        },
    )

    result = service.resume_product_research_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.processing_state == "complete"
    assert listing.needs_review is True
    assert listing.title.startswith("Whirlpool W11478526")


def test_recovery_backfill_defers_heavy_image_identification_until_dedicated_worker(db_session, monkeypatch):
    user = User(email="backfill-defer-image-id@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Unknown Item",
        description="Recovered from preserved inventory photos.",
        category_suggestion="Unknown",
        listing_price=12.0,
        suggested_price=12.0,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Unknown Item", "description": "Recovered from preserved inventory photos.", "listing_price": 12.0},
            existing={"package_weight": "2 lb", "package_dimensions": {"length": 8, "width": 6, "height": 4}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/recovery-defer.jpg"),
        image_urls=["/media/uploads/recovery-defer.jpg"],
        source_metadata={"recovery": {"image_identity_v1": {"identity": {"title": "Unknown", "brand": "Unknown", "model": "Unknown", "identifier": "Unknown"}}}},
        needs_review=False,
        processing_state="queued",
        processing_stage="resume",
    )
    db_session.add(listing)
    db_session.commit()

    called = {"enrich_group": 0}

    def _fail_enrich_group(self, photo_paths):  # noqa: ANN001
        called["enrich_group"] += 1
        raise AssertionError("heavy image identification should not run in the normal backlog worker")

    monkeypatch.setattr(PhotoEnrichmentService, "enrich_group", _fail_enrich_group)

    result = ListingProcessingService().resume_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert called["enrich_group"] == 0
    assert result["processed"] >= 1
    assert listing.processing_state == "needs_attention"
    assert listing.processing_stage == "image_identification"
    assert listing.processing_blocking_reason == "needs_image_identification"


def test_image_identification_backlog_promotes_identified_recovery_listing(db_session, monkeypatch):
    user = User(email="backfill-image-id-worker@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Unknown Item",
        description="Recovered from preserved inventory photos.",
        category_suggestion="Unknown",
        listing_price=24.0,
        suggested_price=24.0,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Unknown Item", "description": "Recovered from preserved inventory photos.", "listing_price": 24.0},
            existing={
                "package_weight": "3 lb",
                "package_dimensions": {"length": 10, "width": 8, "height": 6},
                "manual_measurement_needed": False,
            },
        ),
        listing_images=_approved_images("/media/uploads/recovery-identify.jpg"),
        image_urls=["/media/uploads/recovery-identify.jpg"],
        source_metadata={
            "recovery": {
                "image_identity_v1": {
                    "identity": {"title": "Unknown", "brand": "Unknown", "model": "Unknown", "identifier": "Unknown"},
                    "quality_gate": "needs_identity_review",
                    "group_kind": "one_item",
                }
            }
        },
        needs_review=False,
        processing_state="needs_attention",
        processing_stage="image_identification",
        processing_blocking_reason="needs_image_identification",
    )
    db_session.add(listing)
    db_session.commit()

    def _enrich_group(self, photo_paths):  # noqa: ANN001
        return {
            "photos_evaluated": len(photo_paths),
            "photos_excluded": [],
            "group_synthesis": {
                "pipeline_version": "recovery_full_group_evidence_v3",
                "group_kind": "one_item",
                "identity": {
                    "title": "RYOBI ONE+ Cordless Impact Driver",
                    "brand": "RYOBI",
                    "model": "P235A",
                    "mpn": "P235A",
                    "identifier": "P235A",
                },
                "identity_confidence": 0.91,
                "category": "Tools & Home Improvement > Power Tools > Impact Drivers",
                "item_specifics": {"Brand": "RYOBI", "Model": "P235A"},
                "included_parts": [],
                "condition": "Used",
                "damage": [],
                "testing_status": None,
                "conflicting_evidence": [],
                "supporting_media_ids": {"brand": [1], "model": [1], "product_name": [1]},
                "usable_media_ids": [1, 2, 3],
                "photo_count": len(photo_paths),
                "field_confidence": {"brand": 0.91, "model": 0.91, "product_name": 0.91},
                "reason_selected": "test fixture",
                "identity_candidates": [],
                "placeholders": [],
                "review_flags": [],
                "quality_gate": "trusted_for_draft",
                "description": "RYOBI ONE+ cordless impact driver with tested photos and visible labeling.",
            },
        }

    monkeypatch.setattr(PhotoEnrichmentService, "enrich_group", _enrich_group)

    result = ListingProcessingService().resume_image_identification_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.needs_review is True
    assert listing.processing_state == "complete"
    assert listing.processing_stage == "quality_gate"
    assert "RYOBI" in (listing.title or "")
    assert "Impact Driver" in (listing.title or "")


def test_backfill_preserves_manual_copy_and_marks_needs_attention_when_blocked(db_session):
    user = User(email="backfill-manual@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Custom Manual Title",
        description="Custom manual description with human edits.",
        category_suggestion="Electronics > Accessories",
        listing_price=None,
        suggested_price=None,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Custom Manual Title", "description": "Custom manual description with human edits."},
            existing={"manual_measurement_needed": True},
        ),
        listing_images=[],
        image_urls=[],
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    service = ListingProcessingService()
    result = service.resume_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.title == "Custom Manual Title"
    assert listing.description == "Custom manual description with human edits."
    assert listing.processing_state in {"retry", "blocked", "needs_attention"}
    assert listing.processing_blocking_reason


def test_recovery_backfill_refreshes_empty_cached_image_identity(db_session, monkeypatch):
    user = User(email="backfill-image-identity@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="media_inventory_recovery",
        title="Recovered photographed inventory item requiring identity review",
        description="Recovered from preserved inventory photos. Review the attached photos for condition.",
        category_suggestion="General resale > Needs category review",
        listing_price=21.0,
        suggested_price=21.0,
        condition="Used",
        condition_data=derive_condition_data(
            listing={"condition": "Used", "source_type": "media_inventory_recovery"},
            source_type="media_inventory_recovery",
            existing={"operator_review_required": False, "condition_bucket": "used"},
        ),
        shipping_profile=derive_shipping_profile(
            listing={"title": "Recovered photographed inventory item requiring identity review", "description": "Recovered from preserved inventory photos.", "listing_price": 21.0},
            existing={"package_weight": "4 lb", "package_dimensions": {"length": 12, "width": 10, "height": 8}, "manual_measurement_needed": False},
        ),
        listing_images=_approved_images("/media/uploads/ryobi-link.jpg"),
        image_urls=["/media/uploads/ryobi-link.jpg"],
        source_metadata={
            "recovery": {
                "image_identity_v1": {
                    "identity": {},
                    "quality_gate": "needs_identity_review",
                    "group_kind": "single_item",
                }
            }
        },
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()

    service = ListingProcessingService()
    monkeypatch.setattr(
        service.photo_enrichment,
        "enrich_group",
        lambda paths: {
            "group_synthesis": {
                "identity": {"title": "RYOBI LINK Modular Wall Storage Accessory Black Green", "brand": "RYOBI", "model": "LINK"},
                "identity_confidence": 0.91,
                "description": "RYOBI LINK modular wall storage accessory for garage organization.",
                "category": "Home & Garden > Home Organization > Garage Storage & Organization",
                "item_specifics": {"Brand": "RYOBI", "Product Type": "Wall Storage Accessory"},
                "tags": ["ryobi", "link"],
                "usable_media_ids": [1],
                "quality_gate": "trusted_for_draft",
            },
            "photos_evaluated": len(paths),
            "photos_excluded": [],
        },
    )
    monkeypatch.setattr(
        service.listing_ai,
        "generate",
        lambda signals: {
            "title": "RYOBI LINK Modular Wall Storage Accessory Black Green",
            "description": "RYOBI LINK modular wall storage accessory for garage organization.",
            "item_specifics": {"Brand": "RYOBI", "Product Type": "Wall Storage Accessory"},
            "tags": ["ryobi", "link"],
            "estimated_value": 24.99,
        },
    )

    listing.processing_state = "needs_attention"
    listing.processing_stage = "image_identification"
    listing.processing_blocking_reason = "needs_image_identification"
    db_session.add(listing)
    db_session.commit()

    result = service.resume_image_identification_backlog(db_session, user_id=user.id, limit=5, worker_id="test-worker")
    db_session.refresh(listing)

    assert result["processed"] >= 1
    assert listing.needs_review is True
    assert listing.processing_state == "complete"
    assert "RYOBI" in (listing.title or "")
    assert "LINK" in (listing.title or "")
    assert ((listing.source_metadata or {}).get("recovery") or {}).get("image_identity_v1", {}).get("identity", {}).get("brand") == "RYOBI"


def test_requeue_repairable_blocked_listings_resets_recoverable_rows(db_session):
    user = User(email="requeue-blocked@example.com")
    db_session.add(user)
    db_session.flush()

    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="amazon_vine",
        title="Item B0GT3S6CYV",
        description="Recovered retail item with placeholder title.",
        category_suggestion="Other > Needs category review",
        processing_state="blocked",
        processing_stage="quality_gate",
        processing_blocking_reason="generic_or_underspecified_title; missing_specific_identifier_evidence",
        processing_last_error="placeholder",
        processing_error_stage="quality_gate",
        processing_next_retry_at=datetime.now(UTC) + timedelta(days=1),
        listing_images=_approved_images("/media/uploads/vine-item.jpg"),
        image_urls=["/media/uploads/vine-item.jpg"],
    )
    db_session.add(listing)
    db_session.commit()

    result = ListingProcessingService().requeue_repairable_blocked_listings(db_session, user_id=user.id, limit=10)
    db_session.refresh(listing)

    assert result["requeued"] == 1
    assert listing.processing_state == "queued"
    assert listing.processing_stage == "recovery_requeue"
    assert listing.processing_blocking_reason is None
    assert listing.processing_last_error is None
