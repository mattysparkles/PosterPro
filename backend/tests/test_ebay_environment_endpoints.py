from app.core.config import reload_settings, settings
from app.services.ebay_service import EbayAPIClient
from app.services import ebay_service
from types import SimpleNamespace
import pytest


def test_ebay_api_client_uses_production_base_url_in_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    client = EbayAPIClient("token")
    assert client.base_url == "https://api.ebay.com"


def test_ebay_api_client_uses_sandbox_base_url_outside_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    client = EbayAPIClient("token")
    assert client.base_url == "https://api.sandbox.ebay.com"


def test_reload_settings_preserves_singleton_references(monkeypatch):
    reference = settings
    previous = reference.environment
    monkeypatch.setenv("ENVIRONMENT", "production")
    refreshed = reload_settings()
    assert refreshed is reference
    assert reference.environment == "production"
    # Do not leak the in-place mutation into tests that follow this module.
    reference.environment = previous


@pytest.mark.anyio
async def test_condition_policy_lookup_is_category_filtered_and_read_only(monkeypatch):
    calls = []

    class Client:
        def __init__(self, token):
            assert token == "operator-token"

        async def request(self, method, path, *, params, headers):
            calls.append((method, path, params, headers))
            return {"itemConditionPolicies": [{
                "categoryId": "123",
                "itemConditionRequired": True,
                "itemConditions": [{"conditionId": 3000, "conditionDescription": "Used"}],
            }]}

    monkeypatch.setattr(ebay_service, "EbayAPIClient", Client)
    policy = await ebay_service.get_ebay_item_condition_policies("operator-token", "123")
    assert policy["itemConditionRequired"] is True
    assert calls == [(
        "GET",
        "/sell/metadata/v1/marketplace/EBAY_US/get_item_condition_policies",
        {"filter": "categoryIds:{123}"},
        {"Accept-Encoding": "gzip"},
    )]


def test_condition_policy_summary_exposes_provider_values_without_guessing_mapping():
    policy = {
        "categoryId": "123",
        "itemConditionRequired": True,
        "itemConditions": [{"conditionId": 3000, "conditionDescription": "Used"}],
    }
    listing = SimpleNamespace(
        condition="Used", condition_data={"condition_bucket": "used_good"},
    )
    summary = ebay_service.summarize_ebay_condition_policy(policy, listing)
    assert summary["supported_conditions"] == [{
        "condition_id": 3000, "description": "Used", "help_text": None,
    }]
    assert "operator_review_required" in summary["compatibility_assessment"]
