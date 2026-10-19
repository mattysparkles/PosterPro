"""Consolidate sibling recovery child groups that resolve to one item."""
from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import MediaRecoveryItemGroup, MediaRecoveryRun
from app.services.media_recovery import consolidate_boundary_parents_by_evidence, consolidate_sibling_children_by_evidence


def main(run_id: int = 1) -> None:
    with SessionLocal() as db:
        run = db.get(MediaRecoveryRun, run_id)
        if not run:
            raise RuntimeError(f"Recovery run {run_id} not found")
        parents = db.execute(
            select(MediaRecoveryItemGroup)
            .where(MediaRecoveryItemGroup.run_id == run.id, MediaRecoveryItemGroup.parent_group_id.is_(None))
            .order_by(MediaRecoveryItemGroup.id)
        ).scalars().all()
        report = {"run_id": run.id, "parents": len(parents), "merged_clusters": 0, "merged_groups": 0}
        for parent in parents:
            result = consolidate_sibling_children_by_evidence(db, parent=parent)
            report["merged_clusters"] += int(result.get("merged_clusters") or 0)
            report["merged_groups"] += int(result.get("merged_groups") or 0)
            db.commit()
        boundary_result = consolidate_boundary_parents_by_evidence(db, run=run)
        report["boundary_merged_clusters"] = int(boundary_result.get("merged_clusters") or 0)
        report["boundary_merged_groups"] = int(boundary_result.get("merged_groups") or 0)
        db.commit()
        print(report)


if __name__ == "__main__":
    main()
