from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import text

from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import IntakePhotoBatch, Listing, ListingProcessingEvent, MarketplaceListing, MediaRecoveryItemGroup, MediaRecoveryRun, User, VineImportBatch, VineImportItem
from app.services.listing_processing import ListingProcessingService, _needs_description_enrichment
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


def test_machine_owned_google_copy_is_requeued_when_thin_but_operator_copy_is_preserved():
    thin = "This is a product listing with limited details."
    assert _needs_description_enrichment(thin, source_type="intake_head_slate", source_metadata={}) is True
    assert _needs_description_enrichment(thin, source_type="intake_head_slate", source_metadata={"recovery": {"field_provenance": {"description": "operator"}}}) is False
    assert _needs_description_enrichment("A" * 220, source_type="intake_head_slate", source_metadata={}) is False


def test_completion_hydrates_alternate_ask_and_blocks_missing_price(db_session):
    user = User(email="review-price-invariant@example.com")
    db_session.add(user)
    db_session.flush()
    alternate = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Specific item with alternate price",
        listing_price=None,
        suggested_price=27.5,
        processing_state="processing",
        needs_review=False,
    )
    missing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        title="Specific item without a price",
        listing_price=None,
        suggested_price=None,
        processing_state="processing",
        needs_review=False,
    )
    db_session.add_all([alternate, missing])
    db_session.flush()
    service = ListingProcessingService()

    service._mark_listing_complete(
        db_session,
        listing=alternate,
        worker_id="test-worker",
        stage="quality_gate",
        message="quality complete",
    )
    service._mark_listing_complete(
        db_session,
        listing=missing,
        worker_id="test-worker",
        stage="quality_gate",
        message="quality complete",
    )

    assert alternate.listing_price == 27.5
    # Price hydration alone does not make a listing reviewable: this minimal
    # fixture intentionally has no approved image/complete draft evidence.
    assert alternate.needs_review is False
    assert alternate.processing_blocking_reason != "MISSING_VALID_PRICE"
    assert missing.needs_review is False
    assert missing.processing_state == "needs_attention"
    assert missing.processing_error_stage == "pricing_research"
    assert missing.processing_blocking_reason == "MISSING_VALID_PRICE"


def test_google_intake_backlog_selects_newest_unpublished_listings_first(db_session, monkeypatch):
    user = User(email="google-newest-first@example.com")
    db_session.add(user)
    db_session.flush()
    rows = []
    for day in (1, 2, 3):
        row = Listing(
            user_id=user.id,
            status=ListingStatus.draft,
            source_type="intake_head_slate",
            title=f"Specific test product {day}",
            description=f"Specific description for product {day}.",
            category_suggestion="Verified test category",
            category_id="1001",
            listing_price=10.0,
            suggested_price=10.0,
            image_urls=[f"https://example.test/{day}.jpg"],
            listing_images=[{"url": f"https://example.test/{day}.jpg"}],
            processing_state="queued",
            needs_review=False,
            created_at=datetime(2026, 9, day),
            updated_at=datetime(2026, 9, day),
        )
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    service = ListingProcessingService()
    monkeypatch.setattr(service, "_requeue_now_reviewable_blocked_listings", lambda *args, **kwargs: 0)
    visited = []
    monkeypatch.setattr(
        service,
        "resume_listing",
        lambda _db, *, listing, **kwargs: visited.append(listing.id) or {"listing_id": listing.id},
    )

    result = service.resume_backlog(
        db_session,
        user_id=user.id,
        limit=2,
        worker_id="test-google-newest-first",
        dry_run=True,
        source_types={"intake_head_slate"},
    )

    assert result["processed"] == 2
    assert visited == [rows[2].id, rows[1].id]


def test_recovery_backlog_prefers_highest_durable_google_group_first(db_session, monkeypatch):
    user = User(email="recovery-group-priority@example.com")
    db_session.add(user)
    db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="priority-run", pipeline_version="test")
    db_session.add(run)
    db_session.flush()
    rows = []
    for group_id in (396, 398, 397):
        row = Listing(
            user_id=user.id,
            status=ListingStatus.draft,
            source_type="media_inventory_recovery",
            title=f"Specific recovery item {group_id}",
            processing_state="queued",
            needs_review=False,
        )
        db_session.add(row)
        db_session.flush()
        db_session.add(
            MediaRecoveryItemGroup(
                id=group_id,
                run_id=run.id,
                recovery_item_id=f"REC-{group_id}",
                grouping_status="ready",
                draft_listing_id=row.id,
                created_at=datetime(2026, 8, 1),
            )
        )
        rows.append(row)
    db_session.commit()

    service = ListingProcessingService()
    monkeypatch.setattr(service, "_requeue_now_reviewable_blocked_listings", lambda *args, **kwargs: 0)
    visited = []
    monkeypatch.setattr(
        service,
        "resume_listing",
        lambda _db, *, listing, **kwargs: visited.append(listing.id) or {"listing_id": listing.id},
    )

    result = service.resume_backlog(
        db_session,
        user_id=user.id,
        limit=2,
        worker_id="test-recovery-priority",
        dry_run=True,
        source_types={"media_inventory_recovery"},
    )

    assert result["processed"] == 2
    assert visited == [rows[1].id, rows[2].id]


def test_recovery_backlog_prefers_newer_group_chronology_over_larger_id(db_session, monkeypatch):
    user = User(email="recovery-chronology@example.com")
    db_session.add(user)
    db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="chronology-run", pipeline_version="test")
    db_session.add(run)
    db_session.flush()
    older = Listing(user_id=user.id, status=ListingStatus.draft, source_type="media_inventory_recovery", title="Older recovery", processing_state="queued")
    newer = Listing(user_id=user.id, status=ListingStatus.draft, source_type="media_inventory_recovery", title="Newer recovery", processing_state="queued")
    db_session.add_all([older, newer])
    db_session.flush()
    db_session.add_all([
        MediaRecoveryItemGroup(id=900, run_id=run.id, recovery_item_id="OLD", grouping_status="ready", draft_listing_id=older.id, created_at=datetime(2026, 7, 1)),
        MediaRecoveryItemGroup(id=100, run_id=run.id, recovery_item_id="NEW", grouping_status="ready", draft_listing_id=newer.id, created_at=datetime(2026, 8, 1)),
    ])
    db_session.commit()
    service = ListingProcessingService()
    monkeypatch.setattr(service, "_requeue_now_reviewable_blocked_listings", lambda *args, **kwargs: 0)
    visited = []
    monkeypatch.setattr(service, "resume_listing", lambda _db, *, listing, **kwargs: visited.append(listing.id) or {"listing_id": listing.id})

    service.resume_backlog(db_session, user_id=user.id, limit=1, worker_id="test-chronology", dry_run=True, source_types={"media_inventory_recovery"})

    assert visited == [newer.id]


def test_recovery_legacy_reevaluation_skips_human_newest_and_requeues_older_machine_row(db_session):
    user = User(email="recovery-reevaluation@example.com")
    db_session.add(user)
    db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="reevaluation-run", pipeline_version="test")
    db_session.add(run)
    db_session.flush()
    human = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="media_inventory_recovery",
        title="Mixed recovery", processing_state="needs_attention", processing_stage="grouping_review",
        processing_blocking_reason="photo_grouping_review_required", source_metadata={"grouping_review": {"status": "required"}},
    )
    machine = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="media_inventory_recovery",
        title="Generic recovery", processing_state="needs_attention", processing_stage="quality_gate",
        processing_blocking_reason="generic_or_underspecified_title; missing_specific_identifier_evidence",
        listing_images=_approved_images("/media/uploads/recovery.jpg"), image_urls=["/media/uploads/recovery.jpg"],
        source_metadata={"recovery": {"full_group_evidence_v3": {"identity": {"title": "Known recovery product"}}}},
    )
    db_session.add_all([human, machine])
    db_session.flush()
    db_session.add_all([
        MediaRecoveryItemGroup(id=501, run_id=run.id, recovery_item_id="HUMAN", grouping_status="needs_grouping_review", draft_listing_id=human.id, created_at=datetime(2026, 8, 2)),
        MediaRecoveryItemGroup(id=500, run_id=run.id, recovery_item_id="MACHINE", grouping_status="confirmed", draft_listing_id=machine.id, created_at=datetime(2026, 8, 1)),
    ])
    db_session.commit()

    result = ListingProcessingService().reevaluate_legacy_recovery_attention(db_session, user_id=user.id, limit=1)
    db_session.refresh(human)
    db_session.refresh(machine)

    assert result["requeued_listing_ids"] == [machine.id]
    assert human.processing_state == "needs_attention"
    assert human.processing_stage == "legacy_human_verified_v1"
    assert machine.processing_state == "quality_repair_required"
    assert machine.source_metadata["processing_reevaluation"]["outcome"] == "NOW_MACHINE_RESOLVABLE"


def test_huge_closed_google_group_is_attention_not_a_single_product_draft(db_session):
    user = User(email="google-huge-group-review@example.com")
    db_session.add(user)
    db_session.flush()
    batch = IntakePhotoBatch(
        user_id=user.id,
        item_id="SLATE-HUGE",
        status="drafted",
        photo_count=573,
        public_photo_count=569,
    )
    db_session.add(batch)
    db_session.flush()
    image_urls = [f"/media/intake-items/SLATE-HUGE/photo-{index}.jpg" for index in range(569)]
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        source_type="intake_head_slate",
        title="Electronics - PosterPro Box ID BX-0571",
        description="Preserve the current draft copy while the photo grouping is reviewed.",
        image_urls=image_urls,
        listing_images=[{"url": image_urls[0], "is_reference": False}],
        category_suggestion="Electronics",
        source_metadata={"intake": {"item_id": "SLATE-HUGE"}, "custom_note": "preserve"},
        processing_state="queued",
    )
    db_session.add(listing)
    db_session.flush()
    original_images = list(listing.image_urls)
    original_title = listing.title
    original_description = listing.description

    result = ListingProcessingService().resume_listing(
        db_session,
        listing=listing,
        worker_id="test-huge-group",
        dry_run=False,
    )

    assert result["status"] == "needs_attention"
    assert result["group_photo_count"] == 569
    assert listing.needs_review is False
    assert listing.processing_stage == "grouping_review"
    assert "569 listing photos" in listing.processing_blocking_reason
    assert listing.image_urls == original_images
    assert listing.title == original_title
    assert listing.description == original_description
    assert listing.source_metadata["custom_note"] == "preserve"


def test_google_group_synthesis_multiple_products_stops_category_retry(db_session):
    user = User(email="google-multi-product-group-review@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="intake_head_slate",
        title="Main B/D",
        description="Keep current descriptive evidence while the photos are separated.",
        image_urls=["/media/intake-items/SLATE-MULTI/front.jpg"],
        listing_images=[{"url": "/media/intake-items/SLATE-MULTI/front.jpg", "is_reference": False}],
        source_metadata={
            "intake": {"item_id": "SLATE-MULTI"},
            "source_facts": {
                "group_kind": "multiple_unrelated_products",
                "conflicting_evidence": ["multiple_model_labels", "multiple_product_types"],
            },
        },
        processing_state="retry",
    )
    db_session.add(listing)
    db_session.flush()
    original_images = list(listing.image_urls)
    original_description = listing.description

    result = ListingProcessingService().resume_listing(
        db_session,
        listing=listing,
        worker_id="test-multi-product-group",
        dry_run=False,
    )

    assert result["status"] == "needs_attention"
    assert result["group_kind"] == "multiple_unrelated_products"
    assert listing.processing_stage == "grouping_review"
    assert listing.processing_next_retry_at is None
    assert listing.source_metadata["grouping_review"]["status"] == "required"
    assert listing.image_urls == original_images
    assert listing.description == original_description


def test_archived_ebay_vine_row_is_terminal_for_processing_worker():
    listing = SimpleNamespace(
        status=ListingStatus.PROCESSED,
        ebay_listing_id=None,
        ebay_publish_status=None,
        sold_at=None,
        custom_labels=["amazon_vine", "archived_ebay"],
    )
    assert ListingProcessingService()._is_terminal(listing) is True


def test_backlog_restores_frozen_operator_approved_payload_without_reprocessing(db_session):
    user = User(email="frozen-approved-payload@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.ready,
        source_type="amazon_vine",
        title="Approved specific product title",
        description="Approved buyer-facing product description.",
        processing_state="queued",
        processing_stage="stalled_processing_requeue",
        processing_attempt_count=30,
        needs_review=False,
        source_metadata={"operator_approved_at": "2026-10-01T12:00:00+00:00", "operator_approved_by_user_id": user.id},
    )
    db_session.add(listing)
    db_session.commit()
    original_title = listing.title
    original_description = listing.description

    result = ListingProcessingService().resume_backlog(
        db_session,
        user_id=user.id,
        limit=1,
        source_types={"amazon_vine"},
    )
    db_session.refresh(listing)

    assert listing.processing_state == "complete"
    assert listing.processing_stage == "operator_approved_payload_frozen"
    assert listing.status == ListingStatus.ready
    assert listing.title == original_title
    assert listing.description == original_description
    assert all(item.get("listing_id") != listing.id for item in result["results"])


def test_google_processor_persists_quality_passed_rewrite_and_preserves_operator_copy(db_session, monkeypatch):
    user = User(email="google-description-persistence@example.com")
    db_session.add(user)
    db_session.flush()
    facts = {
        "feature_bullets": ["Stainless steel spoon head", "Handle heat resistant to 400 degrees Fahrenheit"],
        "specifications": {"Brand": "Farberware", "Type": "Basting Spoon"},
    }
    generated_copy = (
        "Farberware Basting Spoon for cooking and serving. The stainless steel spoon head is suited to everyday kitchen use, "
        "and its handle is heat resistant to 400 degrees Fahrenheit. Use it to baste a roast during cooking or bring it to the "
        "table for serving. KEY DETAILS: Brand Farberware; type Basting Spoon; stainless steel head; handle heat resistance to "
        "400 degrees Fahrenheit. Review the photos for the exact item condition and included pieces, then add this kitchen "
        "utensil to your cooking tools while it is available."
    )

    def make_listing(title, source_metadata):
        row = Listing(
            user_id=user.id,
            status=ListingStatus.draft,
            source_type="intake_head_slate",
            title=title,
            description="This is a marketplace listing built from the item details available for this product.",
            canonical_description="This is a marketplace listing built from the item details available for this product.",
            category_suggestion="Other Kitchen Tools & Gadgets",
            category_id="20651",
            item_specifics={"Brand": "Farberware", "Type": "Basting Spoon"},
            source_metadata=source_metadata,
            listing_price=8.0,
            suggested_price=8.0,
        )
        db_session.add(row)
        db_session.flush()
        return row

    machine_owned = make_listing("Farberware Basting Spoon", {"source_facts": facts})
    operator_owned = make_listing(
        "Farberware Basting Spoon",
        {"source_facts": facts, "recovery": {"field_provenance": {"description": "human_operator"}, "operator_locked_fields": ["description"]}},
    )
    captured = []
    service = ListingProcessingService()

    def generate(payload, **_kwargs):
        captured.append(payload)
        return {"description": generated_copy, "title": "Farberware Basting Spoon", "generation_source": "openai", "item_specifics": {}}

    monkeypatch.setattr(service.listing_ai, "generate", generate)
    service._resume_generic_listing(db_session, listing=machine_owned, worker_id="test-google", dry_run=False)
    service._resume_generic_listing(db_session, listing=operator_owned, worker_id="test-google", dry_run=False)
    db_session.flush()

    assert machine_owned.description == generated_copy
    assert machine_owned.canonical_description == generated_copy
    assert captured[0]["source_metadata"]["source_facts"] == facts
    assert operator_owned.description.startswith("This is a marketplace listing")


def test_resume_backlog_promotes_ready_vine_listing_to_needs_review(db_session):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_MOTORS_US','100','83','261150','RV Doors',null,true,'Root > eBay Motors > RV, Trailer & Camper Parts > Doors & Windows > RV Doors',5)"))
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
        category_id="261150",
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
                "pricing_analysis": {
                    "current_price": 21.0,
                    "price_confidence": 0.91,
                    # A Vine ETV is first-party source evidence, not the
                    # former synthetic $20/$21 fallback.
                    "provenance": "vine_etv_fallback",
                    "comp_count_used": 1,
                },
            # Needs Review now requires the same fresh destination preflight
            # used at publish time; this fixture represents that passing gate.
            "marketplace_preflight": {
                "by_marketplace": {
                    "ebay": {"status": "ready", "blockers": [], "stale": False},
                }
            },
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


def test_future_retry_backoff_is_not_claimed_again(db_session, monkeypatch):
    user = User(email="future-processing-retry@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        source_type="intake_head_slate",
        title="Acme Stainless Steel Mixing Bowl",
        description="Acme stainless steel mixing bowl for food preparation and serving.",
        processing_state="retry",
        processing_stage="quality_gate",
        processing_next_retry_at=datetime.now(UTC) + timedelta(minutes=20),
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()
    service = ListingProcessingService()
    invoked = []
    monkeypatch.setattr(service, "resume_listing", lambda *_args, **_kwargs: invoked.append(True))

    result = service.resume_backlog(db_session, user_id=user.id, limit=1, worker_id="test-backoff")

    assert result["processed"] == 0
    assert invoked == []
    assert listing.processing_state == "retry"


def test_finalizer_does_not_promote_quality_complete_when_destination_is_blocked(db_session, monkeypatch):
    user = User(email="preflight-blocked-finalizer@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="intake_head_slate",
        title="Acme Stainless Steel Mixing Bowl",
        description="Acme stainless steel mixing bowl for food preparation and serving.",
        listing_price=12.0,
        suggested_price=12.0,
        processing_state="queued",
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.listing_processing.compute_listing_quality_summary",
        lambda *_args, **_kwargs: {"specificity_status": "trusted_for_draft", "specificity_blockers": []},
    )
    monkeypatch.setattr(
        "app.services.listing_processing.canonical_listing_readiness",
        lambda *_args, **_kwargs: {
            "queue": "PROCESSING",
            "review_required": False,
            "publishable": False,
            "blocking_reasons": [],
            "canonical_blocking_reasons": [],
            "destination_blocking_reasons": ["eBay image payload is blocked"],
        },
    )

    ListingProcessingService()._finalize_review_ready_listing(db_session, listing_id=listing.id, worker_id="test-finalizer")
    db_session.refresh(listing)

    assert listing.needs_review is False
    assert listing.status == ListingStatus.draft


def test_ready_vine_fast_path_audits_semantic_category_and_stale_preflight(db_session, monkeypatch):
    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','162480','Other Camera & Photo Accs',null,true,'Root > Cameras & Photo > Camera Accessories > Other Camera & Photo Accs',4)"))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','48638','Security Cameras',null,true,'Root > Consumer Electronics > Home Surveillance > Security Cameras',4)"))
    db_session.commit()
    user = User(email="ready-vine-category-audit@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.ready,
        source_type="amazon_vine",
        title="Dual Lens 3MP Window Camera for Home Security",
        description="Dual-lens home security camera with two-way monitoring and Wi-Fi support. Check the photos and compatibility details before purchase.",
        category_id="162480",
        category_suggestion="Root > Consumer Electronics > Home Surveillance > Security Cameras",
        listing_price=49.99,
        suggested_price=49.99,
        condition="New",
        listing_images=_approved_images("/media/uploads/vine-security-camera.jpg"),
        image_urls=["/media/uploads/vine-security-camera.jpg"],
        item_specifics={"Brand": "GULZIIYA", "Model": "K4W"},
        condition_data={"condition": "New", "operator_review_required": False},
        shipping_profile={"manual_measurement_needed": False},
        marketplace_data={
            "pricing_analysis": {"current_price": 49.99, "price_confidence": 0.9, "provenance": "asin_matched_amazon_offer"},
            "marketplace_preflight": {"by_marketplace": {"ebay": {"status": "ready", "blockers": [], "stale": False}}},
        },
        source_metadata={"amazon_product_facts": {"brand": "GULZIIYA", "model": "K4W", "product_type": "Security Camera"}},
        needs_review=True,
        processing_state="complete",
    )
    db_session.add(listing)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.listing_processing.MarketplacePreflightService.preflight_listing",
        lambda _self, _db, _listing, marketplace: {
            "marketplace": marketplace,
            "status": "ready",
            "blockers": [],
            "warnings": [],
            "last_checked_at": datetime.now(UTC).isoformat(),
        },
    )
    result = ListingProcessingService().resume_listing(db_session, listing=listing, worker_id="test-worker")
    db_session.refresh(listing)

    assert result.get("skipped_repair") is not True
    assert listing.category_id == "48638"
    assert listing.needs_review is True
    assert listing.processing_state == "complete"
    assert listing.marketplace_data["marketplace_preflight"]["by_marketplace"]["ebay"]["status"] == "ready"


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
    # Identification is persisted, but fallback-only pricing is deliberately
    # not review-ready.  It remains a retryable machine concern rather than
    # being promoted to Needs Review.
    assert listing.needs_review is False
    assert listing.processing_state == "retry"
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

    # The sibling inherits the coherent merged-group identity, but its
    # fallback-only price still requires the separate pricing path before
    # review. Identity repair must not promote it prematurely.
    assert result["status"] == "complete"
    assert sibling.needs_review is False
    assert sibling.processing_state == "retry"
    assert sibling.title == "Pennyfarthing Galleries, Torquay, England"
    assert sibling.category_suggestion == "art"


def test_image_identification_failure_clears_stale_needs_review_marker(db_session, monkeypatch):
    user = User(email="stale-image-review-marker@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        source_type="media_inventory_recovery",
        title="Unidentified photographed item",
        description="Existing draft copy that must remain untouched.",
        processing_state="needs_attention",
        processing_stage="image_identification",
        processing_blocking_reason="needs_image_identification",
        needs_review=True,
        source_metadata={"recovery": {"image_identity_v1": {"quality_gate": "needs_human_review"}}},
        image_urls=["/media/recovery/unidentified.jpg"],
        listing_images=[{"url": "/media/recovery/unidentified.jpg"}],
    )
    db_session.add(listing)
    db_session.commit()
    service = ListingProcessingService()
    monkeypatch.setattr(
        service,
        "resume_listing",
        lambda _db, *, listing, **kwargs: {
            "listing_id": listing.id,
            "status": "needs_attention",
            "ready_for_review": False,
            "blocking": ["needs_image_identification"],
        },
    )

    result = service.resume_image_identification_backlog(
        db_session,
        user_id=user.id,
        limit=1,
        worker_id="test-stale-review-marker",
    )
    db_session.refresh(listing)

    assert result["processed"] == 1
    assert listing.needs_review is False
    assert listing.processing_state == "needs_attention"
    assert listing.title == "Unidentified photographed item"
    assert listing.image_urls == ["/media/recovery/unidentified.jpg"]


def test_review_contract_reconciliation_clears_only_unpublishable_unfrozen_rows(db_session, monkeypatch):
    user = User(email="review-contract-reconcile@example.com")
    db_session.add(user)
    db_session.flush()
    blocked = Listing(
        user_id=user.id, status=ListingStatus.PROCESSED, source_type="media_inventory_recovery",
        title="Unidentified photographed item", description="Preserve this copy.",
        processing_state="complete", processing_stage="quality_gate", needs_review=True,
        image_urls=["/media/recovery/item.jpg"], listing_images=[{"url": "/media/recovery/item.jpg"}],
        source_metadata={},
    )
    frozen = Listing(
        user_id=user.id, status=ListingStatus.ready, source_type="media_inventory_recovery",
        title="Operator approved item", description="Frozen operator copy.",
        processing_state="complete", needs_review=True,
        source_metadata={"operator_approved_at": "2026-10-01T10:00:00Z"},
    )
    ready = Listing(
        user_id=user.id, status=ListingStatus.PROCESSED, source_type="amazon_vine",
        title="Specific approved product", description="Complete product description.",
        processing_state="complete", needs_review=True, source_metadata={},
    )
    remote_live = Listing(
        user_id=user.id, status=ListingStatus.PROCESSED, source_type="media_inventory_recovery",
        title="Live external listing", description="Preserve live listing state.",
        processing_state="complete", needs_review=True, source_metadata={},
    )
    db_session.add_all([blocked, frozen, ready, remote_live])
    db_session.flush()
    db_session.add(MarketplaceListing(
        listing_id=remote_live.id, marketplace=MarketplaceName.ebay,
        marketplace_listing_id="remote-item-1", status=MarketplaceListingStatus.PUBLISHED,
    ))
    db_session.commit()

    def readiness(row):
        if row.id == blocked.id or row.id == remote_live.id:
            return {
                "queue": "NEEDS_ATTENTION", "review_required": False, "publishable": False,
                "canonical_blocking_reasons": ["Product identity is too generic to safely publish"],
                "destination_blocking_reasons": [], "blocking_reasons": [],
                "destination_preflight_pending": False,
            }
        return {"queue": "NEEDS_REVIEW", "review_required": True, "publishable": True}

    monkeypatch.setattr("app.services.listing_processing.canonical_listing_readiness", readiness)
    service = ListingProcessingService()
    preview = service.reconcile_review_contract_rows(
        db_session, user_id=user.id, listing_ids={blocked.id, frozen.id, ready.id, remote_live.id}, dry_run=True,
    )
    assert preview["invalidated"] == 0
    assert blocked.needs_review is True

    result = service.reconcile_review_contract_rows(
        db_session, user_id=user.id, listing_ids={blocked.id, frozen.id, ready.id, remote_live.id},
    )
    db_session.refresh(blocked)
    db_session.refresh(frozen)
    db_session.refresh(ready)
    db_session.refresh(remote_live)
    # The deterministic audit now also removes the synthetic "ready" fixture:
    # it lacks the required category, shipping and approved image evidence.
    assert result["invalidated"] == 2
    assert blocked.needs_review is False
    assert blocked.processing_state == "quality_repair_required"
    assert blocked.processing_blocking_reason == "CANONICAL_CATEGORY_INCOMPLETE"
    assert blocked.title == "Unidentified photographed item"
    assert blocked.description == "Preserve this copy."
    assert blocked.image_urls == ["/media/recovery/item.jpg"]
    assert frozen.needs_review is True
    assert ready.needs_review is False
    assert ready.processing_state == "quality_repair_required"
    assert remote_live.needs_review is True


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
    assert listing.processing_state == "blocked"
    assert listing.needs_review is False
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
    assert listing.needs_review is False
    assert listing.processing_state == "retry"
    assert listing.processing_stage == "machine_quality_repair"
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
    assert listing.needs_review is False
    assert listing.processing_state == "retry"
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


def test_reviewable_quality_does_not_requeue_fresh_blocked_destination(db_session):
    user = User(email="motors-blocked-requeue@example.com")
    db_session.add(user)
    db_session.flush()
    reason = "This eBay Motors category requires the Trading API listing path."
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        source_type="amazon_vine",
        title="RV Interior Light Fixture",
        description="Verified replacement RV interior light fixture with 12V power.",
        category_id="261084",
        category_suggestion="eBay Motors > RV Lighting",
        listing_price=24.99,
        suggested_price=24.99,
        processing_state="blocked",
        processing_stage="quality_gate",
        processing_blocking_reason=reason,
        needs_review=False,
        listing_images=_approved_images("/media/uploads/rv-light.jpg"),
        image_urls=["/media/uploads/rv-light.jpg"],
        marketplace_data={"marketplace_preflight":{"by_marketplace":{"ebay":{
            "status":"blocked","stale":False,"blockers":[{
                "code":"EBAY_MOTORS_TRADING_API_REQUIRED","message":reason,
            }],
        }}}},
    )
    db_session.add(listing)
    db_session.commit()

    changed = ListingProcessingService()._requeue_now_reviewable_blocked_listings(
        db_session, user_id=user.id, source_types={"amazon_vine"}, limit=10,
    )
    db_session.refresh(listing)

    assert changed == 0
    assert listing.processing_state == "needs_attention"


def test_backlog_recovery_does_not_requeue_attention_with_fresh_destination_blocker(db_session):
    user = User(email="motors-stalled-machine@example.com")
    db_session.add(user)
    db_session.flush()
    reason = "This eBay Motors category requires the Trading API listing path."
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        source_type="amazon_vine",
        title="RV Interior Light Fixture",
        description="Verified replacement RV interior light fixture with 12V power.",
        category_id="261084",
        category_suggestion="eBay Motors > RV Lighting",
        listing_price=24.99,
        suggested_price=24.99,
        processing_state="needs_attention",
        processing_stage="quality_gate",
        processing_blocking_reason=reason,
        needs_review=False,
        listing_images=_approved_images("/media/uploads/rv-light.jpg"),
        image_urls=["/media/uploads/rv-light.jpg"],
        marketplace_data={"marketplace_preflight": {"by_marketplace": {"ebay": {
            "status": "blocked", "stale": False, "blockers": [{
                "code": "EBAY_MOTORS_TRADING_API_REQUIRED", "message": reason,
            }],
        }}}},
    )
    db_session.add(listing)
    db_session.commit()
    service = ListingProcessingService()

    result = service.resume_backlog(
        db_session,
        user_id=user.id,
        limit=5,
        worker_id="test-motors-stalled-machine",
        source_types={"amazon_vine"},
    )
    db_session.refresh(listing)

    assert result["stalled_destination_blockers_preserved"] == 1
    assert result["processed"] == 0
    assert listing.processing_state == "needs_attention"
    assert listing.processing_stage == "preflight"
    assert listing.processing_error_stage == "preflight"
    assert listing.processing_blocking_reason == reason
    assert db_session.query(ListingProcessingEvent).filter(
        ListingProcessingEvent.listing_id == listing.id,
        ListingProcessingEvent.event_type == "processing_started",
    ).count() == 0


def test_backlog_recovery_does_not_repeat_explicit_grouping_review(db_session):
    user = User(email="google-stalled-grouping-review@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        status=ListingStatus.PROCESSED,
        source_type="intake_head_slate",
        title="Mixed Electronics Group",
        description="Preserve the item notes while the group boundary is reviewed.",
        processing_state="needs_attention",
        processing_stage="grouping_review",
        processing_blocking_reason="Saved photo analysis found multiple unrelated products.",
        source_metadata={
            "grouping_review": {"status": "required", "reason": "Saved photo analysis found multiple unrelated products."},
            "source_facts": {"group_kind": "multiple_unrelated_products"},
        },
        image_urls=["/media/intake/group/photo.jpg"],
        listing_images=[{"url": "/media/intake/group/photo.jpg"}],
        needs_review=False,
    )
    db_session.add(listing)
    db_session.commit()
    service = ListingProcessingService()

    result = service.resume_backlog(
        db_session,
        user_id=user.id,
        limit=5,
        worker_id="test-grouping-review-preserved",
        source_types={"intake_head_slate"},
    )
    db_session.refresh(listing)

    assert result["stalled_grouping_reviews_preserved"] == 1
    assert result["processed"] == 0
    assert listing.processing_state == "needs_attention"
    assert listing.processing_stage == "grouping_review"
    assert listing.needs_review is False
    assert db_session.query(ListingProcessingEvent).filter(
        ListingProcessingEvent.listing_id == listing.id,
        ListingProcessingEvent.event_type == "processing_started",
    ).count() == 0
