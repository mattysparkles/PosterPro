#!/usr/bin/env bash
set -euo pipefail

# Builds an immutable, self-contained application release from canonical main.
# It does not touch systemd, production data, credentials, or the active release.
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
release_root="${POSTERPRO_RELEASE_ROOT:-/opt/apps/posterpro/releases}"
lock_path="${POSTERPRO_RELEASE_LOCK:-/run/lock/posterpro-release-build.lock}"
builder_python="${POSTERPRO_PYTHON:-/opt/apps/posterpro/repo/backend/.venv/bin/python}"
pytest_bin="${POSTERPRO_PYTEST:-/opt/apps/posterpro/repo/backend/.venv/bin/pytest}"
node_heap="${POSTERPRO_NODE_HEAP_MB:-1024}"
minimum_free_kb="${POSTERPRO_RELEASE_MIN_FREE_KB:-8388608}"

branch="$(git -C "$repo_root" branch --show-current)"
commit="$(git -C "$repo_root" rev-parse HEAD)"
if [[ "$branch" != "main" ]]; then echo "Refusing non-canonical branch: $branch" >&2; exit 2; fi
if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then echo "Refusing dirty canonical source." >&2; exit 2; fi
if [[ ! -x "$builder_python" || ! -x "$pytest_bin" ]]; then echo "Reviewed Python test environment is unavailable." >&2; exit 2; fi
if [[ ! -f "$repo_root/backend/runtime-requirements.lock" || ! -f "$repo_root/frontend/package-lock.json" ]]; then echo "Runtime dependency locks are incomplete." >&2; exit 2; fi
if git -C "$repo_root" ls-tree -r --name-only "$commit" | awk '
  /(^|\/)[.]env([.]|$)/ && $0 !~ /[.]env([.](example|sample|template)|[.][^.]+[.](example|sample|template))$/ { print; found=1 }
  /(^|\/)[^/]+[.](pem|key|p12|pfx)$/ { print; found=1 }
  END { exit !found }
' | grep -q .; then
  echo "Tracked environment or private-key file detected; refusing to package secrets." >&2
  exit 2
fi

available_kb="$(df -Pk "$release_root" 2>/dev/null | awk 'NR==2 {print $4}')"
if [[ -z "$available_kb" ]]; then mkdir -p "$release_root"; available_kb="$(df -Pk "$release_root" | awk 'NR==2 {print $4}')"; fi
if (( available_kb < minimum_free_kb )); then echo "Insufficient release volume space: ${available_kb}KB available." >&2; exit 3; fi

release_id="posterpro-$(date -u +%Y%m%dT%H%M%SZ)-${commit:0:12}"
release_dir="$release_root/$release_id"
if [[ -e "$release_dir" ]]; then echo "Immutable release ID already exists: $release_id" >&2; exit 3; fi
mkdir -p "$(dirname "$lock_path")"
exec 9>"$lock_path"
if ! flock -n 9; then echo "Another PosterPro release build is active." >&2; exit 3; fi
mkdir "$release_dir"
mkdir "$release_dir/source"

# Extract tracked source from the exact commit. The Git checkout stays untouched.
git -C "$repo_root" archive --format=tar "$commit" | tar -xf - -C "$release_dir/source"
source_root="$release_dir/source"

python3 -m venv "$release_dir/backend-venv"
"$release_dir/backend-venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$source_root/backend/runtime-requirements.lock"
"$release_dir/backend-venv/bin/python" -m pip check
export PYTHONPATH="$source_root/backend"
"$builder_python" -m compileall -q "$source_root/backend/app" "$source_root/backend/scripts"
(cd "$source_root/backend" && PYTHONPATH="$source_root/backend" "$pytest_bin" -q tests) > "$release_dir/backend-tests.txt" 2>&1 || { cat "$release_dir/backend-tests.txt"; exit 4; }
PYTHONPATH="$source_root/bridge" "$pytest_bin" -q "$source_root/bridge/tests" >> "$release_dir/backend-tests.txt" 2>&1 || { cat "$release_dir/backend-tests.txt"; exit 4; }
"$release_dir/backend-venv/bin/python" - <<'PY'
from app.main import app
from app.workers.celery_app import celery_app
from app.workers.publish_dispatcher import celery_app as dispatcher_app
assert app is not None and celery_app is not None and dispatcher_app is not None
paths = {getattr(route, 'path', '') for route in app.routes}
# Census is a frontend page. Its backend API is nested under the inventory
# router; check concrete API endpoints rather than a nonexistent bare route.
required = {'/health', '/inventory/census/locations', '/inventory/census/search', '/inventory/census/print-agents/pair'}
assert required.issubset(paths), f'missing required backend routes: {required - paths}'
PY
PYTHONPATH="$source_root/bridge" "$release_dir/backend-venv/bin/python" - <<'PY'
from app.main import app
paths = {getattr(route, 'path', '') for route in app.routes}
assert '/health' in paths and '/jobs/crosspost' in paths
print(f'Automation bridge import passed; checked {len(paths)} routes.')
PY

(
  cd "$source_root/frontend"
  npm ci --no-audit --no-fund
  NEXT_PUBLIC_FRONTEND_GIT_SHA="$commit" \
  NEXT_PUBLIC_FRONTEND_BUILD_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  NODE_OPTIONS="--max-old-space-size=$node_heap" npm run build
)

mkdir "$release_dir/frontend-runtime"
cp -a "$source_root/frontend/.next/standalone/." "$release_dir/frontend-runtime/"
mkdir -p "$release_dir/frontend-runtime/.next/static"
cp -a "$source_root/frontend/.next/static/." "$release_dir/frontend-runtime/.next/static/"
if [[ -d "$source_root/frontend/public" ]]; then
  mkdir -p "$release_dir/frontend-runtime/public"
  cp -a "$source_root/frontend/public/." "$release_dir/frontend-runtime/public/"
fi
frontend_build_id="$(cat "$source_root/frontend/.next/BUILD_ID")"
node_major="$(node -p 'process.versions.node')"
python_version="$("$release_dir/backend-venv/bin/python" -c 'import platform; print(platform.python_version())')"
expected_python="$(cat "$source_root/backend/runtime-python-version.txt")"
expected_node="$(cat "$source_root/frontend/runtime-node-version.txt")"
if [[ "$python_version" != "$expected_python" || "$node_major" != "$expected_node" ]]; then
  echo "Runtime version mismatch: Python $python_version (expected $expected_python), Node $node_major (expected $expected_node)." >&2
  exit 4
fi

schema_version="${POSTERPRO_DATABASE_SCHEMA_VERSION:-baseline-existing-schema}"
feature_manifest_sha="$(sha256sum "$source_root/ops/POSTERPRO_FEATURE_MANIFEST_2026-11-10.csv" | awk '{print $1}')"
python_lock_sha="$(sha256sum "$source_root/backend/runtime-requirements.lock" | awk '{print $1}')"
node_lock_sha="$(sha256sum "$source_root/frontend/package-lock.json" | awk '{print $1}')"
migration_plan="20261109_shelf_census.sql,20261110_amazon_media_retry_state.sql"
cat > "$release_dir/release.json" <<EOF
{
  "release_id": "$release_id",
  "artifact_kind": "runtime_candidate",
  "production_eligible": false,
  "canonical_branch": "$branch",
  "git_commit": "$commit",
  "backend_commit": "$commit",
  "frontend_source_commit": "$commit",
  "frontend_build_id": "$frontend_build_id",
  "python_version": "$python_version",
  "node_version": "$node_major",
  "python_lock_sha256": "$python_lock_sha",
  "node_lock_sha256": "$node_lock_sha",
  "database_schema_version": "$schema_version",
  "feature_manifest_sha256": "$feature_manifest_sha",
  "migration_plan": "$migration_plan",
  "built_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "requires_operator_approval": true,
  "persistent_storage_root": "/mnt/posterprodata/app",
  "environment_file_reference": "/opt/apps/posterpro/repo/backend/.env",
  "physical_printing_enabled": false,
  "candidate_only_reason": "Runtime built and checked; live migration preflight and coordinated versioned service transition rehearsal remain required before production eligibility."
}
EOF

(
  cd "$release_dir"
  find source backend-venv frontend-runtime -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum release.json backend-tests.txt >> SHA256SUMS
)
chmod -R a-w "$release_dir"
"$(dirname "$0")/runtime_preflight.sh" "$release_dir"
echo "$release_dir"
