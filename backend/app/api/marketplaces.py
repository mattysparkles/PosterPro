from __future__ import annotations

import csv
import io
import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.schemas import (
    BulkMarketplacePreflightRequest,
    BulkMarketplacePreflightResponse,
    BulkMarketplacePublishReadyRequest,
    BulkMarketplacePublishReadyResponse,
    EbayAccountReadinessResponse,
    EbayMerchantLocationRequest,
    EbayPolicyCatalogResponse,
    EbayPolicySelectRequest,
    EbayPolicySyncRequest,
    ActiveBridgeConnectSessionSummaryResponse,
    AccountSetupSummaryResponse,
    BulkMarketplacePublishRequest,
    BulkMarketplacePublishResponse,
    EbayPublishConfirmationRequest,
    LaunchCandidateRequest,
    LaunchCandidateResponse,
    LaunchDrillRequest,
    LaunchDrillResponse,
    EbayLaunchRepairActionRequest,
    EbayLaunchRepairQueueResponse,
    ConnectMarketplaceResponse,
    MarketplaceConnectionUpdateRequest,
    MarketplaceConnectionStatusResponse,
    MarketplacePublishRequest,
    MarketplacePreflightResponse,
    ServerReadinessResponse,
    SoldSyncRequest,
    UserResponse,
)
from app.core.auth import ensure_user_owns_resource, get_current_user, get_user_role, is_effective_admin, is_viewing_as_regular, resolve_user_scope, user_has_vine_access
from app.core.config import reload_settings, settings
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import Listing, ListingTemplate, MarketplaceAccount, User
from app.connectors.registry import MARKETPLACE_REGISTRY
from app.services.marketplace_setup import (
    MANUAL_MARKETPLACES,
    marketplace_status_snapshot,
    save_manual_marketplace_settings,
)
from app.services.multi_platform_publisher import get_enabled_platforms
from app.services.automation_bridge import AutomationBridgeError, get_active_bridge_connect_session
from app.services.marketplace_orchestrator import (
    list_marketplaces,
    bulk_publish_ready,
    listing_marketplace_status,
    queue_publish,
    trigger_sync_sold,
)
from app.services.marketplace_preflight import MarketplacePreflightService
from app.services.ebay_service import (
    EbayIntegrationError,
    get_or_refresh_account,
    _list_business_policies_for_account,
    list_business_policies,
    revise_ebay_listing,
    sync_ebay_fulfillment_history,
    sync_business_policies,
    sync_ebay_active_listings,
    summarize_ebay_account_health,
    verify_merchant_location,
)

router = APIRouter()
LIVE_EBAY_CONFIRMATION_PHRASE = "QUEUE LIVE EBAY READY LISTINGS"


def _require_live_ebay_confirmation(marketplaces: list[str] | None, *, confirm_live_publish: bool, confirmation_phrase: str | None) -> None:
    markets = [str(market or "").strip().lower() for market in (marketplaces or []) if str(market or "").strip()]
    if "ebay" not in markets:
        return
    if not confirm_live_publish or str(confirmation_phrase or "").strip() != LIVE_EBAY_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f"Live eBay queueing requires explicit confirmation. Use the confirmation phrase '{LIVE_EBAY_CONFIRMATION_PHRASE}'.",
        )


def _build_marketplace_connections(*, user: User, accounts: list[MarketplaceAccount]) -> list[MarketplaceConnectionStatusResponse]:
    runtime_settings = reload_settings()
    accounts_by_market = {account.marketplace.value: account for account in accounts}
    enabled_platforms = set(get_enabled_platforms(user))
    sale_detection_platforms = set(user.sale_detection_platforms or [])

    responses: list[MarketplaceConnectionStatusResponse] = []
    for marketplace in MarketplaceName:
        snapshot = marketplace_status_snapshot(
            marketplace=marketplace.value,
            account=accounts_by_market.get(marketplace.value),
            user=user,
        )
        snapshot["enabled_for_publishing"] = marketplace.value in enabled_platforms
        snapshot["enabled_for_sale_detection"] = marketplace.value in sale_detection_platforms
        responses.append(MarketplaceConnectionStatusResponse(**snapshot))
    return sorted(
        responses,
        key=lambda item: (
            int(getattr(item, "ui_priority", 99)),
            str(getattr(item, "marketplace", "")),
        ),
    )


def _require_publishable_marketplaces(user: User, requested: list[str], accounts: list[MarketplaceAccount]) -> None:
    statuses = {
        item.marketplace: item
        for item in _build_marketplace_connections(user=user, accounts=accounts)
    }
    blocked = [name for name in requested if not statuses.get(name) or not statuses[name].can_publish]
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=f"Marketplace setup is incomplete for: {', '.join(blocked)}",
        )


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "is_admin": user.is_admin,
        "effective_is_admin": is_effective_admin(user),
        "view_as_regular": is_viewing_as_regular(user),
        "role": get_user_role(user),
        "can_access_vine_import": settings.amazon_vine_import_enabled and user_has_vine_access(user),
    }


def _persist_ebay_policy_settings(current_user: User, updates: dict[str, object]) -> dict[str, object]:
    settings_json = dict(current_user.settings_json or {})
    current = settings_json.get("ebay_marketplace_policy_settings")
    current = current if isinstance(current, dict) else {}
    for key, value in updates.items():
        if value is not None:
            current[key] = value
    settings_json["ebay_marketplace_policy_settings"] = current
    current_user.settings_json = settings_json
    return current


def _persist_ebay_policy_settings_row(db: Session, user_id: int, updates: dict[str, object]) -> dict[str, object]:
    user = db.get(User, user_id)
    if not user:
        return {}
    persisted = _persist_ebay_policy_settings(user, updates)
    db.execute(update(User).where(User.id == user_id).values(settings_json=user.settings_json))
    db.commit()
    db.refresh(user)
    return persisted


def _bulk_preflight_response_to_csv(report: dict[str, object]) -> str:
    rows: list[dict[str, object]] = []
    items = report.get("items") if isinstance(report, dict) else []
    items = items if isinstance(items, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        marketplaces = item.get("marketplaces") if isinstance(item.get("marketplaces"), dict) else {}
        for marketplace, data in marketplaces.items():
            if not isinstance(data, dict):
                continue
            rows.append(
                {
                    "listing_id": item.get("listing_id"),
                    "title": item.get("title"),
                    "marketplace": marketplace,
                    "status": data.get("status"),
                    "blocker_count": data.get("blocker_count", 0),
                    "warning_count": data.get("warning_count", 0),
                    "blocker_codes": "|".join(data.get("blocker_codes") or []),
                    "blocker_messages": "|".join(data.get("blocker_messages") or []),
                    "warning_codes": "|".join(data.get("warning_codes") or []),
                    "warning_messages": "|".join(data.get("warning_messages") or []),
                    "missing_fields": "|".join(data.get("missing_fields") or []),
                    "price": item.get("price"),
                    "category": item.get("category"),
                    "condition": item.get("condition"),
                    "image_count": item.get("image_count"),
                    "actual_image_count": item.get("actual_image_count"),
                    "package_weight": item.get("package_weight"),
                    "package_dimensions": json.dumps(item.get("package_dimensions") or {}, sort_keys=True),
                    "last_preflight_at": item.get("last_preflight_at").isoformat() if hasattr(item.get("last_preflight_at"), "isoformat") else item.get("last_preflight_at"),
                }
            )
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "listing_id",
            "title",
            "marketplace",
            "status",
            "blocker_count",
            "warning_count",
            "blocker_codes",
            "blocker_messages",
            "warning_codes",
            "warning_messages",
            "missing_fields",
            "price",
            "category",
            "condition",
            "image_count",
            "actual_image_count",
            "package_weight",
            "package_dimensions",
            "last_preflight_at",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


@router.get("/marketplaces")
def get_marketplaces():
    return {"marketplaces": list_marketplaces()}


@router.post("/marketplaces/{name}/connect", response_model=ConnectMarketplaceResponse)
async def connect_marketplace(
    name: str,
    user_id: int | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    if name.lower() == MarketplaceName.ebay.value:
        from app.connectors.registry import get_connector

        connector = get_connector(name)
        auth = await connector.authenticate(scoped_user_id)
        return {"marketplace": name.lower(), "auth": auth}
    if name.lower() in MANUAL_MARKETPLACES:
        return {
            "marketplace": name.lower(),
            "auth": {
                "status": "manual_setup",
                "message": "This marketplace uses an operator-managed workflow today. Save the account details in Settings before enabling it.",
                "settings_route": f"/settings?tab=marketplaces&marketplace={name.lower()}",
                "user_id": scoped_user_id,
            },
        }
    raise HTTPException(status_code=404, detail="Unsupported marketplace")


@router.get("/marketplaces/{name}/callback")
def marketplace_callback(name: str, code: str | None = None, state: str | None = None):
    if name.lower() not in MarketplaceName._value2member_map_:
        raise HTTPException(status_code=404, detail="Unsupported marketplace")
    return {"marketplace": name.lower(), "connected": True, "code": code, "state": state}


@router.post("/listings/{listing_id}/publish")
def publish_listing_multi(
    listing_id: int,
    payload: MarketplacePublishRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    _require_live_ebay_confirmation(
        payload.marketplaces,
        confirm_live_publish=payload.confirm_live_publish,
        confirmation_phrase=payload.confirmation_phrase,
    )
    try:
        results = queue_publish(db, listing_id, payload.marketplaces)
        blocked = [row for row in results if row.get("status") == "BLOCKED"]
        response = {"listing_id": listing_id, "results": results}
        if blocked:
            response["error"] = "; ".join(row.get("error") or "Publish blocked" for row in blocked)
        return response
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/marketplaces/{marketplace}/listings/{listing_id}/preflight", response_model=MarketplacePreflightResponse)
def get_marketplace_preflight(
    marketplace: str,
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    market = marketplace.lower()
    if market not in MarketplaceName._value2member_map_:
        raise HTTPException(status_code=404, detail="Unsupported marketplace")
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    return MarketplacePreflightService().preflight_listing(db, listing, market)


@router.get("/marketplaces/{marketplace}/listings/{listing_id}/payload-preview")
def get_marketplace_payload_preview(
    marketplace: str,
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    market = marketplace.lower()
    if market not in MarketplaceName._value2member_map_:
        raise HTTPException(status_code=404, detail="Unsupported marketplace")
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    return MarketplacePreflightService().preflight_listing(db, listing, market)["payload_preview"]["payload"]


@router.post("/marketplaces/preflight/bulk", response_model=BulkMarketplacePreflightResponse)
def bulk_marketplace_preflight(
    payload: BulkMarketplacePreflightRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if len(payload.listing_ids) > 250:
        raise HTTPException(status_code=400, detail="Bulk preflight is limited to 250 listings per request. Split large batches into smaller groups.")
    listings = db.execute(select(Listing).where(Listing.id.in_(payload.listing_ids))).scalars().all()
    for listing in listings:
        ensure_user_owns_resource(current_user, listing.user_id)
    report = MarketplacePreflightService().bulk_preflight_listing_report(
        db,
        listings,
        payload.marketplaces,
        force_refresh=payload.force_refresh,
        only_drafts=payload.only_drafts,
        selected_statuses=payload.selected_statuses,
        only_missing_preflight=payload.only_missing_preflight,
        only_stale_preflight=payload.only_stale_preflight,
        only_ready_candidates=payload.only_ready_candidates,
        only_blocked_candidates=payload.only_blocked_candidates,
    )
    return report


@router.post("/marketplaces/preflight/bulk/export")
def export_marketplace_preflight_csv(
    payload: BulkMarketplacePreflightRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if len(payload.listing_ids) > 250:
        raise HTTPException(status_code=400, detail="Bulk preflight export is limited to 250 listings per request. Split large batches into smaller groups.")
    listings = db.execute(select(Listing).where(Listing.id.in_(payload.listing_ids))).scalars().all()
    for listing in listings:
        ensure_user_owns_resource(current_user, listing.user_id)
    report = MarketplacePreflightService().bulk_preflight_listing_report(
        db,
        listings,
        payload.marketplaces,
        force_refresh=payload.force_refresh,
        only_drafts=payload.only_drafts,
        selected_statuses=payload.selected_statuses,
        only_missing_preflight=payload.only_missing_preflight,
        only_stale_preflight=payload.only_stale_preflight,
        only_ready_candidates=payload.only_ready_candidates,
        only_blocked_candidates=payload.only_blocked_candidates,
    )
    csv_content = _bulk_preflight_response_to_csv(report)
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="posterpro-preflight-report.csv"'},
    )


@router.get("/marketplaces/launch-candidates", response_model=LaunchCandidateResponse)
def get_launch_candidates(
    marketplace: str = Query("ebay"),
    max_items: int = Query(10, ge=1, le=50),
    max_price: float = Query(50, ge=0),
    include_warning_only: bool = Query(False),
    include_local_pickup: bool = Query(False),
    include_risky_shipping: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listings = db.execute(select(Listing).where(Listing.user_id == current_user.id)).scalars().all()
    report = MarketplacePreflightService().launch_candidates(
        db,
        listings,
        marketplace=marketplace,
        max_items=max_items,
        max_price=max_price,
        include_warning_only=include_warning_only,
        include_local_pickup=include_local_pickup,
        include_risky_shipping=include_risky_shipping,
    )
    return report


@router.get("/marketplaces/ebay/launch-repair-queue", response_model=EbayLaunchRepairQueueResponse)
def get_ebay_launch_repair_queue(
    max_items: int = Query(50, ge=1, le=250),
    max_price: float = Query(50, ge=0),
    image_status: str | None = Query(default=None),
    has_category_suggestion: bool | None = Query(default=None),
    repair_difficulty: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listings = db.execute(select(Listing).where(Listing.user_id == current_user.id)).scalars().all()
    report = MarketplacePreflightService().launch_repair_queue(
        db,
        listings,
        marketplace="ebay",
        max_items=max_items,
        max_price=max_price,
        image_status=image_status,
        has_category_suggestion=has_category_suggestion,
        repair_difficulty=repair_difficulty,
    )
    return report


@router.post("/marketplaces/ebay/listings/{listing_id}/repair")
def apply_ebay_launch_repair(
    listing_id: int,
    payload: EbayLaunchRepairActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    return MarketplacePreflightService().apply_repair_actions(
        db,
        listing,
        apply_category_suggestion=payload.apply_category_suggestion,
        validate_images=payload.validate_images,
    )


@router.post("/marketplaces/launch-drill/dry-run", response_model=LaunchDrillResponse)
def launch_drill_dry_run(
    payload: LaunchDrillRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if len(payload.listing_ids) > 250:
        raise HTTPException(status_code=400, detail="Launch drill is limited to 250 listings per request. Split large batches into smaller groups.")
    listings = db.execute(select(Listing).where(Listing.user_id == current_user.id, Listing.id.in_(payload.listing_ids))).scalars().all()
    report = MarketplacePreflightService().launch_drill_dry_run(
        db,
        listings,
        marketplace=payload.marketplace,
        max_items=payload.max_items,
        require_ready=payload.require_ready,
        include_payload_preview=payload.include_payload_preview,
    )
    return report


@router.post("/marketplaces/publish-ready/bulk", response_model=BulkMarketplacePublishReadyResponse)
def publish_marketplace_ready_bulk(
    payload: BulkMarketplacePublishReadyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if len(payload.listing_ids) > 250:
        raise HTTPException(status_code=400, detail="Bulk publish-ready is limited to 250 listings per request. Split large batches into smaller groups.")
    listings = db.execute(select(Listing).where(Listing.id.in_(payload.listing_ids))).scalars().all()
    for listing in listings:
        ensure_user_owns_resource(current_user, listing.user_id)
    if not payload.dry_run:
        _require_live_ebay_confirmation(
            payload.marketplaces,
            confirm_live_publish=payload.confirm_live_publish,
            confirmation_phrase=payload.confirmation_phrase,
        )
    report = bulk_publish_ready(
        db,
        payload.listing_ids,
        payload.marketplaces,
        allow_warnings=payload.allow_warnings,
        dry_run=payload.dry_run,
        force_preflight_refresh=payload.force_preflight_refresh,
        skip_already_queued=payload.skip_already_queued,
    )
    return report


@router.get("/marketplaces/ebay/policies", response_model=EbayPolicyCatalogResponse)
async def get_ebay_policy_catalog(
    marketplace_id: str = Query("EBAY_US"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    account = await get_or_refresh_account(current_user.id, db)
    try:
        catalog = await _list_business_policies_for_account(
            current_user.id,
            db,
            account,
            marketplace_id=marketplace_id,
        )
        current_settings = current_user.settings_json or {}
        current = current_settings.get("ebay_marketplace_policy_settings")
        current = current if isinstance(current, dict) else {}
        return EbayPolicyCatalogResponse(
            marketplace_id=catalog.get("marketplace_id") or marketplace_id,
            status="ready",
            payment_policies=[row for row in catalog.get("payment_policies") or []],
            fulfillment_policies=[row for row in catalog.get("fulfillment_policies") or []],
            return_policies=[row for row in catalog.get("return_policies") or []],
            selected=catalog.get("selected") or {},
            policy_settings=current,
            missing_policy_types=[
                name
                for name, rows in (
                    ("payment", catalog.get("payment_policies") or []),
                    ("fulfillment", catalog.get("fulfillment_policies") or []),
                    ("return", catalog.get("return_policies") or []),
                )
                if not rows
            ],
            sync_error=None,
            last_synced_at=None,
        )
    except Exception as exc:  # noqa: BLE001
        current_settings = current_user.settings_json or {}
        current = current_settings.get("ebay_marketplace_policy_settings")
        current = current if isinstance(current, dict) else {}
        return EbayPolicyCatalogResponse(
            marketplace_id=marketplace_id,
            status="unavailable",
            policy_settings=current,
            sync_error=str(exc),
        )


@router.post("/marketplaces/ebay/policies/sync", response_model=EbayPolicyCatalogResponse)
async def sync_ebay_policy_settings(
    payload: EbayPolicySyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = await sync_business_policies(
        current_user.id,
        db,
        marketplace_id=payload.marketplace_id,
        create_missing_defaults=payload.create_missing_defaults,
    )
    policy_settings = _persist_ebay_policy_settings_row(db, current_user.id, report.get("settings_updates") or {})
    catalog = report.get("policy_catalog") or {}
    return EbayPolicyCatalogResponse(
        marketplace_id=report.get("marketplace_id") or payload.marketplace_id,
        status=report.get("status") or "ready",
        payment_policies=[row for row in catalog.get("payment_policies") or []],
        fulfillment_policies=[row for row in catalog.get("fulfillment_policies") or []],
        return_policies=[row for row in catalog.get("return_policies") or []],
        selected=report.get("selected") or {},
        policy_settings=policy_settings,
        missing_policy_types=report.get("missing_policy_types") or [],
        sync_error=(report.get("settings_updates") or {}).get("policy_sync_error") or None,
        last_synced_at=datetime.fromisoformat((report.get("settings_updates") or {}).get("last_policy_sync_at")) if (report.get("settings_updates") or {}).get("last_policy_sync_at") else None,
    )


@router.post("/marketplaces/ebay/policies/select", response_model=EbayPolicyCatalogResponse)
def select_ebay_policy_settings(
    payload: EbayPolicySelectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updates = {
        "marketplace_id": payload.marketplace_id,
        "payment_policy_id": payload.payment_policy_id or "",
        "payment_policy_name": payload.payment_policy_name or "",
        "fulfillment_policy_id": payload.fulfillment_policy_id or "",
        "fulfillment_policy_name": payload.fulfillment_policy_name or "",
        "return_policy_id": payload.return_policy_id or "",
        "return_policy_name": payload.return_policy_name or "",
        "policy_sync_status": "selected_manual",
        "policy_sync_error": "",
    }
    policy_settings = _persist_ebay_policy_settings_row(db, current_user.id, updates)
    return EbayPolicyCatalogResponse(
        marketplace_id=payload.marketplace_id,
        status="ready",
        policy_settings=policy_settings,
        selected={
            "payment_policy_id": policy_settings.get("payment_policy_id"),
            "payment_policy_name": policy_settings.get("payment_policy_name"),
            "fulfillment_policy_id": policy_settings.get("fulfillment_policy_id"),
            "fulfillment_policy_name": policy_settings.get("fulfillment_policy_name"),
            "return_policy_id": policy_settings.get("return_policy_id"),
            "return_policy_name": policy_settings.get("return_policy_name"),
        },
    )


@router.post("/marketplaces/ebay/location/verify")
async def verify_ebay_merchant_location(
    payload: EbayMerchantLocationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = await verify_merchant_location(
        current_user.id,
        db,
        location_key=payload.merchant_location_key,
        create_if_missing=payload.create_if_missing,
        origin={
            "merchant_location_location_name": payload.merchant_location_location_name or "",
            "merchant_location_postal_code": payload.merchant_location_postal_code or "",
            "merchant_location_country": payload.merchant_location_country or "",
            "merchant_location_city": payload.merchant_location_city or "",
            "merchant_location_state_or_province": payload.merchant_location_state_or_province or "",
            "merchant_location_phone": payload.merchant_location_phone or "",
        },
    )
    persisted = _persist_ebay_policy_settings_row(db, current_user.id, report.get("settings_updates") or {})
    expected_status = str(report.get("status") or "").strip().lower()
    persisted_status = str(persisted.get("merchant_location_status") or "").strip().lower()
    if expected_status in {"verified", "created"} and persisted_status not in {"verified", "created"}:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Merchant location response did not persist to the operator record.",
                "expected_status": expected_status,
                "persisted_status": persisted_status,
                "persisted_settings": persisted,
            },
        )
    return report


@router.post("/marketplaces/ebay/location/create")
async def create_ebay_merchant_location(
    payload: EbayMerchantLocationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = await verify_merchant_location(
        current_user.id,
        db,
        location_key=payload.merchant_location_key,
        create_if_missing=True,
        origin={
            "merchant_location_location_name": payload.merchant_location_location_name or "",
            "merchant_location_postal_code": payload.merchant_location_postal_code or "",
            "merchant_location_country": payload.merchant_location_country or "",
            "merchant_location_city": payload.merchant_location_city or "",
            "merchant_location_state_or_province": payload.merchant_location_state_or_province or "",
            "merchant_location_phone": payload.merchant_location_phone or "",
        },
    )
    persisted = _persist_ebay_policy_settings_row(db, current_user.id, report.get("settings_updates") or {})
    expected_status = str(report.get("status") or "").strip().lower()
    persisted_status = str(persisted.get("merchant_location_status") or "").strip().lower()
    if expected_status in {"verified", "created"} and persisted_status not in {"verified", "created"}:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Merchant location response did not persist to the operator record.",
                "expected_status": expected_status,
                "persisted_status": persisted_status,
                "persisted_settings": persisted,
            },
        )
    return report


@router.post("/marketplaces/ebay/policies/refresh")
async def refresh_ebay_policy_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = await sync_business_policies(
        current_user.id,
        db,
        marketplace_id="EBAY_US",
        create_missing_defaults=False,
    )
    _persist_ebay_policy_settings(current_user, report.get("settings_updates") or {})
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    policy_settings = current_user.settings_json.get("ebay_marketplace_policy_settings") if isinstance(current_user.settings_json, dict) else {}
    policy_settings = policy_settings if isinstance(policy_settings, dict) else {}
    return {"status": report.get("status") or "updated", "ebay_marketplace_policy_settings": policy_settings, "report": report}


@router.post("/marketplaces/ebay/sync")
async def refresh_ebay_inventory(
    user_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=250),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    try:
        return await sync_ebay_active_listings(scoped_user_id, db, limit=limit)
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/marketplaces/ebay/sync/history")
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


@router.get("/marketplaces/ebay/account-readiness", response_model=EbayAccountReadinessResponse)
def get_ebay_account_readiness(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == current_user.id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    health = summarize_ebay_account_health(account)
    settings_json = current_user.settings_json or {}
    policy_settings = settings_json.get("ebay_marketplace_policy_settings") if isinstance(settings_json, dict) else {}
    policy_settings = policy_settings if isinstance(policy_settings, dict) else {}
    merchant_location_last_checked_at = None
    raw_last_checked = str(policy_settings.get("merchant_location_last_checked_at") or "").strip()
    if raw_last_checked:
        try:
            merchant_location_last_checked_at = datetime.fromisoformat(raw_last_checked)
        except ValueError:
            merchant_location_last_checked_at = None
    response = {
        **health,
        "payment_policy_name": str(policy_settings.get("payment_policy_name") or "").strip() or None,
        "payment_policy_id": str(policy_settings.get("payment_policy_id") or "").strip() or None,
        "fulfillment_policy_name": str(policy_settings.get("fulfillment_policy_name") or "").strip() or None,
        "fulfillment_policy_id": str(policy_settings.get("fulfillment_policy_id") or "").strip() or None,
        "return_policy_name": str(policy_settings.get("return_policy_name") or "").strip() or None,
        "return_policy_id": str(policy_settings.get("return_policy_id") or "").strip() or None,
        "merchant_location_key": str(policy_settings.get("merchant_location_key") or "").strip() or None,
        "merchant_location_verified": bool(policy_settings.get("merchant_location_verified")),
        "merchant_location_status": str(policy_settings.get("merchant_location_status") or "").strip() or None,
        "merchant_location_last_checked_at": merchant_location_last_checked_at,
        "policy_sync_status": str(policy_settings.get("policy_sync_status") or "").strip() or None,
        "policy_sync_error": str(policy_settings.get("policy_sync_error") or "").strip() or None,
        "shipping_service_code": str(policy_settings.get("shipping_service_code") or "").strip() or None,
        "handling_time_days": int(policy_settings.get("handling_time_days") or 1),
        "local_pickup_allowed": bool(policy_settings.get("local_pickup_allowed")),
        "calculated_shipping": bool(policy_settings.get("calculated_shipping")),
        "package_weight_required": bool(policy_settings.get("package_weight_required", True)),
        "package_dimensions_required": bool(policy_settings.get("package_dimensions_required", True)),
        "policies_present": bool(
            str(policy_settings.get("payment_policy_id") or "").strip()
            and str(policy_settings.get("fulfillment_policy_id") or "").strip()
            and str(policy_settings.get("return_policy_id") or "").strip()
        ),
        "location_present": bool(str(policy_settings.get("merchant_location_key") or "").strip()),
        "publish_ready": bool(
            health["import_ready"]
            and str(policy_settings.get("payment_policy_id") or "").strip()
            and str(policy_settings.get("fulfillment_policy_id") or "").strip()
            and str(policy_settings.get("return_policy_id") or "").strip()
            and str(policy_settings.get("merchant_location_key") or "").strip()
        ),
        "mode": reload_settings().environment,
    }
    return EbayAccountReadinessResponse(**response)


@router.post("/marketplaces/ebay/listings/{listing_id}/sync")
async def sync_ebay_listing_revision(
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
    if not payload or not payload.confirm_live_publish or str(payload.confirmation_phrase or "").strip() != LIVE_EBAY_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f"Live eBay update requires explicit confirmation. Use the confirmation phrase '{LIVE_EBAY_CONFIRMATION_PHRASE}' to proceed.",
        )
    try:
        return await revise_ebay_listing(listing, db)
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/listings/publish-bulk", response_model=BulkMarketplacePublishResponse)
def publish_listings_bulk(
    payload: BulkMarketplacePublishRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_live_ebay_confirmation(
        payload.marketplaces,
        confirm_live_publish=payload.confirm_live_publish,
        confirmation_phrase=payload.confirmation_phrase,
    )
    results: list[dict] = []
    for listing_id in payload.listing_ids:
        listing = db.get(Listing, listing_id)
        if not listing:
            continue
        ensure_user_owns_resource(current_user, listing.user_id)
        try:
            queued = queue_publish(db, listing_id, payload.marketplaces)
            blocked = [row for row in queued if row.get("status") == "BLOCKED"]
            payload_result = {"listing_id": listing_id, "results": queued}
            if blocked:
                payload_result["error"] = "; ".join(row.get("error") or "Publish blocked" for row in blocked)
            results.append(payload_result)
        except ValueError as exc:
            results.append({"listing_id": listing_id, "results": [], "error": str(exc)})
    return {"results": results}


@router.get("/listings/{listing_id}/marketplace_status")
def get_marketplace_status(
    listing_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    ensure_user_owns_resource(current_user, listing.user_id)
    rows = listing_marketplace_status(db, listing_id)
    return {
        "listing_id": listing_id,
        "marketplaces": [
            {
                "marketplace": row.marketplace.value,
                "status": row.status.value,
                "marketplace_listing_id": row.marketplace_listing_id,
                "raw_response": row.raw_response,
            }
            for row in rows
        ],
    }


@router.post("/listings/sync_sold")
def sync_sold(payload: SoldSyncRequest, current_user: User = Depends(get_current_user)):
    return trigger_sync_sold(payload.listing_ids)


@router.get("/users/{user_id}/platform-config")
def get_platform_config(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    user = db.get(User, scoped_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    enabled = get_enabled_platforms(user)
    return {"user_id": scoped_user_id, "enabled_platforms": enabled}


@router.put("/users/{user_id}/platform-config")
def update_platform_config(
    user_id: int,
    payload: MarketplacePublishRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    user = db.get(User, scoped_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    requested = [market.lower() for market in (payload.marketplaces or [MarketplaceName.ebay.value])]
    invalid = [market for market in requested if market not in MarketplaceName._value2member_map_]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported marketplaces: {', '.join(invalid)}")
    accounts = db.execute(
        select(MarketplaceAccount).where(MarketplaceAccount.user_id == scoped_user_id)
    ).scalars().all()
    _require_publishable_marketplaces(user, requested, accounts)
    user.enabled_platforms = list(dict.fromkeys(requested))
    db.add(user)
    db.commit()
    return {"user_id": scoped_user_id, "enabled_platforms": user.enabled_platforms}


@router.put("/users/{user_id}/marketplace-connections/{name}", response_model=MarketplaceConnectionStatusResponse)
def update_marketplace_connection(
    user_id: int,
    name: str,
    payload: MarketplaceConnectionUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    marketplace = name.lower()
    if marketplace not in MarketplaceName._value2member_map_:
        raise HTTPException(status_code=404, detail="Unsupported marketplace")
    if marketplace == MarketplaceName.ebay.value:
        raise HTTPException(status_code=400, detail="Use the eBay OAuth flow for this marketplace")
    if marketplace not in MANUAL_MARKETPLACES:
        raise HTTPException(status_code=400, detail="This marketplace does not support account setup in the dashboard yet")

    scoped_user_id = resolve_user_scope(current_user, user_id)
    user = db.get(User, scoped_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    save_manual_marketplace_settings(user, marketplace, payload.model_dump(exclude_none=True))
    db.add(user)
    db.commit()
    db.refresh(user)
    snapshot = {
        **marketplace_status_snapshot(marketplace=marketplace, account=None, user=user),
        "enabled_for_publishing": marketplace in get_enabled_platforms(user),
        "enabled_for_sale_detection": marketplace in (user.sale_detection_platforms or []),
    }
    return MarketplaceConnectionStatusResponse(**snapshot)


@router.get("/users/{user_id}/setup", response_model=AccountSetupSummaryResponse)
def get_account_setup_summary(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    runtime_settings = reload_settings()
    scoped_user_id = resolve_user_scope(current_user, user_id)
    user = db.get(User, scoped_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    accounts = db.execute(
        select(MarketplaceAccount).where(MarketplaceAccount.user_id == scoped_user_id)
    ).scalars().all()
    total_listings = db.execute(
        select(func.count()).select_from(Listing).where(Listing.user_id == scoped_user_id)
    ).scalar_one()
    ready_to_publish_count = db.execute(
        select(func.count()).select_from(Listing).where(Listing.user_id == scoped_user_id, Listing.status == "ready")
    ).scalar_one()
    has_templates = db.execute(
        select(func.count()).select_from(ListingTemplate).where(ListingTemplate.user_id == scoped_user_id)
    ).scalar_one() > 0

    marketplace_connections = _build_marketplace_connections(user=user, accounts=accounts)
    try:
        active_bridge_connect_session = get_active_bridge_connect_session()
    except AutomationBridgeError:
        active_bridge_connect_session = None
    server_has_ebay = bool(runtime_settings.ebay_client_id and runtime_settings.ebay_client_secret and (runtime_settings.ebay_runame or runtime_settings.ebay_redirect_uri))

    return AccountSetupSummaryResponse(
        user=UserResponse.model_validate(_serialize_user(user)),
        ready_to_publish_count=ready_to_publish_count,
        total_listings=total_listings,
        connected_marketplaces=sum(1 for account in accounts if account.access_token),
        has_templates=has_templates,
        account_profile_complete=bool((user.full_name or "").strip()),
        marketplace_connections=marketplace_connections,
        active_bridge_connect_session=(
            ActiveBridgeConnectSessionSummaryResponse(**active_bridge_connect_session)
            if isinstance(active_bridge_connect_session, dict)
            else None
        ),
        server_readiness=ServerReadinessResponse(
            openai_configured=bool(runtime_settings.openai_api_key),
            photoroom_configured=bool(runtime_settings.photoroom_api_key),
            ebay_oauth_configured=server_has_ebay,
            storage_root_configured=bool(runtime_settings.storage_root),
            session_secret_configured=bool(runtime_settings.session_secret),
            amazon_vine_import_enabled=runtime_settings.amazon_vine_import_enabled,
            amazon_media_lookup_enabled=runtime_settings.amazon_media_lookup_enabled,
            amazon_paapi_configured=bool(runtime_settings.amazon_paapi_access_key and runtime_settings.amazon_paapi_secret_key),
        ),
    )
