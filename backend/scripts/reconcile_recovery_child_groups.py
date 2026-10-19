"""Read-only reconciliation for recovery child-group lineage and overlap."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import MediaRecoveryItemGroup, MediaRecoveryRun


def fingerprint(paths: list[str]) -> str:
    return hashlib.sha256("\n".join(paths).encode()).hexdigest()


def main() -> None:
    with SessionLocal() as db:
        run = db.get(MediaRecoveryRun, 1)
        groups = db.execute(select(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.run_id == run.id)).scalars().all()
        parents = [group for group in groups if group.parent_group_id is None]
        children = [group for group in groups if group.parent_group_id is not None]
        child_by_parent = Counter(group.parent_group_id for group in children)
        ids = Counter(group.recovery_item_id for group in groups)
        signatures = defaultdict(list); path_owners = defaultdict(list)
        for group in children:
            paths = list(group.media_paths_json or [])
            signatures[fingerprint(paths)].append(group.id)
            for path in paths: path_owners[path].append(group.id)
        active = [group for group in children if group.grouping_status != "superseded"]
        active_owner = defaultdict(list)
        for group in active:
            for path in group.media_paths_json or []: active_owner[path].append(group.id)
        # Pair count avoids reporting the same overlap once per individual path.
        pairs = Counter()
        for owners in active_owner.values():
            if len(owners) > 1:
                for left in range(len(owners)):
                    for right in range(left + 1, len(owners)):
                        pairs[(owners[left], owners[right])] += 1
        report = {
            "run_id": run.id,
            "draft_creation_state": run.draft_creation_state,
            "top_level_parents": len(parents),
            "superseded_parents": sum(group.grouping_status == "superseded" for group in parents),
            "pending_parents": sum(group.grouping_status != "superseded" for group in parents),
            "child_groups": len(children),
            "child_statuses": dict(Counter(group.grouping_status for group in children)),
            "children_with_drafts": sum(group.draft_listing_id is not None for group in children),
            "children_without_drafts": sum(group.draft_listing_id is None for group in children),
            "children_per_parent": dict(sorted(child_by_parent.items())),
            "duplicate_recovery_item_ids": {key: value for key, value in ids.items() if value > 1},
            "duplicate_ordered_media_fingerprints": {key: value for key, value in signatures.items() if len(value) > 1},
            "substantially_overlapping_active_group_pairs": {f"{left}:{right}": count for (left, right), count in pairs.items()},
            "media_assigned_to_multiple_active_children": sum(len(owners) > 1 for owners in active_owner.values()),
        }
        print(report)


if __name__ == "__main__":
    main()
