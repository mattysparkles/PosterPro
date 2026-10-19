from uuid import uuid4

import pytest

from app.core import database as database_module
from app.models.models import Listing


@pytest.mark.anyio
async def test_listing_diagnostics_is_admin_only_and_bounded(async_client):
    admin = await async_client.post("/auth/register", json={"full_name": "Diag Admin", "email": f"diag-admin-{uuid4()}@example.com", "password": "supersecret123"})
    assert admin.status_code == 201
    db = database_module.SessionLocal(); row = Listing(user_id=admin.json()["user"]["id"], title="Diagnostic item", description="A safe draft", status="draft", source_type="amazon_vine", source_metadata={"asin": "BTEST"}); db.add(row); db.commit(); db.refresh(row); listing_id = row.id; db.close()
    response = await async_client.post("/admin/listing-diagnostics", json={"listing_ids": [listing_id, listing_id, 999999], "run_fresh_preflight": False})
    assert response.status_code == 200
    body = response.json(); assert body["count"] == 2
    assert body["items"][0]["listing_id"] == listing_id
    assert body["items"][1]["status"] == "NOT_FOUND"
    assert "access_token" not in str(body) and "refresh_token" not in str(body)

    user = await async_client.post("/auth/register", json={"full_name": "Diag User", "email": f"diag-user-{uuid4()}@example.com", "password": "supersecret123"})
    assert user.status_code == 201
    denied = await async_client.post("/admin/listing-diagnostics", json={"listing_ids": [listing_id], "run_fresh_preflight": False})
    assert denied.status_code == 403
