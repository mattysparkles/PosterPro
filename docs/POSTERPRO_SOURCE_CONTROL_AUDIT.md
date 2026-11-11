# PosterPro source-control audit — 2026-10-18

## Active-source gap

The deployed repository has material active source outside Git tracking. This
is a release risk, not proof that the files are safe to discard. The observed
untracked groups are backend services/API, 12 additive migrations, backend
tests, frontend inventory/packing/scan pages and interaction tests,
browser-extension diagnostics, and recovery documentation.

Explicitly excluded from any staging proposal: `.env`/credentials,
`node_modules`, temporary files, snapshots/artifacts, local databases,
screenshots, media/storage, and customer records.

## 2026-10-18 verified inventory correction

`docs/` is not ignored: the recovery ledgers themselves are untracked. A full
porcelain scan found 2,165 untracked files. That set includes active source,
migrations, and tests, but also generated Vite results, extension ZIPs,
timestamped source snapshots, and `debug_fb_publish.db`; it is unsafe to
bulk-stage or bulk-commit.

Priority active untracked paths needing an explicit review allow-list include:

- `backend/app/services/{shipping_profitability_policy,ebay_operations_preview,ebay_failure_recovery,ebay_category_readiness_repair,scanner_dispatcher,package_labels}.py`
- `backend/migrations/20260920_*` through `20261017_*`
- `backend/tests/test_{ebay_failure_recovery,ebay_operations_preview,pricing_evidence_gate,scanner_dispatcher}.py`
- `frontend/pages/{packing.js,intake/timeline.jsx,inventory/*}` and focused tests

No `git add -A`, blanket commit, reset, or clean is authorized. A deployment
requires a reviewed allow-list commit with only source, migrations, tests,
configuration templates, and durable ledgers; it must exclude all databases,
media, generated outputs, snapshots, packaged artifacts, and customer data.

## 2026-10-19 recheck

Read-only `git ls-files --others --exclude-standard` now reports 2,175
untracked paths (up from 2,165 on the prior scan). Broad categories include 16
untracked `backend/app` files, 12 migrations, 19 backend tests, 17 frontend
page/component/lib/hook files, 10 frontend tests, 2 browser-extension files,
8 docs, and 2,076 `ops` paths (primarily snapshots/artifacts). `git status`
also reports 138 modified entries and 70 untracked directory-level entries.
These counts are not an allow-list: `ops` contains data/snapshots, frontend
contains generated dependencies, and the active source tree remains mixed.
No files were staged or committed in this recheck. The current shipping
preview correction and scanner additions remain in the dirty worktree; an
isolated source release still requires per-path review and a clean reviewed
base artifact.

The active worktree is on branch `codex/cms-workspace-theme` at HEAD
`292c80f`; the index was empty at inspection. This branch/base identity is
not evidence of the production source revision. Do not switch branches or
commit the dirty worktree as a shortcut to reproducibility.

## Approved-increment release procedure

1. Preserve the active worktree; never reset, clean, or broadly commit it.
2. Create an isolated release worktree from a known revision.
3. Copy/stage only the reviewed increment's source, tests, migrations, and
   documentation by explicit path list.
4. Run backend tests plus frontend tests/build in that isolated worktree.
5. Review `git diff --check`, status, migrations, and service configuration.
6. Back up the database before migrations; deploy only the reviewed artifact.
7. Verify affected endpoints and, for marketplace work, authenticated remote
   state. Record exact revision and results in the deployment ledger.
8. Roll back by redeploying the previous reviewed artifact; never reset the
   active worktree.
