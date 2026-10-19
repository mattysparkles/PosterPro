# PosterPro deployment state

## 2026-10-19 ENR-012 local-only price evidence correction

- `canonical_readiness.py` no longer treats ETV, category-model or
  deterministic fallback provenance as a researched listing ask; legacy
  blocker cleanup no longer clears ETV pricing debt. An explicit operator
  price lock remains usable. Needs Review aggregates consequently exclude
  fallback-priced Vine rows.
- Focused regressions covering ETV provenance, category-model $21.60,
  operator override, and review aggregate: 4 passed. A broader readiness pair
  reported 8 unrelated legacy expectation failures (queue/shipping/category/
  destination) after the price-path assertion was corrected; release review
  must triage these before deployment.
- Not deployed; no DB rows, listings, settings, or marketplace state changed.
  Frontend build is unrelated and passed in this continuation; no service
  restart or migration occurred. Roll back only this isolated resolver/gate
  and test change if review rejects it.

## 2026-10-19 PUB-014 local-only increment

- Source updated in the dirty active repository: recovery API returns
  `local_aspect_names` and `remote_required_aspects_missing_locally`; Settings
  → eBay displays that draft-vs-remote required-specific diff.
- Validation: focused API regression 1 passed; targeted ESLint 0 errors, one
  existing hook warning; frontend production build passed with repository
  warnings. Not deployed, no restart, no migration, and no eBay write/retry.
  Production remains at the previously recorded revision;
  the modified repository is not a reproducible release.
- Future rollback for an isolated release: revert only the reviewed PUB-014
  API/UI/test change after inspecting that release diff. No production data
  rollback is required because this increment has not been deployed.

## Verified 2026-10-18 baseline

- Active source/deployment directory: `/opt/apps/posterpro/repo`.
- Backend: `posterpro-backend.service`, uvicorn from `/opt/apps/posterpro/repo/backend`, port 8030, active.
- Frontend: `posterpro-frontend.service`, Next from `/opt/apps/posterpro/repo/frontend`, port 3030, active.
- Worker and beat: active, using the repository backend virtual environment.
- Database: PostgreSQL `posterpro` on local host; health endpoint reports `database_ready: true`.
- Git revision before this increment: `e673eb0 Log latest marketplace and import software updates`.

## Source-control warning

The active repository contains extensive pre-existing modified and untracked application sources, tests, migrations, frontend pages, browser-extension files, and documentation. They have not been discarded, reset, or broadly committed by this recovery pass. Before any release, review and stage only intentional source/migration/test/documentation files; exclude `.env`, credentials, `node_modules`, snapshots, temporary databases, generated build outputs, media, and customer data.

## Current increment

- Adds centralized shipping-policy normalization and calculated-rate parcel-evidence validation.
- Changes default under-threshold policy from $10 to $25.
- Removes category/title-derived package-weight/dimension fabrication from new shipping profiles.
- No database migration, service restart, deployment, remote listing update, or marketplace publication has occurred yet.

### Latest read-only provider verification — 2026-10-18

- Fresh direct invocation of the local preview code against the authenticated
  eBay account returned 162 active offers; all 162 mapped to local Listing
  rows. The current preview is not deployed to the live dashboard.
- The 24 under-$25 offers all currently use free-shipping calculated
  `USPSParcel`, and none has trusted measured parcel dimensions/weight.
- Handling is 161 offers at 0 business days and one at 1; proposed per-listing
  increments to 1/2 are supported. Policy `260642879013` is shared by 161
  active offers; inactive/draft dependencies remain unknown.
- No provider writes, deployment, service restart, or database listing changes
  occurred. Active changes remain approval-gated.

### 2026-10-18 local preview economics increment (not deployed)

- `ebay_operations_preview` now computes its fee estimate using the remote
  active ask, flags local/remote ask mismatches, and returns the persisted
  research evidence summary. It separates estimated proceeds before the
  seller's shipping-label cost from estimated profit.
- Profit is withheld unless explicit cost basis exists for item acquisition,
  label, packaging/materials, promotion, and other transaction costs.
- The Settings → eBay line item and preview CSV now surface these distinctions.
- Validation: backend preview + eBay fee tests `19 passed`; CSV Node test
  `1 passed`; `cd frontend && npm run build` passed with existing warnings.
- No deployment, service restart, database/listing mutation, or provider write.

### 2026-10-18 assisted-marketplace production observation

- Current source manifest and latest unrevoked production extension heartbeat
  both report `0.3.70` (device 73; heartbeat 2026-10-19 03:02 UTC).
- Production DB shows 0 Facebook jobs executed by `.70` in the last 30 days;
  the most recent failed job/result versions are older `.68`/`.69`. Thirty-two
  Facebook pending-review jobs have no external listing identity/URL, so they
  remain unconfirmed. Mercari has no durable job with remote ID/URL; its only
  recent create attempt ended after a remote-not-found reconciliation.
- These are read-only production observations. No browser job was launched,
  no customer listing was changed, and no deployment occurred.

### 2026-10-18 local offline packing replay increment (not deployed)

- The Packing workstation now journals scan/photo/voice actions locally before
  network transmission, replays pending events in capture order on reconnect,
  and exposes pending count/manual sync. Photos/audio are retained as
  data-URLs within explicit 15 MB photo / 25 MB audio queue caps.
- Scanner dispatch continues to use the stable journal event ID. Slate create
  and workstation-photo upload now accept `client_event_id` and return the
  existing boundary/photo on a replay, avoiding duplicate Slate rendering,
  label queueing, bridge upload, or photo records after lost acknowledgements.
- Verification: targeted intake route/service tests `3 passed`; offline
  workstation interaction plus IndexedDB journal tests `4 passed`; backend
  py_compile passed. Frontend build remains to run after this increment.
- No migration, deployment, service restart, production DB mutation, or device
  test. Only local files are modified.

## 2026-10-18 local pricing/category recovery increments (not deployed)

- Current checkout contains the eight-row category-candidate reconnaissance in
  `POSTERPRO_OPEN_DEFECTS.md`; it was read-only and did not change listing or
  provider data. Category candidate endpoint/UI remains unshipped.
- Pricing service now withholds ineligible category/model fallback values from
  recommendation variants; the dashboard withholds the unsupported number and
  labels the need for research. `test_pricing_evidence_gate.py`: 6 passed;
  frontend `npm run build`: passed with existing warnings. Broader
  `test_listing_processing_backfill.py`: 27 passed, 8 failed on needs-review or
  retry-state expectations. Those failures require triage; nothing deployed.
- No commit, migration, service restart, marketplace mutation, or publication
  occurred. Production revision and active services remain unchanged from the
  verified baseline above.
- Additional local-only safeguards remove visual/image-only `$19.99` and `$24`
  asks, legacy heuristic/LLM price writes, fabricated recovery parcel facts,
  and eBay missing-price substitution. Evidence-gated eBay service/preflight,
  intake/recovery, AI, and pricing tests: 120 passed. No deployment/restart or
  listing changes; already persisted old price fields still need audit.
- Read-only production DB audit: `media_inventory_recovery=962`,
  `intake_head_slate=404`, `google_photos_album=0`; missing/ineligible pricing
  provenance totals 410/121; known prior fallback amounts total 369/99. None
  has `Listing.status=PUBLISHED`; local eBay crosspost rows (7/3) have no
  remote ID or published marker. This does not prove remote absence. No rows
  were changed; remote reconciliation remains required.
- eBay failure diagnostics now preserve a sanitized payload shape for future
  request failures; tests 63 passed. No deployment/restart. The original 25
  submissions' request bodies/methods were not retained and remain unavailable.
- Adds a local-only Mercari diagnostic-state correction: an incomplete current
  browser diagnostic is recorded as `BLOCKED_EXTERNAL` with its supplied
  required-field error rather than `COMPLETED`. The ten historical diagnostic
  records have not been altered, and no Mercari browser action ran.
- Extends the local-only eBay shipping/handling preview with remote/local
  identity-drift counts and an explicit bulk-revision safety blocker. No eBay
  API mutation, service restart, or deployment occurred.
- Changes local photo-recovery draft creation so provisional observations are
  research-pending candidates, never initial Needs Review records. No intake
  data was backfilled or changed in production.
- Adds a local-only dashboard cross-market calendar-reprice preview. It has no
  executor path in this increment and made no provider call or listing change.
- Adds local scanner-command aliases for NEXT ITEM and BUNDLE and checkpoint
  handling for NEXT ITEM. No production scanner or inventory record was used.
- Adds local free-form box-QR intake classification. It makes no Slate,
  inventory, package, or marketplace mutation until operator confirmation.
- Adds a local read-only free-form QR inventory lookup endpoint; it has no
  automatic association or cross-tenant result path.
- Adds local-only persisted operator shipping profitability preferences and
  Settings controls. The policy normalizer consumes the same stored fields, and
  the above-threshold default is explicitly `preserve_existing`. Focused
  backend tests passed (`13 passed`) and the frontend production build
  completed with the existing repository warnings. No service was restarted
  and none of these local changes are deployed.
- Extends the local-only eBay revenue preview to query current fulfillment
  policy definitions, map exact offer policy IDs, count active offers sharing
  each policy, and read exact Trading listing details for active offers without
  a policy reference. The operator UI displays shipping blockers and every
  listing's current → proposed handling time. Focused backend suite:
  `47 passed`; Next.js build completed successfully. No provider mutation,
  service restart, deployment, or live verification occurred.
- Adds local-only current eBay taxonomy/aspect inspection to the 25-failure
  recovery endpoint and a Settings → eBay per-item report. The endpoint uses
  read-only provider GETs for deterministic failures, preserves the fourteen
  `25001` rows as unknown, and cannot retry. Numeric categories without stored
  tree provenance require one unique local taxonomy ownership match. Recovery
  tests `3 passed`, related eBay suites `42 passed`, compile passed, frontend
  build completed. No live taxonomy result, deployment, retry, or publication
  was recorded.

## Reproducible-release control

The explicit source-control inventory and isolated-worktree release procedure
are documented in `docs/POSTERPRO_SOURCE_CONTROL_AUDIT.md`. They are required
because the active service tree remains intentionally dirty.

## Rollback

Revert only the shipping-policy increment files after preserving the current worktree; then restart backend only after the regression suite passes. Do not alter marketplace records as part of rollback.

## 2026-10-18 latest runtime and provider observation

- Repository HEAD observed: `292c80f`. This is not asserted to be the deployed
  revision: the worktree has 196 modified/untracked paths and source parity was
  not established.
- Backend and frontend services are active; backend health returned
  `database_ready: true`. Backend started 2026-10-18 12:25 EDT and frontend
  12:24 EDT. Worker and beat are active from 2026-10-14 15:22 EDT.
- Read-only authenticated eBay state: 162 active offers, 24 below $25. The
  full preview confirmed all 24 are free/seller-funded calculated USPSParcel;
  parcel dimensions lack measured provenance. Handling values returned for
  all offers (161 at 0, one at 1 business day), but exact supported choices
  and shared-policy effects remain unverified.
- Active exact-SKU reconciliation for the 14 unknown `25001` batch outcomes
  found no exact active match. This does not resolve inactive/draft offers.
- Local source changes after the current runtime were not deployed. No live
  shipping, handling, pricing, publishing, or deactivation mutation occurred.
- A further local-only pricing correction marks `category_model_estimate` as
  ineligible for publication recommendations and returns an explicit blocker.
  The price-apply API now rejects unevidenced estimates without a documented
  operator-entered amount/reason and rejects local apply on live-published
  records. Pricing-focused tests passed (`21 passed`); it has not been deployed.
- The recovery parser was hardened for five persisted string-valued provider
  error records. Current local API code then completed a live read-only batch
  preview (25 rows; 11 taxonomy checks confirmed; 14 no-active-SKU matches;
  0 retryable). This is not evidence that the endpoint is deployed. Recovery
  tests passed (`5 passed`); no restart or provider mutation occurred.
- Latest local code read exact eBay `SellerShippingProfile` IDs/names from
  Trading GetItem and grouped active references. Live read-only preview found
  the 24 under-$25 offers share one policy; 161 of 162 active offers use
  policies shared by multiple active offers, one is referenced once. Current
  handling distribution is 161 at 0 days and one at 1. This UI/parser update is
  not deployed; no policy changes occurred.
- Rollback: preserve the full dirty worktree and revert only the reviewed
  isolated release increment if later deployed; do not reset/clean the active
  repository. No rollback action is currently required because nothing from
  this increment is deployed.
- Latest local-only pricing increment adds a shared external-research
  persistence service and bounded `research_google_intake_pricing` Celery task
  scheduled every 15 minutes (five drafts per invocation). It records only
  active-asking evidence and analysis, never price/readiness. Compile and
  focused research/pricing tests passed (26); no backend/worker deployment or live-worker
  verification occurred. Existing services remain untouched.
- Latest eBay handling increment calls Trading `GeteBayDetails` read-only and
  exposes supported values/nearest alternatives in the preview UI. Provider
  read returned `[0,1,2,3,4,5,6,7,10,15,20,30,40]` with metadata timestamp
  2022-09-24; no listing/policy was revised. Provider/preview tests: 41 passed;
  frontend build passed with existing warnings. A fresh full local-code,
  authenticated preview then completed using batched Trading listing reads:
  162 active, 24 under threshold, 0 remote IDs unmapped; current handling 161
  at 0 and one at 1, both +1 results supported. No deployment/restart; no live
  listing or policy changed. Policy references outside active snapshot remain
  unknown. Earlier interrupted read-only attempt changed no data.
- The Settings → eBay CSV preview export and formula-safe serializer are local
  only. Direct Node report test passed (1); frontend production build passed
  with existing warnings. No service restart/deployment; browser download and
  public production state have not been verified.
- 2026-10-18 eBay failed-write reconciliation is local only: recovery now uses
  the Trading active snapshot plus exact-SKU Inventory getOffers reads. Against
  the live provider through local code, all 14 unknown rows returned 404/25713
  for exact SKU and had no active match. Service/recovery tests: 45 passed.
  No deploy/restart, remote write, retry, or publication. Rollback is limited
  to the reviewed files `backend/app/api/ebay.py`,
  `backend/app/services/ebay_service.py`,
  `backend/app/services/ebay_failure_recovery.py`,
  `backend/app/workers/tasks.py`, `frontend/pages/settings/ebay.js`, and
  their targeted tests; preserve all other dirty worktree changes.
- Follow-up diagnostics now retain HTTP method for new eBay REST errors and
  the recovery UI displays the exact-SKU result and historical request method
  when available. This narrows future attribution but cannot reconstruct the
  missing method on these historical 25001 records. Focused recovery,
  eBay-service, and marketplace-job-summary suites: 64 passed; frontend
  production build completed with existing warnings. No service restart or
  deployment; public UI remains unverified. No live provider mutation.
- 2026-10-18 failed-batch semantic-category checks are local only. Read-only
  live preview confirmed all 14 unknown rows’ leaf taxonomy/aspects, while local
  semantic validation blocked seven obvious product/category contradictions.
  Added semantic guard and recovery UI coverage; focused integration suite is
  verified locally: taxonomy/preflight 68 passed, recovery/method capture 10
  passed, compile passed, and frontend production build completed with existing
  warnings. No deploy/restart, category revision, or retry. Preserve the
  current dirty worktree; rollback only the isolated taxonomy/recovery/UI edits
  if a reviewed release later requires it.
- Follow-up local-only increment adds per-listing current eBay taxonomy
  candidate search to Settings → eBay, verifying leaf status and required
  aspects and an exact read-only before/after selection preview without
  applying a selection. It also tenant-scopes failed-batch
  recovery jobs/listings. Recovery tests: 12 passed; `git diff --check` clean.
  After the UI preview change, Next.js production build passed with existing
  warnings. The new endpoint was not live-queried; no deployment/restart or
  provider mutation occurred.
- 2026-10-18 local-only condition-evidence increment: added the read-only Sell
  Metadata `get_item_condition_policies` lookup for failed-batch categories,
  exposed supported condition IDs/descriptions in Settings → eBay without
  guessing PosterPro enum mappings, and normalized provider-required fields
  (the three batch errors now report `Product`, `Original/Reproduction`,
  `Original/Reproduction`). The authenticated local-code replay queried the
  production provider and returned all 25 rows; all 11 deterministic category/
  required-data rows returned condition policy metadata. For the eight category
  errors, saved `New` appeared among current category policy choices; provider
  errors still confirm invalid categories, so no category or condition edits
  were inferred. Recovery/environment tests: 18 passed; compile and frontend
  production build passed with existing warnings. No deploy/restart, retry, or
  remote write. The 14 unknown outcomes remain unresolved.
- 2026-10-18 local-only parcel-provenance safeguard: the 5 retained Trading
  failures had numeric package facts from `estimated_category_profile` despite
  `manual_measurement_needed=false`. The shared eBay preflight now blocks
  explicitly estimated required weight/dimensions, and the payload builder
  omits those estimated values. EBay service, marketplace preflight, and
  operations-preview suites: 75 passed; compile and `git diff --check` passed.
  No deploy/restart or retry. The 5 physical packages still need measured data.
- Follow-up adds the operator-facing packed-parcel confirmation in Listing
  Editor. Editing weight or dimensions clears confirmation; explicit
  confirmation stores field-level measurement provenance. Central calculated-
  shipping readiness rejects values without that provenance. Relevant tests:
  77 passed; frontend production build passed with existing warnings. Still not
  deployed; browser interaction and production preflight remain unverified.
- Settings → eBay under-threshold risk rows now link mapped items directly to
  their listing editor when packed measurements are not ready. Frontend build
  passed with existing warnings. No deployment/restart; link/browser workflow
  is not production-verified.
- Failed-batch correction preview now shows current synced category path,
  stored condition/specifics, candidate taxonomy/aspects/condition policy, and
  local shipping/readiness blockers. A live read-only call for failed item 2744
  returned the exact stored category path and five current candidates with
  provider aspect/condition checks. Recovery + environment tests: 18 passed;
  frontend build passed. No deploy/restart/category edit/retry.
- Local-only shipping-default consistency repair: `backend/app/api/routes.py`
  now delegates shipping fields to `normalize_shipping_policy`, removing its
  stale $10/free-above fallback while preserving an explicit stored operator
  choice. Two targeted route preference regressions passed. No migration,
  deploy, restart, provider call, or listing mutation.
- Packing offline work's frontend build has since completed successfully with
  the existing warnings; offline interaction + journal tests passed 4/4 and
  `git diff --check` was clean. It remains undeployed and hardware/browser
  reconnect behavior is unverified.
- EBay deterministic recovery now joins exact-SKU unpublished offer records
  and Inventory item GETs with the current local category/taxonomy. On the
  authenticated read-only run, rows 3440/3425/3407 had remote categories
  Snacks > Chips / Television Memorabilia > Color / Television Memorabilia >
  Color versus current local Interface ICs / Other Measuring & Layout Tools /
  Chandeliers & Ceiling Fixtures. Current remote-category requirements match
  the saved `Product` and `Original/Reproduction` errors. Recovery suite: 14
  passed; backend py_compile and frontend build passed with existing warnings.
  No deploy/restart, remote write, repricing, or retry. Local preflight blocks
  all three for unsupported pricing evidence.
- Local read-only diagnostic for all eight category/condition failures found
  unpublished Inventory offers for 2744/2283/3405; their saved remote category
  IDs 261084/262095/2208 return current Taxonomy HTTP 400 / 62005. Exact-SKU
  offer lookups for 3406/2238/2751/3475/3201 return 404/25713. Recovery now
  distinguishes that response from unavailable lookup and keeps retry disabled.
  All eight local preflights block on unsupported price evidence. No changes to
  any offer/item/listing and no deploy/restart. Recovery suite 14 passed; full
  frontend build passed with existing warnings.
- Timeline-derived price-gate correction is local only. Production DB rows were
  evaluated without writes: prior local-code classification had 115/1,357 in
  Needs Review, including 92 with `category_model_estimate`; after the new gate,
  0 are Needs Review (350 Needs Attention, 1,007 Processing). Focused readiness
  tests passed 3/3. Combined canonical-readiness/pricing-evidence run reported
  46 passed and 8 failures in older readiness expectations; no broad green
  claim is made. No deployment or listing mutation.
- PUB-013 local delta extends exact-SKU checks to the Inventory item after offer
  lookups miss: all 14 unknown 25001 rows return an Inventory item, while none
  is confirmed as an offer/listing. Recovery tests and compilation pass; the
  frontend build process was lost before final exit capture. This delta is not
  deployed and caused no provider write or retry. Production services remain
  unchanged by this work.
- 2026-10-19 read-only deployment/source audit: HEAD `292c80f`; `git status`
  reports 206 dirty/untracked paths, including 31 untracked application source
  paths, 12 untracked migrations, and 18 untracked backend tests (counts are
  category counts and not additive to status entries). Several include active
  runtime dependencies. No files were staged/committed or removed. Backend
  systemd WorkingDirectory is `/opt/apps/posterpro/repo/backend` and launches
  that repo's venv Uvicorn on 127.0.0.1:8030; worker uses the same backend
  checkout; frontend runs from `/opt/apps/posterpro/repo/frontend` at 127.0.0.1:
  3030. All three services report active. This confirms runtime paths, not a
  clean/reproducible revision: the services use a dirty checkout and a safe
  isolated release cannot be made without curating these user-owned changes.
  No restart/deployment performed.
- ENR-010 local category guard: live read-only Dorman 600-103 research confirmed
  eBay's current first category suggestion for that full title is `Other US
  License Plates`; the item remains blocked and unmodified. The new fail-closed
  semantic guard passes 4 focused tests and full eBay service/taxonomy suites
  (85 passed), plus compile/diff-check. Not deployed;
  no service restart or live category edit.
- ENR-011 local fitment tool: authenticated Catalog MPN search + Motors Metadata
  research exposed in Listing Workspace and persisted only its research result
  to local `marketplace_data`; exact ePID `15079948485` matched Dorman 600-103,
  compatibility count 0. Draft content/price/status/readiness remained
  unchanged. Six tests (Brand+MPN/GTIN), compilation, diff check and frontend build passed.
  Not deployed; no listing or marketplace mutation and no service restart.
- PUB-003 Mercari policy guard: local source removes Mercari extension mutation
  capabilities, blocks synthetic form diagnostics and new assisted jobs, and
  converts queued/retryable legacy Mercari jobs to `BLOCKED_EXTERNAL` before
  extension claim. Settings now explains the restriction, removes Mercari from
  live form diagnostics, and Listing Workspace provides a manual draft packet.
  Current policy source is linked in DEF-MER-001. Focused extension tests 8
  passed (45 deselected); marketplace routing 11 passed; Next build and
  `git diff --check` passed. Not deployed. No service restart or remote Mercari
  request was made. Active, already claimed jobs have not been altered.
- INV-003 offline scanner command normalization: local packing UI recognizes
  plain and namespaced command labels while offline. Packing interaction tests
  2 passed and backend scanner dispatcher tests 3 passed; not deployed. No
  physical device or browser offline/reconnect rehearsal was performed.
- INV-004 scanner package/label workflow: local backend now creates durable
  tenant-scoped packages on `NEW PACKAGE`, saves the active package in the
  workstation checkpoint, and queues one idempotent thermal label job on
  `PRINT LABEL` or explicit `REPRINT LABEL`; missing package is a visible
  blocker. Backend scanner tests 7 passed and packing offline interaction
  tests 4 passed. Frontend `npm run build` passed with existing lint/noVNC
  warnings. Not
  deployed; no service restart, physical scanner rehearsal, or printer
  verification.
- INV-005 scanner tote workflow: `NEW TOTE` persists an awaiting-code state;
  next scan creates or selects a tenant-owned tote and updates the checkpoint
  without treating the tote ID as an inventory barcode/Slate boundary. UI
  displays the next-scan cue while offline. Backend scanner dispatcher tests
  6 passed; offline interaction tests 3 passed; frontend production build
  passed with existing lint/noVNC warnings.
  Not deployed; no physical HID/NFC or lookup test.
- PUB-010 Trading request evidence: all after-submit error branches for eBay
  `AddFixedPriceItem` now persist `method=POST`; targeted rejection and
  crosspost-serializer tests 2 passed, `py_compile` and `git diff --check`
  passed. Not deployed. Current read-only eBay Taxonomy review for the 11
  deterministic batch failures produced candidates/evidence only; details and
  unresolved product-fit/parcel blockers are in the defect ledger. No
  category, condition, price, package, offer, or listing was changed.
- INV-006 scanner lookup: `FIND ITEM` followed by a scan calls the private
  tenant-scoped location lookup, checkpoints the result, and never creates an
  item/Slate boundary. Offline query scans remain in the durable journal until
  synchronization. Backend scanner tests 7 passed; offline UI tests 4 passed.
  Frontend `npm run build` passed with existing lint/noVNC warnings. Not
  deployed; no production inventory or hardware verification.
- Offline workstation now distinguishes recognized server-side actions
  (package/tote/find/label) from genuinely unimplemented commands; queued
  server actions are reported as pending sync, not falsely described as
  unimplemented. Explicit REPRINT LABEL queues a separate idempotent job.
  Targeted backend suite 7 passed, frontend interaction suite 4 passed,
  eslint has 0 errors (one existing unnecessary-dependency warning), build
  passed. Not deployed or physically printer-verified.

## 2026-10-19 REV-008 readiness/shipping setup local-only increment

- Canonical readiness now reports missing measured parcel data as
  `shipping_setup_required` with a human-action warning; otherwise complete
  drafts may be reviewed but are not publishable without the parcel facts.
  Unknown recovery items without source facts are not mislabeled as machine
  price research.
- Full canonical readiness + listing aggregate suites: 62 passed. Targeted
  eBay operations preview provenance tests: 2 passed; relevant compile and
  diff checks passed.
- Not deployed. No service restart, migration, database write, or marketplace
  revision. Production behavior remains unverified. Rollback is limited to the
  local changes in `canonical_readiness.py` and its focused test if release
  review rejects the contract.

## 2026-10-19 INV-007 scanner MOVE ITEM local-only increment

- Added `POST /inventory/stock/move` for explicit movement between locations
  owned by the authenticated operator (totes and open packages). It rejects
  foreign/unknown locations, closed-package source/destination use, reserved or
  insufficient stock, and duplicate-operation payload mismatches; source and
  destination stock are updated in one transaction and an inventory event is
  recorded.
- Packing workstation's `MOVE ITEM` command arms the next scan as a lookup,
  avoids creating a Slate, then presents a confirmation control requiring one
  unambiguous product, eligible source, active destination tote or package,
  and quantity.
  Offline replay keeps the movement selection result in the durable checkpoint;
  the stock mutation itself requires a connected explicit confirmation.
- Validation: package + scanner dispatcher API tests `15 passed`; Python
  compile and `git diff --check` passed; packing interaction suite `5 passed`
  with explicit-confirmation/no-Slate package-destination coverage; targeted
  ESLint reported 0 errors and one existing hook-dependency warning.
- Not deployed, browser/device tested, or production inventory verified. No
  database migration, live inventory operation, service restart, or publication
  occurred. Rollback: after reviewing release scope, revert only the INV-007
  source/test/UI changes; no production data rollback is needed because this
  increment was not deployed.
# 2026-10-19 — INV-008 scanner BUNDLE capture, local only

- Source revision: dirty, uncommitted worktree; no release artifact/commit.
- Files: `backend/app/api/inventory.py`, `backend/tests/test_scanner_dispatcher.py`, `frontend/pages/packing.js`.
- Migration/database/service state: no migration, production DB write, service restart, or deployment.
- Verification: bundle + move API tests 2 passed; frontend packing offline interaction suite 5 passed; no physical scanner or production verification.
- Rollback: review/revert only the INV-008 hunks in those files; no deployed-data rollback required.
- Release blocker: review-only grouping is not sellable or inventory-accounted.

### 2026-10-19 — PUB-009 row 2238 research evidence (no deploy)

- Read-only official manufacturer source: https://www.dahkee.com/en/product/DK_Alternator-11273.html. It lists DK 11273 as 12V/150A, Mitsubishi mount/S6 pulley, with 11273N as an OEM cross-reference.
- The source itself conflicts between narrative and tabular Lincoln MKS/MKZ engine assignments. Exact failed item identity remains unverified; no fitment claim, price recommendation, or category correction was made.
- No source release, deployment, service restart, DB mutation, or eBay write. Failed-batch retry remains disabled pending physical identity, pricing evidence, parcel measurement, category/condition review, and explicit item-level authorization.

### 2026-10-19 — REV-003 domestic policy preview correction (local only)

- `backend/app/api/ebay.py` now reads domestic fulfillment services from the DOMESTIC option rather than flattening international/domestic services; preview output includes carrier and configured domestic service options. `backend/app/services/ebay_operations_preview.py` preserves carrier/options in the row.
- Regression fixture places an international UPS service before domestic USPS and verifies the preview still reports domestic calculated USPS with buyer responsibility. `backend/tests/test_ebay_operations_preview.py`: 11 passed.
- Settings UI displays carrier and multi-service domestic options; CSV includes carrier and option list. `frontend/lib/ebayOperationsReport.test.mjs`: 2 passed; targeted ESLint: 0 errors and one pre-existing `reload` hook dependency warning.
- Not deployed; no service restart, live provider write, or production state change. Release still awaits worktree/source review and operator live-action approval for any changes.
- Current source-control recheck: 2,175 untracked paths, including active source/tests/migrations plus data and generated artifacts; 138 modified Git entries. See `docs/POSTERPRO_SOURCE_CONTROL_AUDIT.md`. No staging/commit was performed; production is not reproducible from this mixed worktree yet.

### 2026-10-19 process/source revision recheck (read-only)

- `posterpro-backend.service`: active, `WorkingDirectory=/opt/apps/posterpro/repo/backend`, process started 2026-10-18 12:25:04 EDT (16:25:04 UTC).
- `posterpro-frontend.service`: active, `WorkingDirectory=/opt/apps/posterpro/repo/frontend`, started 2026-10-18 12:24:31 EDT (16:24:31 UTC).
- `posterpro-worker.service`: active, backend working directory, started 2026-10-14 15:22:05 EDT (19:22:05 UTC).
- Active checkout at inspection: branch `codex/cms-workspace-theme`, HEAD `292c80f`, extensive dirty/untracked files. The processes do not expose a trustworthy loaded source commit, and backend predates the REV-003 domestic-option correction; it is therefore not deployed/production-verified. No restart was performed.

### 2026-10-19 urgent frontend asset recovery

- Symptom: operator reported unusable, visually broken site. Root cause verified: frontend process HTML referenced build ID `-_ARyL_X_jEqenk2UrLOT`; current `frontend/.next` had been replaced by a different build. Referenced Listings and `_app` chunks and CSS returned Next.js 400 error documents; these missing assets explain absent styles/hydration.
- Action: restarted only `posterpro-frontend.service`. Its configured `ExecStartPre=npm run build` succeeded and the service started at 2026-10-19 05:33:36 UTC. Build ID now `Mspxrnyfm4AVG2tUxP8ii`. Backend and worker were not restarted. No DB/listing/inventory/provider changes.
- Verification: local shell routes `/`, `/app`, `/listings`, `/settings`, `/packing`, `/inventory`, `/intake` all HTTP 200; every JS/CSS asset referenced by local `/listings` HTTP 200; public `/listings` assets through Caddy HTTP 200; public `/` HTTP 200; all three services active.
- Source version: current dirty worktree, HEAD `292c80f`; no release commit/artifact and frontend source hash not separately captured. Deployment is therefore asset-coherence verified but not reproducible-release verified. Build emitted existing lint/Browserslist/noVNC warnings and reported compile success.
- Operator verification still needed: hard-refresh authenticated session, confirm layout/hydration and relevant actions. If display remains wrong, capture route + screenshot/browser console to diagnose design/runtime defects. Rollback: repeat service build/start from a separately verified prior source/artifact; do not assume the dirty tree is a safe rollback.
