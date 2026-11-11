#!/usr/bin/env bash
set -euo pipefail

# Fast path for frontend and browser-extension-only changes. Backend test and
# runtime evidence may be reused only from the active immutable release after
# exact source/lock/config compatibility checks. This is not a test shortcut
# for backend, worker, bridge, or database changes; those use the full builder.
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
release_root="${POSTERPRO_RELEASE_ROOT:-/opt/apps/posterpro/releases}"
active_link="${POSTERPRO_ACTIVE_RELEASE_LINK:-/opt/apps/posterpro/current}"
lock_path="${POSTERPRO_RELEASE_LOCK:-/run/lock/posterpro-release-build.lock}"
node_heap="${POSTERPRO_NODE_HEAP_MB:-1024}"
branch="$(git branch --show-current)"
commit="$(git rev-parse HEAD)"

[[ "$branch" == main ]] || { echo "Refusing non-canonical branch: $branch" >&2; exit 2; }
[[ -z "$(git status --porcelain)" ]] || { echo "Refusing dirty canonical source." >&2; exit 2; }
[[ -L "$active_link" ]] || { echo "Active immutable release link is unavailable." >&2; exit 2; }
base_release="$(readlink -f "$active_link")"
base_manifest="$base_release/release.json"
[[ -f "$base_manifest" && -f "$base_release/SHA256SUMS" ]] || { echo "Active release is incomplete." >&2; exit 2; }
active_commit="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["git_commit"])' "$base_manifest")"
active_release_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release_id"])' "$base_manifest")"
comparison_commit="$active_commit"
lineage_base=""
lineage_fingerprint=""
if ! git merge-base --is-ancestor "$active_commit" "$commit"; then
  proof="$repo_root/ops/release/source-lineage-baseline.json"
  [[ -f "$proof" ]] || { echo "Candidate is not descended from active release and has no verified source-lineage proof." >&2; exit 2; }
  proof_release="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("legacy_release_id",""))' "$proof")"
  proof_active_commit="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("legacy_release_commit",""))' "$proof")"
  lineage_base="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("github_lineage_base_commit",""))' "$proof")"
  lineage_fingerprint="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("production_application_source_sha256",""))' "$proof")"
  [[ "$proof_release" == "$active_release_id" && "$proof_active_commit" == "$active_commit" ]] || { echo "Source-lineage proof does not match the active release." >&2; exit 2; }
  git merge-base --is-ancestor "$lineage_base" "$commit" || { echo "Candidate does not descend from the verified public GitHub lineage base." >&2; exit 2; }
  verified="$("$repo_root/ops/release/verify_lineage_transition.py" "$base_release/source" "$repo_root" "$lineage_base" "$lineage_fingerprint" "$active_release_id" "$active_commit")"
  [[ "$verified" == "$lineage_fingerprint" ]] || { echo "Source-lineage equivalence proof failed." >&2; exit 2; }
  comparison_commit="$lineage_base"
fi
if [[ -n "$lineage_base" ]]; then
  lineage_transition_json="{\"from_release_id\":\"$active_release_id\",\"from_commit\":\"$active_commit\",\"base_commit\":\"$lineage_base\",\"source_equivalence_sha256\":\"$lineage_fingerprint\"}"
else
  lineage_transition_json="null"
fi

changed_paths="$(git diff --name-only "$comparison_commit" "$commit")"
[[ -n "$changed_paths" ]] || { echo "No source changes from active release." >&2; exit 2; }
while IFS= read -r path; do
  [[ "$path" == AGENTS.md || "$path" == ops/FEATURE_MANIFEST.csv || "$path" =~ ^(frontend/|browser-extension/|ops/release/) ]] || {
    echo "Change '$path' requires the full runtime builder." >&2; exit 4;
  }
done <<< "$changed_paths"

for stable_path in backend bridge; do
  git diff --quiet "$comparison_commit" "$commit" -- "$stable_path" || { echo "$stable_path changed; fast release is forbidden." >&2; exit 4; }
done
git diff --quiet "$comparison_commit" "$commit" -- backend/migrations backend/runtime-requirements.lock frontend/package-lock.json frontend/runtime-node-version.txt || {
  echo "Runtime dependencies or schema inputs changed; fast release is forbidden." >&2; exit 4;
}

(cd "$base_release" && sha256sum -c SHA256SUMS >/dev/null) || { echo "Active release checksum validation failed." >&2; exit 2; }

base_source="$base_release/source"
[[ -f "$base_release/backend-tests.txt" && -d "$base_release/backend-venv" && -f "$base_source/backend/runtime-requirements.lock" ]] || {
  echo "Active release lacks reusable backend test/runtime evidence." >&2; exit 4;
}
if ! grep -Eq '[0-9]+ passed' "$base_release/backend-tests.txt"; then
  echo "Active backend test evidence does not record a passing suite." >&2; exit 4
fi
for rel in backend/runtime-requirements.lock frontend/package-lock.json frontend/runtime-node-version.txt; do
  cmp -s "$repo_root/$rel" "$base_source/$rel" || { echo "Active release dependency input differs: $rel" >&2; exit 4; }
done

available_kb="$(df -Pk "$release_root" 2>/dev/null | awk 'NR==2 {print $4}')"
mkdir -p "$release_root" "$(dirname "$lock_path")"
if [[ -z "$available_kb" ]]; then available_kb="$(df -Pk "$release_root" | awk 'NR==2 {print $4}')"; fi
(( available_kb >= ${POSTERPRO_RELEASE_MIN_FREE_KB:-4194304} )) || { echo "Insufficient release volume space: ${available_kb}KB available." >&2; exit 3; }
exec 9>"$lock_path"
flock -n 9 || { echo "Another PosterPro release build is active." >&2; exit 3; }

release_id="posterpro-$(date -u +%Y%m%dT%H%M%SZ)-${commit:0:12}"
release_dir="$release_root/$release_id"
[[ ! -e "$release_dir" ]] || { echo "Immutable release already exists: $release_id" >&2; exit 3; }
mkdir -p "$release_dir/source"
git archive --format=tar "$commit" | tar -xf - -C "$release_dir/source"
source_root="$release_dir/source"
ln -s "$(readlink -f "$base_release/backend-venv")" "$release_dir/backend-venv"

mkdir -p "$release_dir/test-results"
cp "$base_release/backend-tests.txt" "$release_dir/test-results/backend-reused-from-$active_commit.txt"
cp "$base_release/backend-tests.txt" "$release_dir/backend-tests.txt"
cat > "$release_dir/test-results/reuse-evidence.json" <<EOF
{
  "backend_tests_ran_for_candidate": false,
  "backend_tests_reused_from_commit": "$active_commit",
  "backend_test_output_sha256": "$(sha256sum "$base_release/backend-tests.txt" | awk '{print $1}')",
  "backend_source_unchanged_from_active": true,
  "bridge_source_unchanged_from_active": true,
  "migrations_unchanged_from_active": true,
  "python_lock_unchanged_from_active": true,
  "node_lock_unchanged_from_active": true,
  "backend_runtime_target": "$(readlink -f "$base_release/backend-venv")"
}
EOF

export POSTERPRO_RELEASE_SOURCE="$source_root"
bash "$repo_root/ops/release/tests/test_release_guards.sh"
(
  cd "$source_root/frontend"
  npm ci --no-audit --no-fund
  npm test
  frontend_tests_status="passed"
  if git -C "$repo_root" diff --quiet "$comparison_commit" "$commit" -- browser-extension; then
    extension_tests_status="not-required-no-extension-source-change"
  else
    (cd "$source_root/browser-extension" && node --test tests/posterpro-link.test.js)
    extension_tests_status="passed"
  fi
  printf 'Frontend tests: %s\nExtension bridge tests: %s\n' "$frontend_tests_status" "$extension_tests_status" > "$release_dir/test-results/frontend-extension.txt"
  NEXT_PUBLIC_FRONTEND_GIT_SHA="$commit" \
  NEXT_PUBLIC_FRONTEND_BUILD_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  NODE_OPTIONS="--max-old-space-size=$node_heap" npm run build
)

mkdir -p "$release_dir/frontend-runtime/.next/static"
cp -a "$source_root/frontend/.next/standalone/." "$release_dir/frontend-runtime/"
cp -a "$source_root/frontend/.next/static/." "$release_dir/frontend-runtime/.next/static/"
if [[ -d "$source_root/frontend/public" ]]; then
  mkdir -p "$release_dir/frontend-runtime/public"
  cp -a "$source_root/frontend/public/." "$release_dir/frontend-runtime/public/"
fi
frontend_build_id="$(cat "$source_root/frontend/.next/BUILD_ID")"
node_version="$(node -p 'process.versions.node')"
expected_node="$(cat "$source_root/frontend/runtime-node-version.txt")"
[[ "$node_version" == "$expected_node" ]] || { echo "Node runtime mismatch: $node_version (expected $expected_node)." >&2; exit 4; }
python_version="$("$release_dir/backend-venv/bin/python" -c 'import platform; print(platform.python_version())')"
expected_python="$(cat "$source_root/backend/runtime-python-version.txt")"
[[ "$python_version" == "$expected_python" ]] || { echo "Python runtime mismatch: $python_version (expected $expected_python)." >&2; exit 4; }

# Build/test output must not leak into the versioned source tree or future
# source archives. It remains in frontend-runtime and test-results instead.
rm -rf "$source_root/frontend/node_modules" "$source_root/frontend/.next" "$source_root/.pytest_cache"
schema_version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("database_schema_version",""))' "$base_manifest")"
feature_sha="$(sha256sum "$source_root/ops/FEATURE_MANIFEST.csv" | awk '{print $1}')"
python_lock_sha="$(sha256sum "$source_root/backend/runtime-requirements.lock" | awk '{print $1}')"
node_lock_sha="$(sha256sum "$source_root/frontend/package-lock.json" | awk '{print $1}')"
source_tree_sha="$("$repo_root/ops/release/source_tree_fingerprint.py" "$source_root" backend/app backend/tests backend/migrations backend/runtime-requirements.lock backend/runtime-python-version.txt bridge)"
base_source_tree_sha="$("$repo_root/ops/release/source_tree_fingerprint.py" "$base_source" backend/app backend/tests backend/migrations backend/runtime-requirements.lock backend/runtime-python-version.txt bridge)"
[[ "$source_tree_sha" == "$base_source_tree_sha" ]] || { echo "Backend/bridge source artifact hash differs from the tested base." >&2; exit 4; }

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
  "node_version": "$node_version",
  "python_lock_sha256": "$python_lock_sha",
  "node_lock_sha256": "$node_lock_sha",
  "backend_bridge_source_sha256": "$source_tree_sha",
  "backend_test_evidence_reused_from": "$active_commit",
  "database_schema_version": "$schema_version",
  "feature_manifest_sha256": "$feature_sha",
  "migration_plan": "",
  "built_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "requires_operator_approval": true,
  "persistent_storage_root": "/mnt/posterprodata/app",
  "environment_file_reference": "/opt/apps/posterpro/repo/backend/.env",
  "physical_printing_enabled": false,
  "candidate_only_reason": "Fast-path candidate pending immutable runtime preflight and evidence promotion.",
  "reused_backend_runtime_release": "$(basename "$base_release")",
  "source_lineage_transition": $lineage_transition_json,
  "production_verified_preconditions": []
}
EOF
cat > "$release_dir/release.env" <<EOF
POSTERPRO_RELEASE_ID=$release_id
POSTERPRO_BACKEND_COMMIT=$commit
POSTERPRO_FRONTEND_BUILD_ID=$frontend_build_id
POSTERPRO_SCHEMA_VERSION=$schema_version
POSTERPRO_DEPLOYMENT_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
(
  cd "$release_dir"
  find source frontend-runtime test-results -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum release.json release.env backend-tests.txt >> SHA256SUMS
)
"$(dirname "$0")/runtime_preflight.sh" "$release_dir"
python3 - "$release_dir/release.json" <<'PY'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1])
m=json.loads(p.read_text())
m.update({
    "artifact_kind":"production_release",
    "production_eligible":True,
    "requires_operator_approval":False,
    "candidate_only_reason":None,
    "production_verified_preconditions":[
        "clean-main-exact-commit",
        "backend-bridge-source-hash-matched-active-tested-release",
        "backend-tests-reused-with-source-and-lock-hash-proof",
        "frontend-tests-ran",
        "frontend-production-build-ran",
        "extension-bridge-tests-ran-when-changed",
        "migration-and-schema-inputs-unchanged",
        "active-runtime-and-storage-preserved",
        "runtime-preflight-passed",
        "rollback-target-verified"
    ]
})
p.write_text(json.dumps(m,indent=2)+"\n")
PY
(
  cd "$release_dir"
  find source frontend-runtime test-results -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum release.json release.env backend-tests.txt >> SHA256SUMS
)
"$(dirname "$0")/runtime_preflight.sh" "$release_dir"
chmod -R a-w "$release_dir"
echo "$release_dir"
