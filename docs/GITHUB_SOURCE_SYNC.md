# PosterPro canonical source synchronization

This GitHub `main` snapshot is synchronized from the verified local canonical source commit `d52fd1b0a6c8dafc92aaba10a5fd33cd47f47def`, deployed as release `posterpro-20261111T211543Z-d52fd1b0a6c8` on 2026-11-11. Application source files and build inputs match that commit; operational records and local release data are intentionally omitted.

The destination repository had independent history at `e673eb09338120da89512b4da0095afbde5b0460`. This commit preserves that `main` ancestry and records the consolidated source tree without force-pushing or importing the local operational-data history. The source commit identifier above is retained as provenance; this synchronization commit therefore has a different Git SHA.

## Upstream reconciliation

- Vine draft creation flags, item-local image retry behavior, and media status handling are represented by the newer canonical Vine services and regression tests.
- Marketplace sale polling capability gating is represented by the canonical setup snapshot contract and `test_sale_detection_gating.py`.
- Canonical marketplace enums and readiness logic supersede the older enum. The older Amazon seller-channel setup stub is not included: PosterPro currently treats Amazon as the Vine source workflow, and the stub did not provide an authorized Amazon seller publish connector.
- Older mutable-directory systemd templates and the `repo` gitlink are superseded by canonical immutable-release tooling and the in-tree bridge implementation.
- The canonical repository's current CI workflow is retained.

## Excluded from the public source snapshot

Operational snapshots, dry-run reports, remediation/audit ledgers, prebuilt extension ZIPs, and tote inventory manifests are not source code and are not published. No environment file, credential, database dump, uploaded product media, or release backup is included.
