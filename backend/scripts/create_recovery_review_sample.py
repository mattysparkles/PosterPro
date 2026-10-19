"""Create a bounded sample of recovery review drafts from unresolved groups."""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import MediaRecoveryItemGroup, MediaRecoveryRun, User
from app.services.media_recovery import MediaRecoveryService


def _review_facts(group: MediaRecoveryItemGroup) -> dict:
    photo_count = len(group.media_paths_json or [])
    title = f"Recovered photographed inventory item requiring identity review"
    return {
        "title": title,
        "product_name": "Recovered photographed inventory item requiring identity review",
        "category": "General resale > Identity review required",
        "specifics": {
            "Recovery SKU": group.recovery_item_id,
            "Group Status": str(group.grouping_status or "needs_grouping_review"),
            "Photo Count": str(photo_count),
        },
        "keywords": ["recovered inventory", "identity review", "photo evidence"],
        "description": (
            f"Review draft created from a bounded album group containing {photo_count} original photo(s). "
            "The item has not been forced into a publish-ready identity; review all attached photos for exact identity, "
            "condition, completeness, measurements, and compatibility before publishing."
        ),
        "included": "Only components visible in the attached original photos",
        "condition": "Used",
        "condition_notes": "Identity, condition, completeness, and compatibility require operator review.",
        "suggested_price": 19.99,
        "quick_sale_price": 14.99,
        "price_range": "$15–$30",
        "pricing_explanation": "Temporary review-draft placeholder pricing pending identity and condition confirmation.",
        "shipping": "Measure and pack before publish; provisional ground-service recommendation.",
        "shipping_weight": "3 lb (estimated)",
        "package_dimensions": {"length": 12, "width": 10, "height": 8},
        "confidence": 0.42,
        "field_confidence": {"grouping": 0.58, "identity": 0.2, "condition": 0.3, "price": 0.2},
        "warnings": ["Identity review required before publish.", "Verify condition, completeness, measurements, and shipping details."],
        "alternatives": [],
    }


def main(limit: int = 25) -> None:
    with SessionLocal() as db:
        run = db.get(MediaRecoveryRun, 1)
        if not run:
            raise RuntimeError("Recovery run 1 was not found")
        user = db.execute(select(User).order_by(User.id)).scalars().first()
        if not user:
            raise RuntimeError("No operator user available")
        previous_state = run.draft_creation_state
        run.draft_creation_state = "validation_sample_only"
        db.commit()
        try:
            groups = db.execute(
                select(MediaRecoveryItemGroup)
                .where(
                    MediaRecoveryItemGroup.run_id == run.id,
                    MediaRecoveryItemGroup.grouping_status == "needs_grouping_review",
                    MediaRecoveryItemGroup.draft_listing_id.is_(None),
                )
                .order_by(MediaRecoveryItemGroup.id)
            ).scalars().all()
            groups = sorted(groups, key=lambda group: (-(len(group.media_paths_json or [])), group.id))[:limit]
            service = MediaRecoveryService()
            created = []
            for group in groups:
                created.append(service.create_draft(db, user=user, group=group, facts=_review_facts(group)))
            run.result_json = {**(run.result_json or {}), "validation_sample_created": [listing.id for listing in created]}
            db.commit()
            print({"run_id": run.id, "draft_creation_state": run.draft_creation_state, "created_count": len(created), "draft_ids": [listing.id for listing in created]})
        finally:
            run = db.get(MediaRecoveryRun, run.id)
            if run and run.draft_creation_state != previous_state:
                run.draft_creation_state = previous_state
                db.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    main(limit=args.limit)
