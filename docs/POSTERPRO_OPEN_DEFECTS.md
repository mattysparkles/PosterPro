# PosterPro open defects

### 2026-10-19 local-vs-remote required-specific visibility (PUB-014)

- Recovery preview compares local `item_specifics` names with current required
  aspects for the exact remote offer category and returns
  `remote_required_aspects_missing_locally`; Settings → eBay displays that
  diff beside remote category/aspect evidence.
- Regression passed (1 test); targeted frontend lint: 0 errors and one
  pre-existing hook dependency warning.
- For listings 3440, 3425 and 3407, this exposes missing `Product` or
  `Original/Reproduction` locally but does not make the remote categories
  appropriate. Retrieved identity facts lack reliable brand/model/MPN, and
  the current prices are Vine estimated-tax fallback values, not market
  research. Keep blocked; no retry/category revision/deployment/live mutation.

| ID | Symptom | Confirmed cause/evidence | Status | Safe next action |
|---|---|---|---|---|
| DEF-REV-001 | Calculated shipping could be based on invented parcel facts | `derive_shipping_profile` assigned title/category weight/dimensions | Fixed locally, unverified deployment | Deploy only after broader regression run; audit existing estimated profiles. |
| DEF-REV-002 | Under-$25 default was $10; above-threshold path implied free shipping | Legacy workflow defaults used $10/free shipping | Fixed locally, unverified deployment | Tenant settings and UI now persist policy controls; above-threshold default preserves existing. Deploy after clean release review. |
| DEF-REV-003 | Local eBay publication state differs from current remote active state (254 vs 162) | Fresh exact reconciliation found 254 unique local MarketplaceListing rows marked PUBLISHED; all 162 current active remote listing IDs map locally, while 92 local PUBLISHED IDs are absent from the active snapshot. Absence alone does not reveal whether remote state is ended, sold, inactive, or otherwise unavailable. | Open | Shipping/handling preview blocks bulk revision while identity drift remains. Reconcile those 92 using supported ended/sold state reads before changing local publication state. |
| DEF-REV-004 | Existing-listing handling preview did not inspect listings above the shipping threshold or identify legacy shared policies | Prior endpoint fetched exact Trading details only for under-$25 rows and did not parse `SellerShippingProfile` IDs | Mitigated locally, unverified deployment | Preview now uses paginated Trading `GetMyeBaySelling` plus one Account fulfillment-policy read and one `GeteBayDetails` capability read, avoiding per-item `GetItem` fan-out where policy/listing data is present. Fresh live local-code preview returned 162 active (161 at 0, one at 1); supported results 1/2. Shared active policy references are 161/1. Inactive/draft dependencies remain unknown and are blockers. New regression ensures only DOMESTIC policy options drive shipping responsibility/service when INTERNATIONAL is first; full operations-preview suite 11 passed. No revision/deployment. |
| DEF-REV-005 | eBay publication could submit invented or provenance-unknown parcel facts as if measured | Five failed Trading listings had package weight/dimensions from `estimated_category_profile` with numeric values and `manual_measurement_needed=false`; provider errors said weight was invalid/missing. The shared eBay preflight checked presence but not measurement provenance, and payload builder serialized estimates. | Fixed locally, deployment unverified | Listing Editor now asks the operator to attest final packed measurements; changes clear confirmation. Central provenance requires per-field `operator_measured_packed_parcel`; preflight blocks estimated/unverified required values and payload omits them. Three focused suites: 77 passed; frontend build passed. No deployment/retry; five records still require measured packed values. |
| DEF-REV-006 | Shipping preview could not distinguish research-supported price and complete profit from gross proceeds | Preview fee estimate used local ask even when it differed from remote; all economics were labeled `estimated_net_before_seller_shipping` and subtracted item cost, which could imply profit without shipping/material/promotion/other costs. | Fixed locally, unverified deployment | Preview now uses the remote ask for fee calculation, exposes price evidence and local/remote mismatch, labels net proceeds before label cost separately, and reports profit only with explicit item/label/packaging/promotion/other costs. Backend preview + eBay fee tests 19 passed; CSV Node test 1 passed; Next build passed. No deployment or remote change. |
| DEF-ENR-012 | Revenue-risk preview omitted provenance stored under legacy `price_source` | Local preview only read `pricing_analysis.provenance`, while production-shaped timeline records store `price_source`; stale eligibility could mask an ETV fallback. | Fixed locally, unverified deployment | Preview now normalizes either provenance key and rejects ETV/category/fallback provenance even if stale eligibility is true. Regression plus existing preview test: 2 passed; compile and diff check passed. No deployment/provider mutation. |
| DEF-PUB-001 | Latest 25-item eBay batch failed | Batch `approve-09df…`: 8 category/condition rejections, 3 Inventory requirement responses, and 14 eBay Inventory API system 25001 responses. Listing IDs are recorded below. | Open | Per-item classification and preflight correction; no blind retries. |
| DEF-FB-001 | Facebook jobs fail or remain unconfirmed | Fresh production DB: device 73 is active, paired at `0.3.70`, and checked in 2026-10-19 03:02 UTC; source manifest is also `0.3.70`. Last 30 days: 155 jobs, 102 failed (60 form-stall retry exhausted, 17 required fields, 13 dirty form, 12 other); 32 `SUBMITTED_PENDING_REVIEW` have no remote ID/URL (13 `PENDING_REVIEW`, 19 lack reconciliation). Recent job attempts are 0.3.68/.69; no 0.3.70 jobs exist yet. Job 193's stale .69 result explicitly saw $59.90 requested vs $60 observed and correctly withheld submit. | Open | Run the safe diagnostic with the actual paired .70 browser; resolve pending-review records, then use an operator-approved test create and verify remote identity. Do not infer a price mismatch is safe to round. |
| DEF-MER-001 | Mercari has no authorized PosterPro publication interface or verified commercial CREATE | Live DB read on 2026-10-19: 21 extension jobs, comprising 1 CREATE (job 60, failed `REMOTE_RECONCILIATION_NOT_FOUND`) and 20 diagnostics; 11 completed, 5 failed, 5 blocked. No remote listing ID/URL and no active/queued/retryable jobs. Two non-revoked devices were recorded (0.3.70 last seen 2026-10-19 04:06:43 UTC; 0.3.2 last seen 2026-09-25). Mercari's [current Prohibited Conduct policy](https://www.mercari.com/us/help_center/topics/listing/policies/prohibited-conduct/) prohibits automated means/interfaces not provided by Mercari. | Open; publication is manual-only pending an authorized provider | Local guard blocks new extension CREATE/UPDATE/END, synthetic form diagnostics, and queued/retryable Mercari job claims. Settings and Listing Workspace explain/carry manual handoff. Tests: focused extension selection 8 passed; routing suite 11 passed; frontend build passed. Not deployed. Existing active queue is empty; this does not prove a remote listing exists. Manual readiness/verification is unfinished. |
| DEF-INV-001 | Packing capture actions were not using the IndexedDB journal; offline capture was lost or left without an item boundary | `frontend/lib/intakeOfflineJournal.js` existed, but `packing.js` called scanner, Slate, photo, and voice APIs directly. Retrying Slate/media after a lost acknowledgement could duplicate records. | Fixed locally, unverified deployment | Packing page now queues scan/photo/voice in order and replays on reconnect; Slate and photo endpoints dedupe by stable client event ID. Targeted API tests 3 passed and offline interaction/journal tests 4 passed. No deployment or physical browser test. |
| DEF-INV-002 | Offline plain-text scanner commands were treated as product barcodes | In `frontend/pages/packing.js`, `nextPackingState` and offline queue classification only parsed `POSTERPRO:COMMAND:...`; USB/Bluetooth wedge readers that return a literal command label such as `NEXT ITEM` opened a false provisional item boundary. | Fixed locally, unverified deployment | Shared label normalizer now accepts plain and namespaced registered commands; local state subset is applied, while other registered-but-unimplemented actions are explicitly reported as incomplete. Regression: `frontend/test/packing.offline.interaction.test.jsx` (2 passed), backend dispatcher tests (3 passed). No deploy or device rehearsal. |
| DEF-INV-003 | NEW PACKAGE and PRINT LABEL scanner commands lacked inventory-side actions | Scanner dispatcher journaled commands but did not create a package or queue a thermal label; the UI could not expose a durable label result. | Fixed locally, unverified deployment | Tenant-scoped dispatcher now derives a stable package code from user+operation identity, persists package linkage in workstation checkpoint, queues one 4×6 `InventoryPrintJob` per idempotent operation, and blocks PRINT LABEL without an active owned package. Regression: backend scanner dispatcher 5 passed; offline packing UI 2 passed. No deploy, physical device, or printer verification. Remaining package-content/stock movement and other commands are separate open requirements. |
| DEF-INV-004 | NEW TOTE followed by tote barcode was handled as a product scan or gave no operator cue | `NEW TOTE` and subsequent identity were not linked in workstation state; offline and live code paths could create a false item boundary. | Fixed locally, unverified deployment | Dispatcher persists an awaiting-tote checkpoint, treats exactly the next scan as a tenant-owned tote create/select, and writes the active tote. UI cues the operator, including while offline, and does not create a Slate. Backend scanner dispatcher 6 passed; offline packing interaction 3 passed. No deployment, HID/NFC verification. |
| DEF-INV-005 | FIND ITEM scanner command did not resolve a barcode to owned stock location | The command was recognized but had no inventory-side action; follow-up barcode would be handled as a product capture. | Fixed locally, unverified deployment | `FIND ITEM` now arms a query checkpoint; next scan performs the existing tenant-scoped private inventory lookup and returns product/tote/package availability without creating a Slate. Offline query is journaled and reconciled on reconnect. Backend scanner tests 7 passed; offline UI tests 4 passed. No deployment/device verification. |
| DEF-INV-006 | Explicit REPRINT LABEL scanner command was recognized but did not queue a label | Only PRINT LABEL mapped to the durable package label service; REPRINT LABEL was treated as unimplemented. | Fixed locally, unverified deployment | Both commands now queue one new label job per unique operation; repeated delivery of the same operation returns the existing job. Missing active package remains blocked. Backend scanner suite 7 passed. No deploy or physical printer verification. |
| DEF-INV-007 | MOVE ITEM command had no safe inventory transfer path | Scanner registry knew the command, but there was no common tote/package transfer operation or operator-confirmed scan workflow. | Fixed locally, unverified deployment | Added an authenticated, tenant-scoped, idempotent transfer route and packing UI source/destination/quantity confirmation. Transfers are limited to owned totes/open packages and unreserved stock; sealed packages cannot be a source; audit event recorded. Tests cover stock conservation, replay, reserved quantity, foreign destination and closed package. Package + scanner suites 15 passed; packing interaction suite 5 passed including confirmation/no-Slate behavior; targeted ESLint 0 errors, one existing hook warning. Still requires real browser/offline replay verification and no production/hardware test has occurred. |
| DEF-ENR-001 | Generic/arbitrary intake prices | Live recovery catalog has 961 timeline-derived recovery rows; sampled rows used unproven `$17.99`-style values and generic/OCR titles without pricing provenance. Intake fallback assigned 24.0; category fallbacks include 18.99/21.99/24.99. | Fixed locally, unverified deployment | Evidence gate blocks unresearched media-recovery prices and new recovery candidates start research pending; complete real research integration and deploy an isolated increment. |
| DEF-PUB-002 | Cached eBay preflight cleared known category failures without proving category plausibility | Read-only preflight had returned zero blockers for deterministic failures, including RV LED fixtures → `Adhesive Insect Light Traps` and scale labels → `Stickers` | Fixed locally, unverified deployment | New semantic guards block demonstrated collisions; require current taxonomy review and provider verification before retry. |
| DEF-PUB-003 | Operators had no dashboard report joining the 25 failed eBay submissions to current category/aspect requirements | The batch evidence was available only through a backend preview; no recovery action could be reviewed per item in Settings | Fixed locally, unverified deployment | Settings → eBay renders all 25 failures. The read-only endpoint queries current taxonomy/aspects for the 11 deterministic failures and blocks category IDs without unique synchronized tree ownership. New query has not been deployed or run against the saved live batch. The 14 `25001` results remain unknown and no retry is enabled. |
| DEF-PUB-008 | Failed-batch category recovery lacked safe current-taxonomy search, exact correction preview, and tenant-filtered child queries | Operators could see a mismatch but had no in-context way to research or compare a replacement category; recovery lookup selected jobs/listings by IDs without enforcing row ownership | Fixed locally, unverified deployment | Added read-only per-listing category suggestions with leaf/aspect/condition policy evidence and detailed selection-only before/after preview; no category is applied. Live read-only endpoint test on item 2744 returned the current Insect Trap path and five candidates with current requirements. Recovery service filters jobs/listings by owner; cross-tenant regression covered. Focused suite 18 passed; frontend build passed; not deployed. |
| DEF-PUB-009 | The 25-item eBay recovery report could not validate category-specific condition requirements and exposed prose as if it were a missing field | The recovery path queried Taxonomy aspects but not Sell Metadata `get_item_condition_policies`; 25002 error parameters may contain both a field name and explanatory sentence. Authenticated read-only replay shows 8 category errors (5 Trading category invalid with condition dropped and weight invalid/missing; 3 Inventory invalid category), 3 exact required specifics, and 14 unknown outcomes. Metadata policy results for the 11 deterministic rows all returned; the 8 category-error rows had `New` among their category choices. | Fixed locally, deployed unverified | Added provider condition-policy lookup/UI evidence without guessing enum mapping; parser now returns only exact missing names. Recovery + environment suites 18 passed, frontend build passed. No data edits or retry. Remaining: review replacement categories, measured weights, and exact remediation before any approved retry. |
| DEF-PUB-014 | Legacy Trading AddFixedPriceItem failure records omitted HTTP method | Five historical failed rows retained endpoint `AddFixedPriceItem` and provider response but no method, limiting request reconstruction; endpoint contract uses an HTTP POST. | Fixed locally, unverified deployment | All post-submission Trading exceptions now carry `method="POST"`; targeted provider-rejection and serializer regressions passed (2). Historical failure rows cannot be backfilled and remain method-unknown. No publish/retry/deployment. |
| DEF-ENR-005 | Category fallback values could be shown as recommendation amounts despite an ineligible-evidence marker | `_category_fallback_baseline` supplied $18.99/$21.99/$24.99 paths and service response populated recommendation fields even when `recommendation_eligible=false`; dashboard IntelligencePanel printed any non-null recommendation. | Fixed locally, not deployed; integration impact open | Unverified amounts now use only `unverified_estimate`, recommendation variants are null, dashboard withholds them. Pricing evidence tests 6 passed; frontend build passed. Broader listing-processing suite had 8 failed promotion/readiness assertions (33 passed); investigate before release and preserve block on timeline drafts lacking evidence. |
| DEF-ENR-006 | Recovery/intake/legacy eBay code could assign or publish unsupported $19.99/$24.99/image-derived amounts | `MediaRecoveryService._proposal_facts` filled missing visual estimates with $19.99 and fixed estimated package dimensions/weight; ListingProcessing copied model estimates into listing/suggested price; ListingAI supplied $24; legacy PricingService assigned rule/LLM prices; eBay price resolver defaulted to $19.99 and accepted acquisition estimate. | Fixed locally, not deployed | Visual values now persist only as unverified provenance; recovery has no fabricated price or parcel facts; ListingAI no longer invents $24; PricingService returns evidence-backed review-only proposal without changing ask; eBay ignores estimate/fallback and blocks missing evidence/price. Combined targeted tests: 120 passed. Existing persisted listing fields have not been bulk-changed; requires read-only audit. |

| DEF-ENR-007 | Existing Google Photos/recovery drafts retain many asking-price values without reliable research provenance | Read-only live DB query found 962 recovery + 404 Head Slate rows with an ask field; 410 recovery + 121 Head Slate rows have missing/ineligible provenance; 369 + 99 have known fallback amounts. None has local Listing.status PUBLISHED; local eBay crosspost rows are 7/3 with no remote listing ID or published marker. This does not prove the items are not remotely active. | Open; no data modified | Reconcile source/listing IDs against authenticated remote snapshot, export exact operator preview with provenance and status, block unsupported drafts, and require approval before repricing or ending any remote listing. Do not bulk-clear asking price fields. |
| DEF-ENR-008 | Automated eBay research cannot currently provide authorized sold-history comps | The integration uses Browse and correctly labels results as active asking listings; it returns no sold transaction history. Official eBay Marketplace Insights docs describe sold history up to 90 days but classify the API as Limited Release for selected approved developers/categories. PosterPro production entitlement was not verified. | Open external capability check | Verify account entitlement through eBay developer console or support; until then preserve `sold_comparable_count=0`, distinguish active asks from sold transactions, and keep unsubstantiated price recommendations blocked. |
| DEF-ENR-009 | Timeline/photo listing readiness admitted arbitrary $21.60 category-model prices as Needs Review | `canonical_listing_readiness` only rejected exact $20/$21/$22 placeholders (or all unresearched recovery records); `category_model_estimate` at $21.60 had `recommendation_eligible=false` but bypassed that condition. Read-only production DB evaluation found 115 Needs Review among 1,357 timeline/photo rows; 92 had this fallback provenance, 9 had missing provenance. | Fixed locally, unverified deployment | Photo-derived pricing now requires `recommendation_eligible=true`, recognized evidence provenance, nonzero evidence count, and confidence ≥0.45, or explicit operator price. Three focused tests pass. Full read-only reevaluation: zero Needs Review. Deployment and generation of real acceptable drafts remain open; do not treat zero queue rows as product success. |
| DEF-ENR-012 | Vine estimated-tax-value and deterministic estimate paths could become listing asks | Failed eBay records 3440/3425/3407 had `vine_estimated_tax_value_fallback` provenance and were treated as usable prices by canonical readiness; previous cleanup also removed the research blocker. | Fixed locally, not deployed | Authoritative price resolution now refuses ETV/category-model/fallback provenance unless explicitly operator-owned; readiness keeps those rows out of Needs Review even with stale `needs_price_research=false`. Four focused pricing regressions pass; complete readiness/aggregate run: 62 passed. Operations preview reads legacy `price_source` and rejects ETV despite stale eligibility. Existing rows need real research/operator price; no remote retry. |
| DEF-REV-008 | Missing packed parcel data was conflated with product identity/readiness failure | Canonical readiness treated an incomplete shipping profile as a generic blocker, making otherwise complete drafts appear Needs Attention instead of exposing shipping setup as an operator action. | Fixed locally, unverified deployment | Readiness emits `shipping_setup_required=true` and an actionable warning, allows otherwise complete draft review, and retains `publishable=false` until measured parcel facts exist. Listing detail visibly renders this state. Targeted regressions and complete readiness/aggregate suites: 62 passed; targeted ESLint: 0 errors, 7 existing warnings. No measurements are estimated. |

### DEF-PUB-009 exact saved category for three required-data failures

Read-only current taxonomy lookup for the failed drafts returned:

| Listing | Provider-named missing field | Saved eBay category path | Current Taxonomy required aspects | Evidence note |
|---|---|---|---|---|
| 3440 — LONELY BINARY RS485 transceiver modules | `Product` | Business & Industrial > Electrical Equipment & Supplies > Electronic Components & Semiconductors > Semiconductors & Actives > Integrated Circuits > Interface ICs | `Brand` (present as `Unbranded`) | Provider's publish rejection names `Product`, but the current Taxonomy aspect response for the saved category does not. Don't substitute the title or infer the expected value format; needs a provider-diagnostic/category-specific field check. |
| 3425 — YOWEXA paint thickness gauge | `Original/Reproduction` | Home & Garden > Tools & Workshop Equipment > Measuring & Layout Tools > Other Measuring & Layout Tools | `Brand` (present as `Unbranded`) | The saved category's current Taxonomy required-aspect response does not list the provider-rejected field. Preserve the exact error and verify the supported field/value at the listing API layer. |
| 3407 — Rvhouse 12V RV dome light | `Original/Reproduction` | Home & Garden > Lamps, Lighting & Ceiling Fans > Chandeliers & Ceiling Fixtures | `Brand` and `Type` (both names present; `Brand=Unbranded`) | The product likely merits an RV/vehicle lighting category review, but search candidates were mixed. Do not move category or assert equivalence without operator validation. |

Current category search for these three returned noisy alternatives, and the
candidate-level required-aspect results likewise did not explain the exact
provider rejection. This is a concrete layer mismatch: provider responses
remain authoritative for repair, while Taxonomy metadata alone is insufficient
to explain the Item Specifics call. Next investigate the retained request body,
provider method/endpoint, and supported aspect mapping without retrying or
changing a listing.

Historical forensic limit: for listings 3440/3425/3407, durable job results
contain the error response and endpoint `/sell/inventory/v1/offer/{offerId}/publish`
but no submitted request body or HTTP method. Method cannot be asserted from
the endpoint alone. New local code records HTTP method and a sanitized field-name
summary on future failures, without logging product title/description or aspect
values. The historical requests remain unrecoverable; no replay or edit ran.

### DEF-PUB-009 per-item candidate reconnaissance (read-only, 2026-10-18)

The current eBay taxonomy search was exercised for all eight category/condition
failures using the failed draft's product words. These are candidate leaves,
not approved corrections; search relevance is mixed and current specifics have
data-quality issues. A leaf result alone does not establish product fit.

| Listing | Current category ID | Candidate leaves returned (IDs) | Draft evidence needing human validation |
|---|---:|---|---|
| 3475 — 165° UTV windshield wiper kit | 178918 | Other ATV/Side-by-Side/UTV Parts (43972); Wipers/Washers (263159); Wiper Linkages/Motors (61941); Other Car/Truck Parts (9886); Additional Wiper Components (262179) | Saved specifics include universal fit and part M005BK, but several consumer-copy attributes are mixed into specifics; verify vehicle fitment and category before selecting. |
| 3406 — 9×5 ft T-top sun shade | 26447 | Other ATV/Side-by-Side/UTV Parts (43972); Trim (33705); Covers (26447); Other Body/Frame (43971); Other Golf Cart Parts (75208) | Saved `Type=Backpack` conflicts with the product; supplied data says boat/UTV/RV, so product type and intended taxonomy need confirmation. |
| 3405 — 8040 scale labels, 36 rolls | 11794 | Tabletop Scales (184280); Address/Shipping Labels (184271); Printers (1245); Labels (567); POS Scales (71475) | Current specifics mix labels, printer, and scale concepts; determine whether the item is the label consumable and validate compatible printer model before choosing. |
| 3201 — StarknightMT Ranger windshield | 100467 | Windshields/Windows (178918); Other Auto Tools (43998); Windshields (35567); Other ATV/Side-by-Side/UTV Parts (43972); Wiper Components (61941) | Brand and MPN are present; title asserts Ranger years/models. Verify exact vehicle fitment and leaf choice; do not treat a broad windshield result as fitment proof. |
| 2751 — UTV turn signal kit | 263320 | Other Lighting (43980); Other Electrical/Ignition (43976); Turn Signal Assemblies (178023); Mirror Assemblies (263125); Turn Signal Assemblies (263320) | Saved data conflicts (`Vehicle Service Type=Motorcycle`, year 1998, CFMOTO, rear/front, dimensions in inches); reconcile source evidence before using specifics or fitment. |
| 2744 — Fouwick RV interior LED fixtures | 183782 | Light Bulbs (20706); Chandeliers/Ceiling Fixtures (117503); Tools/Supplies (4843); Car Lighting (71536) | Saved `Type=Switch` conflicts with fixture title; MPN fcd-1 and 12V are present. Confirm actual fixture form and required aspects. |
| 2283 — Bisida 20S/72V/60A BMS | 259063 | UPS (99265); Rechargeable Batteries (48619); Other Business/Industrial (26261); Alternative Energy (41980); UPS Components (48479) | Only Brand=Unbranded and Model=Universal are saved despite specific BMS title; confirm part number, chemistry/voltage/current, application, and category from package/product evidence. |
| 2238 — 11273N alternator | 177697 | Alternators/Generators (177697); Pullers/Extractors (179522); Other Car/Truck Parts (9886); Aviation (50453); Body Moldings/Trims (33654) | Part/brand fields conflict (`Brand=11273N Alternator`, MPN HRDAMALT-11273, OEM 11273); verify catalog fitment and identity before correction. |

The searches were live provider reads only. The compact reconnaissance call did not
fetch candidate-specific required-aspect lists for all 40 suggestions, did not
write categories/condition/specifics, and did not retry publication. The
existing per-category UI/API can fetch aspects and condition policy for a
selected leaf. Exact accepted field validation and measured parcel weights are
still required before a safe retry preview can be enabled.

### Current provider taxonomy suggestions for the eight category failures

Read-only current-tree searches were run for all eight item titles. These are
candidate evidence only, not accepted category corrections:

| Listing | Product evidence | Stored category | Current provider suggestion(s) | Remaining review |
|---|---|---|---|---|
| 3475 | 165° UTV windshield wiper kit | Windshields & Windows | Wiper Linkages, Transmissions & Motors | Confirm specific part type/fitment and category. |
| 3406 | Boat/UTV/RV T-top sun shade | Covers | Covers; Anchoring, Docking | Existing label may be plausible; inspect taxonomy details and item type. |
| 3405 | Scale printer labels, 58×60mm | Stickers | Address & Shipping Labels; Tabletop Scales; Other Packing & Shipping | Choose label vs scale taxonomy based on actual intended use/catalog. |
| 3201 | Polaris Ranger/Fullsize flip windshield | Windshields | Windshields & Windows | Verify fitment/model range and current category policy. |
| 2751 | UTV/ATV turn signal kit | Turn Signal Light Assemblies | Turn Signal Light Assemblies; Other Lighting & Lamps | Choose appropriate assembly/electrical category and validate required specifics. |
| 2744 | 12V RV interior LED fixtures | Adhesive Insect Light Traps | Light Bulbs; Car Lighting; Tools & Supplies | Resolve between RV/vehicle lighting and bulb category from item form. |
| 2283 | 20S 72V 60A BMS protection board | Batteries, Battery Boxes & Chargers | Printed Circuit Boards; Other Electrical Equipment & Supplies; Power Regulators & Converters | Confirm whether a BMS board or complete battery is being sold. |
| 2238 | 11273N alternator | Alternators & Generators | Alternators & Generators | Existing category is plausible; review required compatibility specifics. |
| DEF-ENR-002 | Category/model fallback prices appeared recommendation-eligible and could be applied despite no market evidence | `PricingResearchService` only marked `fallback_estimate` ineligible; `category_model_estimate` (e.g. 21.99) incorrectly returned eligible. The apply API did not require evidence or distinguish a documented operator-entered price. | Fixed locally, unverified deployment | Both estimate-only provenance classes now return `recommendation_eligible=false`; apply rejects unsupported estimates without an explicit entered amount/reason and rejects local applies to live-published items. Focused pricing suites 21 passed. Real comparable research adapter and deployment remain open. |
| DEF-ENR-003 | Timeline drafts did not automatically obtain external comparable evidence; legacy `EbayService.enrich_price` returns no comparables | The timeline creation path still invokes the legacy stub; the new Browse adapter was only reachable from a user-triggered pricing refresh. | Partially mitigated locally, unverified deployment | Added bounded Celery beat research task over unreviewed `intake_head_slate` drafts, using shared adapter/persistence. It stores query/provider provenance and asking-only analysis without changing price/readiness. Worker tests cover batch bound, no mutation, and retry cadence. No production worker execution; sold comps, stronger identity/fitment, and deployment remain open. |
| DEF-PUB-004 | Failed-batch recovery preview crashed on persisted string-valued eBay provider errors | Five of 25 durable records stored `provider_response.errors` as a string rather than a list, and `_required_fields` called `.get()` on each character | Fixed locally, unverified deployment | Parser now safely handles JSON strings, unstructured strings, dicts, and lists. Regression suite 5 passed. Direct read-only live preview then returned all 25 rows (11 current taxonomy checks, 14 active-SKU reconciliation results), 0 retryable; local endpoint/UI is undeployed. |
| DEF-PUB-005 | Fourteen eBay 25001 calls had unknown remote outcomes | Fresh active Trading snapshot had 0 exact PosterPro SKU matches; supported Inventory `getOffers?sku=<exact>` returned HTTP 404/error 25713 (“This Offer is not available”) for all 14 SKUs. Historical exact offer-ID GETs returned HTTP 500/25001. The saved original endpoint retained only the resource path, not HTTP method, so the failing step (read vs write) is unconfirmed. | Reconciled read-only locally; not deployed | Preview distinguishes exact-SKU no-offer from lookup failure and keeps `retry_eligible=false`. New REST error records retain HTTP method. Recovery/service/job-summary tests: 64 passed; frontend build passed. Trace historical offer IDs, validate listing readiness, then propose any retry for operator approval; no automatic retry. |
| DEF-PUB-006 | Current taxonomy leaf validity falsely appeared sufficient for eBay retry readiness | Live taxonomy/aspect reads confirmed all 14 `25001` rows’ category leaves and required specifics, but seven title/category pairs were obviously unrelated (including mattress topper→right-angle drills and golf cart LED kit→insect traps). Local resolver top candidates for several products remain implausible, so no replacement is safe to auto-select. | Fixed locally for seven observed pairs; not deployed | Added seven narrow semantic guards and an exact failed-batch regression; local live-code recovery preview now reports seven `CATEGORY_REVALIDATION_REQUIRED` blockers. Taxonomy/preflight: 68 passed; recovery/method capture: 10 passed. Next use current marketplace taxonomy search and operator confirmation to propose replacements. The other seven categories also need product-level review; no category updates or retries. |

## Latest failed eBay batch: `approve-09dfdc7ccb9d4f6088f0883365ac8c89`

| Failure class | Listing IDs | Corrective path |
|---|---|---|
| Category/condition | 3406, 2744, 2238, 2751, 2283, 3475, 3405, 3201 | Re-resolve current eBay taxonomy leaf and permitted condition/item specifics, then preflight. |
| Required listing data (25002) | 3425, 3440, 3407 | Read exact provider-required fields from the offer response, repair only those fields, then preflight. |
| eBay system error (25001) | 2264, 2233, 2755, 2727, 2252, 2216, 3199, 3228, 3223, 3218, 3214, 3202, 2763, 2758 | Reconcile remote offer/listing identity before retry; do not treat an unknown provider outcome as failed. |

### 2026-10-18 durable-record verification

- The batch is actually `failed` with 25 child jobs and no completed item; its
  saved summary reports 25 requested eBay CREATE operations.
- `3440` received Inventory API `25002` for missing required item specific
  `Product`; `3425` and `3407` received `25002` for missing
  `Original/Reproduction`. These need current taxonomy-aspect validation,
  not a generic retry.
- The Trading API rejection records include the provider text: invalid
  category, condition not applicable for category, and missing/invalid package
  weight. A measured package profile and category/condition revalidation are
  required before any controlled retry.
- The fourteen `25001` records were HTTP 500 Inventory API responses on the
  `/sell/inventory/v1/offer/{offerId}` resource path and are persisted with
  `remote_outcome=REMOTE_STATE_UNKNOWN`. Historical telemetry retained the
  path but not HTTP method, so it cannot establish whether the failed call was
  a read or a write. Reconcile by exact SKU and active-listing identity before
  any retry.
- **2026-10-18 authenticated recheck:** each of the fourteen exact retained
  Inventory offer IDs returned a fresh HTTP 500 / error `25001` to a bounded,
  read-only `GET /sell/inventory/v1/offer/{offerId}` request. No offer could
  be read as remotely created and no retry is safe. This is a current provider
  reconciliation blocker, not an inferred local state.

### 2026-10-18 deterministic-failure preflight evidence

All eleven non-25001 rows completed the current cached local eBay preflight
with zero blockers. That is not clearance to retry: the output still contains
implausible categories, including listing `2744` (RV LED fixture) in category
`183782` *Adhesive Insect Light Traps*, `3405` (scale labels) in `11794`
*Stickers*, and `3201` (UTV windshield) in the Snowmobile windshield category.
The existing preflight therefore does not prove category correctness and these
rows remain blocked for human/taxonomy validation.

### 2026-10-18 local repair verification

Added conservative semantic guards for the provider-observed collisions and
corrected the Motors preflight branch so `revalidation_required` cannot be
bypassed by otherwise valid Motors identity. Focused taxonomy tests passed
(`8 passed`); the actual read-only preflight now returns
`CATEGORY_REVALIDATION_REQUIRED` for listings `2744`, `3201`, and `3405`.
This repair is local and has not been deployed or provider-verified.

### 2026-10-18 authenticated active-offer reconciliation

- A fresh authenticated active-offer snapshot returned 162 active eBay offers;
  24 were priced below $25.
- A read-only shipping/handling preview inspected every returned offer. The 24
  under-threshold offers are all free/seller-funded calculated `USPSParcel`.
  All 24 are blocked from a Ground Advantage revision because saved parcel
  provenance is estimated/approximate and exact remote listing reads do not
  provide verified dimensions. For legacy offers, exact listing reads also
  cannot establish shared fulfillment-policy impact. Safe next steps in the
  preview are recording measured packed dimensions, choosing a separately
  approved supported alternative, or preparing deactivation for approval.
- Handling values were returned for all 162 offers: 161 at 0 business days
  and one at 1. The proposed increment is 1/2 days respectively. All remain
  blocked because supported choices and shared-policy effects were not
  verified; no remote change was made.
- For the 14 `25001` failures, the active snapshot contained no exact generated
  PosterPro SKU matches, and exact-SKU `getOffers` returned HTTP 404 / 25713
  for each. The observed exact offer-ID GET still returned HTTP 500 / `25001`.
  Retry remains disabled pending listing-readiness and historical request
  method investigation.
- New REST provider errors record the HTTP method as well as path/status; the
  historical 25-item jobs do not contain it. The local recovery UI now exposes
  active and exact-SKU results. Focused recovery/service tests: 45 passed;
  method-capture regression is also added. This code is not deployed.

### 2026-10-18 refreshed shipping/handling findings

The fresh authenticated preview confirms the 24 under-$25 listings are
currently free-shipping calculated `USPSParcel` (`buyer_pays_shipping=false`),
not merely an unspecified seller-paid policy. All 162 active remote IDs map to
local Listing records; 353 local IDs exist in the endpoint scope and 191 are
not active in this snapshot, with their ended/sold/inactive state unresolved.
All 24 low-price candidates still lack verified measured parcel data. Handling
is confirmed at 161×0 and 1×1 business days; +1 (1/2) is supported, but shared
policy `260642879013` has 161 active references. This is a preview only; no
live change was made and local code is not deployed. Do not treat modeled or
approximate parcel values as measurements.

### DEF-REV-007 — stale route-level shipping defaults

- First confirmed: 2026-10-18. A route-local workflow preference helper still
  defaulted to a $10 threshold and free shipping above it, unlike the shared
  policy/auth settings ($25 and preserve existing). Listing review consumes
  the shared policy, but route consumers could expose inconsistent values.
- Fix: route helper now calls `normalize_shipping_policy`; no-setting defaults
  are consistent, while an explicitly stored operator choice is retained.
- Regression: two focused tests cover safe defaults and explicit preference
  preservation (`backend/tests/test_marketplace_api.py`). Both pass locally.
- Deployment/verification: not deployed; no listing state or operator setting
  was changed. Add to isolated release review.

### 2026-10-19 authenticated remote offer evidence for DEF-PUB-001

- Read exact-SKU Inventory offers and their current inventory items for the
  three `25002` rows. All are `UNPUBLISHED` and none has a remote listing ID.
- Listing `3440` has remote category `179179` (Snacks > Chips), while the
  current local draft is `181905` (Interface ICs). Current provider taxonomy
  for the remote offer's category requires `Product`, absent from its remote
  aspects and named in the saved provider rejection.
- Listings `3425` and `3407` both have remote category `60409` (Television
  Memorabilia > Photographs > Color), while the current local drafts are
  `42632` (Other Measuring & Layout Tools) and `117503` (Chandeliers & Ceiling
  Fixtures). Current provider taxonomy for remote category `60409` requires
  `Original/Reproduction`, absent from both remote item aspect maps and named
  in the saved provider rejections.
- This establishes a remote/local category mismatch and explains the
  provider-required specifics in current taxonomy; it does not recover the
  historical publish request body. All three current local preflights block
  because the old amounts are unsupported estimates, and exact remote package
  field presence does not establish measurement provenance.
- Added read-only exact-SKU offer/inventory/category/aspect evidence to the
  recovery API and Settings → eBay UI. Regression suite: 14 passed; frontend
  build passed with existing warnings. No remote write, retry, deployment, or
  service restart. The 14 `25001` rows and 8 category/condition rows remain
  unresolved individually.

### 2026-10-19 read-only category/condition offer reconciliation

- For category/condition rows 2744, 2283, and 3405, exact PosterPro SKU reads
  found existing unpublished Inventory offers with remote categories 261084,
  262095, and 2208. Current eBay Taxonomy aspect lookups for those remote
  categories return HTTP 400 / error 62005, so the remote category records
  cannot be treated as current valid leaves. The local drafts have different
  categories; 2744 is currently Adhesive Insect Light Traps and 3405 is
  Stickers, both implausible for their product titles. No automatic
  replacement is safe.
- Exact-SKU `getOffers` returned HTTP 404 / 25713 for rows 3406, 2238, 2751,
  3475, and 3201. This means no offer was returned at query time; it is not
  proof that every possible remote state is absent.
- All eight current local preflights still block due `PRICING_EVIDENCE_MISSING`
  and `EBAY_PRICE_MISSING`; remote offer amounts are not accepted as approved
  recommendations. No retry is safe even if the category defect is fixed.
- Recovery API now renders exact remote category/aspect state and classifies
  404/25713 separately from transport/provider unavailability. Full recovery
  tests: 14 passed; py_compile, diff-check, and frontend build passed with
  existing warnings. No deployment, live offer/category edit, or retry.

### DEF-PUB-013 — eBay failed 25001 rows have inventory items but no confirmed offers

- Symptom: all 14 historical `EBAY_25001` failed batch rows have an exact-SKU
  remote Inventory item (`condition=NEW`), but active snapshot had no exact
  match and exact-SKU `getOffers` returned HTTP 404 / 25713 for every row.
- Root cause: not confirmed. The Inventory item lookup proves neither an offer
  nor a publication; historical provider request method/body was not retained.
- Reproduction: read-only recovery preview → exact SKU → inventory item found;
  offer lookup reports no exact-SKU offer. Do not infer no alternate listing
  identity or treat Inventory package values as measured parcel provenance.
- Fix: PUB-013 exposes this distinct state in the recovery UI/API and keeps
  retries disabled. Recovery suite: 14 passed; targeted related suite: 26
  passed; module compile, diff check, and Next build passed.
- Deployment/verification: local only, not deployed. No marketplace mutation.
- Next: reconcile alternate identity and source request evidence safely; only
  then present a controlled retry preview requiring operator approval.

### DEF-ENR-010 — eBay top taxonomy suggestion can contradict identified product

- Symptom: for timeline-derived Dorman 4WD Actuator 600-103 (listing 2405),
  current eBay category suggestion returned `Other US License Plates`. The
  listing had no validated category ID and canonical readiness correctly kept
  it in NEEDS_ATTENTION; no publish occurred.
- Research state: one authorized read-only Browse refresh stored 30 active
  asking comps, 0 sold comps, and provisional $106.79 with `active_comps`
  provenance/confidence 0.67. Listing ask, status, and processing state were
  unchanged. This is not a sold-price finding or accepted listing.
- Fix: add a narrow semantic guard and search a subsequent suggestion only
  after the top result flags, requiring a verified leaf; if no compatible leaf
  is available, fail closed for operator category review.
- Regression: focused set 4 passed; complete `test_ebay_service.py` +
  `test_ebay_taxonomy.py` suites 85 passed; py_compile and diff check passed.
  No category mutation/deployment.
- Remaining: this narrow guard does not prove category research complete across
  other products/marketplaces. The exact item also lacks condition/category
  readiness and confirmed automotive fitment.

### DEF-ENR-011 — Dorman 600-103 has no eBay catalog fitment rows

- Symptom: listing 2405 has verified Brand/MPN fields (Dorman / 600-103), but
  previously lacked structured vehicle applications.
- Verified provider result: eBay Catalog MPN search returned a unique exact
  product ePID `15079948485` (`Dorman 600103`, `4WD Actuator`). eBay Motors
  Metadata `get_product_compatibilities` returned zero application records.
  PosterPro persisted this as `compatibility_records_unavailable`; this does
  not mean the part fits no vehicles, and no fitment is asserted.
- Authoritative external manufacturer research identifies applications on
  [Dorman's 600-103 product page](https://www.dormanproducts.com/p-19432-600-103.aspx),
  but PosterPro has not implemented a permitted, repeatable Dorman catalog
  adapter or persisted that evidence; do not treat this one-off check as
  pipeline integration.
- Fix: add `ebay_fitment_research.py`, authenticated tenant-scoped research
  endpoint, and Listing Workspace evidence panel. Exact matching requires one
  Brand+MPN match and full-product revalidation; no match/ambiguity/zero
  compatibility fail closed. Tests: 6 passed; Next build passed. No listing
  content, price, status, readiness or remote marketplace state changed.
- Remaining: connect an authorized authoritative manufacturer/fitment source,
  persist its URL/date/vehicle fields with independent provenance, and test
  against representative automotive items. Not deployed.

### 2026-10-19 failed eBay batch current taxonomy candidate reads (read-only)

- Read the actual provider errors for the deterministic 11 rows and queried
  current eBay Taxonomy suggestions, verified leaf status, required aspects,
  and candidate condition policies. No category, condition, price, parcel,
  offer, or listing was changed.
- Five Trading `AddFixedPriceItem` rejections (3475, 3406, 3201, 2751, 2238)
  each retain the same combined error: invalid category, condition dropped as
  inapplicable, and package weight invalid or missing. Candidate research:
  3475 has Motors leaf 61941 Wiper Linkages/Transmissions/Motors (Brand
  required); 3201 has leaf 178918 Windshields & Windows (Brand); 2751 has
  leaf 178023 Turn Signal Light Assemblies (Brand). 3406's current leaf
  26447 Covers remains a suggestion (Brand + Manufacturer Part Number), so the
  provider rejection is not resolved by taxonomy existence alone. 2238's
  current leaf 177697 Alternators & Generators remains valid (Brand). All
  candidate condition policies include New, but this is not a substitute for
  checking the individual item's true condition. Each of the five still needs
  measured packed weight; no values were invented.

### DEF-PUB-009 follow-up — row 2238 catalog evidence (read-only, 2026-10-19)

The manufacturer DAH KEE's [DK 11273 record](https://www.dahkee.com/en/product/DK_Alternator-11273.html) describes a 12V, 150A alternator, Mitsubishi mounting, S6 pulley and includes 11273N in its OEM number list. This is a plausible candidate match for failed row 2238 but is not proof that the physical unit is DK-branded or the same variant: the failed row's saved Brand is `11273N Alternator`, MPN `HRDAMALT-11273`, and OEM `11273`. The manufacturer page's introductory prose says Lincoln MKS 3.7L / MKZ 3.5L while its detailed table says MKS 3.5L / MKZ 3.7L. Do not publish either disputed application until resolved by the physical label and stronger fitment data. This is technical identity research, not pricing/sold-comparable evidence. No category, specifics, price, parcel, offer, or listing was changed; exact-SKU remote `getOffers` previously returned 404/25713 and no publication retry was made.
- The three Inventory 25005 rows: 3405 has verified leaf 184271 Address &
  Shipping Labels (no required aspects reported); 2283 has leaf 185140 Printed
  Circuit Boards (Brand required); 2744's top results were Light Bulbs, Tools
  & Supplies, and Car Lighting, none selected as a semantic correction.
- The three 25002 rows remain exact provider-required fields on existing
  unpublished remote offers: 3440 `Product`, 3425 and 3407
  `Original/Reproduction`. Current local taxonomy is inconsistent with remote
  categories. Candidate title searches were noisy: 3440 returned electrical
  supplies/chipsets/embedded processors; 3425 returned HVAC gauges/inspection
  gages/meters; 3407 returned smart/landscape lights. No value/category was
  guessed or applied.
- This evidence narrows the repair path but does not produce retry readiness.
  The 14 `25001` unknown outcomes, unsupported prices, measured weights, and
  operator product/category choices remain unresolved. All retries remain
  disabled. Historical Trading calls lacked their HTTP method; new Trading
  failure capture now records `POST` (local regression verified, undeployed).
- Follow-up product research for failed row 3440 (`LONELY BINARY 12-Pack
  RS485 Transceiver Modules, MAX485 & MAX3485 Chips`) found manufacturer
  documentation at
  [Lonely Binary's RS485 box-contents page](https://learn.lonelybinary.com/manuals/rs485/whats-in-the-box)
  confirming 12 boards: six MAX485 5V and six MAX3485 3.3V, with loose
  straight/right-angle headers. This is useful identity/specification evidence,
  but does not confirm the actual physical unit or its condition/accessories.
  The exact local category `181905` is Interface ICs, while remote unpublished
  category `179179` is Snacks > Chips; both need category-level review for a
  board-module bundle. Existing rejected remote category requires `Product`,
  but no field value or category correction was applied. Public eBay search
  found active asks for individual MAX485/MAX3485 modules (e.g. $6.51), not a
  sufficiently matched 12-board bundle or verified sold comps, so it is not a
  price recommendation. Row 3440 remains blocked pending category choice,
  researched/operator price, parcel measurements and exact approval.
- Follow-up product research for failed row 3425 (`YOWEXA Paint Thickness
  Gauge for Cars, 0-5000μm, Bluetooth, Color Screen`) found the manufacturer's
  current [YOWEXA product-family catalog](https://ywcoating.com/) and its
  [EC-777/777E specification page](https://en.yowexa.com/prod_view.aspx?Fid=t3%3A10%3A3&Id=297&TypeId=10).
  The title attributes (YOWEXA, color screen, Bluetooth, 0-5000μm) closely
  match the manufacturer's currently described EC-777E variant, but the older
  EC-777/777E page states standard range 0-2000μm with custom ranges available;
  the physical model/range and included probe were not verified. The local
  draft's exact category is Other Measuring & Layout Tools, while the failed
  remote offer is a TV memorabilia photo category requiring
  `Original/Reproduction`; no correction was applied. Retailer/manufacturer
  price evidence matching the exact range/model/condition was not established,
  and ETV remains unsupported. This tool is not a vehicle-fitment part; no
  automotive compatibility claim is appropriate. Public eBay browse shows
  thickness gauges under general category `42291` and an automotive precision
  & measuring browse branch at `43991`; this presents a genuine general-vs-
  automotive category choice. Neither has been validated through current
  authenticated Taxonomy aspects for the exact leaf. Row 3425 remains blocked.
- Follow-up research for failed row 3407 (`Rvhouse 12V RV Dome Light, 1000LM
  Tri-Color Dimmable, 1 Pack`) located an exact-title retailer page at
  [IBSpot](https://ibspot.com/products/rvhouse-12v-rv-dome-light-1000lm-tri-color-dimmable-1-pack).
  It lists brand/manufacturer Rvhouse, part number `XGJ8LBC`, 12V, 1000 lm,
  three color temperatures, dimming, 3W, and product dimensions 0.75 × 5.2 ×
  7.76 in / 5.92 oz; displayed retailer ask was $40.29 when queried. This is an
  active retailer ask, not a completed sale, and does not verify the operator's
  physical model, condition, or included parts. Product dimensions/weight are
  not final packed-parcel measurements. Local specifics currently contradict
  the source/title (`Unbranded`, chandelier category, malformed dimensions),
  while the failed remote category is TV memorabilia requiring
  `Original/Reproduction`; no category or specifics were changed. The retailer
  amount is not yet accepted as a recommended ask or sold comparable. Row 3407
  remains blocked pending physical item verification, category validation,
  evidence-weighted pricing and measured parcel facts. Current eBay public
  category browse results place similar RV dome/interior lights in category
  `261084` (RV, Trailer & Camper Interior Lighting), which is a strong draft
  candidate over both the saved chandelier category and remote TV memorabilia
  node. It remains a candidate only until current authenticated Taxonomy API
  leaf/aspect validation and physical-item review.
# DEF-INV-008 — Bundle scanner capture is not a sellable inventory bundle (2026-10-19)

- Symptom: Packing can capture existing items as a bundle draft, but that result cannot be sold or fulfilled as a bundle.
- Affected workflows: scanner intake, inventory accounting, listing creation, sales and fulfillment.
- Root cause: no canonical bundle product identity, component reservation/consolidation, pricing/readiness model, marketplace relationship, or fulfillment decomposition.
- Reproduction: Packing → Start bundle → Add scanned item → scan an existing owned product → Finish bundle. Result enters `review_required` and explicitly says it is not sellable.
- Current fix: local-only review checkpoint; verifies ownership and availability at capture, does not reserve/move stock, create a Slate, or create a listing.
- Regression tests: `backend/tests/test_scanner_dispatcher.py::test_bundle_capture_records_identified_components_for_review_without_reserving_stock`; `frontend/test/packing.offline.interaction.test.jsx` (6 tests, including start→scan→review state).
- Deployment/verification: not deployed; focused API tests 2 passed and UI interaction suite 6 passed locally. No production inventory changed.
- Next: implement stable bundle identity, race-safe reversible reservations and rollback, then bundle pricing/listing and component-level fulfillment.

## DEF-OPS-001 — Frontend served HTML with missing build assets (2026-10-19)

- Symptom: operator reported PosterPro was unusable and displayed without correct styling/functionality.
- First confirmed: 2026-10-19 05:31 UTC. Affected: all Next.js pages requiring client hydration/styles.
- Confirmed root cause: running Next process held stale build ID `-_ARyL_X_jEqenk2UrLOT`, while `frontend/.next/static` had assets from a different build. HTML requested absent Listings and `_app` JS/CSS; those requests returned Next.js 400 error pages. Ordinary HTML route/health 200 checks missed the issue.
- Reproduction before recovery: GET local `/listings` -> 200 HTML, extract its script/style URLs, GET missing page chunks/CSS -> 400; current `.next` contained differently hashed equivalents.
- Fix: restarted only `posterpro-frontend.service`; its configured build pre-start completed and Next started build `Mspxrnyfm4AVG2tUxP8ii` at 05:33:36 UTC.
- Verification: local key routes all 200; every CSS/JS asset referenced by local `/listings` all 200; public `/listings` assets through Caddy all 200; all services active. Build log says compiled successfully, static pages generated, then service started.
- Deployment: production frontend restarted/rebuilt from current dirty worktree; no commit or reproducible release artifact. Backend/worker and customer data untouched.
- Status: asset mismatch resolved at HTTP asset level; visual/authenticated-browser acceptance remains pending because the operator reports the site still looks terrible and no browser automation is installed. Request hard refresh and exact route/screenshot/console for remaining UX issue. Do not close as fully resolved until operator confirms usable display.
