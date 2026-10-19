# PosterPro Automation Bridge

This service accepts job submissions from PosterPro and processes unsupported-marketplace work through a bridge contract.

Supported endpoints:

- `GET /health`
- `POST /jobs/import`
- `POST /jobs/crosspost`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/cancel`
- `GET /accounts`
- `PUT /accounts/{marketplace}/{account_key}`
- `POST /accounts/{marketplace}/{account_key}/session`
- `POST /accounts/{marketplace}/{account_key}/connect/start`
- `GET /connect-sessions/{connect_session_id}`
- `POST /accounts/{marketplace}/{account_key}/connect`

Environment:

- `AUTOMATION_BRIDGE_API_KEY`
- `AUTOMATION_BRIDGE_DATA_DIR`
- `AUTOMATION_BRIDGE_RUNNER_MODE`
- `AUTOMATION_BRIDGE_JOB_DELAY_SECONDS`
- `AUTOMATION_BRIDGE_MAX_WORKERS`
- `AUTOMATION_BRIDGE_PORT`
- `AUTOMATION_BRIDGE_BROWSER_HEADLESS`
- `AUTOMATION_BRIDGE_BROWSER_TIMEOUT_MS`
- `AUTOMATION_BRIDGE_BROWSER_SUBMIT_ENABLED`
- `AUTOMATION_BRIDGE_BROWSER_SCREENSHOTS_DIR`

The bridge is designed to read the same `backend/.env` file that PosterPro uses
so the app and the runner can share one configured bridge token and one bridge
URL.

Run locally:

```bash
cd /opt/apps/posterpro/repo/bridge
AUTOMATION_BRIDGE_API_KEY=change-me \
  /opt/apps/posterpro/repo/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8040
```

Playwright runner:

- Set `AUTOMATION_BRIDGE_RUNNER_MODE=playwright` to enable the real browser-assisted Facebook runner.
- Install the Python dependency from `requirements.txt`.
- Install browser binaries:

```bash
cd /opt/apps/posterpro/repo/bridge
/opt/apps/posterpro/repo/backend/.venv/bin/python -m playwright install chromium
```

- Save a bridge account and session payload from PosterPro Settings -> Automation.
- PosterPro Settings -> Marketplaces -> Facebook now includes a `Connect Facebook account` action that launches a real headed bridge browser, waits for login/MFA completion, and stores the captured Playwright storage state back into the bridge account automatically.
- PosterPro also exposes a browser-based `/bridge-desktop` workspace that renders the live bridge-host desktop in the operator's browser through an authenticated backend WebSocket proxy, so the Facebook login can be completed inside the app instead of requiring local VNC.
- Use `AUTOMATION_BRIDGE_BROWSER_SUBMIT_ENABLED=true` only when you want the runner to click the final marketplace action instead of stopping after filling the Facebook draft form.

Remote desktop bridge host:

- Install a local X server and remote viewer stack on the bridge host:
  - `Xvfb`
  - `fluxbox`
  - `x11vnc`
- Run the bridge service with `DISPLAY=:99` so headed Playwright Chromium can attach to the virtual display.
- Keep VNC bound to `127.0.0.1` and access it through an SSH tunnel for safety:

```bash
ssh -L 5901:127.0.0.1:5901 your-bridge-host
```

- Then connect a VNC viewer to `127.0.0.1:5901` and complete the Facebook login in the bridge-host desktop session.
- PosterPro backend defaults for the authenticated browser workspace proxy:
  - `AUTOMATION_BRIDGE_VNC_HOST=127.0.0.1`
  - `AUTOMATION_BRIDGE_VNC_PORT=5901`
