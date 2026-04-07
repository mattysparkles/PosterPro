from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.models import Listing


class BaseMarketplaceConnector(ABC):
    name: str
    supports_oauth: bool = False

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
