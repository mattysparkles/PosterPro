#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

API_BASE="${POSTERPRO_API_BASE:-http://127.0.0.1:8030}"
EMAIL="${POSTERPRO_EMAIL:-}"
PASSWORD="${POSTERPRO_PASSWORD:-}"

COOKIE_JAR="${COOKIE_JAR:-/tmp/posterpro-acceptance-cookies.txt}"

PY="$ROOT_DIR/backend/.venv/bin/python"

if [[ -z "${EMAIL}" || -z "${PASSWORD}" ]]; then
  echo "Set POSTERPRO_EMAIL and POSTERPRO_PASSWORD to run the acceptance script." >&2
  echo "Optional: POSTERPRO_API_BASE (default: ${API_BASE})" >&2
  exit 2
fi

echo "== Health checks =="
curl -sS "${API_BASE}/health" | head -c 400 || true
echo

echo "== Login (cookie session) =="
rm -f "${COOKIE_JAR}"
curl -sS -c "${COOKIE_JAR}" -H "Content-Type: application/json" \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\"}" \
  "${API_BASE}/auth/login" >/dev/null

ME_JSON="$(curl -sS -b "${COOKIE_JAR}" "${API_BASE}/auth/me")"
USER_ID="$("${PY}" - <<PY
import json, sys
payload = json.loads(sys.stdin.read() or "{}")
print(payload.get("id") or "")
PY
<<<"${ME_JSON}")"

if [[ -z "${USER_ID}" ]]; then
  echo "Login failed: could not resolve /auth/me user id." >&2
  echo "${ME_JSON}" >&2
  exit 3
fi
echo "Logged in as user_id=${USER_ID}"

echo "== Marketplace setup summary =="
SETUP_JSON="$(curl -sS -b "${COOKIE_JAR}" "${API_BASE}/users/${USER_ID}/setup")"
echo "${SETUP_JSON}" | head -c 1200
echo

echo "== Bulk import (queues import jobs for eligible marketplaces) =="
BULK_JSON="$(curl -sS -b "${COOKIE_JAR}" -H "Content-Type: application/json" \
  -d "{\"max_listings\":50}" \
  "${API_BASE}/imports/marketplaces/bulk")"
echo "${BULK_JSON}" | head -c 1600
echo

echo "== Draft listings (pick one for preview) =="
LISTINGS_JSON="$(curl -sS -b "${COOKIE_JAR}" "${API_BASE}/listings")"
LISTING_ID="$("${PY}" - <<PY
import json, sys
items = json.loads(sys.stdin.read() or "[]")
if isinstance(items, list) and items:
    print(items[0].get("id") or "")
PY
<<<"${LISTINGS_JSON}")"

if [[ -z "${LISTING_ID}" ]]; then
  echo "No listings found for this operator. Import jobs may need to complete first." >&2
  exit 0
fi
echo "Using listing_id=${LISTING_ID} for crosspost preview"

echo "== Crosspost preview (facebook + ebay) =="
curl -sS -b "${COOKIE_JAR}" \
  "${API_BASE}/listings/${LISTING_ID}/crosspost-preview?marketplaces=facebook,ebay" | head -c 2000
echo

echo "OK"

