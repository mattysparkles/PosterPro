from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.models import Listing
from app.connectors.capabilities import MarketplaceCapabilities


class BaseMarketplaceConnector(ABC):
    name: str
    supports_oauth: bool = False
    capabilities = MarketplaceCapabilities()

    def prepare_listing(self, listing: Listing) -> dict[str, Any]:
        """Build the destination-specific projection without claiming to publish."""
        return self.to_marketplace_payload(listing)

    def validate_listing(self, listing: Listing) -> list[dict[str, str]]:
        payload = self.prepare_listing(listing)
        errors = []
        for field in ("title", "description", "price", "image_urls"):
            if not payload.get(field):
                errors.append({"field": field, "code": "REQUIRED_FOR_MARKETPLACE"})
        return errors

    def get_capabilities(self) -> dict[str, bool]:
        return self.capabilities.as_dict()

    async def create_listing(self, listing: Listing) -> dict[str, Any]:
        return await self.publish(listing)

    async def update_listing(self, listing: Listing) -> dict[str, Any]:
        return await self.update(listing)

    async def end_listing(self, listing: Listing) -> dict[str, Any]:
        return await self.delete(listing)

    @abstractmethod
    async def authenticate(self, user_id: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def refresh_tokens(self, user_id: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def publish(self, listing: Listing) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def update(self, listing: Listing) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, listing: Listing) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_status(self, listing: Listing) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def to_marketplace_payload(self, listing: Listing) -> dict[str, Any]:
        raise NotImplementedError

    async def poll_sales(self, user_id: int, since: str | None = None) -> list[dict[str, Any]]:
        """Fetch sold-order events from the marketplace."""
        return []
