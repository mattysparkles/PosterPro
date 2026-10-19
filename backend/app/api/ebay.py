from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.schemas import EbayManualConnectRequest, EbayPublishConfirmationRequest
from app.core.auth import ensure_user_owns_resource, get_current_user, resolve_user_scope
from app.core.config import reload_settings, settings
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import EbayOfferHistory, Listing, MarketplaceAccount, MarketplaceCrosspostJob, MarketplaceMetadataCache, MarketplacePublishBatch, User
from app.services.ebay_service import (
    EbayIntegrationError,
    get_incoming_best_offers,
    authenticate_user_ebay,
    exchange_code_for_tokens,
    get_or_refresh_account,
    get_active_ebay_listings,
    get_ebay_trading_item,
    get_ebay_supported_handling_times,
    get_ebay_trading_active_operations,
    get_ebay_offers_for_sku,
    get_ebay_inventory_item_for_sku,
    list_business_policies,
    _build_ebay_sku,
    get_required_item_specifics,
    get_ebay_item_condition_policies,
    summarize_ebay_condition_policy,
    verify_ebay_category,
    _list_business_policies_for_account,
    parse_oauth_state,
    publish_listing_to_ebay,
    revise_ebay_listing,
    sync_ebay_active_listings,
    sync_ebay_fulfillment_history,
    suggest_ebay_category,
    search_ebay_categories,
    browse_ebay_categories,
)
from app.services.pricing_research_service import validate_marketplace_readiness
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.ebay_operations_preview import build_shipping_handling_preview
from app.services.ebay_failure_recovery import build_ebay_failed_batch_recovery_preview
router = APIRouter()


@router.get("/failed-batches/{batch_id}/recovery-preview")
async def preview_failed_ebay_batch_recovery(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return retained provider evidence and safe corrective steps; never retry."""
    preview = build_ebay_failed_batch_recovery_preview(db, batch_id=batch_id, user_id=current_user.id)
    if preview is None:
        raise HTTPException(status_code=404, detail="eBay publish batch not found")
    # Refresh taxonomy evidence for deterministic and remote-unknown failures.
    # All calls here are provider GETs; no listing, offer, or job is edited.
    candidates = [row for row in preview["rows"] if row.get("classification") in {"CATEGORY_OR_TAXONOMY", "CATEGORY_CONDITION_OR_PARCEL", "REQUIRED_DATA", "REMOTE_OUTCOME_UNKNOWN"}]
    listing_ids = {int(row["listing_id"]) for row in preview["rows"] if row.get("listing_id") is not None}
    listings = db.execute(select(Listing).where(Listing.user_id == current_user.id, Listing.id.in_(listing_ids))).scalars().all() if listing_ids else []
    listing_by_id = {listing.id: listing for listing in listings}
    account = None
    try:
        account = await get_or_refresh_account(current_user.id, db)
    except EbayIntegrationError as exc:
        for row in candidates:
            row["taxonomy_preflight"] = {"status": "unavailable", "source": "ebay_provider", "error": str(exc)[:400]}
    else:
        results_by_category = {}
        for row in candidates:
            listing = listing_by_id.get(row.get("listing_id"))
            if listing is None:
                row["taxonomy_preflight"] = {"status": "unavailable", "source": "local_mapping", "error": "Listing is not available in this operator scope."}
                continue
            source = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
            category_id = str(listing.category_id or listing.category_suggestion or "").strip()
            tree_id = str(source.get("taxonomy_tree_id") or "").strip()
            marketplace = str(source.get("taxonomy_marketplace") or "").upper()
            if not tree_id or not marketplace:
                from app.services.ebay_taxonomy import category_by_id
                ownership = []
                try:
                    for candidate_marketplace, candidate_tree in (("EBAY_US", "0"), ("EBAY_MOTORS_US", "100")):
                        if category_by_id(db, category_id, candidate_tree, candidate_marketplace):
                            ownership.append((candidate_marketplace, candidate_tree))
                except Exception:
                    ownership = []
                if len(ownership) == 1:
                    marketplace, tree_id = ownership[0]
                else:
                    row["taxonomy_preflight"] = {
                        "status": "blocked",
                        "source": "local_taxonomy_ownership",
                        "category_id": category_id,
                        "error": "Saved eBay category does not resolve to exactly one synchronized marketplace tree; select and validate its taxonomy ownership before retry.",
                    }
                    continue
            tree_id = str(tree_id).strip()
            marketplace = str(marketplace).upper()
            cache_key = (marketplace, tree_id, category_id)
            if not category_id or not category_id.isdigit():
                row["taxonomy_preflight"] = {"status": "blocked", "source": "current_listing", "category_id": category_id or None, "error": "No numeric eBay category is saved; select and validate a category before retry."}
                continue
            if cache_key not in results_by_category:
                try:
                    category = await verify_ebay_category(account, category_id, tree_id, marketplace_id=marketplace)
                    aspects = await get_required_item_specifics(account.access_token, category_id, marketplace_id=marketplace, tree_id=tree_id)
                    raw_aspects = aspects.get("aspects") if isinstance(aspects, dict) else []
                    required = [
                        str(aspect.get("localizedAspectName") or aspect.get("aspectName") or "").strip()
                        for aspect in (raw_aspects or []) if isinstance(aspect, dict)
                        and bool((aspect.get("aspectConstraint") or {}).get("aspectRequired"))
                        and str(aspect.get("localizedAspectName") or aspect.get("aspectName") or "").strip()
                    ]
                    results_by_category[cache_key] = {
                        "status": "confirmed" if category.get("verified") and category.get("leaf") is not None else "unavailable",
                        "source": "ebay_taxonomy_live",
                        "checked_at": datetime.now(UTC).isoformat(),
                        "marketplace": marketplace,
                        "tree_id": tree_id,
                        "category_id": category_id,
                        "category_exists": category.get("verified"),
                        "leaf": category.get("leaf"),
                        "category_verification_source": category.get("source"),
                        "required_aspects": required,
                        "category_tree_version": aspects.get("categoryTreeVersion") if isinstance(aspects, dict) else None,
                    }
                except EbayIntegrationError as exc:
                    results_by_category[cache_key] = {"status": "unavailable", "source": "ebay_taxonomy_live", "checked_at": datetime.now(UTC).isoformat(), "marketplace": marketplace, "tree_id": tree_id, "category_id": category_id, "error": str(exc)[:400]}
            taxonomy = dict(results_by_category[cache_key])
            specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
            provided = {str(name).strip().casefold(): value for name, value in specifics.items() if str(name).strip() and value not in (None, "", [])}
            missing = [name for name in taxonomy.get("required_aspects", []) if name.casefold() not in provided]
            taxonomy["missing_required_aspects"] = missing
            taxonomy["provider_required_fields_present"] = [name for name in row.get("required_fields", []) if name.casefold() in provided]
            taxonomy["provider_required_fields_missing"] = [name for name in row.get("required_fields", []) if name.casefold() not in provided]
            condition_data = listing.condition_data if isinstance(listing.condition_data, dict) else {}
            taxonomy["current_condition"] = listing.condition
            taxonomy["condition_bucket"] = condition_data.get("condition_bucket")
            taxonomy["condition_validation"] = "not_exposed_by_item_aspects_endpoint; provider condition rejection still requires correction"
            if row.get("classification") in {"CATEGORY_OR_TAXONOMY", "CATEGORY_CONDITION_OR_PARCEL", "REQUIRED_DATA"} and taxonomy.get("status") == "confirmed":
                try:
                    condition_policy = await get_ebay_item_condition_policies(
                        account.access_token, category_id, marketplace_id=marketplace,
                    )
                    taxonomy["condition_policy"] = summarize_ebay_condition_policy(condition_policy, listing)
                    taxonomy["condition_validation"] = "current_provider_values_loaded_operator_review_required"
                except EbayIntegrationError as exc:
                    taxonomy["condition_policy"] = {
                        "status": "unavailable",
                        "error": str(exc)[:300],
                        "compatibility_assessment": "unknown; do not retry based on local condition alone",
                    }
            row["taxonomy_preflight"] = taxonomy
    # A 25001 response leaves the offer write outcome unknown. Reconcile those
    # rows against the authoritative active-offer snapshot by PosterPro's exact
    # deterministic SKU. A missing active match is useful evidence, but does
    # not prove that a draft/inactive offer was never created, so it never
    # enables retry by itself.
    unknown_rows = [row for row in preview["rows"] if row.get("classification") == "REMOTE_OUTCOME_UNKNOWN"]
    preflight_service = MarketplacePreflightService()
    for row in preview["rows"]:
        listing = listing_by_id.get(row.get("listing_id"))
        if listing is None:
            row["local_recovery_preflight"] = {
                "status": "unavailable", "blockers": [],
                "error": "Listing is not available in this operator scope.",
            }
            continue
        local = preflight_service.preflight_listing(db, listing, "ebay")
        row["local_recovery_preflight"] = {
            "status": local.get("status"),
            "blockers": [
                {"code": issue.get("code"), "message": issue.get("message") or issue.get("user_message"), "fix_hint": issue.get("fix_hint")}
                for issue in (local.get("blockers") or []) if isinstance(issue, dict)
            ],
            "warnings": [
                {"code": issue.get("code"), "message": issue.get("message") or issue.get("user_message")}
                for issue in (local.get("warnings") or []) if isinstance(issue, dict)
            ],
            "category_summary": local.get("category_summary"),
            "shipping_summary": local.get("shipping_summary"),
            "quality_summary": local.get("quality_summary"),
        }
    # The deterministic 25002/25005 errors happened after Inventory offers
    # existed. Reconcile their exact remote SKU records and current inventory
    # item before suggesting any repair; the saved local draft may now carry a
    # different category than the offer rejected by eBay. These are GETs only.
    deterministic_rows = [
        row for row in preview["rows"]
        if row.get("classification") in {"CATEGORY_OR_TAXONOMY", "CATEGORY_CONDITION_OR_PARCEL", "REQUIRED_DATA"}
    ]
    if account is not None:
        from app.services.ebay_taxonomy import category_by_id
        checked_at = datetime.now(UTC).isoformat()
        remote_required_by_category = {}
        for row in deterministic_rows:
            listing = listing_by_id.get(row.get("listing_id"))
            if listing is None:
                continue
            sku = _build_ebay_sku(current_user.id, int(row["listing_id"]))
            try:
                offers = await get_ebay_offers_for_sku(account, sku)
                matches = [offer for offer in offers if str(offer.get("sku") or sku).casefold() == sku.casefold()]
                reconciliation = {
                    "status": "multiple_exact_sku_offers" if len(matches) > 1 else "exact_sku_offer_found" if matches else "no_exact_sku_offer_returned",
                    "checked_at": checked_at,
                    "expected_sku": sku,
                    "retry_eligible": False,
                    "offers": [],
                }
                for offer in matches[:5]:
                    offer_id = str(offer.get("offerId") or "").strip()
                    marketplace = str(offer.get("marketplaceId") or "EBAY_US").strip().upper()
                    remote_category_id = str(offer.get("categoryId") or "").strip()
                    offer_listing = offer.get("listing") if isinstance(offer.get("listing"), dict) else {}
                    item = await get_ebay_inventory_item_for_sku(account, sku) if offer_id else {}
                    product = item.get("product") if isinstance(item.get("product"), dict) else {}
                    remote_aspects = product.get("aspects") if isinstance(product.get("aspects"), dict) else {}
                    remote_tree_id = "100" if marketplace == "EBAY_MOTORS_US" else "0"
                    try:
                        remote_category = category_by_id(db, remote_category_id, remote_tree_id, marketplace) if remote_category_id else None
                    except Exception:
                        remote_category = None
                    local_category_id = str(listing.category_id or listing.category_suggestion or "").strip()
                    local_tree_id = str((listing.source_metadata or {}).get("taxonomy_tree_id") or "0") if isinstance(listing.source_metadata, dict) else "0"
                    local_marketplace = str((listing.source_metadata or {}).get("taxonomy_marketplace") or "EBAY_US").upper() if isinstance(listing.source_metadata, dict) else "EBAY_US"
                    try:
                        local_category = category_by_id(db, local_category_id, local_tree_id, local_marketplace) if local_category_id else None
                    except Exception:
                        local_category = None
                    remote_required = []
                    remote_aspects_status = "not_checked"
                    if remote_category_id:
                        if (marketplace, remote_tree_id, remote_category_id) not in remote_required_by_category:
                            try:
                                aspect_data = await get_required_item_specifics(account.access_token, remote_category_id, marketplace_id=marketplace, tree_id=remote_tree_id)
                                remote_required_by_category[(marketplace, remote_tree_id, remote_category_id)] = (
                                    "provider_confirmed",
                                    [
                                        str(aspect.get("localizedAspectName") or aspect.get("aspectName") or "").strip()
                                        for aspect in (aspect_data.get("aspects") or [])
                                        if isinstance(aspect, dict)
                                        and bool((aspect.get("aspectConstraint") or {}).get("aspectRequired"))
                                        and str(aspect.get("localizedAspectName") or aspect.get("aspectName") or "").strip()
                                    ],
                                    None,
                                )
                            except EbayIntegrationError as exc:
                                remote_required_by_category[(marketplace, remote_tree_id, remote_category_id)] = ("unavailable", [], str(exc)[:250])
                        remote_aspects_status, remote_required, metadata_error = remote_required_by_category[(marketplace, remote_tree_id, remote_category_id)]
                        if metadata_error:
                            reconciliation.setdefault("metadata_errors", []).append(metadata_error)
                    remote_aspect_names = {str(name).casefold() for name in remote_aspects}
                    local_aspects = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
                    local_aspect_names = sorted(str(name).strip() for name, value in local_aspects.items() if str(name).strip() and value not in (None, "", []))
                    local_aspect_name_set = {name.casefold() for name in local_aspect_names}
                    reconciliation["offers"].append({
                        "offer_id": offer_id or None,
                        "status": str(offer.get("listingStatus") or offer.get("status") or "UNKNOWN").upper(),
                        "remote_listing_id": str(offer_listing.get("listingId") or offer.get("listingId") or "") or None,
                        "marketplace_id": marketplace,
                        "remote_category_id": remote_category_id or None,
                        "remote_category_path": str((remote_category or {}).get("full_path") or "") or None,
                        "local_category_id": local_category_id or None,
                        "local_category_path": str((local_category or {}).get("full_path") or "") or None,
                        "category_mismatch": bool(remote_category_id and local_category_id and remote_category_id != local_category_id),
                        "remote_title": str(product.get("title") or "") or None,
                        "remote_aspect_names": sorted(str(name) for name in remote_aspects),
                        "local_aspect_names": local_aspect_names,
                        "remote_required_aspects_status": remote_aspects_status,
                        "remote_required_aspects": remote_required,
                        "remote_required_aspects_missing": [name for name in remote_required if name.casefold() not in remote_aspect_names],
                        "remote_required_aspects_missing_locally": [name for name in remote_required if name.casefold() not in local_aspect_name_set],
                        "remote_condition": item.get("condition"),
                        "remote_package_fields_present": sorted((item.get("packageWeightAndSize") or {}).keys()) if isinstance(item.get("packageWeightAndSize"), dict) else [],
                        "remote_package_measurement_provenance": "not available from the Inventory API response; do not treat field presence as operator-measured packed dimensions",
                    })
                reconciliation["meaning"] = "Remote offer and Inventory item are read-only evidence. Category or specifics mismatch is not corrected automatically; retry remains disabled."
                row["deterministic_offer_reconciliation"] = reconciliation
            except EbayIntegrationError as exc:
                exact_offer_not_found = (
                    exc.http_status == 404
                    and any(
                        str(error.get("errorId")) == "25713"
                        for error in ((exc.provider_response or {}).get("errors") or [])
                        if isinstance(error, dict)
                    )
                )
                row["deterministic_offer_reconciliation"] = {
                    "status": "no_exact_sku_offer_returned" if exact_offer_not_found else "unavailable",
                    "checked_at": checked_at,
                    "expected_sku": sku, "retry_eligible": False,
                    "provider_http_status": exc.http_status,
                    "provider_error_ids": [
                        str(error.get("errorId"))
                        for error in ((exc.provider_response or {}).get("errors") or [])
                        if isinstance(error, dict) and error.get("errorId") is not None
                    ],
                    "meaning": "eBay returned 404/25713 for this exact-SKU lookup. No offer record was returned at query time; this does not resolve other remote state or authorize retry." if exact_offer_not_found else None,
                    "error": None if exact_offer_not_found else str(exc)[:400],
                }
    if unknown_rows:
        try:
            if account is None:
                raise EbayIntegrationError("eBay account authorization is unavailable for remote reconciliation")
            # Use the bounded, read-only Trading API snapshot directly. The
            # Inventory catalog can fail account-wide when an old invalid SKU
            # exists, even though Trading API remains able to enumerate active
            # listings. This still cannot rule out draft/inactive offers.
            active_rows = await get_ebay_trading_active_operations(account, limit=1000)
            by_sku = {
                str(((remote.get("source_identifiers") or {}).get("sku") or "")).strip().casefold(): remote
                for remote in active_rows
                if isinstance(remote, dict)
            }
            snapshot_checked_at = datetime.now(UTC).isoformat()
            for row in unknown_rows:
                expected_sku = _build_ebay_sku(current_user.id, int(row["listing_id"]))
                match = by_sku.get(expected_sku.casefold())
                identifiers = (match or {}).get("source_identifiers") if isinstance((match or {}).get("source_identifiers"), dict) else {}
                row["active_offer_reconciliation"] = {
                    "status": "active_exact_sku_match" if match else "no_active_exact_sku_match",
                    "checked_at": snapshot_checked_at,
                    "expected_sku": expected_sku,
                    "remote_listing_id": identifiers.get("ebay_listing_id") if match else None,
                    "retry_eligible": False,
                    "meaning": (
                        "An active remote listing with the exact PosterPro SKU exists; reconcile it locally before any retry."
                        if match else
                        "No active exact-SKU match was found. Inactive/draft provider state is not ruled out; keep retry blocked."
                    ),
                }
                if match:
                    continue
                try:
                    sku_offers = await get_ebay_offers_for_sku(account, expected_sku)
                    offer_rows = [
                        {
                            "offer_id": str(offer.get("offerId") or "") or None,
                            "listing_status": str(offer.get("listingStatus") or offer.get("status") or "UNKNOWN").upper(),
                            "listing_id": str(offer.get("listing") or offer.get("listingId") or "") or None,
                            "marketplace_id": offer.get("marketplaceId"),
                        }
                        for offer in sku_offers
                    ]
                    row["sku_offer_reconciliation"] = {
                        "status": "offers_found" if offer_rows else "no_exact_sku_offer_returned",
                        "checked_at": snapshot_checked_at,
                        "expected_sku": expected_sku,
                        "offers": offer_rows,
                        "retry_eligible": False,
                        "meaning": (
                            "Exact-SKU offer records exist; inspect their remote state and reconcile before any retry."
                            if offer_rows else
                            "No offer was returned for this exact SKU, but provider outcome remains unresolved; retry stays blocked."
                        ),
                    }
                except EbayIntegrationError as exc:
                    not_found = (
                        exc.http_status == 404
                        and any(
                            str(error.get("errorId")) == "25713"
                            for error in ((exc.provider_response or {}).get("errors") or [])
                            if isinstance(error, dict)
                        )
                    )
                    row["sku_offer_reconciliation"] = {
                        "status": "no_exact_sku_offer_returned" if not_found else "unavailable",
                        "checked_at": snapshot_checked_at,
                        "expected_sku": expected_sku,
                        "provider_http_status": exc.http_status,
                        "provider_error_ids": [
                            str(error.get("errorId"))
                            for error in ((exc.provider_response or {}).get("errors") or [])
                            if isinstance(error, dict) and error.get("errorId") is not None
                        ],
                        "retry_eligible": False,
                        "meaning": "eBay returned 404/25713 for the exact-SKU offer query; no offer record was available at query time. This does not authorize retry." if not_found else None,
                        "error": None if not_found else str(exc)[:400],
                    }
                # A missing offer does not mean no remote write occurred: the
                # inventory item upsert can succeed before offer creation or
                # publication fails. Read that exact SKU separately and report
                # it as inventory-only evidence, never as a published listing.
                try:
                    inventory_item = await get_ebay_inventory_item_for_sku(account, expected_sku)
                    product = inventory_item.get("product") if isinstance(inventory_item.get("product"), dict) else {}
                    item_aspects = product.get("aspects") if isinstance(product.get("aspects"), dict) else {}
                    package = inventory_item.get("packageWeightAndSize") if isinstance(inventory_item.get("packageWeightAndSize"), dict) else {}
                    row["inventory_item_reconciliation"] = {
                        "status": "inventory_item_found",
                        "checked_at": snapshot_checked_at,
                        "expected_sku": expected_sku,
                        "remote_title": str(product.get("title") or "") or None,
                        "condition": inventory_item.get("condition"),
                        "aspect_names": sorted(str(name) for name in item_aspects),
                        "package_fields_present": sorted(package.keys()),
                        "package_measurement_provenance": "not supplied by Inventory API; field presence does not establish packed-measurement provenance",
                        "remote_listing_id": None,
                        "meaning": "The exact remote inventory item exists, but this is not an offer or publication confirmation. Preserve the offer/listing outcome as unresolved and do not retry automatically.",
                        "retry_eligible": False,
                    }
                except EbayIntegrationError as exc:
                    item_not_found = exc.http_status == 404
                    row["inventory_item_reconciliation"] = {
                        "status": "no_inventory_item_returned" if item_not_found else "unavailable",
                        "checked_at": snapshot_checked_at,
                        "expected_sku": expected_sku,
                        "provider_http_status": exc.http_status,
                        "provider_error_ids": [
                            str(error.get("errorId"))
                            for error in ((exc.provider_response or {}).get("errors") or [])
                            if isinstance(error, dict) and error.get("errorId") is not None
                        ],
                        "meaning": "No exact-SKU Inventory item was returned at query time; this does not resolve the historical offer/publish outcome." if item_not_found else None,
                        "error": None if item_not_found else str(exc)[:400],
                        "retry_eligible": False,
                    }
        except EbayIntegrationError as exc:
            for row in unknown_rows:
                row["active_offer_reconciliation"] = {
                    "status": "unavailable", "checked_at": datetime.now(UTC).isoformat(),
                    "retry_eligible": False, "error": str(exc)[:400],
                }
    return preview


@router.get("/failed-batches/{batch_id}/listings/{listing_id}/category-candidates")
async def search_failed_ebay_listing_category_candidates(
    batch_id: str,
    listing_id: int,
    q: str = Query(..., min_length=2, max_length=120),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Search current eBay taxonomy for one failed-batch draft; never apply a category."""
    batch = db.get(MarketplacePublishBatch, batch_id)
    if batch is None or batch.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="eBay publish batch not found")
    linked_job = db.execute(
        select(MarketplaceCrosspostJob).where(
            MarketplaceCrosspostJob.batch_id == batch_id,
            MarketplaceCrosspostJob.user_id == current_user.id,
            MarketplaceCrosspostJob.listing_id == listing_id,
        )
    ).scalars().first()
    listing = db.execute(
        select(Listing).where(Listing.id == listing_id, Listing.user_id == current_user.id)
    ).scalars().first()
    if linked_job is None or listing is None:
        raise HTTPException(status_code=404, detail="Listing is not part of this operator's failed batch")

    source = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    marketplace = str(source.get("taxonomy_marketplace") or "").strip().upper()
    tree_id = str(source.get("taxonomy_tree_id") or "").strip()
    if not marketplace or tree_id not in {"0", "100"}:
        from app.services.ebay_taxonomy import category_by_id
        ownership = []
        category_id = str(listing.category_id or "").strip()
        for candidate_marketplace, candidate_tree in (("EBAY_US", "0"), ("EBAY_MOTORS_US", "100")):
            if category_id and category_by_id(db, category_id, candidate_tree, candidate_marketplace):
                ownership.append((candidate_marketplace, candidate_tree))
        if len(ownership) != 1:
            raise HTTPException(
                status_code=409,
                detail="Taxonomy ownership is ambiguous; confirm the listing's marketplace tree before category search.",
            )
        marketplace, tree_id = ownership[0]

    account = await get_or_refresh_account(current_user.id, db)
    try:
        suggestions = await search_ebay_categories(
            q.strip(), account,
            marketplace_id=marketplace,
            taxonomy_tree_id=tree_id,
            verification_limit=5,
        )
        specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
        provided = {str(name).strip().casefold() for name, value in specifics.items() if str(name).strip() and value not in (None, "", [])}
        for suggestion in suggestions:
            if suggestion.get("leaf_verified") is not True or suggestion.get("leaf") is not True:
                suggestion["required_aspects_status"] = "not_checked_nonleaf_or_unverified"
                suggestion["required_aspects"] = []
                suggestion["missing_required_aspects"] = []
                continue
            try:
                aspects = await get_required_item_specifics(
                    account.access_token,
                    str(suggestion["category_id"]),
                    marketplace_id=marketplace,
                    tree_id=tree_id,
                )
                raw_aspects = aspects.get("aspects") if isinstance(aspects, dict) else []
                required = [
                    str(aspect.get("localizedAspectName") or aspect.get("aspectName") or "").strip()
                    for aspect in (raw_aspects or [])
                    if isinstance(aspect, dict)
                    and bool((aspect.get("aspectConstraint") or {}).get("aspectRequired"))
                    and str(aspect.get("localizedAspectName") or aspect.get("aspectName") or "").strip()
                ]
                suggestion["required_aspects_status"] = "confirmed"
                suggestion["required_aspects"] = required
                suggestion["missing_required_aspects"] = [name for name in required if name.casefold() not in provided]
                suggestion["category_tree_version"] = aspects.get("categoryTreeVersion") if isinstance(aspects, dict) else None
                try:
                    condition_policy = await get_ebay_item_condition_policies(
                        account.access_token,
                        str(suggestion["category_id"]),
                        marketplace_id=marketplace,
                    )
                    suggestion["condition_policy"] = {
                        "status": "provider_metadata_confirmed",
                        "condition_required": condition_policy.get("itemConditionRequired"),
                        "supported_conditions": [
                            {
                                "condition_id": value.get("conditionId"),
                                "description": value.get("conditionDescription") or value.get("conditionName"),
                                "help_text": value.get("conditionHelpText"),
                            }
                            for value in (condition_policy.get("itemConditions") or [])
                            if isinstance(value, dict)
                        ],
                        "compatibility_assessment": "operator_review_required; no PosterPro condition enum mapping is guessed",
                    }
                except EbayIntegrationError as exc:
                    suggestion["condition_policy"] = {"status": "unavailable", "error": str(exc)[:300]}
            except EbayIntegrationError as exc:
                suggestion["required_aspects_status"] = "unavailable"
                suggestion["required_aspects"] = []
                suggestion["missing_required_aspects"] = []
                suggestion["aspect_error"] = str(exc)[:300]
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    from app.services.ebay_taxonomy import category_by_id
    try:
        stored_category = category_by_id(
            db, str(listing.category_id or ""), tree_id, marketplace,
        ) if str(listing.category_id or "").strip() else None
    except Exception:
        # Taxonomy path is useful context but not required for the provider
        # search; migrations/caches may be unavailable in isolated installs.
        stored_category = None
    condition_data = listing.condition_data if isinstance(listing.condition_data, dict) else {}
    return {
        "operation": "read_only_ebay_taxonomy_category_candidates",
        "batch_id": batch_id,
        "listing_id": listing_id,
        "current_category_id": str(listing.category_id or "") or None,
        "current_category": ({
            "category_id": str(stored_category["category_id"]),
            "category_name": str(stored_category["category_name"]),
            "full_path": str(stored_category["full_path"] or ""),
            "leaf": bool(stored_category["leaf"]),
            "source": "local_synchronized_taxonomy",
        } if stored_category else None),
        "current_condition": {
            "listing_condition": str(listing.condition or "").strip() or None,
            "condition_bucket": str(condition_data.get("condition_bucket") or "").strip() or None,
            "ebay_condition_override": str(condition_data.get("ebay_condition_override") or "").strip() or None,
        },
        "current_item_specifics": listing.item_specifics if isinstance(listing.item_specifics, dict) else {},
        "query": q.strip(),
        "marketplace": marketplace,
        "taxonomy_tree_id": tree_id,
        "source": "ebay_taxonomy_live_suggestions",
        "suggestions": suggestions,
        "selection_required": True,
        "application_available": False,
        "message": "Candidates are current taxonomy suggestions, not automatic corrections. Review product fit and required aspects; this endpoint does not change the draft or any live listing.",
    }


def _taxonomy_cache_row(db: Session, cache_key: str):
    return db.execute(
        select(MarketplaceMetadataCache)
        .where(MarketplaceMetadataCache.marketplace == "ebay", MarketplaceMetadataCache.cache_key == cache_key)
        .order_by(MarketplaceMetadataCache.updated_at.desc(), MarketplaceMetadataCache.id.desc())
    ).scalars().first()


def _cached_taxonomy_tree_id(db: Session) -> str | None:
    full_tree = _taxonomy_cache_row(db, "ebay:EBAY_US:category_tree")
    if full_tree and isinstance(full_tree.payload, dict):
        tree_id = full_tree.payload.get("categoryTreeId") or full_tree.payload.get("treeId")
        if tree_id:
            return str(tree_id)
    rows = db.execute(
        select(MarketplaceMetadataCache)
        .where(MarketplaceMetadataCache.marketplace == "ebay", MarketplaceMetadataCache.cache_key.like("ebay:EBAY_US:browse:%"))
        .order_by(MarketplaceMetadataCache.updated_at.desc(), MarketplaceMetadataCache.id.desc())
    ).scalars().all()
    for row in rows:
        tree_id = (row.payload or {}).get("tree_id") if isinstance(row.payload, dict) else None
        if tree_id:
            return str(tree_id)
    return None


def _taxonomy_node_rows(tree: dict, parent_category_id: str | None = None):
    root = tree.get("rootCategoryNode") or tree
    target = root
    trail: list[str] = []
    if parent_category_id:
        found = None
        def locate(node, path):
            nonlocal found
            category = node.get("category") or node
            cid = str(category.get("categoryId") or "")
            name = str(category.get("categoryName") or "")
            next_path = [*path, name] if name else path
            if cid == str(parent_category_id):
                found = (node, next_path)
                return
            for child in node.get("childCategoryTreeNodes") or []:
                locate(child, next_path)
        locate(root, [])
        if not found:
            return []
        target, trail = found
    tree_id = str(tree.get("categoryTreeId") or tree.get("treeId") or "")
    rows = []
    for child in target.get("childCategoryTreeNodes") or []:
        category = child.get("category") or child
        cid = str(category.get("categoryId") or "").strip()
        name = str(category.get("categoryName") or cid).strip()
        if cid:
            rows.append({"category_id": cid, "category_name": name, "has_children": bool(child.get("childCategoryTreeNodes")), "leaf": not bool(child.get("childCategoryTreeNodes")), "publishable": not bool(child.get("childCategoryTreeNodes")), "leaf_verified": not bool(child.get("childCategoryTreeNodes")), "taxonomy_tree_id": tree_id, "breadcrumb": " › ".join([*trail, name])})
    return rows


def _search_local_taxonomy(tree: dict, query: str):
    needle = query.strip().casefold()
    root = tree.get("rootCategoryNode") or tree
    results = []
    def walk(node, path):
        category = node.get("category") or node
        cid = str(category.get("categoryId") or "").strip()
        name = str(category.get("categoryName") or cid).strip()
        next_path = [*path, name] if name else path
        if cid and needle in name.casefold():
            leaf = not bool(node.get("childCategoryTreeNodes"))
            results.append({"category_id": cid, "category_name": name, "breadcrumb": " › ".join(next_path), "leaf": leaf, "publishable": leaf, "leaf_verified": leaf, "taxonomy_tree_id": str(tree.get("categoryTreeId") or tree.get("treeId") or "")})
        for child in node.get("childCategoryTreeNodes") or []:
            walk(child, next_path)
    walk(root, [])
    return sorted(results, key=lambda row: (not row["leaf"], row["category_name"].casefold()))[:5]


def _is_taxonomy_rate_limit(error: Exception) -> bool:
    text = str(error).lower()
    return "429" in text or "too many requests" in text or "request limit has been reached" in text


def _serve_stale_taxonomy(cache_row: MarketplaceMetadataCache | None, now: datetime):
    if cache_row and isinstance(cache_row.payload, dict):
        age = str(now - cache_row.updated_at) if cache_row.updated_at else None
        return {**cache_row.payload, "source": "local_taxonomy_cache_stale", "stale": True, "cache_age": age}
    return None

@router.get("/ebay/taxonomy/suggestions")
async def ebay_taxonomy_suggestions(q: str = Query(..., min_length=2), tree_ids: str | None = Query(None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # The normalized local trees are authoritative.  Search both the regular
    # and Motors domains without requiring an account connection.
    wanted = {item.strip() for item in (tree_ids or "0,100").split(",") if item.strip() in {"0", "100"}} or {"0", "100"}
    needle = f"%{q.strip().casefold()}%"
    rows = db.execute(text("""
        select category_id, category_name, parent_category_id, leaf, full_path,
               depth, category_tree_version, marketplace, category_tree_id
        from ebay_taxonomy_categories
        where category_tree_id = any(:trees)
          and (lower(category_name) like :needle or lower(full_path) like :needle)
        order by leaf desc, depth desc, category_name asc
        limit 50
    """), {"trees": list(wanted), "needle": needle}).mappings().all()
    tree_count = db.execute(text("select count(*) from ebay_taxonomy_categories where category_tree_id in ('0','100')")).scalar_one()
    if tree_count:
        return {"results": [{**dict(row), "publishable": bool(row["leaf"]), "leaf_verified": bool(row["leaf"]), "breadcrumb": row["full_path"], "domain": row["marketplace"]} for row in rows], "source": "local_normalized_tree", "trees": sorted(wanted)}
    account = db.execute(select(MarketplaceAccount).where(MarketplaceAccount.user_id == current_user.id, MarketplaceAccount.marketplace == MarketplaceName.ebay)).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=409, detail="Connect eBay before searching taxonomy")
    full_tree = _taxonomy_cache_row(db, "ebay:EBAY_US:category_tree")
    if full_tree and isinstance(full_tree.payload, dict):
        return {"results": _search_local_taxonomy(full_tree.payload, q), "taxonomy_tree_id": _cached_taxonomy_tree_id(db), "source": "local_taxonomy_tree"}
    cache_key = f"ebay:EBAY_US:suggestions:{hashlib.sha256(q.strip().lower().encode()).hexdigest()[:32]}"
    cached = _taxonomy_cache_row(db, cache_key)
    if cached and isinstance(cached.payload, dict) and cached.expires_at and cached.expires_at > datetime.utcnow():
        return {**cached.payload, "source": "local_taxonomy_cache", "cache_age": str(datetime.utcnow() - cached.updated_at) if cached.updated_at else None}
    now = datetime.utcnow()
    cooldown = _taxonomy_cache_row(db, "ebay:EBAY_US:taxonomy_rate_limit")
    if cooldown and cooldown.expires_at and cooldown.expires_at > now:
        stale = _serve_stale_taxonomy(cached, now)
        if stale:
            return stale
        return {"results": [], "source": "rate_limited", "stale": True, "message": "eBay taxonomy is temporarily rate-limited; retry is suppressed for 15 minutes."}
    try:
        results = await search_ebay_categories(q, account, taxonomy_tree_id=_cached_taxonomy_tree_id(db))
    except EbayIntegrationError as exc:
        if _is_taxonomy_rate_limit(exc):
            sentinel = cooldown or MarketplaceMetadataCache(marketplace="ebay", cache_key="ebay:EBAY_US:taxonomy_rate_limit")
            sentinel.payload = {"rate_limited": True, "message": str(exc)}
            sentinel.source_version = "ebay_taxonomy_rate_limit"
            sentinel.expires_at = now + timedelta(minutes=15)
            db.add(sentinel); db.commit()
            stale = _serve_stale_taxonomy(cached, now)
            if stale:
                return stale
            return {"results": [], "source": "rate_limited", "stale": True, "message": "eBay taxonomy is temporarily rate-limited; retry is suppressed for 15 minutes."}
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    payload = {"results": results, "taxonomy_tree_id": (results[0].get("taxonomy_tree_id") if results else None)}
    row = cached or MarketplaceMetadataCache(marketplace="ebay", cache_key=cache_key)
    row.payload = payload
    row.source_version = "ebay_taxonomy_v1"
    row.expires_at = datetime.utcnow() + timedelta(days=30)
    db.add(row); db.commit()
    return {**payload, "source": "ebay_taxonomy_live_sync"}

@router.get("/ebay/taxonomy/browse")
async def ebay_taxonomy_browse(parent_category_id: str | None = Query(None), tree_id: str = Query("0"), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if str(tree_id) in {"0", "100"}:
        marketplace = "EBAY_MOTORS_US" if str(tree_id) == "100" else "EBAY_US"
        parent = str(parent_category_id or ("0" if str(tree_id) == "100" else "0"))
        rows = db.execute(text("""
            select category_id, category_name, parent_category_id, leaf,
                   full_path, depth, category_tree_version, marketplace, category_tree_id
            from ebay_taxonomy_categories
            where marketplace=:marketplace and category_tree_id=:tree and parent_category_id=:parent
            order by category_name asc
        """), {"marketplace": marketplace, "tree": str(tree_id), "parent": parent}).mappings().all()
        tree_exists = db.execute(text("select count(*) from ebay_taxonomy_categories where marketplace=:marketplace and category_tree_id=:tree"), {"marketplace": marketplace, "tree": str(tree_id)}).scalar_one()
        if tree_exists:
            return {"tree_id": str(tree_id), "marketplace": marketplace, "parent_category_id": parent_category_id, "categories": [{**dict(row), "has_children": not bool(row["leaf"]), "publishable": bool(row["leaf"]), "leaf_verified": bool(row["leaf"]), "breadcrumb": row["full_path"], "domain": marketplace} for row in rows], "source": "local_normalized_tree"}
    account = db.execute(select(MarketplaceAccount).where(MarketplaceAccount.user_id == current_user.id, MarketplaceAccount.marketplace == MarketplaceName.ebay)).scalar_one_or_none()
    if not account: raise HTTPException(status_code=409, detail="Connect eBay before browsing taxonomy")
    full_tree = _taxonomy_cache_row(db, "ebay:EBAY_US:category_tree")
    if full_tree and isinstance(full_tree.payload, dict):
        return {"tree_id": _cached_taxonomy_tree_id(db), "parent_category_id": parent_category_id, "categories": _taxonomy_node_rows(full_tree.payload, parent_category_id), "source": "local_taxonomy_tree", "cache_age": str(datetime.utcnow() - full_tree.updated_at) if full_tree.updated_at else None}
    cache_key = f"ebay:EBAY_US:browse:{str(parent_category_id or 'root').strip()}"
    cached = _taxonomy_cache_row(db, cache_key)
    if cached and isinstance(cached.payload, dict) and cached.expires_at and cached.expires_at > datetime.utcnow():
        return {**cached.payload, "source": "local_taxonomy_cache", "cache_age": str(datetime.utcnow() - cached.updated_at) if cached.updated_at else None}
    now = datetime.utcnow()
    cooldown = _taxonomy_cache_row(db, "ebay:EBAY_US:taxonomy_rate_limit")
    if cooldown and cooldown.expires_at and cooldown.expires_at > now:
        stale = _serve_stale_taxonomy(cached, now)
        if stale:
            return stale
        return {"categories": [], "source": "rate_limited", "stale": True, "message": "eBay taxonomy is temporarily rate-limited; retry is suppressed for 15 minutes."}
    try:
        result = await browse_ebay_categories(account, parent_category_id, taxonomy_tree_id=_cached_taxonomy_tree_id(db))
        tree_payload = result.pop("_tree_payload", None)
        if isinstance(tree_payload, dict):
            tree_cache = _taxonomy_cache_row(db, "ebay:EBAY_US:category_tree") or MarketplaceMetadataCache(marketplace="ebay", cache_key="ebay:EBAY_US:category_tree")
            tree_cache.payload = tree_payload
            tree_cache.source_version = f"ebay_taxonomy_tree_{result.get('tree_id') or 'unknown'}"
            tree_cache.expires_at = datetime.utcnow() + timedelta(days=30)
            db.add(tree_cache)
        row = cached or MarketplaceMetadataCache(marketplace="ebay", cache_key=cache_key)
        row.payload = result
        row.source_version = f"ebay_taxonomy_tree_{result.get('tree_id') or 'unknown'}"
        row.expires_at = datetime.utcnow() + timedelta(days=30)
        db.add(row); db.commit()
        return {**result, "source": "ebay_taxonomy_live_sync"}
    except EbayIntegrationError as exc:
        if _is_taxonomy_rate_limit(exc):
            sentinel = cooldown or MarketplaceMetadataCache(marketplace="ebay", cache_key="ebay:EBAY_US:taxonomy_rate_limit")
            sentinel.payload = {"rate_limited": True, "message": str(exc)}
            sentinel.source_version = "ebay_taxonomy_rate_limit"
            sentinel.expires_at = now + timedelta(minutes=15)
            db.add(sentinel); db.commit()
            stale = _serve_stale_taxonomy(cached, now)
            if stale:
                return stale
            return {"categories": [], "source": "rate_limited", "stale": True, "message": "eBay taxonomy is temporarily rate-limited; retry is suppressed for 15 minutes."}
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/ebay/taxonomy/aspects")
async def ebay_taxonomy_aspects(
    category_id: str = Query(..., min_length=1),
    marketplace: str = Query("EBAY_US"),
    tree_id: str = Query("0"),
    tree_version: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return cached aspect metadata for the exact selected taxonomy leaf."""
    row = db.execute(text("""
        select category_name, full_path, leaf, category_tree_version, marketplace, category_tree_id
        from ebay_taxonomy_categories
        where category_id=:category_id and marketplace=:marketplace and category_tree_id=:tree
        limit 1
    """), {"category_id": str(category_id), "marketplace": marketplace.upper(), "tree": str(tree_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Category is not present in the synchronized local taxonomy")
    cache_keys = [
        f"ebay:{marketplace.upper()}:{category_id}:aspects",
        f"ebay:{marketplace.lower()}:{category_id}:aspects",
        f"ebay:EBAY_US:{category_id}:aspects" if marketplace.upper() == "EBAY_US" else "",
    ]
    cached = None
    for key in cache_keys:
        if not key:
            continue
        cached = _taxonomy_cache_row(db, key)
        if cached and isinstance(cached.payload, dict):
            break
    payload = cached.payload if cached and isinstance(cached.payload, dict) else {}
    raw = payload.get("aspects") if isinstance(payload, dict) else []
    normalized = []
    for aspect in raw if isinstance(raw, list) else []:
        if not isinstance(aspect, dict):
            continue
        constraint = aspect.get("aspectConstraint") or {}
        values = aspect.get("aspectValues") or []
        normalized.append({
            "name": aspect.get("localizedAspectName") or aspect.get("aspectName"),
            "required": bool(constraint.get("aspectRequired")),
            "enabled_for_variations": bool(constraint.get("aspectEnabledForVariations")),
            "mode": "select" if values else "text",
            "allowed_values": [v.get("localizedValue") or v.get("value") for v in values if isinstance(v, dict)],
        })
    return {"category": dict(row), "required": [item for item in normalized if item["required"]], "recommended": [item for item in normalized if not item["required"]], "source": "local_aspect_cache" if cached else "unavailable", "stale": bool(cached and cached.expires_at and cached.expires_at < datetime.utcnow())}


@router.get("/ebay/auth/url")
async def ebay_auth_url(
    user_id: int | None = Query(None),
    redirect_uri: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    runtime_settings = reload_settings()
    callback = redirect_uri or runtime_settings.ebay_runame or runtime_settings.ebay_redirect_uri
    if not callback:
        raise HTTPException(status_code=400, detail="eBay RuName is required")
    try:
        url = await authenticate_user_ebay(user_id=resolve_user_scope(current_user, user_id), redirect_uri=callback)
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"auth_url": url}


@router.get("/ebay/callback")
async def ebay_callback(
    code: str = Query(...),
    state: str = Query(...),
    redirect_uri: str | None = Query(None),
    db: Session = Depends(get_db),
):
    runtime_settings = reload_settings()
    callback = redirect_uri or runtime_settings.ebay_runame or runtime_settings.ebay_redirect_uri
    if not callback:
        raise HTTPException(status_code=400, detail="eBay RuName is required")

    try:
        user_id = parse_oauth_state(state)
        token_bundle = await exchange_code_for_tokens(code, callback)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"OAuth callback failed: {exc}") from exc

    account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == user_id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    if not account:
        account = MarketplaceAccount(
            user_id=user_id,
            marketplace=MarketplaceName.ebay,
            external_account_id=f"ebay-user-{user_id}",
            access_token=token_bundle.access_token,
            refresh_token=token_bundle.refresh_token,
            token_expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=token_bundle.expires_in),
        )
    else:
        account.access_token = token_bundle.access_token
        account.refresh_token = token_bundle.refresh_token
        account.token_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=token_bundle.expires_in)
    # A successful OAuth callback only proves the code exchange. Verify the
    # actual Sell API before showing the operator a connected state.
    try:
        await _list_business_policies_for_account(user_id, db, account, marketplace_id="EBAY_US")
    except EbayIntegrationError as exc:
        # OAuth can be valid even when the seller account is not enrolled in
        # eBay Business Policies (error 20403). Do not mislabel that account
        # as requiring reauthorization; preserve the provider capability
        # blocker for policy setup instead.
        message = str(exc)
        account.connection_status = "connected" if "20403" in message or "not eligible for business policy" in message.lower() else "reauthorization_required"
        account.last_error = f"eBay policy capability check: {message}"
        db.add(account)
        db.commit()
        return {
            "connected": account.connection_status == "connected",
            "user_id": user_id,
            "marketplace": "ebay",
            "verification_error": message,
        }

    account.connection_status = "connected"
    account.last_error = None
    account.last_successful_check_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(account)
    db.commit()
    return {"connected": True, "user_id": user_id, "marketplace": "ebay"}


@router.put("/ebay/account/manual")
async def save_ebay_tokens_manually(
    payload: EbayManualConnectRequest,
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    access_token = (payload.access_token or "").strip()
    refresh_token = (payload.refresh_token or "").strip() or None
    if not access_token:
        raise HTTPException(status_code=400, detail="An access token is required for manual import")

    expires_in = payload.expires_in_seconds or 7200
    account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == scoped_user_id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    if not account:
        account = MarketplaceAccount(
            user_id=scoped_user_id,
            marketplace=MarketplaceName.ebay,
            external_account_id=(payload.external_account_id or "").strip() or f"ebay-user-{scoped_user_id}",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=expires_in),
        )
    else:
        account.external_account_id = (payload.external_account_id or "").strip() or account.external_account_id
        account.access_token = access_token
        account.refresh_token = refresh_token or account.refresh_token
        account.token_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=expires_in)
    account.connection_status = "connected"
    account.last_error = None

    db.add(account)
    db.commit()
    return {
        "connected": True,
        "user_id": scoped_user_id,
        "marketplace": "ebay",
        "manual_import": True,
        "token_expires_at": account.token_expires_at.isoformat() if account.token_expires_at else None,
    }


@router.post("/sync")
async def sync_ebay_inventory(
    user_id: int | None = Query(None),
    # Active inventory reconciliation is paginated internally by the eBay
    # client; allow the normal full-account sync instead of truncating it at
    # the first 100/250 listings.
    limit: int = Query(1000, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    try:
        return await sync_ebay_active_listings(scoped_user_id, db, limit=limit)
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/operations/shipping-handling-preview")
async def preview_ebay_shipping_and_handling_operations(
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Read active remote eBay offers and build a non-mutating operator preview."""
    scoped_user_id = resolve_user_scope(current_user, user_id)
    account = await get_or_refresh_account(scoped_user_id, db)
    try:
        remote_rows = await get_ebay_trading_active_operations(account, limit=1000)
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target_user = db.get(User, scoped_user_id)
    settings_json = target_user.settings_json if target_user and isinstance(target_user.settings_json, dict) else {}
    workflow = settings_json.get("workflow_preferences") if isinstance(settings_json.get("workflow_preferences"), dict) else {}
    try:
        supported_handling = await get_ebay_supported_handling_times(account)
        handling_values_error = None
    except EbayIntegrationError as exc:
        supported_handling = None
        handling_values_error = str(exc)[:500]
    try:
        policy_catalog = await list_business_policies(account.access_token)
        policy_error = None
    except EbayIntegrationError as exc:
        policy_catalog = {"fulfillment_policies": []}
        policy_error = str(exc)[:500]
    policies_by_id = {
        str(policy.get("id") or "").strip(): policy
        for policy in policy_catalog.get("fulfillment_policies") or []
        if isinstance(policy, dict) and str(policy.get("id") or "").strip()
    }
    policy_ids = {}
    for row in remote_rows:
        offer = row.get("raw_offer") if isinstance(row.get("raw_offer"), dict) else {}
        listing_policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
        policy_id = str(listing_policies.get("fulfillmentPolicyId") or "").strip()
        if policy_id:
            policy_ids[policy_id] = policy_ids.get(policy_id, 0) + 1

    # Handling time applies to every active listing, not just the under-$25
    # shipping candidates. Resolve a shared policy once where Inventory offers
    # expose its ID; use exact read-only GetItem for legacy listings that have
    # no usable business-policy reference. Provider rate limiting remains in
    # the eBay client and each failure is visible in the preview.
    for row in remote_rows:
        offer = row.get("raw_offer") if isinstance(row.get("raw_offer"), dict) else {}
        listing_policies = offer.get("listingPolicies") if isinstance(offer.get("listingPolicies"), dict) else {}
        policy_id = str(listing_policies.get("fulfillmentPolicyId") or "").strip()
        if policy_id and policy_id in policies_by_id:
            policy = policies_by_id[policy_id]
            handling = policy.get("handling_time") if isinstance(policy.get("handling_time"), dict) else {}
            value = handling.get("value")
            unit = str(handling.get("unit") or "").upper()
            if unit == "DAY" and value is not None:
                try:
                    row["handling_time_days"] = int(value)
                except (TypeError, ValueError):
                    row["handling_time_days"] = None
            # A fulfillment policy can define separate domestic and
            # international shipping options. Only the domestic option
            # describes the US buyer-facing policy used by this preview; a
            # flattened list can accidentally report an international carrier
            # as the domestic service when provider ordering changes.
            shipping_options = [option for option in policy.get("shipping_options", []) if isinstance(option, dict)]
            domestic_option = next((
                option for option in shipping_options
                if str(option.get("option_type") or "").strip().upper() == "DOMESTIC"
            ), None)
            services = domestic_option.get("shipping_services") if domestic_option and isinstance(domestic_option.get("shipping_services"), list) else []
            primary_service = next((service for service in services if isinstance(service, dict)), {})
            row["shipping"] = {
                "shipping_type": domestic_option.get("cost_type") if domestic_option else None,
                "service": primary_service.get("service"),
                "carrier": primary_service.get("carrier"),
                "free_shipping": primary_service.get("free_shipping"),
                "buyer_pays_shipping": primary_service.get("buyer_responsible"),
                "available_services": [
                    {
                        "carrier": service.get("carrier"),
                        "service": service.get("service"),
                        "free_shipping": service.get("free_shipping"),
                        "buyer_responsible": service.get("buyer_responsible"),
                    }
                    for service in services if isinstance(service, dict)
                ],
                "calculated": bool(domestic_option and str(domestic_option.get("cost_type") or "").upper() == "CALCULATED"),
                "provenance": "ebay_fulfillment_policy_read",
            }
            row["operation_inspection"] = {
                "status": "confirmed",
                "method": "ebay_fulfillment_policy_read",
                "handling_source": "ebay_fulfillment_policy_read",
                "inspected_at": datetime.now(UTC).isoformat(),
                "fulfillment_policy_id": policy_id,
                "fulfillment_policy_name": policy.get("name"),
                "shared_policy_listing_count": policy_ids.get(policy_id, 0),
                "shared_policy_impact": "shared_policy" if policy_ids.get(policy_id, 0) > 1 else "single_active_listing_in_snapshot",
            }
            continue

        if str(offer.get("source") or "") == "ebay_trading_api":
            # GetMyeBaySelling already returned the exact active listing's
            # dispatch and shipping fields in its paginated response. Do not
            # fan out an unnecessary GetItem call per listing.
            inspection = row.get("operation_inspection") if isinstance(row.get("operation_inspection"), dict) else {}
            if inspection.get("fulfillment_policy_id"):
                inspection.setdefault("shared_policy_impact", "policy_reference_pending_snapshot_count")
            row["operation_inspection"] = inspection or {
                "status": "unavailable",
                "method": "ebay_trading_get_my_ebay_selling",
                "inspected_at": datetime.now(UTC).isoformat(),
                "error": "The batch response did not include dispatch-time or policy details.",
            }
            continue

        remote_id = str((row.get("source_identifiers") or {}).get("ebay_listing_id") or "").strip()
        if not remote_id:
            row["operation_inspection"] = {
                "status": "unavailable",
                "method": "ebay_fulfillment_policy_read" if policy_error is None else "ebay_fulfillment_policy_and_trading_read",
                "inspected_at": datetime.now(UTC).isoformat(),
                "error": policy_error or "Remote listing has no fulfillment-policy ID or eBay item ID for exact inspection.",
            }
            continue
        try:
            detail = await get_ebay_trading_item(account, remote_id)
        except EbayIntegrationError as exc:
            row["operation_inspection"] = {
                "status": "unavailable",
                "method": "ebay_trading_get_item",
                "inspected_at": datetime.now(UTC).isoformat(),
                "error": str(exc)[:500],
            }
            continue
        if detail.get("ack", "").lower() in {"success", "warning"}:
            row["handling_time_days"] = detail.get("handling_time_days")
            row["shipping"] = detail.get("shipping")
            row["operation_inspection"] = {
                "status": "confirmed",
                "method": "ebay_trading_get_item",
                "handling_source": "ebay_trading_get_item",
                "inspected_at": datetime.now(UTC).isoformat(),
                "fulfillment_policy_id": detail.get("shipping_policy_id"),
                "fulfillment_policy_name": detail.get("shipping_policy_name"),
                "shared_policy_impact": "policy_reference_pending_snapshot_count" if detail.get("shipping_policy_id") else "no_business_policy_reference_returned_by_exact_get_item",
            }
        else:
            row["operation_inspection"] = {
                "status": "unavailable",
                "method": "ebay_trading_get_item",
                "inspected_at": datetime.now(UTC).isoformat(),
                "error": str(detail.get("errors") or detail.get("ack") or "eBay did not return an active listing")[:500],
            }
    policy_listing_counts = {}
    for row in remote_rows:
        inspection = row.get("operation_inspection") if isinstance(row.get("operation_inspection"), dict) else {}
        policy_id = str(inspection.get("fulfillment_policy_id") or "").strip()
        if policy_id:
            policy_listing_counts[policy_id] = policy_listing_counts.get(policy_id, 0) + 1
    for row in remote_rows:
        inspection = row.get("operation_inspection") if isinstance(row.get("operation_inspection"), dict) else {}
        policy_id = str(inspection.get("fulfillment_policy_id") or "").strip()
        if policy_id:
            count = policy_listing_counts.get(policy_id, 0)
            inspection["shared_policy_listing_count"] = count
            inspection["shared_policy_impact"] = "shared_policy" if count > 1 else "single_active_listing_in_snapshot"
    for row in remote_rows:
        row["marketplace_handling_values"] = supported_handling.get("values_days") if supported_handling else None
        row["handling_values_source"] = supported_handling.get("source") if supported_handling else None
        row["handling_values_updated_at"] = supported_handling.get("updated_at") if supported_handling else None
        row["handling_values_error"] = handling_values_error
    preview = build_shipping_handling_preview(db, user_id=scoped_user_id, remote_rows=remote_rows, policy=workflow)
    preview["handling_capability"] = supported_handling or {"status": "unavailable", "error": handling_values_error}
    return preview


@router.post("/sync/history")
async def sync_ebay_history(
    user_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=250),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    try:
        return await sync_ebay_fulfillment_history(scoped_user_id, db, limit=limit)
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/listings/{listing_id}/publish/ebay")
async def publish_listing_ebay(
    listing_id: int,
    payload: EbayPublishConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    if not listing.title or not listing.description:
        raise HTTPException(status_code=400, detail="Listing must be generated before publishing")
    if not payload or not payload.confirm_live_publish or str(payload.confirmation_phrase or "").strip() != "QUEUE LIVE EBAY READY LISTINGS":
        raise HTTPException(
            status_code=400,
            detail="Live eBay publish requires explicit confirmation. Use the phrase 'QUEUE LIVE EBAY READY LISTINGS' to proceed.",
        )
    pricing = ((listing.marketplace_data or {}).get("pricing_analysis") or {}) if isinstance(listing.marketplace_data, dict) else {}
    blockers = validate_marketplace_readiness(listing=listing, marketplace="ebay", pricing_analysis=pricing)
    if blockers:
        raise HTTPException(status_code=400, detail="; ".join(blockers))

    try:
        return await publish_listing_to_ebay(listing, db)
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/listings/{listing_id}/sync")
async def sync_listing_to_ebay(
    listing_id: int,
    payload: EbayPublishConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    if not listing.ebay_listing_id:
        raise HTTPException(status_code=400, detail="Listing has not been published to eBay yet")
    if not payload or not payload.confirm_live_publish or str(payload.confirmation_phrase or "").strip() != "QUEUE LIVE EBAY READY LISTINGS":
        raise HTTPException(
            status_code=400,
            detail="Live eBay update requires explicit confirmation. Use the phrase 'QUEUE LIVE EBAY READY LISTINGS' to proceed.",
        )
    try:
        return await revise_ebay_listing(listing, db)
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ebay/status/{listing_id}")
async def ebay_listing_status(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)

    return {
        "id": listing.id,
        "ebay_listing_id": listing.ebay_listing_id,
        "status": listing.ebay_publish_status,
        "marketplace_data": listing.marketplace_data,
    }


@router.get("/ebay/offers/dashboard")
async def ebay_offer_dashboard(
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    try:
        account = await get_or_refresh_account(scoped_user_id, db)
    except EbayIntegrationError:
        return {
            "connected": False,
            "active_offers": [],
            "decision_log": [],
        }
    try:
        active_offers = await get_incoming_best_offers(account, limit=50)
        offer_error = None
    except EbayIntegrationError as exc:
        # Do not block authenticated workspace load when eBay offers are unavailable.
        active_offers = []
        offer_error = str(exc)
    decisions = db.execute(
        select(EbayOfferHistory)
        .where(EbayOfferHistory.user_id == scoped_user_id)
        .order_by(EbayOfferHistory.created_at.desc())
        .limit(100)
    ).scalars().all()
    return {
        "connected": True,
        "active_offers": active_offers,
        "offer_error": offer_error,
        "decision_log": [
            {
                "id": row.id,
                "listing_id": row.listing_id,
                "ebay_offer_id": row.ebay_offer_id,
                "ebay_listing_id": row.ebay_listing_id,
                "offered_amount": row.offered_amount,
                "currency": row.currency,
                "offer_status": row.offer_status,
                "decision": row.decision,
                "decision_reason": row.decision_reason,
                "decided_at": row.decided_at.isoformat() if row.decided_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in decisions
        ],
    }
