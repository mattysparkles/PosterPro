"""Regression checks for the Vine category-ID audit.

These are intentionally semantic guardrails: a numeric ID must not be reused
as evidence for an unrelated product family merely because it exists locally.
"""

from app.services.category_rules import verified_category_id


def test_audited_public_category_ids_are_stable():
    assert verified_category_id("Sporting Goods > Team Sports > Basketball > Basketballs") == "21208"
    assert verified_category_id("Home & Garden > Home Improvement > Heating, Cooling & Air > Humidifiers") == "71240"
    assert verified_category_id("Home & Garden > Yard, Garden & Outdoor Living > Pools & Spas > Pool Pumps") == "181485"
    assert verified_category_id("Home & Garden > Kitchen, Dining & Bar > Table Linens > Placemats") == "20660"
    assert verified_category_id("Home & Garden > Kitchen, Dining & Bar > Kitchen Tools & Gadgets > Cooking Thermometers") == "43421"
    assert verified_category_id("Consumer Electronics > Multipurpose Batteries & Power > Battery Management Systems > Protection Boards") == "262095"
    assert verified_category_id("Sporting Goods > Hunting > Scopes, Optics & Lasers > Scope Rings & Mounts") == "52510"


def test_known_bad_reused_ids_are_not_accepted_for_new_semantic_paths():
    assert verified_category_id("Home & Garden > Home Décor > Decorative Statues") is None
    assert verified_category_id("Consumer Electronics > Computers/Tablets & Networking > Monitors") is None
    assert verified_category_id("Sporting Goods > Fishing > Fishing Equipment > Rod Holders") is None
    assert verified_category_id("Automotive > Parts & Accessories > Golf Cart Parts > Lighting") is None


def test_verified_product_families_do_not_cross_map():
    assert verified_category_id("Welders, Cutters & Torches > MIG Welders") == "124822"
    assert verified_category_id("Automotive > Parts & Accessories > Charging & Starting Systems > Alternators & Generators") == "177697"
    assert verified_category_id("Health & Beauty > Medical & Mobility > Daily Living Aids > Medical Alert Systems") == "159878"
    assert verified_category_id("eBay Motors > Boat Parts > Engines & Components > Alternators & Charging Systems") == "50440"


def test_google_taxonomy_repair_rejects_the_generic_automotive_sealer_leaf(db_session, monkeypatch):
    from datetime import datetime
    from sqlalchemy import text
    from app.models.enums import ListingStatus
    from app.models.models import Listing, User
    from app.workers import tasks

    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    db_session.execute(text("insert into ebay_taxonomy_categories values ('EBAY_US','0','134','11808','Sealers & Sealing Machines',null,true,'Root > Business & Industrial > Material Handling > Sealers & Sealing Machines',5)"))
    user = User(email="google-bad-automotive-category@example.com")
    db_session.add(user)
    db_session.flush()
    row = Listing(
        user_id=user.id,
        status=ListingStatus.draft,
        source_type="intake_head_slate",
        title="Nissan Connecting Rod 56214-JA100",
        category_suggestion="Automotive",
        category_id="11808",
        processing_state="queued",
        created_at=datetime(2026, 9, 30),
        updated_at=datetime(2026, 9, 30),
    )
    db_session.add(row)
    db_session.flush()
    row_id = row.id
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(tasks, "resolve_local_category", lambda *_args, **_kwargs: [])

    result = tasks.repair_google_listing_categories_task.run(user.id, 10)

    db_session.expire_all()
    repaired = db_session.get(Listing, row_id)
    assert result["updated"] == 1, result
    assert repaired.category_id is None
    assert repaired.needs_review is False
    assert repaired.processing_state == "needs_attention"
    assert repaired.processing_stage == "category_review"
    assert "Sealers & Sealing Machines" in repaired.processing_blocking_reason
    assert repaired.source_metadata["category_history"][-1]["category_id"] == "11808"


def test_google_taxonomy_repair_selects_newest_unpublished_row_first(db_session, monkeypatch):
    from datetime import datetime
    from sqlalchemy import text
    from app.models.enums import ListingStatus
    from app.models.models import Listing, User
    from app.workers import tasks

    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    user = User(email="google-newest-category-first@example.com")
    db_session.add(user)
    db_session.flush()
    rows = []
    for day in (1, 2, 3):
        row = Listing(
            user_id=user.id,
            status=ListingStatus.draft,
            source_type="intake_head_slate",
            title=f"Test intake product {day}",
            category_id=None,
            marketplace_data={"marketplace_preflight": {"stale": False, "by_marketplace": {"ebay": {"status": "blocked", "stale": False}}}},
            processing_state="queued",
            created_at=datetime(2026, 9, day),
            updated_at=datetime(2026, 9, day),
        )
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    expected_id = rows[2].id
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(tasks, "resolve_local_category", lambda *_args, **_kwargs: [{
        "category_id": "62044", "category_name": "Test Leaf", "category_tree_id": "0",
        "category_tree_version": "134", "marketplace": "EBAY_US", "full_path": "Root > Household Goods > Test Leaf",
        "leaf": True, "score": 30,
    }])

    result = tasks.repair_google_listing_categories_task.run(user.id, 1)

    assert result["processed"] == 1
    assert result["updated"] == 1
    db_session.expire_all()
    repaired = db_session.get(Listing, expected_id)
    assert repaired.category_id == "62044"
    assert repaired.marketplace_data["marketplace_preflight"]["stale"] is True
    assert repaired.marketplace_data["marketplace_preflight"]["by_marketplace"]["ebay"]["stale"] is True


def test_google_taxonomy_repair_skips_recent_unchanged_ambiguous_rows(db_session, monkeypatch):
    from datetime import datetime
    from sqlalchemy import text
    from app.models.enums import ListingStatus
    from app.models.models import Listing, User
    from app.workers import tasks

    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    user = User(email="google-category-fair-retry@example.com")
    db_session.add(user)
    db_session.flush()
    user_id = user.id
    older = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Older unique unidentified product", category_id=None,
        processing_state="queued", created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1),
    )
    newest = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Newest ambiguous unidentified product", category_id=None,
        processing_state="queued", created_at=datetime(2026, 9, 2), updated_at=datetime(2026, 9, 2),
    )
    db_session.add_all([older, newest])
    db_session.flush()
    older_id, newest_id = older.id, newest.id
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(tasks, "resolve_local_category", lambda *_args, **_kwargs: [])

    first = tasks.repair_google_listing_categories_task.run(user_id, 1)
    assert first["failures"][0]["listing_id"] == newest_id
    db_session.expire_all()
    assert db_session.get(Listing, newest_id).source_metadata["category_resolution_attempt"]["status"] == "ambiguous_local_taxonomy_match"

    second = tasks.repair_google_listing_categories_task.run(user_id, 1)
    assert second["failures"][0]["listing_id"] == older_id
    assert second["skipped_recent_unchanged"] >= 1


def test_google_taxonomy_repair_skips_photo_groups_already_waiting_for_boundary_review(db_session, monkeypatch):
    from datetime import datetime
    from sqlalchemy import text
    from app.models.enums import ListingStatus
    from app.models.models import Listing, User
    from app.workers import tasks

    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    user = User(email="google-skip-group-review@example.com")
    db_session.add(user)
    db_session.flush()
    needs_group_review = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Mixed appliance boards", category_id=None, processing_state="needs_attention",
        processing_stage="grouping_review", processing_error_stage="grouping_review",
        created_at=datetime(2026, 9, 30), updated_at=datetime(2026, 9, 30),
    )
    category_candidate = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Specific kitchen appliance part", category_id=None, processing_state="retry",
        created_at=datetime(2026, 9, 29), updated_at=datetime(2026, 9, 29),
    )
    db_session.add_all([needs_group_review, category_candidate])
    db_session.flush()
    expected_id = category_candidate.id
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(tasks, "resolve_local_category", lambda *_args, **_kwargs: [])

    result = tasks.repair_google_listing_categories_task.run(user.id, 10)

    assert result["processed"] == 1
    assert result["failures"][0]["listing_id"] == expected_id


def test_google_taxonomy_repair_never_touches_archived_or_remote_live_rows(db_session, monkeypatch):
    from datetime import datetime
    from sqlalchemy import text
    from app.models.enums import ListingStatus, MarketplaceListingStatus, MarketplaceName
    from app.models.models import Listing, MarketplaceListing, User
    from app.workers import tasks

    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    user = User(email="google-category-protected-rows@example.com")
    db_session.add(user)
    db_session.flush()
    archived = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Archived toner cartridge", category_id=None, custom_labels=["archived_vine"],
        processing_state="retry", created_at=datetime(2026, 9, 30), updated_at=datetime(2026, 9, 30),
    )
    remote_live = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Remote-live thermostat", category_id=None,
        processing_state="retry", created_at=datetime(2026, 9, 29), updated_at=datetime(2026, 9, 29),
    )
    db_session.add_all([archived, remote_live])
    db_session.flush()
    db_session.add(MarketplaceListing(
        listing_id=remote_live.id, marketplace=MarketplaceName.ebay,
        marketplace_listing_id="REMOTE-123", status=MarketplaceListingStatus.PUBLISHED,
    ))
    db_session.flush()
    user_id = user.id
    archived_id = archived.id
    remote_live_id = remote_live.id
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(tasks, "resolve_local_category", lambda *_args, **_kwargs: [])

    result = tasks.repair_google_listing_categories_task.run(user_id, 10)

    assert result["processed"] == 0
    assert result["selected"] == 0
    db_session.expire_all()
    assert db_session.get(Listing, archived_id).category_id is None
    assert db_session.get(Listing, remote_live_id).category_id is None


def test_google_taxonomy_repair_requeues_only_stale_blocker_with_verified_category(db_session, monkeypatch):
    from datetime import datetime
    from sqlalchemy import text
    from app.models.enums import ListingStatus
    from app.models.models import Listing, User
    from app.workers import tasks

    db_session.execute(text("""create table ebay_taxonomy_categories (
        marketplace text, category_tree_id text, category_tree_version text,
        category_id text, category_name text, parent_category_id text,
        leaf boolean, full_path text, depth integer
    )"""))
    user = User(email="google-stale-category-blocker@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, status=ListingStatus.draft, source_type="intake_head_slate",
        title="Namco Game Controller", category_id="117042",
        category_suggestion="Controllers & Attachments", processing_state="retry",
        processing_blocking_reason=("A validated marketplace category ID is required before this Google intake draft can reach review; "
                                    "A validated marketplace category ID is required before this Google intake draft can reach review"),
        source_metadata={"category_validation": {
            "status": "VALID_EBAY_US", "category_id": "117042", "full_path": "Root > Video Games & Consoles > Controllers & Attachments"
        }},
        marketplace_data={"marketplace_preflight": {
            "stale": False, "by_marketplace": {"ebay": {"status": "blocked", "stale": False, "blockers": [{"code": "CATEGORY_REQUIRED"}]}}
        }},
        created_at=datetime(2026, 9, 30), updated_at=datetime(2026, 9, 30),
    )
    db_session.add(listing)
    db_session.flush()
    listing_id = listing.id
    user_id = user.id
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(tasks, "resolve_local_category", lambda *_args, **_kwargs: [])

    result = tasks.repair_google_listing_categories_task.run(user_id, 10)

    db_session.expire_all()
    repaired = db_session.get(Listing, listing_id)
    assert result["stale_valid_category_requeued"] == 1
    assert repaired.processing_state == "queued"
    assert repaired.processing_stage == "quality_validation"
    assert repaired.processing_blocking_reason is None
    assert repaired.marketplace_data["marketplace_preflight"]["stale"] is True
    assert repaired.marketplace_data["marketplace_preflight"]["by_marketplace"]["ebay"]["stale"] is True


def test_google_thermostat_exact_evidence_breaks_programmable_leaf_tie():
    from app.workers.tasks import _prefer_specific_google_category

    programmable = {"category_id": "115949", "category_name": "Programmable Thermostats", "full_path": "Root > Programmable Thermostats", "leaf": True, "score": 73}
    nonprogrammable = {"category_id": "115948", "category_name": "Non-programmable Thermostats", "full_path": "Root > Non-programmable Thermostats", "leaf": True, "score": 73}
    component = {"category_id": "182019", "category_name": "Component Thermostats & Hygrostats", "full_path": "Root > Component Thermostats & Hygrostats", "leaf": True, "score": 9}

    result = _prefer_specific_google_category("Nest Learning Thermostat", [nonprogrammable, programmable, component])

    assert [row["category_id"] for row in result] == ["115949", "182019"]
    assert _prefer_specific_google_category("Basic Thermostat", [nonprogrammable, programmable]) == [nonprogrammable, programmable]


def test_google_electric_pressure_washer_breaks_sibling_leaf_tie():
    from app.workers.tasks import _prefer_specific_google_category

    electric = {"category_id": "183764", "category_name": "Electric Pressure Washers", "leaf": True, "score": 51}
    diesel = {"category_id": "183763", "category_name": "Diesel Pressure Washers", "leaf": True, "score": 51}
    gas = {"category_id": "183765", "category_name": "Gas Pressure Washers", "leaf": True, "score": 51}
    broad = {"category_id": "50388", "category_name": "Pressure Washers", "leaf": True, "score": 51}
    parts = {"category_id": "183777", "category_name": "Pressure Washer Accessory Kits", "leaf": True, "score": 23}

    result = _prefer_specific_google_category("Electric Pressure Washer", [broad, diesel, gas, electric, parts])

    assert [row["category_id"] for row in result] == ["183764", "183777"]
    assert _prefer_specific_google_category("Pressure Washer", [broad, diesel, gas]) == [broad, diesel, gas]


def test_google_electric_paint_sprayer_selects_retail_leaf_not_industrial_leaf():
    from app.workers.tasks import _prefer_specific_google_category

    retail = {"category_id": "260160", "category_name": "Paint Sprayers", "full_path": "Root > Home & Garden > Paint Sprayers", "leaf": True, "score": 42}
    industrial = {"category_id": "71403", "category_name": "Paint Sprayers & Striping Machines", "full_path": "Root > Business & Industrial > Paint Sprayers & Striping Machines", "leaf": True, "score": 42}
    parts = {"category_id": "260639", "category_name": "Paint Sprayer Parts", "full_path": "Root > Home & Garden > Paint Sprayer Parts", "leaf": True, "score": 18}

    result = _prefer_specific_google_category("Electric Paint Sprayer", [industrial, parts, retail])

    assert [row["category_id"] for row in result] == ["260160", "260639"]
    assert _prefer_specific_google_category("Paint Sprayer", [industrial, retail]) == [industrial, retail]
