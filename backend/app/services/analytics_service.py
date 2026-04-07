from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.models import CategoryStat, DailyStat, Listing, Sale


class AnalyticsService:
    def compute_overview(self, db: Session, user_id: int) -> dict:
        listings = db.execute(select(Listing).where(Listing.user_id == user_id)).scalars().all()
        sold = [l for l in listings if l.sale_price is not None]

        total_revenue = round(sum((l.sale_price or 0) for l in sold), 2)
        total_profit = round(sum((l.profit or 0) for l in sold), 2)
        invested = sum((l.purchase_cost or 0) + (l.fees_actual or l.fees_estimated or 0) + (l.shipping_cost or 0) for l in sold)
        roi = round((total_profit / invested) * 100, 2) if invested > 0 else 0.0

        total_listed = len(listings)
        sell_through_rate = round((len(sold) / total_listed) * 100, 2) if total_listed else 0.0

        durations = [
            (l.sold_at - l.created_at).days
            for l in sold
            if l.sold_at and l.created_at
        ]
        avg_days_to_sell = round(sum(durations) / len(durations), 2) if durations else 0.0

        avg_sale_to_listing = round(
            sum((l.sale_price or 0) / max((l.listing_price or l.suggested_price or 1), 1) for l in sold) / len(sold),
            3,
        ) if sold else 0.0

        keyword_counter = Counter()
        for l in sold:
            keyword_counter.update((l.tags or []))

        category_map: dict[str, list[Listing]] = {}
        for l in listings:
            key = l.category_suggestion or "Uncategorized"
            category_map.setdefault(key, []).append(l)

        category_performance = []
        for category, cat_listings in category_map.items():
            cat_sold = [l for l in cat_listings if l.sale_price is not None]
            category_performance.append(
                {
                    "category": category,
                    "total_listed": len(cat_listings),
                    "total_sold": len(cat_sold),
                    "sell_through_rate": round((len(cat_sold) / len(cat_listings)) * 100, 2) if cat_listings else 0.0,
                    "avg_sale_price": round(sum((l.sale_price or 0) for l in cat_sold) / len(cat_sold), 2) if cat_sold else 0.0,
                }
            )

        return {
            "total_revenue": total_revenue,
            "total_profit": total_profit,
            "roi_percentage": roi,
            "sell_through_rate": sell_through_rate,
            "avg_days_to_sell": avg_days_to_sell,
            "avg_sale_price_vs_listing_price": avg_sale_to_listing,
            "category_performance": sorted(category_performance, key=lambda x: x["sell_through_rate"], reverse=True),
            "keyword_performance": keyword_counter.most_common(10),
        }

    def store_daily_stats(self, db: Session, user_id: int) -> DailyStat:
        overview = self.compute_overview(db, user_id)
        stat = db.execute(
            select(DailyStat).where(DailyStat.user_id == user_id, DailyStat.stat_date == date.today())
        ).scalar_one_or_none()
        if not stat:
            stat = DailyStat(user_id=user_id, stat_date=date.today())
            db.add(stat)

        stat.total_revenue = overview["total_revenue"]
        stat.total_profit = overview["total_profit"]
        stat.roi_percentage = overview["roi_percentage"]
        stat.sell_through_rate = overview["sell_through_rate"]
        stat.avg_days_to_sell = overview["avg_days_to_sell"]

        for c in overview["category_performance"]:
            row = db.execute(
                select(CategoryStat).where(CategoryStat.user_id == user_id, CategoryStat.category == c["category"])
            ).scalar_one_or_none()
            if not row:
                row = CategoryStat(user_id=user_id, category=c["category"])
                db.add(row)
            row.total_listed = c["total_listed"]
            row.total_sold = c["total_sold"]
            row.avg_sale_price = c["avg_sale_price"]
            row.sell_through_rate = c["sell_through_rate"]

        db.commit()
        db.refresh(stat)
        return stat

    def listing_detail(self, db: Session, listing_id: int) -> dict:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")

        is_sold = listing.sale_price is not None
        days_live = (listing.sold_at.date() if listing.sold_at else date.today()) - listing.created_at.date()
        return {
            "listing_id": listing.id,
            "title": listing.title,
            "is_sold": is_sold,
            "days_live": days_live.days,
            "profit": listing.profit,
            "roi_percentage": listing.roi_percentage,
            "sale_price": listing.sale_price,
            "listing_price": listing.listing_price or listing.suggested_price,
            "category": listing.category_suggestion,
            "keywords": listing.tags or [],
        }

    def dashboard(self, db: Session, user_id: int, days: int = 30) -> dict:
        days = max(7, min(days, 365))
        now = datetime.utcnow()
        start = now - timedelta(days=days)
        sales = db.execute(
            select(Sale).where(Sale.user_id == user_id, Sale.sold_at.is_not(None), Sale.sold_at >= start)
        ).scalars().all()
        listings = db.execute(select(Listing).where(Listing.user_id == user_id)).scalars().all()

        top_items = sorted(
            [
                {
                    "listing_id": listing.id,
                    "title": listing.title or f"Listing #{listing.id}",
                    "units_sold": len([sale for sale in sales if sale.listing_id == listing.id]),
                    "revenue": round(sum(float(sale.amount or 0) for sale in sales if sale.listing_id == listing.id), 2),
                }
                for listing in listings
            ],
            key=lambda row: (row["revenue"], row["units_sold"]),
            reverse=True,
        )[:8]

        platform_rows = db.execute(
            select(Sale.platform, func.count(Sale.id), func.sum(Sale.amount))
            .where(Sale.user_id == user_id, Sale.sold_at.is_not(None), Sale.sold_at >= start)
            .group_by(Sale.platform)
        ).all()
        revenue_by_marketplace = [
            {"platform": platform.value, "sales_count": int(count or 0), "revenue": round(float(revenue or 0), 2)}
            for platform, count, revenue in platform_rows
        ]
        revenue_by_marketplace.sort(key=lambda row: row["revenue"], reverse=True)

        trend_map: dict[str, float] = {}
        for idx in range(days):
            current = (start + timedelta(days=idx)).date().isoformat()
            trend_map[current] = 0.0
        for sale in sales:
            sold_day = sale.sold_at.date().isoformat() if sale.sold_at else None
            if sold_day in trend_map:
                trend_map[sold_day] += float(sale.amount or 0)
        sales_trend = [{"date": key, "revenue": round(value, 2)} for key, value in trend_map.items()]

        sold_listing_ids = {sale.listing_id for sale in sales if sale.listing_id is not None}
        total_revenue = round(sum(float(sale.amount or 0) for sale in sales), 2)
        units_sold = int(sum(int(sale.quantity or 1) for sale in sales))
        return {
            "period_days": days,
            "kpis": {
                "total_revenue": total_revenue,
                "total_sales": len(sales),
                "units_sold": units_sold,
                "avg_order_value": round(total_revenue / len(sales), 2) if sales else 0.0,
                "active_listings": len([listing for listing in listings if listing.status in {"ready", "posted"}]),
                "sold_unique_items": len(sold_listing_ids),
            },
            "top_items": [row for row in top_items if row["units_sold"] > 0 or row["revenue"] > 0],
            "revenue_by_marketplace": revenue_by_marketplace,
            "sales_trend": sales_trend,
        }
