#!/usr/bin/env bash
set -euo pipefail

candidate="${1:-}"
release_root="${POSTERPRO_RELEASE_ROOT:-/opt/apps/posterpro/releases}"
active_link="${POSTERPRO_ACTIVE_RELEASE_LINK:-}"
lock_path="${POSTERPRO_DEPLOY_LOCK:-/run/lock/posterpro-deploy.lock}"
health_url="${POSTERPRO_BACKEND_HEALTH_URL:-https://posterpro.sparkleserver.site/api/health}"
frontend_url="${POSTERPRO_FRONTEND_HEALTH_URL:-https://posterpro.sparkleserver.site/}"
canonical_repo="${POSTERPRO_CANONICAL_REPO:-/opt/apps/posterpro/integration-20261110}"
units="${POSTERPRO_SYSTEMD_UNITS:-posterpro-backend.service posterpro-frontend.service}"
health_retries="${POSTERPRO_HEALTH_RETRIES:-20}"
health_retry_delay="${POSTERPRO_HEALTH_RETRY_DELAY:-2}"

if [[ -z "$candidate" || "$candidate" != "$release_root/"* || ! -f "$candidate/release.json" ]]; then
  echo "Candidate must be an immutable release directory with release.json under $release_root." >&2
  exit 2
fi
if [[ ! -f "$candidate/SHA256SUMS" ]]; then
  echo "Candidate has no content checksum manifest." >&2
  exit 2
fi
if ! (cd "$candidate" && sha256sum -c SHA256SUMS >/dev/null); then
  echo "Candidate checksum verification failed." >&2
  exit 2
fi
if python3 - "$candidate/release.json" <<'PYMANIFEST'
import json,sys
with open(sys.argv[1]) as f: manifest=json.load(f)
valid = (
    manifest.get('artifact_kind') == 'production_release'
    and manifest.get('production_eligible') is True
    and manifest.get('requires_operator_approval') is False
)
raise SystemExit(0 if valid else 1)
PYMANIFEST
then :; else
  echo "Candidate is not an approved production_release artifact." >&2
  exit 3
fi
if [[ -z "$active_link" || ! -L "$active_link" ]]; then
  echo "Current production uses direct service working directories; no validated atomic release symlink is configured." >&2
  exit 2
fi
if [[ ! -f "$(dirname "$active_link")/$(basename "$active_link")/release.json" ]]; then
  echo "Current release has no manifest; refusing stale-release comparison or activation." >&2
  exit 2
fi
mkdir -p "$(dirname "$lock_path")"
exec 9>"$lock_path"
if ! flock -n 9; then
  echo "Another PosterPro deployment is active." >&2
  exit 3
fi
current="$(readlink -f "$active_link")"
python3 - "$current/release.json" "$candidate/release.json" "$canonical_repo" <<'PY'
import json,sys
with open(sys.argv[1]) as f: old=json.load(f)
with open(sys.argv[2]) as f: new=json.load(f)
if old.get('canonical_branch') != 'main' or new.get('canonical_branch') != 'main': raise SystemExit('non-canonical release')
if old.get('git_commit') == new.get('git_commit'): raise SystemExit('release already active')
if old.get('built_at_utc','') >= new.get('built_at_utc',''): raise SystemExit('stale release candidate')
import subprocess
if subprocess.run(['git','-C',sys.argv[3],'merge-base','--is-ancestor',old['git_commit'],new['git_commit']],check=False).returncode: raise SystemExit('candidate does not descend from active release')
PY
previous="$current"
read -r -a unit_list <<< "$units"
tmp_link="${active_link}.next.$$"
ln -s "$candidate" "$tmp_link"
mv -Tf "$tmp_link" "$active_link"
if ! systemctl restart "${unit_list[@]}"; then
  ln -s "$previous" "$tmp_link"
  mv -Tf "$tmp_link" "$active_link"
  systemctl restart "${unit_list[@]}" || true
  echo "Service restart failed; prior release restored." >&2
  exit 4
fi
wait_for_health() {
  local url="$1"
  local attempt
  for ((attempt = 1; attempt <= health_retries; attempt++)); do
    if curl --fail --silent --max-time 15 "$url" >/dev/null; then
      return 0
    fi
    if (( attempt < health_retries )); then sleep "$health_retry_delay"; fi
  done
  echo "Health check did not become ready after ${health_retries} attempts: $url" >&2
  return 1
}
if ! wait_for_health "$health_url" || ! wait_for_health "$frontend_url"; then
  ln -s "$previous" "$tmp_link"
  mv -Tf "$tmp_link" "$active_link"
  systemctl restart "${unit_list[@]}" || true
  echo "Health check failed; prior release restored." >&2
  exit 4
fi
echo "Activated $(basename "$candidate"); public health check passed."
