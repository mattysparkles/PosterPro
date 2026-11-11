#!/usr/bin/env bash
set -euo pipefail

candidate="${1:-}"
if [[ -z "$candidate" || ! -d "$candidate" || ! -f "$candidate/release.json" || ! -f "$candidate/SHA256SUMS" ]]; then
  echo "Usage: runtime_preflight.sh /path/to/immutable-release" >&2
  exit 2
fi
if ! (cd "$candidate" && sha256sum -c SHA256SUMS >/dev/null); then
  echo "Release checksum verification failed." >&2
  exit 3
fi
python3 - "$candidate" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / "release.json").read_text())
source = root / "source"
checks = {
    "artifact_kind": manifest.get("artifact_kind") == "runtime_candidate",
    "candidate_not_marked_eligible": manifest.get("production_eligible") is False,
    "source_exists": (source / "backend/app/main.py").is_file(),
    "bridge_source_exists": (source / "bridge/app/main.py").is_file(),
    "frontend_standalone_exists": (root / "frontend-runtime/server.js").is_file(),
    "python_runtime_exists": (root / "backend-venv/bin/python").is_file(),
    "frontend_build_id_exists": bool(manifest.get("frontend_build_id")),
    "backend_commit_matches": manifest.get("git_commit") == manifest.get("backend_commit"),
    "frontend_commit_matches": manifest.get("git_commit") == manifest.get("frontend_source_commit"),
    "persistent_storage_is_external": manifest.get("persistent_storage_root") == "/mnt/posterprodata/app",
    "physical_printing_disabled": manifest.get("physical_printing_enabled") is False,
    "frontend_node_dependencies_exist": (root / "frontend-runtime/node_modules/next/package.json").is_file(),
    "backend_test_manifest_exists": (root / "backend-tests.txt").is_file(),
}
lock = source / "backend/runtime-requirements.lock"
node_lock = source / "frontend/package-lock.json"
if lock.is_file():
    checks["python_lock_hash_matches"] = hashlib.sha256(lock.read_bytes()).hexdigest() == manifest.get("python_lock_sha256")
else:
    checks["python_lock_hash_matches"] = False
if node_lock.is_file():
    checks["node_lock_hash_matches"] = hashlib.sha256(node_lock.read_bytes()).hexdigest() == manifest.get("node_lock_sha256")
else:
    checks["node_lock_hash_matches"] = False
for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'} {name}")
if not all(checks.values()):
    raise SystemExit(4)
PY

expected_python="$(cat "$candidate/source/backend/runtime-python-version.txt")"
actual_python="$("$candidate/backend-venv/bin/python" -c 'import platform; print(platform.python_version())')"
expected_node="$(cat "$candidate/source/frontend/runtime-node-version.txt")"
if [[ "$actual_python" != "$expected_python" ]]; then
  echo "Backend runtime mismatch: expected $expected_python, found $actual_python." >&2
  exit 4
fi
command -v node >/dev/null || { echo "Node is not available for the frontend runtime preflight." >&2; exit 4; }
actual_node="$(node -p 'process.versions.node')"
if [[ "$actual_node" != "$expected_node" ]]; then
  echo "Node runtime mismatch: expected $expected_node, found $actual_node." >&2
  exit 4
fi
"$candidate/backend-venv/bin/python" -m pip check
PYTHONPATH="$candidate/source/backend" "$candidate/backend-venv/bin/python" - "$candidate" <<'PY'
import pathlib, sys
from app.main import app
from app.workers.celery_app import celery_app
from app.workers.publish_dispatcher import celery_app as dispatcher_app
root = pathlib.Path(sys.argv[1])
paths = {getattr(route, "path", "") for route in app.routes}
required = {"/health", "/inventory/census/locations", "/inventory/census/search", "/inventory/census/print-agents/pair"}
missing = required - paths
if missing:
    raise SystemExit(f"Runtime API route check failed: {sorted(missing)}")
if celery_app is None or dispatcher_app is None:
    raise SystemExit("Required worker application did not import")
print(f"Runtime imports passed; checked {len(paths)} API routes from {root.name}.")
PY
"$candidate/backend-venv/bin/python" - "$candidate" <<'PY'
import json, os, pathlib, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
root = pathlib.Path(sys.argv[1])
bridge = root / "source/bridge"
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
with tempfile.TemporaryDirectory(prefix="posterpro-bridge-preflight-") as data_dir:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(bridge),
        "AUTOMATION_BRIDGE_API_KEY": "release-preflight-only",
        "AUTOMATION_BRIDGE_DATA_DIR": data_dir,
        "AUTOMATION_BRIDGE_RUNNER_MODE": "simulated",
        "AUTOMATION_BRIDGE_BROWSER_SUBMIT_ENABLED": "false",
    })
    proc = subprocess.Popen(
        [str(root / "backend-venv/bin/python"), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=bridge, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise SystemExit(f"Automation bridge exited during startup: {proc.stderr.read()[-3000:]}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    body = json.loads(response.read())
                    if response.status != 200 or body.get("ok") is not True:
                        raise SystemExit(f"Automation bridge health response was invalid: {body}")
                    print("Automation bridge isolated HTTP startup/health smoke passed.")
                    break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.2)
        else:
            raise SystemExit("Automation bridge did not become healthy within 15 seconds.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
PY
if [[ ! -d /mnt/posterprodata/app || ! -f /opt/apps/posterpro/repo/backend/.env ]]; then
  echo "Persistent storage or the existing external environment reference is unavailable." >&2
  exit 4
fi
if ! mountpoint -q /opt/apps/posterpro || ! mountpoint -q /mnt/posterprodata; then
  echo "Expected application/data mount topology is not active." >&2
  exit 4
fi
echo "Runtime candidate preflight passed. This is not production eligibility or activation authorization."
