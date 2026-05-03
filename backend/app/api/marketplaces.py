from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AccountSetupSummaryResponse,
    ConnectMarketplaceResponse,
    MarketplaceConnectionStatusResponse,
    MarketplacePublishRequest,
    ServerReadinessResponse,
    SoldSyncRequest,
    UserResponse,
)
from app.core.auth import ensure_user_owns_resource, get_current_user, resolve_user_scope
from app.core.config import settings
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import Listing, ListingTemplate, MarketplaceAccount, User
from app.connectors.registry import MARKETPLACE_REGISTRY
from app.services.marketplace_orchestrator import (
    list_marketplaces,
    listing_marketplace_status,
    queue_publish,
    trigger_sync_sold,
)

router = APIRouter()


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
    raise HTTPException(status_code=400, detail="TODO – API keys coming")


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
    user.enabled_platforms = list(dict.fromkeys(requested))
    db.add(user)
    db.commit()
    return {"user_id": scoped_user_id, "enabled_platforms": user.enabled_platforms}


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
    accounts_by_market = {account.marketplace.value: account for account in accounts}

    total_listings = db.execute(
        select(func.count()).select_from(Listing).where(Listing.user_id == scoped_user_id)
    ).scalar_one()
    ready_to_publish_count = db.execute(
        select(func.count()).select_from(Listing).where(Listing.user_id == scoped_user_id, Listing.status == "ready")
    ).scalar_one()
    has_templates = db.execute(
        select(func.count()).select_from(ListingTemplate).where(ListingTemplate.user_id == scoped_user_id)
    ).scalar_one() > 0

    enabled_platforms = set(user.enabled_platforms or [MarketplaceName.ebay.value])
    sale_detection_platforms = set(user.sale_detection_platforms or [])

    marketplace_connections: list[MarketplaceConnectionStatusResponse] = []
    server_has_ebay = bool(
        settings.ebay_client_id and settings.ebay_client_secret and settings.ebay_redirect_uri
    )

    for marketplace in MarketplaceName:
        connector = MARKETPLACE_REGISTRY.get(marketplace.value)
        account = accounts_by_market.get(marketplace.value)
        supports_oauth = bool(connector and getattr(connector, "supports_oauth", False))
        available = marketplace.value == MarketplaceName.ebay.value and server_has_ebay
        if marketplace.value == MarketplaceName.ebay.value:
            note = (
                "Ready for account-level OAuth connection."
                if available
                else "Server eBay OAuth credentials are missing."
            )
        else:
            note = "UI model exists, but account-level connection flow is not implemented yet."

        marketplace_connections.append(
            MarketplaceConnectionStatusResponse(
                marketplace=marketplace.value,
                supports_oauth=supports_oauth,
                connection_mode="oauth" if supports_oauth else "coming_soon",
                connected=account is not None,
                available=available,
                enabled_for_publishing=marketplace.value in enabled_platforms,
                enabled_for_sale_detection=marketplace.value in sale_detection_platforms,
                external_account_id=account.external_account_id if account else None,
                token_expires_at=account.token_expires_at if account else None,
                status_note=note,
            )
        )

    return AccountSetupSummaryResponse(
        user=UserResponse.model_validate(user),
        ready_to_publish_count=ready_to_publish_count,
        total_listings=total_listings,
        connected_marketplaces=sum(1 for account in accounts if account.access_token),
        has_templates=has_templates,
        account_profile_complete=bool((user.full_name or "").strip()),
        marketplace_connections=marketplace_connections,
        server_readiness=ServerReadinessResponse(
            openai_configured=bool(settings.openai_api_key),
            photoroom_configured=bool(settings.photoroom_api_key),
            ebay_oauth_configured=server_has_ebay,
            storage_root_configured=bool(settings.storage_root),
        ),
    )
