# PosterPro implementation progress

## 2026-10-18 master recovery baseline

- Verified the active deployment is `/opt/apps/posterpro/repo`, not the outer
  workspace copy. Backend, frontend, Celery worker, and Celery beat are active;
  backend health is database-ready.
- Performed read-only operator (user 2) eBay baseline: last fresh remote
  snapshot contains 162 active offers; local mapped eBay rows contain 254,
  requiring reconciliation before any live bulk change. Of the local rows, 37
  are below $25, 36 have inconsistent/free or seller-paid shipping, zero have
  measured non-estimated safe parcel facts, and all 254 lack stored handling
  time. No live changes were made.
- Located latest failed 25-item eBay batch `approve-09df…`; failures are mixed
  category/condition rejection, Inventory API requirements (25002/25005), and
  eBay system error 25001. Facebook has current form-required-field and
  publish-action failures; Mercari historical state includes false `COMPLETED`
  records with unresolved fields.
- Added the four durable recovery ledgers and began the revenue-safe shipping
  increment. The new policy service defaults under-$25 rows to buyer-paid,
  calculated USPS Ground Advantage and refuses to treat title-derived parcel
  estimates as calculated-rate evidence.

- Continued remote eBay read: 162 active offers and 24 below $25. Exact
  Trading detail reads show all 24 are free, calculated `USPSParcel`; no
  package dimensions or handling time was returned. The new eBay settings
  preview exposes measurement, approved-alternative, and explicit
  deactivation options without submitting a revision.
- Corrected above-threshold semantics to preserve existing explicit shipping
  choices/require setup rather than assume free shipping. Intake fallback no
  longer seeds `$24`; fallback-only research is not publication-price evidence.
- Read the actual durable records for all 25 eBay failures. The batch has 25
  failed child jobs and zero completed operations. Exact required data is now
  known: listing `3440` lacks `Product`; `3425` and `3407` lack
  `Original/Reproduction`. Trading rejection rows also name invalid category,
  inapplicable condition, and missing package weight. The 14 Inventory API
  `25001` failures are correctly persisted as `REMOTE_STATE_UNKNOWN`; no
  retry has been queued.
- Added `GET /ebay/failed-batches/{batch_id}/recovery-preview`. It is a
  tenant-scoped, read-only operator report backed by the durable batch/job
  records. Each row shows retained provider error evidence, named required
  fields, classification, corrective action, and `retry_eligible=false`; it
  cannot create offers or queue a retry. Focused recovery/operations-preview
  tests: `3 passed`.
- Authenticated read-only reconciliation rechecked all 14 retained `25001`
  offer IDs with bounded current Inventory API GET calls. Every exact offer
  request still returned HTTP 500 / `25001`; none yielded a remote SKU or
  listing ID. The ambiguous branch remains blocked at the provider, and the
  system correctly leaves all 14 out of retry eligibility.
- Read-only cached preflight of the other 11 failures found a second systemic
  defect: it returned zero blockers even while retaining implausible category
  choices (RV LED fixture → insect traps; scale labels → stickers; UTV
  windshield → snowmobile windshields). These are not cleared for retry;
  semantic category validation is now an explicit repair dependency.
- Added conservative eBay semantic guards for those concrete collisions and
  fixed the Motors preflight condition that could bypass an existing semantic
  revalidation flag. Focused taxonomy tests (`8 passed`) and a read-only live
  database preflight now block all three demonstrated examples with
  `CATEGORY_REVALIDATION_REQUIRED`. No category was auto-edited or published.
- Facebook production diagnostics: device 71 is an old `0.3.69` installation,
  revoked and offline; it is not evidence that the `0.3.70` source manifest
  was installed. Historical job 193 chose Facebook Furniture for an inkjet
  printer and blocked before submit because price `59.90` persisted as `60`.
  Live browser CREATE eligibility is now restricted to the exact reviewed
  current extension version; diagnostics stay available to older builds.
  Focused extension-version tests: `2 passed`. No Facebook action was queued
  or submitted.
- Read-only quality audit found 961 `media_inventory_recovery` (Google
  timeline-derived) rows. Sampled rows contained OCR fragments/generic titles
  and legacy `$17.99`-style prices without pricing provenance. The canonical
  evidence gate now treats this source as photo intake and requires real
  comparable/source or operator pricing evidence regardless of the fallback
  amount. Sample rows now remain Processing/Needs Attention with the explicit
  pricing-research blocker. Focused pricing evidence tests: `6 passed`.
- Dashboard-agent safety: the existing reprice parser cannot yet construct the
  specification's Monday/odd-day/cross-marketplace operation. It now returns
  an explicit unsupported-constraint response with zero eligible listings for
  that request instead of silently turning it into an eBay-only price change.
  Focused command tests: `2 passed`.
- Mercari diagnostics: direct database inspection found 10 historical user-2
  `DIAGNOSTIC` jobs marked `COMPLETED` despite
  `REQUIRED_FIELDS_UNRESOLVED`, no external identity, and no submission. New
  incomplete diagnostics now become `BLOCKED_EXTERNAL` while preserving their
  provider error code; this is local code only and does not reclassify the
  historical evidence. Focused extension transport tests: `3 passed`.
- eBay shipping/handling preview: the read-only operator result now reports
  local/remote identity reconciliation explicitly and marks a bulk remote
  revision unsafe whenever an active remote offer is unmapped or a local eBay
  mapping is absent remotely. This addresses the verified 162-remote versus
  254-local discrepancy without modifying either side. Focused preview tests:
  `3 passed`.
- Google Photos recovery creation: newly reconstructed timeline candidates no
  longer start with `needs_review=true` or a seemingly usable generated price.
  They begin in `research_pending` with an explicit pricing-evidence blocker
  and an ineligible recommendation marker, then require real research or an
  operator price before promotion. Focused recovery creation tests: `3
  passed`.
- Dashboard agent: implemented the specified Monday/odd-day/cross-marketplace
  price-reduction request as a read-only structured preview. It uses a stored
  remote eBay publication timestamp (not local creation time), requires active
  eBay and Facebook identities plus current remote prices, applies 5%/3%
  rounding independently, and excludes missing evidence. It remains approval
  only; no marketplace call or price mutation occurred. Operator-command suite:
  `23 passed`.
- Scanner workstation: added the required compact `NEXT ITEM` and `BUNDLE`
  command labels to the shared HID/camera/manual registry. `NEXT ITEM` clears
  only the durable current-item checkpoint, preserving tote and quantity;
  legacy `FINISH ITEM`/`START BUNDLE` labels remain compatible. Focused
  scanner dispatch/API tests: `3 passed`.
- Free-form box QR support: intake now preserves non-PosterPro decoded QR text
  as internal-only provenance with a bounded searchable association suggestion
  and required operator confirmation. It does not create a Slate, merge stock,
  or promote the QR image to a customer-facing listing asset. Focused intake
  test: `1 passed`.
- Free-form box QR lookup: added an authenticated, tenant-scoped read-only
  inventory suggestion endpoint for descriptive payload text. It returns only
  owned product candidates, marks `automatic_merge_performed=false`, and
  requires operator confirmation before any later association. Focused Find
  inventory tests: `3 passed`.

## Active work

- [~] Milestone 1: private totes, manifest preview/import, idempotency, and per-entry reconciliation.
- [ ] Manifest UI/history/retry regression suite.
- [~] Scanner dispatcher and packing workstation.
- [~] Offline IndexedDB journal and synchronization.
- [~] Package/label adapter expansion, Find mode, URL aliases.
- [~] Full photo/voice reconciliation and provider validation.

## 2026-10-17 sustained implementation run

This run continues the existing additive unified-inventory worktree; it does
not reset or replace the deployed intake, Vine, Slate, marketplace, or legacy
inventory paths. The repository has substantial pre-existing uncommitted work,
so all changes are being scoped narrowly and validated against the active
models, migrations, and routes before integration.

- [~] Manifest workflow: API foundation is present (tote creation/listing,
  plaintext upload/preview/import, scoped history, idempotency, and terminal
  reconciliation decisions). Operator workflow, source-location conflict
  selection, history/totals, and regression coverage are now automated-tested.
- [~] Scanner/workstation: command dispatch, durable event journal/checkpoint,
  HID/Bluetooth/manual/camera input surface, and command-sheet endpoint are
  automated-tested. Physical scanner and camera tests remain outstanding.
- [~] Offline, NFC, and labels: durable IndexedDB journal/state/export and
  intake-only PWA cache/replay are automated-tested. Hardware NFC/printing and
  offline browser-device rehearsal remain outstanding.
- [ ] Product/media/voice reconstruction, enrichment quality gates, and
  marketplace end-to-end reliability follow those dependency integrations.

Current verification rule: builds and automated tests are recorded separately
from authenticated browser, physical printer/scanner/NFC, and provider tests.
No live marketplace publication is authorized by this implementation run.

### 2026-10-17 verification checkpoint

- Live database schema read-only verification found all unified inventory and
  reconciliation tables plus the required `container_uuid`,
  `parent_location_key`, `request_fingerprint`, and `reconciliation_json`
  columns. No migration was needed in this checkpoint.
- Combined focused tests: backend manifest/scanner `4 passed`; frontend
  manifest/offline interactions `4 passed`.
- Frontend production build passed with existing lint/image warnings.

### 2026-10-17 package, physical-stock, and capture checkpoint

- Added real private package containment: nested packages reject cycles, moves
  lock/retain reserved stock, and repeated operations are idempotent.
- Package labels now have genuine Code 128 Set B (including checksum/STOP and
  quiet zones), QR private lookup, 4 × 6, 54 × 100 mm, and 58 mm rendering,
  a durable local print queue, browser printable/reprint path, and configurable
  copies. NFC tote bulk creation returns programming instructions but never
  falsely claims a tag was written.
- Applied additive production migration
  `20261017_inventory_packages_labels.sql` after backup
  `posterpro-before-package-labels-20261017T162715Z.sql.gz`; it adds nullable
  `inventory_print_jobs.package_id` and its index only.
- Added authenticated Find My Item lookup for barcode/product, package, tote,
  and NFC identifiers. It returns explicit found/not-found states and only
  resolves locations owned by the authenticated operator.
- Added durable, idempotent post-boundary Slate voice attachments. Original
  audio, transcript, notes, structured intelligence, and bounded history are
  retained without duplicating offline retries.
- Marketplace preflight now blocks deterministically linked listings when
  unreserved physical stock is insufficient. Confirmed sales deplete linked
  stock once and write an inventory event; no live publish was invoked.
- Expanded the offline worker to the Packing workstation. It remains
  network-first and capture-route-only; disconnected browser/device rehearsal
  and physical hardware checks are still required.
- The packing screen now creates a durable Slate capture boundary after a
  barcode, attaches recorded WebM audio through the idempotent voice endpoint,
  and inserts device-camera photos into that Slate's timeline window. Frontend
  rebuild/restart completed and `/packing` returned 200.
- Verification: package/find focused tests `6 passed`; package/manifest/scanner
  tests previously `8 passed`; voice route tests `3 passed`; physical-stock
  marketplace suite `37 passed`; frontend build completed with existing
  warnings. Production smoke: backend health 200 and `/packing`,
  `/inventory/packages`, `/inventory/find`, and `/inventory/manifests` all 200.

### Immediate continuation

- Add offline replay for packing-created Slate/media actions, then exercise a
  real browser-offline reload/reconnect (scanner journal/state persistence is
  already available; media uploads still require an online retry).
- Continue photo timestamp calibration/association and original-media import
  validation; retain manual correction as the authoritative fallback.
- Complete printer-bridge claim/retry integration and physical scanner/NFC/
  thermal-printer verification without representing unavailable hardware as
  tested.

### 2026-10-18 timeline Slate reliability repair

- Fixed the UI transaction boundary that caused committed Slate insertions and
  group-marking mutations to be reported as failures when the follow-up
  timeline refresh timed out or returned an error.
- A successful mutation now remains visible/acknowledged; refresh failure is
  reported separately as pending rather than removing the optimistic Slate or
  claiming the group mark failed.
- Added a regression interaction test for a committed Slate with a failed
  refresh. Timeline interaction suite: `4 passed`.
- Rebuilt/restarted the active frontend and backend. `/intake/timeline` and
  `/packing` both return 200; backend health reports `database_ready: true`.

## Verified foundations

- Active deployment is Next.js → Caddy → FastAPI/Celery/PostgreSQL.
- Existing `inventory_bins` remains compatible; no live legacy tote records existed at the adoption audit.
- Physical stock, packages, products, events, manifests, and reconciliation decisions use additive migrations.
- NFC/QR lookup requires authentication and owner authorization.

## Safety / verification notes

- Do not claim hardware printing, scanner hardware, offline browser persistence, or live marketplace publication without device/provider verification.
- Run focused database-state tests before deploying inventory mutations.

## Exact continuation checkpoint

1. Add a read-only, operator-visible controlled retry-preflight report for
   batch `approve-09dfdc7ccb9d4f6088f0883365ac8c89`. It must classify the
   existing provider response per listing and prohibit a retry for any
   `REMOTE_STATE_UNKNOWN` record until exact offer/SKU reconciliation is
   successful. **Completed locally; not deployed.**
2. **Implemented locally, unqueried against the live batch:** recovery preview
   now performs read-only current taxonomy/aspect calls for the 11
   deterministic failures and displays their per-item evidence in Settings →
   eBay. It intentionally does not claim category-specific condition validity;
   the supported current provider response has not been verified live.
3. Run the deployed recovery preview against the saved batch after an isolated
   release is reviewed; verify exact current taxonomy evidence and retain all
   14 `25001` outcomes as unknown. Do not issue a publish request.
4. Keep revenue preview, recovery-preview and evidence-gate changes undeployed until an isolated
   review/deployment increment is ready. No remote shipping, handling, pricing,
   publication, or inventory changes have been authorized.

### 2026-10-18 operator shipping-policy settings increment (local only)

- Added persistent, tenant-scoped workflow preferences for shipping-policy
  enablement, currency, preferred carrier/service, calculated versus flat
  buyer-paid rate, profitability floors, promotions, and negotiated offers.
  Explicit null clears an optional profitability floor; omitted fields preserve
  the existing operator preference.
- The Settings → Workflow UI now exposes those controls. Its at/above-$25
  default remains **preserve existing / require setup**; it does not imply or
  assign free shipping above the threshold.
- Central policy normalization now maps the persisted workflow preference names
  to the shared policy service. A disabled low-price policy preserves an
  existing setting rather than applying the under-threshold rule.
- Verification: `pytest backend/tests/test_auth.py
  backend/tests/test_ebay_operations_preview.py -q` → `12 passed`.
  `frontend npm run build` completed after the one new JSX lint correction;
  repository-wide pre-existing lint/module warnings remain.
- Not deployed. No operator setting in production, local listing, remote eBay
  offer, shipping policy, handling time, price, or publication state changed.

### 2026-10-18 full active handling evidence in revenue preview (local only)

- The `/ebay/operations/shipping-handling-preview` route now reads the current
  eBay fulfillment-policy catalog and associates Inventory offers by exact
  `fulfillmentPolicyId`. It records handling-time and shipping-policy evidence,
  policy identity, and the number of active offers using each shared policy in
  the snapshot.
- Active listings without a resolvable fulfillment-policy ID receive an exact
  read-only Trading `GetItem` inspection regardless of price. Provider errors
  are preserved as per-listing `unavailable` evidence; no missing handling
  value is inferred.
- The preview keeps current handling days, requested +1 business day, proposed
  result, evidence source, supported-value status (still unknown), and shared
  policy effects separate. The dashboard renders each under-threshold shipping
  blocker and every active listing's handling before/after row.
- Verification: focused `test_auth.py`, `test_ebay_operations_preview.py`,
  and `test_ebay_service.py` → `47 passed`. Frontend production build completed
  successfully; existing lint/module warnings remain. No deployment or live
  marketplace mutation occurred.
- Exact live policy/handling values remain unverified by this local test. The
  24 live under-$25 offers remain approval-gated; parcel measurement and remote
  identity reconciliation are still unresolved.

### Exact continuation checkpoint

1. **Completed locally, not queried against the live batch:** the recovery
   preview checks current leaf/aspect metadata for the eight category/condition
   and three required-data failures. Numeric categories without stored tree
   provenance are checked only if exactly one synchronized local taxonomy tree
   owns the ID; otherwise they are blocked. Condition compatibility remains
   unverified.
2. Preserve all fourteen 25001 rows as `REMOTE_STATE_UNKNOWN`; do not retry
   until exact remote offer/SKU reconciliation succeeds.
3. Keep shipping/handling and recovery preview changes local until a clean,
   reviewed release increment is assembled. No live shipping/handling, pricing, publishing,
   deactivation, or inventory changes have been authorized.

### 2026-10-18 failed eBay batch taxonomy/recovery UI (local only)

- The recovery endpoint for batch
  `approve-09dfdc7ccb9d4f6088f0883365ac8c89` now reads live eBay leaf and
  required-aspect metadata for the eight category/condition and three
  required-data failures. It compares provider-required fields with the saved
  listing specifics and records source, check time, category/tree, leaf state,
  and fields still missing. A category's current condition support remains
  explicitly unverified because this aspect response does not establish it.
- All fourteen `25001` rows stay excluded from taxonomy retry logic and remain
  `REMOTE_STATE_UNKNOWN`; every row reports `retry_eligible: false`.
- Settings → eBay now renders all 25 rows and the taxonomy fields. Client
  request timeouts were lengthened to accommodate bounded read-only provider
  inspection (60 seconds for the 11-row report, 180 seconds for full active
  handling reconciliation).
- Numeric categories without saved tree provenance are checked only if the
  synchronized local taxonomy maps the ID to exactly one eBay tree; otherwise
  the report blocks before making a provider query.
- Verification: recovery endpoint tests `3 passed`; combined eBay failure,
  shipping preview and eBay service tests `42 passed`; backend compile passed;
  frontend production build completed. Taxonomy API results used in tests were
  fixtures. No live recovery-preview query, deployment, retry, or provider
  mutation occurred.

### 2026-10-18 live eBay snapshot reconciliation and recovery improvement

- Fresh authenticated read-only eBay active-offer snapshot returned 162 active
  offers; 24 are below $25. A full shipping/handling preview inspected all 162.
- All 24 under-$25 offers currently use free/seller-funded calculated
  `USPSParcel`. All are blocked from safe Ground Advantage revision because
  packed dimensions are only estimated/approximate, not measured; exact remote
  reads cannot establish shared-policy effects for legacy listings. Preview
  presents safe next steps (record measurements, choose an approved supported
  alternative, or prepare deactivation for operator approval).
- Remote handling reads returned 0 business days on 161 offers and 1 day on
  one offer. Proposed values are 1/2 days. Every row remains blocked pending
  account/category supported-value and shared-policy verification. No remote
  change was made.
- Fresh active snapshot exact-SKU reconciliation found no active exact match
  among the fourteen `25001` failures. This does not rule out inactive/draft
  remote state; exact-offer reads still fail with `25001`, so retries remain
  prohibited.
- Added per-row exact-SKU active reconciliation to the read-only failed-batch
  preview and displayed the result in Settings → eBay. Tests:
  `backend/tests/test_ebay_failure_recovery.py -q` → 4 passed; frontend
  production build passed with existing warnings. No deployment or service
  restart.
- Runtime observation: repository HEAD `292c80f`; backend/frontend started
  2026-10-18 12:25 EDT, worker/beat 2026-10-14 15:22 EDT, all active; backend
  health is database-ready. The 196-entry worktree remains dirty/untracked;
  runtime source parity with HEAD is not established.

### Exact continuation checkpoint

1. Do not retry or mutate live eBay listings. Keep all fourteen `25001` jobs
   unknown; active exact-SKU non-match is not enough to make retry safe.
2. Build a reviewed, isolated source release from selected files only; inspect
   `git status` and exclude generated/private artifacts. No broad commit or
   service restart.
3. Continue source work on Google Photos evidence research and readiness, which
   can proceed independently of live eBay authorization; add a representative
   end-to-end test proving unsupported price evidence stays out of Needs Review.
4. Separately resolve supported handling-time choices/shared policy scope and
   measured packed dimensions. Prepare exact per-listing previews only.
5. After that, resume deterministic eBay failure taxonomy/condition repairs,
   Facebook extension diagnostics, Mercari supported workflow, dashboard agent
   operations, and scanner/fulfillment integration.

### 2026-10-18 timeline-derived draft quality sample (read-only)

- Evaluated the five newest user-2 `media_inventory_recovery` DB rows with the
  current local `canonical_listing_readiness` implementation. All five remain
  in PROCESSING or NEEDS_ATTENTION, not Needs Review. Every sample had the
  evidence-backed pricing blocker; four carried `category_model_estimate`
  provenance with zero comparable evidence. Other blockers included generic
  identity, generic/unvalidated category, incomplete description, and missing
  destination preflight.
- This checks local code against existing production DB records only. It is
  not a live frontend/deployed-gate verification and no research source was
  queried. No row changed.
- Highest unblocked continuation: trace how external comparable research is
  invoked/stored, add a permitted research adapter and provenance-preserving
  tests, then run representative diverse item groups. The five-sample read
  shows the gate is conservatively blocking low-quality drafts, but does not
  supply the missing product identification or pricing evidence itself.

### 2026-10-18 category-estimate pricing gate correction (local only)

- Found a gap in `PricingResearchService`: fallback-only prices were marked
  ineligible, but `category_model_estimate` (including values such as 21.99)
  still returned `recommendation_eligible=true` without comp/source evidence.
- Changed the rule so only sold comparables, active comparables, source-retail,
  or a documented operator price can be publication-ready evidence. Category
  model and generic fallback estimates retain provenance and explanatory
  amounts for diagnostics but are explicitly blocked from recommendation use.
- Added regression assertions that category-model prices cannot pass the
  marketplace readiness validator. Focused pricing suites:
  `backend/tests/test_pricing_evidence_gate.py backend/tests/test_pricing_research_service.py -q`
  → 20 passed.
- Found and closed the connected apply-path gap: the authenticated
  `/pricing/recommendations/{id}/apply` endpoint could apply an ineligible
  estimate. It now requires an explicit positive operator-entered amount plus
  a reason when research is insufficient, records the manual-price provenance,
  and refuses silent local price changes for already live-published items.
  The schema and route regressions are included in the same run; updated total:
  21 passed.
- Updated the Listing pricing panel to label estimate-only output as
  non-researched and expose a documented operator-price entry path; it hides
  the regular “apply recommendation” buttons while evidence is insufficient.
  Frontend production build passed with existing lint and noVNC warnings.
- No deployment. Exact next task remains adding a permitted real comparable
  research adapter and evidence capture, then prove the diverse timeline
  cohort can generate research-supported drafts.
- No data rows changed; no provider or deployment action occurred. The five
  actual intake samples remain blocked.

### 2026-10-18 live failed-batch preview and retained-response parser repair

- First direct read-only preview crashed before provider calls because five
  saved `provider_response.errors` values were strings; `_required_fields`
  treated each character as a dict. The durable records were not modified.
- Hardened the parser for JSON-encoded error strings, unstructured strings,
  dicts and lists; added parser regression coverage. Recovery suite now reports
  `5 passed`, compile passed. Combined failed-batch/operations-preview/eBay
  service suites report `44 passed`.
- Re-ran the same read-only operation after the parser fix against the current
  provider and database: all 25 rows returned; 11 deterministic rows had live
  taxonomy/aspect check status `confirmed`; all 14 `25001` rows had
  `no_active_exact_sku_match`; zero rows were retryable. The latter does not
  resolve inactive/draft remote offers, and previous exact offer GETs still
  returned `25001`.
- Result came from local code calling the live provider; new Settings UI/API
  remain undeployed. No publication, retry, revision, or data mutation ran.
- Existing services remain untouched; deployment source parity remains
  unknown. No broad commit or restart was performed.

### 2026-10-18 local/remote publication identity clarification

- Fresh authenticated eBay read and direct local DB join confirmed 162 active
  remote offer IDs. Every active remote ID exactly matches a local eBay listing
  ID; there are zero active remote offers without a local item mapping.
- The older “254 active local” description was inaccurate: 254 is the number
  of unique local eBay `MarketplaceListing` rows currently marked `PUBLISHED`.
  All 162 active remote offers map within that set, but 92 local rows marked
  PUBLISHED are absent from the active snapshot. They may be ended, sold, or
  otherwise inactive; no local status was changed based on absence.
- The 37 local under-$25 and 36 inconsistent counts are not the live risk
  population; the actionable remote population is 24 active offers below $25.
- Exact snapshot summaries: 254 unique local PUBLISHED IDs, 162 exact active
  remote ID matches, 92 local PUBLISHED IDs not in current active snapshot.

### 2026-10-18 live shared-policy and handling preview verification

- Updated exact `GetItem` parsing to capture `SellerShippingProfile` ID/name
  and added an active-snapshot policy-reference count in the dashboard preview.
  Official eBay Trading API documentation says the shipping profile ID is
  returned by GetItem when a seller business policy is attached.
- Regression tests cover XML extraction and policy counts across Inventory- and
  Trading-sourced rows (`2 passed`).
- Direct local-code preview against the live account (read-only) returned 162
  offers, 24 under $25. All 24 share a single returned shipping policy in the
  active snapshot. All remain blocked because packed dimensions are estimated
  or approximate, not measured. No live shipping change.
- Handling results: 161 at 0 days, one at 1 day; resulting values are 1 and 2.
  161 listings are on policies shared by multiple active offers; one listing
  uses a policy referenced once in the active snapshot. This does not include
  inactive/draft dependents, and exact supported choices remain unverified.
  No handling change.
- Next technical gap: fetch account/category-supported handling values and
  determine policy dependents outside active listings; do not mutate policies
  until the exact before/after impact is shown and explicitly approved.
- Integration regression run after these changes:
  `test_ebay_failure_recovery.py test_ebay_operations_preview.py test_ebay_service.py test_pricing_evidence_gate.py test_pricing_research_service.py -q`
  → 66 passed; targeted compileall passed. Frontend build passed after its
  latest relevant UI change. Services remain untouched and deployment is not
  established.

### 2026-10-18 timeline pricing-research integration increment

- Replaced the manual-only external pricing refresh persistence with a shared
  service and wired the existing Celery beat/worker to process at most five
  unreviewed `intake_head_slate` drafts every 15 minutes.
- The worker stores query, provider/source, research timestamp, active asking
  count and sold count, and the pricing analysis. It never changes any listing
  price or readiness state; Browse asks remain explicitly distinct from sold
  evidence. Insufficient-identity records retry no more than daily; other
  provider failures retry no more than every six hours.
- Changed files: `backend/app/services/external_pricing_research.py`,
  `backend/app/api/intelligence.py`, `backend/app/workers/tasks.py`,
  `backend/app/workers/celery_app.py`,
  `backend/tests/test_pricing_evidence_gate.py`, and the requirements,
  acceptance, defect, deployment, and progress ledgers.
- Targeted compile passed. `test_ebay_comparable_research.py`,
  `test_pricing_evidence_gate.py`, `test_pricing_research_service.py`:
  26 passed (167 existing warnings). Worker regressions verify the bounded
  selection, unchanged price/status/review state, and provider/identity retry
  cadence. An initial run exposed a test patch seam
  changed by service extraction; it was corrected to mock the shared service
  dependency, and the rerun passed.
- Not deployed; production worker/beat were not restarted or invoked. No
  listing price/status, marketplace record, or external listing was changed.
- Next: continue operator-visible research and draft readiness; provider retry
  backoff and no-mutation behavior now have worker regressions.

### 2026-10-18 handling capability and preview increment

- Added a read-only eBay Trading `GeteBayDetails(DispatchTimeMaxDetails)` call
  and connected its site-supported handling values to each handling preview.
  The preview distinguishes current value, requested +1, exact support, nearest
  higher supported alternative, and shared active-policy impact. Settings UI
  displays source/update time and no longer describes unsupported results as
  “not calculable.”
- Authenticated provider read returned `[0,1,2,3,4,5,6,7,10,15,20,30,40]`;
  metadata update time was `2022-09-24T00:09:32Z`. Previously verified current
  values were 161 active offers at 0 days and one at 1; resulting 1 and 2 are
  present in the supported set. This is not authorization to revise; 161 offers
  had shared policies in the prior snapshot, and inactive/draft dependents are
  unknown.
- The first fresh full-preview attempt exposed sequential per-item remote
  inspection latency and was interrupted without mutation. Replaced that path
  with paginated Trading `GetMyeBaySelling` snapshots and policy resolution.
- A subsequent complete local-code live preview finished in about 6 seconds:
  162 active remote offers, 24 below $25, zero remote active IDs unmapped;
  handling is 161 at 0 and one at 1, with all +1 results supported. All 24
  under-$25 offers are seller-paid/free calculated `USPSParcel` and use one
  policy referenced by 161 active listings. A second policy has one active
  reference. Inactive/draft policy use remains unenumerated and is surfaced as
  a blocker. The local `Listing.ebay_listing_id` denominator is 353 (191 not
  active), distinct from the earlier 254 `MarketplaceListing` denominator
  (92 not active); no status was inferred or changed.
- `test_ebay_service.py` + `test_ebay_operations_preview.py`: 42 passed;
  compile passed. `cd frontend && npm run build` passed with the existing
  React-hook, image, stale Browserslist, and noVNC top-level-await warnings. No
  service restart or deployment.
- Official guidance directs integrations to `GeteBayDetails` for
  `DispatchTimeMaxDetails`: https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/shipping-handling.html
- Next: implement efficient bounded remote policy inspection and establish
  exact inactive/draft policy dependencies before any approval proposal; then
  continue Google Photos research/readiness work. Sold-comp research and
  representative accepted timeline drafts remain unmet.

### 2026-10-18 approval-preview export increment

- Added a Settings → eBay CSV export for the current shipping/handling preview.
  It joins remote listing IDs across shipping and handling rows and includes
  current/resolved service facts, proposed shipping, parcel readiness/missing
  facts, policy reference counts, current/requested/result handling values,
  supported status, nearest alternative, and blockers.
- CSV cells are quoted and spreadsheet formula-like values are prefixed as
  literal text. Regression: `node --test
  frontend/lib/ebayOperationsReport.test.mjs` — 1 passed. An initial assertion
  expected the wrong column sequence; the test was corrected against actual
  CSV output, then passed. `cd frontend && npm run build` passed with existing
  warnings. No deployment or live state changes; browser download verification
  remains open.
- Latest full authenticated read-only preview (local code, not deployed): 162
  active offers, 24 under $25, 0 remote-active IDs unmapped. Current handling
  161 at 0 days and one at 1; all +1 values supported. Under-$25 offers all
  free/seller-paid calculated USPSParcel; one policy has 161 active references
  and a second policy has one. Policy use outside the active snapshot remains
  unknown. No listing/policy mutation.
- Next: resolve inactive/draft policy dependents and measured parcel facts for
  the urgent live shipping correction; then continue the other full-scope
  requirements. No live action is approved.

### 2026-10-18 eBay exact-SKU failed-write reconciliation

- Changed failed-batch recovery to use the already-supported paginated Trading
  active snapshot rather than hydrating the full Inventory catalog, then query
  `GET /sell/inventory/v1/offer?sku=<exact PosterPro SKU>` for active misses.
  This can reveal retained unpublished offers without treating an active miss
  as proof that no offer exists.
- Current authenticated local-code read for batch
  `approve-09dfdc7ccb9d4f6088f0883365ac8c89`: all 25 rows returned; the 14
  `25001` outcomes have zero active exact-SKU matches and each exact-SKU query
  returned HTTP 404 / provider error 25713 (“This Offer is not available”).
  The persisted error shows the `/sell/inventory/v1/offer/{offerId}` path and
  “A system error has occurred,” but historical telemetry omitted HTTP method,
  so it does not establish whether the failing request was a read or write.
  Root cause remains unconfirmed. No retry, create, revise, or publication
  was attempted.
- Recovery output now records provider status/error IDs and exact-SKU absence
  distinctly; retries remain false. eBay service + failed recovery tests:
  combined recovery, eBay-service, and marketplace-job-summary suites:
  `64 passed` (warnings only); compile passed. Recovery UI now displays
  exact-SKU result and retained request method (unknown for this historical
  batch). Frontend production build completed with existing warnings. No
  restart/deploy; recovery UI is not production-accessible yet.
- Next: trace why the 14 jobs targeted those retained offer IDs, run complete
  listing readiness validation on them, then construct an exact, approval-gated
  controlled retry preview. Resume shipping policy dependency reconciliation
  after this independent safe eBay increment.

### 2026-10-18 failed-batch category quality and retry readiness

- Expanded recovery to refresh current taxonomy/aspects for all 14 remote-unknown
  rows and attach a local eBay publication-readiness summary to each. Latest
  authenticated local-code preview: 14/14 current leaves confirmed, no required
  aspects missing, 0 active exact-SKU matches, and 14 exact-SKU offer lookups
  returned 404/25713. All rows remain non-retryable pending operator approval.
- Compared retained titles against taxonomy paths. Seven `25001` rows had
  absurd but provider-valid leaf mappings: RV water service panel → commercial
  booster water heaters; heated RV water hose → fire hoses; car wheel lock →
  alarm control panels; RV mattress topper → right-angle drills; nail clippers
  → cake flower nails; RV sconce → smart wall switches; golf-cart LED kit →
  insect traps. Added narrowly scoped semantic guards and a regression covering
  all seven; current live-code preview now reports all seven as
  `CATEGORY_REVALIDATION_REQUIRED` and the other seven are locally
  `ready_with_warnings`. These categories are not auto-corrected.
- Found 14 failed eBay mapping rows across the unknown cohort, all FAILED and
  none retaining a remote listing ID. Separate local state check: 14 eBay rows
  FAILED, 14 Facebook rows FAILED, and 1 Mercari row PENDING across 29
  MarketplaceListing rows. No status or mapping was changed.
- Focused semantic-suite first run found a too-narrow “LED light kit” token for
  the actual “LED Deluxe Light Kit” title. The predicate was broadened to the
  product-family phrase and its targeted regression passed. The subsequent live
  preview confirmed seven—not six—blocked rows. Final local validation:
  `test_ebay_taxonomy.py` + `test_marketplace_preflight.py`: 68 passed;
  `test_ebay_failure_recovery.py` + HTTP-method regression: 10 passed;
  targeted compile passed; frontend Next build completed successfully with
  existing lint/Browserslist/noVNC warnings. An earlier combined test process
  had its final summary lost while the shell session was being polled, so it is
  not counted; the focused suites above completed with captured exit status 0.
- A read-only local candidate-ranking check for the seven flagged products
  confirmed the current resolver still ranks implausible leaves for multiple
  cases (including medical-equipment lamps for an RV sconce and LED display
  arrays for a golf-cart light kit). No replacement category was selected or
  written; current taxonomy search plus operator validation is required.
- Next: prepare a per-item category correction and retry approval preview (no
  execution), then return to under-$25 shipping/handling policy dependency
  investigation. No category fix, retry, or live listing change is approved.

### 2026-10-18 failed-batch category research and tenant boundary (local only)

- Added a dashboard/API path to search current eBay category candidates for one
  failed-batch item, verifying candidate leaf status and retrieving current
  required aspects. UI requires operator selection; this path never changes a
  listing category. Taxonomy tree ownership must be unique or the endpoint
  returns a clarification blocker.
- Audited recovery preview data access and found child jobs/listings were
  queried by IDs without repeating owner scope. Added owner filters and a
  cross-tenant regression.
- Validation: `backend/tests/test_ebay_failure_recovery.py` — 12 passed;
  Next.js production build passed with existing warnings; `git diff --check`
  clean. No live endpoint result was captured, no production deployment/restart
  occurred, and no provider write was made.
- Current task: prepare the controlled eBay correction/retry preview and inspect
  adjacent recovery endpoints for tenant-safe access. Preserve the dirty tree;
  live shipping, handling, category, and publication mutations remain
  approval-gated.

### 2026-10-18 eBay category condition-policy evidence and exact field parsing

- Added a read-only Sell Metadata `get_item_condition_policies` integration,
  exposed required flag plus provider condition IDs/descriptions in failed-
  batch and candidate previews, and explicitly avoided inferred mapping from
  provider numeric IDs to PosterPro enums. The required-field parser now
  extracts exact names from provider messages and omits explanatory prose.
- Production-provider read-only replay of batch `approve-09dfdc7ccb9d4f6088f0883365ac8c89` returned 25 rows: 8 category errors (5 Trading responses also name condition dropped and package weight invalid/missing; 3 Inventory 25005 invalid category), 3 missing specifics (`Product`, `Original/Reproduction` twice), and 14 unknown 25001 outcomes. All 11 deterministic rows returned condition metadata; each of the eight category-error rows had `New` among current condition choices. This does not validate the failed category itself or resolve its provider rejection.
- Verification: recovery + eBay environment suites — 18 passed; targeted compile passed; frontend production build passed with existing warnings; `git diff --check` clean. No live edits, retry, deployment, or service restart.
- Next: use current eBay category candidates to prepare per-item correction proposals, capture actual required specifics and package-weight blockers, then prepare a controlled retry preview only after remote unknown outcomes are reconciled. Continue shipping/handling work in parallel; preserve the dirty worktree.

### 2026-10-18 read-only candidate research for failed eBay categories

- Queried current Production taxonomy suggestions (GET only; no category
  selection or write) for all eight saved category-failure titles. Candidate
  results and product-level review blockers are recorded under DEF-PUB-009 in
  `docs/POSTERPRO_OPEN_DEFECTS.md`.
- Clearer product-family suggestions surfaced for the UTV wiper kit, scale
  labels, UTV windshield, UTV signal kit, RV interior lights, and BMS board.
  Existing `Covers` (T-top shade) and `Alternators & Generators` categories
  remain plausible, but provider errors still called the saved categories
  invalid. Suggestions are evidence only; the first result is not accepted
  automatically.
- Current provider read confirms exact missing-specific fields: listing 3440
  `Product`; listings 3425 and 3407 `Original/Reproduction`. Five Trading
  failures still require actual package weight evidence; no weight estimate was
  substituted.
- Next: use current candidates in the per-item correction preview, then prepare
  exact approval details and a duplicate-safe retry plan only for validated
  rows. The 14 unknown outcomes remain unretryable pending reconciliation.

### 2026-10-18 block estimated parcel data in eBay publication (local only)

- Root cause inspection of the five failed Trading rows found numeric weight
  and dimension values with provenance `estimated_category_profile`; records
  also falsely carried `manual_measurement_needed=false`. The old eBay preflight
  only checked whether values were present and the payload serialized them.
- Central `estimated_parcel_fields` now recognizes estimate provenance, legacy
  estimated flags, and explicit estimated weight text. eBay preflight adds
  `EBAY_WEIGHT_UNVERIFIED` / `EBAY_DIMENSIONS_UNVERIFIED` for required estimated
  fields, and the payload builder omits them. This leaves those five listings
  blocked until measured packed values are recorded rather than retrying with
  fabricated facts.
- Added an operator-visible Listing Editor confirmation: “I measured the final
  packed parcel (not just the item).” Editing either weight or dimensions clears
  the confirmation; explicit confirmation marks present fields with
  `operator_measured_packed_parcel` provenance. Other estimates remain blocked.
- Validation: eBay service + marketplace preflight + shipping/handling preview
  suites — 77 passed; targeted compile and `git diff --check` passed; Next.js
  production build passed with existing warnings. No deployment, service
  restart, or retry. Existing local listing records were not modified.
- Next: browser-verify the new measured-parcel confirmation on an isolated
  draft, then prepare per-item category/aspect/weight remediation previews for
  the 11 deterministic failures, reconcile the 14 unknown offers safely, and
  continue the under-$25 live shipping/handling approval workflow.
- The under-$25 Settings → eBay preview now links every mapped row with
  unready parcel measurements directly to its listing editor. This remains
  local-only; Next.js production build passed with existing warnings, but no
  browser interaction/deployment was performed.

### 2026-10-18 expanded eBay correction preview, read-only production check

- The candidate endpoint now includes current synchronized category/path,
  saved condition/bucket and specifics alongside the selected candidate's
  current taxonomy version, required aspects, condition requirement/choices,
  and local shipping/readiness blockers. UI presents an exact current → proposed
  selection preview but never applies it or queues retry.
- Exercised the endpoint against failed production item 2744 through current
  local code. It returned stored `Adhesive Insect Light Traps`, five current
  suggestions, confirmed leaf/aspect/condition policy data, and
  `application_available=false`. The suggestions ranged from irrelevant to
  plausible, confirming why the operator must choose rather than auto-accept
  the first result. No category or item data changed.
- Validation: recovery/environment tests — 18 passed; targeted compile and
  `git diff --check` clean; frontend build passed. No deploy/restart or remote
  write. Next: extend this review to all eight categories and preserve exact
  blocker/choice evidence before any approval or retry proposal.

### 2026-10-18 all-eight category reconnaissance and price fallback correction

- Read-only current eBay taxonomy searches were run for all eight category
  failures. Their returned leaves and draft-data conflicts are recorded in
  `POSTERPRO_OPEN_DEFECTS.md`; examples include `Type=Backpack` for a T-top
  shade, `Type=Switch` for RV LED fixtures, and conflicting motorcycle/year
  fitment values for a UTV signal kit. Suggestions are not corrections; no
  candidate-specific aspects were fetched for all 40 leaves and no category,
  condition, specifics, listing, or provider state was changed.
- Traced unsupported fallback pricing to
  `pricing_research_service._category_fallback_baseline`: wiper $18.99, belt
  $21.99, automotive/general $24.99 (plus other category estimates). Though
  already marked ineligible, values populated `recommended_price`, quick-sale,
  floor, and stretch fields and the dashboard printed the non-null number.
- Changed unsupported amounts to an explicit `unverified_estimate` diagnostic;
  recommendation variants now remain null unless evidence is eligible. The
  dashboard shows “Price research needed” instead of a fabricated recommendation.
- Validation: `test_pricing_evidence_gate.py` — 6 passed. `test_listing_processing_backfill.py`
  integration pass — 33 passed, 8 failed at promotion/readiness assertions
  (including legacy Vine/recovery expectations); investigate the behavior
  before release. Frontend production build passed with existing warnings.
  No deployment, restart, or marketplace mutation. Next: reconcile the eight
  affected test expectations against the timeline readiness contract, then
  proceed with exact-aspect remediation and Facebook/Mercari diagnostics.
- Additional read-only required-field checks for failed listings 3440/3425/3407
  found current saved categories/aspects, but none explains the provider-named
  missing fields (`Product`; `Original/Reproduction` twice). Exact paths and
  the evidence mismatch are captured in the defect register. This remains
  unresolved: inspect retained request/response and listing API field mapping;
  do not inject guessed specifics or retry.
- Triage of representative broader-suite failures shows these assertions are
  not test-harness exceptions: a Vine fixture is blocked by an unverified leaf
  plus no connected eBay account, and photo-recovery fixtures are blocked by
  the explicit “Evidence-backed pricing research is required” gate. Those old
  tests expect Needs Review/retry despite these blockers. Do not weaken the
  gate; update tests only after documenting the intended queue contract and
  verifying a genuinely supported/approved source price path.
- Consumer regression check after withholding fallback recommendation fields:
  `test_pricing_evidence_gate.py`, `test_marketplace_preflight.py`, and
  `test_intake_head_slate.py` — 120 passed. This confirms null recommendation
  values are handled across the key publication/intake consumers; it does not
  resolve the eight older promotion/retry expectations or establish a deployed
  Google Photos draft cohort.
- Further trace found additional direct fallback paths: media-recovery used
  `$19.99` plus fixed estimated parcel dimensions/weight; ListingProcessing
  copied model/image `estimated_value` into asking-price fields; ListingAI
  supplied a default `$24`; PricingService wrote heuristic/LLM prices; eBay
  resolved missing prices to `$19.99` and could use `estimated_value`. These
  paths were changed: no fabricated recovery price/parcel, image estimates are
  provenance-only, legacy PricingService returns evidence-backed review-only
  suggestions without applying them, and eBay plan/preflight fails closed on
  unsupported evidence or no price.
- Regression hypothesis: those paths should not create or transmit an ask
  without source/operator evidence. Targeted eBay/preflight/recovery/AI/pricing
  set — 120 passed; compileall and `git diff --check` clean. A failed fixture
  pass was corrected by making the generic eBay test fixture explicitly
  operator-managed rather than incorrectly labeling it media recovery. No
  marketplace writes, deploy, or service restart. Legacy listing-processing
  suite still has 8 blocker-vs-old-promotion expectation failures (27 passed);
  they are not being waived.
- Read-only production DB audit completed: 962 `media_inventory_recovery` and
  404 `intake_head_slate` rows; all have some price field. Missing/ineligible
  pricing provenance occurs on 410/121 rows; known fallback values on 369/99.
  Local PUBLISHED status count is zero; local eBay crosspost mappings are 7/3
  with no remote ID or published marker. This does not prove remote absence.
  No records changed; reconciliation and any correction require exact preview
  and approval.
- Historical 25002 audit confirms the durable batch row retained response and
  endpoint only—not request body or HTTP method—so exact original aspect lists
  cannot be reconstructed. Added sanitized future-failure request shape
  telemetry (top-level/product/package keys and item-specific names only) to
  eBay errors and persisted attempt/crosspost diagnostics; no listing text or
  aspect values are logged. Provider-method/request-shape regressions with job
  summaries: 63 passed. Not deployed; historical records remain unresolved.
- Exact continuation task: use the latest authenticated 162-offer active eBay
  snapshot to reconcile the legacy 1,366 timeline/recovery asks by external
  listing identity and classify every unsupported amount; keep it read-only.
  Then present the 24-under-$25 shipping and all-listing +1-business-day
  proposals with shared policy/parcel blockers; no live operation until exact
  operator approval. Next continue 14 unknown 25001 outcomes and assisted
  Facebook/Mercari diagnostics.

### 2026-10-18 latest live eBay shipping/handling preview rerun

- Hypothesis: a fresh authenticated provider read can provide a usable
  item-level proposal without changing remote listings. Called the existing
  read-only preview endpoint directly against operator 2; no update/publish
  method was invoked.
- Remote active offers: 162. All 162 remote IDs map to a local Listing row;
  0 remote active IDs are unmapped. There are 353 distinct local Listing
  eBay-ID mappings in the endpoint's reconciliation scope, of which 191 do
  not appear in this active snapshot. Their remote ended/sold/inactive state
  is not inferred by this preview.
- Under $25: 24. Actual remote policy read reports all 24 as calculated
  `USPSParcel`, `free_shipping=true`, `buyer_pays_shipping=false`; none is the
  requested buyer-paid Ground Advantage policy. No candidate has verified
  measured packed parcel data. Existing category/approximate measurement
  values are explicitly unverified and do not qualify. Per-row safe options
  are: request measurements, propose an operator-approved supported
  alternative, or prepare temporary deactivation for explicit approval.
- Current handling: 161 at 0 business days and 1 at 1 business day. The
  per-listing +1 results (1 and 2 days) are all present in eBay's supported
  values. Fulfillment policy `260642879013` is shared across active offers
  (161 references in the full snapshot); editing it may affect listings beyond
  the 24 shipping candidates and unknown inactive/draft offers. No change was
  made.
- This is a live-provider read through local/un-deployed code. It is not a
  deployed dashboard verification or approval to change any active listing.
- Next: persist a line-item report that reconciles these active remote IDs to
  listing identity and price-evidence provenance without reproducing sensitive
  listing data; then continue controlled recovery of the 25-item batch and
  independent Facebook/Mercari readiness. Keep unknown `25001` outcomes
  unretried and do not mutate the shared shipping policy absent exact approval.

### 2026-10-18 shipping preview evidence/economics increment

- Implemented the next safe user-visible revenue-protection increment. The
  active-offer preview now calculates the fee estimate from the actual remote
  price rather than a potentially stale local ask, reports whether the local
  ask matches remote state, and surfaces persisted pricing provenance,
  recommendation eligibility, and research date.
- Renamed the proceeds figure to `estimated_net_proceeds_before_shipping_label`
  and no longer subtracts item cost from a value presented as proceeds. Profit
  remains null unless acquisition, label, packaging/materials, promotion, and
  other transaction cost inputs are all explicitly known. The dashboard row
  and CSV export display the same separation.
- Files changed: `backend/app/services/ebay_operations_preview.py`,
  `backend/tests/test_ebay_operations_preview.py`,
  `frontend/pages/settings/ebay.js`,
  `frontend/lib/ebayOperationsReport.mjs`,
  `frontend/lib/ebayOperationsReport.test.mjs`, and requirements, acceptance,
  defect, deployment, and progress ledgers.
- Verification hypothesis: a remote price mismatch must affect its fee
  estimate and be disclosed; missing cost inputs must never produce a profit
  number; CSV and dashboard must carry those facts. Backend preview + eBay fee
  tests: `19 passed`; CSV Node test: `1 passed`; frontend production build
  passed with existing repository warnings. `git diff --check` remains to run.
- Not deployed, no service restart, no database/listing mutation, no remote
  action. Live saved price provenance/cost values remain to be audited.
- Exact next task: extend safe item-level reconciliation to classify all
  remote active offers by local source and price-evidence quality; then proceed
  with one controlled read-only eBay failure-batch diagnostics increment and
  independent FB/Mercari readiness. No live revision/retry is authorized.

### 2026-10-18 active Facebook extension and Mercari readiness recheck

- Read-only production DB and repository manifest confirm paired Facebook
  device `73` is active at extension `0.3.70`, matching the current manifest;
  last heartbeat was 2026-10-19 03:02 UTC (2026-10-18 America/New_York). This
  supersedes the earlier stale/revoked device-71 snapshot, but does not prove a
  0.3.70 publication: no jobs in the last 30 days were run by .70.
- In the last 30 days Facebook has 155 jobs: 102 failed (60
  `FACEBOOK_FORM_STALL_RETRY_EXHAUSTED`, 17 `REQUIRED_FIELDS_UNRESOLVED`, 13
  `FACEBOOK_DIRTY_CREATE_FORM`, 5 `SUBMISSION_OUTCOME_UNKNOWN`, 3 each
  extension-execution and form-changed, and one progress-report timeout); 32
  are `SUBMITTED_PENDING_REVIEW`, 19 `COMPLETED`, one cancelled, one blocked.
  All 32 pending-review rows lack external ID/URL: 13 have reconciliation
  `PENDING_REVIEW`; 19 have no reconciliation record. Do not treat these as
  published. Example job 193 (old .69) verified login/form/title/description,
  saw $59.90 requested vs $60 observed, and correctly blocked before submit;
  do not silently round a consequential price.
- Mercari has 21 durable extension jobs: one CREATE failed after
  `REMOTE_RECONCILIATION_NOT_FOUND`; the other 20 are diagnostics (11
  completed diagnostics, four failed, five blocked). None has a remote ID/URL.
  Current official Mercari help describes its cross-list import as creating a
  draft that must be activated, not a third-party listing API:
  https://www.mercari.com/us/help_center/article/609/. No API capability or
  live publication is inferred from that page.
- Updated PUB-002/PUB-003, acceptance rows, and DEF-FB-001/DEF-MER-001 with the
  refreshed evidence. No marketplace mutation, browser task, service restart,
  or deployment occurred. Source code was not changed in this audit.
- Next: resolve extension job state/remote confirmation safely for pending
  Facebook tasks, starting with the current-device non-submitting diagnostic;
  then continue the independent evidence-backed product/pricing pipeline.

### 2026-10-18 official sold-comparable capability boundary

- Reviewed eBay's primary developer documentation. Browse results are active
  asks only. Marketplace Insights can return sold history up to 90 days, but is
  explicitly Limited Release for approved developers and vetted categories.
  PosterPro account entitlement was not established, so no sold evidence is
  claimed. Sources: eBay Developers Program,
  https://developer.ebay.com/api-docs/buy/marketplace-insights/static/overview.html
  and https://developer.ebay.com/develop/get-started/get-started-on-a-buying-application.
- Existing research persists active-asking counts separately and the analysis
  records `sold_comps_unavailable`; the dashboard shipping preview now reads
  the stored correct research date and sold/active counts. No API access or
  credentials were changed.
- Added ENR-008, acceptance row, and DEF-ENR-008 with an explicit entitlement
  check as the blocker. Keep estimates from masquerading as sold research.
- The latest backend suite remains `19 passed`, CSV Node `1 passed`, and
  frontend build passed with existing warnings; build was run after the final
  preview UI change. No deployment or live write.
- Next: reconcile stored price evidence against remote active IDs, then resume
  the 25-row per-item eBay recovery preview and research pipeline; the live
  shipping/handling operations remain approval-gated and undeployed.

### 2026-10-18 packing workstation durable offline replay increment

- Returned to unfinished scanner/packing work rather than leaving it behind
  marketplace investigations. Packing now journals scan events before network
  calls; reconnect/manual sync replays them in order. Associated camera photos
  and recorded voice are stored as data URLs in the same IndexedDB event
  journal, with 15 MB photo and 25 MB voice limits. Pending count is visible
  in the workstation UI.
- Stable retry IDs now protect boundaries and photos: `IntakeSlateCreateRequest`
  carries `client_event_id`; the create path returns a prior Slate without
  rerendering/requeueing Google Photos/print side effects, and workstation photo
  upload returns its prior row instead of storing another copy. Voice already
  used event IDs and keeps that behavior.
- Files changed: `frontend/pages/packing.js`,
  `frontend/lib/api.js`, `frontend/test/packing.offline.interaction.test.jsx`,
  `backend/app/api/schemas.py`, `backend/app/api/intake.py`,
  `backend/app/services/intake_slate.py`,
  `backend/tests/test_intake_head_slate.py`, plus requirements, acceptance,
  defect, deployment, and progress ledgers.
- Hypothesis/verification: identical event replay should create only one Slate
  and photo; offline scans/photos should persist locally without invoking the
  server. Targeted API/service tests `3 passed`; offline packing interaction
  plus journal tests `4 passed`; backend py_compile passed. Frontend production
  build after this increment remains pending.
- No migration, deployment, service restart, real inventory mutation,
  browser-offline reload/reconnect rehearsal, or physical scanner/camera/mic/
  printer/NFC test. Not marked production-complete.
- Next: run frontend build/`git diff --check`, then add exact offline browser
  reload/reconnect verification if a safe test harness is available; resume
  eBay 25-row controlled preview and the Google Photos enrichment gate.

### 2026-10-18 continuation: policy-default consistency and workstation checks

- Verified native Goal remains active with no configured execution budget;
  no promise of unlimited runtime is made. The verbatim 1,557-line master
  specification remains at `docs/POSTERPRO_MASTER_RECOVERY_SPEC.md`.
- Closed the prior offline-workstation check: interaction+journal tests passed
  4/4, frontend production build passed with existing warnings, and
  `git diff --check` passed. No deploy or hardware/browser reconnect test.
- Fixed an independently confirmed revenue-policy divergence: the listing
  route helper had stale defaults ($10 and free shipping at/above threshold).
  It now uses the centralized policy normalizer, which defaults to $25 and
  `preserve_existing` above threshold while retaining explicit stored operator
  preferences. Two focused regressions passed.
- Files in this increment: `backend/app/api/routes.py`,
  `backend/tests/test_marketplace_api.py`, and the requirements, acceptance,
  defect, deployment, and progress ledgers.
- No live listing change, publication, migration, deployment, or service
  restart. The dirty worktree was not staged or discarded.
- Next task: inspect the three eBay provider `25002` required-field mismatches
  (`Product` and `Original/Reproduction`) against retained sanitized request
  shape and endpoint mapping; then continue category/condition and 25001
  cases. Keep retries disabled until readiness is demonstrated and an exact
  retry preview is approved.

### 2026-10-19 continuation: exact remote state for eBay required-data failures

- Evidence: authenticated read-only exact-SKU `getOffers` plus Inventory item
  reads found the three required-data rows have existing unpublished offers,
  no remote listing IDs, and stale categories that differ from current local
  draft categories. The remote categories' current taxonomy requirements
  explain the saved provider-missing fields: row 3440 (`Chips` → `Product`);
  rows 3425 and 3407 (`Color` TV memorabilia → `Original/Reproduction`).
  This narrows the root cause to remote offer/category drift; historical
  request payloads remain unavailable.
- The recovery endpoint now adds exact remote offer/item/category/aspect
  evidence, explicit local-vs-remote category comparison, and a warning that
  package fields returned by eBay do not prove operator-measured provenance.
  Settings → eBay renders this evidence read-only. No correction is applied.
- Current local preflight blocks all three for unsupported price evidence
  (`PRICING_EVIDENCE_MISSING` and `EBAY_PRICE_MISSING`). The remote offer price
  is not accepted as an operator-approved/researched price. No retries.
- Files: `backend/app/services/ebay_service.py`,
  `backend/app/api/ebay.py`, `backend/tests/test_ebay_failure_recovery.py`,
  `frontend/pages/settings/ebay.js`, and the six authoritative/working ledgers.
- Validation: `test_ebay_failure_recovery.py` → 14 passed; shipping policy
  persistence/default tests → 3 passed; backend `py_compile`, `git diff
  --check`, and Next.js production build passed (existing warnings only).
  Authenticated read-only provider evidence verified locally, not in deployed
  UI. No service restart, deployment, offer/item update, publication, or retry.
- Continue next with the 8 category/condition failures and 14 remote-unknown
  outcomes; confirm per-row local blockers and remote offer mapping, then move
  into Google Photos product identity/pricing work that does not depend on
  live listing approval.

### 2026-10-19 continuation: category-failure exact-SKU reconciliation

- Queried all eight category/condition batch rows by exact PosterPro SKU and
  fetched inventory data only where an offer exists. Three offers (2744, 2283,
  3405) are unpublished and their remote category IDs 261084/262095/2208 no
  longer resolve in current eBay Taxonomy (HTTP 400 / 62005). Five exact-SKU
  offer requests (3406, 2238, 2751, 3475, 3201) return HTTP 404 / 25713;
  this does not prove every possible remote state absent.
- Current local categories include obvious collisions (2744 → Adhesive Insect
  Light Traps, 3405 → Stickers); other rows need item-level category review.
  Every one of the eight current local preflights still blocks because its
  asking-price evidence is unsupported. No category correction, parcel edit,
  repricing, remote update, or retry was made.
- Exact-SKU recovery now labels provider 404/25713 as `no_exact_sku_offer_returned`
  (not a generic outage), exposes status/error IDs in the operator UI, and
  preserves `retry_eligible=false`. Aspect lookup failures for old remote
  categories remain visible as provider errors rather than guessed results.
- Verification after this change: full eBay recovery suite 14 passed; Python
  compile and `git diff --check` passed; Next.js production build passed with
  existing warnings. The previous authenticated full recovery preview also
  confirmed the three required-data rows now show current exact offer details.
  Nothing is deployed or remotely modified.
- Next: inspect exact provider state for the remaining 14 `25001` outcomes
  with the improved read-only reconciliation, then continue Google Photos
  identity/research. Preserve unknown outcome semantics; require valid price,
  category, package measurements, and exact operator approval before retry.

### 2026-10-19 continuation: close a timeline-price readiness bypass

- Read-only inspection of all 1,357 user-2 timeline/photo listings showed 115
  were classified Needs Review by current local code. Ninety-two had
  `category_model_estimate` prices (many `$21.60`) with `recommendation_eligible`
  false or absent; nine had no pricing provenance. The prior gate only rejected
  exact `$20/$21/$22` placeholders for these source types, so non-round fallback
  prices bypassed the research gate.
- Updated `canonical_listing_readiness` so `intake_head_slate`,
  `google_photos_album`, and `media_inventory_recovery` require an eligible
  research result with recognized evidence provenance, nonzero evidence count,
  and confidence ≥0.45, or explicit operator price. Vine's separate source
  price handling was not changed.
- Three focused readiness regressions pass, including the $21.60 case, valid
  eligible comps, and documented operator lock. Read-only re-evaluation of all
  1,357 rows is now 0 Needs Review, 350 Needs Attention, 1,007 Processing.
  This is a gate correction, not evidence of acceptable generated listings.
- Full combined canonical-readiness + evidence-gate tests: 46 passed, 8 failed
  on legacy readiness expectations. Existing project notes already document
  broader readiness/promotion regressions; no failures were masked and no
  compatibility relaxation was applied. Focused regression cases passed.
- Files: `backend/app/services/canonical_readiness.py`,
  `backend/tests/test_canonical_readiness.py`, and master requirements,
  acceptance, defect, deployment, and progress ledgers.
- No pricing values, listing state, or production DB rows were changed;
  readiness evaluation used production data read-only. No deploy or service
  restart. Continue with the remaining 14 eBay `25001` outcomes and then the
  actual item identification/research pipeline; priority is producing
  evidence-backed drafts, not promoting existing fallback values.

### 2026-10-19 exact-SKU inventory-item reconciliation for 25001 batch

- Extended the 25-failure recovery preview to query eBay Inventory API for the
  exact deterministic SKU after the active-offer snapshot and exact-SKU offer
  lookup have no result. The UI distinguishes an existing inventory item from
  an offer or published listing and states that package fields do not prove
  measured provenance.
- Live read-only result: all 14 `25001` rows have a remote Inventory item with
  condition `NEW`; exact `getOffers` returned 404/25713 for each, and the active
  snapshot had no exact-SKU active match. Thus the inventory-item upsert likely
  exists, but none of these reads verifies an offer or listing. Historical
  request method/body is unavailable, so this does not establish root cause or
  prove no other listing identity exists. Retry remains disabled pending
  broader reconciliation and controlled approval.
- Returned aspects vary by item. Provider package objects contain dimensions,
  weight, and/or shipping fields, but do not establish operator-measured
  provenance; these values are not accepted as measurements.
- Recovery tests: 14 passed; module compilation and `git diff --check` passed.
  The initial frontend build session was lost; a fresh build was run after
  confirming its changed input was the UI addition. `npm run build` exited 0
  with existing lint/novnc warnings. Targeted eBay preview/recovery/shipping
  tests: 26 passed, 55 deselected. No deployment, restart, retry, publication,
  or remote mutation.
- Files: `backend/app/api/ebay.py`, `backend/app/services/ebay_service.py`,
  `backend/tests/test_ebay_failure_recovery.py`,
  `frontend/pages/settings/ebay.js`. Next: continue the unblocked product
  research/readiness pipeline; do not repeat a build without first checking
  changed inputs or build artifacts. Next unblocked requirement: investigate
  recoverable release/source-control boundaries and build a reviewed deployable
  increment without staging unknown dirty files; in parallel, continue real
  product identification/research acceptance.

### 2026-10-19 active runtime and recoverability check (OPS-001)

- Read-only systemd inspection confirms backend and worker use
  `/opt/apps/posterpro/repo/backend`; frontend uses
  `/opt/apps/posterpro/repo/frontend`. All three services report active.
- Git HEAD is `292c80f`; worktree has 206 dirty/untracked paths, including 31
  untracked app source paths, 12 migrations, and 18 backend tests. Additional
  untracked entries include generated dependencies, private/debug data, media,
  and snapshots. None was staged, committed, overwritten, or deleted.
- This checkout is the active runtime tree, so source identity is not a clean
  deployed commit. No safe isolated release was assembled; no restart. A
  reviewed path-level allowlist/exclusion pass is still required before any
  deployment. Requirement OPS-001 remains IN PROGRESS.
- Latest local verification for PUB-013: recovery suite 14 passed; combined
  targeted preview/recovery/route tests 26 passed (55 deselected); Python
  compilation and `git diff --check` passed; fresh `npm run build` exited 0
  with existing warnings. This is not deployment verification.

### 2026-10-19 actual timeline product research and category guard (ENR-010)

- Read-only DB sample: 240/1,357 timeline rows had some model/GTIN-like value;
  manual sample showed some are placeholders such as “Not specified” or “Needs
  review”, so this is not a verified-identification count.
- For listing 2405 (Dorman 4WD Actuator, Brand Dorman, MPN/model 600-103), the
  existing Browse refresh ran against live eBay and persisted evidence only:
  30 active asking comps, 0 sold comps, active-only provisional recommendation
  $106.79, confidence 0.67. Listing ask, status, and processing state were
  confirmed unchanged. Its readiness remains NEEDS_ATTENTION for missing
  validated category and operator condition review; no accepted draft claim.
- Current provider taxonomy suggestion for the full title was `Other US License
  Plates`. Added narrow semantic rejection and fallback to another suggestion
  only after leaf verification; fail-closed if no compatible category is
  returned. This guards the observed bad result without changing listing data.
- Tests: `test_ebay_service.py` + `test_ebay_taxonomy.py` focused set 4 passed;
  full service/taxonomy suites 85 passed; py_compile and `git diff --check`
  passed. Not deployed. No live category/price
  change or publication.
- Files: `backend/app/services/ebay_service.py`,
  `backend/app/services/ebay_taxonomy.py`,
  `backend/tests/test_ebay_service.py`,
  `backend/tests/test_ebay_taxonomy.py`, and status ledgers. Next: continue
  non-destructive category/fitment research on real representative rows and
  improve supported taxonomy resolution; keep the Dorman item blocked until
  condition, category and automotive compatibility evidence are verified.

### 2026-10-19 exact-identity automotive fitment research (ENR-011)

- Implemented a read-only eBay Catalog/Metadata research service. It queries by
  MPN (not ambiguous free text), requires exactly one normalized exact
  Brand+MPN match, retrieves the full product and rechecks identity, then asks
  the eBay Motors compatibility endpoint. Candidates are never treated as
  confirmed applications. The scoped API persists evidence only; a Listing
  Workspace panel displays status, source, candidate/product, and compatibility
  records. It does not edit listing content, readiness, price, or publish state.
- Live test against user-2 draft 2405: Catalog MPN search found one exact Dorman
  `600-103`/`Dorman` ePID `15079948485`, product aspects show Sub Type `4WD
  Actuator`. Motors Metadata returned zero compatibility details. The new API
  persisted `compatibility_records_unavailable`; after refresh, title, price,
  status, and processing state were byte/value-equivalent. Draft remains
  blocked from claiming vehicle fitment.
- Independent read-only manufacturer research on [Dorman product page](https://www.dormanproducts.com/p-19432-600-103.aspx)
  lists specific applications, but no authorized repeatable manufacturer
  catalog API/adapter has been implemented. Those one-off observations were
  not merged into listing fields. Current eBay official API docs specify
  Catalog MPN search and Motors compatibility APIs; the eBay US general
  marketplace call rejected compatibility with error 47006, so the new service
  correctly uses `EBAY_MOTORS_US` (see official
  [product compatibility guide](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/specify-compatibility-by-product.html)).
- Tests: `test_ebay_fitment_research.py` 6 passed, including exact Brand+MPN
  and GTIN matching, ambiguous candidates, zero records, missing identity, and
  API persistence without listing mutation. Python compile and diff check passed; Next build
  exited 0 with existing warnings. Not deployed.
- Files: `backend/app/services/ebay_fitment_research.py`,
  `backend/app/api/intelligence.py`,
  `backend/tests/test_ebay_fitment_research.py`, `frontend/lib/api.js`,
  `frontend/pages/listings/[listingId].js`, and requirements/acceptance/defect/
  deployment/progress ledgers. Next: implement permitted manufacturer evidence
  integration and gather verifiable fitment for a representative automotive
  cohort; preserve no-fitment blockers where the catalog is silent.

### 2026-10-19 research provenance UI and Mercari policy guard (ENR-011 / PUB-003)

- Fitment evidence refresh preserves independently sourced `manufacturer_sources`
  records in the local research object. Listing Workspace now renders these
  separately from eBay Motors compatibility and explicitly labels them
  research evidence—not customer-facing claims—until the physical unit and
  variant are verified. Added regression asserting an eBay refresh preserves
  those independent source records.
- Live manufacturer page review reconfirmed Dorman 600-103's application
  summary and specifications; no repeatable API/source adapter or evidence
  capture integration exists yet. No customer listing metadata was changed.
- Mercari's current [Prohibited Conduct policy](https://www.mercari.com/us/help_center/topics/listing/policies/prohibited-conduct/)
  prohibits automated interfaces not provided by Mercari. The existing
  PosterPro Mercari extension did automated form interaction/submission and
  has no verified authorized API. Local safeguards now remove connector
  extension capabilities, reject new Mercari assisted actions and live-form
  diagnostics, and block queued/retryable legacy jobs before extension claim.
  Settings copy identifies manual handoff; Mercari is removed from form-test
  selector and default installer buttons now point to Facebook.
- Manual Mercari packet copies draft title, current price, condition, destination
  description, and verified-record brand/part details only. It expressly leaves
  Mercari category, photo selection, shipping and final publish to the operator;
  nothing is submitted or remotely changed.
- Read-only live DB check: 21 Mercari jobs (CREATE id 60 failed
  `REMOTE_RECONCILIATION_NOT_FOUND`; 20 diagnostics), status distribution 11
  completed, 5 failed, 5 blocked. No remote listing identity and zero active,
  queued or retryable jobs. Two non-revoked devices recorded: 0.3.70 last seen
  2026-10-19 04:06:43 UTC and stale 0.3.2 last seen 2026-09-25.
- Verification: fitment + shipping/handling preview suites 16 passed; Mercari/
  extension transport selection 8 passed (45 deselected); marketplace routing
  11 passed; Next build passed with existing lint/novnc warnings. One initial
  auth-fixture assertion returned 401; corrected to use the test's session
  cookie and reran successfully. Python compile passed; `git diff --check` passed.
- No deployment/restart, provider form access, listing change, retry, or
  publication. Guard does not cancel jobs already claimed by an extension.
  Current worktree remains heavily dirty and cannot safely be deployed as a
  whole. Next: continue exact corrections for the 25-item eBay failure batch,
  then proceed with scanner/inventory and remaining enrichment requirements.

### 2026-10-19 offline scanner command normalization (INV-001 / INV-003)

- Reproduced a packing workstation bug: offline scanner classification and
  session-state logic recognized only namespaced `POSTERPRO:COMMAND:...` scans.
  Plain reader labels such as `NEXT ITEM` were therefore treated as product
  barcodes, creating a false provisional boundary.
- `frontend/pages/packing.js` now normalizes plain and namespaced registered
  labels. Implemented session-local commands clear item/tote state or adjust
  quantity as appropriate. Registered commands without a linked inventory
  operation are visibly identified as not implemented instead of getting a
  success message.
- Tests: `frontend/test/packing.offline.interaction.test.jsx` 2 passed,
  including literal whitespace-varied `NEXT ITEM`; backend
  `test_scanner_dispatcher.py` 3 passed. `git diff --check` passed. Frontend
  build passed with existing lint and noVNC async warnings; no deployment or
  device rehearsal.

### 2026-10-19 scanner package, label, and tote actions (INV-004 / INV-005)

- `NEW PACKAGE` now creates one tenant-owned package with deterministic code
  based on user + operation ID and persists the active package checkpoint.
  Replaying the same event creates no duplicate.
- `PRINT LABEL` now queues one durable 4×6 print job and returns a printable
  package URL; explicit `REPRINT LABEL` intentionally queues another job with
  a distinct operation identity. Delivery replay for either event cannot
  duplicate its job. No active owned package is an explicit blocker.
- `NEW TOTE` now arms exactly the next scanner input as the tote identity.
  Online dispatch creates or selects a tenant-owned tote; offline UI shows the
  next-scan cue and records the code as tote state instead of product media.
  Backend idempotency replay prevents duplicate tote creation.
- Tests: `backend/tests/test_scanner_dispatcher.py` 7 passed;
  `frontend/test/packing.offline.interaction.test.jsx` 4 passed. One initial UI
  assertion incorrectly queried an accessible label not exposed by the shared
  Input component; corrected to use the actual placeholder. A second attempt
  exposed missing visible workflow status; added the status and the regression
  passed. Backend/frontend production builds/test status are recorded in
  deployment/acceptance ledgers. Not deployed; no HID/NFC/printer rehearsal.
- Remaining scanner actions (MOVE ITEM, FIND ITEM, BUNDLE), package contents
  reconciliation, inventory-to-Slate/listing linkage, physical hardware, and
  fulfillment remain incomplete. Next safe task: continue scanner stock
  location and packing integration, then return to the 25-item eBay recovery
  preview. Live listing changes and publication remain approval-gated.

### 2026-10-19 eBay recovery diagnostics and Trading request method (PUB-001 / PUB-010)

- Re-read actual stored provider error payloads for the deterministic eleven
  rows. Confirmed five Trading failures each state category invalid, condition
  dropped/inapplicable, and package weight invalid or missing; three Inventory
  25005 failures state invalid category; the three 25002 rows name exact
  missing fields (`Product`, `Original/Reproduction` twice). The fourteen
  25001 rows retain only system-error prose and offer paths, not proof of a
  successful/failed remote publication.
- Ran read-only current eBay Taxonomy suggestion, category-leaf, required-
  aspects, and condition-policy calls for the eleven deterministic records.
  Returned candidates include 3475 → 61941 wiper mechanisms; 3201 → 178918
  windshields/windows; 2751 → 178023 turn-signal assemblies; 2283 → 185140
  PCBs; 3405 → 184271 shipping labels. 3406 and 2238's existing categories
  remain valid taxonomy leaves despite historical rejection. 2744 and 3407
  candidate results remain semantically unsuitable/noisy. All candidates are
  unapproved and none was applied; all five Trading rows still need measured
  packed weight. Details and required candidate aspects are in
  `docs/POSTERPRO_OPEN_DEFECTS.md`.
- Confirmed historical Trading failure records have endpoint but no method.
  Updated every post-submission `AddFixedPriceItem` error branch to retain
  `POST`. Regression: Trading rejection + crosspost serialization tests 2
  passed; compile and diff checks passed. No deployment/restart, provider
  mutation, retry, or listing change.
- Next: resolve current eBay `Product` specifics / draft-vs-remote field
  mapping for rows 3440/3425/3407; then complete per-row correction previews
  for all deterministic failures, preserving measured parcel and operator
  product-choice blockers. Continue scanner and evidence-enrichment work in
  parallel; 25001 retry remains prohibited until remote outcomes reconcile.

### 2026-10-19 scanner FIND ITEM lookup (INV-006)

- Wired the registered `FIND ITEM` scanner action to the existing private
  inventory lookup. The following barcode/code is treated as a query, not an
  item capture; results include only tenant-owned physical locations and
  availability and are saved in the durable scanner session checkpoint.
- Offline workstation behavior now arms the same query state, journals the
  next scan without creating a provisional item/Slate, and reconciles the
  server lookup result after synchronization.
- Tests: `backend/tests/test_scanner_dispatcher.py` 7 passed, including exact
  product/barcode/tote location + reserved-quantity result and idempotent
  replay; `frontend/test/packing.offline.interaction.test.jsx` 4 passed.
  Frontend `npm run build` passed with existing lint/noVNC warnings. No
  deployment or production inventory lookup was performed.
- Offline UI now labels server-backed commands as waiting for synchronization
  instead of incorrectly saying their operation is not implemented. Explicit
  REPRINT LABEL is wired to one new durable label job and replays idempotently.
  Updated scanner tests remain 7 passed, frontend interaction tests 4 passed,
  eslint 0 errors (one unnecessary-dependency warning), frontend production
  build passed. Not deployed; no thermal printer test.
- Next: continue package contents/stock conservation and scanner MOVE/BUNDLE
  workflows, while resolving the exact remote/local specifics mismatch for
  eBay rows 3440/3425/3407. The 25-item retry preview remains disabled until
  per-item blockers are resolved and operator approval is obtained.

### 2026-10-19 failed eBay required-specific diff (PUB-014)

- Added local draft aspect names and a direct comparison against required
  aspects for the exact remote offer category to the read-only recovery API.
  Settings → eBay now displays which specifics are missing locally, alongside
  existing remote category/aspect evidence.
- Regression: `PYTHONPATH=backend backend/.venv/bin/pytest
  backend/tests/test_ebay_failure_recovery.py -q -k
  deterministic_failure_reconciles_remote_offer_category_and_specifics_read_only`
  — 1 passed. `cd frontend && npx eslint pages/settings/ebay.js` — 0 errors,
  one existing `useEffect` dependency warning. `cd frontend && npm run build`
  passed with existing repository lint/noVNC and stale Browserslist-data warnings.
- This is diagnostic visibility, not a repair: records 3440, 3425 and 3407
  retain incorrect remote categories, weak identity evidence and unsupported
  Vine tax-value fallback prices. No provider write/retry, deployment, or
  service restart. Acceptance/defect ledgers updated.
- Next: continue per-row product identity/price/category correction evidence
  for the failed eBay batch; also take an independent safe inventory workflow
  increment. Do not retry or revise remote offers without an exact approved
  preview.

### 2026-10-19 ETV and estimate provenance enforcement (ENR-012)

- Trace confirmed eBay failed items 3440/3425/3407 carried
  `vine_estimated_tax_value_fallback`. `canonical_listing_readiness` explicitly
  accepted Vine ETV as a bounded asking price and later removed its own
  evidence blocker. Corrected authoritative price resolution to reject ETV,
  category-model, generic fallback, default, temporary, and unresearched
  provenance unless the operator explicitly owns/locks the price. Removed the
  legacy Vine blocker cleanup for ETV; the user-visible Needs Review aggregate
  no longer counts ETV-only rows.
- Tests: four focused regressions passed, including the new check that stale
  `needs_price_research=false` does not bless ETV and a locked operator price
  still resolves. Broader readiness + listing aggregate run before final
  expectation adjustment reported 52 passed/9 failed; one was the expected
  aggregate shift and was corrected. Eight unrelated queue/shipping/category/
  destination expectations remain to triage before release acceptance.
- No production data or remote listings changed. Not deployed/restarted.
- Next: collect exact identity and valid pricing evidence for the 25 eBay
  failures; separately triage the eight readiness regressions and continue
  scanner stock-conservation/MOVE/BUNDLE work. Do not stage/release the dirty
  active worktree as-is.

### 2026-10-19 Legacy pricing provenance in eBay operations preview

- Current task: align the revenue-risk preview with actual persisted pricing
  records, which commonly store `price_source` rather than `provenance`.
- Changed `backend/app/services/ebay_operations_preview.py` to read either
  field, preserve the source in evidence output, and reject ETV/category/fallback
  provenance even if a stale `recommendation_eligible=true` flag is present.
- Added `test_preview_reads_legacy_price_source_and_rejects_etv_as_research` to
  `backend/tests/test_ebay_operations_preview.py`, using the production-shaped
  Vine ETV metadata.
- Validation: two targeted preview tests passed; `py_compile` and
  `git diff --check` passed. No provider write, deployment, or service restart.
- Acceptance: preview provenance correction is tested locally only; live preview
  rerun and production deployment remain outstanding.
- Next: continue precise reconciliation/read-only preview work for active offers
  and failed eBay rows, then take an independent safe inventory increment. Keep
  live revisions and retry/publish operations approval-gated.

### 2026-10-19 Readiness queue and explicit shipping-setup correction (REV-008 / ENR-012)

- Re-ran the broad canonical readiness + listing aggregate suites after the
  final pricing expectation correction: 54 passed / 8 failed. Investigation
  showed missing parcel data was being treated as a generic product blocker,
  and an unidentified recovery item was held in machine price research despite
  lacking source facts.
- `canonical_listing_readiness` now emits `shipping_setup_required` and an
  actionable warning for missing packed weight/dimensions. It permits otherwise
  complete product data to reach operator review but keeps `publishable=false`;
  no parcel values are estimated. Generic `media_inventory_recovery` without
  source facts now surfaces its identity blocker rather than masking it as
  machine price work.
- Regression expectation now explicitly checks the shipping setup flag, warning,
  and publish block. Broad readiness + aggregate verification: 62 passed.
- Listing detail (`frontend/pages/listings/[listingId].js`) now visibly renders
  a distinct “Shipping setup required” callout when that readiness flag is set,
  explaining no estimated dimensions/weight are used and publication stays
  blocked. Targeted ESLint: 0 errors, 7 pre-existing warnings.
- `py_compile` and `git diff --check` passed on the relevant service/tests.
  No database row, remote offer, service, or deployment was changed.
- Timestamp: run finished at 2026-10-19 04:46 UTC; exact start time was not
  separately recorded.
- Next: expose the shipping setup flag in the dashboard, reconcile exact
  provider/category requirements for the 25 eBay failures, and continue the
  scanner stock-movement workflow. Production verification remains outstanding.

### 2026-10-19 eBay failed row 3440 product-evidence increment (PUB-009)

- Read-only local production-DB lookup confirmed listing 3440 is titled
  “LONELY BINARY 12-Pack RS485 Transceiver Modules, MAX485 & MAX3485 Chips”; its
  local item specifics/category are incomplete or inconsistent with the title.
- Researched the manufacturer documentation, which confirms the 12-pack has
  six MAX485 5V boards and six MAX3485 3.3V boards with loose headers. Added the
  URL and exact evidence scope to `docs/POSTERPRO_OPEN_DEFECTS.md`.
- Current local Interface ICs category and remote Chips category both need
  human/taxonomy review for board modules; provider-required `Product` was not
  guessed. Individual-board active eBay asks are not comparable to the 12-pack
  and no sold comp was verified, so no price recommendation is made.
- No listing, offer, category, specifics, or price was changed. Row 3440 remains
  ineligible for retry. Exact task start time not separately logged; this
  read-only research completed 2026-10-19 around 04:48 UTC.
- Next: continue resolving the other failed rows with authoritative item-level
  evidence; preserve unknown 25001 outcomes and approval gates.
- Additional read-only research completed for failed row 3425 (`YOWEXA Paint
  Thickness Gauge`). Manufacturer family/catalog evidence makes EC-777E the
  strongest model candidate for the title's 0-5000μm range, Bluetooth and color
  screen, but manufacturer pages conflict on standard versus customized range;
  physical model and probe remain unverified. ETV cannot support a price, no
  exact comparable was found, and no automotive fitment applies to this tool.
  Added sources/uncertainties to `docs/POSTERPRO_OPEN_DEFECTS.md`; no listing or
  provider state changed. Row 3425 remains blocked from retry.
- Read-only exact-title retailer research for failed row 3407 found a current
  Rvhouse 12V dome-light page listing part `XGJ8LBC`, 1000 lm, 3W, tri-color
  dimming, product dimensions and a $40.29 active asking price. It is not a
  sold comp, product dimensions are not parcel measurements, and the physical
  item/model/condition remain unverified. Local specifics and categories
  conflict with the title; remote `Original/Reproduction` belongs to a TV
  memorabilia category. Recorded evidence and caveats in defect ledger; no
  draft/provider fields changed. Row 3407 remains blocked.
- Current eBay public category browse surfaces `261084` RV, Trailer & Camper
  Interior Lighting with similar 12V dome lights. Logged as a strong category
  candidate only; current authenticated Taxonomy leaf/aspect validation and
  operator review are still needed before any correction.
- For row 3425, current eBay browse exposes general Thickness Gauges `42291`
  and an Automotive Precision & Measuring branch `43991`, confirming the
  category ambiguity. Neither is a validated leaf/aspect result for this exact
  product, so no category was selected.

### 2026-10-19 scanner MOVE ITEM transfer increment (INV-007)

- Added tenant-scoped `POST /inventory/stock/move` for explicit stock moves
  between owned totes/open packages. Source and destination stock rows are
  locked, only available (unreserved) quantity may move, sealed packages cannot
  be opened by removing stock, and every transfer has a request fingerprint,
  idempotent operation ID and auditable `inventory_stock_moved` event.
- Added packing workstation `MOVE ITEM` scan flow: the command arms the next
  scan as a private product lookup (not a Slate capture), preserves move lookup
  through offline replay, and requires a unique product, eligible source,
  active destination tote or package, quantity and explicit operator confirmation before
  the connected API updates stock.
- Regression coverage checks conservation, replay, foreign destination,
  reserved quantity, closed-package rejection, command checkpoint and that
  lookup itself never changes stock. Package + scanner suites: `15 passed`.
  Packing interaction suite: `5 passed`, including explicit operator
  confirmation before API call and no Slate creation. The UI test preloads an
  active package and confirms it is selected as the destination. `py_compile`,
  `git diff --check` passed. Targeted ESLint: 0 errors, one existing
  unnecessary `session.id` hook dependency warning.
- No deployment, real browser/device rehearsal, database migration, production
  inventory mutation, printer test, or service restart. Exact start time was
  not separately recorded; the core implementation finished around 04:59 UTC
  and package-destination interaction test finished around 05:03 UTC; elapsed
  duration unavailable. Next: exercise the workflow in a real browser against
  an isolated fixture, then continue the registered BUNDLE workflow.
### 2026-10-19 INV-008 — scanner BUNDLE review capture (local-only)

- Implemented `BUNDLE`/`START BUNDLE`, `ADD TO BUNDLE`, and `FINISH BUNDLE` in scanner dispatch and Packing UI. Scans resolve only to existing tenant-owned products; quantities are captured and checked against available stock but are not reserved. Finish creates a review-required workstation checkpoint with explicit non-sellable blockers.
- Files: `backend/app/api/inventory.py`, `backend/tests/test_scanner_dispatcher.py`, `frontend/pages/packing.js`.
- Evidence: focused API bundle+move tests `2 passed`; an additional API assertion verifies the review-required bundle and component survive `GET /inventory/scanner/sessions/{session_id}` after reload (`1 passed`); `frontend/test/packing.offline.interaction.test.jsx` `6 passed`, including visible start→scan→review state; `git diff --check` clean. The new UI test needed selector/persistent-state/mock-isolation corrections before passing. No deployment, physical scanner test, DB migration, or production mutation.
- Time: start not captured; final UI validation completed at 2026-10-19 05:11:22 UTC; elapsed unavailable.
- Remaining: canonical bundle identity, concurrency-safe component reservations and rollback, pricing/listing, and fulfillment. This increment is not commercial bundle completion.
- Next: continue highest-priority unblocked shared-inventory/fulfillment task; define durable bundle identity and reversible stock semantics before any stock-affecting bundle operation.

### 2026-10-19 PUB-009 row 2238 manufacturer evidence follow-up (read-only)

- Read the current official DAH KEE page for DK 11273. It documents 12V/150A, Mitsubishi mounting, S6 pulley, and includes 11273N among OEM cross-references. The failed local row stores brand `11273N Alternator`, MPN `HRDAMALT-11273`, OEM `11273`; actual physical identity is not confirmed.
- Found a material source inconsistency: introductory fitment prose and detailed application table disagree on Lincoln MKS/MKZ engine assignments. Do not use those fitments as listing claims until resolved by the physical label and authoritative fitment data.
- Source: https://www.dahkee.com/en/product/DK_Alternator-11273.html. No price evidence, provider write, category correction, listing edit, or retry; active row still unresolved.
- Files changed: master requirements, acceptance matrix, open defects, progress ledger, deployment ledger, goal status report. Exact research start unavailable; report/docs update complete by 2026-10-19 05:12 UTC; elapsed unavailable.
- Next: resolve exact product with operator-visible label/VIN/catalog evidence; meanwhile continue to another safe, unblocked requirement.

### 2026-10-19 REV-003 domestic fulfillment-policy interpretation

- Concrete hypothesis: flattening `shipping_options` could report an international shipping service as the domestic shipping setup when provider order differs.
- Changed `backend/app/api/ebay.py` to select only `option_type=DOMESTIC`, preserve domestic carrier and available service options, and derive cost type/free/buyer responsibility from that option. `backend/app/services/ebay_operations_preview.py` returns carrier and all configured domestic services in `current_shipping`.
- Regression fixture puts international UPS first and domestic USPS second; verified output remains calculated USPS and buyer-responsible. Full targeted `backend/tests/test_ebay_operations_preview.py`: `11 passed`; diff check clean.
- Not deployed; no remote revisions or service restart. Start time not captured; validation finished by 2026-10-19 05:15 UTC; elapsed unavailable.
- Next highest priority: establish reviewed release path for existing source changes and verify exact shipping/handling preview against production; do not apply live revisions before item-level approval.

### 2026-10-19 source-control inventory refresh

- Read-only `git ls-files --others --exclude-standard` found 2,175 untracked paths; `git status` reports 138 modified entries and 70 untracked directory-level entries. Counts by broad path group: backend app 16, migrations 12, backend tests 19, frontend source 17, frontend tests 10, extension 2, docs 8, ops 2,076. `ops` includes snapshots/artifacts; counts are not a commit allow-list.
- Updated `docs/POSTERPRO_SOURCE_CONTROL_AUDIT.md`. No file staged, committed, or discarded. Safe release is still blocked on explicit source/test/migration allow-list and reviewed isolated base; no service restart/deploy.
- Next: use an explicit allow-list per release increment and preserve the active dirty tree. Do not claim reproducible production release until a reviewed artifact exists.

### 2026-10-19 deployed-process baseline recheck (read-only)

- Confirmed backend and frontend services are active from `/opt/apps/posterpro/repo/{backend,frontend}`; backend process start `2026-10-18 16:25:04 UTC`, frontend `16:24:31 UTC`; worker `2026-10-14 19:22:05 UTC`.
- Current checkout is branch `codex/cms-workspace-theme`, HEAD `292c80f`, dirty with extensive untracked source and artifacts; current process has no trustworthy code hash. The backend process predates this turn's shipping-preview correction, which remains undeployed.
- Read-only `systemctl show`/process inspection only. No restart or deployment. Next: explicit allow-list and isolated release verification; do not use dirty-tree runtime as proof.

### 2026-10-19 REV-003 preview UI/export service-detail follow-through

- Exposed the corrected domestic carrier and configured domestic services in Settings → eBay and the preview CSV; formula-injection escaping is preserved. The formatter de-duplicates provider entries.
- Files: `frontend/pages/settings/ebay.js`, `frontend/lib/ebayOperationsReport.mjs`, `frontend/lib/ebayOperationsReport.test.mjs`.
- Validation: Node tests `2 passed`; targeted ESLint `0 errors`, one pre-existing missing `reload` dependency warning. Backend operations-preview suite remains `11 passed` from the preceding increment. No full frontend build, deployment, restart or provider write.
- Time: exact start not recorded; tests/lint completed by 2026-10-19 05:20 UTC; elapsed unavailable.
- Next: continue explicit source allow-list and isolate a reviewed release without changing branches or discarding the dirty worktree. Then revalidate the preview against production; active listing changes still require item-level approval.

### 2026-10-19 regression triage checkpoint

- Existing latest evidence: eBay-focused suite `152 passed`; broader marketplace/review/pricing suite `80 passed, 6 failed`, with the six reproduced in a focused invocation. No tests were run while preparing the status report.
- Read-only inspection narrowed possible causes: queue fixtures assert the strict readiness contract; `_listing_bucket` has a legacy non-Vine review compatibility path that needs comparison against canonical blockers; one reviewability expected value may predate stricter completeness; dashboard aggregate total requires matching its exact fixture and Python-vs-SQL classification; the Vine correction test invokes taxonomy lookup without creating the explicit taxonomy-cache migration table; manual-comparable failure needs its original assertion output, which was truncated, so cause is unknown.
- No failure is marked fixed or dismissed as a stale test. No source code changed and no release/deployment action occurred.
- Current task: inspect the six cases and implementations, then form one precise hypothesis per targeted regression before running a test. Preserve the strict product/readiness gate; resolve test fixture schema separately if confirmed.
- Time: investigation began after report generation; exact start/finish and elapsed time unavailable. This is an in-progress checkpoint, not acceptance evidence.

### 2026-10-19 urgent frontend asset-mismatch recovery

- Operator reported the site was completely unusable/visually broken. Read-only evidence found the running Next process rendered build ID `-_ARyL_X_jEqenk2UrLOT`, but its HTML-referenced Listings and `_app` chunks/CSS did not exist in `frontend/.next/static`; direct requests returned Next.js 400 error pages. The current disk build had different asset hashes and build ID. Backend health was 200/database ready and all key HTML routes returned 200, which alone had falsely looked healthy.
- Restarted only `posterpro-frontend.service`; its configured `ExecStartPre=npm run build` completed successfully, then `next start` loaded build `Mspxrnyfm4AVG2tUxP8ii` at 2026-10-19 05:33:36 UTC. Backend and worker were not restarted; no database, inventory, listing, or marketplace data changed.
- Verification: local `/`, `/app`, `/listings`, `/settings`, `/packing`, `/inventory`, `/intake` all returned 200; every JS/CSS asset referenced by local `/listings` returned 200. Public `/listings` assets through Caddy also all returned 200; public `/` returned 200. All three services report active. This verifies build/asset consistency and route reachability, not authenticated interactive browser usability or visual design quality.
- Production hygiene exception: frontend restart rebuilt and served the current dirty frontend worktree because the service's normal startup pre-step always builds in place. This was an evidence-based operational recovery action after the operator reported complete site failure. No commit/release artifact exists; curate source and reproduce separately before the next planned release. Hard refresh the operator browser to discard cached HTML/chunk references.
- Next: request/reproduce any remaining layout/usability defect after hard refresh; if the display still looks wrong, capture authenticated browser screenshot/console and route. Keep urgent frontend usability ahead of feature work. Separately resume regression triage; its code changes remain local and were not included in this frontend-only deployment.
- Time: failing asset observed by 05:31:24 UTC; build completion/start 05:33:36 UTC; investigation/recovery start command timestamp not preserved, so total elapsed unavailable. Frontend service was in start-pre for roughly 1 minute before activation.
