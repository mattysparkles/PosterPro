#!/usr/bin/env python3
"""Create a non-recursive archive of the clean canonical Git source tree."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/opt/apps/posterpro/canonical"))
    parser.add_argument("--output-root", type=Path, default=Path("/mnt/posterprodata/backups/posterpro-source-snapshots"))
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    output_root = args.output_root.resolve()
    if git(repo, "branch", "--show-current") != "main":
        raise SystemExit("source snapshot requires canonical main")
    if git(repo, "status", "--porcelain"):
        raise SystemExit("source snapshot refuses dirty or unidentified work")
    for forbidden in (repo, Path("/opt/apps/posterpro/releases").resolve(), Path("/mnt/posterprodata/app").resolve()):
        if inside(output_root, forbidden) or inside(forbidden, output_root):
            raise SystemExit(f"snapshot output must be separate from protected source/release/storage path: {forbidden}")
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    commit = git(repo, "rev-parse", "HEAD")
    final_dir = output_root / f"{stamp}-{commit[:12]}"
    if final_dir.exists():
        raise SystemExit(f"snapshot already exists: {final_dir}")
    with (output_root / ".snapshot.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another canonical snapshot is running")
        final_dir.mkdir(mode=0o750)
        temporary = final_dir / "source.tar.gz.tmp"
        archive = final_dir / "source.tar.gz"
        try:
            with temporary.open("wb") as out:
                source = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", commit], stdout=subprocess.PIPE)
                assert source.stdout is not None
                compressed = subprocess.run(["gzip", "-n", "-c"], stdin=source.stdout, stdout=out)
                source.stdout.close()
                if source.wait() != 0 or compressed.returncode != 0:
                    raise RuntimeError("git archive or gzip failed")
            os.replace(temporary, archive)
            hasher = hashlib.sha256()
            with archive.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            digest = hasher.hexdigest()
            manifest = {
                "format": "git-archive-tar-gzip",
                "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "branch": "main",
                "commit": commit,
                "archive": archive.name,
                "archive_bytes": archive.stat().st_size,
                "sha256": digest,
                "recursive_copy": False,
            }
            (final_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        except Exception:
            # Keep a failed partial artifact for inspection; never silently
            # remove possible recovery material.
            raise
    print(final_dir)
    print(f"sha256={digest} bytes={archive.stat().st_size} commit={commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
