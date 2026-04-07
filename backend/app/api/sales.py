from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import SaleDetailsUpdateRequest, SaleDetectionConfigRequest
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import AutomatedOfferLog, Listing, MarketplaceAccount, OfferAutomationRule, Sale, User
from app.services.offer_service import OfferService

router = APIRouter(prefix="/sales", tags=["sales"])
offer_service = OfferService()


@router.get("/dashboard")
def sales_dashboard(user_id: int = Query(1), limit: int = Query(50, ge=1, le=250), db: Session = Depends(get_db)):
    sales = db.execute(
        select(Sale).where(Sale.user_id == user_id).order_by(Sale.sold_at.desc().nullslast(), Sale.id.desc()).limit(limit)
    ).scalars().all()
    gross_sales = sum(float(s.amount or 0.0) for s in sales)
    units = sum(int(s.quantity or 1) for s in sales)
    by_platform = {
        row[0].value: {"count": row[1], "gross": float(row[2] or 0)}
        for row in db.execute(
            select(Sale.platform, func.count(Sale.id), func.sum(Sale.amount)).where(Sale.user_id == user_id).group_by(Sale.platform)
        ).all()
    }
    return {
        "user_id": user_id,
        "summary": {"total_sales": len(sales), "units": units, "gross": gross_sales, "by_platform": by_platform},
        "sales": [
            {
                "id": sale.id,
                "listing_id": sale.listing_id,
                "platform": sale.platform.value,
                "amount": sale.amount,
                "currency": sale.currency,
                "quantity": sale.quantity,
                "sold_at": sale.sold_at.isoformat() if sale.sold_at else None,
                "status": sale.status,
                "marketplace_order_id": sale.marketplace_order_id,
                "marketplace_listing_id": sale.marketplace_listing_id,
                "details": sale.details,
                "created_at": sale.created_at.isoformat() if sale.created_at else None,
            }
            for sale in sales
        ],
    }


@router.patch("/{sale_id}/details")
def update_sale_details(sale_id: int, payload: SaleDetailsUpdateRequest, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")

    sale.details = {
        **(sale.details or {}),
        "bookkeeping": payload.model_dump(exclude_none=True),
    }
    if sale.listing_id:
        listing = db.get(Listing, sale.listing_id)
        if listing:
            if payload.fees_actual is not None:
                listing.fees_actual = payload.fees_actual
            if payload.shipping_cost is not None:
                listing.shipping_cost = payload.shipping_cost
            db.add(listing)
    db.add(sale)
    db.commit()
    return {"sale_id": sale.id, "status": "updated", "details": sale.details}


@router.get("/settings/{user_id}")
def get_sale_detection_settings(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    configured = user.sale_detection_platforms or [m.value for m in MarketplaceName]
    return {"user_id": user_id, "marketplaces": configured}


@router.put("/settings/{user_id}")
def update_sale_detection_settings(user_id: int, payload: SaleDetectionConfigRequest, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    invalid = [name for name in payload.marketplaces if name not in MarketplaceName._value2member_map_]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported marketplaces: {', '.join(invalid)}")
    user.sale_detection_platforms = list(dict.fromkeys(payload.marketplaces))
    db.add(user)
    db.commit()
    return {"user_id": user_id, "marketplaces": user.sale_detection_platforms}


@router.get("/reports/sales.csv")
def export_sales_csv(user_id: int = Query(1), db: Session = Depends(get_db)):
    sales = db.execute(select(Sale).where(Sale.user_id == user_id).order_by(Sale.sold_at.desc().nullslast())).scalars().all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["sale_id", "listing_id", "platform", "amount", "currency", "quantity", "sold_at", "status"])
    for sale in sales:
        writer.writerow(
            [
                sale.id,
                sale.listing_id or "",
                sale.platform.value,
                sale.amount or 0,
                sale.currency,
                sale.quantity,
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
def export_inventory_csv(user_id: int = Query(1), db: Session = Depends(get_db)):
    listings = db.execute(select(Listing).where(Listing.user_id == user_id).order_by(Listing.updated_at.desc())).scalars().all()
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
def get_offer_rules(user_id: int, db: Session = Depends(get_db)):
    rule = offer_service.get_or_create_rule(db, user_id)
    return {"user_id": user_id, "is_enabled": rule.is_enabled, "rules": rule.rules or OfferService.DEFAULT_RULES}


@router.put("/offers/rules/{user_id}")
def update_offer_rules(user_id: int, payload: dict, db: Session = Depends(get_db)):
    rule = offer_service.update_rules(
        db,
        user_id=user_id,
        is_enabled=bool(payload.get("is_enabled")),
        rules=payload.get("rules") or {},
    )
    return {"user_id": user_id, "is_enabled": rule.is_enabled, "rules": rule.rules or OfferService.DEFAULT_RULES}


@router.post("/offers/send/{user_id}")
def send_offers_now(user_id: int, db: Session = Depends(get_db)):
    account = db.execute(
        select(MarketplaceAccount).where(MarketplaceAccount.user_id == user_id, MarketplaceAccount.marketplace == MarketplaceName.ebay)
    ).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="No connected eBay account found")
    result = offer_service.send_personalized_offers(db, account, force=True)
    return {"user_id": user_id, **result}


@router.get("/offers/history")
def offer_history(user_id: int = Query(1), limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.execute(
        select(AutomatedOfferLog)
        .where(AutomatedOfferLog.user_id == user_id)
        .order_by(AutomatedOfferLog.sent_at.desc().nullslast(), AutomatedOfferLog.id.desc())
        .limit(limit)
    ).scalars().all()
    return {
        "user_id": user_id,
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
