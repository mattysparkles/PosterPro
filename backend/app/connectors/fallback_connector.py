from __future__ import annotations

from app.connectors.base import BaseMarketplaceConnector
from app.models.models import Listing


class ProxyAutomationConnector(BaseMarketplaceConnector):
    """Connector template for marketplaces with limited/no official public listing APIs.

    Uses a placeholder API2Cart/browser-automation strategy.
    """

    proxy_provider = "api2cart"

    async def authenticate(self, user_id: int) -> dict:
        return {
            "status": "manual_required",
            "message": f"{self.name} requires unified provider credentials or automation service login.",
            "user_id": user_id,
        }

    async def refresh_tokens(self, user_id: int) -> dict:
        return {"status": "noop", "provider": self.proxy_provider, "user_id": user_id}

    async def publish(self, listing: Listing) -> dict:
        payload = self.to_marketplace_payload(listing)
        return {
            "status": "QUEUED_AUTOMATION",
            "external_listing_id": f"{self.name.upper()}-{listing.id}",
            "submitted_payload": payload,
        }

    async def update(self, listing: Listing) -> dict:
        return {"status": "QUEUED_AUTOMATION", "listing_id": listing.id}

    async def delete(self, listing: Listing) -> dict:
        return {"status": "QUEUED_AUTOMATION", "listing_id": listing.id}

    async def fetch_status(self, listing: Listing) -> dict:
        return {"status": "PENDING_PROVIDER_SYNC", "listing_id": listing.id}

    def to_marketplace_payload(self, listing: Listing) -> dict:
        return {
            "headline": listing.title,
            "details": listing.description,
            "amount": listing.suggested_price,
            "metadata": {"cluster_id": listing.cluster_id},
        }
