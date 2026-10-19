# PosterPro Completion Roadmap

Last updated: 2026-05-24

## Current verified state

- Live services were restarted and verified active:
  - `posterpro-automation-bridge.service`
  - `posterpro-backend.service`
  - `posterpro-frontend.service`
  - `posterpro-worker.service`
- Verified endpoints:
  - backend `http://127.0.0.1:8030/health`
  - frontend `http://127.0.0.1:3030/settings`
  - bridge `http://127.0.0.1:8040/health`
- Strongest completed product lanes today:
  - eBay direct publish/import baseline
  - bridge/browser connect-session hardening
  - assisted marketplace job visibility and operator drill-down pass 1
  - jobs console "next step" guidance surfaced via backend-derived `operator_action`

## Main remaining risks

- Non-eBay marketplace capability is still assisted/manual-heavy and must be described more truthfully across UI and backend.
- Non-eBay sales polling still contains stub behavior.
- Browser-assisted flows are still primarily draft-fill/handoff oriented unless final submit is explicitly enabled.
- Test coverage improved, but backend `TestClient` health is still unresolved and frontend lacks a meaningful automated test layer.
- Schema/runtime cleanup is incomplete: startup compatibility remains, migrations are SQL-file based, and several payloads remain dict-heavy.
- Some intelligence/pricing features still use placeholder or no-op scaffolding.

## Gaps not captured well enough by the earlier roadmap

- Connector truth gap:
  - eBay is the only meaningfully stronger native integration path.
  - Other connectors still rely on proxy/stub patterns for some operations.
- Sales-sync truth gap:
  - non-eBay sale polling still returns explicit stub events.
- Intelligence gap:
  - embeddings are still deterministic placeholders.
  - comps-based pricing adjustment is still a placeholder action.
- CI gap:
  - no visible GitHub Actions or equivalent CI workflow currently enforces validation.
- Frontend test gap:
  - no real automated frontend test suite is present beyond build validation.
- Runtime hygiene gap:
  - deprecated FastAPI startup event usage remains.
  - repeated `datetime.utcnow()` warnings remain across backend paths.

## Roadmap to completion

### 1. Lock the actual support matrix

- Define the exact supported behavior for every marketplace.
- Align marketplace cards, readiness states, connectors, and job summaries to that truth.
- Remove any misleading implication of native capability where only assisted/manual execution exists.

Completion criteria:
- Every marketplace surface tells the same truth about supported auth, publish, import, and sales-sync behavior.

### 2. Finish assisted marketplace execution

- Decide and document final policy for browser-assisted execution:
  - draft/handoff only
  - or final submit for selected channels
- Verify real assisted crosspost flows end-to-end on live channels.
- Confirm bridge screenshot/assets persistence under live runtime.
- Improve reconciliation from bridge outcomes back into marketplace listing records.

Completion criteria:
- At least one real positive-path assisted crosspost flow is verified per supported assisted execution mode.

### 3. Finish the supported import flows

- Re-verify eBay import with fresh real credentials.
- Verify Facebook browser import under a real bridge session.
- Decide which additional marketplaces truly need import support now versus explicit deferral.

Completion criteria:
- Supported import flows complete live without ambiguous hanging or unclear failure states.

### 4. Fix connector and sales-sync truthfulness

- Replace or gate stubbed non-eBay sale polling.
- Ensure sales-sync controls only appear where real sold-order detection exists.
- Audit generic update/delete/status paths for unsupported connectors.

Completion criteria:
- No marketplace appears sync-capable or integration-capable unless the backend path is real.

### 5. Remove or finish placeholder intelligence features

- Replace placeholder embeddings with a real embedding path, or explicitly defer clustering/intelligence claims.
- Replace placeholder comps-based pricing adjustment, or remove the action from production-facing flows.
- Audit remaining placeholder/no-op intelligence paths.

Completion criteria:
- Operator-facing intelligence actions are either real and validated or intentionally hidden/deferred.

### 6. Finish migration and data-contract cleanup

- Eliminate remaining startup schema compatibility behavior.
- Move schema ownership fully to migrations.
- Establish a repeatable migration runner/process.
- Reduce dict-heavy contracts in:
  - crosspost result summaries
  - import previews
  - bridge session/account payloads

Completion criteria:
- Startup no longer mutates schema opportunistically and core execution payloads have clearer typed contracts.

### 7. Repair the test harness and add missing test layers

- Resolve backend `TestClient` hangs.
- Add frontend automated coverage for jobs/settings/bridge operator flows.
- Add a small end-to-end smoke layer for auth, setup, imports, and assisted-job visibility.

Completion criteria:
- Backend route tests, bridge tests, and core frontend/operator flows are all reliably validated.

### 8. Add CI and repeatable validation

- Encode the critical validation commands in repo-native automation.
- Ensure push/PR validation is not dependent on tribal knowledge in `AGENTS.md`.

Completion criteria:
- A PR can automatically prove the key build and test gates.

### 9. Finish operator control surfaces

- Continue collapsing movement between Settings, Jobs, Listings, and bridge workspace follow-up.
- Add clearer next-step guidance after reconnect-required, failed, imported, and handoff-required states.

Completion criteria:
- Operators can complete supported flows without raw JSON inspection or codebase knowledge.

### 10. Run the final production acceptance pass

- Re-verify all supported workflows live.
- Confirm restart behavior, worker recovery, email delivery, and bridge reconnect paths.
- Freeze and document the final support matrix.

Completion criteria:
- PosterPro has a documented, production-verified support matrix and repeatable deployment validation checklist.

## Recommended next focus

1. Finish roadmap item 1: support-matrix truth.
2. Finish roadmap item 2: assisted execution policy and live validation.
3. Then move to imports and sales-sync truth before broader cleanup and CI work.
