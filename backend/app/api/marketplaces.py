from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.schemas import ConnectMarketplaceResponse, MarketplacePublishRequest, SoldSyncRequest
from app.connectors.registry import MARKETPLACE_REGISTRY, get_connector
from app.core.database import get_db
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
    try:
        connector = get_connector(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    auth = asyncio.run(connector.authenticate(user_id))
    return {"marketplace": name.lower(), "auth": auth}


@router.get("/marketplaces/{name}/callback")
def marketplace_callback(name: str, code: str | None = None, state: str | None = None):
    if name.lower() not in MARKETPLACE_REGISTRY:
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
