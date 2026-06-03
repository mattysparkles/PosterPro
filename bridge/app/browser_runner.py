from __future__ import annotations

import base64
import json
import mimetypes
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import httpx


class BrowserRunnerError(RuntimeError):
    pass


@dataclass
class BrowserRunnerConfig:
    headless: bool = True
    timeout_ms: int = 45000
    submit_enabled: bool = False
    screenshots_dir: Path = Path("./data/screenshots")
    app_base_url: str | None = None
    asset_persistor: Callable[[bytes, str | None, str | None, str | None], dict[str, Any]] | None = None


@dataclass(frozen=True)
class MarketplaceBrowserSpec:
    marketplace: str
    label: str
    home_url: str
    create_url: str
    auth_check_url: str
    connect_start_url: str
    title_selectors: tuple[str, ...]
    price_selectors: tuple[str, ...]
    description_selectors: tuple[str, ...]
    submit_selectors: tuple[str, ...]
    import_overview_url: str | None = None
    supports_import: bool = False
    auth_cookie_names: tuple[str, ...] = field(default_factory=tuple)
    auth_url_tokens: tuple[str, ...] = field(default_factory=tuple)
    auth_required_text: tuple[str, ...] = field(default_factory=tuple)
    auth_forbidden_text: tuple[str, ...] = field(default_factory=tuple)
    auth_required_selectors: tuple[str, ...] = field(default_factory=tuple)


MARKETPLACE_BROWSER_SPECS: dict[str, MarketplaceBrowserSpec] = {
    "amazon": MarketplaceBrowserSpec(
        marketplace="amazon",
        label="Amazon",
        home_url="https://www.amazon.com/",
        create_url="https://www.amazon.com/",
        auth_check_url="https://www.amazon.com/",
        connect_start_url="https://www.amazon.com/ap/signin",
        title_selectors=("input[type='search']",),
        price_selectors=("input[type='number']",),
        description_selectors=("textarea",),
        submit_selectors=("button",),
        import_overview_url="https://www.amazon.com/",
        supports_import=True,
        auth_cookie_names=("session-id", "ubid-main"),
        auth_url_tokens=("/gp/", "/dp/", "/s?", "amazon.com"),
    ),
    "facebook": MarketplaceBrowserSpec(
        marketplace="facebook",
        label="Facebook Marketplace",
        home_url="https://www.facebook.com/",
        create_url="https://www.facebook.com/marketplace/create/item",
        auth_check_url="https://www.facebook.com/marketplace/you/selling",
        connect_start_url="https://www.facebook.com/",
        title_selectors=(
            'input[aria-label*="Title"]',
            'input[placeholder*="Title"]',
            'input[type="text"]',
        ),
        price_selectors=(
            'input[aria-label*="Price"]',
            'input[placeholder*="Price"]',
            'input[inputmode="numeric"]',
        ),
        description_selectors=(
            'textarea[aria-label*="Description"]',
            'textarea[placeholder*="Description"]',
            "textarea",
        ),
        submit_selectors=(
            'button:has-text("Publish")',
            'button:has-text("Next")',
            '[role="button"]:has-text("Publish")',
            '[role="button"]:has-text("Next")',
        ),
        import_overview_url="https://www.facebook.com/marketplace/you/selling",
        supports_import=True,
        auth_cookie_names=("c_user",),
        auth_url_tokens=("/marketplace/",),
    ),
    "mercari": MarketplaceBrowserSpec(
        marketplace="mercari",
        label="Mercari",
        home_url="https://www.mercari.com/",
        create_url="https://www.mercari.com/sell/",
        auth_check_url="https://www.mercari.com/sell/",
        connect_start_url="https://www.mercari.com/login/",
        title_selectors=(
            'input[name*="title" i]',
            'input[placeholder*="Title" i]',
            'input[maxLength="80"]',
        ),
        price_selectors=(
            'input[name*="price" i]',
            'input[placeholder*="Price" i]',
            'input[inputmode="numeric"]',
        ),
        description_selectors=(
            'textarea[name*="description" i]',
            'textarea[placeholder*="Describe" i]',
            "textarea",
        ),
        submit_selectors=(
            'button:has-text("List item")',
            'button:has-text("List")',
            'button:has-text("Submit")',
            'button:has-text("Next")',
        ),
        auth_cookie_names=("_mwus",),
        auth_url_tokens=("/sell",),
        auth_forbidden_text=("performing security verification", "verify you are not a bot", "cloudflare"),
    ),
    "poshmark": MarketplaceBrowserSpec(
        marketplace="poshmark",
        label="Poshmark",
        home_url="https://poshmark.com/",
        create_url="https://poshmark.com/create-listing",
        auth_check_url="https://poshmark.com/create-listing",
        connect_start_url="https://poshmark.com/login",
        title_selectors=(
            'input[name*="title" i]',
            'input[placeholder*="Title" i]',
            'input[maxlength="50"]',
        ),
        price_selectors=(
            'input[name*="price" i]',
            'input[placeholder*="List price" i]',
            'input[inputmode="numeric"]',
        ),
        description_selectors=(
            'textarea[name*="description" i]',
            'textarea[placeholder*="Describe" i]',
            "textarea",
        ),
        submit_selectors=(
            'button:has-text("List Item")',
            'button:has-text("Next")',
            'button:has-text("Submit")',
            '[role="button"]:has-text("List Item")',
        ),
        auth_url_tokens=("/create-listing",),
        auth_required_text=("sell on poshmark", "list item", "create listing"),
    ),
    "etsy": MarketplaceBrowserSpec(
        marketplace="etsy",
        label="Etsy",
        home_url="https://www.etsy.com/",
        create_url="https://www.etsy.com/your/shops/me/tools/listings/create",
        auth_check_url="https://www.etsy.com/your/shops/me/tools/listings/create",
        connect_start_url="https://www.etsy.com/signin",
        title_selectors=(
            'input[name*="title" i]',
            'input[placeholder*="Title" i]',
            'input[maxlength="140"]',
        ),
        price_selectors=(
            'input[name*="price" i]',
            'input[placeholder*="Price" i]',
            'input[inputmode="decimal"]',
            'input[inputmode="numeric"]',
        ),
        description_selectors=(
            'textarea[name*="description" i]',
            'textarea[placeholder*="Describe" i]',
            "textarea",
        ),
        submit_selectors=(
            'button:has-text("Publish")',
            'button:has-text("Save as draft")',
            'button:has-text("Next")',
        ),
        auth_url_tokens=("/your/shops/", "/shop-manager", "/your/"),
        auth_required_text=("shop manager", "listings", "add a listing"),
    ),
    "whatnot": MarketplaceBrowserSpec(
        marketplace="whatnot",
        label="Whatnot",
        home_url="https://www.whatnot.com/",
        create_url="https://seller.whatnot.com/listings/create",
        auth_check_url="https://seller.whatnot.com/listings",
        connect_start_url="https://www.whatnot.com/login",
        title_selectors=(
            'input[name*="title" i]',
            'input[placeholder*="Title" i]',
            'input[maxlength="120"]',
        ),
        price_selectors=(
            'input[name*="price" i]',
            'input[name*="bid" i]',
            'input[placeholder*="Price" i]',
            'input[inputmode="numeric"]',
        ),
        description_selectors=(
            'textarea[name*="description" i]',
            'textarea[placeholder*="Description" i]',
            "textarea",
        ),
        submit_selectors=(
            'button:has-text("Create listing")',
            'button:has-text("Publish")',
            'button:has-text("Next")',
            'button:has-text("Save")',
        ),
        auth_url_tokens=("seller.whatnot.com", "/listings"),
        auth_required_text=("listings", "seller hub", "create listing"),
        auth_forbidden_text=("404: page not found", "page not found", "sorry, the page you were trying to view does not exist"),
    ),
    "depop": MarketplaceBrowserSpec(
        marketplace="depop",
        label="Depop",
        home_url="https://www.depop.com/",
        create_url="https://www.depop.com/sell/",
        auth_check_url="https://www.depop.com/sell/",
        connect_start_url="https://www.depop.com/login/",
        title_selectors=(
            'input[name*="title" i]',
            'input[placeholder*="Title" i]',
            'input[maxlength="100"]',
        ),
        price_selectors=(
            'input[name*="price" i]',
            'input[placeholder*="Price" i]',
            'input[inputmode="decimal"]',
            'input[inputmode="numeric"]',
        ),
        description_selectors=(
            'textarea[name*="description" i]',
            'textarea[placeholder*="Describe" i]',
            "textarea",
        ),
        submit_selectors=(
            'button:has-text("List item")',
            'button:has-text("Publish")',
            'button:has-text("Next")',
            'button:has-text("Save")',
        ),
        auth_url_tokens=("/sell", "/mydepop"),
        auth_required_text=("sell", "my depop", "list an item"),
    ),
    "vinted": MarketplaceBrowserSpec(
        marketplace="vinted",
        label="Vinted",
        home_url="https://www.vinted.com/",
        create_url="https://www.vinted.com/items/new",
        auth_check_url="https://www.vinted.com/items/new",
        connect_start_url="https://www.vinted.com/member/login",
        title_selectors=(
            'input[name*="title" i]',
            'input[placeholder*="Title" i]',
            'input[maxlength="100"]',
        ),
        price_selectors=(
            'input[name*="price" i]',
            'input[placeholder*="Price" i]',
            'input[inputmode="decimal"]',
            'input[inputmode="numeric"]',
        ),
        description_selectors=(
            'textarea[name*="description" i]',
            'textarea[placeholder*="Describe" i]',
            "textarea",
        ),
        submit_selectors=(
            'button:has-text("Upload")',
            'button:has-text("List item")',
            'button:has-text("Next")',
            'button:has-text("Save")',
        ),
        auth_url_tokens=("/items/new", "/member/"),
        auth_required_text=("sell now", "upload item", "list your item"),
    ),
}


class MarketplaceBrowserRunner:
    def __init__(self, config: BrowserRunnerConfig, spec: MarketplaceBrowserSpec) -> None:
        self.config = config
        self.spec = spec

    def _persist_generated_asset(self, file_path: Path, *, source_url: str | None = None) -> dict[str, Any] | str:
        if not file_path.exists() or not self.config.asset_persistor:
            return str(file_path)
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        asset = self.config.asset_persistor(
            file_path.read_bytes(),
            content_type,
            file_path.name,
            source_url,
        )
        return asset if isinstance(asset, dict) else str(file_path)

    def run_crosspost(self, *, job_id: str, bridge_account: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        playwright = self._load_playwright()
        listing_payload = payload.get("payload") or {}
        title = str(listing_payload.get("title") or "").strip()
        price = listing_payload.get("price") or listing_payload.get("listing_price") or listing_payload.get("buy_it_now_price")
        description = str(listing_payload.get("description") or "").strip()
        image_urls = list(listing_payload.get("image_urls") or [])
        shipping_scope = payload.get("shipping_scope")

        if not title:
            raise BrowserRunnerError("Browser runner requires a listing title")
        if price in (None, ""):
            raise BrowserRunnerError("Browser runner requires a listing price")

        screenshot_dir = self.config.screenshots_dir
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        before_path = screenshot_dir / f"{job_id}-before-submit.png"
        after_path = screenshot_dir / f"{job_id}-after-submit.png"

        temp_files: list[Path] = []
        with playwright as playwright_instance:
            browser = playwright_instance.chromium.launch(headless=self.config.headless)
            try:
                context = self._new_context(browser, bridge_account)
                page = context.new_page()
                page.goto(self.spec.create_url, wait_until="domcontentloaded")
                self._ensure_logged_in(page)
                self._require_authenticated_page(page)
                self._dismiss_optional_dialogs(page)

                uploaded_count = 0
                if image_urls:
                    temp_files = self._download_images(image_urls)
                    uploaded_count = self._upload_images(page, temp_files)

                if self.spec.marketplace == "facebook":
                    self._fill_facebook_form(page, title=title, price=str(price), description=description)
                else:
                    self._fill_first(page, list(self.spec.title_selectors), title, "title")
                    self._fill_first(page, list(self.spec.price_selectors), str(price), "price")
                if self.spec.marketplace != "facebook" and description:
                    self._fill_first(page, list(self.spec.description_selectors), description, "description")

                self._apply_marketplace_specific_fields(page, listing_payload)
                self._apply_shipping_scope(page, shipping_scope)
                page.screenshot(path=str(before_path), full_page=True)

                submitted = False
                if self.config.submit_enabled:
                    submitted = self._click_first(page, list(self.spec.submit_selectors))
                    if not submitted:
                        raise BrowserRunnerError(f"Could not find a publish action on the {self.spec.label} listing flow")
                    page.wait_for_timeout(3000)

                page.screenshot(path=str(after_path), full_page=True)
                final_storage_state = context.storage_state()
                return {
                    "job_id": job_id,
                    "marketplace": self.spec.marketplace,
                    "status": "submitted_to_marketplace" if submitted else "draft_form_filled",
                    "submitted": submitted,
                    "bridge_account": self._bridge_account_summary(bridge_account),
                    "create_url": self.spec.create_url,
                    "uploaded_image_count": uploaded_count,
                    "screenshots": {
                        "before_submit": self._persist_generated_asset(before_path),
                        "after_submit": self._persist_generated_asset(after_path),
                    },
                    "session_state": {
                        "session_state": "active",
                        "session_payload": final_storage_state,
                    },
                }
            except playwright._timeout_error as exc:
                raise BrowserRunnerError(f"{self.spec.label} browser automation timed out: {exc}") from exc
            finally:
                browser.close()
                for temp_file in temp_files:
                    temp_file.unlink(missing_ok=True)

    def run_import(self, *, job_id: str, bridge_account: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not self.spec.supports_import or not self.spec.import_overview_url:
            raise BrowserRunnerError(f"{self.spec.label} import is not implemented in the bridge runner")
        playwright = self._load_playwright()
        max_listings = self._coerce_limit(
            payload.get("max_listings")
            or ((payload.get("payload") or {}).get("max_listings") if isinstance(payload.get("payload"), dict) else None)
        )
        screenshot_dir = self.config.screenshots_dir
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        overview_path = screenshot_dir / f"{job_id}-{self.spec.marketplace}-selling-overview.png"

        with playwright as playwright_instance:
            browser = playwright_instance.chromium.launch(headless=self.config.headless)
            try:
                context = self._new_context(browser, bridge_account)
                page = context.new_page()
                page.goto(self.spec.import_overview_url, wait_until="domcontentloaded")
                self._ensure_logged_in(page)
                self._require_authenticated_page(page)
                self._dismiss_optional_dialogs(page)
                page.wait_for_timeout(2500)
                page.screenshot(path=str(overview_path), full_page=True)

                listing_urls = self._collect_listing_urls(page, max_listings=max_listings)
                imported_listings = []
                for listing_url in listing_urls:
                    item_page = context.new_page()
                    try:
                        imported_listings.append(self._extract_listing(item_page, listing_url))
                    finally:
                        item_page.close()

                final_storage_state = context.storage_state()
                return {
                    "job_id": job_id,
                    "marketplace": self.spec.marketplace,
                    "status": "import_completed",
                    "bridge_account": self._bridge_account_summary(bridge_account),
                    "imported_listing_count": len(imported_listings),
                    "imported_listings": imported_listings,
                    "screenshots": {
                        "selling_overview": self._persist_generated_asset(overview_path),
                    },
                    "session_state": {
                        "session_state": "active",
                        "session_payload": final_storage_state,
                    },
                }
            except playwright._timeout_error as exc:
                raise BrowserRunnerError(f"{self.spec.label} import automation timed out: {exc}") from exc
            finally:
                browser.close()

    def capture_session(
        self,
        *,
        account_key: str,
        bridge_account: dict[str, Any] | None = None,
        login_handle: str | None = None,
        wait_timeout_seconds: int = 300,
        status_callback: Callable[[str, str | None], None] | None = None,
    ) -> dict[str, Any]:
        playwright = self._load_playwright()
        screenshot_dir = self.config.screenshots_dir
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        before_path = screenshot_dir / f"{account_key}-{self.spec.marketplace}-connect-start.png"
        after_path = screenshot_dir / f"{account_key}-{self.spec.marketplace}-connect-complete.png"

        with playwright as playwright_instance:
            try:
                self._emit_status(status_callback, "launching_browser", f"Starting the {self.spec.label} browser session.")
                browser = playwright_instance.chromium.launch(headless=False)
            except Exception as exc:
                raise BrowserRunnerError(
                    f"{self.spec.label} connect requires a headed Chromium session on the bridge host. "
                    "Set up a desktop-capable bridge environment or use the manual session JSON fallback."
                ) from exc

            try:
                storage_state = self._optional_storage_state(bridge_account or {})
                context = browser.new_context(storage_state=storage_state) if storage_state else browser.new_context()
                context.set_default_timeout(self.config.timeout_ms)
                page = context.new_page()
                self._emit_status(status_callback, "opening_marketplace", f"Opening {self.spec.label} in the bridge browser.")
                page.goto(self.spec.connect_start_url, wait_until="domcontentloaded")
                self._dismiss_optional_dialogs(page)
                page.screenshot(path=str(before_path), full_page=True)
                self._emit_status(
                    status_callback,
                    "waiting_for_login",
                    f"{self.spec.label} is open in the bridge desktop. Complete login and any MFA to continue.",
                )
                self._wait_for_authenticated_session(
                    page,
                    context,
                    login_handle=login_handle,
                    wait_timeout_seconds=wait_timeout_seconds,
                )
                self._emit_status(status_callback, "validating_session", f"{self.spec.label} login detected. Validating the captured session.")
                page.goto(self.spec.auth_check_url, wait_until="domcontentloaded")
                self._ensure_logged_in(page)
                self._require_authenticated_page(page)
                self._dismiss_optional_dialogs(page)
                page.wait_for_timeout(1500)
                final_storage_state = context.storage_state()
                self._validate_storage_state(final_storage_state)
                page.screenshot(path=str(after_path), full_page=True)
                self._emit_status(status_callback, "completed", f"{self.spec.label} session captured successfully.")
                return {
                    "marketplace": self.spec.marketplace,
                    "status": "session_captured",
                    "connect_mode": "bridge_browser",
                    "bridge_account": self._bridge_account_summary(bridge_account) if bridge_account else None,
                    "login_handle": (login_handle or "").strip() or None,
                    "screenshots": {
                        "connect_start": self._persist_generated_asset(before_path),
                        "connect_complete": self._persist_generated_asset(after_path),
                    },
                    "session_state": {
                        "session_state": "active",
                        "session_payload": final_storage_state,
                    },
                }
            except playwright._timeout_error as exc:
                raise BrowserRunnerError(f"{self.spec.label} connect timed out while waiting for the login flow: {exc}") from exc
            finally:
                browser.close()

    def _emit_status(
        self,
        callback: Callable[[str, str | None], None] | None,
        status: str,
        message: str | None = None,
    ) -> None:
        if callback is None:
            return
        callback(status, message)

    def _load_playwright(self) -> Any:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserRunnerError(
                "Playwright is not installed in the bridge environment. Install the 'playwright' package and browser binaries."
            ) from exc

        class _PlaywrightWrapper:
            _timeout_error = PlaywrightTimeoutError

            def __enter__(self):
                self._instance = sync_playwright().start()
                return self._instance

            def __exit__(self, exc_type, exc, tb):
                self._instance.stop()
                return False

        return _PlaywrightWrapper()

    def _bridge_account_summary(self, bridge_account: dict[str, Any] | None) -> dict[str, Any] | None:
        if not bridge_account:
            return None
        return {
            "account_id": bridge_account["account_id"],
            "account_key": bridge_account["account_key"],
            "display_name": bridge_account.get("display_name"),
        }

    def _new_context(self, browser: Any, bridge_account: dict[str, Any] | None) -> Any:
        storage_state = self._optional_storage_state(bridge_account) if bridge_account else None
        context = browser.new_context(storage_state=storage_state) if storage_state else browser.new_context()
        context.set_default_timeout(self.config.timeout_ms)
        return context

    def _build_storage_state(self, bridge_account: dict[str, Any]) -> dict[str, Any]:
        state = self._optional_storage_state(bridge_account)
        if not state:
            raise BrowserRunnerError("Bridge account session payload is missing browser storage state or cookies")
        return state

    def _optional_storage_state(self, bridge_account: dict[str, Any]) -> dict[str, Any] | None:
        payload = bridge_account.get("session_payload") or {}
        cookies = payload.get("cookies") or []
        origins = payload.get("origins") or []
        if not cookies and not origins:
            return None
        return {"cookies": cookies, "origins": origins}

    def _validate_storage_state(self, storage_state: dict[str, Any]) -> None:
        cookies = storage_state.get("cookies") or []
        if not cookies:
            raise BrowserRunnerError(f"{self.spec.label} connect finished without capturing browser cookies")

    def _coerce_limit(self, value: Any) -> int:
        try:
            parsed = int(value or 10)
        except (TypeError, ValueError):
            parsed = 10
        return max(1, min(25, parsed))

    def _download_images(self, image_urls: list[str]) -> list[Path]:
        temp_dir = Path(tempfile.mkdtemp(prefix="posterpro-bridge-images-"))
        saved: list[Path] = []
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            for index, url in enumerate(image_urls):
                resolved_url = self._resolve_media_url(url)
                try:
                    response = client.get(resolved_url)
                except Exception as exc:
                    raise BrowserRunnerError(f"Failed to download bridge image {resolved_url}: {exc}") from exc
                response.raise_for_status()
                suffix = Path(urlparse(resolved_url).path).suffix or ".jpg"
                destination = temp_dir / f"image-{index + 1}{suffix}"
                destination.write_bytes(response.content)
                saved.append(destination)
        return saved

    def _resolve_media_url(self, url: str) -> str:
        cleaned = str(url or "").strip()
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            return cleaned
        base_url = str(self.config.app_base_url or "").strip().rstrip("/")
        if not base_url:
            raise BrowserRunnerError(f"Bridge runner needs an absolute URL for media asset '{cleaned}'")
        if cleaned.startswith("/"):
            return f"{base_url}{cleaned}"
        return f"{base_url}/{cleaned.lstrip('/')}"

    def _upload_images(self, page: Any, image_paths: list[Path]) -> int:
        for selector in ['input[type="file"]']:
            locator = page.locator(selector).first
            if locator.count():
                locator.set_input_files([str(path) for path in image_paths])
                page.wait_for_timeout(2000)
                return len(image_paths)
        return 0

    def _fill_first(self, page: Any, selectors: list[str], value: str, field_name: str) -> None:
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count():
                locator.click()
                locator.fill(value)
                return
        raise BrowserRunnerError(f"Could not locate a {self.spec.label} {field_name} field")

    def _fill_facebook_form(self, page: Any, *, title: str, price: str, description: str | None) -> None:
        page.wait_for_timeout(1000)
        text_inputs = page.locator('input[type="text"]')
        if text_inputs.count() >= 2:
            title_input = text_inputs.nth(0)
            price_input = text_inputs.nth(1)
            title_input.click()
            title_input.fill(title)
            price_input.click()
            price_input.fill(price)
        else:
            self._fill_facebook_labeled_field(page, "Title", title, "input")
            self._fill_facebook_labeled_field(page, "Price", price, "input")

        if description:
            textareas = page.locator("textarea")
            if textareas.count():
                description_input = textareas.nth(0)
                description_input.click()
                description_input.fill(description)
            else:
                self._fill_facebook_labeled_field(page, "Description", description, "textarea")

    def _fill_facebook_labeled_field(self, page: Any, label: str, value: str, preferred_tag: str = "input") -> None:
        normalized_label = str(label or "").strip().lower()
        normalized_value = str(value or "").strip()
        if not normalized_value:
            return

        selectors: list[str] = []
        if preferred_tag == "textarea":
            selectors.extend(["textarea", 'div[contenteditable="true"]'])
        else:
            selectors.extend(['input[type="text"]', 'input[type="number"]', 'input[role="spinbutton"]', 'div[contenteditable="true"]'])
        candidates = page.locator(", ".join(selectors))
        for index in range(candidates.count()):
            locator = candidates.nth(index)
            try:
                if not locator.is_visible():
                    continue
                parent_text = locator.evaluate(
                    """
                    (el) => {
                      let node = el;
                      for (let i = 0; i < 4 && node; i += 1) {
                        const parent = node.parentElement;
                        if (!parent) {
                          break;
                        }
                        const text = (parent.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (text) {
                          return text;
                        }
                        node = parent;
                      }
                      return '';
                    }
                    """
                )
            except Exception:
                continue
            if normalized_label not in str(parent_text or "").lower():
                continue
            locator.click()
            locator.fill(normalized_value)
            return
        raise BrowserRunnerError(f"Could not locate a Facebook Marketplace {label.lower()} field")

    def _try_fill_first(self, page: Any, selectors: list[str], value: str | None) -> bool:
        normalized = str(value or "").strip()
        if not normalized:
            return False
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count():
                locator.click()
                locator.fill(normalized)
                return True
        return False

    def _click_first(self, page: Any, selectors: list[str]) -> bool:
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count():
                locator.click()
                return True
        return False

    def _dismiss_optional_dialogs(self, page: Any) -> None:
        self._click_first(
            page,
            [
                'button:has-text("Not now")',
                'button:has-text("Close")',
                '[role="button"]:has-text("Close")',
            ],
        )

    def _ensure_logged_in(self, page: Any) -> None:
        if self._looks_logged_out(page):
            raise BrowserRunnerError(f"Bridge account session is not authenticated with {self.spec.label}")

    def _require_authenticated_page(self, page: Any) -> None:
        current_url = str(page.url or "").strip().lower()
        page_text = self._page_text(page).lower()

        forbidden = [text for text in self.spec.auth_forbidden_text if text and text.lower() in page_text]
        if forbidden:
            raise BrowserRunnerError(
                f"{self.spec.label} session did not reach an authenticated seller page. Encountered: {forbidden[0]}"
            )

        if self.spec.auth_url_tokens and not any(token.lower() in current_url for token in self.spec.auth_url_tokens):
            raise BrowserRunnerError(
                f"{self.spec.label} session did not land on the expected seller page for validation"
            )

        if self.spec.auth_required_selectors and not self._has_any_selector(page, list(self.spec.auth_required_selectors)):
            if self.spec.auth_required_text and not any(text.lower() in page_text for text in self.spec.auth_required_text):
                raise BrowserRunnerError(
                    f"{self.spec.label} session reached a page that does not look like an authenticated seller workspace"
                )
        elif self.spec.auth_required_text and not any(text.lower() in page_text for text in self.spec.auth_required_text):
            raise BrowserRunnerError(
                f"{self.spec.label} session reached a page that does not look like an authenticated seller workspace"
            )

    def _wait_for_authenticated_session(
        self,
        page: Any,
        context: Any,
        *,
        login_handle: str | None = None,
        wait_timeout_seconds: int = 300,
    ) -> None:
        deadline = time.monotonic() + max(60, int(wait_timeout_seconds or 300))

        while time.monotonic() < deadline:
            page.wait_for_timeout(1500)
            self._dismiss_optional_dialogs(page)
            if not self._has_authenticated_session(context):
                continue
            probe = context.new_page()
            try:
                probe.goto(self.spec.auth_check_url, wait_until="domcontentloaded")
                self._dismiss_optional_dialogs(probe)
                if self._looks_logged_out(probe):
                    continue
                self._require_authenticated_page(probe)
                return
            except BrowserRunnerError:
                continue
            except Exception:
                continue
            finally:
                probe.close()

        handle_hint = f" for {login_handle}" if login_handle else ""
        raise BrowserRunnerError(
            f"{self.spec.label} connect timed out after {max(60, int(wait_timeout_seconds or 300))} seconds while waiting for an authenticated session{handle_hint}. "
            f"Complete the login challenge in the opened bridge browser and try again."
        )

    def _has_authenticated_session(self, context: Any) -> bool:
        try:
            cookies = context.cookies()
        except Exception:
            return False
        if self.spec.auth_cookie_names:
            return any(
                str(cookie.get("name") or "") in self.spec.auth_cookie_names and str(cookie.get("value") or "").strip()
                for cookie in cookies
            )
        domain = urlparse(self.spec.home_url).netloc.replace("www.", "")
        return any(domain in str(cookie.get("domain") or "") and str(cookie.get("value") or "").strip() for cookie in cookies)

    def _looks_logged_out(self, page: Any) -> bool:
        current_url = str(page.url or "").lower()
        auth_tokens = ("login", "signin", "sign-in", "authenticate", "auth", "register")
        if any(token in current_url for token in auth_tokens):
            return True
        try:
            password_fields = page.locator('input[type="password"]')
            if password_fields.count():
                return True
        except Exception:
            return False
        return False

    def _has_any_selector(self, page: Any, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count():
                    return True
            except Exception:
                continue
        return False

    def _page_text(self, page: Any) -> str:
        try:
            body = page.locator("body").first
            if body.count():
                return body.inner_text(timeout=1000).strip()
        except Exception:
            return ""
        return ""

    def _apply_marketplace_specific_fields(self, page: Any, listing_payload: dict[str, Any]) -> None:
        quantity = listing_payload.get("quantity")
        if self.spec.marketplace in {"etsy", "whatnot"}:
            self._try_fill_first(
                page,
                [
                    'input[name*="quantity" i]',
                    'input[placeholder*="Quantity" i]',
                    'input[inputmode="numeric"]',
                ],
                str(quantity) if quantity not in (None, "") else None,
            )
        if self.spec.marketplace == "poshmark":
            self._try_fill_first(page, ['input[name*="brand" i]', 'input[placeholder*="Brand" i]'], listing_payload.get("brand"))
            self._try_fill_first(page, ['input[name*="size" i]', 'input[placeholder*="Size" i]'], listing_payload.get("size"))
        if self.spec.marketplace == "mercari":
            self._try_fill_first(page, ['input[name*="brand" i]', 'input[placeholder*="Brand" i]'], listing_payload.get("brand"))
        if self.spec.marketplace == "etsy":
            materials = listing_payload.get("materials") or []
            if isinstance(materials, list):
                self._try_fill_first(
                    page,
                    ['input[name*="material" i]', 'input[placeholder*="Materials" i]'],
                    ", ".join(str(item).strip() for item in materials if str(item).strip()),
                )
        if self.spec.marketplace == "whatnot":
            self._try_fill_first(
                page,
                ['input[name*="starting" i]', 'input[name*="bid" i]', 'input[placeholder*="Starting" i]'],
                str(listing_payload.get("starting_bid") or listing_payload.get("price") or ""),
            )

    def _apply_shipping_scope(self, page: Any, shipping_scope: str | None) -> None:
        normalized = str(shipping_scope or "").strip().lower()
        if normalized == "shipping_only":
            self._click_first(
                page,
                [
                    '[role="radio"]:has-text("Shipping")',
                    '[role="button"]:has-text("Shipping")',
                ],
            )
        elif normalized == "local_and_shipping":
            self._click_first(
                page,
                [
                    '[role="radio"]:has-text("Both")',
                    '[role="button"]:has-text("Shipping and local pickup")',
                ],
            )

    def _collect_listing_urls(self, page: Any, *, max_listings: int) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for _ in range(7):
            hrefs = page.eval_on_selector_all(
                '[href*="/marketplace/item/"], [href*="/commerce/listing/"], [href*="listing_id="]',
                "nodes => nodes.map((node) => node.href || node.getAttribute('href') || '').filter(Boolean)",
            )
            for href in hrefs:
                normalized = self._normalize_listing_url(href)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    found.append(normalized)
                    if len(found) >= max_listings:
                        return found
            for href in self._collect_listing_urls_from_html(page.content()):
                normalized = self._normalize_listing_url(href)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    found.append(normalized)
                    if len(found) >= max_listings:
                        return found
            page.mouse.wheel(0, 2200)
            page.wait_for_timeout(1200)
        if not found:
            raise BrowserRunnerError("Could not find any Facebook Marketplace listing links on the selling page")
        return found[:max_listings]

    def _collect_listing_urls_from_html(self, html: str) -> list[str]:
        if not html:
            return []
        decoded = html.replace("\\u002F", "/").replace("\\/", "/")
        candidates = re.findall(
            r"https?://(?:www\.)?facebook\.com/(?:marketplace/item/\d+|commerce/listing/\d+|marketplace/edit/\?[^\"'\\s<>]*listing_id=\d+)"
            r"|/(?:marketplace/item/\d+|commerce/listing/\d+|marketplace/edit/\?[^\"'\\s<>]*listing_id=\d+)",
            decoded,
            flags=re.IGNORECASE,
        )
        return [candidate for candidate in candidates if candidate]

    def _normalize_listing_url(self, href: str | None) -> str | None:
        if not href:
            return None
        parsed = urlparse(href)
        path = parsed.path or ""
        listing_id_match = (
            re.search(r"/marketplace/item/(\d+)", path, flags=re.IGNORECASE)
            or re.search(r"/commerce/listing/(\d+)", path, flags=re.IGNORECASE)
        )
        listing_id = listing_id_match.group(1) if listing_id_match else None
        if not listing_id:
            query_listing_id = parse_qs(parsed.query or "").get("listing_id") or []
            if query_listing_id:
                listing_id = str(query_listing_id[0]).strip()
        if not listing_id:
            return None
        return f"https://www.facebook.com/marketplace/item/{listing_id}"

    def _extract_listing(self, page: Any, listing_url: str) -> dict[str, Any]:
        page.goto(listing_url, wait_until="domcontentloaded")
        self._ensure_logged_in(page)
        self._dismiss_optional_dialogs(page)
        page.wait_for_timeout(1500)

        ld_product = self._extract_ld_product(page)
        meta_title = self._read_meta(page, 'meta[property="og:title"]')
        meta_description = self._read_meta(page, 'meta[property="og:description"]')
        meta_image = self._read_meta(page, 'meta[property="og:image"]')
        page_heading = self._first_text(page, ["h1", '[role="main"] h1'])
        title = (
            ld_product.get("name")
            or page_heading
            or (meta_title if not self._looks_like_generic_listing_title(meta_title) else "")
        )
        description = (
            ld_product.get("description")
            or meta_description
            or self._first_text(page, ['[data-testid="marketplace_pdp_description"]', '[role="main"] div[dir="auto"]'])
            or ""
        )
        raw_price = ld_product.get("price") or self._first_text(
            page,
            [
                '[aria-label*="$"]',
                '[role="main"] span:has-text("$")',
            ],
        )
        image_urls = []
        for image_url in [
            *self._coerce_images(ld_product.get("image")),
            meta_image,
            *self._collect_gallery_images(page),
        ]:
            if image_url and image_url not in image_urls:
                image_urls.append(image_url)
        image_assets = self._capture_listing_assets(page, image_urls)
        return {
            "source_listing_reference": listing_url,
            "source_url": listing_url,
            "title": (title or "").strip(),
            "description": (description or "").strip(),
            "price": self._coerce_price(raw_price),
            "listing_price": self._coerce_price(raw_price),
            "quantity": 1,
            "image_urls": image_urls,
            "image_assets": image_assets,
            "item_specifics": {
                "scraped_via": "facebook_browser_assist",
                "source_url": listing_url,
            },
            "tags": ["facebook", "imported"],
        }

    def _looks_like_generic_listing_title(self, value: str | None) -> bool:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return True
        generic_titles = {
            "chat",
            "chats",
            "marketplace",
            "facebook marketplace",
            "facebook",
        }
        return normalized in generic_titles or normalized.startswith("chat |") or normalized.startswith("marketplace |")

    def _extract_ld_product(self, page: Any) -> dict[str, Any]:
        for script_text in page.locator('script[type="application/ld+json"]').all_text_contents():
            try:
                payload = json.loads(script_text)
            except json.JSONDecodeError:
                continue
            product = self._find_product_payload(payload)
            if product:
                offers = product.get("offers") or {}
                return {
                    "name": product.get("name"),
                    "description": product.get("description"),
                    "price": offers.get("price") if isinstance(offers, dict) else None,
                    "image": product.get("image"),
                }
        return {}

    def _find_product_payload(self, payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            payload_type = payload.get("@type")
            if payload_type == "Product" or (isinstance(payload_type, list) and "Product" in payload_type):
                return payload
            for value in payload.values():
                match = self._find_product_payload(value)
                if match:
                    return match
        elif isinstance(payload, list):
            for item in payload:
                match = self._find_product_payload(item)
                if match:
                    return match
        return None

    def _read_meta(self, page: Any, selector: str) -> str | None:
        locator = page.locator(selector).first
        if locator.count():
            content = locator.get_attribute("content")
            return content.strip() if content else None
        return None

    def _first_text(self, page: Any, selectors: list[str]) -> str | None:
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count():
                text = locator.inner_text().strip()
                if text:
                    return text
        return None

    def _coerce_images(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _collect_gallery_images(self, page: Any) -> list[str]:
        try:
            candidates = page.evaluate(
                """
                () => {
                  const results = [];
                  const seen = new Set();
                  const add = (value, score = 0) => {
                    if (!value || typeof value !== "string") {
                      return;
                    }
                    const normalized = value.trim();
                    if (!normalized || seen.has(normalized)) {
                      return;
                    }
                    seen.add(normalized);
                    results.push({ url: normalized, score });
                  };

                  const imgNodes = Array.from(document.querySelectorAll('img'));
                  for (const img of imgNodes) {
                    const src = img.currentSrc || img.src || img.getAttribute('src') || '';
                    const width = Number(img.naturalWidth || img.width || 0);
                    const height = Number(img.naturalHeight || img.height || 0);
                    const rect = img.getBoundingClientRect();
                    const visibleArea = Math.max(0, rect.width) * Math.max(0, rect.height);
                    const bigEnough = width >= 240 || height >= 240 || visibleArea >= 40000;
                    const inMain = Boolean(img.closest('[role="main"]')) || Boolean(img.closest('main'));
                    const score =
                      (bigEnough ? 20 : 0) +
                      (inMain ? 15 : 0) +
                      (src.includes('scontent') || src.includes('fbcdn') ? 20 : 0) +
                      (visibleArea >= 120000 ? 15 : 0);
                    if (src && score >= 20) {
                      add(src, score);
                    }
                    const srcset = img.getAttribute('srcset') || '';
                    for (const candidate of srcset.split(',')) {
                      const url = candidate.trim().split(/\\s+/)[0];
                      if (url) {
                        add(url, score - 5);
                      }
                    }
                  }

                  const styledNodes = Array.from(document.querySelectorAll('[style*="background-image"]'));
                  for (const node of styledNodes) {
                    const style = window.getComputedStyle(node);
                    const backgroundImage = style.backgroundImage || '';
                    const matches = [...backgroundImage.matchAll(/url\\((['"]?)(.*?)\\1\\)/g)];
                    const rect = node.getBoundingClientRect();
                    const visibleArea = Math.max(0, rect.width) * Math.max(0, rect.height);
                    const score = (visibleArea >= 40000 ? 20 : 0) + (visibleArea >= 120000 ? 15 : 0);
                    for (const match of matches) {
                      const url = match[2] || '';
                      if (url) {
                        add(url, score);
                      }
                    }
                  }

                  return results
                    .filter((item) => item.url.startsWith('http'))
                    .sort((left, right) => right.score - left.score)
                    .map((item) => item.url);
                }
                """
            )
        except Exception:
            return []
        if not isinstance(candidates, list):
            return []
        filtered: list[str] = []
        for candidate in candidates:
            value = str(candidate).strip()
            if not value:
                continue
            lowered = value.lower()
            if not lowered.startswith("http"):
                continue
            if "emoji.php" in lowered or "static.xx.fbcdn.net" in lowered:
                continue
            if value not in filtered:
                filtered.append(value)
        return filtered[:12]

    def _capture_listing_assets(self, page: Any, image_urls: list[str]) -> list[dict[str, Any]]:
        if not self.config.asset_persistor:
            return []
        assets: list[dict[str, Any]] = []
        for index, image_url in enumerate(image_urls):
            fetched = self._fetch_authenticated_image(page, image_url)
            if not fetched:
                continue
            file_name = self._asset_filename(image_url, fetched.get("content_type"), index)
            asset = self.config.asset_persistor(
                fetched["bytes"],
                fetched.get("content_type"),
                file_name,
                image_url,
            )
            if isinstance(asset, dict):
                assets.append(asset)
        return assets

    def _fetch_authenticated_image(self, page: Any, image_url: str) -> dict[str, Any] | None:
        if not image_url:
            return None
        try:
            response = page.context.request.get(
                image_url,
                headers={
                    "Referer": page.url,
                },
            )
        except Exception:
            return None
        if not response.ok:
            return None
        try:
            return {
                "bytes": response.body(),
                "content_type": str(response.headers.get("content-type") or "").strip() or None,
            }
        except Exception:
            return None

    def _asset_filename(self, image_url: str, content_type: str | None, index: int) -> str:
        parsed = urlparse(image_url)
        candidate = Path(parsed.path or "").name
        if candidate and "." in candidate:
            return candidate
        extension = mimetypes.guess_extension(content_type or "") or ".jpg"
        if extension == ".jpe":
            extension = ".jpg"
        return f"{self.spec.marketplace}-import-{index + 1}{extension}"

    def _coerce_price(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"(\d[\d,]*\.?\d{0,2})", str(value))
        if not match:
            return None
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None


class FacebookMarketplaceBrowserRunner(MarketplaceBrowserRunner):
    def __init__(self, config: BrowserRunnerConfig) -> None:
        super().__init__(config, MARKETPLACE_BROWSER_SPECS["facebook"])

    def _validate_storage_state(self, storage_state: dict[str, Any]) -> None:
        cookies = storage_state.get("cookies") or []
        if not any(str(cookie.get("name") or "") == "c_user" for cookie in cookies):
            raise BrowserRunnerError("Facebook connect finished without capturing an authenticated browser session")

    def _has_authenticated_session(self, context: Any) -> bool:
        try:
            cookies = context.cookies()
        except Exception:
            return False
        return any(str(cookie.get("name") or "") == "c_user" and str(cookie.get("value") or "").strip() for cookie in cookies)


class MercariMarketplaceBrowserRunner(MarketplaceBrowserRunner):
    def __init__(self, config: BrowserRunnerConfig) -> None:
        super().__init__(config, MARKETPLACE_BROWSER_SPECS["mercari"])


class PoshmarkMarketplaceBrowserRunner(MarketplaceBrowserRunner):
    def __init__(self, config: BrowserRunnerConfig) -> None:
        super().__init__(config, MARKETPLACE_BROWSER_SPECS["poshmark"])


class EtsyMarketplaceBrowserRunner(MarketplaceBrowserRunner):
    def __init__(self, config: BrowserRunnerConfig) -> None:
        super().__init__(config, MARKETPLACE_BROWSER_SPECS["etsy"])


class WhatnotMarketplaceBrowserRunner(MarketplaceBrowserRunner):
    def __init__(self, config: BrowserRunnerConfig) -> None:
        super().__init__(config, MARKETPLACE_BROWSER_SPECS["whatnot"])

    def _has_authenticated_session(self, context: Any) -> bool:
        try:
            cookies = context.cookies()
        except Exception:
            return False
        claims_cookie = next((cookie for cookie in cookies if str(cookie.get("name") or "") == "__Secure-claims"), None)
        if not claims_cookie:
            return False
        value = str(claims_cookie.get("value") or "").strip()
        if not value:
            return False
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
        except Exception:
            return False
        try:
            return int(payload.get("u") or 0) > 0
        except (TypeError, ValueError):
            return False


class DepopMarketplaceBrowserRunner(MarketplaceBrowserRunner):
    def __init__(self, config: BrowserRunnerConfig) -> None:
        super().__init__(config, MARKETPLACE_BROWSER_SPECS["depop"])


class VintedMarketplaceBrowserRunner(MarketplaceBrowserRunner):
    def __init__(self, config: BrowserRunnerConfig) -> None:
        super().__init__(config, MARKETPLACE_BROWSER_SPECS["vinted"])


class AmazonMarketplaceBrowserRunner(MarketplaceBrowserRunner):
    def __init__(self, config: BrowserRunnerConfig) -> None:
        super().__init__(config, MARKETPLACE_BROWSER_SPECS["amazon"])

    def run_import(self, *, job_id: str, bridge_account: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        playwright = self._load_playwright()
        screenshot_dir = self.config.screenshots_dir
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        overview_path = screenshot_dir / f"{job_id}-amazon-media-capture.png"

        raw_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        requested_asins = payload.get("asins") or raw_payload.get("asins") or []
        single_asin = payload.get("asin") or raw_payload.get("asin")
        if single_asin:
            requested_asins = [*requested_asins, single_asin]
        normalized_asins = []
        for value in requested_asins:
            asin = str(value or "").strip().upper()
            if re.fullmatch(r"[A-Z0-9]{10}", asin) and asin not in normalized_asins:
                normalized_asins.append(asin)
        if not normalized_asins:
            raise BrowserRunnerError("Amazon import requires at least one valid ASIN")

        with playwright as playwright_instance:
            browser = playwright_instance.chromium.launch(headless=self.config.headless)
            try:
                context = self._new_context(browser, bridge_account)
                page = context.new_page()
                page.goto("https://www.amazon.com/", wait_until="domcontentloaded")
                page.screenshot(path=str(overview_path), full_page=True)
                page.close()
                imported_listings: list[dict[str, Any]] = []
                for asin in normalized_asins:
                    product_url = f"https://www.amazon.com/dp/{asin}"
                    item_page = context.new_page()
                    try:
                        item_page.goto(product_url, wait_until="domcontentloaded")
                        item_page.wait_for_timeout(1500)
                        image_urls = self._extract_amazon_product_images(item_page)
                        title = self._extract_amazon_title(item_page)
                    except Exception:
                        image_urls = []
                        title = None
                    finally:
                        item_page.close()
                    imported_listings.append(
                        {
                            "source_listing_reference": asin,
                            "source_url": product_url,
                            "asin": asin,
                            "image_urls": image_urls,
                            "title": title,
                        }
                    )

                final_storage_state = context.storage_state()
                return {
                    "job_id": job_id,
                    "marketplace": self.spec.marketplace,
                    "status": "import_completed",
                    "bridge_account": self._bridge_account_summary(bridge_account),
                    "imported_listing_count": len(imported_listings),
                    "imported_listings": imported_listings,
                    "screenshots": {
                        "capture_overview": self._persist_generated_asset(overview_path),
                    },
                    "session_state": {
                        "session_state": "active",
                        "session_payload": final_storage_state,
                    },
                }
            except playwright._timeout_error as exc:
                raise BrowserRunnerError(f"Amazon media capture timed out: {exc}") from exc
            finally:
                browser.close()

    def _extract_amazon_title(self, page: Any) -> str | None:
        selectors = ("#productTitle", "h1.a-size-large", "h1#title")
        for selector in selectors:
            try:
                node = page.query_selector(selector)
                if not node:
                    continue
                value = (node.inner_text() or "").strip()
                if value:
                    return value
            except Exception:
                continue
        return None

    def _extract_amazon_product_images(self, page: Any) -> list[str]:
        urls: list[str] = []
        try:
            dom_urls = page.evaluate(
                """
                () => {
                  const out = new Set();
                  const push = (v) => {
                    if (!v || typeof v !== 'string') return;
                    const raw = v.trim();
                    if (!raw) return;
                    let cleaned = raw.replace(/\\._[A-Z0-9_,]+_\\./g, '.');
                    cleaned = cleaned.replace(/\\._AC_[A-Z0-9_,]+_\\./g, '.');
                    if (/^https?:\\/\\//i.test(cleaned) && /\\.(jpg|jpeg|png|webp)(\\?|$)/i.test(cleaned)) out.add(cleaned);
                  };
                  document.querySelectorAll('#imgTagWrapperId img, #landingImage, #altImages img, img[data-old-hires], img[src*="images-amazon.com/images/I/"]').forEach((img) => {
                    push(img.getAttribute('data-old-hires'));
                    push(img.getAttribute('data-a-dynamic-image') ? Object.keys(JSON.parse(img.getAttribute('data-a-dynamic-image')) || {})[0] : null);
                    push(img.getAttribute('src'));
                  });
                  return [...out];
                }
                """
            )
            if isinstance(dom_urls, list):
                urls.extend(str(item) for item in dom_urls if str(item).strip())
        except Exception:
            pass

        try:
            html = page.content()
            for match in re.findall(r'"hiRes"\s*:\s*"([^"]+)"', html):
                urls.append(match)
            for match in re.findall(r'"large"\s*:\s*"([^"]+)"', html):
                urls.append(match)
        except Exception:
            pass

        deduped: list[str] = []
        for raw in urls:
            cleaned = str(raw or "").strip().replace("\\u0026", "&").replace("\\/", "/")
            cleaned = re.sub(r"\._[A-Z0-9_,]+_\.", ".", cleaned)
            if cleaned.startswith("http") and cleaned not in deduped:
                deduped.append(cleaned)
        return deduped[:12]


def create_marketplace_browser_runner(marketplace: str, config: BrowserRunnerConfig) -> MarketplaceBrowserRunner:
    normalized = str(marketplace or "").strip().lower()
    runners: dict[str, type[MarketplaceBrowserRunner]] = {
        "amazon": AmazonMarketplaceBrowserRunner,
        "facebook": FacebookMarketplaceBrowserRunner,
        "mercari": MercariMarketplaceBrowserRunner,
        "poshmark": PoshmarkMarketplaceBrowserRunner,
        "etsy": EtsyMarketplaceBrowserRunner,
        "depop": DepopMarketplaceBrowserRunner,
        "whatnot": WhatnotMarketplaceBrowserRunner,
        "vinted": VintedMarketplaceBrowserRunner,
    }
    runner_class = runners.get(normalized)
    if not runner_class:
        raise BrowserRunnerError(f"No browser runner is configured for marketplace '{marketplace}'")
    return runner_class(config)
