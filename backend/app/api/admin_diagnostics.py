"""Small, authenticated platform-admin diagnostics for support triage."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, is_effective_admin
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import Listing, ListingCorrectionJob, MarketplaceListing, MarketplacePublishAttempt, MarketplaceCrosspostJob, User
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.pricing_research_service import compute_listing_quality_summary

router = APIRouter(prefix="/admin", tags=["admin-diagnostics"])


class ListingDiagnosticsRequest(BaseModel):
    listing_ids: list[int] = Field(min_length=1, max_length=100)
    marketplace: str = "ebay"
    run_fresh_preflight: bool = True


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


def _safe_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {"code": issue.get("code"), "message": issue.get("message"), "field": issue.get("field"), "fix_hint": issue.get("fix_hint")}


def _blocker_kind(code: str | None) -> str:
    c = (code or "").upper()
    if "ASPECT" in c: return "ASPECT_REQUIRED"
    if "CATEGORY" in c: return "CATEGORY_INVALID" if "INVALID" in c else "CATEGORY_MISSING"
    if "CONDITION" in c: return "CONDITION_INVALID"
    if "DESCRIPTION" in c: return "DESCRIPTION_MISSING" if "MISSING" in c else "DESCRIPTION_GENERIC"
    if "IMAGE" in c or "PHOTO" in c: return "IMAGE_REQUIRED"
    if "POLICY" in c or "ACCOUNT" in c: return "EBAY_POLICY"
    if "PREFLIGHT" in c: return "PREFLIGHT_ERROR"
    return "OTHER"


@router.post("/listing-diagnostics")
def listing_diagnostics(payload: ListingDiagnosticsRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not is_effective_admin(current_user):
        raise HTTPException(status_code=403, detail="Platform admin access required")
    ids = list(dict.fromkeys(int(i) for i in payload.listing_ids if int(i) > 0))
    if not ids:
        raise HTTPException(status_code=422, detail="At least one valid listing ID is required")
    market = payload.marketplace.strip().lower() or MarketplaceName.ebay.value
    listings = db.execute(select(Listing).where(Listing.id.in_(ids))).scalars().all()
    by_id = {row.id: row for row in listings}
    service = MarketplacePreflightService()
    output = []
    for listing_id in ids:
        listing = by_id.get(listing_id)
        if not listing:
            output.append({"listing_id": listing_id, "status": "NOT_FOUND", "primary_blocker": "NOT_FOUND", "all_blockers": []})
            continue
        jobs = db.execute(select(ListingCorrectionJob).where(ListingCorrectionJob.listing_id == listing.id).order_by(ListingCorrectionJob.created_at.desc()).limit(1)).scalars().all()
        job = jobs[0] if jobs else None
        preflight = service.preflight_listing(db, listing, market) if payload.run_fresh_preflight else None
        if preflight is not None:
            service.cache_preflight_summary(db, listing, preflight)
        cached = ((listing.marketplace_data or {}).get("marketplace_preflight") or {}).get("by_marketplace", {}).get(market, {})
        blockers = preflight.get("blockers", []) if preflight else cached.get("blockers", [])
        safe_blockers = [_safe_issue(i) for i in blockers if isinstance(i, dict)]
        quality = compute_listing_quality_summary(listing, pricing_analysis=((listing.marketplace_data or {}).get("pricing_analysis") or {}))
        attempts = db.execute(select(MarketplacePublishAttempt).where(MarketplacePublishAttempt.listing_id == listing.id).order_by(MarketplacePublishAttempt.created_at.desc()).limit(1)).scalars().all()
        attempt = attempts[0] if attempts else None
        external = db.execute(select(MarketplaceListing).where(MarketplaceListing.listing_id == listing.id, MarketplaceListing.marketplace == market).order_by(MarketplaceListing.id.desc()).limit(1)).scalars().first()
        crosspost_jobs = db.execute(select(MarketplaceCrosspostJob).where(MarketplaceCrosspostJob.listing_id == listing.id).order_by(MarketplaceCrosspostJob.created_at.desc()).limit(20)).scalars().all()
        output.append({
            "listing_id": listing.id, "title": listing.title, "user_id": listing.user_id, "source_type": listing.source_type,
            "asin": (listing.source_metadata or {}).get("asin") or (listing.source_metadata or {}).get("amazon_product_facts", {}).get("asin"),
            "status": getattr(listing.status, "value", listing.status), "needs_review": listing.needs_review,
            "correction_status": (listing.source_metadata or {}).get("correction_status"),
            "latest_correction_job": ({"id": job.id, "status": job.status, "priority": job.priority, "requested_fields": job.fields, "operator_note": job.operator_note, "attempts": job.attempt_count, "created_at": _iso(job.created_at), "claimed_at": _iso(job.claimed_at), "started_at": _iso(job.started_at), "completed_at": _iso(job.completed_at), "failure_reason": job.failure_reason, "field_results": (job.result or {}).get("field_results", []), "material_delta": job.material_delta} if job else None),
            "condition": {"value": listing.condition, "data": listing.condition_data or {}},
            "listing_state": {"processing_state": listing.processing_state, "processing_stage": listing.processing_stage, "price": listing.listing_price, "suggested_price": listing.suggested_price, "quantity": listing.quantity, "sold_at": _iso(listing.sold_at), "ebay_publish_status": getattr(listing.ebay_publish_status, "value", listing.ebay_publish_status), "ebay_listing_id": listing.ebay_listing_id},
            "category": {"id": listing.category_id, "name": listing.category_suggestion, "path": (listing.source_metadata or {}).get("category_path"), "taxonomy_tree_id": (listing.source_metadata or {}).get("taxonomy_tree_id"), "provenance": (listing.source_metadata or {}).get("category_provenance"), "leaf_verified": (listing.source_metadata or {}).get("leaf_verified"), "publishable": (listing.source_metadata or {}).get("publishable")},
            "description": {"character_count": len(listing.description or ""), "quality": quality.get("status")},
            "images": {"total": len(listing.image_urls or []), "eligible": len(listing.listing_images or []), "summary": (preflight or {}).get("image_summary")},
            "aspects": {"populated": listing.item_specifics or {}, "missing_required": [b.get("field") for b in blockers if isinstance(b, dict) and b.get("code") == "EBAY_REQUIRED_ASPECT_MISSING"]},
            "readiness": (preflight or {}).get("readiness_summary"), "ebay_preflight": {"status": (preflight or cached).get("status"), "blocker_codes": [b.get("code") for b in safe_blockers], "blocker_messages": [b.get("message") for b in safe_blockers], "blockers": safe_blockers, "timestamp": _iso((preflight or cached).get("last_checked_at"))},
            "marketplace_listing": {"exists": bool(external), "marketplace": market, "external_listing_id": getattr(external, "marketplace_listing_id", None), "status": getattr(external, "status", None)} if external else {"exists": False, "marketplace": market},
            "latest_publish_job": ({"id": attempt.job_id, "status": attempt.marketplace_status or attempt.preflight_status, "failure_reason": attempt.raw_error or (attempt.translated_error or {}).get("message")} if attempt else None),
            "marketplace_jobs": [{"id": j.id, "status": j.status, "priority": j.priority, "attempts": j.attempt_count, "target_marketplaces": j.target_marketplaces, "requested_mode": j.requested_mode, "created_at": _iso(j.created_at), "last_error": j.last_error, "result_summary": j.result_summary, "execution_plan": j.execution_plan} for j in crosspost_jobs],
            "primary_blocker": _blocker_kind(safe_blockers[0].get("code")) if safe_blockers else None,
            "all_blockers": [{**issue, "kind": _blocker_kind(issue.get("code"))} for issue in safe_blockers],
        })
    db.commit()
    return {"marketplace": market, "count": len(output), "items": output}
