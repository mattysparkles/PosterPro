from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import EbayManualConnectRequest, EbayPublishConfirmationRequest
from app.core.auth import ensure_user_owns_resource, get_current_user, resolve_user_scope
from app.core.config import settings
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import EbayOfferHistory, Listing, MarketplaceAccount, User
from app.services.ebay_service import (
    EbayIntegrationError,
    get_incoming_best_offers,
    authenticate_user_ebay,
    exchange_code_for_tokens,
    get_or_refresh_account,
    parse_oauth_state,
    publish_listing_to_ebay,
    revise_ebay_listing,
    sync_ebay_active_listings,
)
from app.services.pricing_research_service import validate_marketplace_readiness
router = APIRouter()


@router.get("/ebay/auth/url")
async def ebay_auth_url(
    user_id: int | None = Query(None),
    redirect_uri: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    callback = redirect_uri or settings.ebay_runame or settings.ebay_redirect_uri
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
    callback = redirect_uri or settings.ebay_runame or settings.ebay_redirect_uri
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
    limit: int = Query(100, ge=1, le=250),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    try:
        return await sync_ebay_active_listings(scoped_user_id, db, limit=limit)
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
