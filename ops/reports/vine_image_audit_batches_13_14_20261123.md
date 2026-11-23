# Amazon Vine image integrity audit (read only)

Audit date: 2026-11-23. Source: PostgreSQL read-only snapshot, persisted image metadata, and bounded local-file inspection. No database, media, collector session, listing, or marketplace state was changed.

## Source imports

| Batch | Original filename | Created | Source rows | Unique ASIN/order identities | Duplicates | Rows linked to listing | Unlinked rows | Unique linked listings | Archived linked listings |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 13 | `Itemized_Report_2026 (18).xlsx` | 2026-10-25 07:59 UTC | 1,368 | 1,359 | 1,304 | 1,354 | 14 | 1,349 | 59 |
| 14 | `Itemized_Report_2026_19.xlsx` | 2026-11-09 12:35 UTC | 1,441 | 1,432 | 1,368 | 1,425 | 16 | 1,420 | 59 |

Batch 15 (2026-11-20, same filename as batch 14) is a re-import attempt: all 1,441 normalized row fingerprints match batch 14 and it created zero new listings. Original upload bytes were not retained, so byte-for-byte identity cannot be established. The original file timestamps are the batch creation times; the source workbook itself has no independent start/completion events. Batch 13 completion/update was recorded around 08:48 UTC; batch 14 around 13:10 UTC. Audit includes archived listings and rows without links. Per-source-row details are in [vine_image_audit_batches_13_14_20261123.csv](vine_image_audit_batches_13_14_20261123.csv).

## Findings

- The blue image is **stored media**, not a frontend-generated placeholder. Its exact URL is `https://m.media-amazon.com/images/G/01/marketing/prime/2022PrimeBrand/Logos/Prime_Logo_RGB_Prime_Blue_MASTER._CB542735979_.png`; SHA-256 `0d319bf7425072bad431fd891c02ec8c9f954aa1a9a2e0a230a9049c3ed38105`; PNG, 4454×2634, 65,327 bytes. The local file exists and decodes. It is an Amazon Prime logo, not product photography.
- Batch 13 has 15 source-row occurrences with that exact asset, Batch 14 has 73; 15 overlap. The union is **73 unique ASINs/listings**. Exact affected listing IDs and ASINs are in the CSV. Each matching primary row is stored as Amazon catalog/reference media and marked approved in the listing metadata; that approval state is not evidence of product identity or redistribution rights.
- Confirmed route: `AmazonProductMediaProvider._lookup_from_product_page` extracted page metadata/image URLs and called the generic `LocalStorage.save_from_url`; any successfully saved URL caused `ProductMediaCache.fetch_status="fetched"`. The extractor included page-wide JSON-LD and `og:image` as well as product widget data. The source provider is `page_metadata` for the Prime records. Persisted evidence does **not** record which selector returned the specific URL, so the precise selector cannot be proven retrospectively.
- A second unvalidated path accepts browser bridge image URLs via `cache_gallery_from_remote_urls`; the bridge page scraper does not persist page-ASIN or selected-variation evidence alongside each candidate. This allows “image URL downloaded” to be confused with “exact product image verified.”
- The previous “15 missing images” query tested whether a primary image field was empty. It therefore counted 15 empty primaries per batch while counting the Prime graphic as an image. It did not test visual identity, decoder/displayability, image provenance, or rights.

## Image reconciliation

| Measure | Batch 13 | Batch 14 | Unique union (avoid double count) |
|---|---:|---:|---:|
| Unique linked listings | 1,349 | 1,420 | 1,420 |
| No primary image metadata | 15 | 15 | 15 |
| Confirmed exact Prime logo as primary | 15 | 73 | 73 |
| Primary local paths missing on disk | 86 | 86 | 86 |
| Other primary local files physically present and container-verified | 1,233 | 1,246 | 1,246 |
| Proven exact-product matches among the non-placeholder files | Not recorded | Not recorded | Not established |
| Rights verified | 0 evidenced | 0 evidenced | 0 evidenced |

The union of listings with no primary metadata, a missing primary local file, or the confirmed Prime placeholder is **174 unique listings**: 73 Prime, 86 missing-file primaries, and 15 without primary metadata. These groups do not overlap in the audited snapshot. This is a conservative “no currently usable primary established” count; some of the 86 may have usable alternate gallery media, which the row-level CSV records and which must be checked before repair.

The 86 missing primary files are a shared cohort. A missing primary does not mean every gallery image is missing; alternate gallery media is recorded per row in the CSV and should be reviewed before acquisition. The remaining existing files were checked for local existence and image-container validity, not manually matched visually to product variants. They are marked identity-unverified. Repeated hashes across unrelated ASINs are recorded evidence for review, not automatic rejection, because legitimate manufacturer family imagery exists.

No remote URL probes were necessary for the identified bad image: the source resolved to the local saved file. Other saved gallery paths are local. This audit did not make network requests to Amazon or marketplace services.

## Code correction included in this revision

- Added reusable `image_quality` validation with known Prime asset URL/hash detection, bounded byte handling, decoder/dimension checks, optional exact-ASIN/variation evidence, and conservative verdicts. Hash similarity/reuse is not treated as proof.
- The fast Amazon HTML extractor now drops the known Prime logo asset before it can be saved as product media.
- Added deterministic unit coverage for known logo detection and exact page/variation matching. This is a narrow first integrity layer; it does not yet provide a durable per-ASIN browser-gallery job queue or candidate review/approval API.

## Remaining work and safe repair sequence

1. Review the 73 confirmed Prime-primary listings and 86 missing-primary paths against alternate gallery assets. Do not automatically replace any approved image.
2. Add a durable, idempotent acquisition queue that stores exact-ASIN page/variation evidence and candidate URLs separately from listing galleries.
3. Gate browser collection on an existing paired, authorized Amazon bridge account. The currently inspected bridge import path permits Amazon without an account; invoking it for this task would violate the no-arbitrary-session requirement. Until the bridge exposes that authorization gate, affected rows need pending/manual review rather than automated browser access.
4. Candidate media should remain pending operator review with source and rights unverified. Explicit approval is required before attaching or promoting it. No external submission is implied.

Draft preparation remains independent from seller-taken photos per the existing Vine readiness correction. This report does not claim that the affected listings are image-ready for final marketplace submission.
