"""Read-only eBay shipping and handling-operation previews.

The preview is intentionally built from a fresh remote listing read.  It does
not queue revisions or change local listing state, and every row says exactly
which evidence is missing before an operator can approve an operation.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Listing, MarketplaceListing
from app.services.ebay_fee_service import calculate_expected_ebay_fee
from app.services.shipping_profitability_policy import measured_parcel_readiness, normalize_shipping_policy


def build_shipping_handling_preview(db: Session, *, user_id: int, remote_rows: list[dict[str, Any]], policy: dict | None) -> dict[str, Any]:
    policy = normalize_shipping_policy(policy)
    remote_by_id = {
        str((row.get("source_identifiers") or {}).get("ebay_listing_id") or "").strip(): row
        for row in remote_rows if isinstance(row, dict)
    }
    remote_by_id = {key: value for key, value in remote_by_id.items() if key}
    # Load every locally mapped eBay listing for this operator, not just the
    # remote intersection.  A bulk live revision must not hide local/remote
    # identity drift merely because its candidate list happens to be small.
    all_local_rows = db.execute(
        select(Listing).where(Listing.user_id == user_id, Listing.ebay_listing_id.is_not(None))
    ).scalars().all()
    local_rows = [row for row in all_local_rows if str(row.ebay_listing_id or "").strip() in remote_by_id]
    local_by_id = {str(row.ebay_listing_id): row for row in local_rows if row.ebay_listing_id}
    local_ids = {str(row.ebay_listing_id).strip() for row in all_local_rows if str(row.ebay_listing_id or "").strip()}

    shipping_rows = []
    handling_rows = []
    for remote_id, remote in remote_by_id.items():
        price = _number(remote.get("listing_price"))
        local = local_by_id.get(remote_id)
        remote_shipping = remote.get("shipping") if isinstance(remote.get("shipping"), dict) else {}
        inspection = remote.get("operation_inspection") if isinstance(remote.get("operation_inspection"), dict) else {}
        inspection_status = str(inspection.get("status") or "not_requested")
        local_profile = local.shipping_profile if local and isinstance(local.shipping_profile, dict) else {}
        profile = {
            **local_profile,
            "package_weight": remote_shipping.get("package_weight") or local_profile.get("package_weight"),
            "package_dimensions": remote_shipping.get("package_dimensions") or local_profile.get("package_dimensions"),
        }
        parcel = measured_parcel_readiness(profile)
        remote_service = remote_shipping.get("service")
        remote_calculated = bool(remote_shipping.get("calculated"))
        remote_free = bool(remote_shipping.get("free_shipping"))
        under_threshold = price is not None and price < float(policy["shipping_price_threshold"])
        shipping_blockers = []
        if local is None:
            shipping_blockers.append("No local listing is mapped to this active remote offer.")
        if not parcel["ready"]:
            shipping_blockers.append("Measured parcel setup is required before calculated Ground Advantage can be configured.")
        if inspection.get("shared_policy_impact") in {None, "unknown_from_trading_item", "unknown_from_trading_snapshot"}:
            shipping_blockers.append("Shared fulfillment-policy impact is unknown from the available remote evidence.")
        if inspection_status == "unavailable":
            shipping_blockers.append("Exact remote listing inspection was unavailable; do not use fallback data for a live revision.")
        fee_listing = copy.copy(local) if local else None
        if fee_listing is not None and price is not None:
            fee_listing.listing_price = price
            fee_listing.sale_price = None
            fee_listing.suggested_price = None
        fee = calculate_expected_ebay_fee(fee_listing) if fee_listing else {"totalExpectedFee": None}
        fees = _number(fee.get("totalExpectedFee"))
        cost = _number(getattr(local, "purchase_cost", None)) if local else None
        # Proceeds are not profit.  We can estimate cash proceeds before the
        # seller's label/material costs, but call it profit only when the
        # required cost basis is complete and explicitly recorded.
        net_before_shipping = round(price - fees, 2) if price is not None and fees is not None else None
        local_data = local.marketplace_data if local and isinstance(local.marketplace_data, dict) else {}
        pricing = local_data.get("pricing_analysis") if isinstance(local_data.get("pricing_analysis"), dict) else {}
        override = local_data.get("marketplace_overrides") if isinstance(local_data.get("marketplace_overrides"), dict) else {}
        ebay_override = override.get("ebay") if isinstance(override.get("ebay"), dict) else {}
        operator_price = (
            local_data.get("manual_price_override") not in (None, "")
            and bool(str(local_data.get("manual_price_override_reason") or "").strip())
        ) or ebay_override.get("price") not in (None, "")
        provenance = str(pricing.get("provenance") or pricing.get("price_source") or "").strip().lower()
        unsupported_provenance = provenance in {
            "vine_estimated_tax_value_fallback", "etv", "category_model", "fallback_estimate",
            "fallback", "default", "temporary", "unresearched", "needs_price_research",
        }
        evidence_supported = operator_price or (not unsupported_provenance and (
            pricing.get("recommendation_eligible") is True or provenance in {
            "sold_comps", "active_comps", "source_msrp", "source_retail", "market_comps",
            "operator", "operator_set", "manual",
            }
        ))
        local_price = _number(getattr(local, "listing_price", None)) if local else None
        cost_details = local_data.get("profitability_costs") if isinstance(local_data.get("profitability_costs"), dict) else {}
        shipping_label_cost = _number(cost_details.get("seller_shipping_label_cost"))
        if shipping_label_cost is None:
            shipping_label_cost = _number(getattr(local, "shipping_cost", None)) if local else None
        packaging_cost = _number(cost_details.get("packaging_materials"))
        promotion_cost = _number(cost_details.get("promotion_expense"))
        other_costs = _number(cost_details.get("other_transaction_costs"))
        complete_cost_basis = all(value is not None for value in (cost, shipping_label_cost, packaging_cost, promotion_cost, other_costs))
        estimated_profit = round(price - fees - cost - shipping_label_cost - packaging_cost - promotion_cost - other_costs, 2) if (
            price is not None and fees is not None and complete_cost_basis
        ) else None
        row = {
            "listing_id": local.id if local else None,
            "remote_listing_id": remote_id,
            "title": remote.get("title") or (local.title if local else None),
            "current_price": price,
            "current_shipping": {
                "buyer_pays_shipping": remote_shipping.get("buyer_pays_shipping"),
                "free_shipping": remote_free,
                "calculated": remote_calculated,
                "carrier_service": remote_service,
                "carrier": remote_shipping.get("carrier"),
                "available_services": remote_shipping.get("available_services") or [],
                "shipping_type": remote_shipping.get("shipping_type"),
                "shipping_cost": remote_shipping.get("shipping_cost"),
                "source": remote_shipping.get("provenance") or "unknown",
            },
            "remote_evidence": {
                "listing_source": ((remote.get("raw_offer") or {}).get("source") if isinstance(remote.get("raw_offer"), dict) else None) or "inventory_offer_read",
                "shipping_source": remote_shipping.get("provenance") or "unknown",
                "handling_source": inspection.get("handling_source") or (remote_shipping.get("provenance") if remote.get("handling_time_days") is not None else None),
                "inspection_status": inspection_status,
                "inspected_at": inspection.get("inspected_at"),
                "inspection_error": inspection.get("error"),
            },
            "parcel": {**parcel, "measurement_provenance": local_profile.get("provenance") if local_profile else None},
            "proposal": {
                "eligible_by_price": under_threshold,
                "requested": "buyer_paid_calculated_usps_ground_advantage" if under_threshold else None,
                "can_apply_without_measurement": False,
                "shared_policy_impact": inspection.get("shared_policy_impact") or "unknown_from_remote_evidence",
                "fulfillment_policy_id": inspection.get("fulfillment_policy_id"),
                "fulfillment_policy_name": inspection.get("fulfillment_policy_name"),
                "active_listings_using_policy_in_snapshot": inspection.get("shared_policy_listing_count"),
            },
            "profitability": {
                "estimated_ebay_fee": fees,
                "estimated_net_proceeds_before_shipping_label": net_before_shipping,
                "estimated_profit": estimated_profit,
                "profit_estimate_complete": complete_cost_basis,
                "item_cost_known": cost is not None,
                "seller_shipping_label_cost_known": shipping_label_cost is not None,
                "packaging_material_cost_known": packaging_cost is not None,
                "promotion_expense_known": promotion_cost is not None,
                "other_transaction_costs_known": other_costs is not None,
                "risk": "UNKNOWN_PARCEL_COST" if not parcel["ready"] else "REQUIRES_DESTINATION_RATE",
            },
            "price_evidence": {
                "provenance": provenance or ("operator_override" if operator_price else "unknown"),
                "research_date": pricing.get("external_research_date") or pricing.get("researched_at") or pricing.get("generated_at") or pricing.get("research_date"),
                "recommendation_eligible": evidence_supported,
                "evidence_type": provenance or ("operator_entered_with_reason" if operator_price else None),
                "sold_comparable_count": pricing.get("sold_comp_count_used", pricing.get("external_sold_comparable_count")),
                "active_comparable_count": pricing.get("active_comp_count_used", pricing.get("external_active_asking_count")),
                "local_approved_price": local_price,
                "remote_price_matches_local": local_price is None or price is None or round(local_price, 2) == round(price, 2),
            },
            "blockers": shipping_blockers,
            "safe_options": _safe_options(parcel, under_threshold),
        }
        if under_threshold:
            shipping_rows.append(row)

        current_handling = _integer(remote.get("handling_time_days"))
        supported_values = remote.get("marketplace_handling_values")
        supported_values = sorted({value for value in (_integer(item) for item in supported_values) if value is not None}) if isinstance(supported_values, list) else None
        proposed_handling = current_handling + 1 if current_handling is not None else None
        exact_increment_supported = proposed_handling in supported_values if proposed_handling is not None and supported_values is not None else None
        alternatives = [value for value in (supported_values or []) if current_handling is not None and value > current_handling]
        nearest_alternative = min(alternatives, key=lambda value: (abs(value - proposed_handling), value < proposed_handling, value)) if alternatives and proposed_handling is not None else None
        handling_blockers = (
            (["Exact remote listing inspection was unavailable; do not infer handling time from fallback data."] if inspection_status == "unavailable" else [])
            + (["Remote handling time was not returned; inspect the exact listing or fulfillment policy before proposing a change."] if current_handling is None else [])
            + (["Marketplace-supported handling values were not retrieved; exact increment support is unknown."] if supported_values is None else [])
            + ([f"The requested result ({proposed_handling} days) is not supported; nearest higher supported alternative is {nearest_alternative} days and requires operator choice."] if exact_increment_supported is False else [])
            + (["The fulfillment policy is shared by other active listings in the snapshot; changing it affects all of them."] if int(inspection.get("shared_policy_listing_count") or 0) > 1 else [])
            + (["Policy reference count covers the active snapshot only; effects on unpublished/draft or otherwise inactive offers remain unknown."] if inspection.get("fulfillment_policy_id") else [])
        )
        handling_rows.append({
            "listing_id": local.id if local else None,
            "remote_listing_id": remote_id,
            "title": row["title"],
            "current_handling_time_days": current_handling,
            "requested_additional_business_days": 1,
            "proposed_handling_time_days": proposed_handling,
            "resulting_handling_time_days": proposed_handling if exact_increment_supported is not False else None,
            "exact_increment_supported": exact_increment_supported,
            "marketplace_supported_values": supported_values,
            "nearest_higher_supported_alternative_days": nearest_alternative if exact_increment_supported is False else None,
            "handling_values_source": remote.get("handling_values_source"),
            "handling_values_updated_at": remote.get("handling_values_updated_at"),
            "shared_policy_impact": inspection.get("shared_policy_impact") or "unknown_from_remote_evidence",
            "fulfillment_policy_id": inspection.get("fulfillment_policy_id"),
            "fulfillment_policy_name": inspection.get("fulfillment_policy_name"),
            "active_listings_using_policy_in_snapshot": inspection.get("shared_policy_listing_count"),
            "evidence": {
                "source": inspection.get("handling_source") or (remote_shipping.get("provenance") if current_handling is not None else None),
                "inspection_status": inspection_status,
                "inspected_at": inspection.get("inspected_at"),
                "error": inspection.get("error"),
            },
            "blockers": handling_blockers,
        })
    return {
        "operation": "read_only_ebay_shipping_handling_preview",
        "remote_active_count": len(remote_by_id),
        "identity_reconciliation": {
            "remote_active_mapped_count": len(local_by_id),
            "remote_active_unmapped_count": len(set(remote_by_id) - set(local_by_id)),
            "local_mapped_count": len(local_ids),
            "local_mapped_not_in_remote_count": len(local_ids - set(remote_by_id)),
            "safe_for_bulk_remote_revision": not bool(set(remote_by_id) - set(local_by_id)),
            "blocker": (
                "One or more active remote offers are not mapped to a local listing; do not bulk revise until identity reconciliation is complete."
                if set(remote_by_id) - set(local_by_id)
                else None
            ),
        },
        "shipping_policy": policy,
        "under_threshold_count": len(shipping_rows),
        "shipping_rows": shipping_rows,
        "handling_rows": handling_rows,
        "approval_required": True,
        "execution_available": False,
        "execution_blocker": "This preview is read-only. A verified offer/policy inspection and explicit operator approval are required before remote revision.",
    }


def _safe_options(parcel: dict[str, Any], under_threshold: bool) -> list[str]:
    if not under_threshold:
        return ["Keep the existing explicit shipping configuration or set an operator default; no threshold change is proposed."]
    if parcel["ready"]:
        return ["Prepare buyer-paid calculated USPS Ground Advantage for operator approval."]
    return [
        "Request and record measured packed weight and dimensions.",
        "Choose an operator-approved supported alternative after reviewing the exact offer and destination constraints.",
        "Prepare temporary listing deactivation for explicit operator approval if the profitability risk cannot be accepted.",
    ]


def _number(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        result = int(value)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None
