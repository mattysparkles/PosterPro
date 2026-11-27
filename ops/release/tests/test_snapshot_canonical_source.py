#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "snapshot_canonical_source.py"


def run(repo: Path, output: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), "--output-root", str(output)],
                          capture_output=True, text=True)


class CanonicalSnapshotTests(unittest.TestCase):
    def test_refuses_dirty_and_overlapping_paths_then_archives_git_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / "canonical"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Snapshot Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "snapshot@example.invalid"], check=True)
            (repo / "tracked.txt").write_text("tracked\n")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
            (repo / "untracked.txt").write_text("must not be lost\n")
            dirty = run(repo, base / "external")
            self.assertNotEqual(dirty.returncode, 0)
            self.assertIn("dirty", dirty.stderr)
            (repo / "untracked.txt").unlink()

            overlap = run(repo, repo / "ops" / "snapshots")
            self.assertNotEqual(overlap.returncode, 0)
            ancestor = run(repo, base)
            self.assertNotEqual(ancestor.returncode, 0)

            output = base / "external"
            result = run(repo, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            directory = Path(result.stdout.splitlines()[0])
            manifest = json.loads((directory / "manifest.json").read_text())
            self.assertFalse(manifest["recursive_copy"])
            with tarfile.open(directory / "source.tar.gz", "r:gz") as archive:
                names = archive.getnames()
            self.assertIn("tracked.txt", names)
            self.assertNotIn("source.tar.gz", names)
            self.assertNotIn("untracked.txt", names)


if __name__ == "__main__":
    unittest.main()
