from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import EbayOfferHistory, Listing, MarketplaceAccount
from app.services.ebay_service import (
    EbayIntegrationError,
    get_incoming_best_offers,
    authenticate_user_ebay,
    exchange_code_for_tokens,
    get_or_refresh_account,
    parse_oauth_state,
    publish_listing_to_ebay,
)

router = APIRouter()


@router.get("/ebay/auth/url")
async def ebay_auth_url(user_id: int = Query(...), redirect_uri: str | None = Query(None)):
    callback = redirect_uri or settings.ebay_redirect_uri
    if not callback:
        raise HTTPException(status_code=400, detail="redirect_uri is required")
    try:
        url = await authenticate_user_ebay(user_id=user_id, redirect_uri=callback)
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
    callback = redirect_uri or settings.ebay_redirect_uri
    if not callback:
        raise HTTPException(status_code=400, detail="redirect_uri is required")

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

    db.add(account)
    db.commit()
    return {"connected": True, "user_id": user_id, "marketplace": "ebay"}


@router.post("/listings/{listing_id}/publish/ebay")
async def publish_listing_ebay(listing_id: int, db: Session = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not listing.title or not listing.description:
        raise HTTPException(status_code=400, detail="Listing must be generated before publishing")

    try:
        return await publish_listing_to_ebay(listing, db)
    except EbayIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ebay/status/{listing_id}")
async def ebay_listing_status(listing_id: int, db: Session = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    return {
        "id": listing.id,
        "ebay_listing_id": listing.ebay_listing_id,
        "status": listing.ebay_publish_status,
        "marketplace_data": listing.marketplace_data,
    }


@router.get("/ebay/offers/dashboard")
async def ebay_offer_dashboard(user_id: int = Query(1), db: Session = Depends(get_db)):
    account = await get_or_refresh_account(user_id, db)
    active_offers = await get_incoming_best_offers(account, limit=50)
    decisions = db.execute(
        select(EbayOfferHistory)
        .where(EbayOfferHistory.user_id == user_id)
        .order_by(EbayOfferHistory.created_at.desc())
        .limit(100)
    ).scalars().all()
    return {
        "active_offers": active_offers,
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
