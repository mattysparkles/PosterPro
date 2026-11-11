#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
release_root="${POSTERPRO_RELEASE_ROOT:-/opt/apps/posterpro/releases}"
python_bin="${POSTERPRO_PYTHON:-$repo_root/backend/.venv/bin/python}"
pytest_bin="${POSTERPRO_PYTEST:-$repo_root/backend/.venv/bin/pytest}"
node_heap="${POSTERPRO_NODE_HEAP_MB:-1024}"
requires_approval="${POSTERPRO_RELEASE_REQUIRES_APPROVAL:-true}"
production_eligible="false"
artifact_kind="integration_candidate"
schema_version="${POSTERPRO_DATABASE_SCHEMA_VERSION:-unbaselined}"
if [[ "$requires_approval" != "true" && "$requires_approval" != "false" ]]; then
  echo "POSTERPRO_RELEASE_REQUIRES_APPROVAL must be true or false." >&2
  exit 2
fi

branch="$(git -C "$repo_root" branch --show-current)"
commit="$(git -C "$repo_root" rev-parse HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "Refusing release from non-canonical branch: $branch" >&2
  exit 2
fi
if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
  echo "Refusing release from dirty source." >&2
  exit 2
fi
if [[ ! -x "$python_bin" || ! -x "$pytest_bin" ]]; then
  echo "Set POSTERPRO_PYTHON and POSTERPRO_PYTEST to the reviewed environment." >&2
  exit 2
fi

release_id="posterpro-$(date -u +%Y%m%dT%H%M%SZ)-${commit:0:12}"
release_dir="$release_root/$release_id"
lock_path="${POSTERPRO_RELEASE_LOCK:-/run/lock/posterpro-release-build.lock}"
mkdir -p "$release_root" "$(dirname "$lock_path")"
exec 9>"$lock_path"
if ! flock -n 9; then
  echo "Another PosterPro release build is active." >&2
  exit 3
fi
if [[ -e "$release_dir" ]]; then
  echo "Release ID already exists; release artifacts are immutable." >&2
  exit 3
fi

build_log="$(mktemp)"
cleanup() { rm -f "$build_log"; }
trap cleanup EXIT
export PYTHONPATH="$repo_root/backend${PYTHONPATH:+:$PYTHONPATH}"
"$python_bin" -m compileall -q "$repo_root/backend/app" "$repo_root/backend/scripts"
"$pytest_bin" -q "$repo_root/backend/tests" | tee -a "$build_log"
(
  cd "$repo_root/frontend"
  NODE_OPTIONS="--max-old-space-size=$node_heap" npm run build
) | tee -a "$build_log"

git -C "$repo_root" diff --quiet "$commit" --
git -C "$repo_root" diff --cached --quiet "$commit" --
mkdir -p "$release_dir"
git -C "$repo_root" archive --format=tar "$commit" | gzip -n > "$release_dir/source.tar.gz"
tar -C "$repo_root/frontend" -czf "$release_dir/frontend-build.tar.gz" .next
cp "$build_log" "$release_dir/test-results.txt"
frontend_build_id="$(cat "$repo_root/frontend/.next/BUILD_ID")"
manifest_hash="$(sha256sum "$repo_root/ops/POSTERPRO_FEATURE_MANIFEST_2026-11-10.csv" | awk '{print $1}')"
migration_hash="$(sha256sum "$repo_root/backend/migrations/20261109_shelf_census.sql" | awk '{print $1}')"

cat > "$release_dir/release.json" <<EOF
{
  "release_id": "$release_id",
  "artifact_kind": "$artifact_kind",
  "production_eligible": false,
  "canonical_branch": "$branch",
  "git_commit": "$commit",
  "backend_commit": "$commit",
  "frontend_build_id": "$frontend_build_id",
  "frontend_source_commit": "$commit",
  "database_schema_version": "${schema_version:-unspecified}",
  "feature_manifest_sha256": "$manifest_hash",
  "managed_migration": "20261109_shelf_census.sql",
  "migration_sha256": "$migration_hash",
  "built_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "requires_operator_approval": $requires_approval,
  "candidate_only_reason": "This builder emits source/build bundles, not a verified runtime release layout. Promotion requires a passing migration preflight and a separately validated production_release package.",
  "physical_printing_enabled": false
}
EOF
(
  cd "$release_dir"
  sha256sum source.tar.gz frontend-build.tar.gz test-results.txt release.json > SHA256SUMS
)
chmod -R a-w "$release_dir"
echo "$release_dir"
