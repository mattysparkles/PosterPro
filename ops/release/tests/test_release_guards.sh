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
cat > "$release_root/known-good/release.json" <<'JSON'
{
  "release_id": "known-good",
  "artifact_kind": "production_release",
  "production_eligible": true,
  "requires_operator_approval": false,
  "canonical_branch": "main",
  "git_commit": "7569047d168f12eee6133a99aedd41d3d47e42a2",
  "built_at_utc": "2026-11-10T20:00:00Z"
}
JSON
cat > "$release_root/eligible/release.json" <<'JSON'
{
  "release_id": "eligible",
  "artifact_kind": "production_release",
  "production_eligible": true,
  "requires_operator_approval": false,
  "canonical_branch": "main",
  "git_commit": "dc6002b5e974ad363cc061353b7e4ffa710d6c88",
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
exit 0
SH
chmod +x "$tmp_root/fake-bin/systemctl" "$tmp_root/fake-bin/curl"
PATH="$tmp_root/fake-bin:$PATH" \
POSTERPRO_RELEASE_ROOT="$release_root" \
POSTERPRO_ACTIVE_RELEASE_LINK="$tmp_root/active" \
POSTERPRO_DEPLOY_LOCK="$tmp_root/deploy.lock" \
POSTERPRO_CANONICAL_REPO="$repo_root" \
"$repo_root/ops/release/activate_release.sh" "$release_root/eligible" >/dev/null
if [[ "$(readlink -f "$tmp_root/active")" != "$(readlink -f "$release_root/eligible")" ]]; then
  echo "Expected valid production_release to pass eligibility and activation guards." >&2
  exit 1
fi
echo "eligible candidate guard path passed with mocked service restart and public health checks"
