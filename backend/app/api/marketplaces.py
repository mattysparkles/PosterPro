from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.schemas import ConnectMarketplaceResponse, MarketplacePublishRequest, SoldSyncRequest
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import User
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
def connect_marketplace(name: str, user_id: int = Query(1)):
    if name.lower() == MarketplaceName.ebay.value:
        from app.connectors.registry import get_connector

        connector = get_connector(name)
        auth = asyncio.run(connector.authenticate(user_id))
        return {"marketplace": name.lower(), "auth": auth}
    raise HTTPException(status_code=400, detail="TODO – API keys coming")


@router.get("/marketplaces/{name}/callback")
def marketplace_callback(name: str, code: str | None = None, state: str | None = None):
    if name.lower() not in MarketplaceName._value2member_map_:
        raise HTTPException(status_code=404, detail="Unsupported marketplace")
    return {"marketplace": name.lower(), "connected": True, "code": code, "state": state}


@router.post("/listings/{listing_id}/publish")
def publish_listing_multi(listing_id: int, payload: MarketplacePublishRequest, db: Session = Depends(get_db)):
    try:
        return {"listing_id": listing_id, "results": queue_publish(db, listing_id, payload.marketplaces)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/listings/{listing_id}/marketplace_status")
def get_marketplace_status(listing_id: int, db: Session = Depends(get_db)):
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
def sync_sold(payload: SoldSyncRequest):
    return trigger_sync_sold(payload.listing_ids)


@router.get("/users/{user_id}/platform-config")
def get_platform_config(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    enabled = user.enabled_platforms or [MarketplaceName.ebay.value]
    return {"user_id": user_id, "enabled_platforms": enabled}


@router.put("/users/{user_id}/platform-config")
def update_platform_config(user_id: int, payload: MarketplacePublishRequest, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    requested = [market.lower() for market in (payload.marketplaces or [MarketplaceName.ebay.value])]
    invalid = [market for market in requested if market not in MarketplaceName._value2member_map_]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported marketplaces: {', '.join(invalid)}")
    user.enabled_platforms = list(dict.fromkeys(requested))
    db.add(user)
    db.commit()
    return {"user_id": user_id, "enabled_platforms": user.enabled_platforms}
