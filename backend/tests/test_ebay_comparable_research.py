import pytest
from types import SimpleNamespace

from app.services import ebay_comparable_research as research_service


@pytest.mark.anyio
async def test_browse_search_records_active_asking_provenance_and_shipping(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        def __init__(self, payload): self._payload = payload
        def json(self): return self._payload

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def post(self, url, **kwargs):
            calls.append(("token", url, kwargs.get("data")))
            return Response({"access_token": "test-app-token", "expires_in": 3600})
        async def get(self, url, **kwargs):
            calls.append(("browse", url, kwargs.get("params"), kwargs.get("headers")))
            return Response({"itemSummaries": [{
                "itemId": "v1|item-1|0", "title": "Acme Widget Model 123",
                "itemWebUrl": "https://www.ebay.com/itm/123", "categoryPath": "Home/Widgets",
                "condition": "Used", "price": {"value": "45.00", "currency": "USD"},
                "shippingOptions": [{"shippingCost": {"value": "7.50", "currency": "USD"}}],
            }]})

    monkeypatch.setattr(research_service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(research_service, "reload_settings", lambda: SimpleNamespace(
        ebay_client_id="client", ebay_client_secret="secret", environment="production",
    ))
    async def no_wait(*_args, **_kwargs): return None
    monkeypatch.setattr(research_service.rate_limiter, "acquire_async", no_wait)

    listing = SimpleNamespace(title="Acme Widget", item_specifics={"Brand": "Acme", "MPN": "123"}, source_metadata={})
    result = await research_service.search_ebay_active_comparables(listing)

    assert result["status"] == "results"
    assert result["sold_comparable_count"] == 0
    assert result["active_asking_count"] == 1
    comp = result["comparables"][0]
    assert comp["comp_type"] == "active"
    assert comp["sold_status"] == "asking_active"
    assert comp["price"] == 45.0
    assert comp["shipping_price"] == 7.5
    assert comp["shipping_inclusive"] is True
    assert comp["source_record_id"] == "v1|item-1|0"
    assert calls[1][2]["q"] == "Acme 123"


@pytest.mark.anyio
async def test_browse_search_does_not_invent_shipping_or_search_generic_placeholder(monkeypatch):
    generic = SimpleNamespace(title="Head Slate intake Unknown item", item_specifics={}, source_metadata={})
    result = await research_service.search_ebay_active_comparables(generic)
    assert result["status"] == "insufficient_identity"
    assert result["comparables"] == []

    class Response:
        status_code = 200
        def __init__(self, payload): self._payload = payload
        def json(self): return self._payload

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def post(self, *_args, **_kwargs): return Response({"access_token": "token"})
        async def get(self, *_args, **_kwargs): return Response({"itemSummaries": [{
            "itemId": "active-2", "title": "Acme Widget Model 123", "price": {"value": "12.00", "currency": "USD"},
        }]})

    monkeypatch.setattr(research_service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(research_service, "reload_settings", lambda: SimpleNamespace(
        ebay_client_id="client", ebay_client_secret="secret", environment="production",
    ))
    async def no_wait(*_args, **_kwargs): return None
    monkeypatch.setattr(research_service.rate_limiter, "acquire_async", no_wait)
    listing = SimpleNamespace(title="Acme Widget Model 123", item_specifics={}, source_metadata={})
    result = await research_service.search_ebay_active_comparables(listing)
    comp = result["comparables"][0]
    assert comp["shipping_price"] is None
    assert comp["shipping_inclusive"] is False
