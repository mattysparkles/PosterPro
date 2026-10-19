"""Persist a non-mutating quality audit for every recovery child group/draft."""
from __future__ import annotations
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.models import Listing, MediaRecoveryItemGroup

DEFAULTS = {19.99, 3.0}

def main() -> None:
    with SessionLocal() as db:
        groups = db.execute(select(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.run_id == 1)).scalars().all()
        audited = 0
        for group in groups:
            if group.parent_group_id is None:
                continue
            listing = db.get(Listing, group.draft_listing_id) if group.draft_listing_id else None
            paths = list(group.media_paths_json or [])
            recovery = ((listing.source_metadata or {}).get("recovery") or {}) if listing else {}
            flags = []
            if len(paths) <= 1: flags.append("one_ambiguous_photo")
            if group.grouping_status == "needs_grouping_review": flags.append("unresolved_grouping")
            if listing and float(listing.listing_price or 0) == 19.99: flags.append("default_price")
            if listing and str((listing.shipping_profile or {}).get("package_weight") or "") == "3 lb (estimated)": flags.append("default_weight")
            if listing and str(listing.title or "").lower().startswith("recovered photographed"): flags.append("generic_title")
            if listing and not recovery.get("photo_level_evidence"): flags.append("not_full_group_evidence_v2")
            payload = dict(group.analysis_json or {})
            payload["quality_audit_v2"] = {"photo_count":len(paths),"parent_group_id":group.parent_group_id,"draft_id":getattr(listing,'id',None),"flags":flags,"draft_quality_gate":"blocked" if flags else "pending_full_evidence"}
            group.analysis_json = payload
            audited += 1
        db.commit()
        print({"audited":audited})

if __name__ == "__main__": main()
