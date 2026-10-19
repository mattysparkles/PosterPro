from __future__ import annotations

from app.connectors.base import BaseMarketplaceConnector
from app.connectors.capabilities import ASSISTED_MARKETPLACE
from app.models.models import Listing
from app.services.marketplace_field_mapper import build_marketplace_payload


class ProxyAutomationConnector(BaseMarketplaceConnector):
    """Destination mapper for channels whose execution is browser-assisted.

    Publishing is intentionally *not* faked here. Durable extension jobs are
    created by the authenticated marketplace-job API.
    """

    capabilities = ASSISTED_MARKETPLACE

    async def authenticate(self, user_id: int) -> dict:
        return {
            "status": "manual_required",
            "message": f"{self.name} uses the PosterPro manual/provider-assisted setup path today.",
            "user_id": user_id,
            "settings_route": f"/settings?tab=marketplaces&marketplace={self.name}",
        }

    async def refresh_tokens(self, user_id: int) -> dict:
        return {"status": "AUTH_REQUIRED", "marketplace": self.name, "user_id": user_id}

    async def publish(self, listing: Listing) -> dict:
        return {
            "status": "EXTENSION_JOB_REQUIRED",
            "marketplace": self.name,
            "listing_id": listing.id,
            "capabilities": self.get_capabilities(),
        }

    async def update(self, listing: Listing) -> dict:
        return {"status": "EXTENSION_JOB_REQUIRED", "action": "UPDATE", "marketplace": self.name, "listing_id": listing.id}

    async def delete(self, listing: Listing) -> dict:
        return {"status": "EXTENSION_JOB_REQUIRED", "action": "END", "marketplace": self.name, "listing_id": listing.id}

    async def fetch_status(self, listing: Listing) -> dict:
        return {"status": "UNSUPPORTED_EXTERNAL_STATUS", "marketplace": self.name, "listing_id": listing.id}

    async def poll_sales(self, user_id: int, since: str | None = None) -> list[dict]:
        return []

    def to_marketplace_payload(self, listing: Listing) -> dict:
        return build_marketplace_payload(listing, self.name)
