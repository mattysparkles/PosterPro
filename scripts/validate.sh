#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Backend: compileall =="
PYTHONPATH="$ROOT_DIR/backend" "$ROOT_DIR/backend/.venv/bin/python" -m compileall "$ROOT_DIR/backend/app" >/dev/null

echo "== Bridge: compileall =="
PYTHONPATH="$ROOT_DIR/bridge" "$ROOT_DIR/backend/.venv/bin/python" -m compileall "$ROOT_DIR/bridge/app" >/dev/null

echo "== Backend: pytest (targeted) =="
PYTHONPATH="$ROOT_DIR/backend" "$ROOT_DIR/backend/.venv/bin/pytest" \
  "$ROOT_DIR/backend/tests/test_startup_schema_compat.py" \
  "$ROOT_DIR/backend/tests/test_auth.py" \
  "$ROOT_DIR/backend/tests/test_marketplace_setup.py" \
  "$ROOT_DIR/backend/tests/test_marketplace_setup_health.py" \
  "$ROOT_DIR/backend/tests/test_marketplace_api.py" \
  "$ROOT_DIR/backend/tests/test_storage_unit_batch_e2e.py" \
  "$ROOT_DIR/backend/tests/test_ebay_publish_e2e.py" \
  "$ROOT_DIR/backend/tests/test_marketplace_bridge_connect_sessions.py" \
  "$ROOT_DIR/backend/tests/test_marketplace_job_summaries.py" \
  "$ROOT_DIR/backend/tests/test_marketplace_publish_paths.py" \
  "$ROOT_DIR/backend/tests/test_marketplace_import_paths.py" \
  "$ROOT_DIR/backend/tests/test_marketplace_job_recovery.py" \
  -q

echo "== Bridge: pytest (targeted) =="
PYTHONPATH="$ROOT_DIR/bridge" "$ROOT_DIR/backend/.venv/bin/pytest" \
  "$ROOT_DIR/bridge/tests/test_browser_runner_assets.py" \
  -q

echo "== Frontend: build =="
(cd "$ROOT_DIR/frontend" && npm run build)

echo "OK"
