import asyncio

from app.connectors.registry import MARKETPLACE_REGISTRY


REQUIRED_METHODS = ["authenticate", "refresh_tokens", "publish", "update", "delete", "fetch_status"]


class DummyListing:
    id = 1
    cluster_id = 10
    title = "Demo"
    description = "Desc"
    suggested_price = 10


def test_connector_interface_compliance():
    listing = DummyListing()
    for name, connector in MARKETPLACE_REGISTRY.items():
        for method in REQUIRED_METHODS:
            assert callable(getattr(connector, method))

        result = asyncio.run(connector.publish(listing))
        assert isinstance(result, dict)
        assert "status" in result
        assert connector.name == name
