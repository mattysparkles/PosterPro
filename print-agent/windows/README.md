# PosterPro WT20 Windows print agent

This project is an explicitly launched, outbound-only Windows process. It has
no HTTP listener, startup service, raw printer protocol, or default-printer
changes. It is restricted to the `Sparkles SKU Printer` queue.

## Install and pair (Acer Predator Windows workstation)

1. Confirm Windows shows `Sparkles SKU Printer`, driver `WT20`, port `USB003`.
   Open Printing Preferences and verify the `58 mm × 40 mm` preset, portrait,
   and 100% scaling. The discovery report read back 58 × 60 mm at capture time;
   verify the current ticket before proceeding.
2. Install 64-bit Python 3.12 from python.org. In PowerShell, open this folder,
   create a venv, and install only the pinned agent requirements:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. In PosterPro → Inventory → Shelf Census, create a one-time pairing code.
   Run `python -m posterpro_print_agent.agent enroll` and enter the production
   HTTPS URL and pairing code at the prompts. The server returns the agent
   credential once; it is stored encrypted to the current Windows account using
   DPAPI in `%LOCALAPPDATA%\PosterPro\print-agent.json`.
4. Run `python -m posterpro_print_agent.agent status` to inspect the exact
   configured queue. Missing or ambiguous 58 × 40 mm driver forms stop the agent.
5. Run `python -m posterpro_print_agent.agent dry-run` to exercise outbound
   pairing, schema validation, queue heartbeat, and job handling without GDI
   printing. Do not enable automatic printing until the physical checklist is
   completed and the operator deliberately marks the agent tested/enabled.
6. To run interactively, launch `python -m posterpro_print_agent.agent run`.
   Ctrl+C stops it. It is not installed as a service or startup task.

The local journal at `%LOCALAPPDATA%\PosterPro\job-journal.json` records the
pre-submission marker before calling GDI. A job in `SUBMITTING` or a later
uncertain state is never automatically resubmitted after a crash. Review the
PosterPro job and physical label, then use the operator reconciliation/reprint
controls. Logs omit bearer credentials and label contents.

## API contract

Base path: `POSTERPRO_HTTPS_ORIGIN/api/inventory`. Every request uses standard
TLS certificate verification. The agent has only the `census_label_jobs` scope.

| Method and path | Caller | Purpose |
|---|---|---|
| `POST /census/print-agents/pair` | One-time code, no main password | Consume pairing code; returns one-time `ppprint_...` credential |
| `GET /census/print-agent/profile` | Agent bearer | Exact printer allowlist and per-job 58x40/203 DPI profile |
| `POST /census/print-agent/heartbeat` | Agent bearer | Capabilities, printer status, and last-contact update |
| `POST /census/print-agent/jobs/claim` | Enabled, tested agent | Atomically claims one queued tenant Census label using row lock/skip-locked; 204-like empty `{job:null}` when none |
| `POST /census/print-agent/jobs/{job_uuid}/state` | Claiming agent + lease token | `RENDERED → SUBMITTING → SPOOLER_ACCEPTED`, or explicit `FAILED`/`UNKNOWN` outcomes |
| `POST /census/print-agents/{id}/revoke` | Paired PosterPro user | Revoke credential and quarantine active leases |

Claim response contains only a fixed schema: job UUID/idempotency key, opaque
lease, exact printer profile, item/title/SKU/token/location snapshot, template
version, 464 × 320 1-bit PNG base64, and no commands, filesystem paths, or raw
printer bytes. Every query is scoped to the paired user. `SPOOLER_ACCEPTED`
means GDI returned successfully; it is not proof that paper emerged.

Polling interval is five seconds; leases are three minutes. An expired lease
becomes `UNKNOWN` and is never automatically requeued. An operator must record
whether they physically saw the label; a new print is always a new job with
reprint provenance and does not touch stock.

## Supported local checks and limits

The platform-neutral validation/journal code can run on Linux. Native queue
enumeration, DEVMODE handling, GDI output, installed-form readback, spooler IDs,
driver behavior, barcode scanner decoding, and paper output require Windows and
the actual WT20. The agent intentionally refuses alternate queues and does not
fall back to ESC/POS/TSPL/ZPL or change persistent printer preferences.
