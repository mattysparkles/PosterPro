# PosterPro release operations — current safety baseline

Last inspected: 2026-11-10. This document describes the observed deployment and
required release gates. Runtime packaging and an explicit runtime preflight are
being implemented; the active systemd services still use mutable directories,
so do not treat candidate packaging as authorization to activate a release.

## Current live deployment

- Frontend/backend and Vine/publish workers use
  `/opt/apps/posterpro/release-candidate.Yzg7AG` as their working directory.
- Backend, workers, and automation bridge use the Python environment and
  `EnvironmentFile` under `/opt/apps/posterpro/repo`; the bridge itself runs
  source from `/opt/apps/posterpro/repo/bridge`.
- Frontend build identity is available at `/frontend-deployment`. Backend
  identity is not. Health is available at `/api/health` and returns
  `database_ready`.
- `/opt/apps/posterpro` is a systemd-bound path to
  `/mnt/posterprodata/app` on the persistent ext4 volume. Do not change or
  remount it as part of a release.
- There is no application migration ledger, deployment lock, immutable unified
  release manifest, stale-build rejection, atomic promotion script, or tested
  automated rollback in the inspected source/units.

## Mandatory gates before a future release

1. Select the canonical application repository/branch and record its immutable
   commit. The candidate must include all approved changes and tests; do not
   deploy from any dirty source tree.
2. Record the active frontend build, backend process start/source identity,
   worker source, bridge compatibility, systemd drop-ins, public health, and
   persistent mount state.
3. Verify a recovery backup of the live database and preserve the current
   active release as an immutable rollback target. Confirm space and read the
   backup catalog before proceeding.
4. Compare the candidate's ordered migrations against the live PostgreSQL
   schema. Since no migration ledger exists, inspect required tables/columns
   directly and record explicit preconditions/postconditions. Do not infer
   application from migration filenames.
5. Run backend and frontend regression suites from the clean candidate, run
   production builds, verify migration compatibility, and record exact results.
6. Serialize deployment with a lock. Reject candidates whose parent/release
   identity would remove already integrated behavior.
7. Stage a new versioned release directory. Never overwrite files under an
   active service working directory. Activate backend, frontend, workers, and
   bridge only as a tested compatibility set; do not change collector windows.
8. For an authorized additive migration, take and verify the backup first,
   apply once, then verify schema postconditions. Do not automatically reverse
   a migration that could discard data.
9. Run public health and read-only smoke checks, verify the public release
   identity, and exercise the requested authenticated feature without
   marketplace publication or physical printing unless separately authorized.
10. If checks fail, switch all affected services back to the preserved prior
    release as one serialized rollback, verify health, and retain logs. Report
    the feature as failed, not deployed. Preserve forward-compatible schema
    unless a reviewed data-safe rollback is available.

## Current release boundary

The active candidate is dirty; the backend's exact loaded revision cannot be
proven. The Shelf Census source/migration is absent from this release and the
live database. Vine photo validation and review terminology differ between the
active candidate and nested application source. Therefore no single candidate
currently satisfies the required “preserve all approved functionality” gate.
Do not restart or repoint production until a clean integration release and
schema plan exist.

## Integration-candidate and promotion gates

The current builder emits an immutable integration_candidate artifact, not a
production release. It packages the canonical source and frontend build but
does not package a validated runtime dependency layout. Its manifest always
sets production_eligible to false. Do not change that field by hand.

The activator verifies every entry in SHA256SUMS, then requires
artifact_kind=production_release, production_eligible=true, and
requires_operator_approval=false. It also requires a previously activated
release manifest and a configured active symlink before it can acquire the
deployment lock or touch services. The current direct-working-directory
systemd setup intentionally fails this preflight.

A production-capable package still needs a tested runtime layout for the
backend's selected Python environment and the frontend's Next runtime
dependencies, a successful schema/migration preflight tied to the exact commit,
a prior immutable release manifest, and an explicit reviewed transition of
the current systemd units. Until then, integration artifacts are for review
and validation only.

## 2026-11-10 continuation status

- The production schema baseline now exists: fingerprint
  `4194ee56cf56503f3b493caf58f2048130b02779e2ab0bd26127c6a429633f92`,
  recorded as `baseline-existing-schema`. A fresh database dump was restored
  into an isolated temporary PostgreSQL database; the baseline, Census and
  media retry migrations applied in order, and the second apply made no
  changes. The temporary database was removed. Production currently has only
  the baseline row; application migrations remain pending release activation.
- `ops/release/build_runtime_release.sh` now assembles a versioned source,
  pinned Python virtual environment and Next standalone runtime from an exact
  clean `main` commit. `runtime_preflight.sh` validates hashes, runtime
  versions, standalone server, external environment reference and persistent
  mount assumptions. It deliberately emits/refuses only a candidate and does
  not change systemd or mark production eligibility.
- The pinned Python runtime lock installed successfully into a temporary
  isolated Python 3.13.3 environment and `pip check` reported no broken
  requirements. A synthetic preflight fixture passed checksum, version,
  storage, and release-shape checks; this validates the guard mechanics, not a
  complete application runtime or production release.
- The live service units still use `/opt/apps/posterpro/repo` and
  `/opt/apps/posterpro/release-candidate.Yzg7AG`; backend, frontend, workers
  and automation bridge are not yet switched to an immutable common release.
  A rehearsed service transition and rollback are still required.
- The production schema fingerprint was confirmed unchanged after ledger
  initialization. Root filesystem utilization is 89% with 19 GB free; the
  application volume has 35 GB free. No releases, backups, source trees or
  uploaded media were deleted.
- Candidate is not production eligible: the full backend suite currently
  reports failures (see continuation remediation entry), so no release build,
  service restart, migration application, or live feature activation occurred.


### Verification continuation — 2026-11-10

- Canonical `main` checkpoint: `74da2011ab550fdc5e10e28e23dce0ce45bc1d01`.
  It contains Vine bounded retry and dry-run reconciliation, Farmingdale origin
  configuration and eBay handling-time checks, the overlap ledger dispositions,
  and runtime packaging/preflight scripts. It is a source checkpoint, not a
  production release.
- Focused changed-code suites passed: 196 backend Vine/eBay tests; 2 frontend
  approval-terminology and Census interaction tests. A lockfile-based frontend
  production build passed and includes the Shelf Census route. The latest full
  backend suite remains 32 failed / 1,044 passed. npm ci reported 20 audit
  findings in the existing dependency lock (9 moderate, 10 high, 1 critical);
  no upgrades were applied.
- Read-only public verification: `/api/health` is 200 and database-ready.
  `/api/deployment` reports backend `70026b389d84409571d142c4abf4c012edfbb11a`
  from the old release-candidate directory; frontend reports build
  `ZSLW0Cay7ZSfVKgBeUI8e` from that same mutable tree. The public census route
  is still the older tote UI. No deployment occurred.
- Production migration ledger holds only the baseline with the unchanged
  fingerprint. Census and retry migrations remain pending. eBay local config is
  Farmingdale NY 11735 and old policy IDs are preserved; street address is not
  yet entered, so merchant-location verification/publishing remains blocked.
- Activation remains blocked until full backend failures are resolved or
  specifically dispositioned with evidence, the immutable runtime is built from
  a clean exact commit, and the common service transition/rollback is rehearsed.
