#!/usr/bin/env bash
set -euo pipefail

# Promote an immutable, already tested runtime candidate by creating a new
# release artifact. The candidate itself is never edited. This operation
# requires current main, intact checksums/evidence, a passing runtime
# preflight, and a live schema ledger matching the candidate migrations.
candidate="${1:-}"
release_root="${POSTERPRO_RELEASE_ROOT:-/opt/apps/posterpro/releases}"
canonical_repo="${POSTERPRO_CANONICAL_REPO:-/opt/apps/posterpro/canonical}"
active_link="${POSTERPRO_ACTIVE_RELEASE_LINK:-/opt/apps/posterpro/current}"
lock_path="${POSTERPRO_DEPLOY_LOCK:-/run/lock/posterpro-deploy.lock}"
env_file="${POSTERPRO_ENV_FILE:-/opt/apps/posterpro/repo/backend/.env}"

if [[ -z "$candidate" || "$candidate" != "$release_root/"* || ! -d "$candidate" ]]; then
  echo "Usage: promote_runtime_candidate.sh /opt/apps/posterpro/releases/<runtime_candidate>" >&2
  exit 2
fi
if [[ ! -L "$active_link" || ! -f "$env_file" ]]; then
  echo "A versioned active release and production environment file are required." >&2
  exit 2
fi
mkdir -p "$(dirname "$lock_path")"
exec 9>"$lock_path"
if ! flock -n 9; then echo "Another PosterPro deployment is active." >&2; exit 3; fi

python3 - "$candidate" "$canonical_repo" "$active_link" <<'PY'
import json, pathlib, subprocess, sys
candidate, repo, active = map(pathlib.Path, sys.argv[1:])
manifest = json.loads((candidate / "release.json").read_text())
if manifest.get("artifact_kind") != "runtime_candidate" or manifest.get("production_eligible") is not False:
    raise SystemExit("Input is not an unpromoted runtime_candidate.")
if manifest.get("canonical_branch") != "main": raise SystemExit("Candidate is not from canonical main.")
commit = manifest.get("git_commit", "")
if len(commit) != 40: raise SystemExit("Candidate commit identity is invalid.")
if subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip():
    raise SystemExit("Canonical source is dirty; refusing promotion.")
branch = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True, check=True).stdout.strip()
if branch != "main": raise SystemExit("Canonical checkout is not on main.")
head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
origin = subprocess.run(["git", "-C", str(repo), "rev-parse", "origin/main"], capture_output=True, text=True, check=True).stdout.strip()
if head != origin: raise SystemExit("Canonical main differs from origin/main.")
if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, head]).returncode:
    raise SystemExit("Candidate commit is not present in current canonical main history.")
old = json.loads((active.resolve() / "release.json").read_text())
if old.get("artifact_kind") != "production_release" or old.get("production_eligible") is not True:
    raise SystemExit("Active rollback target is not an eligible production release.")
if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", old["git_commit"], commit]).returncode:
    raise SystemExit("Candidate source does not descend from the active production commit.")
print(f"Promotion preconditions: candidate={commit}, active={old['release_id']}, canonical_head={head}")
PY

if ! rg -q '^1079 passed[, ]' "$candidate/backend-tests.txt" || ! rg -q '^12 passed([ ,]|$)' "$candidate/backend-tests.txt"; then
  echo "Candidate lacks the expected full backend and bridge test evidence." >&2
  exit 4
fi
"$canonical_repo/ops/release/runtime_preflight.sh" "$candidate"

# Match the live migration ledger against the candidate's exact migration SQL.
set -a
. "$env_file"
set +a
PYTHONPATH="$candidate/source/backend" "$candidate/backend-venv/bin/python" - "$candidate" <<'PY'
import hashlib, json, os, pathlib, sys
from app.schema_migrations import migration_status
root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / "release.json").read_text())
status = migration_status(os.environ["DATABASE_URL"])
applied = {row["version"]: row["checksum"] for row in status["applied"]}
versions = [part for part in manifest.get("migration_plan", "").split(",") if part]
if not status["initialized"]: raise SystemExit("Production schema ledger is not initialized.")
if not versions: raise SystemExit("Candidate migration plan is empty; schema evidence is required.")
for version in versions:
    path = root / "source/backend/migrations" / version
    if not path.is_file(): raise SystemExit(f"Candidate migration is missing: {version}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if applied.get(version) != digest: raise SystemExit(f"Production schema does not contain the exact candidate migration: {version}")
latest = versions[-1]
print(json.dumps({"schema_fingerprint": status["schema_fingerprint"], "schema_version": latest, "migrations_verified": versions}))
PY

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
commit_short="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["git_commit"][:12])' "$candidate/release.json")"
release_id="posterpro-${stamp}-${commit_short}"
promoted="$release_root/$release_id"
if [[ -e "$promoted" ]]; then echo "Release identity already exists: $release_id" >&2; exit 3; fi
cp -a --reflink=auto "$candidate" "$promoted"
chmod -R u+w "$promoted"
python3 - "$candidate" "$promoted" "$release_id" <<'PY'
import datetime, hashlib, json, pathlib, sys
candidate, promoted = map(pathlib.Path, sys.argv[1:3]); release_id=sys.argv[3]
manifest_path=promoted/"release.json"
manifest=json.loads(manifest_path.read_text())
source_manifest=json.loads((candidate/"release.json").read_text())
manifest.update({
    "release_id": release_id,
    "artifact_kind": "production_release",
    "production_eligible": True,
    "requires_operator_approval": False,
    "built_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "candidate_only_reason": None,
    "promoted_from_release_id": source_manifest["release_id"],
    "promotion_source_manifest_sha256": hashlib.sha256((candidate/"release.json").read_bytes()).hexdigest(),
    "promotion_test_evidence_sha256": hashlib.sha256((candidate/"backend-tests.txt").read_bytes()).hexdigest(),
    "promotion_procedure": "ops/release/promote_runtime_candidate.sh",
    "production_preconditions": ["candidate-checksums-verified", "canonical-main-clean-and-synchronized", "full-backend-and-bridge-test-evidence-verified", "runtime-preflight-passed", "live-migration-ledger-matched", "active-rollback-release-verified"],
})
manifest["database_schema_version"] = source_manifest["migration_plan"].split(",")[-1]
manifest_path.write_text(json.dumps(manifest,indent=2)+"\n")
env=promoted/"release.env"
env.write_text("\n".join([
    f"POSTERPRO_RELEASE_ID={release_id}",
    f"POSTERPRO_BACKEND_COMMIT={manifest['backend_commit']}",
    f"POSTERPRO_FRONTEND_BUILD_ID={manifest['frontend_build_id']}",
    f"POSTERPRO_SCHEMA_VERSION={manifest['database_schema_version']}",
    f"POSTERPRO_DEPLOYMENT_TIMESTAMP={manifest['built_at_utc']}",
]) + "\n")
provenance={"promoted_release_id":release_id,"source_candidate_release_id":source_manifest["release_id"],"source_candidate_sha256sums_sha256":hashlib.sha256((candidate/"SHA256SUMS").read_bytes()).hexdigest(),"source_commit":manifest["git_commit"],"promoted_at_utc":manifest["built_at_utc"],"candidate_unchanged":True}
(promoted/"promotion-provenance.json").write_text(json.dumps(provenance,indent=2)+"\n")
PY
(
  cd "$promoted"
  find source backend-venv frontend-runtime -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum release.json release.env backend-tests.txt promotion-provenance.json >> SHA256SUMS
)
"$canonical_repo/ops/release/runtime_preflight.sh" "$promoted"
chmod -R a-w "$promoted"
echo "$promoted"
