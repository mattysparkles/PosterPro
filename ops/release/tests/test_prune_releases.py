#!/usr/bin/env python3
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "prune_releases.py"
NOW = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)


def add_release(root: Path, name: str, kind: str, age_days: float) -> Path:
    path = root / name
    path.mkdir()
    built = NOW - dt.timedelta(days=age_days)
    (path / "release.json").write_text(json.dumps({
        "release_id": name,
        "artifact_kind": kind,
        "production_eligible": kind == "production_release",
        "requires_operator_approval": kind != "production_release",
        "built_at_utc": built.isoformat(),
    }))
    (path / "payload").write_text("fixture\n")
    return path


class ReleaseRetentionTests(unittest.TestCase):
    def test_dry_run_and_apply_keep_active_three_productions_and_referenced_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "releases"
            root.mkdir()
            productions = [add_release(root, f"prod-{i}", "production_release", i) for i in range(4)]
            active = base / "current"
            active.symlink_to(productions[0])
            fresh = add_release(root, "candidate-fresh", "runtime_candidate", 0.1)
            older_candidate = add_release(root, "candidate-older", "runtime_candidate", 3)
            stale_candidate = add_release(root, "candidate-stale", "runtime_candidate", 9)
            referenced = add_release(root, "candidate-referenced", "runtime_candidate", 10)
            (base / "external-reference").symlink_to(referenced)
            unknown = root / "unknown-artifact"
            unknown.mkdir()
            (unknown / "payload").write_text("preserve\n")
            env = dict(os.environ, POSTERPRO_RETENTION_LOCK=str(base / "retention.lock"))
            command = [sys.executable, str(SCRIPT), "--root", str(root), "--active-link", str(active),
                       "--now", NOW.isoformat(), "--effective-after", (NOW - dt.timedelta(days=20)).isoformat()]

            dry = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
            self.assertIn("retention_mode=dry-run", dry.stdout)
            self.assertTrue(productions[3].exists())
            self.assertTrue(stale_candidate.exists())

            done = subprocess.run(command + ["--apply"], env=env, capture_output=True, text=True, check=True)
            self.assertIn("retention_mode=apply", done.stdout)
            self.assertTrue(productions[0].exists())
            self.assertTrue(productions[1].exists())
            self.assertTrue(productions[2].exists())
            self.assertFalse(productions[3].exists())
            self.assertTrue(fresh.exists())
            self.assertFalse(older_candidate.exists())
            self.assertFalse(stale_candidate.exists())
            self.assertTrue(referenced.exists())
            self.assertTrue(unknown.exists())


if __name__ == "__main__":
    unittest.main()
