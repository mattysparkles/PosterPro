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
