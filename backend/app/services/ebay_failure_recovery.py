"""Read-only recovery preview for a failed eBay publish batch.

This deliberately never creates an offer, modifies a listing, or queues a
retry.  It turns the provider responses retained on durable cross-post jobs
into an operator-reviewable recovery plan and protects ambiguous 25001 calls
from being replayed as duplicate listings.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Listing, MarketplaceCrosspostJob, MarketplacePublishBatch


def build_ebay_failed_batch_recovery_preview(
    db: Session, *, batch_id: str, user_id: int
) -> dict[str, Any] | None:
    batch = db.get(MarketplacePublishBatch, batch_id)
    if batch is None or batch.user_id != user_id:
        return None
    jobs = db.execute(
        select(MarketplaceCrosspostJob)
        .where(
            MarketplaceCrosspostJob.batch_id == batch_id,
            MarketplaceCrosspostJob.user_id == user_id,
        )
        .order_by(MarketplaceCrosspostJob.id)
    ).scalars().all()
    listing_ids = [job.listing_id for job in jobs]
    listings = (
        db.execute(
            select(Listing).where(
                Listing.id.in_(listing_ids),
                Listing.user_id == user_id,
            )
        ).scalars().all()
        if listing_ids
        else []
    )
    listing_by_id = {listing.id: listing for listing in listings}
    rows = [_row(job, listing_by_id.get(job.listing_id)) for job in jobs]
    return {
        "operation": "read_only_ebay_failed_batch_recovery_preview",
        "batch_id": batch.id,
        "batch_status": batch.status,
        "selected_count": batch.selected_count,
        "row_count": len(rows),
        "rows": rows,
        "approval_required": True,
        "execution_available": False,
        "execution_blocker": "No retry is available from this preview. Reconcile unknown remote outcomes and correct each recorded provider requirement before requesting an approval-gated retry batch.",
    }


def _row(job: MarketplaceCrosspostJob, listing: Listing | None) -> dict[str, Any]:
    response = _ebay_response(job.result_summary)
    code = str(response.get("error_code") or "EBAY_FAILURE").upper()
    message = str(response.get("provider_short_message") or response.get("error") or job.last_error or "Unknown eBay failure")
    remote_outcome = str(response.get("remote_outcome") or "UNKNOWN").upper()
    required_fields = _required_fields(response)
    if remote_outcome == "REMOTE_STATE_UNKNOWN" or code == "EBAY_25001":
        classification, action = "REMOTE_OUTCOME_UNKNOWN", "Reconcile exact offer ID, PosterPro SKU, and active remote listing before any retry."
    elif code in {"EBAY_25002", "EBAY_25005"}:
        classification = "REQUIRED_DATA" if required_fields else "CATEGORY_OR_TAXONOMY"
        action = ("Validate and supply the provider-named required item specifics, then rerun current taxonomy preflight."
                  if required_fields else "Resolve a current eBay leaf category and permitted condition/item specifics, then rerun preflight.")
    elif "category is not valid" in message.lower() or "condition is not applicable" in message.lower():
        classification, action = "CATEGORY_CONDITION_OR_PARCEL", "Resolve current category and permitted condition; record a measured package profile before preflight."
    else:
        classification, action = "OTHER_PROVIDER_REJECTION", "Inspect the retained provider response and rerun preflight; do not retry blindly."
    return {
        "job_id": job.id,
        "listing_id": job.listing_id,
        "title": listing.title if listing else None,
        "job_status": job.status,
        "provider_error_code": code,
        "provider_message": message,
        "provider_method": response.get("provider_method"),
        "provider_endpoint": response.get("provider_endpoint"),
        "remote_outcome": remote_outcome,
        "required_fields": required_fields,
        "classification": classification,
        "corrective_action": action,
        "retry_eligible": False,
    }


def _ebay_response(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    for result in summary.get("results") or []:
        if isinstance(result, dict) and str(result.get("marketplace") or "").lower() == "ebay":
            return result.get("response") if isinstance(result.get("response"), dict) else {}
    return {}


def _required_fields(response: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    provider_response = response.get("provider_response")
    raw_errors = provider_response.get("errors") if isinstance(provider_response, dict) else None
    if isinstance(raw_errors, str):
        try:
            raw_errors = json.loads(raw_errors)
        except (TypeError, ValueError):
            raw_errors = []
    errors = raw_errors if isinstance(raw_errors, list) else [raw_errors] if isinstance(raw_errors, dict) else []
    for error in errors:
        if not isinstance(error, dict):
            continue
        for message_key in ("message", "longMessage", "shortMessage"):
            message = str(error.get(message_key) or "").replace("\u00a0", " ")
            match = re.search(r"item specific\s+(.+?)\s+is missing\b", message, flags=re.IGNORECASE)
            if match:
                name = match.group(1).strip(" .:;\t\r\n")
                if name and name not in fields:
                    fields.append(name)
        raw_parameters = error.get("parameters")
        parameters = raw_parameters if isinstance(raw_parameters, list) else [raw_parameters] if isinstance(raw_parameters, dict) else []
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            value = str(parameter.get("value") or "").strip()
            if (
                value
                and len(value) <= 80
                and not re.search(r"\b(missing|add .+ to this listing)\b", value, flags=re.IGNORECASE)
                and value not in fields
                and value not in {"3"}
            ):
                fields.append(value)
    return fields
