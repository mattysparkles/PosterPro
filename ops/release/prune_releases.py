#!/usr/bin/env python3
"""Conservative retention for immutable PosterPro releases.

Dry-run by default. With --apply, keep the active release and the three newest
eligible production releases. Keep the two newest candidates until they are at
least one day old; candidates older than seven days are also eligible. Any
release referenced by a top-level symlink or a live process is always kept.
Unknown or malformed artifacts are never removed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def release_references(release_root: Path, active_link: Path) -> set[Path]:
    protected: set[Path] = set()
    links = [active_link]
    # The active/legacy links are the only supported external release links.
    app_root = release_root.parent
    try:
        links.extend(p for p in app_root.iterdir() if p.is_symlink())
    except OSError:
        pass
    for link in links:
        try:
            resolved = link.resolve(strict=True)
            if resolved == release_root or release_root in resolved.parents:
                protected.add(resolved)
        except (OSError, RuntimeError):
            continue

    # Release roots sometimes share a versioned virtual environment by symlink.
    # Inspect only each artifact's immediate entries; do not walk node_modules.
    try:
        for artifact in release_root.iterdir():
            if not artifact.is_dir() or artifact.is_symlink():
                continue
            for child in artifact.iterdir():
                if not child.is_symlink():
                    continue
                try:
                    resolved = child.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if resolved == release_root or release_root in resolved.parents:
                    protected.add(resolved)
    except OSError:
        pass

    # Preserve artifacts named directly by an installed service configuration.
    units = Path("/etc/systemd/system")
    try:
        config_files = list(units.glob("*.service")) + list(units.glob("*.service.d/*.conf"))
        for config in config_files:
            try:
                content = config.read_text(errors="replace")
            except OSError:
                continue
            for artifact in release_root.iterdir():
                if artifact.is_dir() and str(artifact) in content:
                    protected.add(artifact.resolve())
    except OSError:
        pass

    proc_root = Path("/proc")
    try:
        for proc in proc_root.iterdir():
            if not proc.name.isdigit():
                continue
            for name in ("cwd", "exe"):
                try:
                    resolved = (proc / name).resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if resolved == release_root or release_root in resolved.parents:
                    protected.add(resolved)
    except OSError:
        pass
    return protected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("POSTERPRO_RELEASE_ROOT", "/opt/apps/posterpro/releases")))
    parser.add_argument("--active-link", type=Path, default=Path(os.environ.get("POSTERPRO_ACTIVE_RELEASE_LINK", "/opt/apps/posterpro/current")))
    parser.add_argument("--apply", action="store_true", help="remove only eligible, verified release artifacts")
    parser.add_argument("--now", help="UTC ISO timestamp, used for deterministic tests")
    parser.add_argument("--effective-after", help="UTC policy start; older artifacts are preserved pending explicit review")
    args = parser.parse_args()

    root = args.root.resolve()
    active_link = args.active_link
    if not root.is_dir() or not active_link.is_symlink():
        raise SystemExit("release root or active release symlink is missing; refusing retention")
    active = active_link.resolve(strict=True)
    if active.parent != root or not (active / "release.json").is_file():
        raise SystemExit("active release is not a manifested direct child of the release root")
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    if now is None:
        raise SystemExit("--now must be an ISO UTC timestamp")
    cutoff_path = Path(os.environ.get("POSTERPRO_RETENTION_CUTOFF_FILE", "/var/lib/posterpro-release-retention/effective-after-utc"))
    cutoff_value = args.effective_after
    if cutoff_value is None and cutoff_path.is_file():
        cutoff_value = cutoff_path.read_text().strip()
    cutoff = parse_time(cutoff_value)
    if cutoff is None:
        print(f"retention disabled: valid effective-after marker required at {cutoff_path}")
        return 0

    lock_path = Path(os.environ.get("POSTERPRO_RETENTION_LOCK", "/run/lock/posterpro-release-retention.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock_file:
        import fcntl
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another release retention run is active")

        protected = release_references(root, active_link)
        releases: list[tuple[Path, dict, dt.datetime]] = []
        unknown: list[Path] = []
        for path in root.iterdir():
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                manifest = json.loads((path / "release.json").read_text())
            except (OSError, ValueError):
                unknown.append(path)
                continue
            built_at = parse_time(manifest.get("built_at_utc"))
            if not built_at or manifest.get("release_id") != path.name:
                unknown.append(path)
                continue
            releases.append((path, manifest, built_at))

        productions = sorted(
            (row for row in releases if row[1].get("artifact_kind") == "production_release"
             and row[1].get("production_eligible") is True
             and row[1].get("requires_operator_approval") is False
             and row[2] >= cutoff),
            key=lambda row: row[2], reverse=True,
        )
        keep = {row[0] for row in productions[:3]}
        keep.add(active)
        candidates = sorted(
            (row for row in releases if row[1].get("artifact_kind") == "runtime_candidate"),
            key=lambda row: row[2], reverse=True,
        )
        keep.update(row[0] for row in candidates[:2] if now - row[2] < dt.timedelta(days=1))

        removals: list[tuple[Path, str]] = []
        for path, manifest, built_at in releases:
            kind = manifest.get("artifact_kind")
            age = now - built_at
            reason = None
            if built_at < cutoff:
                print(f"KEEP pre-policy {path}")
                continue
            if kind == "production_release" and manifest.get("production_eligible") is True and manifest.get("requires_operator_approval") is False:
                if path not in keep:
                    reason = "older production release; three eligible releases retained"
            elif kind == "runtime_candidate":
                if age >= dt.timedelta(days=7):
                    reason = "runtime candidate exceeded seven-day maximum age"
                elif path not in keep and age >= dt.timedelta(days=1):
                    reason = "older unpromoted runtime candidate"
            elif kind in {"integration_candidate", "release_candidate"}:
                if age >= dt.timedelta(days=7):
                    reason = "unpromoted candidate exceeded seven-day maximum age"
            else:
                continue
            if not reason:
                continue
            if path == active or path in keep:
                continue
            if any(ref == path or path in ref.parents for ref in protected):
                print(f"KEEP referenced {path}")
                continue
            removals.append((path, reason))

        for path, reason in removals:
            print(f"{'REMOVE' if args.apply else 'WOULD_REMOVE'} {path} reason={reason}")
        for path in unknown:
            print(f"KEEP unidentified {path}")
        print(f"retention_mode={'apply' if args.apply else 'dry-run'} effective_after={cutoff.isoformat()} removable_artifacts={len(removals)} protected_references={len(protected)}")
        if args.apply:
            for path, _ in removals:
                if path.exists() and not path.is_symlink():
                    shutil.rmtree(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
