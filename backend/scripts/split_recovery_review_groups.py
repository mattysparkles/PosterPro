"""Drain bounded recovery-review groups with image sequence analysis."""
from __future__ import annotations

import argparse
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import MediaRecoveryItemGroup, MediaRecoveryRun, User
from app.services.media_recovery import MediaRecoveryService
from app.services.photo_enrichment import PhotoEnrichmentService


def main(limit: int) -> None:
    with SessionLocal() as db:
        run = db.get(MediaRecoveryRun, 1)
        if not run:
            raise RuntimeError("recovery run 1 does not exist")
        user = db.get(User, run.user_id)
        service, analyst = MediaRecoveryService(), PhotoEnrichmentService()
        parents = db.execute(select(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.run_id == run.id, MediaRecoveryItemGroup.grouping_status == "needs_grouping_review", MediaRecoveryItemGroup.parent_group_id.is_(None)).order_by(MediaRecoveryItemGroup.id).limit(limit)).scalars().all()
        result = {"processed": 0, "children": 0, "drafts": 0, "unresolved": 0, "errors": 0}
        for parent in parents:
            result["processed"] += 1
            try:
                contact = service.build_sequence_contact_sheet(list(parent.media_paths_json or []))
                proposal = analyst.analyze_recovery_sequence(contact, image_count=len(parent.media_paths_json or []))
                counts = service.split_review_group(db, run=run, user=user, parent=parent, proposal=proposal)
                for key, value in counts.items():
                    result[key] += value
            except Exception as exc:
                payload = dict(parent.analysis_json or {})
                payload["sequence_split_v1"] = {"status": "error", "error": str(exc)[:400]}
                parent.analysis_json = payload
                db.commit()
                result["errors"] += 1
        print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    main(parser.parse_args().limit)
