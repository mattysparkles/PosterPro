from app.connectors.fallback_connector import ProxyAutomationConnector
from app.connectors.capabilities import MarketplaceCapabilities


class MercariConnector(ProxyAutomationConnector):
    name = "mercari"
    # Mercari's current US terms do not permit browser automation. PosterPro
    # can prepare listing packets for manual operator completion only.
    capabilities = MarketplaceCapabilities()
