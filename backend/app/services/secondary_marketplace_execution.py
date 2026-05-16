from __future__ import annotations

from datetime import datetime, timedelta, UTC
from typing import Any

from app.models.models import Listing
from app.services.automation_bridge import submit_bridge_job
from app.services.marketplace_field_mapper import build_marketplace_payload


def _operator_checklist(marketplace: str, execution_mode: str) -> list[str]:
    base_checklists = {
        "etsy": [
            "Verify the Etsy shop, quantity, and fulfillment settings match the product being listed.",
            "Confirm handmade, vintage, or craft-supply attributes before publishing because Etsy requires tighter catalog detail.",
            "Review title, materials, and category-specific attributes so the draft does not ship with incomplete product metadata.",
        ],
        "facebook": [
            "Confirm the Facebook profile or page is the correct seller identity before proceeding.",
            "Review meetup, pickup, or shipping scope details because Facebook listing flow varies by category and local delivery options.",
            "Validate the photo order and condition details before final submission in Marketplace.",
        ],
        "mercari": [
            "Confirm Mercari shipping responsibility, package size, and seller account before submitting.",
            "Review brand, condition, and category hints because Mercari buyer trust depends heavily on those fields being accurate.",
            "Check the final price against Mercari fees and shipping assumptions before posting.",
        ],
        "poshmark": [
            "Confirm the correct Poshmark closet is active before continuing.",
            "Review brand, size, and condition because those fields drive discoverability on Poshmark.",
            "Verify the pricing strategy is aligned with expected offer and closet-sharing behavior.",
        ],
        "depop": [
            "Confirm the Depop shop identity and style tags before publishing.",
            "Review title, brand, condition, and hashtag coverage so discovery does not depend on improvised edits later.",
            "Check the final price and shipping assumptions against the shop's usual policy.",
        ],
        "whatnot": [
            "Confirm whether this draft is intended for a live sale, buy-it-now flow, or pre-show inventory prep.",
            "Review starting bid, quantity, and condition because live-selling mistakes are hard to correct once inventory is staged.",
            "Validate the seller handle and show workflow notes before pushing the item into Whatnot operations.",
        ],
        "vinted": [
            "Confirm the Vinted closet, size, and brand fields before publishing.",
            "Review condition and shipping expectations because Vinted flows can vary by region and package profile.",
            "Check final price and photo order before the manual or assisted handoff is completed.",
        ],
    }
    generic = [
        "Confirm the target marketplace account is the correct seller identity.",
        "Review price, shipping, and required attributes before publishing.",
        "Verify any marketplace-specific fields that are not fully represented in the core listing record.",
    ]
    checklist = base_checklists.get(marketplace, generic)
    if execution_mode == "manual_only":
        return [
            f"Open {marketplace.capitalize()} manually and use PosterPro as the source of truth for the draft.",
            *checklist[1:],
        ]
    return checklist


def _renewal_plan(listing: Listing, marketplace: str) -> dict[str, Any] | None:
    data = listing.marketplace_data or {}
    channels = data.get("channels") or {}
    channel = channels.get(marketplace) or {}
    renewal_mode = str(channel.get("renewal_mode") or "manual").strip().lower()
    if renewal_mode == "manual":
        return {"mode": "manual", "next_due_at": None}
    days = 1 if renewal_mode == "daily" else 3
    return {
        "mode": renewal_mode,
        "next_due_at": (datetime.now(UTC) + timedelta(days=days)).isoformat(),
    }


def execute_secondary_marketplace_path(*, listing: Listing, marketplace: str, execution_mode: str) -> dict[str, Any]:
    payload = build_marketplace_payload(listing, marketplace)
    shipping = payload.get("shipping") or {}
    renewal_plan = _renewal_plan(listing, marketplace)

    if execution_mode == "provider_assist":
        bridge_payload = {
            "marketplace": marketplace,
            "listing_id": listing.id,
            "payload": payload,
            "renewal_plan": renewal_plan,
        }
        bridge_result = submit_bridge_job(
            job_type="crosspost",
            execution_mode=execution_mode,
            payload=bridge_payload,
        )
        return {
            "status": "PROVIDER_PACKET_READY",
            "execution_mode": execution_mode,
            "provider_packet": {
                "marketplace": marketplace,
                "listing_id": listing.id,
                "payload": payload,
            },
            "bridge_submission": bridge_result,
            "operator_checklist": _operator_checklist(marketplace, execution_mode),
            "renewal_plan": renewal_plan,
        }

    if execution_mode == "browser_assist":
        bridge_payload = {
            "marketplace": marketplace,
            "listing_id": listing.id,
            "payload": payload,
            "shipping_scope": (((listing.marketplace_data or {}).get("channels") or {}).get(marketplace) or {}).get("shipping_scope"),
            "renewal_plan": renewal_plan,
        }
        bridge_result = submit_bridge_job(
            job_type="crosspost",
            execution_mode=execution_mode,
            payload=bridge_payload,
        )
        return {
            "status": "BROWSER_AUTOMATION_READY",
            "execution_mode": execution_mode,
            "browser_handoff": {
                "marketplace": marketplace,
                "listing_id": listing.id,
                "payload": payload,
                "shipping_scope": (((listing.marketplace_data or {}).get("channels") or {}).get(marketplace) or {}).get("shipping_scope"),
            },
            "bridge_submission": bridge_result,
            "operator_checklist": _operator_checklist(marketplace, execution_mode),
            "shipping_summary": shipping,
            "renewal_plan": renewal_plan,
        }

    return {
        "status": "MANUAL_HANDOFF_READY",
        "execution_mode": "manual_only",
        "manual_handoff": {
            "marketplace": marketplace,
            "listing_id": listing.id,
            "payload": payload,
        },
        "operator_checklist": [
            *_operator_checklist(marketplace, "manual_only"),
            "Mark the resulting listing ID back into PosterPro after the manual step is completed.",
        ],
        "renewal_plan": renewal_plan,
    }
