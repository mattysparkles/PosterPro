import asyncio

from app.connectors.registry import MARKETPLACE_REGISTRY
from app.connectors import ebay_connector
from app.models.enums import ListingStatus
from app.models.models import Listing, User


REQUIRED_METHODS = ["authenticate", "refresh_tokens", "publish", "update", "delete", "fetch_status"]


class DummyListing:
    id = 1
    cluster_id = 10
    title = "Demo"
    description = "Desc"
    suggested_price = 10


def test_connector_interface_compliance(monkeypatch, db_session):
    # This contract test must not execute a real eBay service/database workflow.
    monkeypatch.setattr(ebay_connector, "publish_listing_to_ebay", lambda *_args, **_kwargs: asyncio.sleep(0, result={"status": "adapter_called"}))
    owner = User(email="connector-interface@example.com")
    db_session.add(owner)
    db_session.flush()
    listing = Listing(id=1, user_id=owner.id, status=ListingStatus.draft, title="Demo", description="Desc")
    db_session.add(listing)
    db_session.commit()
    for name, connector in MARKETPLACE_REGISTRY.items():
        for method in REQUIRED_METHODS:
            assert callable(getattr(connector, method))

        result = asyncio.run(connector.publish(listing))
        assert isinstance(result, dict)
        assert "status" in result
        assert connector.name == name
