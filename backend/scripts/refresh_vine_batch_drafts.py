"""Refresh a Vine batch from Amazon product facts without publishing anything.

Run this with the running backend's DATABASE_URL.  It is safe to resume: Vine
rows retain their listing relationship and ``create_listing_drafts`` updates
the same drafts rather than creating a second one.
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import VineImportBatch, VineImportItem
from app.services.vine_import_service import VineImportService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", type=int, required=True)
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument("--repair-images", action="store_true")
    parser.add_argument("--from-stored-facts", action="store_true")
    args = parser.parse_args()
    service = VineImportService()
    with SessionLocal() as db:
        batch = db.get(VineImportBatch, args.batch_id)
        if batch is None:
            raise SystemExit(f"Vine batch {args.batch_id} was not found")
        if args.from_stored_facts:
            print({"batch_id": batch.id, "completed": True, **service.refresh_batch_drafts_from_stored_amazon_facts(db, batch=batch)}, flush=True)
            return
        items = db.execute(
            select(VineImportItem)
            .where(VineImportItem.batch_id == batch.id)
            .order_by(VineImportItem.shipped_date.desc().nullslast(), VineImportItem.order_date.desc().nullslast(), VineImportItem.id.desc())
        ).scalars().all()
        totals = {"created": 0, "updated": 0, "skipped": 0, "image_updated": 0, "image_failed": 0}
        size = max(1, args.chunk_size)
        for offset in range(0, len(items), size):
            chunk = items[offset : offset + size]
            result = service.create_listing_drafts(
                db,
                batch=batch,
                item_ids=[item.id for item in chunk],
                include_cancelled=False,
                allow_drafts_without_media=True,
            )
            for key in ("created", "updated", "skipped"):
                totals[key] += int(result.get(key) or 0)
            if args.repair_images:
                listing_ids = [item.listing_id for item in chunk if item.listing_id]
                repair = service.repair_vine_listing_images(
                    db,
                    user_id=batch.user_id,
                    batch_id=batch.id,
                    listing_ids=listing_ids,
                    include_archived=False,
                    force_refresh=False,
                    use_bridge_session=True,
                    only_missing_images=True,
                )
                totals["image_updated"] += int(repair.get("updated") or 0)
                totals["image_failed"] += int(repair.get("bridge_failed") or 0)
            print({"batch_id": batch.id, "offset": offset, "processed": len(chunk), "totals": totals}, flush=True)
        print({"batch_id": batch.id, "completed": True, "totals": totals, "publication_actions": 0}, flush=True)


if __name__ == "__main__":
    main()
