from pathlib import Path

from app.browser_runner import BrowserRunnerConfig, MARKETPLACE_BROWSER_SPECS, MarketplaceBrowserRunner


def test_generated_screenshot_is_persisted_as_asset(tmp_path: Path):
    captured = {}

    def fake_asset_persistor(data: bytes, content_type: str | None, file_name: str | None, source_url: str | None):
        captured["data"] = data
        captured["content_type"] = content_type
        captured["file_name"] = file_name
        captured["source_url"] = source_url
        return {
            "asset_id": "asset-123",
            "file_name": file_name,
            "content_type": content_type,
            "download_path": "/assets/asset-123",
        }

    runner = MarketplaceBrowserRunner(
        BrowserRunnerConfig(asset_persistor=fake_asset_persistor),
        MARKETPLACE_BROWSER_SPECS["facebook"],
    )
    screenshot = tmp_path / "example.png"
    screenshot.write_bytes(b"png-bytes")

    result = runner._persist_generated_asset(screenshot)

    assert result["asset_id"] == "asset-123"
    assert captured["data"] == b"png-bytes"
    assert captured["content_type"] == "image/png"
    assert captured["file_name"] == "example.png"
    assert captured["source_url"] is None
