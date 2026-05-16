from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ActiveBridgeConnectSessionSummaryResponse,
    AccountSetupSummaryResponse,
    ConnectMarketplaceResponse,
    MarketplaceConnectionUpdateRequest,
    MarketplaceConnectionStatusResponse,
    MarketplacePublishRequest,
    ServerReadinessResponse,
    SoldSyncRequest,
    UserResponse,
)
from app.core.auth import ensure_user_owns_resource, get_current_user, get_user_role, is_effective_admin, is_viewing_as_regular, resolve_user_scope, user_has_vine_access
from app.core.config import settings
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import Listing, ListingTemplate, MarketplaceAccount, User
from app.connectors.registry import MARKETPLACE_REGISTRY
from app.services.marketplace_setup import (
    MANUAL_MARKETPLACES,
    marketplace_status_snapshot,
    save_manual_marketplace_settings,
)
from app.services.automation_bridge import AutomationBridgeError, get_active_bridge_connect_session
from app.services.marketplace_orchestrator import (
    list_marketplaces,
    listing_marketplace_status,
    queue_publish,
    trigger_sync_sold,
)

router = APIRouter()


def _build_marketplace_connections(*, user: User, accounts: list[MarketplaceAccount]) -> list[MarketplaceConnectionStatusResponse]:
    accounts_by_market = {account.marketplace.value: account for account in accounts}
    enabled_platforms = set(user.enabled_platforms or [MarketplaceName.ebay.value])
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
    return responses


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


@router.get("/marketplaces")
def get_marketplaces():
    return {"marketplaces": list_marketplaces()}


@router.post("/marketplaces/{name}/connect", response_model=ConnectMarketplaceResponse)
def connect_marketplace(
    name: str,
    user_id: int | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    if name.lower() == MarketplaceName.ebay.value:
        from app.connectors.registry import get_connector

        connector = get_connector(name)
        auth = asyncio.run(connector.authenticate(scoped_user_id))
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
    try:
        return {"listing_id": listing_id, "results": queue_publish(db, listing_id, payload.marketplaces)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    enabled = user.enabled_platforms or [MarketplaceName.ebay.value]
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
        "enabled_for_publishing": marketplace in (user.enabled_platforms or []),
        "enabled_for_sale_detection": marketplace in (user.sale_detection_platforms or []),
    }
    return MarketplaceConnectionStatusResponse(**snapshot)


@router.get("/users/{user_id}/setup", response_model=AccountSetupSummaryResponse)
def get_account_setup_summary(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
    server_has_ebay = bool(settings.ebay_client_id and settings.ebay_client_secret and (settings.ebay_runame or settings.ebay_redirect_uri))

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
            openai_configured=bool(settings.openai_api_key),
            photoroom_configured=bool(settings.photoroom_api_key),
            ebay_oauth_configured=server_has_ebay,
            storage_root_configured=bool(settings.storage_root),
            session_secret_configured=bool(settings.session_secret),
            amazon_vine_import_enabled=settings.amazon_vine_import_enabled,
            amazon_media_lookup_enabled=settings.amazon_media_lookup_enabled,
            amazon_paapi_configured=bool(settings.amazon_paapi_access_key and settings.amazon_paapi_secret_key),
        ),
    )
