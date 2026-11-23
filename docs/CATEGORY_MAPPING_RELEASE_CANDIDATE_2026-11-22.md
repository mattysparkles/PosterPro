# Marketplace category mapping release candidate

## Candidate scope

- Isolated worktree: `/opt/apps/posterpro/worktrees/category-mapping-candidate-20261122`
- Temporary branch: `category-mapping-candidate-20261122`
- Base source: `4eafdadbdc73ce3c574fcc6cee73b7e376c07f22`
- The earlier eBay origin guard implementation at `3eeefd4` is an ancestor of
  this base. The separately reported `6676b04` object is not present in this
  checkout's Git history, so it is not represented as verified ancestry.
- No database migration is introduced. Suggestion work uses the existing
  owner-scoped `bulk_jobs` table and existing taxonomy registry tables.
- No external marketplace listing was submitted or changed.

## Included implementation

- Per-marketplace category candidates and persisted manual confirmation.
- Strict, explicit source listing selection for confirmed category reuse;
  at most 100 explicitly selected listings, same owner, exact strong product
  identifier plus model/size/color/variation/bundle dimensions, current
  verified taxonomy leaf, row locking, idempotent retry, and transactional
  rollback.
- Owner-scoped bulk suggestion jobs for selected listings or up to 10,000
  Draft/Ready rows. Jobs process 25 listings at a time, persist a cursor,
  resume after worker restarts, and save candidates separately from confirmed
  mappings. Suggestions do not publish, queue, or change assigned categories.
- Listing-level candidate review with rationale, confirm/reject controls,
  and marketplace-specific category-review filters.
- Mercari data/draft readiness is distinguished from authorized direct
  publication. Unknown required-field schemas, actual-item-photo requirements,
  and shipping gaps remain explicit.
- Facebook confirmed category paths are passed to the supervised browser form
  adapter. Ambiguous visible category matches fail closed.
- Vine seller-photo upload/capture records attestation as provenance but does
  not approve a photo automatically; photo approval remains explicit.

## Validation

- Backend category/readiness/media/publish regressions: **89 passed**.
- Frontend unit suite: **48 passed**.
- Browser-extension DOM/harness suite: **108 passed**.
- Python compile checks: passed.
- `git diff --check`: passed.
- No schema migration was needed.
- Test suites used isolated fixtures; no production listing, photo, category,
  or marketplace state was changed.

## Not yet release-verified

- Full Next.js production build was not run on the PosterPro host: the last
  observed host state had 1.6 GiB available RAM and 90% filesystem use. The
  repository CI workflow uses a disposable GitHub runner, but this checkout has
  no authenticated GitHub push credential and its configured `origin` is a
  local release-candidate repository. CI was therefore not triggered.
- No signed-in application browser environment is available in this session.
  The extension tests use synthetic DOM fixtures; they do not verify Facebook
  Marketplace in a live account or prove a completed draft handoff.
- Inventory-wide suggestion jobs were implemented and covered by API/service
  tests but were not run against production inventory. No actual candidate or
  readiness counts are claimed.
- Facebook remains a supervised browser workflow. Mercari remains manual or
  browser-assisted; no direct Mercari publication API is represented as
  available.
- Fifteen Vine listings still require verified physical-item photographs.
  Camera/upload UI is not physical-camera verified. Existing zero-day eBay
  handling policy mismatch against the one-business-day preference remains a
  publication blocker; no global policy was changed.

## Activation and rollback

This candidate is not production eligible. Do not activate it until a clean
runner completes the production build and an authenticated isolated browser
test validates category confirmation persistence and safe marketplace form
preparation. Rollback is the existing active immutable release; this candidate
has not altered production services or schema.

## Release validation follow-up — 2026-11-23

- Verified the original candidate commit `f05ad62bedf95d51757b0fc3bee3ffd9aff9ebd2`
  was intact and clean before this follow-up.
- Worker review found that per-listing exceptions were terminal on first
  failure, system retry counters were not reset after a successful chunk, and
  the capped 500-error detail list was also being used as the total error
  counter. Per-listing failures now receive three total attempts with persisted
  30/60-second backoff; final error count is independent of the capped detail
  list; successful chunks reset consecutive system retry count; duplicate
  delivery that cannot obtain the row lock reports `not_claimed` without
  mutating the job.
- Production topology review found the generic `posterpro-worker` disabled,
  while `posterpro-vine-worker` and the dedicated publisher dispatcher are
  active. Category work now routes to an independent `posterpro-category`
  queue, and the dispatcher Beat schedule includes its recovery watchdog. A
  matching versioned-runtime unit is prepared at
  `ops/systemd/posterpro-category-worker.service`; it is not installed or
  started. The isolated consumer prevents category batches from competing
  with or starving Vine imports/image-repair work.
- Focused backend suite after worker changes: **93 passed**, 546 warnings.
  The suite covers retry/backoff, terminal-error counting, completion
  idempotency, Celery registration/routing, and existing category, readiness,
  Facebook, photo and publishing behavior. SQLite tests do not prove concurrent
  PostgreSQL row-lock behavior.
- The prior candidate's frontend suite (**48 passed**) and extension harness
  (**108 passed**) remain applicable to unchanged frontend/extension files;
  neither was rerun in this backend-only follow-up.
- Build remains **not run**. The server is at 94% root filesystem use, 90% on
  the mounted application filesystem, and about 1.4 GiB available RAM. No
  disposable build runner or authenticated GitHub CLI was available. The only
  configured Git remote is a local release-candidate checkout; no candidate
  was pushed. The existing `.github/workflows/ci.yml` uses GitHub-hosted
  runners (Python 3.13, Node 20, `npm ci`, `npm run build`) on push/PR, but it
  does not currently run this candidate's category-suggestion test file,
  provide an authenticated browser acceptance job, or publish a build
  artifact. Safe off-server procedure: reconcile the candidate patch onto the
  authorized GitHub `main` lineage through a reviewed PR (no force push); add
  the category backend tests to that PR gate; run the full frontend build on
  the hosted runner; then run a separate manual staging job against a disposable
  database and seeded listing fixtures, capturing browser checks for suggestion,
  confirmation, reload persistence, exact-identity reuse, and independent
  Facebook/eBay/Mercari readiness. The staging browser must use isolated test
  credentials and must not attach to the operator's paired collector or submit
  external listings. Upload only the tested commit-bound build artifact and
  manifest. Required prerequisites currently missing here are the approved
  GitHub lineage write/PR path, an artifact-producing CI job, and isolated
  authenticated browser-test credentials/runtime. Do not build Next.js on this
  live host.
- Read-only cache inspection found `/root/.npm` at 2.3 GiB (`_cacache` 1.6 GiB,
  `_npx` 654 MiB) and `/root/.cache` at 2.3 GiB (`ms-playwright` 1.3 GiB,
  `pnpm` 227 MiB, `pip` 167 MiB, `puppeteer` 334 MiB, plus smaller caches).
  The integration checkout's `frontend/node_modules` is 691 MiB. These are
  possible cleanup candidates only; none were deleted. Browser caches and dirty
  checkout dependencies remain preserved.
- Active immutable release symlink resolves to
  `/opt/apps/posterpro/releases/posterpro-20261114T190836Z-943db7c0c214`.
  This is the rollback target. No service, database, collector session, photo,
  listing, or external marketplace was changed during validation.
- Signed-in browser acceptance and production deployment remain gated. The
  candidate is still not production eligible until a suitable isolated runner
  completes the full production build and authenticated browser acceptance.

## GitHub integration and hosted CI follow-up — 2026-11-23

- Preserved the candidate commits on a local review branch based on the
  observed GitHub `main` commit `eb62f8e19d743ee20dfcc40a5bd9244978478dbd`:
  `category-mapping-github-integration-20261123`.
- The branch was pushed without force-pushing or modifying `main`. Pull request
  [#26](https://github.com/mattysparkles/PosterPro/pull/26) is open for review.
  Its initial hosted CI run failed at backend test collection because clean
  installs omitted the existing `qrcode` runtime import. The active release
  reports `qrcode 8.2`; that version is now declared in
  `backend/requirements.txt`. Hosted CI must rerun on this follow-up commit.
- CI now includes the category/readiness, marketplace taxonomy, photo, Vine,
  worker and extension taxonomy test paths; a disposable PostgreSQL service;
  full frontend unit tests and production build; CSS verification; and a
  commit-bound frontend artifact manifest/checksum. The PostgreSQL concurrency,
  restart and rollback test is prepared but has not yet passed on the hosted
  service.
- Local checks on the integration branch: JavaScript extension/category
  harness **154 passed**; additional taxonomy traversal subset **46 passed**;
  Python syntax compilation, CI YAML parse, and `git diff --check` passed.
  Backend pytest could not run locally because this host has no pytest/runtime
  dependencies in the integration worktree. No dependency installation or
  production build was attempted on the host.
- No real authenticated staging browser acceptance has run. The host has no
  installed browser/Playwright command or staging login, and the current CI job
  is a build/test gate rather than an authenticated staging UI test. This is a
  remaining release blocker; unit/extension harness tests do not substitute.
- Current host storage is 94% full on `/` (9.4 GiB free) and 90% on the
  application mount (11 GiB free); available RAM was about 410 MiB at inspection.
  `/root/.npm` and `/root/.cache` each measure 2.3 GiB, including Playwright and
  Puppeteer assets. Nothing was removed; cache cleanup requires operator
  approval and active-use review.
- Production remains unchanged. The current release symlink remains the
  rollback target recorded above. No jobs, collector sessions, marketplace
  listings, or inventory were modified.
