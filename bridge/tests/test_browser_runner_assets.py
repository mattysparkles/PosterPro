from pathlib import Path

import pytest

from app.browser_runner import AmazonMarketplaceBrowserRunner, BrowserRunnerConfig, BrowserRunnerError, MARKETPLACE_BROWSER_SPECS, MarketplaceBrowserRunner


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


def test_amazon_asin_capture_records_exact_page_and_gallery_evidence(tmp_path: Path):
    class Locator:
        first = None
        def __init__(self): self.first = self
        def inner_text(self, timeout=None): raise RuntimeError("variation not present")

    class Title:
        def inner_text(self): return "Exact Product Title"

    class Page:
        url = "https://www.amazon.com/dp/B000000001"
        def goto(self, url, wait_until=None): self.url = url
        def wait_for_timeout(self, _milliseconds): pass
        def evaluate(self, _script): return ["https://m.media-amazon.com/images/I/exact.jpg"]
        def content(self): return ""
        def query_selector(self, _selector): return Title()
        def locator(self, _selector): return Locator()
        def close(self): pass

    class Context:
        def new_page(self): return Page()

    runner = AmazonMarketplaceBrowserRunner(BrowserRunnerConfig(screenshots_dir=tmp_path))
    capture = runner._capture_amazon_product_by_asin(Context(), asin="B000000001")
    assert capture["page_asin"] == "B000000001"
    assert capture["title"] == "Exact Product Title"
    assert capture["image_urls"] == ["https://m.media-amazon.com/images/I/exact.jpg"]
    assert capture["gallery_evidence"][0]["page_asin"] == "B000000001"


def test_amazon_public_asin_capture_records_http_error_without_auth_fallback(tmp_path: Path):
    class Response:
        status = 503

    class Page:
        url = "https://www.amazon.com/error/500"
        def goto(self, url, wait_until=None):
            self.url = "https://www.amazon.com/error/500"
            return Response()
        def close(self): pass

    class Context:
        def new_page(self): return Page()

    runner = AmazonMarketplaceBrowserRunner(BrowserRunnerConfig(screenshots_dir=tmp_path))
    with pytest.raises(BrowserRunnerError, match="public product page returned HTTP 503"):
        runner._capture_amazon_product_by_asin(Context(), asin="B000000001")


def test_vine_recovery_uses_product_title_before_source_asin(tmp_path: Path, monkeypatch):
    calls = []

    class Browser:
        def close(self): pass

    class Chromium:
        def launch(self, headless=True): return Browser()

    class Playwright:
        chromium = Chromium()
        def __enter__(self): return self
        def __exit__(self, *_args): pass

    runner = AmazonMarketplaceBrowserRunner(BrowserRunnerConfig(screenshots_dir=tmp_path))
    monkeypatch.setattr(runner, "_load_playwright", lambda: Playwright())
    monkeypatch.setattr(runner, "_new_context", lambda *_args: object())

    def title_search(_context, *, title_hint):
        calls.append(("title", title_hint))
        return {
            "source_url": "https://www.amazon.com/dp/B000000002",
            "image_urls": ["https://m.media-amazon.com/images/I/product.jpg"],
            "title": title_hint,
            "page_asin": "B000000002",
            "identity_status": "title_tokens_verified",
            "gallery_evidence": [],
        }

    def asin_lookup(*_args, **_kwargs):
        calls.append(("asin", None))
        raise AssertionError("recovery must not navigate to the source ASIN first")

    monkeypatch.setattr(runner, "_capture_amazon_product_by_title", title_search)
    monkeypatch.setattr(runner, "_capture_amazon_product_by_asin", asin_lookup)
    result = runner.run_import(
        job_id="test-job", bridge_account=None,
        payload={"operation": "vine_image_gallery_recovery", "asin": "B000000001", "payload": {"product_name": "Exact Brand Model 16.9 oz Product"}},
    )
    record = result["imported_listings"][0]
    assert calls == [("title", "Exact Brand Model 16.9 oz Product")]
    assert record["page_asin"] == "B000000002"
    assert record["asin_cross_check"] == "not_matched_title_identity_used"
    assert record["identity_status"] == "title_tokens_verified"


def test_title_identity_match_requires_variant_tokens():
    runner = AmazonMarketplaceBrowserRunner(BrowserRunnerConfig())
    assert runner._title_identity_tokens_match(
        "Siftary 100 Pack 16.9 oz Clear PET Cups",
        "Siftary 100 Pack 16.9 oz Clear PET Cups with Lids",
    )
    assert not runner._title_identity_tokens_match(
        "Siftary 100 Pack 16.9 oz Clear PET Cups",
        "Siftary 100 Pack 12 oz Clear PET Cups with Lids",
    )


class _CountLocator:
    def __init__(self, count: int):
        self._count = count

    def count(self):
        return self._count


class _SellerPage:
    url = "https://www.facebook.com/marketplace/you/selling"

    def locator(self, _selector):
        return _CountLocator(0)

    def eval_on_selector_all(self, selector, _script):
        if "marketplace/item" in selector:
            return []
        return [{
            "href": "",
            "text": "Smart AI Anti-Snore System Pillow $500 Active · Listed on 10/7",
            "title": "Smart AI Anti-Snore System Pillow",
            "remote_id": "1444392307650558",
            "category_id": "1569171756675761",
            "status": "Active",
        }]


def test_facebook_seller_page_requires_seller_route_and_inventory_ui():
    runner = MarketplaceBrowserRunner(BrowserRunnerConfig(), MARKETPLACE_BROWSER_SPECS["facebook"])

    class Page:
        url = "https://www.facebook.com/marketplace/"

        def locator(self, _selector):
            return _CountLocator(0)

    runner._page_text = lambda _page: "Marketplace Your listings"  # type: ignore[method-assign]
    evidence = runner._facebook_selling_page_evidence(Page())
    assert evidence["your_listings_or_selling_ui_present"] is True
    assert evidence["seller_listing_page_detected"] is False


def test_facebook_seller_card_fallback_uses_non_anchor_card_identity():
    runner = MarketplaceBrowserRunner(BrowserRunnerConfig(), MARKETPLACE_BROWSER_SPECS["facebook"])
    cards = runner._collect_facebook_selling_cards(_SellerPage(), expected_title="")

    assert len(cards) == 1
    assert cards[0]["remote_id"] == "1444392307650558"
    assert cards[0]["url"] == "https://www.facebook.com/marketplace/item/1444392307650558"
    assert cards[0]["title"] == "Smart AI Anti-Snore System Pillow"
    assert cards[0]["price"] == "$500"
    assert cards[0]["status"] == "Active"


def test_facebook_category_repair_fails_closed_without_exact_remote_facts():
    runner = MarketplaceBrowserRunner(BrowserRunnerConfig(), MARKETPLACE_BROWSER_SPECS["facebook"])

    with pytest.raises(BrowserRunnerError, match="exact expected/current/target listing facts"):
        runner.run_facebook_existing_listing_category_repair(
            job_id="test",
            bridge_account={},
            payload={"remote_id": "1457804986214655"},
        )


def test_facebook_category_selection_skips_hidden_duplicate_text_matches():
    runner = MarketplaceBrowserRunner(BrowserRunnerConfig(), MARKETPLACE_BROWSER_SPECS["facebook"])
    clicked = []

    class Candidate:
        def __init__(self, visible):
            self.visible = visible

        def is_visible(self):
            return self.visible

        def is_enabled(self):
            return True

        def click(self):
            clicked.append(self.visible)

    class Matches:
        def __init__(self):
            self.items = [Candidate(False), Candidate(True)]

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Page:
        def locator(self, _selector):
            return Matches()

    assert runner._click_visible_first(Page(), ['text=Access Control']) is True
    assert clicked == [True]


def test_facebook_publish_flow_advances_next_before_publish():
    runner = MarketplaceBrowserRunner(BrowserRunnerConfig(), MARKETPLACE_BROWSER_SPECS["facebook"])

    class Candidate:
        def __init__(self, label, page):
            self.label = label
            self.page = page

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def inner_text(self, timeout=None):  # noqa: ARG002
            return self.label.title()

        def get_attribute(self, _name):
            return None

        def scroll_into_view_if_needed(self, timeout=None):  # noqa: ARG002
            return None

        def click(self, timeout=None):  # noqa: ARG002
            if self.label == "next":
                self.page.preview = True
            elif not self.page.preview:
                raise AssertionError("Publish must not be clicked before the preview step")
            else:
                self.page.published = True

    class Matches:
        def __init__(self, candidates):
            self.candidates = candidates

        def count(self):
            return len(self.candidates)

        def nth(self, index):
            return self.candidates[index]

    class Page:
        preview = False
        published = False

        def locator(self, selector):
            if "Publish" in selector and self.preview:
                return Matches([Candidate("publish", self)])
            if "Next" in selector and not self.preview:
                return Matches([Candidate("next", self)])
            return Matches([])

        def wait_for_timeout(self, _milliseconds):
            return None

    page = Page()
    result = runner._click_facebook_publish_flow(page)
    assert result == {"submitted": True, "publish_click_attempted": True}
    assert page.published is True


def test_facebook_unconfirmed_publish_click_is_explicitly_ambiguous():
    runner = MarketplaceBrowserRunner(BrowserRunnerConfig(), MARKETPLACE_BROWSER_SPECS["facebook"])

    class Candidate:
        def is_visible(self): return True
        def is_enabled(self): return True
        def inner_text(self, timeout=None): return "Publish"
        def get_attribute(self, _name): return None
        def scroll_into_view_if_needed(self, timeout=None): return None
        def click(self, timeout=None): raise TimeoutError("another panel intercepted pointer input")

    class Matches:
        def count(self): return 1
        def nth(self, _index): return Candidate()

    class Page:
        def locator(self, selector):
            return Matches() if "Publish" in selector else type("Empty", (), {"count": lambda self: 0})()

    result = runner._click_facebook_publish_flow(Page())
    assert result["submitted"] is False
    assert result["publish_click_attempted"] is True
    assert "intercepted" in result["error"]
