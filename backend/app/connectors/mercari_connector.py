from app.connectors.fallback_connector import ProxyAutomationConnector
from app.connectors.capabilities import MarketplaceCapabilities


class MercariConnector(ProxyAutomationConnector):
    name = "mercari"
    # Mercari's current policy prohibits automated interfaces not provided by
    # Mercari. Keep PosterPro's draft mapper, but do not advertise a browser
    # extension as an authorized create/update/end provider.
    capabilities = MarketplaceCapabilities()

    async def publish(self, listing):
        return {
            "status": "MANUAL_WORKFLOW_REQUIRED",
            "marketplace": self.name,
            "listing_id": listing.id,
            "reason": "No authorized Mercari publishing API/provider is configured.",
        }
