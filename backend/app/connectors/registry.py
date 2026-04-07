from app.connectors.base import BaseMarketplaceConnector
from app.connectors.depops_connector import DepopConnector
from app.connectors.ebay_connector import EbayConnector
from app.connectors.facebook_marketplace_connector import FacebookMarketplaceConnector
from app.connectors.mercari_connector import MercariConnector
from app.connectors.poshmark_connector import PoshmarkConnector

MARKETPLACE_REGISTRY: dict[str, BaseMarketplaceConnector] = {
    EbayConnector.name: EbayConnector(),
    FacebookMarketplaceConnector.name: FacebookMarketplaceConnector(),
    MercariConnector.name: MercariConnector(),
    PoshmarkConnector.name: PoshmarkConnector(),
    DepopConnector.name: DepopConnector(),
}


def get_connector(name: str) -> BaseMarketplaceConnector:
    connector = MARKETPLACE_REGISTRY.get(name.lower())
    if not connector:
        raise KeyError(f"Unsupported marketplace: {name}")
    return connector
