from __future__ import annotations

from app.connectors.base import BaseMarketplaceConnector
from app.models.models import Listing
from app.services.ebay_service import authenticate_user_ebay, publish_listing_to_ebay


class EbayConnector(BaseMarketplaceConnector):
    name = "ebay"
    supports_oauth = True

    async def authenticate(self, user_id: int) -> dict:
        return {"auth_url": await authenticate_user_ebay(user_id, redirect_uri="http://localhost:8000/marketplaces/ebay/callback")}

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
