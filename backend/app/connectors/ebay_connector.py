from __future__ import annotations

from datetime import datetime, UTC

from app.connectors.base import BaseMarketplaceConnector
from app.core.config import settings
from app.models.models import Listing
from app.services.ebay_service import authenticate_user_ebay, get_fulfillment_orders, get_or_refresh_account, publish_listing_to_ebay


class EbayConnector(BaseMarketplaceConnector):
    name = "ebay"
    supports_oauth = True

    async def authenticate(self, user_id: int) -> dict:
        callback = settings.ebay_runame or settings.ebay_redirect_uri or "http://localhost:8000/marketplaces/ebay/callback"
        return {"auth_url": await authenticate_user_ebay(user_id, redirect_uri=callback)}

    async def refresh_tokens(self, user_id: int) -> dict:
        return {"status": "noop", "user_id": user_id}

    async def publish(self, listing: Listing) -> dict:
        # DB persistence happens inside ebay service flow.
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            fresh = db.get(Listing, listing.id)
            return await publish_listing_to_ebay(fresh, db)

    async def update(self, listing: Listing) -> dict:
        return {"status": "not_implemented", "listing_id": listing.id}

    async def delete(self, listing: Listing) -> dict:
        return {"status": "not_implemented", "listing_id": listing.id}

    async def fetch_status(self, listing: Listing) -> dict:
        return {"status": (listing.marketplace_data or {}).get("ebay_status") or listing.ebay_publish_status}

    def to_marketplace_payload(self, listing: Listing) -> dict:
        return {
            "title": listing.title,
            "description": listing.description,
            "price": listing.suggested_price,
            "category": listing.category_suggestion,
        }

    async def poll_sales(self, user_id: int, since: str | None = None) -> list[dict]:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            account = await get_or_refresh_account(user_id, db)
            filter_expression = None
            if since:
                try:
                    normalized_since = datetime.fromisoformat(str(since).replace("Z", "+00:00")).astimezone(UTC)
                    filter_expression = f"creationdate:[{normalized_since.strftime('%Y-%m-%dT%H:%M:%SZ')}..]"
                except ValueError:
                    filter_expression = None
            orders = await get_fulfillment_orders(account, filter_expression=filter_expression)
            events: list[dict] = []
            for order in orders:
                line_items = order.get("lineItems") or []
                for item in line_items:
                    events.append(
                        {
                            "marketplace": self.name,
                            "marketplace_order_id": order.get("orderId"),
                            "marketplace_listing_id": item.get("legacyItemId"),
                            "amount": (item.get("lineItemCost") or {}).get("value"),
                            "currency": (item.get("lineItemCost") or {}).get("currency") or "USD",
                            "sold_at": order.get("creationDate"),
                            "quantity": item.get("quantity", 1),
                            "raw": {"order": order, "item": item},
                        }
                    )
            return events
