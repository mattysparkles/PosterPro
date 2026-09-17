from app.core.config import reload_settings, settings
from app.services.ebay_service import EbayAPIClient


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
    monkeypatch.setenv("ENVIRONMENT", "production")
    refreshed = reload_settings()
    assert refreshed is reference
    assert reference.environment == "production"
