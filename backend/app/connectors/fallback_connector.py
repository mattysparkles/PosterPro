from __future__ import annotations

from app.connectors.base import BaseMarketplaceConnector
from app.models.models import Listing
from app.services.marketplace_field_mapper import build_marketplace_payload


class ProxyAutomationConnector(BaseMarketplaceConnector):
    """Connector template for marketplaces with limited/no official public listing APIs.

    Uses a placeholder API2Cart/browser-automation strategy.
    """

    proxy_provider = "api2cart"

    async def authenticate(self, user_id: int) -> dict:
        return {
            "status": "manual_required",
            "message": f"{self.name} uses the PosterPro manual/provider-assisted setup path today.",
            "user_id": user_id,
            "settings_route": f"/settings?tab=marketplaces&marketplace={self.name}",
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

    async def poll_sales(self, user_id: int, since: str | None = None) -> list[dict]:
        return [
            {
                "marketplace": self.name,
                "status": "stub",
                "message": f"{self.name} sale polling stubbed pending official integration",
                "user_id": user_id,
                "since": since,
            }
        ]

    def to_marketplace_payload(self, listing: Listing) -> dict:
        return build_marketplace_payload(listing, self.name)
