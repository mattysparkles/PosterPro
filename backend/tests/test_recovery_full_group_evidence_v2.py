from pathlib import Path

import pytest

from app.models.models import Listing, MediaRecoveryItemGroup, MediaRecoveryMedia, MediaRecoveryPhotoEvidence, MediaRecoveryRun, User
from scripts.reprocess_recovery_validation_sample_v2 import _upsert_photo_evidence
from app.services.media_recovery import MediaRecoveryService, consolidate_sibling_children_by_evidence
from app.services.listing_ai import build_listing_description
from app.services.photo_enrichment import FULL_GROUP_EVIDENCE_PIPELINE_VERSION, PHOTO_EVIDENCE_PIPELINE_VERSION, PhotoEnrichmentService, quality_gate


def evidence(media_id, **values):
    return {"media_id": media_id, "photo_role": "alternate_product_view", "barcode_attempts": [], "specifications": {}, "included_components": [], "damage": [], "confidence": .5, "extraction_method": "test", **values}


def test_every_photo_contributes_to_synthesis():
    service = PhotoEnrichmentService()
    output = service.synthesize_group_evidence([evidence(1, product_name="Widget"), evidence(2, brand="Acme", product_type="Widget")])
    assert output["usable_media_ids"] == [1, 2]


def test_upc_in_final_photo_overrides_first_visual_guess():
    output = PhotoEnrichmentService().synthesize_group_evidence([
        evidence(1, visual_title="Wrong visual guess", product_name="Wrong visual guess"),
        evidence(2, decoded_barcode_value="012345678905", decoded_barcode_type="UPC", upc="012345678905", product_name="Verified package"),
    ])
    assert output["identity"]["identifier"] == "012345678905"
    assert output["identity"]["title"] == "Verified package"


def test_product_name_beats_packaging_label_for_title():
    service = PhotoEnrichmentService()
    title = service._best_identity_title(
        name_fact={"value": "Kids Connection Military Building Set"},
        visual_fact={"value": ""},
        packaging_fact={"value": "Pennyfarthing Galleries, Torquay, England"},
        brand_fact={"value": ""},
        model_fact={"value": ""},
        product_types=[],
    )
    assert title == "Kids Connection Military Building Set"


def test_mixed_title_candidates_trigger_grouping_review():
    output = PhotoEnrichmentService().synthesize_group_evidence([
        evidence(1, product_name="Kids Connection Military Building Set", brand="Kids Connection", product_type="Toy"),
        evidence(2, product_name="Pennyfarthing Galleries, Torquay, England", brand="Pennyfarthing Galleries", product_type="Artwork"),
    ])
    assert output["group_kind"] == "multiple_unrelated_products"
    assert "multiple_brands" in output["conflicting_evidence"]


def test_visual_artwork_and_rear_gallery_label_remain_one_item():
    output = PhotoEnrichmentService().synthesize_group_evidence([
        evidence(1, visual_title="Framed seascape artwork", brand="Unknown", product_type="Framed Artwork"),
        evidence(2, packaging_identity="Pennyfarthing Galleries, Torquay, England", brand="Pennyfarthing Galleries"),
    ])
    assert output["identity"]["title"] == "Framed seascape artwork"
    assert output["group_kind"] == "one_item"


@pytest.mark.parametrize(
    "primary_fact, secondary_fact, expected_title",
    [
        (
            {"visual_title": "Whirlpool washer control board", "brand": "Whirlpool", "product_type": "Control Board"},
            {"packaging_identity": "W11478526", "brand": "Whirlpool"},
            "Whirlpool washer control board",
        ),
        (
            {"visual_title": "Vinyl album cover", "brand": "Columbia", "product_type": "Record Album"},
            {"packaging_identity": "PC 34975", "brand": "Columbia"},
            "Vinyl album cover",
        ),
        (
            {"visual_title": "Kids Connection Military Building Set", "brand": "Kids Connection", "product_type": "Toy"},
            {"packaging_identity": "UPC 123456789012", "brand": "Kids Connection"},
            "Kids Connection Military Building Set",
        ),
    ],
)
def test_complementary_front_and_label_roles_remain_one_item(primary_fact, secondary_fact, expected_title):
    output = PhotoEnrichmentService().synthesize_group_evidence([
        evidence(1, **primary_fact),
        evidence(2, **secondary_fact),
    ])
    assert output["identity"]["title"] == expected_title
    assert output["group_kind"] == "one_item"


def test_independent_synthesis_calls_do_not_bleed_titles():
    service = PhotoEnrichmentService()
    first = service.synthesize_group_evidence([
        evidence(1, product_name="Kids Connection Military Building Set", brand="Kids Connection"),
    ])
    second = service.synthesize_group_evidence([
        evidence(2, product_name="Pennyfarthing Galleries, Torquay, England", brand="Pennyfarthing Galleries"),
    ])
    assert first["identity"]["title"] == "Kids Connection Military Building Set"
    assert second["identity"]["title"] == "Pennyfarthing Galleries, Torquay, England"
    assert first["usable_media_ids"] == [1]
    assert second["usable_media_ids"] == [2]


def test_later_model_label_outweighs_visual_guess():
    output = PhotoEnrichmentService().synthesize_group_evidence([evidence(1, visual_title="Unknown tool"), evidence(2, model="P5231", mpn="P5231", product_name="RYOBI tool")])
    assert output["identity"]["model"] == "P5231"


def test_conflicting_barcodes_or_brands_marks_mixed():
    service = PhotoEnrichmentService()
    assert service.synthesize_group_evidence([evidence(1, decoded_barcode_value="012345678905"), evidence(2, decoded_barcode_value="012345678912")])["group_kind"] == "multiple_unrelated_products"
    assert service.synthesize_group_evidence([evidence(1, brand="Acme"), evidence(2, brand="Other")])["group_kind"] == "multiple_unrelated_products"


def test_intentional_set_and_alternate_views_remain_one_group():
    output = PhotoEnrichmentService().synthesize_group_evidence([evidence(1, brand="Acme", intentional_set=True, product_name="Tool set"), evidence(2, brand="Other", intentional_set=True, product_name="Tool set")])
    assert output["group_kind"] == "intentional_set"


def test_placeholder_facts_block_trusted_status():
    result = {"group_kind": "one_item", "usable_media_ids": [1], "identity": {"title": "Widget"}, "identity_confidence": .9, "placeholders": ["price", "weight"], "review_flags": []}
    assert quality_gate(result) == "blocked_placeholder_data"


def test_generic_identity_titles_do_not_pass_recovery_quality_gate():
    result = {
        "group_kind": "one_item",
        "usable_media_ids": [1, 2],
        "identity": {"title": "Automotive Parts"},
        "description": "Recovered from preserved inventory photos.",
        "category": "Automotive Parts",
        "identity_confidence": 0.9,
        "item_specifics": {},
        "placeholders": [],
        "review_flags": [],
    }
    assert quality_gate(result) == "blocked_placeholder_data"


def test_photo_evidence_upsert_is_idempotent(db_session):
    user = User(email="evidence@example.com"); db_session.add(user); db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="evidence-run", pipeline_version="test"); db_session.add(run); db_session.flush()
    group = MediaRecoveryItemGroup(run_id=run.id, recovery_item_id="REC-1", grouping_status="confirmed", media_paths_json=[]); db_session.add(group)
    media = MediaRecoveryMedia(run_id=run.id, absolute_path="/tmp/a.jpg", relative_path="a.jpg", sha256="a" * 64); db_session.add(media)
    listing = Listing(user_id=user.id, title="Existing", source_type="media_inventory_recovery"); db_session.add(listing); db_session.flush()
    payload = evidence(media.id, product_name="Widget")
    _upsert_photo_evidence(db_session, run=run, group=group, listing=listing, media=media, payload=payload)
    db_session.flush()
    _upsert_photo_evidence(db_session, run=run, group=group, listing=listing, media=media, payload=payload)
    db_session.commit()
    assert db_session.query(MediaRecoveryPhotoEvidence).count() == 1


def test_recovery_freeze_does_not_block_normal_listing(db_session):
    user = User(email="ordinary@example.com"); db_session.add(user); db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="frozen-run", pipeline_version="test", draft_creation_state="frozen_for_quality_audit"); db_session.add(run); db_session.flush()
    group = MediaRecoveryItemGroup(run_id=run.id, recovery_item_id="REC-2", grouping_status="confirmed", media_paths_json=[]); db_session.add(group); db_session.flush()
    db_session.add(Listing(user_id=user.id, title="Ordinary non-recovery draft", source_type="manual")); db_session.commit()
    with pytest.raises(RuntimeError):
        MediaRecoveryService().create_draft(db_session, user=user, group=group, facts={})
    assert db_session.query(Listing).filter_by(source_type="manual").count() == 1


def test_validation_sample_only_allows_recovery_draft_creation(db_session):
    user = User(email="sample-mode@example.com")
    db_session.add(user); db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="sample-mode-run", pipeline_version="test", draft_creation_state="validation_sample_only")
    db_session.add(run); db_session.flush()
    group = MediaRecoveryItemGroup(run_id=run.id, recovery_item_id="REC-3", grouping_status="confirmed", media_paths_json=[])
    db_session.add(group); db_session.flush()
    storage_root = Path("/opt/apps/posterpro/repo/backend/storage/recovery_test_sample")
    storage_root.mkdir(parents=True, exist_ok=True)
    photo_path = storage_root / "product.jpg"
    photo_path.write_bytes(b"product")
    db_session.add(
        MediaRecoveryMedia(
            run_id=run.id,
            absolute_path=str(photo_path),
            relative_path="recovery_test_sample/product.jpg",
            sha256="3" * 64,
            final_disposition="assigned_to_item",
        )
    )
    db_session.flush()
    group.media_paths_json = [str(photo_path)]
    facts = {
        "title": "Recovered photographed inventory item requiring identity review",
        "product_name": "Recovered photographed inventory item requiring identity review",
        "category": "General resale > Identity review required",
        "specifics": {"Recovery SKU": "REC-3"},
        "keywords": ["recovered inventory"],
        "description": "Review draft",
        "included": "Visible components only",
        "condition": "Used",
        "condition_notes": "Review required",
        "suggested_price": 19.99,
        "quick_sale_price": 14.99,
        "price_range": "$15–$30",
        "pricing_explanation": "Placeholder",
        "shipping": "Measure and pack before publish",
        "shipping_weight": "3 lb (estimated)",
        "package_dimensions": {"length": 12, "width": 10, "height": 8},
        "confidence": 0.42,
        "field_confidence": {"grouping": 0.58, "identity": 0.2, "condition": 0.3, "price": 0.2},
        "warnings": [],
        "alternatives": [],
    }
    listing = MediaRecoveryService().create_draft(db_session, user=user, group=group, facts=facts)
    db_session.commit()
    assert listing.id is not None


def test_recovery_draft_copy_reads_like_product_listing_and_not_recovery_caption():
    description = build_listing_description(
        title="Keurig K-Compact Single Serve Coffee Maker Black",
        item_specifics={
            "Brand": "Keurig",
            "Model": "K-Compact",
            "Type": "Single Serve Coffee Maker",
            "Color": "Black",
            "Compatible Capsule/Pad System": "K-Cup",
        },
        included="Coffee maker only",
        condition_notes="Light cosmetic wear to the box; unit appears complete in photos.",
        photo_notes=["Barcode and packaging label visible in the recovered photo set."],
    )
    lowered = description.lower()
    assert "recovered from preserved inventory photos" not in lowered
    assert "marketplace listing built from the item details" in lowered
    assert "key details include brand: keurig" in lowered
    assert "coffee maker" in lowered


def test_recovery_draft_excludes_slate_photos_from_listing_gallery(db_session):
    user = User(email="slate-filter@example.com")
    db_session.add(user)
    db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="slate-filter-run", pipeline_version="test", draft_creation_state="validation_sample_only")
    db_session.add(run)
    db_session.flush()
    group = MediaRecoveryItemGroup(run_id=run.id, recovery_item_id="REC-SLATE", grouping_status="confirmed", media_paths_json=[])
    db_session.add(group)
    db_session.flush()

    storage_root = Path("/opt/apps/posterpro/repo/backend/storage/recovery_test")
    storage_root.mkdir(parents=True, exist_ok=True)
    product_path = storage_root / "product.jpg"
    slate_path = storage_root / "slate.jpg"
    product_path.write_bytes(b"product")
    slate_path.write_bytes(b"slate")
    db_session.add_all(
        [
            MediaRecoveryMedia(
                run_id=run.id,
                absolute_path=str(product_path),
                relative_path="product.jpg",
                sha256="1" * 64,
                final_disposition="assigned_to_item",
            ),
            MediaRecoveryMedia(
                run_id=run.id,
                absolute_path=str(slate_path),
                relative_path="slate.jpg",
                sha256="2" * 64,
                final_disposition="probable_slate",
            ),
        ]
    )
    db_session.flush()
    group.media_paths_json = [str(product_path), str(slate_path)]
    facts = {
        "title": "Keurig K-Compact Single Serve Coffee Maker Black",
        "product_name": "Keurig K-Compact Single Serve Coffee Maker Black",
        "category": "Home & Garden > Kitchen, Dining & Bar > Coffee, Tea & Espresso Makers",
        "specifics": {
            "Brand": "Keurig",
            "Model": "K-Compact",
            "Type": "Single Serve Coffee Maker",
        },
        "keywords": ["keurig", "coffee maker"],
        "description": "Keurig single serve coffee maker",
        "included": "Coffee maker only",
        "condition": "Used",
        "condition_notes": "Light wear",
        "suggested_price": 39.99,
        "quick_sale_price": 29.99,
        "price_range": "$30–$50",
        "pricing_explanation": "Test",
        "shipping": "Ground service",
        "shipping_weight": "9 lb (estimated)",
        "package_dimensions": {"length": 16, "width": 14, "height": 12},
        "confidence": 0.88,
        "field_confidence": {"grouping": 0.9, "identity": 0.9, "condition": 0.8, "price": 0.8},
        "warnings": [],
        "alternatives": [],
    }
    listing = MediaRecoveryService().create_draft(db_session, user=user, group=group, facts=facts)
    db_session.commit()
    assert listing.image_urls == ["/media/recovery_test/product.jpg"]


@pytest.mark.parametrize(
    "first_title, second_title",
    [
        ("Hess Truck", "Hess Truck Packaging"),
        ("Plant Grow Light", "Dual Head Plant Grow Light"),
        ("Discovery RC T-Rex", "Discovery RC T-Rex"),
    ],
)
def test_sibling_children_merge_on_normalized_identity(db_session, first_title, second_title):
    user = User(email=f"merge-{first_title.lower().replace(' ', '-')}-{second_title.lower().replace(' ', '-')}"[:255])
    db_session.add(user); db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key=f"merge-{first_title[:8]}-{second_title[:8]}", pipeline_version="test", draft_creation_state="enabled")
    db_session.add(run); db_session.flush()
    parent = MediaRecoveryItemGroup(
        run_id=run.id,
        recovery_item_id="SP-20260708-0001",
        grouping_status="needs_grouping_review",
        media_paths_json=[f"/tmp/{index}.jpg" for index in range(4)],
    )
    db_session.add(parent); db_session.flush()
    child_a = MediaRecoveryItemGroup(
        run_id=run.id,
        parent_group_id=parent.id,
        recovery_item_id="SP-20260708-0001-I001",
        grouping_status="confirmed",
        grouping_confidence=0.9,
        media_paths_json=[parent.media_paths_json[0], parent.media_paths_json[1]],
        analysis_json={"sequence_proposal": {"title": first_title}},
    )
    child_b = MediaRecoveryItemGroup(
        run_id=run.id,
        parent_group_id=parent.id,
        recovery_item_id="SP-20260708-0001-I002",
        grouping_status="confirmed",
        grouping_confidence=0.85,
        media_paths_json=[parent.media_paths_json[2], parent.media_paths_json[3]],
        analysis_json={"sequence_proposal": {"title": second_title}},
    )
    db_session.add_all([child_a, child_b]); db_session.commit()

    merged = consolidate_sibling_children_by_evidence(db_session, parent=parent)
    db_session.commit()

    refreshed_a = db_session.get(MediaRecoveryItemGroup, child_a.id)
    refreshed_b = db_session.get(MediaRecoveryItemGroup, child_b.id)
    assert merged["merged_clusters"] == 1
    assert merged["merged_groups"] == 1
    assert refreshed_a.grouping_status == "confirmed"
    assert refreshed_a.media_paths_json == parent.media_paths_json
    assert refreshed_b.grouping_status == "superseded"
    assert refreshed_b.analysis_json["sequence_consolidation_v1"]["merged_into_child_group_id"] == refreshed_a.id


def test_neighboring_parents_merge_on_boundary_identity(db_session):
    user = User(email="boundary-merge@example.com")
    db_session.add(user); db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="boundary-merge", pipeline_version="test", draft_creation_state="enabled")
    db_session.add(run); db_session.flush()
    left_parent = MediaRecoveryItemGroup(
        run_id=run.id,
        recovery_item_id="SP-20260708-0100",
        grouping_status="needs_grouping_review",
        media_paths_json=["/tmp/left-0.jpg", "/tmp/left-1.jpg"],
    )
    right_parent = MediaRecoveryItemGroup(
        run_id=run.id,
        recovery_item_id="SP-20260708-0101",
        grouping_status="needs_grouping_review",
        media_paths_json=["/tmp/right-0.jpg", "/tmp/right-1.jpg"],
    )
    db_session.add_all([left_parent, right_parent]); db_session.flush()
    left_child = MediaRecoveryItemGroup(
        run_id=run.id,
        parent_group_id=left_parent.id,
        recovery_item_id="SP-20260708-0100-I001",
        grouping_status="confirmed",
        grouping_confidence=0.9,
        media_paths_json=[left_parent.media_paths_json[0], left_parent.media_paths_json[1]],
        analysis_json={"sequence_proposal": {"title": "Dual Head Plant Grow Light"}},
    )
    right_child = MediaRecoveryItemGroup(
        run_id=run.id,
        parent_group_id=right_parent.id,
        recovery_item_id="SP-20260708-0101-I001",
        grouping_status="confirmed",
        grouping_confidence=0.85,
        media_paths_json=[right_parent.media_paths_json[0], right_parent.media_paths_json[1]],
        analysis_json={"sequence_proposal": {"title": "Plant Grow Light"}},
    )
    db_session.add_all([left_child, right_child]); db_session.commit()

    from app.services.media_recovery import consolidate_boundary_parents_by_evidence

    merged = consolidate_boundary_parents_by_evidence(db_session, run=run)
    db_session.commit()

    refreshed_left = db_session.get(MediaRecoveryItemGroup, left_child.id)
    refreshed_right = db_session.get(MediaRecoveryItemGroup, right_child.id)
    assert merged["merged_clusters"] == 1
    assert merged["merged_groups"] == 1
    assert refreshed_left.grouping_status == "confirmed"
    assert refreshed_right.grouping_status == "superseded"


def test_merge_winner_prefers_specific_title_over_bare_identifier(db_session):
    user = User(email="merge-specificity@example.com")
    db_session.add(user); db_session.flush()
    run = MediaRecoveryRun(user_id=user.id, run_key="merge-specificity", pipeline_version="test", draft_creation_state="enabled")
    db_session.add(run); db_session.flush()
    parent = MediaRecoveryItemGroup(
        run_id=run.id,
        recovery_item_id="SP-20260708-0200",
        grouping_status="needs_grouping_review",
        media_paths_json=[f"/tmp/spec-{index}.jpg" for index in range(4)],
    )
    db_session.add(parent); db_session.flush()
    barcode_child = MediaRecoveryItemGroup(
        run_id=run.id,
        parent_group_id=parent.id,
        recovery_item_id="SP-20260708-0200-I001",
        grouping_status="confirmed",
        grouping_confidence=0.9,
        media_paths_json=[parent.media_paths_json[0]],
        analysis_json={"sequence_proposal": {"title": "35541-10020"}},
        draft_listing_id=None,
    )
    product_child = MediaRecoveryItemGroup(
        run_id=run.id,
        parent_group_id=parent.id,
        recovery_item_id="SP-20260708-0200-I002",
        grouping_status="confirmed",
        grouping_confidence=0.8,
        media_paths_json=[parent.media_paths_json[1], parent.media_paths_json[2]],
        analysis_json={"sequence_proposal": {"title": "FurDaddy Pet Hair Remover"}},
        draft_listing_id=None,
    )
    db_session.add_all([barcode_child, product_child]); db_session.commit()

    merged = consolidate_sibling_children_by_evidence(db_session, parent=parent)
    db_session.commit()

    refreshed_barcode = db_session.get(MediaRecoveryItemGroup, barcode_child.id)
    refreshed_product = db_session.get(MediaRecoveryItemGroup, product_child.id)
    assert merged["merged_clusters"] == 1
    assert merged["merged_groups"] == 1
    assert refreshed_product.grouping_status == "confirmed"
    assert refreshed_barcode.grouping_status == "superseded"
    assert refreshed_barcode.analysis_json["sequence_consolidation_v1"]["merged_into_child_group_id"] == refreshed_product.id
