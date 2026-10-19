from app.models.enums import MarketplaceName
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplacePublishBatch, User
from app.services.ebay_failure_recovery import build_ebay_failed_batch_recovery_preview, _required_fields
from types import SimpleNamespace
import pytest


def test_failed_batch_preview_blocks_unknown_and_names_provider_required_fields(db_session):
    user = User(email="recovery-preview@example.com")
    db_session.add(user)
    db_session.flush()
    one, two = Listing(user_id=user.id, title="Unknown outcome"), Listing(user_id=user.id, title="Missing aspect")
    db_session.add_all([one, two])
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-1", user_id=user.id, selected_count=2, status="failed")
    db_session.add(batch)
    db_session.add_all([
        MarketplaceCrosspostJob(batch_id=batch.id, user_id=user.id, listing_id=one.id, status="failed", result_summary={"results":[{"marketplace":"ebay","response":{"error_code":"EBAY_25001","remote_outcome":"REMOTE_STATE_UNKNOWN"}}]}),
        MarketplaceCrosspostJob(batch_id=batch.id, user_id=user.id, listing_id=two.id, status="failed", result_summary={"results":[{"marketplace":"ebay","response":{"error_code":"EBAY_25002","provider_response":{"errors":[{"parameters":[{"value":"Product"}]}]}}}]}),
    ])
    db_session.commit()

    preview = build_ebay_failed_batch_recovery_preview(db_session, batch_id=batch.id, user_id=user.id)
    assert preview["execution_available"] is False
    assert [row["classification"] for row in preview["rows"]] == ["REMOTE_OUTCOME_UNKNOWN", "REQUIRED_DATA"]
    assert preview["rows"][1]["required_fields"] == ["Product"]
    assert all(row["retry_eligible"] is False for row in preview["rows"])


def test_failed_batch_preview_excludes_cross_tenant_jobs_and_listings(db_session):
    owner = User(email="recovery-owner@example.com")
    other = User(email="recovery-other@example.com")
    db_session.add_all([owner, other])
    db_session.flush()
    private_listing = Listing(user_id=other.id, title="Other tenant private item")
    db_session.add(private_listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-tenant-boundary", user_id=owner.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=other.id, listing_id=private_listing.id,
        status="failed", result_summary={"private": "provider response"},
    ))
    db_session.commit()

    preview = build_ebay_failed_batch_recovery_preview(
        db_session, batch_id=batch.id, user_id=owner.id,
    )
    assert preview["row_count"] == 0
    assert preview["rows"] == []


def test_required_field_parser_handles_provider_errors_as_json_or_unstructured_string():
    assert _required_fields({"provider_response": {"errors": '[{"parameters":[{"name":"field","value":"Product"}]}]'}}) == ["Product"]
    assert _required_fields({"provider_response": {"errors": "provider returned an unstructured error string"}}) == []


def test_required_field_parser_extracts_specific_name_not_provider_explanation():
    provider_error = {
        "provider_response": {"errors": [{
            "message": "The item specific Product\u00a0is missing. Add Product to this listing, enter a valid value, and then try again.",
            "parameters": [
                {"name": "error description", "value": "The item specific Product is missing. Add Product to this listing, enter a valid value, and then try again."},
                {"name": "field", "value": "Product"},
            ],
        }]},
    }
    assert _required_fields(provider_error) == ["Product"]


@pytest.mark.anyio
async def test_operator_recovery_endpoint_adds_live_taxonomy_and_missing_specifics(monkeypatch, db_session):
    from app.api import ebay as ebay_api

    user = User(email="taxonomy-recovery@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, title="Sample widget", category_id="12345", category_suggestion="Sample widgets",
        condition="Used", item_specifics={"Brand": "Acme"}, source_metadata={"taxonomy_tree_id": "0", "taxonomy_marketplace": "EBAY_US"},
    )
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-taxonomy", user_id=user.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=user.id, listing_id=listing.id, status="failed",
        result_summary={"results": [{"marketplace": "ebay", "response": {
            "error_code": "EBAY_25002", "provider_short_message": "Missing required field Product",
            "provider_response": {"errors": [{"parameters": [{"value": "Product"}]}]},
        }}]},
    ))
    db_session.commit()

    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    async def fake_aspects(*args, **kwargs):
        return {"categoryTreeVersion": "fresh-v", "aspects": [
            {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True}},
            {"localizedAspectName": "Product", "aspectConstraint": {"aspectRequired": True}},
        ]}
    async def fake_condition_policy(*args, **kwargs):
        return {"categoryId": "12345", "itemConditionRequired": True, "itemConditions": [
            {"conditionId": 3000, "conditionDescription": "Used"},
            {"conditionId": 5000, "conditionDescription": "Good"},
        ]}

    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "get_ebay_offers_for_sku", lambda *args, **kwargs: _async_value([]))
    monkeypatch.setattr(ebay_api, "verify_ebay_category", lambda *args, **kwargs: _async_value({"verified": True, "leaf": True, "source": "live_test"}))
    monkeypatch.setattr(ebay_api, "get_required_item_specifics", fake_aspects)
    monkeypatch.setattr(ebay_api, "get_ebay_item_condition_policies", fake_condition_policy)
    preview = await ebay_api.preview_failed_ebay_batch_recovery(batch_id=batch.id, db=db_session, current_user=user)

    taxonomy = preview["rows"][0]["taxonomy_preflight"]
    assert taxonomy["status"] == "confirmed"
    assert taxonomy["category_tree_version"] == "fresh-v"
    assert taxonomy["missing_required_aspects"] == ["Product"]
    assert taxonomy["provider_required_fields_missing"] == ["Product"]
    assert taxonomy["condition_policy"]["condition_required"] is True
    assert [x["condition_id"] for x in taxonomy["condition_policy"]["supported_conditions"]] == [3000, 5000]
    assert preview["rows"][0]["retry_eligible"] is False


@pytest.mark.anyio
async def test_operator_recovery_endpoint_blocks_unowned_category_without_guessing_tree(monkeypatch, db_session):
    from app.api import ebay as ebay_api

    user = User(email="taxonomy-owner-recovery@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(user_id=user.id, title="Unowned category", category_id="99999", condition="Used")
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-unowned", user_id=user.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=user.id, listing_id=listing.id, status="failed",
        result_summary={"results": [{"marketplace": "ebay", "response": {"error_code": "EBAY_25005", "provider_short_message": "Invalid category"}}]},
    ))
    db_session.commit()

    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    async def should_not_verify(*args, **kwargs): raise AssertionError("no marketplace tree should be guessed")
    async def exact_offer_not_found(*args, **kwargs):
        raise ebay_api.EbayIntegrationError(
            "exact SKU returned no offer", http_status=404,
            provider_response={"errors": [{"errorId": 25713, "message": "This Offer is not available."}]},
        )
    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "get_ebay_offers_for_sku", exact_offer_not_found)
    monkeypatch.setattr(ebay_api, "verify_ebay_category", should_not_verify)
    preview = await ebay_api.preview_failed_ebay_batch_recovery(batch_id=batch.id, db=db_session, current_user=user)

    taxonomy = preview["rows"][0]["taxonomy_preflight"]
    assert taxonomy["status"] == "blocked"
    assert taxonomy["source"] == "local_taxonomy_ownership"
    assert preview["rows"][0]["deterministic_offer_reconciliation"]["status"] == "no_exact_sku_offer_returned"
    assert preview["rows"][0]["deterministic_offer_reconciliation"]["retry_eligible"] is False


@pytest.mark.anyio
async def test_deterministic_failure_reconciles_remote_offer_category_and_specifics_read_only(monkeypatch, db_session):
    from app.api import ebay as ebay_api
    from app.services import ebay_taxonomy
    from app.services.ebay_service import _build_ebay_sku

    user = User(email="remote-offer-category@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, title="Automotive paint thickness gauge", category_id="12345",
        item_specifics={"Brand": "Acme"},
        source_metadata={"taxonomy_tree_id": "0", "taxonomy_marketplace": "EBAY_US"},
    )
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-remote-category", user_id=user.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=user.id, listing_id=listing.id, status="failed",
        result_summary={"results": [{"marketplace": "ebay", "response": {
            "error_code": "EBAY_25002", "provider_short_message": "Missing Original/Reproduction",
            "provider_response": {"errors": [{"parameters": [{"value": "Original/Reproduction"}]}]},
        }}]},
    ))
    db_session.commit()

    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    async def fake_offers(account, sku):
        assert account.access_token == "token" and sku == _build_ebay_sku(user.id, listing.id)
        return [{"offerId": "offer-1", "sku": sku, "categoryId": "99999", "marketplaceId": "EBAY_US", "listingStatus": "UNPUBLISHED"}]
    async def fake_item(account, sku):
        assert sku == _build_ebay_sku(user.id, listing.id)
        return {"condition": "NEW", "product": {"title": "Old remote title", "aspects": {"Brand": ["Acme"]}}}
    async def fake_aspects(token, category_id, *, marketplace_id, tree_id):
        required = "Original/Reproduction" if category_id == "99999" else "Brand"
        return {"categoryTreeVersion": "fresh-v", "aspects": [{"localizedAspectName": required, "aspectConstraint": {"aspectRequired": True}}]}
    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "verify_ebay_category", lambda *args, **kwargs: _async_value({"verified": True, "leaf": True, "source": "live_test"}))
    monkeypatch.setattr(ebay_api, "get_required_item_specifics", fake_aspects)
    monkeypatch.setattr(ebay_api, "get_ebay_item_condition_policies", lambda *args, **kwargs: _async_value({"itemConditions": []}))
    monkeypatch.setattr(ebay_api, "get_ebay_offers_for_sku", fake_offers)
    monkeypatch.setattr(ebay_api, "get_ebay_inventory_item_for_sku", fake_item)
    monkeypatch.setattr(ebay_taxonomy, "category_by_id", lambda db, cid, tree, marketplace: {"full_path": f"Test category {cid}"} if cid in {"12345", "99999"} else None)

    preview = await ebay_api.preview_failed_ebay_batch_recovery(batch_id=batch.id, db=db_session, current_user=user)
    evidence = preview["rows"][0]["deterministic_offer_reconciliation"]
    remote = evidence["offers"][0]
    assert evidence["status"] == "exact_sku_offer_found"
    assert remote["category_mismatch"] is True
    assert remote["remote_category_path"] == "Test category 99999"
    assert remote["local_category_path"] == "Test category 12345"
    assert remote["remote_required_aspects_missing"] == ["Original/Reproduction"]
    assert remote["local_aspect_names"] == ["Brand"]
    assert remote["remote_required_aspects_missing_locally"] == ["Original/Reproduction"]
    assert remote["remote_condition"] == "NEW"
    assert preview["rows"][0]["retry_eligible"] is False
    db_session.refresh(listing)
    assert listing.category_id == "12345"


@pytest.mark.anyio
async def test_operator_recovery_reconciles_25001_against_active_exact_sku_without_enabling_retry(monkeypatch, db_session):
    from app.api import ebay as ebay_api
    from app.services.ebay_service import _build_ebay_sku

    user = User(email="remote-outcome-recovery@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(user_id=user.id, title="Unknown offer outcome")
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-unknown-remote", user_id=user.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=user.id, listing_id=listing.id, status="failed",
        result_summary={"results": [{"marketplace": "ebay", "response": {
            "error_code": "EBAY_25001", "remote_outcome": "REMOTE_STATE_UNKNOWN",
        }}]},
    ))
    db_session.commit()

    async def active_snapshot(account, **kwargs):
        assert account.access_token == "token"
        return [{"source_identifiers": {"sku": _build_ebay_sku(user.id, listing.id), "ebay_listing_id": "remote-123"}}]

    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "get_ebay_trading_active_operations", active_snapshot)
    preview = await ebay_api.preview_failed_ebay_batch_recovery(batch_id=batch.id, db=db_session, current_user=user)

    reconciliation = preview["rows"][0]["active_offer_reconciliation"]
    assert reconciliation["status"] == "active_exact_sku_match"
    assert reconciliation["remote_listing_id"] == "remote-123"
    assert reconciliation["retry_eligible"] is False
    assert preview["rows"][0]["retry_eligible"] is False


@pytest.mark.anyio
async def test_operator_recovery_uses_trading_snapshot_when_inventory_catalog_is_unavailable(monkeypatch, db_session):
    from app.api import ebay as ebay_api

    user = User(email="trading-snapshot-recovery@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(user_id=user.id, title="Unknown remote outcome")
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-trading-reconcile", user_id=user.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=user.id, listing_id=listing.id, status="failed",
        result_summary={"results": [{"marketplace": "ebay", "response": {"error_code": "EBAY_25001", "remote_outcome": "REMOTE_STATE_UNKNOWN"}}]},
    ))
    db_session.commit()

    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    async def inventory_must_not_run(*args, **kwargs): raise AssertionError("reconciliation must not hydrate Inventory catalog")
    async def unavailable_trading(*args, **kwargs): raise ebay_api.EbayIntegrationError("Trading snapshot unavailable")
    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "get_active_ebay_listings", inventory_must_not_run)
    monkeypatch.setattr(ebay_api, "get_ebay_trading_active_operations", unavailable_trading)

    preview = await ebay_api.preview_failed_ebay_batch_recovery(batch_id=batch.id, db=db_session, current_user=user)
    reconciliation = preview["rows"][0]["active_offer_reconciliation"]
    assert reconciliation["status"] == "unavailable"
    assert reconciliation["retry_eligible"] is False
    assert "Trading snapshot unavailable" in reconciliation["error"]


@pytest.mark.anyio
async def test_operator_recovery_checks_exact_sku_offers_after_active_miss(monkeypatch, db_session):
    from app.api import ebay as ebay_api
    from app.services.ebay_service import _build_ebay_sku

    user = User(email="exact-offer-recovery@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(user_id=user.id, title="Possible unpublished offer")
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-exact-offer", user_id=user.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=user.id, listing_id=listing.id, status="failed",
        result_summary={"results": [{"marketplace": "ebay", "response": {"error_code": "EBAY_25001", "remote_outcome": "REMOTE_STATE_UNKNOWN"}}]},
    ))
    db_session.commit()

    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    async def active_miss(*args, **kwargs): return []
    async def exact_offers(account, sku):
        assert account.access_token == "token"
        assert sku == _build_ebay_sku(user.id, listing.id)
        return [{"offerId": "offer-88", "listingStatus": "UNPUBLISHED", "marketplaceId": "EBAY_US"}]
    async def inventory_item(account, sku):
        return {"condition": "NEW", "product": {"title": "Remote inventory only", "aspects": {"Brand": ["Acme"]}}, "packageWeightAndSize": {"weight": {"value": 2}}}
    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "get_ebay_trading_active_operations", active_miss)
    monkeypatch.setattr(ebay_api, "get_ebay_offers_for_sku", exact_offers)
    monkeypatch.setattr(ebay_api, "get_ebay_inventory_item_for_sku", inventory_item)

    preview = await ebay_api.preview_failed_ebay_batch_recovery(batch_id=batch.id, db=db_session, current_user=user)
    row = preview["rows"][0]
    assert row["active_offer_reconciliation"]["status"] == "no_active_exact_sku_match"
    assert row["sku_offer_reconciliation"]["status"] == "offers_found"
    assert row["sku_offer_reconciliation"]["offers"][0]["offer_id"] == "offer-88"
    assert row["sku_offer_reconciliation"]["offers"][0]["listing_status"] == "UNPUBLISHED"
    assert row["sku_offer_reconciliation"]["retry_eligible"] is False
    assert row["inventory_item_reconciliation"]["status"] == "inventory_item_found"
    assert row["inventory_item_reconciliation"]["aspect_names"] == ["Brand"]
    assert row["inventory_item_reconciliation"]["remote_listing_id"] is None
    assert row["local_recovery_preflight"]["status"] in {"blocked", "needs_review", "ready", "ready_with_warnings"}
    assert row["retry_eligible"] is False


@pytest.mark.anyio
async def test_operator_recovery_records_exact_sku_offer_not_found_without_enabling_retry(monkeypatch, db_session):
    from app.api import ebay as ebay_api

    user = User(email="exact-offer-not-found@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(user_id=user.id, title="Offer not found")
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-no-exact-offer", user_id=user.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=user.id, listing_id=listing.id, status="failed",
        result_summary={"results": [{"marketplace": "ebay", "response": {"error_code": "EBAY_25001", "remote_outcome": "REMOTE_STATE_UNKNOWN"}}]},
    ))
    db_session.commit()

    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    async def active_miss(*args, **kwargs): return []
    async def offer_not_found(*args, **kwargs):
        raise ebay_api.EbayIntegrationError(
            "eBay API request failed (404): 25713", http_status=404,
            provider_response={"errors": [{"errorId": 25713, "message": "This Offer is not available."}]},
        )
    async def item_not_found(*args, **kwargs):
        raise ebay_api.EbayIntegrationError("inventory item not found", http_status=404)
    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "get_ebay_trading_active_operations", active_miss)
    monkeypatch.setattr(ebay_api, "get_ebay_offers_for_sku", offer_not_found)
    monkeypatch.setattr(ebay_api, "get_ebay_inventory_item_for_sku", item_not_found)

    preview = await ebay_api.preview_failed_ebay_batch_recovery(batch_id=batch.id, db=db_session, current_user=user)
    row = preview["rows"][0]
    exact = row["sku_offer_reconciliation"]
    assert exact["status"] == "no_exact_sku_offer_returned"
    assert exact["provider_http_status"] == 404
    assert exact["provider_error_ids"] == ["25713"]
    assert exact["retry_eligible"] is False
    assert row["inventory_item_reconciliation"]["status"] == "no_inventory_item_returned"
    assert row["local_recovery_preflight"]["status"] in {"blocked", "needs_review", "ready", "ready_with_warnings"}
    assert row["retry_eligible"] is False


@pytest.mark.anyio
async def test_operator_recovery_refreshes_live_taxonomy_for_unknown_outcome(monkeypatch, db_session):
    from app.api import ebay as ebay_api
    from app.services.ebay_service import _build_ebay_sku

    user = User(email="unknown-taxonomy-recovery@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, title="Unknown outcome product", category_id="12345",
        source_metadata={"taxonomy_tree_id": "0", "taxonomy_marketplace": "EBAY_US"},
    )
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-unknown-taxonomy", user_id=user.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=user.id, listing_id=listing.id, status="failed",
        result_summary={"results": [{"marketplace": "ebay", "response": {"error_code": "EBAY_25001", "remote_outcome": "REMOTE_STATE_UNKNOWN"}}]},
    ))
    db_session.commit()

    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    async def category(*args, **kwargs): return {"verified": True, "leaf": True, "source": "live_test"}
    async def aspects(*args, **kwargs):
        return {"categoryTreeVersion": "fresh", "aspects": [{"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True}}]}
    async def active(*args, **kwargs):
        return [{"source_identifiers": {"sku": _build_ebay_sku(user.id, listing.id), "ebay_listing_id": "remote-1"}}]
    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "verify_ebay_category", category)
    monkeypatch.setattr(ebay_api, "get_required_item_specifics", aspects)
    monkeypatch.setattr(ebay_api, "get_ebay_trading_active_operations", active)

    preview = await ebay_api.preview_failed_ebay_batch_recovery(batch_id=batch.id, db=db_session, current_user=user)
    row = preview["rows"][0]
    assert row["taxonomy_preflight"]["status"] == "confirmed"
    assert row["taxonomy_preflight"]["missing_required_aspects"] == ["Brand"]
    assert row["active_offer_reconciliation"]["status"] == "active_exact_sku_match"
    assert row["retry_eligible"] is False


@pytest.mark.anyio
async def test_failed_batch_category_search_returns_verified_candidates_without_applying(monkeypatch, db_session):
    from app.api import ebay as ebay_api

    user = User(email="category-candidate-search@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, title="RV mattress topper", category_id="56966",
        condition="Used", condition_data={"condition_bucket": "used_good"},
        item_specifics={"Brand": "Example"},
        source_metadata={"taxonomy_tree_id": "0", "taxonomy_marketplace": "EBAY_US"},
    )
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-category-search", user_id=user.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(
        batch_id=batch.id, user_id=user.id, listing_id=listing.id, status="failed",
        result_summary={"results": [{"marketplace": "ebay", "response": {"error_code": "EBAY_25001"}}]},
    ))
    db_session.commit()
    original_category = listing.category_id

    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    async def fake_search(query, account, *, marketplace_id, taxonomy_tree_id, verification_limit):
        assert query == "RV mattress replacement"
        assert account.access_token == "token"
        assert marketplace_id == "EBAY_US" and taxonomy_tree_id == "0"
        assert verification_limit == 5
        return [{
            "category_id": "cat-1", "category_name": "Mattress Toppers",
            "breadcrumb": "Home & Garden > Bedding", "leaf": True,
            "leaf_verified": True, "taxonomy_tree_id": "0",
        }]
    async def fake_aspects(token, category_id, *, marketplace_id, tree_id):
        assert token == "token" and category_id == "cat-1"
        return {"categoryTreeVersion": "current-v", "aspects": [
            {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True}},
            {"localizedAspectName": "Size", "aspectConstraint": {"aspectRequired": True}},
        ]}
    async def fake_condition_policy(token, category_id, *, marketplace_id):
        assert token == "token" and category_id == "cat-1" and marketplace_id == "EBAY_US"
        return {"categoryId": "cat-1", "itemConditionRequired": True, "itemConditions": [
            {"conditionId": 1000, "conditionDescription": "New"},
        ]}

    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "search_ebay_categories", fake_search)
    monkeypatch.setattr(ebay_api, "get_required_item_specifics", fake_aspects)
    monkeypatch.setattr(ebay_api, "get_ebay_item_condition_policies", fake_condition_policy)

    result = await ebay_api.search_failed_ebay_listing_category_candidates(
        batch_id=batch.id, listing_id=listing.id, q="RV mattress replacement",
        db=db_session, current_user=user,
    )
    candidate = result["suggestions"][0]
    assert result["application_available"] is False
    assert result["selection_required"] is True
    assert result["current_category_id"] == original_category
    assert result["current_condition"]["listing_condition"] == "Used"
    assert result["current_condition"]["condition_bucket"] == "used_good"
    assert result["current_item_specifics"] == {"Brand": "Example"}
    assert candidate["required_aspects_status"] == "confirmed"
    assert candidate["required_aspects"] == ["Brand", "Size"]
    assert candidate["missing_required_aspects"] == ["Size"]
    assert candidate["condition_policy"]["condition_required"] is True
    assert candidate["condition_policy"]["supported_conditions"][0]["description"] == "New"
    db_session.refresh(listing)
    assert listing.category_id == original_category


@pytest.mark.anyio
async def test_failed_batch_category_search_is_tenant_scoped(monkeypatch, db_session):
    from fastapi import HTTPException
    from app.api import ebay as ebay_api

    owner = User(email="category-owner@example.com", role="operator")
    other = User(email="category-other@example.com", role="operator")
    db_session.add_all([owner, other])
    db_session.flush()
    listing = Listing(user_id=owner.id, title="Private listing", category_id="1")
    db_session.add(listing)
    db_session.flush()
    batch = MarketplacePublishBatch(id="batch-private-category", user_id=owner.id, selected_count=1, status="failed")
    db_session.add(batch)
    db_session.add(MarketplaceCrosspostJob(batch_id=batch.id, user_id=owner.id, listing_id=listing.id, status="failed"))
    db_session.commit()

    async def must_not_connect(*args, **kwargs): raise AssertionError("unowned batch must not reach provider")
    monkeypatch.setattr(ebay_api, "get_or_refresh_account", must_not_connect)
    with pytest.raises(HTTPException) as exc:
        await ebay_api.search_failed_ebay_listing_category_candidates(
            batch_id=batch.id, listing_id=listing.id, q="private item",
            db=db_session, current_user=other,
        )
    assert exc.value.status_code == 404


async def _async_value(value):
    return value
