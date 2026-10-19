from app.models.models import Listing, User
from app.services.ebay_operations_preview import build_shipping_handling_preview
from types import SimpleNamespace
import pytest


def test_preview_keeps_above_threshold_shipping_unchanged_and_blocks_unmeasured_calculated_rate(db_session):
    user = User(email="preview@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        title="Remote widget",
        listing_price=19.99,
        ebay_listing_id="123",
        shipping_profile={"package_weight": None, "package_dimensions": {}, "estimated": False},
    )
    db_session.add(listing)
    db_session.commit()

    preview = build_shipping_handling_preview(
        db_session,
        user_id=user.id,
        policy={"shipping_price_threshold": 25},
        remote_rows=[{
            "listing_price": "19.99", "title": "Remote widget", "handling_time_days": "2",
            "source_identifiers": {"ebay_listing_id": "123"},
            "shipping": {"shipping_type": "Flat", "service": "USPSGroundAdvantage", "free_shipping": True, "provenance": "ebay_trading_active_listing_read"},
        }],
    )

    row = preview["shipping_rows"][0]
    assert row["proposal"]["requested"] == "buyer_paid_calculated_usps_ground_advantage"
    assert row["proposal"]["can_apply_without_measurement"] is False
    assert row["parcel"]["ready"] is False
    assert "Request and record measured packed weight and dimensions." in row["safe_options"]
    assert preview["handling_rows"][0]["current_handling_time_days"] == 2
    assert preview["handling_rows"][0]["proposed_handling_time_days"] == 3


def test_policy_default_preserves_above_threshold_shipping_choice():
    from app.services.shipping_profitability_policy import normalize_shipping_policy

    assert normalize_shipping_policy({})["shipping_at_or_above_threshold_mode"] == "preserve_existing"


def test_policy_normalizes_persisted_operator_workflow_names():
    from app.services.shipping_profitability_policy import normalize_shipping_policy

    policy = normalize_shipping_policy({
        "shipping_currency": "cad",
        "preferred_shipping_carrier": "UPS",
        "preferred_shipping_service": "UPSStandard",
    })
    assert policy["currency"] == "CAD"
    assert policy["preferred_carrier"] == "UPS"
    assert policy["preferred_service"] == "UPSStandard"


def test_calculated_rate_requires_confirmed_packed_measurement_provenance():
    from app.services.shipping_profitability_policy import measured_parcel_readiness

    unconfirmed = measured_parcel_readiness({
        "package_weight": 2.0,
        "package_dimensions": {"length": 12, "width": 10, "height": 6},
    })
    assert unconfirmed["ready"] is False
    assert unconfirmed["unverified_measurement_fields"] == ["package_dimensions", "package_weight"]

    confirmed = measured_parcel_readiness({
        "package_weight": 2.0,
        "package_dimensions": {"length": 12, "width": 10, "height": 6},
        "parcel_measurement_confirmed": True,
        "provenance": {
            "package_weight": "operator_measured_packed_parcel",
            "package_dimensions": "operator_measured_packed_parcel",
        },
    })
    assert confirmed["ready"] is True


def test_preview_exposes_remote_local_identity_drift_before_bulk_revision(db_session):
    user = User(email="preview-drift@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add_all([
        Listing(user_id=user.id, title="Mapped", ebay_listing_id="mapped"),
        Listing(user_id=user.id, title="Local only", ebay_listing_id="local-only"),
    ])
    db_session.commit()

    preview = build_shipping_handling_preview(
        db_session,
        user_id=user.id,
        policy={},
        remote_rows=[{
            "listing_price": "10", "source_identifiers": {"ebay_listing_id": "mapped"},
            "shipping": {},
        }, {
            "listing_price": "12", "source_identifiers": {"ebay_listing_id": "remote-only"},
            "shipping": {},
        }],
    )

    reconciliation = preview["identity_reconciliation"]
    assert reconciliation["remote_active_mapped_count"] == 1
    assert reconciliation["remote_active_unmapped_count"] == 1
    assert reconciliation["local_mapped_not_in_remote_count"] == 1
    assert reconciliation["safe_for_bulk_remote_revision"] is False


def test_preview_separates_proceeds_from_profit_and_exposes_price_evidence(db_session):
    user = User(email="preview-economics@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        title="Timeline widget",
        source_type="google_photos_album",
        listing_price=20,
        purchase_cost=None,
        shipping_cost=None,
        ebay_listing_id="economics-1",
        marketplace_data={"pricing_analysis": {
            "provenance": "fallback_estimate", "recommendation_eligible": False,
            "generated_at": "2026-10-18T12:00:00Z", "sold_comp_count_used": 0,
            "active_comp_count_used": 7,
        }},
    )
    db_session.add(listing)
    db_session.commit()

    preview = build_shipping_handling_preview(
        db_session,
        user_id=user.id,
        policy={},
        remote_rows=[{
            "listing_price": "19.50",
            "source_identifiers": {"ebay_listing_id": "economics-1"},
            "shipping": {"free_shipping": True, "calculated": True},
        }],
    )

    row = preview["shipping_rows"][0]
    economics = row["profitability"]
    assert economics["estimated_net_proceeds_before_shipping_label"] is not None
    assert economics["estimated_profit"] is None
    assert economics["item_cost_known"] is False
    assert economics["seller_shipping_label_cost_known"] is False
    assert economics["profit_estimate_complete"] is False
    assert row["price_evidence"]["recommendation_eligible"] is False
    assert row["price_evidence"]["remote_price_matches_local"] is False
    assert row["price_evidence"]["provenance"] == "fallback_estimate"
    assert row["price_evidence"]["research_date"] == "2026-10-18T12:00:00Z"
    assert row["price_evidence"]["sold_comparable_count"] == 0
    assert row["price_evidence"]["active_comparable_count"] == 7


def test_preview_reads_legacy_price_source_and_rejects_etv_as_research(db_session):
    user = User(email="preview-legacy-etv@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        title="Timeline item with ETV estimate",
        source_type="google_photos_album",
        listing_price=24,
        ebay_listing_id="legacy-etv-1",
        marketplace_data={"pricing_analysis": {
            "price_source": "vine_estimated_tax_value_fallback",
            "listing_price": 24,
            "recommendation_eligible": True,
        }},
    )
    db_session.add(listing)
    db_session.commit()

    preview = build_shipping_handling_preview(
        db_session,
        user_id=user.id,
        policy={},
        remote_rows=[{
            "listing_price": "24.00",
            "source_identifiers": {"ebay_listing_id": "legacy-etv-1"},
            "shipping": {"free_shipping": True, "calculated": True},
        }],
    )

    evidence = preview["shipping_rows"][0]["price_evidence"]
    assert evidence["provenance"] == "vine_estimated_tax_value_fallback"
    assert evidence["evidence_type"] == "vine_estimated_tax_value_fallback"
    assert evidence["recommendation_eligible"] is False


def test_preview_computes_profit_only_with_explicit_item_and_label_costs(db_session):
    user = User(email="preview-known-cost@example.com")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id,
        title="Costed widget",
        listing_price=20,
        purchase_cost=5,
        shipping_cost=4,
        ebay_listing_id="economics-2",
        marketplace_data={"profitability_costs": {
            "seller_shipping_label_cost": 4,
            "packaging_materials": 1.25,
            "promotion_expense": 0,
            "other_transaction_costs": 0,
        }},
    )
    db_session.add(listing)
    db_session.commit()

    preview = build_shipping_handling_preview(
        db_session,
        user_id=user.id,
        policy={},
        remote_rows=[{
            "listing_price": "20",
            "source_identifiers": {"ebay_listing_id": "economics-2"},
            "shipping": {"free_shipping": True, "calculated": True},
        }],
    )

    economics = preview["shipping_rows"][0]["profitability"]
    assert economics["profit_estimate_complete"] is True
    assert economics["estimated_profit"] == round(20 - economics["estimated_ebay_fee"] - 5 - 4 - 1.25, 2)


def test_preview_surfaces_unavailable_exact_inspection_without_guessing_handling(db_session):
    user = User(email="preview-inspection@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add(Listing(user_id=user.id, title="Remote widget", ebay_listing_id="123"))
    db_session.commit()

    preview = build_shipping_handling_preview(
        db_session,
        user_id=user.id,
        policy={},
        remote_rows=[{
            "listing_price": "10", "source_identifiers": {"ebay_listing_id": "123"},
            "shipping": {"provenance": "ebay_trading_active_listing_read"},
            "operation_inspection": {"status": "unavailable", "method": "ebay_trading_get_item", "error": "timeout"},
        }],
    )

    assert preview["shipping_rows"][0]["remote_evidence"]["inspection_status"] == "unavailable"
    assert any("Exact remote listing inspection" in blocker for blocker in preview["shipping_rows"][0]["blockers"])
    handling = preview["handling_rows"][0]
    assert handling["current_handling_time_days"] is None
    assert handling["evidence"]["error"] == "timeout"


def test_preview_preserves_shared_policy_evidence_and_one_day_increment(db_session):
    user = User(email="preview-policy@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add(Listing(user_id=user.id, title="Policy widget", ebay_listing_id="456"))
    db_session.commit()

    preview = build_shipping_handling_preview(
        db_session,
        user_id=user.id,
        policy={},
        remote_rows=[{
            "listing_price": "35", "handling_time_days": 3,
            "source_identifiers": {"ebay_listing_id": "456"},
            "marketplace_handling_values": [0, 1, 2, 5],
            "handling_values_source": "ebay_trading_get_ebay_details",
            "shipping": {"provenance": "ebay_fulfillment_policy_read"},
            "operation_inspection": {
                "status": "confirmed", "method": "ebay_fulfillment_policy_read",
                "handling_source": "ebay_fulfillment_policy_read",
                "fulfillment_policy_id": "policy-1", "fulfillment_policy_name": "Standard",
                "shared_policy_listing_count": 4, "shared_policy_impact": "shared_policy",
            },
        }],
    )

    row = preview["handling_rows"][0]
    assert row["current_handling_time_days"] == 3
    assert row["exact_increment_supported"] is False
    assert row["resulting_handling_time_days"] is None
    assert row["nearest_higher_supported_alternative_days"] == 5
    assert row["requested_additional_business_days"] == 1
    assert row["proposed_handling_time_days"] == 4
    assert row["shared_policy_impact"] == "shared_policy"
    assert row["active_listings_using_policy_in_snapshot"] == 4
    assert row["fulfillment_policy_id"] == "policy-1"
    assert any("unpublished/draft" in blocker for blocker in row["blockers"])


@pytest.mark.anyio
async def test_dashboard_preview_uses_batched_trading_details_and_shared_policy_snapshot(monkeypatch, db_session):
    from app.api import ebay as ebay_api

    user = User(email="preview-route@example.com", role="operator")
    db_session.add(user)
    db_session.flush()
    db_session.add_all([
        Listing(user_id=user.id, title="Low priced", ebay_listing_id="100"),
        Listing(user_id=user.id, title="Higher priced", ebay_listing_id="200"),
    ])
    db_session.commit()
    remote_rows = [
        {
            "listing_price": "20", "title": "Low priced",
            "source_identifiers": {"ebay_listing_id": "100"},
            "raw_offer": {"listingPolicies": {"fulfillmentPolicyId": "shared"}},
            "shipping": {},
        },
        {
            "listing_price": "40", "title": "Higher priced",
            "source_identifiers": {"ebay_listing_id": "200"},
            "raw_offer": {"source": "ebay_trading_api", "item": {"listingId": "200"}},
            "shipping": {"shipping_type": "Flat", "service": "USPSParcel", "free_shipping": False,
                "buyer_pays_shipping": True, "calculated": False, "provenance": "ebay_trading_active_listing_read"},
            "handling_time_days": 4,
            "operation_inspection": {"status": "confirmed", "method": "ebay_trading_get_my_ebay_selling",
                "handling_source": "ebay_trading_get_my_ebay_selling", "inspected_at": "2026-10-18T00:00:00Z"},
        },
    ]
    calls = []

    monkeypatch.setattr(ebay_api, "resolve_user_scope", lambda current_user, user_id: current_user.id)
    async def fake_active(*args, **kwargs): return remote_rows
    async def fake_account(*args, **kwargs): return SimpleNamespace(access_token="token")
    async def fake_policies(*args, **kwargs):
        return {"fulfillment_policies": [{
            "id": "shared", "name": "Shared policy", "handling_time": {"unit": "DAY", "value": 2},
            "shipping_options": [
                {"option_type": "INTERNATIONAL", "cost_type": "FLAT_RATE", "shipping_services": [
                    {"carrier": "UPS", "service": "UPSWorldwideSaver", "free_shipping": False, "buyer_responsible": False},
                ]},
                {"option_type": "DOMESTIC", "cost_type": "CALCULATED", "shipping_services": [
                    {"carrier": "USPS", "service": "USPSParcel", "free_shipping": False, "buyer_responsible": True},
                ]},
            ],
        }]}
    async def fake_get_item(account, item_id):
        calls.append(item_id)
        return {"ack": "Success", "handling_time_days": "4", "shipping": {
            "shipping_type": "Flat", "service": "USPSParcel", "free_shipping": False,
            "buyer_pays_shipping": True, "calculated": False,
            "provenance": "ebay_trading_get_item",
        }, "shipping_policy_id": "shared", "shipping_policy_name": "Shared policy"}

    async def fake_supported(_account):
        return {"values_days": [0, 1, 2, 3, 4, 5, 10, 15, 20], "source": "test", "updated_at": "2026-10-18"}

    monkeypatch.setattr(ebay_api, "get_ebay_trading_active_operations", fake_active)
    monkeypatch.setattr(ebay_api, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_api, "list_business_policies", fake_policies)
    monkeypatch.setattr(ebay_api, "get_ebay_trading_item", fake_get_item)
    monkeypatch.setattr(ebay_api, "get_ebay_supported_handling_times", fake_supported)

    preview = await ebay_api.preview_ebay_shipping_and_handling_operations(db=db_session, current_user=user)

    assert calls == []  # Batch Trading data avoids a per-listing GetItem fan-out.
    assert preview["shipping_rows"][0]["current_shipping"]["source"] == "ebay_fulfillment_policy_read"
    domestic_shipping = preview["shipping_rows"][0]["current_shipping"]
    assert domestic_shipping["shipping_type"] == "CALCULATED"
    assert domestic_shipping["carrier"] == "USPS"
    assert domestic_shipping["carrier_service"] == "USPSParcel"
    assert domestic_shipping["buyer_pays_shipping"] is True
    assert domestic_shipping["available_services"] == [{
        "carrier": "USPS", "service": "USPSParcel", "free_shipping": False, "buyer_responsible": True,
    }]
    by_id = {row["remote_listing_id"]: row for row in preview["handling_rows"]}
    assert by_id["100"]["current_handling_time_days"] == 2
    assert by_id["100"]["exact_increment_supported"] is True
    assert by_id["100"]["resulting_handling_time_days"] == 3
    assert by_id["100"]["active_listings_using_policy_in_snapshot"] == 1
    assert by_id["100"]["shared_policy_impact"] == "single_active_listing_in_snapshot"
    assert by_id["200"]["current_handling_time_days"] == 4
    assert by_id["200"]["proposed_handling_time_days"] == 5
    assert by_id["200"]["exact_increment_supported"] is True
    assert by_id["200"].get("active_listings_using_policy_in_snapshot") is None
    assert by_id["200"]["shared_policy_impact"] == "unknown_from_remote_evidence"
