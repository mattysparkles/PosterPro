import pytest


@pytest.mark.anyio
async def test_health_route_returns(async_client):
    response = await async_client.get("/health")
    assert response.status_code in {200, 503}

