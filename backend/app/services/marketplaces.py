from abc import ABC, abstractmethod


class MarketplaceAdapter(ABC):
    name: str

    @abstractmethod
    def publish(self, listing: dict) -> dict:
        raise NotImplementedError


class EbayAdapter(MarketplaceAdapter):
    name = "ebay"

    def publish(self, listing: dict) -> dict:
        return {"external_listing_id": f"EBAY-{listing['id']}", "status": "posted"}


MARKETPLACE_REGISTRY = {
    EbayAdapter.name: EbayAdapter(),
}
