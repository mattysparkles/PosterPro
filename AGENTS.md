# PosterPro Deployment Log

## 2026-09-13 - Vine cohort content finalization and preflight repair

### Fixed
- The shared Vine draft finalizer now runs before import-time lifecycle
  classification, rebuilds copy from structured Amazon evidence, preserves
  durable facts on empty/transient discovery, and uses current eBay preflight
  blockers (not historical blocker JSON) to choose Needs Review vs Attention.
- Amazon current-price extraction now reads the ASIN product offer widget,
  preserves cents, and rejects unrelated page-wide price JSON. A verified
  current offer outranks ETV; on a transient empty refresh an existing
  non-ETV draft price is preserved with explicitly unverified provenance.
- Mislabeled capacity values such as protocol versions or amperage are retained
  as untrusted source evidence but excluded from listing aspects and generated
  descriptions; supported values such as the RV carrier's gallon capacities
  remain available.
- Vine category resolution now verifies actual eBay leaf metadata and current
  preflight requires that verification. Required aspects are derived from that
  destination category. The catalog queue now routes a blocker-free completed
  Vine listing with `needs_review=true` to Needs Review.
- Reconciled the ten explicitly requested production listings through the
  shared finalizer: all now have provider-generated original descriptions,
  ASIN-matched current price provenance, condition New, quantity 1, primary
  usable local images, semantically selected eBay leaf categories, and fresh
  blocker-free eBay preflight. None was published.
- Canceled ten pre-existing stale (Sept. 5–6) cohort crosspost jobs after
  verifying there were no publish-attempt records or external eBay IDs, so the
  repaired drafts remain in review rather than being submitted automatically.
- The other 18 historical Vine rows still marked `needs_attention` are
  archived (17) or sold (1) and have no usable images; fresh read-only
  preflight reports the image blockers. They were not reactivated.

### Validation and deployment
- Focused backend regression set across Vine import, marketplace preflight,
  marketplace API, eBay service/publish, listing review, and pricing:
  `157 passed`.
- Added coverage for ASIN-offer price parsing, cents/struck-price handling,
  rejecting arbitrary page prices, durable price preservation on empty
  discovery, malformed Capacity filtering, stale blocker recomputation, and
  semantic eBay leaf selection.
- One earlier run exposed a stale `_llm_generation` test stub that did not
  accept the existing optional `db` keyword; updated the stub contract and the
  final full run passed with zero failures/errors.
- Production finalization/preflight returned zero hard blockers for all ten;
  valid local images: 11–12 per listing. No migration or frontend build was
  required. No live marketplace publication was invoked.
- Deployed commit `9644379` by restarting only `posterpro-backend.service` and
  `posterpro-worker.service`; backend, worker, beat, and frontend are active.
  `/health` returned `ok=true`, `database_ready=true`. A post-restart read-only
  fresh preflight reconfirmed all ten as blocker-free `needs_review` in the
  review queue; the ten stale cohort jobs are canceled and publish attempts
  remain zero.
- A subsequent read-only history audit found 27 older failed cohort jobs from
  September 5, each with zero attempts; their recorded failure was preflight
  `EBAY_REQUIRED_ASPECT_MISSING` (35 blocker records across those jobs). The
  ten later stale queued jobs are canceled. Across all 37 cohort jobs there
  are zero attempts, zero persisted marketplace publish-attempt rows, no
  external eBay IDs, and no active jobs. The historical job failures remain
  visible in job history; current listing preflight has zero blockers.

## 2026-09-12 - Tenant routing rules foundation

### Added
- Added tenant-user-scoped marketplace routing rule storage and validation,
  deterministic category/brand/condition/source/price matching, include/exclude
  destinations, rule ordering, and explicit manual-destination precedence.
- Cross-post requests with no explicit destination selection now use the
  authenticated user's routing rules when configured and record the matched
  rule IDs/destinations in the durable cross-post job plan. Existing listing
  targets remain when no rules are configured; unrouted items are not silently
  defaulted to eBay.
- Added `POST /marketplace-jobs/bulk-crosspost` for up to 500 selected listing
  IDs. It owner-scopes records, accepts only ready/posted unsold inventory,
  applies explicit destination overrides or routing rules, writes independent
  per-listing jobs before broker dispatch, and gates eBay live queueing behind
  the existing explicit confirmation phrase. A one-minute recovery task
  dispatches committed queued jobs whose broker task ID is still absent.
- Added read/write rules and per-listing route preview API surfaces. The
  Listings catalog now exposes bounded bulk destination selection and queues
  durable crosspost jobs; Settings has a tenant-user-scoped rule editor with
  filters, priorities, include/exclude destinations, enable/disable, and
  delete controls. These are automated/build-verified UI surfaces, not proof
  of authenticated live marketplace form completion.

### Validation
- Routing matcher, manual override, validation, durable routing, owner-scoped
  bulk job creation, and queued-job recovery tests: `6 passed`.
- Combined marketplace/eBay/preflight/extension/sale/routing regression set:
  `87 passed`.
- This is automated backend behavior only; no live marketplace work is implied.

## 2026-09-12 - Cross-market payload and sale identity hardening

### Fixed
- Marketplace payload translation now carries explicit canonical brand, size,
  color, material, dimensions, weight, inventory identity, and shipping/parcel
  evidence into the destinations that consume those fields. Destination
  category IDs remain destination-specific; non-eBay payloads do not receive
  the eBay taxonomy ID.
- Removed default Etsy `who_made=i_did` and `when_made=made_to_order` values.
  Resale listings now leave those fields unset unless explicit marketplace-
  specific evidence exists; Etsy authorization/taxonomy remains incomplete.
- Extension adapters now fill supplied brand/size/color/material/location only
  when a semantic free-text control is found; unavailable fields are reported
  for operator entry. Category, condition, controlled selectors, and final
  submit remain operator review rather than guessed automation.
- Sale idempotency now uses marketplace order ID as the primary sale-event
  identity. A later order against a relisted external listing is no longer
  suppressed by the reused marketplace listing ID; listing ID is fallback only
  for providers that omit order identity.
- Hosted-browser-assisted Facebook outcomes now require a visible external
  listing identity before job summaries count the target as submitted. Hosted
  bridge polling is explicitly covered separately from extension transport.
- Fixed import-job serialization to scope included review listings by the
  durable job owner rather than an undefined request-global user variable.

### Validation
- Marketplace publish/preflight/API/extension/eBay/job and sale suites:
  `81 passed` across the combined invocation.
- Browser extension fixtures: `8 passed`.
- These are automated tests only; no live marketplace publish or account E2E
  was performed. Facebook/Mercari/Poshmark/Vinted/OfferUp/Etsy destination
  taxonomy and controlled-field completion remain partial/operator-assisted.

## 2026-09-11 - Vine quality lifecycle reconciliation

### Current truth
- Reconciled the live operator Vine catalog (1,061 listings): 1,043 now have
  durable Amazon-derived metadata, `processing_state=complete`, and
  `needs_review=true`; 18 remain explicitly `needs_attention` because the
  source/media pipeline has no usable image for the marketplace payload.
- The quality worker now reconciles both `VineImportItem.listing_id` and the
  legacy `inventory_item_id` association, so historical rows cannot remain in
  transient `queued` state after repair.
- Listing 2141 remains corrected from durable source evidence: Amazon current
  price $41.00 (ETV $46.82 fallback), New condition, RV cargo-rack category,
  capacity `15, 21, 28, and 36 gallon`, and original factual description.
- Placeholder/source-copy description audit across the two newest batches found
  zero remaining placeholder descriptions after repair. The 18 media failures
  remain concrete, per-listing image blockers; no unsupported marketplace data
  was fabricated to force readiness.

### Validation
- Production-safe Vine quality worker rerun: `updated=1768`, `missing_facts=0`.
- Focused Vine regression tests: `2 passed` (discovery image compatibility and
  fact-driven rewrite); marketplace preflight/API suites were previously green.
- Backend, worker, and beat restarted successfully; `/health` reports
  `database_ready=true`.

## 2026-09-11 - Vine evidence preservation and description quality correction

### Current truth
- Vine quality tranche remains in progress; the two newest batches have not yet
  reached a clean all-row audit.
- Fixed a shared regression where transient Amazon fetch failures erased durable
  facts, causing boilerplate descriptions and stale ETV pricing to return.
- Current Amazon page price now takes precedence over Vine ETV when available.
- Listing 2141 was corrected from verified source evidence: Amazon price $41.00,
  RV Cargo Racks category path, 15/21/28/36 gallon capacity, model 91179B,
  dimensions and original factual description; no marketplace publish occurred.
- Runtime backend health is currently healthy (`database_ready=true`).
- Added a durable `repair_vine_listing_quality` worker/beat task that revisits
  every active Vine listing from stored evidence without publishing; rows with
  missing source facts remain explicitly counted for source retrieval follow-up.

## 2026-09-10 - Deployment identity and Jobs interaction follow-up

### Findings
- Systemd unit files point all PosterPro services at `/opt/apps/posterpro/repo`: frontend `WorkingDirectory=/opt/apps/posterpro/repo/frontend`, backend/worker/beat `WorkingDirectory=/opt/apps/posterpro/repo/backend`; Caddy routes `/api/*` and `/media/*` to 8030 and the browser routes to 3030.
- The shell runtime namespace currently cannot inspect systemd PIDs or reach 8030/3030, so live component verification remains deployment/runtime-required rather than claimed complete.
- Jobs source has one active route (`frontend/pages/jobs.js`) and its Cross-post Details button calls `fetchCrosspostJob` and opens the shared Drawer. Three metric sections were found; the first two were not draggable and several cards lacked drilldown handlers.

### Fix staged
- Added per-user persisted drag/reorder and click drilldown behavior to the overview and live-system metric grids, matching the existing processing-grid behavior. Frontend build compiles successfully; live deployment identity/interaction still requires runtime verification.
- Item 2098 marketplace state could not be queried from this isolated shell; no publish/update mutation was performed.

## 2026-09-09 - Vine cohort blocker convergence after image normalization

### Verified
- Fresh eBay preflight was rerun for all 119 listings in newest Vine batch 6 through the running backend database configuration.
- Post-`49ff5aa` result: 116 `ready_with_warnings`, 3 blocked; counts reconcile to 119.
- Shared evidence aliases now resolve department/style/material/shoe size/form factor and explicit iPhone/Samsung compatibility from canonical Vine facts and title evidence.
- Safe category corrections were applied for marine hardware, microphones, lingerie, shears, ARGB controllers, and iPhone parts; quantity-zero Vine inventory rows normalize to quantity 1.
- Image repair recovered valid media for listings 2093 and 1826; both now preflight with warnings only.

### Remaining operator evidence blockers
- 1652: only a 60x40 GIF is available, below eBay image policy; requires a usable product image.
- 1084 and 1085: eBay category requires Item Height/Width/Length, but canonical Amazon facts contain no item/product dimensions; operator must supply measurements or confirm a semantically correct alternate category.
- No marketplace publishing was performed.

### Follow-up validation
- Focused marketplace preflight/API suites now pass cleanly: 54 passed.
- Queue classification now treats persisted preflight blockers as Needs Attention even when legacy rows lack `processing_state`.
- Correction worker preserves substantive provider-generated descriptions and uses deterministic evidence fallback only when no usable provider copy is returned.
- Queue visibility now treats an existing external eBay listing ID as Published even when a later revise attempt is marked failed; the failed job remains available in Jobs for diagnosis.

## 2026-09-09 - Vine required-aspect enrichment

- Fresh eBay checks identified the remaining blockers: 2179 lacks evidence for ottoman height/length/width; 2187 lacked Style/Type and is now ready with evidence-supported values; 2185 and 2191 are now ready with warnings.
- eBay aspect fallback now parses persisted `Item Dimensions` / `Item dimensions L x W x H` facts for required length/width/height fields instead of relying only on title tokens. No fabricated dimensions were written for 2179 because Amazon facts contain none.

## 2026-09-08 - Vine pricing evidence floor and newest batch refresh

- Corrected Vine pricing precedence: when scraped Amazon current price is below the durable spreadsheet ETV, the listing uses the higher ETV floor and records both values/source (`vine_estimated_tax_value_floor`).
- Refreshed batch 6 (119 newest Vine drafts) from stored product facts; 21 currently pass eBay preflight (ready/with warnings) and 9 have explicit blockers. Listing 2205 now uses `$45.99` (ETV), not the erroneous `$12.00` scrape value; no marketplace publish was invoked.

## 2026-09-08 - Ten-listing Vine correction diagnostics

- Runtime-safe in-application audit executed for listings 2177, 2167, 2170, 2174, 2186, 2178, 2179, 2185, 2187, 2191.
- All ten now have `condition=New`; bounded correction rerun produced material description/condition changes for every row. Listings 2167, 2170, 2174, 2178, 2185 completed correction; 2177/2186 retained valid ready categories but need category result reconciliation; 2179/2187/2191 remain blocked by exact required aspects/category review.
- Fresh eBay preflight blockers are persisted and exposed through admin diagnostics. No marketplace create/publish was invoked.

## 2026-09-08 - Correction queue durability and fresh preflight blockers

### Fixed
- Vine correction workers now expose persisted Amazon facts through both capability input aliases and deterministically generate evidence-backed buyer copy when a model returns unchanged/empty text.
- Correction workers refuse cross-tenant listing/job ownership mismatches instead of mutating a listing from another tenant.
- Explicit preflight requests now persist the compact fresh result so Draft Editor, Listings, and Jobs share current blocker data.
- Need-a-Correction persists a queued job and returns a truthful queued response when the broker is temporarily unavailable; the durable worker/beat can claim it later.

### Validation
- Focused correction API and stale-preflight tests: passed (including transient broker failure coverage).
- Production inspection of listings 2177, 2167, 2170, 2174, 2186, 2178, 2179, 2185, 2187, 2191 remains runtime-blocked: the shell namespace cannot reach 127.0.0.1:5432 or PosterPro services. No production row was guessed or modified.

This file is the accountability contract for the production remediation. Code
presence is `SOURCE_VERIFIED`; passing tests is `AUTOMATED_TESTED`; only a real
user/runtime workflow is `LIVE_VERIFIED`.

## POSTERPRO MASTER REMEDIATION STATUS

Status values: `NOT_AUDITED`, `BROKEN`, `REGRESSION_FOUND`, `PARTIAL`,
`IMPLEMENTED_UNVERIFIED`, `AUTOMATED_TESTED`, `LIVE_VERIFIED`,
`BLOCKED_EXTERNAL`, `DEFERRED_BY_OPERATOR`.

| ID | Requirement / operator expectation | Before / current finding | Components | Implementation / automated evidence | Live verification | Status | Remaining gap / last verification |
|---|---|---|---|---|---|---|---|
| PP-CORE-001 | Preserve production credentials and avoid approval churn | Working Google/OpenAI/bridge configuration exists | systemd env, OAuth services | No credential changes made | Production Google state checked without exposing secrets; no credentials rotated | LIVE_VERIFIED | Continue non-secret state checks only; last checked 2026-09-06 |
| PP-CORE-002 | Maintain this matrix continuously | No repo AGENTS.md existed | AGENTS.md, ops trackers | This matrix created 2026-09-06 | N/A | IMPLEMENTED_UNVERIFIED | Append every material change |
| PP-UI-001 | One coherent global shell with desktop sidebar and mobile drawer | Shell exists; live behavior not fully reverified | AppShell, PageFrame, globals.css | Frontend builds passed | Authenticated click test pending | PARTIAL | Verify navigation at four viewports |
| PP-UI-002 | Site-wide typography, cards, grids, no full-width stacks | Scoped editor/jobs improvements exist | globals.css, pages | Builds pass | Not live re-smoked | PARTIAL | Compute styles and screenshot major routes |
| PP-UI-003 | Opaque centered modal/overlay system | Prior transparent overlay regressions reported | modal/drawer components | Source fixes exist | Not live re-smoked | PARTIAL | Exercise Slate, AI, jobs, notices dialogs |
| PP-UI-004 | Notification bell stays inside viewport | Popover positioning exists in shell | AppShell notification popover | Build pass | Not live verified | PARTIAL | Test desktop/mobile edge alignment |
| PP-UI-005 | Major pages load with measured latency and no fake zeroes | Listings/alerts timeout root causes previously fixed | API routes, alert service, jobs API | Bounded queries deployed | Unauthenticated production HTML probes 2026-09-06: login 0.05s/9.8KB, listings 0.05s/2.6KB, jobs 0.05s/2.6KB, notices 0.07s/33KB, Slate 0.05s/80KB; authenticated API/render timing still pending | PARTIAL | Measure authenticated data/API and browser render timings |
| PP-SLATE-001 | Fast voice -> intake -> Head Slate -> label -> Next loop | Intake endpoints/UI exist | intake API/page, worker | Intake tests/build pass | User verified recording/transcription previously | PARTIAL | Live full loop including upload/label |
| PP-SLATE-002 | Voice guide with optional talking-point checks | Prior modal readability issue reported | Slate page/modal | Source exists | Not live verified | PARTIAL | Verify centered opaque guide and transcript checks |
| PP-SLATE-003 | Voice/notes populate structured fields with provenance | Structured AI path reaches the real Responses API; effective model is hardcoded `gpt-4o-mini` (eligible mini pool) and no OpenAI project/org headers are supplied by PosterPro | intake_slate, listing_ai | Strict schema corrected; safe provider metadata captures request timing/status; local parser extracts product, brand, catalog/model, condition, quantity, price/weight, bundle, marketplaces, completeness with provenance | CURRENT failure: production diagnostic returned HTTP 429 `insufficient_quota`; current balance exhaustion may explain refusal now. HISTORICAL August billing is separate and unresolved: PosterPro cannot access account Usage attribution or historical sharing settings. Whether the ~$2.54 `posterporadmin` traffic appeared under `data sharing incentive tier` versus ordinary billed service tier is UNKNOWN; project/org match, sharing-selection state, service tier, and complimentary-pool consumption are UNKNOWN. Operator must inspect Usage grouped by project, API key, model, service tier, input/output tokens, and cost. If August traffic has paid cost but zero incentive-tier usage, record HISTORICAL COMPLIMENTARY ATTRIBUTION NOT APPLIED and investigate project selection/enrollment/nonqualifying tier versus billing anomaly without guessing | PARTIAL | Do not retry repeatedly or alter credentials/billing. OpenAI enrichment is BLOCKED_EXTERNAL pending Platform Usage/support evidence; local fallback remains operational |
| PP-SLATE-004 | Head/tail transaction captures timestamp first and preserves UUID/IDs/location | Durable sequences and slates exist | intake models/services | Regression tests pass | Partial earlier | PARTIAL | Live browser sequence and metadata proof |
| PP-SLATE-005 | Google OAuth/destination/upload truthful states | OAuth connected previously; upload issues occurred | Google Photos service/UI | Upload code exists | Production user 2 has connected OAuth, refresh token, durable album ID, and recent Slate metadata with `UPLOADED` plus Google media ID | LIVE_VERIFIED | Keep Slate UI status sourced from persisted upload result; last checked 2026-09-06 |
| PP-SLATE-006 | Permanent server Slate archive/download/retry/bulk actions | Server artifacts exist in parts | slate assets/archive UI | Limited tests | Production DB confirms recent Slates have permanent `/media/intake-slates/...png` server assets | PARTIAL | Live archive/download/retry controls still need exercise |
| PP-SLATE-007 | Retroactive/unslated recovery and capture-time grouping | Canonical timeline migration applied | intake reconciliation worker | Late-arrival tests passed | Not live re-smoked | AUTOMATED_TESTED | Live late-photo test |
| PP-SLATE-008 | Digital label, default two copies, Write On Box, print/reprint | Label hooks exist | label service/UI | Limited tests | Not live verified | PARTIAL | Verify saved artifact and actions |
| PP-SLATE-009 | Deterministic transcript fallback keeps human-readable Slate useful during provider outage | OpenAI quota can block enrichment | listing_ai, intake_slate | Reusable regex/heuristic parser added; explicit Science Fair transcript assertions pass | Not yet rendered through live Slate transaction | AUTOMATED_TESTED | Live-generate a fallback Slate and verify product/brand/catalog/condition/quantity text |
| PP-LIST-001 | Listings load quickly with server pagination | Paginated catalog deployed | routes, listings page | API/build checks pass | Production `/listings` HTML probe 0.05s, 2.6KB (unauthenticated shell); authenticated catalog query still pending | PARTIAL | Measure authenticated catalog payload/query timing |
| PP-LIST-002 | Source x lifecycle AND filters including Vine | Backend filters repaired | routes, ListingsToolbar | Filter tests/build pass | Not re-smoked | AUTOMATED_TESTED | Verify rendered Vine combinations |
| PP-VINE-001 | Idempotent Vine import with visible progress | Import/draft path exists | vine_import_service, Vine UI | Vine tests pass | Not live re-smoked | AUTOMATED_TESTED | Confirm latest batch progress and all drafts |
| PP-VINE-002 | Vine drafts use New condition, category, images, shipping data | Recent repairs deployed | Vine service/category rules | Category IDs repaired for 52 numeric Vine suggestions; description generation no longer emits internal guidance | Recent failed-publish cohort still requires readiness recheck | REGRESSION_FOUND | Re-run readiness and bounded retry only after material payload changes |
| PP-JOBS-001 | Compact 3-column metric grids with real values/skeletons | Jobs grid code deployed | jobs page/API | Build and bounded aggregate code pass; job tables now expose priority/attempt columns | Authenticated interaction pending | PARTIAL | Click every metric and inspect persisted order |
| PP-JOBS-002 | Rearrangeable per-user metric cards | Drag handlers/persistence exist | jobs page/API | Operator reports cards still not draggable/clickable; public runtime not verified | Requires deployed browser interaction and persistence proof | REGRESSION_FOUND | Implement durable drag/drop order and metric drilldowns |
| PP-JOBS-003 | Details show chronological actor/status/error/history | Drawer/details code deployed | jobs page, marketplace_jobs API | Operator reported Details does nothing; authoritative detail hydration was added and deployed | Public authenticated click still required; status downgraded pending proof | REGRESSION_FOUND | Verify public Jobs Details click and full history |
| PP-BLOCK-001 | Blocker reasons open dedicated filtered view with compact thumbnails | Blocker route/inline repair exists | jobs/blockers page | Build/API pass | Not live verified | PARTIAL | Exercise reason links and dynamic fields |
| PP-BLOCK-002 | AI/manual inline blocker repair with provenance | Repair endpoints exist | blocker UI/API | Targeted tests exist | Not live verified | PARTIAL | Verify save and re-gate |
| PP-NOTICE-001 | Notices pagination, Gmail select-all, bulk actions | Implemented earlier | notices page/API | Build/API smoke passed | Not live re-smoked | PARTIAL | Exercise page/full-result selection and confirmation |
| PP-NOTICE-002 | Bell links to notices and deduplicates repeated issues | Notification router exists | AppShell, notifications API | Operator reports dropdown clipped off right edge | Fixed positioning deployed; public browser verification pending | REGRESSION_FOUND | Verify viewport fit and notification navigation |
| PP-EDITOR-IMAGE-001 | Reasonable image dimensions, rotation, originals preserved | Operator confirms this works | listing editor/media service | Existing build/tests | Previously user-verified | LIVE_VERIFIED | Regression anchor; do not break |
| PP-EDITOR-002 | Marketplace-style editor hierarchy and thumbnails | Two-column/media-first editor deployed | listing detail page, CSS | Build pass | User reports remaining typography concerns | PARTIAL | Live inspect without regressing image behavior |
| PP-CATEGORY-001 | Real eBay category picker and persisted taxonomy IDs | Category mapping partially repaired | ebay service/editor | Numeric Vine category IDs now persisted where available; picker public runtime not verified | Real taxonomy search/select/required-aspect refresh remains open | REGRESSION_FOUND | Implement and verify public picker |
| PP-DESC-001 | Separate clean customer descriptions from internal notes | Contamination risk remains | listing AI/services | Deterministic forbidden-pattern sanitizer now gates marketplace payload descriptions | Production contamination scan/repair pending | REGRESSION_FOUND | Scan and repair existing drafts before publish |
| PP-QUEUE-001 | Global drafting pause/resume; no infinite refinement loop | Pause controls exist in parts | settings/dashboard, workers | Limited tests | Not live verified | PARTIAL | Verify backend switch blocks jobs and resumes |
| PP-QUEUE-002 | Priority ASC scheduling, demotion, bounded retry, actor audit | Queue conventions existed without durable job priority fields | Marketplace job models, migration, worker/API | Added durable `priority`, `attempt_count`, `next_attempt_at`, and `requested_by` columns/indexes; new manual jobs default priority 0; Celery enqueue maps PosterPro priority (0 highest) to broker priority; cross-post and import failures increment attempts, demote priority, and schedule bounded backoff; compile and marketplace queue tests passed | Production migration applied and worker restarted; no live priority-edit workflow yet | PARTIAL | Add Jobs UI priority editing; last checked 2026-09-06 |
| PP-QUEUE-003 | Central AI invocation idempotency, bounded provider circuit, and runaway-usage containment | Historical Usage shows 17,545 requests and 16,607,453 combined tokens around Aug 19-20; root cause not fully reconstructable from available local data | listing_ai, pricing_service, photo_enrichment, workers, ai_guard | Shared guard now has production `ai_request_reservations`, row-locked daily budgets, reservation reconciliation, and durable provider circuit tables; duplicate signatures are suppressed across sessions, successful work is reusable, and quota/billing 429 opens the shared circuit; deterministic fallback remains available | Separate PostgreSQL processes concurrently tested same-signature ownership (one RESERVED, one DUPLICATE_SUPPRESSED) and competing 8k reservations against 10k capacity (one RESERVED, one WAITING; reserved total 8k). | PARTIAL | Add first-row/new-UTC-day race proof, stale reservation recovery, and wire every provider-capable caller to durable ledger/guard; historical breakdown unavailable |
| PP-QUEUE-004 | AI remediation convergence, capability routing, no-progress detection, and request accounting | Operator authorized continuous improvement, but August repeated passes produced little visible improvement | listing_ai, photo_enrichment, pricing_service, workers | `ai_request_ledger` is production-backed with usage/request metadata columns; ListingAI writes provider/fallback status, signature, latency, request ID, service tier and token usage, then reconciles reservations; shared guard/circuit remains active | Protected startup synthetic probe produced real validated OpenAI output and durable ledger/reservation accounting (1,839 actual tokens, default service tier); no backlog released | PARTIAL | Wire durable ledger/convergence accounting into remaining provider callers and run concurrent duplicate proof |
| PP-EBAY-001 | eBay end-to-end validation/publish/status/sold | Required-aspect repair deployed | ebay service, jobs | 9 tests; Vine jobs 454-458 completed, 458 published | Production publish confirmed for job 458 | LIVE_VERIFIED | Monitor remaining running jobs and remote account errors |
| PP-FB-001 | Facebook publish/update/sold/delist through canonical adapter | Prior bridge could report a synthetic listing result; actual live form flow unproven | connector, durable extension jobs, browser adapter, Jobs/Settings | Durable CREATE/UPDATE/END use tenant-scoped jobs; UPDATE/END require the existing marketplace URL and identity; fixture fills title/price/description/photos and stops at AWAITING_OPERATOR_REVIEW | Extension form fixture and API contracts pass; no authenticated Facebook browser E2E or listing submitted | PARTIAL | Verify seller-session selectors, destination taxonomy/condition/shipping, exact-listing edits, and result capture in operator browser |
| PP-MERCARI-001 | Mercari <=1000-char projection and lifecycle | Canonical 1000-character description projection exists; marketplace UI selectors unverified | connector, mapper, extension transport | Durable CREATE/UPDATE/END transport shares tenant lease/state handling; external identity required for update/end; only generic field fixture selectors exist | Automated transport tests; no authorized seller-session E2E | PARTIAL | Verify Mercari selectors, category/condition/shipping/package mapping, and external result capture |
| PP-POSH-001 | Poshmark market-specific handoff/lifecycle | Payload projection exists; DOM flow unverified | connector, mapper, extension transport | Shared CREATE/UPDATE/END transport; canonical brand/size/category hints mapped; destination taxonomy and condition deliberately remain review-only | Not live verified | PARTIAL | Validate Poshmark selectors, department/category/size mapping, seller session, and external result capture |
| PP-VINTED-001 | Vinted market-specific handoff/lifecycle | Country/domain and form behavior remain unverified | connector, worker, extension transport | `provider_assist` enters the shared durable queue; shared CREATE/UPDATE/END and URL identity guards apply | Not live verified | PARTIAL | Validate country domains, parcel mapping, selector behavior, and seller-session flow |
| PP-ETSY-001 | Etsy API-first listing lifecycle | No direct Etsy API connector/OAuth flow exists; extension fallback only | connector, OAuth/API, extension transport | Shared assisted CREATE/UPDATE/END contract exists; Etsy API/OAuth, shop/taxonomy/shipping/inventory synchronization are not implemented | AUTH_REQUIRED for direct integration; assisted flow not live verified | PARTIAL | Implement Etsy OAuth/API when app authorization exists; otherwise validate assisted selectors and review requirements |
| PP-OFFERUP-001 | OfferUp assisted listing lifecycle | Connector scaffold exists; destination form not verified | connector, mapper, extension transport | Shared durable CREATE/UPDATE/END contract and marketplace-domain identity guard apply | Not live verified | PARTIAL | Validate current OfferUp host/form, local-vs-shipping options, taxonomy, condition, and result capture |
| PP-EXT-001 | Installable, paired browser extension with secure tenant/device queue | Pairing is still a one-time manual authorization; normal queue work is automatic after pairing | browser-extension, bridge, extension API, Settings/Jobs UI | Dynamic ZIP; hashed device token; automatic heartbeat/poll/claim; exclusive leases with expiry recovery; expired owner cannot revive lease; unknown CREATE outcome blocks retry; Jobs owns review/confirmation; popup only shows connection/task/pause/resume/diagnostics; marketplace credentials are never copied to PosterPro | ZIP verified from live endpoint (10 files, version 0.2.0); backend/fixture tests pass; no operator Chrome/Edge install or authenticated marketplace E2E | PARTIAL | Install/pair once in operator browser, then verify automatic claim and a safe form-fill review; live site/session behavior remains external |
| PP-MARKET-001 | Shared explicit marketplace capability contract | Previous fallback connectors implied publish parity | connectors/capabilities.py, registry, field mapper | Direct eBay and assisted capability flags are explicit; assisted UPDATE is gated by external identity; non-eBay payloads receive semantic category hints rather than eBay taxonomy IDs; Etsy remains without direct API | Source + automated connector/payload tests only | AUTOMATED_TESTED | Add destination-specific taxonomy/condition validation and live account checks |
| PP-MARKET-JOB-001 | Durable tenant-scoped assisted job lifecycle | Crosspost parent and extension work were disconnected; some paths could fabricate success | extension models/migration, API, workers, Jobs | Normal publish/crosspost workers enqueue shared child jobs; devices claim with exclusive leases; CREATE/UPDATE/END preserve confirmed external identity; expired leases cannot be revived by former owners; ambiguous post-submit CREATE blocks retry; Jobs exposes child details/review confirmation; failed UPDATE preserves Published lifecycle | 61 marketplace/eBay/API tests pass (extension suite rerun: 11 passed); scoped migration applied; backend/frontend restarted; health and local/public Jobs routes return 200 | OPERATOR_TEST_REQUIRED | Pair the current ZIP in an operator browser and safely walk a job to AWAITING_OPERATOR_REVIEW |
| PP-IDENTITY-001 | Per-marketplace external identity and duplicate protection | MarketplaceListing already stores marketplace-specific identities; CREATE guard was incomplete | MarketplaceListing, extension job API | CREATE refuses active external identity; END requires matching marketplace-domain URL; UPDATE/END result cannot replace confirmed identity | Automated tenant/identity tests only | AUTOMATED_TESTED | Add concurrent production DB claim and live external identity verification |
| PP-SALE-FANOUT-001 | One-time inventory reconciliation and cross-market delist | Sale polling is currently eBay-only; previous fallback could mark assisted listings deleted without action | sale detection, extension END, marketplace listings | Polling now filters by declared sale-poll capability; assisted END is queued with exact external identity and only completion sets DELETED; source listing status is SOLD, not deleted | Synthetic service tests only | AUTOMATED_TESTED | Operator-confirmed sale and extension END live flow; exactly-once production sale reconciliation |
| PP-BULK-001 | Bulk publish distributes eligible selected listings via durable marketplace jobs | Existing publish-bulk endpoint fan-outs selected IDs to worker tasks | marketplace orchestrator, publish worker, Listings UI | Browser/provider-assisted standard publish worker now creates durable extension jobs after fresh preflight; existing per-listing/crosspost dedupe retained | Automated worker tests only | AUTOMATED_TESTED | Prove selected multi-listing batch creates expected independent jobs; no live submissions |
| PP-ROUTING-001 | Category/rule-driven marketplace routing with manual override | No complete rule-builder/routing policy found in current path | marketplace setup, listing taxonomy, bulk routing | Not implemented in this tranche | Not verified | NOT_AUDITED | Design/test category/condition/price rules before automated routing |
| PP-SLATE-TIMELINE-001 | Relevant album/archive timeline loads | No dedicated timeline page existed | intake timeline API/page | Timeline API and active page preserve capture-time order; live Google media corpus audited | 2,487 active Google Timeline images are locally decodable and all 2,487 served media paths returned successful image responses; authenticated operator interaction remains unverified | OPERATOR_TEST_REQUIRED | Exercise Timeline while authenticated |
| PP-SLATE-TIMELINE-002 | Compact chronological filmstrip/group visualization | Queue showed cards but no filmstrip | intake/timeline.js | Production build passes; contiguous containers now alternate from canonical image-group index, independent of Slate number | Live route is served; visual browser interaction not available from this verification | OPERATOR_TEST_REQUIRED | Confirm group shading and zoom in operator browser |
| PP-SLATE-TIMELINE-003 | Add Slate between arbitrary photos | No insertion controls on timeline | timeline UI, retroactive Slate API | Explicit `+ Add Slate` controls remain; retroactive marker references resolve to exact persisted boundaries | Source/build verified; authenticated insertion not exercised in this pass | OPERATOR_TEST_REQUIRED | Exercise head/middle/end insertion in operator UI |
| PP-SLATE-TIMELINE-004 | Retroactive Slate reuses existing voice/text Slate creation | No backend route | intake API, Slate service | Production route now validates ownership/order, derives boundary, and returns concrete regroup preview; `/timeline/regroup/apply` applies associated photos transactionally | Backend compile/health verified; render/upload/undo/operator proof pending | AUTOMATED_TESTED | Complete undo/render/upload and public verification |
| PP-SALES-001 | Aggregate sales facts across marketplaces | Canonical reconciliation engine exists | sales/reconciliation services | Operator reports recent eBay sale was not detected | Live event ingestion and notification proof missing | REGRESSION_FOUND | Trace eBay poll/scheduler and reconcile missed sale |
| PP-SALES-002 | Sale decrements once and delists unrelated channels safely | Engine supports this | reconciliation worker | Operator sale was missed; downstream delist/ship flow therefore unproven | Requires live/synthetic event proof after ingestion repair | REGRESSION_FOUND | Verify sale lifecycle and cross-market fanout |
| PP-SLATE-TIMELINE-011 | Timeline zoom/density control | Filmstrip fixed-size thumbnails | timeline UI | Bounded zoom persists per browser and retains horizontal scroll position | Production build passed; authenticated operator confirmation remains pending | OPERATOR_TEST_REQUIRED | Verify zoom across long sessions |
| PP-SLATE-TIMELINE-012 | Historical classification cleanup and manual Slate designation | False legacy Slate labels | intake timeline/API | Head/Tail and photo classification controls use explicit roles; synthetic Slate IDs are accepted; reset, filters, counts, and selection remain | Backend integration tests pass; authenticated operator interaction/reset undo remains pending | OPERATOR_TEST_REQUIRED | Verify reset does not alter saved group positions and manual provenance persists |
| PP-SLATE-TIMELINE-013 | Classification history/undo and grouping-safe correction | previous_classification metadata only | intake timeline/API | IntakeReconciliationEvent snapshots plus authenticated undo endpoint implemented | Production apply/undo and regroup interaction not live verified | PARTIAL | Complete true regroup transaction and operator test |
| PP-SLATE-TIMELINE-014 | Group-scoped primary, Head/Tail semantics, safe delete, Google media integrity | Batch-wide primary clearing, decorative Tail role, and stale Google local paths | intake API/service, listing media materializer, Timeline UI | Canonical item grouping now scopes manual primary; Head/Tail assigns following/preceding captures; exact authoritative Slate/photo pairs soft-delete; deleted media is excluded from grouping/materialization; all current active Google Timeline files were decoded and served successfully | Eight focused Timeline/intake integration cases pass; 2,487 active Google photos pass local and thumbnail decode/HTTP checks; live authenticated button interaction remains operator-required | OPERATOR_TEST_REQUIRED | Exercise Set/Clear Primary, Tail, Delete, and group backgrounds with operator |
| PP-CORRECTION-001 | Need a Correction creates prioritized executable remediation work | Request only moved listing to Draft | listing editor/routes | Priority field (default 0), selected fields, free-text instructions, provenance and queued state persist in source metadata | Worker execution/order and before/after material delta remain unverified | IMPLEMENTED_UNVERIFIED | Add durable correction worker and Jobs history |
| PP-CORRECTION-002 | Manual correction precedence, convergence, and audit history | No durable correction audit | listing editor, revisions, jobs | Requests retain priority/requester/timestamp and revision history context | No-op prevention, superseding, reprioritization, and same-ID external update verification remain open | PARTIAL | Implement bounded worker convergence |
| PP-DATA-001 | Canonical generic identity gate and blocker repair | Generic gate/API defense exists | listing_processing, routes | Existing tests | Not re-smoked | AUTOMATED_TESTED | Semantic cohort audit |
| PP-VERIFY-001 | Three-level evidence and truthful tracker updates | Prior claims mixed levels | AGENTS/ops docs | Matrix now records levels | N/A | IMPLEMENTED_UNVERIFIED | Update after each material action |
| PP-VERIFY-002 | Responsive verification at 1920/1536/1366/mobile | Source responsive rules exist | frontend | Builds pass | Not current live screenshots | PARTIAL | Authenticated browser/device verification |
| PP-EDITOR-SYNC-001 | Save persists PosterPro changes without touching marketplaces | Editor had Save draft only | listing detail page, routes | PATCH now records a durable ListingRevision, increments posterpro_revision, and marks active listings local_changes_not_published without queuing marketplace jobs; compile/build passed | Production route deployed; external mutation path remains untouched; browser click not available in this environment | AUTOMATED_TESTED | Operator browser verification remains |
| PP-EDITOR-SYNC-002 | Save & Publish Changes persists and queues updates for active marketplaces | Operator reported HTTP 500; root cause was PostgreSQL enum mismatch from comparing `MarketplaceListing.status` to lowercase strings | routes, marketplace jobs, editor | Endpoint now uses `MarketplaceListingStatus.PUBLISHED/UPDATED`; direct production DB-backed route exercise returned success and queued zero jobs for listing 759 with no active external listings | Production active eBay listing 2190 exercised: canonical save succeeded, one UPDATE job queued for existing external ID `188896330968`, job completed with same listing identity and no CREATE; second identical submission reused revision/job with no duplicates | LIVE_VERIFIED | Authenticated browser click remains an operator-side confirmation; remote eBay mutation completed safely with same ID |
| PP-EDITOR-SYNC-003 | Per-marketplace delta/update handling and validation | Existing publish path skipped already-published rows | marketplace worker, eBay service | eBay UPDATE reuses revise_ebay_listing and external identity; assisted channels now fail explicitly as UPDATE_NOT_SUPPORTED_FOR_MARKETPLACE instead of falling through to CREATE; field delta includes marketplace_data, platform quantities, labels, images | Worker compiled/deployed; no remote marketplace mutation performed | AUTOMATED_TESTED | Cross-market adapter update support remains a documented limitation |
| PP-EDITOR-SYNC-004 | Partial update failure, retry, and revision/history visibility | Cross-post jobs support retry but not update-specific history | marketplace jobs, editor, ListingRevision | Production listing_revisions table/model and `/listings/{id}/revisions` endpoint record before/after fields, actor, revision, targets, status and per-market results; worker writes completion/partial-failure results; sync states are visible in editor | Active eBay update job 460 completed; revision 2 records target/result and synced state. Identical resubmission returned `deduplicated=true` with revision/job counts unchanged | LIVE_VERIFIED | Assisted-market partial failure remains separately limited |

### 2026-09-06 audit note

2026-09-07 deployment-truth checkpoint: systemd backend/frontend/worker all run from `/opt/apps/posterpro/repo` (backend WorkingDirectory `/opt/apps/posterpro/repo/backend`, frontend `/opt/apps/posterpro/repo/frontend`); public `/api/deployment` returns source repo and backend commit `84e7c8a2ecac119022a5f7203c2d4564dc088a61`; public extension GET returns HTTP 200 application/zip with valid manifest. Jobs Details now hydrates the authoritative detail endpoint; operator runtime click remains required. Notification panel positioning changed to viewport-fixed bounded panel; operator runtime verification remains required. Operator-reported Jobs metric drag/drilldown and sale detection remain REGRESSION_FOUND. Customer-description sanitizer now removes known internal guidance before marketplace payload generation; production inventory scan remains open.
2026-09-07 Vine/category regression continuation: production scan found 1,116 contaminated descriptions (1,037 Vine, none published). Root cause confirmed: Vine draft generator intentionally appended internal review/category guidance, and many rows persisted numeric taxonomy only in `category_suggestion` rather than `category_id`. Generator now emits buyer-facing condition only; numeric Vine categories are copied into `category_id`. Existing records require bounded evidence-driven rebuild; no external publish was triggered.
2026-09-07 Vine readiness continuation: sanitized all 1,116 contaminated descriptions in production while preserving factual lines; repaired 52 numeric Vine category IDs into `category_id`; removed internal guidance from generator; added `/ebay/taxonomy/suggestions` authenticated endpoint backed by the existing eBay taxonomy service. This is source/deployed evidence only; public editor picker and eBay readiness recheck remain operator/runtime unverified. No marketplace publish was triggered.
2026-09-07 taxonomy-picker continuation: replaced the temporary single inferred category response with multi-result eBay taxonomy suggestions from the live category-suggestion API, returning IDs/names/tree metadata. Listing Editor now exposes a real search/select control that persists the chosen ID/path through the existing save path. Frontend rebuilt and deployment restarted; authenticated public selection and required-aspect refresh remain OPERATOR_TEST_REQUIRED. No marketplace mutation performed.
2026-09-07 sales trace: Celery beat is dispatching `poll_for_sales` every 15 minutes with dry_run=false. A live eBay order (`26-15104-91986`, item `188890426782`) is present and reconciled to listing 2168; sale row 78 is SYNCED, quantity is zero, and marketplace rows are deleted. The operator-visible failure was missing sale/shipping notification; sale processing now emits durable `SOLD - NEEDS SHIPPING` notification for newly detected sales. Existing sale row predates this notification change, so a live notification backfill/verification remains open.
2026-09-07 sale-notification continuation: backfilled the missing `SOLD - NEEDS SHIPPING` notification for sale 78 and exposed `pending_shipments` in `/sales/dashboard`. Polling logs show scheduled execution; eBay order ingestion succeeds for user 2. Backend restarted healthy. Public operator notification visibility remains browser-verification pending.

2026-09-07 additive Usage evidence: operator verified 16,607,453 total tokens (5,015,082 incentive; 11,592,371 default) and approximately 17,545 requests around Aug 19-20. Production `ai_request_ledger` table and indexes are present. Shared AI guard now covers ListingAI, pricing, photo, photo-group, and template extraction; durable cross-process ledger wiring remains open.
2026-09-07 AI safety continuation: added configurable `COMPLIMENTARY_ONLY` mode with separate mini (2.35M safe ceiling / 2.5M entitlement) and large (225k / 250k) pool settings plus UTC reset configuration. Added production `ai_daily_budgets` migration artifact/table. Listing-processing and voice-intelligence paths now pass DB context so durable AI ledger rows can be written; services restarted healthy. Full transactional reservation/reconciliation and all remaining callers are still open.
2026-09-07 enforcement continuation: applied reservation/circuit and ledger-usage migrations to production PostgreSQL. Verified row-locked reservation, duplicate suppression, reconciliation, insufficient-budget withholding, UTC reset timestamp, and durable circuit schema. A single protected startup synthetic request reached real gpt-4o-mini and returned validated structured output; ledger recorded request ID, default service tier, and 1,839 total tokens, reservation reconciled. Subsequent identical startup probe was withheld by durable reservation state; backlog remains unreleased.
2026-09-07 semantic enforcement continuation: successful reservation rows now persist structured results for durable cache reuse; reconciled signatures suppress provider calls across restart while preserving prior result provenance. Added reservation result storage migration. True concurrent multi-process stress and full waiting-job scheduler resume remain open.
2026-09-07 concurrency continuation: added PostgreSQL advisory transaction locks for signature claims and first budget-row creation, widened waiting state storage, and persisted WAITING_FOR_DAILY_AI_RESET work records without polling-count drift. Independent subprocess tests proved same-signature single ownership and limited-budget competition (one reservation, one waiting; no overspend). First-row/new-day and scheduler resume remain open.
2026-09-07 reset/recovery continuation: wired `resume_waiting_ai_work` into Celery beat (UTC-minute bounded batch), releasing prior-day waiting work and recovering stale RESERVED/RUNNING/DISPATCHED reservations with retryable state. Simulated prior-day waiting record released cleanly; worker restarted healthy. New-UTC-day first-row race and full caller/convergence audit remain open.
2026-09-07 new-day concurrency continuation: added injectable budget date and ran independent PostgreSQL processes against a missing 2030-01-01 mini budget row. One process reserved, the other duplicate-suppressed; exactly one budget and reservation row existed with no transaction error. Safety persistence exceptions in ListingAI now emit structured warnings instead of silent pass. Full caller inventory/convergence and real Science Fair Slate proof remain open.
2026-09-07 editor-sync regression follow-up: reproduced Save & Publish HTTP 500 as PostgreSQL enum mismatch and corrected route comparisons to canonical `MarketplaceListingStatus` values. Added backend rapid-double-submit deduplication by latest identical save_publish revision; source compile passed. Production DB/browser verification is currently environment-limited.

2026-09-07 timeline classification/zoom continuation: timeline thumbnail widths now bind to persisted zoom levels (48/64/88/120/160px), saved zoom is loaded on mount, and scroll position is retained while changing density. Asset cards expose immediate MARK AS SLATE / MARK AS PHOTO controls plus bulk selection, filters, and counts. Classification display prioritizes MANUAL_OPERATOR and explicit image_type/is_slate; legacy probable-slate metadata is shown only as POSSIBLE SLATE. Classification and bulk-reset operations now persist durable IntakeReconciliationEvent snapshots with authenticated undo support; reset accepts scope/photo_ids, preview, and preserve_modern options. Frontend build/backend compile completed; public operator classification/reset verification remains OPERATOR_TEST_REQUIRED.
2026-09-07 correction workflow continuation: Need a Correction now accepts and persists an explicit non-negative priority (default 0), operator provenance, selected fields, free-text instructions, request timestamp, and truthful queued/drafting-paused state in listing metadata. The editor exposes the priority control and sends it through the API. This remains IMPLEMENTED_UNVERIFIED: a durable worker claim/order and before/after material-delta execution test are still required; no-op correction must not be considered complete.
2026-09-07 correction queue continuation: Added durable `listing_correction_jobs` PostgreSQL table/model with priority, requested fields/instructions, requester, lifecycle timestamps, snapshots, material-delta/result fields, and superseding linkage. Need a Correction now creates a queue row and exposes its ID through listing metadata; migration applied to production and backend compile/health passed. Worker claim/execution, exact priority-0 newest-first ordering, and before/after no-op enforcement remain IMPLEMENTED_UNVERIFIED.
2026-09-07 correction worker continuation: Added `process_listing_correction_jobs` Celery task with PostgreSQL row-lock/skip-locked claim ordering (priority ascending, newest-first within priority 0), targeted field execution, operator-note context, before/after snapshots, material-delta decision, and NO_PROGRESS protection. Need a Correction dispatches one bounded worker task after persistence; backend/worker restart and health passed. Real persisted ordering and end-to-end operator correction remain OPERATOR_TEST_REQUIRED.
2026-09-07 correction semantics correction: Worker no longer copies operator instructions into title or customer description. It passes the note as `operator_instruction` to ListingAI, applies generated title/description only for requested fields (eBay title capped at 80 chars), sanitizes the generated buyer copy, and records category suggestion deltas rather than treating an existing category ID as a fix. Queue ordering now explicitly uses newest-first only for priority 0 and FIFO for priorities above 0. Source compiles; service restart/DB ordering probe is pending environment access.
2026-09-07 correction execution continuation: Worker now loads persisted source/Amazon evidence into the existing ListingAI capability, treats operator notes as guidance, records per-field result structures with evidence/capability/material-change/decision, and reports PARTIAL/NO_PROGRESS rather than unconditional success. Added authenticated correction-job listing and queued-job reprioritization API with priority history. Backend source compiles; runtime worker/order probe remains pending service/database access.
2026-09-07 correction taxonomy continuation: FIX CATEGORY now performs real taxonomy candidate lookup through the existing eBay category service when an account is available, persists the selected real category ID/path/provenance for an unambiguous result, and retains candidate data for operator review when ambiguous. AI is not invoked solely for category-only corrections. Source compile passed; live taxonomy/account execution remains runtime-unverified.
2026-09-07 correction runtime probe: Production PostgreSQL probe exposed invalid SQL from attempting DESC/ASC inside one CASE expression. Replaced it with two CASE sort terms; direct worker claim test now executes successfully and confirms empty requests become `needs_review` rather than false completion. Five-row ordering probe was run with rollback-safe fixtures; p0 newest, p0 older, p1 FIFO, then p2 ordering was observed. No listing data was changed.
2026-09-07 correction ordering/runtime continuation: Fixed worker and correction-job API ordering to use separate CASE sort terms (p0 newest-first, p1+ FIFO); production probe successfully claimed rollback-safe fixtures in D/C/A/B/E order and no duplicate claims occurred. Added `test_agents_matrix.py`, which passes and prevents duplicate requirement IDs. Production eBay taxonomy search returned multiple real leaf candidates for `electronic project kit`. Services are active and backend health is 200.
2026-09-07 correction Jobs integration: Marketplace jobs overview now includes durable correction jobs with priority, requested fields, operator note, status, attempts, material delta, result, and failure reason; queued correction reprioritization remains available through the authenticated API. Backend schema/compile and production health passed; public Jobs rendering remains operator-browser verification required.
2026-09-07 correction Jobs UI continuation: Added a Correction Jobs tab to the Jobs console using the authoritative overview feed, showing listing, priority, fields, attempts, operator instruction, status, and failure reason. Frontend production build completed and frontend service is active; public authenticated interaction remains OPERATOR_TEST_REQUIRED.
2026-09-07 timeline regroup safety continuation: Regroup apply now validates retroactive Slate ownership and boundary-photo ownership, and scopes its photo query to the boundary batch instead of mutating all user intake photos. Backend compile and post-restart health passed; full canonical item/batch transactional regroup remains incomplete and unverified.
2026-09-07 correction category semantics continuation: Category correction now ranks multiple real eBay taxonomy candidates using product/operator terms, persists a real leaf candidate only when the ranking is distinguishable, and retains ranked candidates for operator review when ambiguous. Per-field results now report category ID/path before/after rather than looking up a nonexistent generic field. Source compile passed; current PostgreSQL runtime access is intermittent.
2026-09-07 correction UI continuation: Jobs overview and Jobs console include correction records; frontend build completed after integration. Service restart/public browser verification is currently blocked by renewed systemd permission denial; no runtime completion claim made.
2026-09-07 category/aspect and correction-details continuation: Taxonomy suggestions now perform explicit category-subtree verification instead of assuming leaf=true; correction worker requires verified publishable candidates, attempts required-aspect population, and reports category/aspect blockers without completing falsely. Field snapshots include item specifics, price, condition, and images. Jobs correction cards now expose reprioritization and expandable before/after/result details. Backend compile and frontend build passed; authenticated public verification remains pending.
2026-09-07 Listings filter regression fix: Root cause was `sameListingsQuery` rebuilding an already URL-shaped query with state defaults, so shallow URL synchronization repeatedly overwrote explicit filters. Comparison now preserves URL-shaped keys (`source`, `page_size`, `q`, etc.) and no longer resets lifecycle/source/page-size selections during fetch/poll cycles. Frontend source patched; build/runtime verification pending.
2026-09-07 manual taxonomy browser: Added authenticated lazy eBay taxonomy browse endpoint backed by category-subtree data, with child/leaf/publishable metadata and tree ID. Listing Editor now offers Search plus hierarchical Browse Categories with drilldown and manual leaf selection persisted as MANUAL_OPERATOR. Backend compile passed; public authenticated browse/aspect refresh remains operator-test required.
2026-09-07 taxonomy browser runtime continuation: Corrected root browse API path to `/commerce/taxonomy/v1/category_tree/{tree_id}` (subtree remains `/get_category_subtree`). Production eBay account probe returned 34 root categories with child/leaf metadata; no listing mutation performed.
2026-09-07 Listings runtime regression continuation: Identified shared `sameListingsQuery` state-vs-URL shape mismatch as the source of filter snap-back; patched URL-shaped query comparison and preserved explicit lifecycle/source/page-size state through shallow synchronization. Frontend build completed; service restart/public authenticated filter persistence remains runtime verification required.

Production eBay failure analysis found category-required aspect blockers, not
missing Amazon data. Latest retry jobs 454-458 completed after safe Type/MPN
mapping repair; job 458 was confirmed published. This does not close unrelated
requirements above. Credentials were not rotated or printed.
2026-09-07 continuation: Listings query comparison now canonicalizes state- and URL-shaped filters across lifecycle/source/page/sort keys, preventing hydration/poll refresh from restoring defaults. Listing Editor eBay taxonomy browser now supports clickable breadcrumb/back navigation, verified-leaf gating, and persists full manual category path/tree/provenance metadata. Correction worker category results now verify taxonomy leaf/publishability, evaluate required aspects, persist structured category states, and field results expose actual category/aspect before/after values. Source/build verification pending deployment/runtime acceptance.
2026-09-07 continuation: Added authenticated request-path regression coverage proving Need a Correction creates a durable priority-0 job with exact fields/instruction and listing correction metadata (no direct SQL insertion). Category worker now verifies taxonomy before fetching aspects and emits truthful category/aspect states; correction Jobs display operator-readable per-field before/after diagnostics and listing status. Runtime production execution remains pending safe draft selection; no publish/create invoked.
2026-09-07 listings queue regression: fixed NULL-safe visibility and bucket ordering so review/ready filters compose correctly for records without image arrays or quantity values; targeted queue regression tests now pass. Added authenticated Need-a-Correction API-path test and scoped regroup event/undo persistence. Runtime full operator correction and Vine cohort audits remain pending safe production execution.
2026-09-07 timeline classification continuation: `POST /intake/timeline/classify` now emits a durable process notification containing affected photo IDs/classification and returns notification_id. Existing serializer exposes authoritative classification fields; frontend refresh path remains in place. Visual selected-slate highlighting and browser verification require final frontend runtime pass.
2026-09-07 performance/UI truth continuation: Runtime probes were blocked by DNS/service permissions, so no fabricated latency claims were made; baseline records UNMEASURED paths. Jobs now avoids unrelated global dashboard fetches. Notification panel is rendered through a body portal to remove transformed/overflow parent containment. Added public frontend deployment identity route exposing non-secret git/build/source metadata. Frontend build passed; runtime/browser verification pending.
### 2026-09-07 - Timeline regression audit and restoration
- Restored the full pre-rewrite intake timeline surface, including retroactive Slate boundary creation, selected-photo timeline details, zoom persistence/scroll retention, filters/counts, reset, per-photo and bulk classification controls.
- Classification remains optimistic and authoritative (`MANUAL_OPERATOR`) with visible lime Slate highlighting and inline success/failure feedback; routine classifications no longer create process-notification-center spam (durable reconciliation events remain).
- Frontend deployment identity now reports build-time metadata from Next config rather than request time. Production/browser verification remains pending when service/DNS access is unavailable.
- Follow-up: timeline cards no longer nest checkbox/classification controls inside a native `<button>` (invalid interactive HTML that prevented bulk selection actions from reliably receiving clicks). Cards use keyboard-accessible divs and real button controls; production interaction still requires browser verification.
- Navigation regression: AppShell now resolves the most-specific matching route before applying active styling, so `/intake/timeline` highlights Photo Timeline only and no longer also highlights the `/intake` parent.
- Bulk Slate interaction hardening: selected IDs are normalized to numeric photo IDs, bulk controls stop propagation/default form behavior, and timeline card selection no longer uses nested native buttons. Metric cards also expose keyboard-accessible click semantics. Frontend build passes; live site is currently unreachable from this environment.
- Persistent Slate display correction: timeline serialization now exposes authoritative top-level classification/source fields; the UI prioritizes them and renders a high-contrast neon-green border/ring plus an in-image `SLATE` badge so a saved Slate remains visibly distinct after refresh.
- Root cause of reported `Classification failed: Input should be a valid dictionary`: Timeline classify/reset API helpers omitted `Content-Type: application/json`, so FastAPI received the JSON text as a string. Both helpers now send the correct header; backend serializer exposes classification fields and both services were restarted.
- Classification semantics tightened per operator confirmation: only MANUAL_OPERATOR or provably modern generated Slate provenance renders as SLATE; legacy/probable detector metadata and unmarked historical `is_slate` rows render as PHOTO (not POSSIBLE SLATE). Timeline image URLs now pass through the thumbnail derivative helper at the active zoom dimensions.
- Add Slate 500 root cause: the classification serializer fields were accidentally added to `_serialize_slate` (which has no `image_type`), so retroactive Slate creation crashed after the Slate was created. Fields are now correctly scoped to `_serialize_photo`; backend compile/restart and health check pass.
- Add Slate correction: fixed the retroactive Slate 500 (`IntakeSlate` serializer was reading photo-only `image_type`), exposed `slate_id` in timeline photos, and manual Slate classification now provisions an official PosterPro `IntakeSlate` through the existing create_slate pipeline while linking the photo. Timeline cards expose EDIT SLATE and VOICE NOTE links; services restarted and healthy.
- Multi-tenant audit started: fixed normal authenticated catalog and sales export/dashboard defaults to current_user scope, and review detail hydration now filters listings by current user. Existing explicit admin scope remains controlled by resolve_user_scope; no Kevin account created yet pending full audit and secure temporary-password input.
- Multi-tenant isolation pass: normal `/listings` requests now always scope by `current_user.id`; sales dashboard and CSV exports default to the authenticated tenant; marketplace job review hydration filters listing IDs by owner. Registration now assigns non-admin accounts the normal `owner` role so standard store capabilities (including Vine) are available without platform-admin privilege. Full backend-wide audit and Kevin account creation remain pending.
- Multi-tenant audit continuation: closed an unauthenticated listing-template apply path by requiring the authenticated tenant to own both listing and template; eBay offer resolution and sale deduplication now include account/user ownership so colliding external IDs cannot cross tenants; recovery sibling lookup is tenant-scoped. Added a two-tenant template IDOR regression test. AppShell's unstaged notification portal positioning change was audited as legitimate layout remediation and remains intentionally unstaged pending broader work. Kevin account creation remains deferred until the backend-wide isolation/IDOR/worker audit is complete.
- Vine correction execution fix: queued revision requests now clear stale review flags so corrected Vine drafts remain in Drafts while work is queued; the worker loads persisted `amazon_product_facts`, passes them as source evidence, enforces Vine condition `New`, and derives category search terms from product facts. Draft workspace now exposes condition selection and candidate category selection controls. Added a regression test proving API request -> queued draft -> worker uses source evidence, rewrites title/description, and enforces New condition. Frontend build and targeted test pass; live Celery execution remains runtime-dependent.
- Vine correction deployment: backend/frontend/worker services restarted successfully and backend health reports `database_ready: true`; targeted correction/API tests pass. The end-to-end test initially exposed the test fixture's separately imported worker SessionLocal, which is now explicitly patched in the regression test. Production Vine correction execution remains observable through durable job status; no marketplace publication was invoked.
- Multi-tenant worker/query continuation: inventory bulk worker chunks now scope listings to the owning durable BulkJob; sold-sync API rejects foreign listing IDs before queueing; pricing AI guard signatures include listing tenant identity. Merged recovery children remain hidden from all catalog queues. Added two-user notification/job and sold-sync IDOR tests; focused marketplace suite (27 passed) and duplicate-ID matrix test pass. Media static mount, AI funding policy, settings/storefront/cache inventory, and remaining worker/query audit are still open; no Kevin account created.
# 2026-09-07 - Modern Slate timeline markers

- Timeline now replaces classified legacy Slate image rows with durable modern
  Slate markers in the authenticated timeline response, preserving item/box/
  location metadata and the existing edit/voice-note links. Retroactive Slates
  are inserted at their effective boundary and remain internal-only.
- Added regression coverage for retroactive Slate marker visibility and modern
  provenance. Backend compile and focused timeline tests pass. Service restart
  is runtime-blocked in this shell (systemd bus unavailable).

# 2026-09-07 - Draft category selector visibility

- The listing draft workspace now always renders a dedicated eBay category
  selector, even when no ranked candidates are present, alongside a verified
  category-ID field for manual entry. Existing taxonomy browser/search remains
  available in the full ListingEditor. Frontend production build passed.

# 2026-09-07 - Draft condition/category autosave

- Selecting a condition or eBay category in the listing draft workspace now
  immediately PATCHes the selected field, updates local state from the server
  response, and provides success/error feedback; Save Draft is no longer
  required for those selections. Frontend build completed successfully.

# 2026-09-07 - Slate marker insertion boundaries

- Retroactive Slate creation now accepts modern timeline marker references and
  resolves them to persisted boundary photos. Add Slate therefore remains
  usable beside an existing modern Slate instead of submitting ``slate-<id>``
  to the integer-only boundary API.

# 2026-09-08 - Legacy Slate metadata migration

- Timeline reads now perform an idempotent compatibility migration for legacy
  image-based Slates: each is linked to an official tenant-owned Slate at the
  same capture position, carrying item ID, box, location, notes, and legacy
  metadata. The timeline then replaces that image in-place with the modern
  Slate marker and its edit/voice-note actions. Focused timeline tests pass.

# 2026-09-08 - Deterministic legacy Slate positioning

- Corrected replacement ordering so each migrated legacy image Slate is
  replaced at its original timeline index rather than removed and reinserted
  using a shifted list index. Added a two-Slate idempotence regression fixture;
  it verifies exact photo/Slate/photo/Slate/photo ordering and inherited
  metadata.

# 2026-09-08 - Slate boundary hardening

- Strengthened the legacy replacement fixture to assert every ordered photo and
  modern marker identity, inherited metadata, and stable IDs across repeated
  timeline reads. Marker payloads now expose legacy photo/source/capture
  provenance for auditability.
- Hardened retroactive marker resolution to enforce current-user Slate
  ownership and fixed regroup apply's batch lookup so Add Slate boundaries do
  not dereference an integer as an ORM object. Adjacent Add Slate boundaries
  have focused coverage. The compatibility migration remains bounded and
  idempotent on GET; a dedicated one-time backfill remains follow-up work.

# 2026-09-08 - Timeline chronology and display counts

- Timeline chronology is assembled before pagination, keeping modern replacement
  Slates at the exact legacy capture position instead of appending markers whose
  source photo falls outside the first window. The endpoint now reports total,
  photo, and Slate counts separately and adds independent Slate/photo/group
  display numbering without inflating the underlying photo count.

# 2026-09-09 - Amazon dimension evidence recovery

- Amazon product-page extraction now normalizes Item/Product/Package Dimensions
  into structured evidence while retaining raw source text and dimension type.
  Vine refresh maps supported item/product dimensions into eBay
  Item Length/Width/Height, replacing stale ``Does Not Apply`` placeholders.
- Live listing 2179 (ASIN B0H6WSH554) was refreshed from the original Amazon
  page: 24.04 x 24.04 x 14.57 inches. Fresh eBay preflight is now
  ``ready_with_warnings`` with no blockers. No marketplace publish was run.

# 2026-09-10 - Mutually exclusive catalog queues

- The Listings catalog now treats ``All Listings`` as the active catalog for
  every source: published, ready, drafts, review, attention, and failed rows
  are included while sold and archived rows are excluded.
- Needs Review is limited to unpublished rows with a ready/ready-with-warnings
  preflight; blocked review rows and approved rows whose readiness regressed are
  surfaced in Needs Attention. Sold rows cannot appear in Published, and queue
  classification is shared by the paginated API and the workspace.
- Added regression coverage for Vine source composition, sold/archive
  exclusion, blocked review routing, and approved-but-blocked rows.

# 2026-09-10 - Marketplace image path normalization

- eBay image payload construction now considers normalized `listing_images` as
  well as the legacy `image_urls` compatibility field, resolving valid local
  media through the existing public media URL path without duplicating
  canonical facts.
- Preflight trusts the publish-plan image validation when a usable public image
  URL was produced, avoiding false `EBAY_IMAGE_URL_INVALID` blockers caused by
  checking only a stale compatibility path. Added regression coverage for the
  normalized-image payload path; live cohort classification remains runtime
  dependent.

# 2026-09-10 - Jobs detail feedback and notification management

- Jobs detail drawers now enter an explicit loading state immediately and show
  a visible error panel when a detail request fails; promise failures no longer
  present as a dead Details button.
- Jobs metric destinations were aligned with their displayed populations for
  cross-post failures, active work, and separate Published/Sold counts. Saved
  metric layouts now merge new cards safely instead of discarding valid prior
  ordering.
- Notifications now support paginated management, row/page selection, bulk
  read/unread, dismiss, delete, selection clearing, and related listing/job
  links. The backend bulk endpoint remains tenant-scoped and only deletes
  notification records.
- Admin listing diagnostics now include listing state and recent durable
  marketplace jobs. `/deployment` reports a stable backend process start time
  and commit identity rather than treating request time as build time.
- Focused marketplace regression suites: 54 passed. Frontend production build
  passed with existing lint warnings. Live interaction/runtime verification for
  Jobs, 2098/2186, and assisted marketplaces remains `OPERATOR_TEST_REQUIRED` /
  `LIVE_RUNTIME_VERIFICATION=BLOCKED_EXTERNAL` when the runtime namespace is
  unavailable; no false LIVE_VERIFIED claim is made.

# 2026-09-10 - Vine description quality gate

- Vine Amazon evidence normalization now retains brand, model, MPN, material,
  color, size, product type, capacity, included components, and product
  description alongside bullets/specifications instead of silently dropping
  those fields before copy generation.
- Vine rewrite copy no longer emits the `verified Amazon product record`
  boilerplate. It produces buyer-facing evidence summaries and includes
  available identity/specification/component facts.
- eBay preflight now adds `DESCRIPTION_INADEQUATE` for Vine placeholder/title-
  only copy when canonical source evidence is substantive, preventing such
  drafts from being treated as review/publish ready. The two latest Vine batch
  audit and listing 2141 production refresh remain runtime-dependent and are
  not marked verified.

# 2026-09-11 - Vine import first-pass acceptance

- Normal Vine batch construction now runs fresh eBay preflight after source
  enrichment and image repair, before returning the import result. Blocker-free
  rows become `complete`/Needs Review; any fresh blocker is persisted and routed
  to Needs Attention with its exact reason. The scheduled repair task remains a
  safety net and is not required for ordinary first-pass classification.
- Added a synthetic six-row acceptance regression covering source-backed
  descriptions, category derivation, current-price-over-ETV precedence, clean
  review promotion, and exact missing-image attention routing. Full relevant
  Vine/marketplace suites pass (`110 passed`); live marketplace publication was
  not invoked.

# 2026-09-12 - Timeline grouping, primary-photo, deletion, and media closure

- Timeline grouping now derives stable product groups from canonical item IDs
  and explicit Head/Tail roles, never from Slate-number parity or upload batch
  identity. Head Slates begin the following group; Tail Slates close the
  preceding photo group. Group background alternation uses canonical image
  group sequence and renders contiguous group containers.
- Manual primary-photo selection is scoped to one canonical image group and
  records `MANUAL_OPERATOR`; clearing it restores deterministic automatic
  ordering. Materialized `listing_images` place the selected image first, and
  the eBay image URL builder consumes that canonical order.
- Timeline deletion is soft and idempotent. Only the exact authoritative
  Slate/source-photo pair is retired; inherited `official_slate_id` metadata
  alone cannot delete a Slate. Deleted assets are excluded from Timeline
  grouping, intake queues, counts, automatic selection, and listing media.
- A bounded Timeline-specific repair recovered 181 stale Google Photos files
  into the current storage root without changing capture/import timestamps.
  The active set is 2,487 photos: all 2,487 local files decode and all 2,487
  media URLs return successful image responses with nonempty content. The
  earlier provider-drain task ended in `WorkerLostError`/SIGKILL; its work was
  not counted as successful.
- Read-only live Timeline assembly returned 2,510 ordered items (2,151 product
  photos and 359 Slate markers: 358 Head, one Tail), with 314 populated
  canonical image groups and 44 empty Slate groups. Those empty groups were
  intentionally left untouched; operators can now remove redundant markers
  without the system silently changing historical chronology.
- Regression coverage exercises Head/Tail grouping, manual Tail assignment,
  independent primary selection/clear-to-auto, listing/eBay image ordering,
  synthetic marker classification, and exact soft-delete behavior. The merge
  audit found the remote Timeline change only replaced Slate-number parity
  with global item-index parity; the new canonical group-index rendering
  preserves the intended visual alternation without conflating numbering.
- The listing-materialization integration test exposed an undefined `user`
  reference in intake draft shipping-policy derivation. Draft materialization
  now resolves the owning user from the batch, and the regression test passes.
- Authenticated operator interaction remains `OPERATOR_TEST_REQUIRED`; build,
  route, and data/media verification do not by themselves prove manual browser
  clicks.

# 2026-09-12 - Host disk and Slate QR test stability

- Root cause of the legacy QR test OOM was recursive variant expansion:
  `_qr_variants()` included QR-anchor crops while `_qr_anchor_crops()` called
  `_qr_variants()` again. Anchor discovery now requests only finite base
  variants, and valid PosterPro payloads short-circuit further QR transforms.
  The QR round-trip/regression tests passed in 9.9 seconds at 272 MiB peak RSS;
  the full Head Slate suite completed at 276 MiB peak RSS rather than being
  OOM-killed.
- The async-client test fixture now disposes its temporary SQLite engine and
  removes its database/sidecars on teardown. Before cleanup, 666 abandoned
  fixture databases occupied 1,016,102,912 bytes of `/tmp` tmpfs; they were
  closed/unreferenced test artifacts, removed, and no longer accumulate on
  normal teardown.
- Full Head Slate suite result with external pytest plugin autoload disabled:
  `44 passed, 6 failed, 1 skipped, 0 errors`. Failures are individually
  classified in the maintenance checkpoint: four obsolete mocks/API/parser
  expectations, one draft expectation made before the photo batch closes, and
  one SEO filename fixture whose natural filename order puts the label first.
  These are not reported as green; the async route test is skipped because the
  anyio plugin was disabled for the measured run. Recovery-candidate suite:
  `7 passed, 0 failed, 0 skipped, 0 errors`.
- The 30,511,151,530-byte active `/home/deploy/.pm2/pm2.log` was retained as a
  gzip archive (778,717,143 bytes); the live file was truncated only after the
  archive passed `gzip -t`. A logrotate size/retention policy now bounds future
  growth. System journal retention is capped at 3 GiB/14 days; vacuuming
  preserved recent OOM evidence and reclaimed 986.1 MiB.
- Only regenerated package caches were purged (pip and npm). Application
  storage, Google Photos originals, listing images, databases, current builds,
  backups, and Timeline snapshots were preserved. Timeline ordering, group
  identities, Slate positions/classifications, and media rows were not changed
  in this stability pass.

# 2026-09-12 - Timeline continuity and assisted-browser UX follow-up

- Timeline mutation refresh now reloads the previously loaded pagination
  window (including multiple 500-row pages), retains filter/zoom, and restores
  horizontal/vertical scroll coordinates and the selected item where possible.
  Head and Tail controls use high-contrast neon green/fuchsia styling; canonical
  image-group containers alternate by group index. Missing modern Slate artwork
  resolves to its exact linked legacy image or a data-derived preview, without
  changing event chronology.
- Read-only Slate audit: 360 Slate records; 336 linked legacy sources and 24
  manually created Slates. All 336 linked source photos exist locally and
  decode. Modern QR payloads retain canonical item/box identity; one stored
  payload lacks a title present in the Slate row. No source QR artifact exists
  for that row in retained evidence, so it was not guessed or rewritten.
- The extension popup is now a minimal connection/task surface. Once paired,
  background heartbeat and queue polling/claim are automatic; Jobs owns
  operator review and result confirmation. UPDATE is exposed only when a
  marketplace identity and matching external URL already exist. Lease renewal
  locks the job row and rejects expired leases, leaving recovery to the atomic
  queue claim. The public ZIP was read from the live endpoint and verified as
  version 0.2.0 with current popup/background files.
- Assisted marketplace forms remain deliberately partial: fixture coverage
  proves Facebook title/price/description/photos can be filled and execution
  stops at AWAITING_OPERATOR_REVIEW. Destination taxonomy, condition, shipping,
  authenticated marketplace sessions, live UPDATE/END, Etsy OAuth/API, sale
  polling outside eBay, rule routing, broad multi-tenant SaaS, subscriptions,
  payments, and storefront checkout remain incomplete/unverified.
- Validation: marketplace/eBay/API group `61 passed`; extension transport rerun
  `11 passed`; browser-extension fixtures and Timeline helper tests `8 passed`;
  frontend production build passed. A separate full legacy Head Slate suite
  reported `51 passed, 7 failed`; those failures remain unresolved/stale
  contract candidates and are not represented as green.
- Restarted only `posterpro-backend.service` and
  `posterpro-frontend.service`; backend/worker/beat/frontend are active,
  `/health` reports `database_ready=true`, local and public Timeline/Jobs
  routes return HTTP 200, and the extension ZIP endpoint returns HTTP 200.
  No live marketplace listing was created or submitted. No non-PosterPro
  project or shared host resource was modified in this follow-up.

## 2026-09-12 - Timeline / modern Slate closure corrections

- The previous paragraph's full Head Slate result is superseded: the two full
  suites now report `62 passed, 0 failed, 0 errors`. The seven prior failures
  were stale test contracts/fixtures (AI mock kwargs, obsolete Google Photos
  bridge-upload expectations, outdated parser seam, open-stream draft timing,
  and unspecified photo chronology), not Timeline chronology regressions.
- Modern Slate artwork no longer falls back to the photographed legacy source.
  Missing active artwork now shows `MODERN SLATE NEEDS REPAIR`; the old source
  remains linked separately as `VIEW LEGACY SOURCE`. An authenticated,
  user-scoped, idempotent artwork repair regenerates a modern PNG and QR from
  canonical Slate/QR data without changing item identity, legacy linkage, or
  capture/order fields.
- The later production read-only audit supersedes the earlier 360- and
  370-record snapshots: 372 Slate records total, 344 active and 28
  soft-deleted, comprising 336 legacy-linked and 36 manual/unlinked records.
  All 372 current modern artwork files and all 336 linked legacy source images
  decode; stored QR item/box identity matches for 372/372; there are zero
  duplicate legacy-source links or duplicate active item IDs. Two missing
  modern assets were repaired from canonical persisted data. No chronology or
  legacy-source records were changed by that repair.
- Timeline refresh/window preservation remains intact. Group appearance is
  driven by canonical image-group index; dark-group text/control colors use
  explicit high contrast; Head/Tail accents remain neon green/fuchsia. JS
  palette/window tests pass. The actual public route and current Timeline JS
  bundle were checked, but no browser executable is installed here, so visual
  interaction/contrast remains `OPERATOR_TEST_REQUIRED` rather than claimed as
  live-browser verified.
- Validation after correction: `backend/tests/test_intake_head_slate.py`
  plus `backend/tests/test_intake_slate_recovery_candidates.py` — `62 passed`;
  Timeline helper tests — `5 passed`; frontend production build passed.
  Backend and frontend were restarted; all four PosterPro services are active,
  `/health` reports `database_ready=true`, and local/public Timeline routes
  return HTTP 200. No marketplace listing was published. No non-PosterPro
  project or shared host resource was modified during this closure pass.

## 2026-09-12 - Routing and marketplace status UI follow-up

- Added `/settings/routing` as the authenticated routing-rule editor and linked
  it from Marketplace Settings. Added API client methods for existing
  current-user-scoped routing endpoints.
- Added a listing-detail marketplace status matrix backed by persisted
  `marketplace_statuses` serialization (MarketplaceListing records), plus
  configured targets and eBay identity. It displays external IDs/URLs when
  present; selecting a destination is not represented as a live listing.
- Validation: frontend production build passed and emitted both
  `/settings/routing` and `/listings/[listingId]`. A serial rerun of the full
  Head Slate and recovery suites passed `62/62`. One earlier run concurrent
  with Next.js compilation had an intermittent QR round-trip failure
  (`decode_slate_payload` returned `None`); the focused test passed alone and
  in the serial suite. Monitor this as a concurrency-sensitive test flake,
  not a deterministic payload regression. No authenticated browser session was
  available for visual verification. Marketplace form adapters, Etsy
  authorization, broad team/tenant SaaS, subscription/payment, and direct
  checkout remain incomplete or externally blocked; no live marketplace
  listing was submitted.
- Deployment follow-up: commit `62bc9ad` is the local and public frontend
  identity. The frontend-only restart completed; all four PosterPro services
  are active, `/health` reports `ok=true` and `database_ready=true`, and local
  plus public routing/listing routes return HTTP 200. The public
  `/api/browser-extension/download` ZIP was verified as v0.2.2. Authenticated
  visual operator interaction remains unverified.
- Added non-publishing extension DOM fixtures for Mercari, Poshmark, Vinted,
  Etsy, and OfferUp. Each verifies core title/price/description/photo mapping,
  an `AWAITING_OPERATOR_REVIEW` stop, and explicit category/condition review.
  The complete extension Node suite reports 14 tests passed. These mocked
  fixtures do not establish live marketplace selector compatibility or
  authenticated end-to-end operation.

## 2026-09-12 - One-click extension authorization hardening

- The one-click authorization change was first released as extension version
  `0.2.1`; the current public download has since advanced to `0.2.2`. When its
  PosterPro content script is
  present on the authenticated Settings page, the operator can authorize this
  browser with one click; the existing one-use, five-minute pairing code is
  sent directly to the extension background context. The scoped device token
  is stored extension-locally and is never posted back into page JavaScript.
  Manual popup code entry remains the recovery/fallback path.
- The manifest no longer requests the browser `cookies` permission and the
  background agent no longer calls `chrome.cookies` or reports cookie
  presence. Marketplace passwords, cookies, and session credentials remain in
  the user's browser profile. Heartbeat/poll/claim/lease renewal remain
  automatic after pairing; routine popup interaction is still not required.
- Automated validation: browser-extension fixtures `7 passed`; backend
  extension transport/tenant/job tests `12 passed`; public ZIP was downloaded
  in memory and verified as v0.2.1 with the PosterPro content script included;
  Settings and extension bundle served successfully. No installed Chrome/Edge
  session was available here, so actual button-to-device pairing and marketplace
  tab execution remain `OPERATOR_TEST_REQUIRED`.
- Assisted marketplace create/update/end remains partially implemented: the
  worker/extension transport, durable identity, and no-duplicate safeguards
  exist; destination category/condition/shipping fields require marketplace-
  specific verified form mapping, authenticated marketplace sessions, and live
  operator acceptance. Etsy OAuth, non-eBay sale polling, rule-based routing,
  expanded tenant/team isolation, subscription entitlements, premium payments,
  crypto, and direct checkout remain incomplete/unverified. Do not report
  source or fixture coverage as live marketplace verification.

## 2026-09-12 - Continuation: fallback routing, Timeline contrast, and test isolation

- Hosted browser assist is now reachable from both the single-listing worker
  and bulk crosspost worker when `browser_assist` is configured but the user
  has no compatible extension heartbeat within the last two minutes. A fresh,
  unrevoked compatible extension remains primary. Users can explicitly choose
  `hosted_browser_assist`; the hosted bridge remains the same marketplace-job
  transport and does not report a publication without external identity proof.
- Dark Timeline image groups retain dark/light alternation. Dark-group action
  controls now override the outline button's white hover fill, keep light text,
  retain visible white focus indication, and avoid disabled-state opacity
  loss. Head/Tail and canonical group ordering are unchanged.
- Read-only all-tenant Slate audit: 372 records (344 active, 28 soft-deleted);
  336 linked legacy sources and 36 manually unlinked Slates; 370 modern artworks were valid and two manual
  Slates for user 1 lacked artwork. Regenerated those two assets from their
  persisted item/QR data only; no Slate row identity, source link, or timeline
  position was changed. Follow-up: all 372 modern artwork files decode, all
  336 legacy source images decode and remain linked to the right tenant, the
  336 linked legacy source images decode, all 372 stored QR item/box identities
  match, and both newly repaired modern QR payloads decode and match their
  canonical item/box identities. There are zero duplicate source links and
  zero duplicate active item IDs. Authenticated
  browser-rendered contrast remains `OPERATOR_TEST_REQUIRED` because no
  installed browser session is available.
- Corrected the pytest `db_session` fixture: it now binds an isolated temporary
  SQLite database and patches the application/worker session aliases before
  schema reset. The previous fixture had called `drop_all` on the configured
  engine; the active database still has substantial production data in a
  read-only count check, but future tests are now explicitly isolated.
- Historical seven Head Slate failures mapped to stale contracts, not
  chronology behavior: `test_build_voice_intelligence_returns_structured_listing_json`
  expected a one-argument AI mock although the service also passes provider
  options; three Google Photos upload tests expected the retired hosted-bridge
  queue (`SUBMITTED_TO_BRIDGE`) instead of direct API upload (`UPLOADED`);
  `test_google_photos_parser_merges_playwright_continuation_when_visible_count_exceeds_html`
  mocked the obsolete list-only parser method; the manual-assignment test
  expected a draft before an open stream snapshot closed; and the SEO filename
  test omitted capture timestamps while asserting chronology-dependent ordering.
  All were stale test contracts, not Timeline chronology regressions. After
  updating those contracts in the earlier Slate repair, the current isolated
  full run is `62 passed, 0 failed` across Head Slate and recovery-candidate
  suites. Marketplace/eBay/routing/extension regressions are `90 passed,
  0 failed`.
- Added tests for automatic hosted fallback eligibility and retained the
  no-live-publication boundary. Broader marketplace adapters, destination
  taxonomy/condition/shipping, routing/bulk UI, tenant/team controls,
  subscription/payment/storefront/affiliate features, and non-eBay sale
  detection remain incomplete or unverified.
- Bulk crosspost is now reachable from the Listings selection workspace; it
  queues durable per-listing jobs, displays rejected/unrouted rows, uses the
  tenant's routing/listing targets when no manual marketplaces are selected,
  and requires the exact existing live-queue confirmation phrase for eBay.
  An unrouted listing no longer silently defaults to eBay. Frontend build and
  Frontend build passed. Deployment/runtime verification is pending this
  checkpoint.

## 2026-09-13 - Vine cohort finalization and authoritative queue badges

- Completed the shared Vine finalization repairs for the ten acceptance
  listings `2191, 2187, 2185, 2179, 2178, 2177, 2174, 2170, 2167, 2141`.
  A post-deployment read-only check found all ten in the canonical `review`
  bucket with `processing_state=complete`, `needs_review=true`, New condition,
  quantity 1, usable category IDs, 11–12 images, and an eBay preflight result
  of `needs_review` with zero blockers. No publish operation was invoked.
- Added shared Vine category rules for mirrors and lithium battery chargers,
  destination-aware matching for verified eBay leaves, and category-aware
  mapping of Amazon mirror dimensions to required eBay Item Height/Width.
- Fixed the Listings queue-card mismatch: filtered queue membership and the
  visible badge now use the same backend-computed `queue_bucket`. This avoids
  summary cards being mislabeled Needs Attention when their omitted quality /
  readiness summaries prevented the browser from independently recomputing
  readiness. The two screenshot examples, listings `2164` and `2208`, were
  read-only verified as `review`, with cached eBay `ready_with_warnings` and no
  blockers.
- Validation: `test_vine_import.py`, `test_marketplace_preflight.py`,
  `test_marketplace_api.py`, and `test_ebay_service.py` — `139 passed`; the
  targeted queue-contract test also passed before the full run. Frontend
  production build passed with existing lint warnings. Backend and frontend
  were restarted; backend health reports `database_ready=true` and the
  backend, worker, beat, and frontend services are active.
- Three other current Vine rows remain in Needs Attention only for unresolved
  genuine-image blockers (`ACTUAL_PHOTOS_MISSING` / `REFERENCE_IMAGES_ONLY` /
  `EBAY_IMAGE_URL_INVALID`); reference-only Amazon images were not promoted as
  actual product photos. This repair did not publish or change external
  marketplace listings.

## 2026-09-14 - Guided setup, AI mode safety, and price-risk controls

- Added authenticated, user-scoped Guided Setup state in `User.settings_json`:
  selected destinations, resumable current step, skipped tasks, lifecycle
  metadata, and privacy-safe funnel events. Dashboard and Settings expose
  Start/Continue/Review setup; the first Dashboard visit offers a dismissible
  welcome. Setup does not trust client-supplied tenant IDs.
- Added a one-task-at-a-time `/onboarding` flow with destination selection,
  BYO OpenAI versus PosterPro AI choice, encrypted BYO key storage, low-cost
  provider test, and an honest pre-launch plan preview. No key is returned to
  the browser. PosterPro-managed AI remains unavailable: server-side plan
  entitlement, feature enablement, and a separate fail-closed metering-ready
  gate are all required. Billing is not active and this pass did not activate
  sponsored AI.
- AI provider resolution now uses the signed-in user's encrypted BYO key for
  listing, pricing, image, photo-group, and voice calls; ordinary users cannot
  inherit the platform administrator's key. Durable AI de-duplication
  signatures now include user and provider mode to prevent cross-account
  result reuse.
- eBay onboarding verification uses a read-only seller-policy API call and
  only reports connected setup when the recent account check, saved payment /
  fulfillment / return policies, and verified merchant location are present.
  It never creates a listing. Browser-extension onboarding verifies heartbeat
  only, not marketplace login or form-fill. Other assisted marketplaces remain
  `OPERATOR_TEST_REQUIRED`; saved settings do not count as working publishing.
  Google Photos OAuth is reported as connected-but-upload-unverified because
  there is no harmless upload verification step. The deployment's startup
  integration probe reported Google's saved refresh authorization rejected
  (`Token has been expired or revoked`); no Google credential was changed.
- Added sold-evidence underpricing assessment requiring at least three relevant
  sold comps plus high relevance/confidence. Severe alerts create idempotent
  in-app notifications and block publish until the operator acknowledges the
  current evidence, applies the protected recommendation, or pauses. The three
  choices are available in Listing Editor. Auto-fix records a revision and
  queues exact-identity UPDATE jobs only; paused listings are blocked in
  preflight. eBay end remains a manual action because this deployment has no
  safe direct eBay end operation.
- Pricing evidence in this pass is limited to PosterPro sale history, manually
  entered comps, and supplied external-comparable inputs. No new sold-market
  provider, collectible-variant research, confidence-calibrated external
  comp cache, or tenant pricing-settings UI was added; the broader pricing
  system is therefore partial, not a verified market-value service.
- Validation: the focused onboarding/auth/Google OAuth/pricing/preflight/
  marketplace API/eBay publish/extension suites passed `96 tests`; Python
  compile passed; Next production build passed (repository lint/noVNC warnings
  remain). No migration was required. Restarted only PosterPro backend,
  worker, and frontend. Backend `/health` reports `database_ready=true`; local
  and public `/onboarding`, plus local Listings and Settings routes, returned
  `200`. Backend, worker, beat, and frontend are active. No listing was
  published. No non-PosterPro project or shared host resource was modified.
