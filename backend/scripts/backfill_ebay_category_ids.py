"""Backfill eBay taxonomy IDs for reviewable listings with only category suggestions.

This maintenance pass is intentionally bounded and idempotent:
- it only touches listings from the live recovery/Vine sources
- it skips rows that already have a numeric marketplace category ID
- it uses the existing preflight repair flow so the resolved taxonomy ID is
  captured in the same place the publish path expects
"""
from __future__ import annotations

from sqlalchemy import and_, or_, select

from app.core.database import SessionLocal
from app.models.models import Listing
from app.services.marketplace_preflight import MarketplacePreflightService


def _needs_category_resolution(listing: Listing) -> bool:
    category_id = str(listing.category_id or "").strip()
    return not category_id.isdigit() and bool(str(listing.category_suggestion or "").strip())


def main(limit: int = 25) -> None:
    service = MarketplacePreflightService()
    touched: list[int] = []
    resolved: list[int] = []
    skipped: list[int] = []

    with SessionLocal() as db:
        rows = db.execute(
            select(Listing)
            .where(
                and_(
                    Listing.source_type.in_(["amazon_vine", "media_inventory_recovery"]),
                    Listing.needs_review.is_(True),
                    or_(Listing.category_id.is_(None), Listing.category_id == ""),
                )
            )
            .order_by(Listing.updated_at.desc(), Listing.id.desc())
            .limit(limit)
        ).scalars().all()

        for listing in rows:
            touched.append(listing.id)
            if not _needs_category_resolution(listing):
                skipped.append(listing.id)
                continue
            try:
                result = service.apply_repair_actions(
                    db,
                    listing,
                    apply_category_suggestion=True,
                    validate_images=False,
                )
            except Exception as exc:  # noqa: BLE001
                skipped.append(listing.id)
                print({"listing_id": listing.id, "status": "failed", "error": type(exc).__name__, "message": str(exc)})
                continue
            db.refresh(listing)
            if str(listing.category_id or "").strip().isdigit():
                resolved.append(listing.id)
                print({"listing_id": listing.id, "status": "resolved", "category_id": listing.category_id, "result_status": result.get("status_after")})
            else:
                skipped.append(listing.id)
                print({"listing_id": listing.id, "status": "unchanged", "category_id": listing.category_id, "result_status": result.get("status_after")})

        db.commit()

    print(
        {
            "touched_count": len(touched),
            "resolved_count": len(resolved),
            "skipped_count": len(skipped),
            "resolved_ids": resolved,
            "skipped_ids": skipped,
        }
    )


if __name__ == "__main__":
    main()
