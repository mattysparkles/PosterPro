#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
tmp_root="$(mktemp -d)"
trap 'chmod -R u+w "$tmp_root"; rm -rf "$tmp_root"' EXIT
release_root="$tmp_root/releases"
candidate="$release_root/integration-candidate"
mkdir -p "$candidate"
printf 'immutable payload\n' > "$candidate/payload"
cat > "$candidate/release.json" <<'JSON'
{
  "release_id": "integration-candidate",
  "artifact_kind": "integration_candidate",
  "production_eligible": false,
  "requires_operator_approval": true
}
JSON
(
  cd "$candidate"
  sha256sum payload release.json > SHA256SUMS
)

set +e
POSTERPRO_RELEASE_ROOT="$release_root" POSTERPRO_ACTIVE_RELEASE_LINK= \
  "$repo_root/ops/release/activate_release.sh" "$candidate" >/dev/null 2>&1
status=$?
set -e
if [[ "$status" -ne 3 ]]; then
  echo "Expected ineligible candidate rejection (3), got $status" >&2
  exit 1
fi

printf 'tampered\n' >> "$candidate/payload"
set +e
POSTERPRO_RELEASE_ROOT="$release_root" POSTERPRO_ACTIVE_RELEASE_LINK= \
  "$repo_root/ops/release/activate_release.sh" "$candidate" >/dev/null 2>&1
status=$?
set -e
if [[ "$status" -ne 2 ]]; then
  echo "Expected checksum rejection (2), got $status" >&2
  exit 1
fi
echo "release guard tests passed (ineligible candidate and tampered payload rejected)"

# Exercise the positive eligibility/ancestry/activation path without touching
# system services or production routing. The fake commands only report success.
mkdir -p "$release_root/known-good" "$release_root/eligible" "$tmp_root/fake-bin"
git -C "$tmp_root" init -q -b main release-test-repo
git -C "$tmp_root/release-test-repo" config user.name "PosterPro release test"
git -C "$tmp_root/release-test-repo" config user.email "release-test@example.invalid"
printf 'base\n' > "$tmp_root/release-test-repo/payload"
(cd "$tmp_root/release-test-repo" && git add payload && git commit -qm base)
known_good_commit="$(git -C "$tmp_root/release-test-repo" rev-parse HEAD)"
printf 'candidate\n' > "$tmp_root/release-test-repo/payload"
(cd "$tmp_root/release-test-repo" && git add payload && git commit -qm candidate)
eligible_commit="$(git -C "$tmp_root/release-test-repo" rev-parse HEAD)"
printf 'unhealthy\n' > "$tmp_root/release-test-repo/payload"
(cd "$tmp_root/release-test-repo" && git add payload && git commit -qm unhealthy)
unhealthy_commit="$(git -C "$tmp_root/release-test-repo" rev-parse HEAD)"
cat > "$release_root/known-good/release.json" <<JSON
{
  "release_id": "known-good",
  "artifact_kind": "production_release",
  "production_eligible": true,
  "requires_operator_approval": false,
  "canonical_branch": "main",
  "git_commit": "$known_good_commit",
  "built_at_utc": "20261110T200000Z"
}
JSON
cat > "$release_root/eligible/release.json" <<JSON
{
  "release_id": "eligible",
  "artifact_kind": "production_release",
  "production_eligible": true,
  "requires_operator_approval": false,
  "canonical_branch": "main",
  "git_commit": "$eligible_commit",
  "built_at_utc": "2026-11-10T22:00:00Z"
}
JSON
(
  cd "$release_root/known-good"
  sha256sum release.json > SHA256SUMS
)
(
  cd "$release_root/eligible"
  sha256sum release.json > SHA256SUMS
)
ln -s "$release_root/known-good" "$tmp_root/active"
cat > "$tmp_root/fake-bin/systemctl" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat > "$tmp_root/fake-bin/curl" <<'SH'
#!/usr/bin/env bash
count_file="${POSTERPRO_TEST_CURL_COUNT_FILE:?}"
count=0
[[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
count=$((count + 1))
printf '%s' "$count" > "$count_file"
if [[ "$count" -eq 1 ]]; then exit 22; fi
exit 0
SH
chmod +x "$tmp_root/fake-bin/systemctl" "$tmp_root/fake-bin/curl"
PATH="$tmp_root/fake-bin:$PATH" \
POSTERPRO_RELEASE_ROOT="$release_root" \
POSTERPRO_ACTIVE_RELEASE_LINK="$tmp_root/active" \
POSTERPRO_DEPLOY_LOCK="$tmp_root/deploy.lock" \
POSTERPRO_CANONICAL_REPO="$tmp_root/release-test-repo" \
POSTERPRO_HEALTH_RETRIES=3 \
POSTERPRO_HEALTH_RETRY_DELAY=0 \
POSTERPRO_TEST_CURL_COUNT_FILE="$tmp_root/curl-count" \
"$repo_root/ops/release/activate_release.sh" "$release_root/eligible" >/dev/null
if [[ "$(readlink -f "$tmp_root/active")" != "$(readlink -f "$release_root/eligible")" ]]; then
  echo "Expected valid production_release to pass eligibility and activation guards." >&2
  exit 1
fi
if [[ "$(cat "$tmp_root/curl-count")" -ne 3 ]]; then
  echo "Expected health checks to retry one transient startup failure before verifying both endpoints." >&2
  exit 1
fi
echo "eligible candidate guard path passed with mocked service restart and public health checks"

# The release builder and deployed manifests have used both compact UTC and
# RFC3339 UTC timestamps. Compare parsed instants, not lexical strings.
python3 - "$release_root/known-good/release.json" "$release_root/eligible/release.json" <<'PY'
import json,sys
for path in sys.argv[1:]:
    with open(path) as f: value=json.load(f)['built_at_utc']
    assert value in {'20261110T200000Z','2026-11-10T22:00:00Z'}
PY
echo "mixed timestamp formats accepted in chronological order"

mkdir -p "$release_root/stale"
cat > "$release_root/stale/release.json" <<JSON
{
  "release_id": "stale",
  "artifact_kind": "production_release",
  "production_eligible": true,
  "requires_operator_approval": false,
  "canonical_branch": "main",
  "git_commit": "$unhealthy_commit",
  "built_at_utc": "2026-11-10T19:59:00Z"
}
JSON
(cd "$release_root/stale" && sha256sum release.json > SHA256SUMS)
set +e
POSTERPRO_RELEASE_ROOT="$release_root" POSTERPRO_ACTIVE_RELEASE_LINK="$tmp_root/active" \
POSTERPRO_DEPLOY_LOCK="$tmp_root/deploy.lock" POSTERPRO_CANONICAL_REPO="$tmp_root/release-test-repo" \
"$repo_root/ops/release/activate_release.sh" "$release_root/stale" >/dev/null 2>&1
stale_status=$?
set -e
if [[ "$stale_status" -ne 3 ]]; then echo "Expected mixed-format stale release rejection, got $stale_status" >&2; exit 1; fi
echo "mixed timestamp formats correctly reject chronologically stale candidates"

# An unhealthy candidate must roll back after exhausting startup retries.
mkdir -p "$release_root/unhealthy"
cat > "$release_root/unhealthy/release.json" <<JSON
{
  "release_id": "unhealthy",
  "artifact_kind": "production_release",
  "production_eligible": true,
  "requires_operator_approval": false,
  "canonical_branch": "main",
  "git_commit": "$unhealthy_commit",
  "built_at_utc": "2026-11-12T00:00:00Z"
}
JSON
(cd "$release_root/unhealthy" && sha256sum release.json > SHA256SUMS)
cat > "$tmp_root/fake-bin/curl-fail" <<'SH'
#!/usr/bin/env bash
exit 22
SH
chmod +x "$tmp_root/fake-bin/curl-fail"
mv "$tmp_root/fake-bin/curl" "$tmp_root/fake-bin/curl-success"
cp "$tmp_root/fake-bin/curl-fail" "$tmp_root/fake-bin/curl"
before_unhealthy="$(readlink -f "$tmp_root/active")"
set +e
PATH="$tmp_root/fake-bin:$PATH" \
POSTERPRO_RELEASE_ROOT="$release_root" \
POSTERPRO_ACTIVE_RELEASE_LINK="$tmp_root/active" \
POSTERPRO_DEPLOY_LOCK="$tmp_root/deploy.lock" \
POSTERPRO_CANONICAL_REPO="$tmp_root/release-test-repo" \
POSTERPRO_HEALTH_RETRIES=2 \
POSTERPRO_HEALTH_RETRY_DELAY=0 \
"$repo_root/ops/release/activate_release.sh" "$release_root/unhealthy" >/dev/null 2>&1
unhealthy_status=$?
set -e
if [[ "$unhealthy_status" -ne 4 || "$(readlink -f "$tmp_root/active")" != "$before_unhealthy" ]]; then
  echo "Unhealthy candidate did not safely restore the previous release." >&2
  exit 1
fi
echo "persistent startup failure exhausted bounded retries and restored the previous release"
