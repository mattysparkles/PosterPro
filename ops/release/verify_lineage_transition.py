#!/usr/bin/env python3
"""Verify the one-time public-source-lineage handoff against active source."""
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile

from source_tree_fingerprint import fingerprint

if len(sys.argv) != 7:
    raise SystemExit("usage: verify_lineage_transition.py ACTIVE_SOURCE REPO BASE_COMMIT EXPECTED_SHA RELEASE_ID ACTIVE_COMMIT")
active_source, repo_arg, base_commit, expected, release_id, active_commit = sys.argv[1:]
active_source = pathlib.Path(active_source)
repo = pathlib.Path(repo_arg)
proof_path = repo / "ops/release/source-lineage-baseline.json"
proof = json.loads(proof_path.read_text())
if proof.get("legacy_release_id") != release_id or proof.get("legacy_release_commit") != active_commit:
    raise SystemExit("lineage proof does not identify the active production release")
if proof.get("github_lineage_base_commit") != base_commit:
    raise SystemExit("lineage proof base does not match the candidate manifest")
paths = proof.get("comparison_paths")
if not isinstance(paths, list) or not paths:
    raise SystemExit("lineage proof has no source comparison paths")
active_hash = fingerprint(active_source, paths)
if active_hash != expected or expected != proof.get("production_application_source_sha256"):
    raise SystemExit("active production source fingerprint does not match the lineage proof")

with tempfile.TemporaryDirectory(prefix="posterpro-lineage-verify-") as temp:
    archive = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", base_commit], stdout=subprocess.PIPE)
    assert archive.stdout is not None
    with tarfile.open(fileobj=archive.stdout, mode="r|*") as tar:
        tar.extractall(temp, filter="data")
    if archive.wait() != 0:
        raise SystemExit("could not read the public lineage base commit")
    base_hash = fingerprint(pathlib.Path(temp), paths)
if base_hash != expected:
    raise SystemExit("public lineage base source does not match the active production source")
print(expected)
