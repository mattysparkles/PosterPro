from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.schemas import SaleDetailsUpdateRequest, SaleDetectionConfigRequest, SaleReconcileRequest
from app.core.auth import ensure_user_owns_resource, get_current_user, resolve_user_scope
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import AutomatedOfferLog, Listing, MarketplaceAccount, OfferAutomationRule, Sale, User
from app.services.offer_service import OfferService
from app.services.marketplace_setup import marketplace_status_snapshot
from app.services.sale_detection_service import SaleDetectionService

router = APIRouter(prefix="/sales", tags=["sales"])
offer_service = OfferService()
sale_detection_service = SaleDetectionService()


@router.get("/dashboard")
def sales_dashboard(
    user_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=250),
    search: str | None = Query(None),
    marketplace: str | None = Query(None),
    sort_by: str = Query("sold_at"),
    sort_dir: str = Query("desc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    stmt = select(Sale).join(Listing, Listing.id == Sale.listing_id, isouter=True).where(Sale.user_id == scoped_user_id)
    if marketplace and marketplace in MarketplaceName._value2member_map_:
        stmt = stmt.where(Sale.platform == MarketplaceName(marketplace))
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Sale.marketplace_order_id.ilike(term),
                Sale.marketplace_listing_id.ilike(term),
                cast(Sale.id, String).ilike(term),
                Listing.title.ilike(term),
                Sale.status.ilike(term),
            )
        )

    sort_columns = {
        "sold_at": Sale.sold_at,
        "amount": Sale.amount,
        "fees_actual": Sale.fees_actual,
        "shipping_cost": Sale.shipping_cost,
        "promotional_fees": Sale.promotional_fees,
        "marketplace_fees": Sale.marketplace_fees,
        "profit": Sale.profit,
        "platform": Sale.platform,
        "created_at": Sale.created_at,
    }
    sort_column = sort_columns.get(sort_by, Sale.sold_at)
    if str(sort_dir or "desc").lower() == "asc":
        stmt = stmt.order_by(sort_column.asc().nullslast(), Sale.id.asc())
    else:
        stmt = stmt.order_by(sort_column.desc().nullslast(), Sale.id.desc())
    sales = db.execute(stmt.limit(limit)).scalars().all()
    gross_sales = sum(float(s.amount or 0.0) for s in sales)
    total_profit = sum(float(s.profit or 0.0) for s in sales)
    units = sum(int(s.quantity or 1) for s in sales)
    by_platform = {
        getattr(row[0], "value", row[0]): {
            "count": row[1],
            "gross": float(row[2] or 0),
            "fees_actual": float(row[3] or 0),
            "shipping_cost": float(row[4] or 0),
            "promotional_fees": float(row[5] or 0),
            "marketplace_fees": float(row[6] or 0),
            "profit": float(row[7] or 0),
        }
        for row in db.execute(
            select(
                Sale.platform,
                func.count(Sale.id),
                func.sum(Sale.amount),
                func.sum(Sale.fees_actual),
                func.sum(Sale.shipping_cost),
                func.sum(Sale.promotional_fees),
                func.sum(Sale.marketplace_fees),
                func.sum(Sale.profit),
            ).where(Sale.user_id == scoped_user_id).group_by(Sale.platform)
        ).all()
    }
    return {
        "user_id": scoped_user_id,
        "summary": {"total_sales": len(sales), "units": units, "gross": gross_sales, "total_profit": total_profit, "by_platform": by_platform},
        "sales": [
            {
                "id": sale.id,
                "listing_id": sale.listing_id,
                "platform": getattr(sale.platform, "value", sale.platform),
                "amount": sale.amount,
                "currency": sale.currency,
                "quantity": sale.quantity,
                "fees_actual": sale.fees_actual,
                "shipping_cost": sale.shipping_cost,
                "promotional_fees": sale.promotional_fees,
                "marketplace_fees": sale.marketplace_fees,
                "profit": sale.profit,
                "roi_percentage": sale.roi_percentage,
                "sold_at": sale.sold_at.isoformat() if sale.sold_at else None,
                "status": sale.status,
                "marketplace_order_id": sale.marketplace_order_id,
                "marketplace_listing_id": sale.marketplace_listing_id,
                "listing_title": sale.listing.title if sale.listing else None,
                "listing_status": getattr(sale.listing.status, "value", sale.listing.status) if sale.listing else None,
                "matched": bool(sale.listing_id),
                "details": sale.details,
                "created_at": sale.created_at.isoformat() if sale.created_at else None,
            }
            for sale in sales
        ],
    }


@router.patch("/{sale_id}/details")
def update_sale_details(
    sale_id: int,
    payload: SaleDetailsUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    ensure_user_owns_resource(current_user, sale.user_id)

    sale.details = {
        **(sale.details or {}),
        "bookkeeping": payload.model_dump(exclude_none=True),
    }
    if sale.listing_id:
        listing = db.get(Listing, sale.listing_id)
        if listing:
            if payload.fees_actual is not None:
                listing.fees_actual = payload.fees_actual
                sale.fees_actual = payload.fees_actual
            if payload.shipping_cost is not None:
                listing.shipping_cost = payload.shipping_cost
                sale.shipping_cost = payload.shipping_cost
            if payload.promotional_fees is not None:
                sale.promotional_fees = payload.promotional_fees
            if payload.marketplace_fees is not None:
                sale.marketplace_fees = payload.marketplace_fees
            if sale.amount is not None:
                sale.profit = round(
                    float(sale.amount or 0)
                    - float(listing.purchase_cost or 0)
                    - float(sale.fees_actual or 0)
                    - float(sale.shipping_cost or 0)
                    - float(sale.promotional_fees or 0)
                    - float(sale.marketplace_fees or 0),
                    2,
                )
                if listing and listing.purchase_cost not in (None, 0):
                    basis = float(listing.purchase_cost or 0) + float(sale.fees_actual or 0) + float(sale.shipping_cost or 0) + float(sale.promotional_fees or 0) + float(sale.marketplace_fees or 0)
                    if basis:
                        sale.roi_percentage = round((float(sale.profit or 0) / basis) * 100.0, 2)
                listing.sale_price = sale.amount
                listing.profit = sale.profit
                listing.roi_percentage = sale.roi_percentage
            db.add(listing)
    db.add(sale)
    db.commit()
    return {"sale_id": sale.id, "status": "updated", "details": sale.details}


@router.post("/{sale_id}/reconcile")
def reconcile_sale(
    sale_id: int,
    payload: SaleReconcileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    ensure_user_owns_resource(current_user, sale.user_id)
    user = db.get(User, sale.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        result = sale_detection_service.reconcile_unmatched_sale(
            db,
            user,
            sale,
            listing_id=payload.listing_id,
            dry_run=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("status") == "unmatched":
        raise HTTPException(status_code=404, detail="No matching listing found for this sale")
    return {"sale_id": sale.id, **result}


@router.get("/settings/{user_id}")
def get_sale_detection_settings(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    user = db.get(User, scoped_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    configured = user.sale_detection_platforms or []
    return {"user_id": scoped_user_id, "marketplaces": configured}


@router.put("/settings/{user_id}")
def update_sale_detection_settings(
    user_id: int,
    payload: SaleDetectionConfigRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    user = db.get(User, scoped_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    invalid = [name for name in payload.marketplaces if name not in MarketplaceName._value2member_map_]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported marketplaces: {', '.join(invalid)}")
    accounts = {
        account.marketplace.value: account
        for account in db.execute(
            select(MarketplaceAccount).where(MarketplaceAccount.user_id == scoped_user_id)
        ).scalars().all()
    }
    blocked = [
        name
        for name in payload.marketplaces
        if not marketplace_status_snapshot(marketplace=name, account=accounts.get(name), user=user)["can_sync_sales"]
    ]
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=f"Sale sync is not available for: {', '.join(blocked)}",
        )
    user.sale_detection_platforms = list(dict.fromkeys(payload.marketplaces))
    db.add(user)
    db.commit()
    return {"user_id": scoped_user_id, "marketplaces": user.sale_detection_platforms}


@router.get("/reports/sales.csv")
def export_sales_csv(
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    sales = db.execute(select(Sale).where(Sale.user_id == scoped_user_id).order_by(Sale.sold_at.desc().nullslast())).scalars().all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["sale_id", "listing_id", "platform", "amount", "currency", "quantity", "fees_actual", "shipping_cost", "promotional_fees", "marketplace_fees", "profit", "roi_percentage", "sold_at", "status"])
    for sale in sales:
        writer.writerow(
            [
                sale.id,
                sale.listing_id or "",
                sale.platform.value,
                sale.amount or 0,
                sale.currency,
                sale.quantity,
                sale.fees_actual or "",
                sale.shipping_cost or "",
                sale.promotional_fees or "",
                sale.marketplace_fees or "",
                sale.profit or "",
                sale.roi_percentage or "",
                sale.sold_at.isoformat() if sale.sold_at else "",
                sale.status,
            ]
        )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="posterpro-sales-report.csv"'},
    )


@router.get("/reports/inventory.csv")
def export_inventory_csv(
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    listings = db.execute(select(Listing).where(Listing.user_id == scoped_user_id).order_by(Listing.updated_at.desc())).scalars().all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["listing_id", "title", "status", "marketplace", "listing_price", "sale_price", "quantity", "updated_at"])
    for listing in listings:
        marketplace = "ebay" if listing.ebay_listing_id else "multi"
        writer.writerow(
            [
                listing.id,
                listing.title or "",
                getattr(listing.status, "value", listing.status),
                marketplace,
                listing.listing_price or listing.suggested_price or 0,
                listing.sale_price or "",
                listing.quantity,
                listing.updated_at.isoformat() if listing.updated_at else "",
            ]
        )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="posterpro-inventory-report.csv"'},
    )


@router.get("/offers/rules/{user_id}")
def get_offer_rules(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    rule = offer_service.get_or_create_rule(db, scoped_user_id)
    return {"user_id": scoped_user_id, "is_enabled": rule.is_enabled, "rules": rule.rules or OfferService.DEFAULT_RULES}


@router.put("/offers/rules/{user_id}")
def update_offer_rules(
    user_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    rule = offer_service.update_rules(
        db,
        user_id=scoped_user_id,
        is_enabled=bool(payload.get("is_enabled")),
        rules=payload.get("rules") or {},
    )
    return {"user_id": scoped_user_id, "is_enabled": rule.is_enabled, "rules": rule.rules or OfferService.DEFAULT_RULES}


@router.post("/offers/send/{user_id}")
def send_offers_now(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    account = db.execute(
        select(MarketplaceAccount).where(MarketplaceAccount.user_id == scoped_user_id, MarketplaceAccount.marketplace == MarketplaceName.ebay)
    ).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="No connected eBay account found")
    result = offer_service.send_personalized_offers(db, account, force=True)
    return {"user_id": scoped_user_id, **result}


@router.get("/offers/history")
def offer_history(
    user_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scoped_user_id = resolve_user_scope(current_user, user_id)
    rows = db.execute(
        select(AutomatedOfferLog)
        .where(AutomatedOfferLog.user_id == scoped_user_id)
        .order_by(AutomatedOfferLog.sent_at.desc().nullslast(), AutomatedOfferLog.id.desc())
        .limit(limit)
    ).scalars().all()
    return {
        "user_id": scoped_user_id,
        "offers": [
            {
                "id": row.id,
                "listing_id": row.listing_id,
                "platform": row.platform,
                "watcher_count": row.watcher_count,
                "offer_percent": row.offer_percent,
                "offer_price": row.offer_price,
                "status": row.status,
                "details": row.details or {},
                "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            }
            for row in rows
        ],
    }
