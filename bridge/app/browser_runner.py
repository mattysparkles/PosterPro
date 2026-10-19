from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import tempfile
import time
from datetime import datetime
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, urlparse

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
    "ebay": MarketplaceBrowserSpec(
        marketplace="ebay",
        label="eBay",
        home_url="https://www.ebay.com/",
        create_url="https://www.ebay.com/sl/sell",
        auth_check_url="https://www.ebay.com/sh/lst/active",
        connect_start_url="https://signin.ebay.com/",
        title_selectors=(
            'input[aria-label*="title" i]',
            'input[placeholder*="title" i]',
            'input[name*="title" i]',
            'input[type="text"]',
        ),
        price_selectors=(
            'input[aria-label*="price" i]',
            'input[placeholder*="price" i]',
            'input[name*="price" i]',
            'input[inputmode="decimal"]',
            'input[inputmode="numeric"]',
        ),
        description_selectors=(
            'textarea[aria-label*="description" i]',
            'textarea[placeholder*="description" i]',
            'textarea[name*="description" i]',
            "textarea",
        ),
        submit_selectors=(
            'button:has-text("List it")',
            'button:has-text("List item")',
            'button:has-text("Publish")',
            'button:has-text("Continue")',
            '[role="button"]:has-text("List it")',
        ),
        auth_cookie_names=("nonsession", "s"),
        auth_url_tokens=("/sh/", "/sl/sell", "ebay.com"),
        auth_required_text=("seller hub", "selling", "list an item"),
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
        if not shipping_scope:
            delivery_method = str(listing_payload.get("delivery_method") or "").strip().lower().replace("_", " ")
            shipping_scope = "shipping_only" if delivery_method == "shipping" else "local_and_shipping" if delivery_method in {"local and shipping", "both"} else None

        if not title:
            raise BrowserRunnerError("Browser runner requires a listing title")
        if price in (None, ""):
            raise BrowserRunnerError("Browser runner requires a listing price")

        screenshot_dir = self.config.screenshots_dir
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        before_path = screenshot_dir / f"{job_id}-before-submit.png"
        after_path = screenshot_dir / f"{job_id}-after-submit.png"

        temp_files: list[Path] = []
        screenshot_errors: list[str] = []
        listing_urls: list[str] = []
        marketplace_listing_id: str | None = None
        submission_visibility: dict[str, Any] | None = None
        submitted = False
        submission_click_attempted = False
        submission_stage = "opening_form"
        with playwright as playwright_instance:
            browser = playwright_instance.chromium.launch(headless=self.config.headless)
            page = None
            try:
                context = self._new_context(browser, bridge_account)
                page = context.new_page()
                if self.spec.marketplace == "facebook":
                    # Facebook frequently renders hidden duplicate controls;
                    # a 120s default wait per locator multiplied into minutes
                    # of worker starvation. Keep form interactions bounded and
                    # let the durable job report the real failing field.
                    page.set_default_timeout(min(int(self.config.timeout_ms or 15000), 15000))
                    page.goto(self.spec.create_url, wait_until="commit", timeout=self.config.timeout_ms)
                else:
                    page.goto(self.spec.create_url, wait_until="domcontentloaded")
                self._ensure_logged_in(page)
                self._require_authenticated_page(page)
                self._dismiss_optional_dialogs(page)
                if self.spec.marketplace == "facebook":
                    self._wait_for_facebook_marketplace_form(page)

                uploaded_count = 0
                submission_stage = "filling_listing_form"
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
                try:
                    page.screenshot(path=str(before_path), full_page=True, timeout=15000)
                except Exception as exc:
                    screenshot_errors.append(f"before_submit:{exc}")

                submission_stage = "ready_to_submit"
                if self.config.submit_enabled:
                    if self.spec.marketplace == "facebook":
                        submit_result = self._click_facebook_publish_flow(page)
                        submission_click_attempted = bool(submit_result.get("publish_click_attempted"))
                        submitted = bool(submit_result.get("submitted"))
                        if submit_result.get("error"):
                            submission_stage = "facebook_publish_click_unconfirmed"
                            raise BrowserRunnerError(
                                "Facebook publish click could not be confirmed; remote state must be reconciled. "
                                + str(submit_result["error"])
                            )
                    else:
                        submitted = self._click_first(page, list(self.spec.submit_selectors))
                    if not submitted:
                        submission_stage = "pre_submit_action_missing"
                        raise BrowserRunnerError(f"Could not find a publish action on the {self.spec.label} listing flow")
                    submission_stage = "submitted_waiting_for_remote_confirmation"
                    page.wait_for_timeout(3000)
                    try:
                        page.goto(self.spec.auth_check_url, wait_until="domcontentloaded")
                        self._ensure_logged_in(page)
                        self._require_authenticated_page(page)
                        self._dismiss_optional_dialogs(page)
                        page.wait_for_timeout(1500)
                        if self.spec.marketplace == "facebook":
                            listing_cards = self._collect_facebook_selling_cards(page, expected_title=title)
                            matching_cards = [card for card in listing_cards if card.get("title_matches") and card.get("looks_new")]
                            if matching_cards:
                                listing_urls = [str(card["url"]) for card in matching_cards]
                                marketplace_listing_id = self._extract_listing_id(listing_urls[0])
                                submission_visibility = {
                                    "state": "visible_listing",
                                    "listing_count": len(listing_urls),
                                    "listing_urls": listing_urls,
                                    "matched_title": matching_cards[0].get("title"),
                                    "listing_cards": matching_cards[:3],
                                }
                            else:
                                listing_urls = []
                                submission_visibility = {
                                    "state": "submitted_without_matching_new_listing",
                                    "listing_count": len(listing_cards),
                                    "listing_cards": listing_cards[:3],
                                }
                        else:
                            listing_urls = self._collect_listing_urls(page, max_listings=1)
                            if listing_urls:
                                marketplace_listing_id = self._extract_listing_id(listing_urls[0])
                            submission_visibility = {
                                "state": "visible_listing",
                                "listing_count": len(listing_urls),
                                "listing_urls": listing_urls,
                            }
                    except Exception as exc:
                        submission_visibility = {"state": "submitted_unverified", "error": str(exc)}

                try:
                    page.screenshot(path=str(after_path), full_page=True, timeout=15000)
                except Exception as exc:
                    screenshot_errors.append(f"after_submit:{exc}")
                final_storage_state = context.storage_state()
                return {
                    "job_id": job_id,
                    "marketplace": self.spec.marketplace,
                    "status": "submitted_to_marketplace" if submitted else "draft_form_filled",
                    "submitted": submitted,
                    "marketplace_listing_id": marketplace_listing_id,
                    "listing_urls": listing_urls,
                    "submission_visibility": submission_visibility or {"state": "not_submitted"},
                    "bridge_account": self._bridge_account_summary(bridge_account),
                    "create_url": self.spec.create_url,
                    "uploaded_image_count": uploaded_count,
                    "screenshots": {
                        "before_submit": self._persist_generated_asset(before_path),
                        "after_submit": self._persist_generated_asset(after_path),
                    },
                    "screenshot_errors": screenshot_errors,
                    "session_state": {
                        "session_state": "active",
                        "session_payload": final_storage_state,
                    },
                }
            except BrowserRunnerError as exc:
                submission_state = "REMOTE_STATE_UNKNOWN" if submission_click_attempted else "NOT_SUBMITTED"
                failure_context = {
                    "stage": submission_stage,
                    "submission_performed": bool(submitted),
                    "submission_click_attempted": bool(submission_click_attempted),
                    "submission_state": submission_state,
                }
                if page is not None:
                    try:
                        failure_context["page_url"] = page.url
                    except Exception:
                        pass
                    try:
                        failure_context["page_title"] = page.title()
                    except Exception:
                        pass
                    try:
                        failure_context["body_snippet"] = page.locator("body").inner_text(timeout=2000)[:2000]
                    except Exception:
                        pass
                if failure_context:
                    raise BrowserRunnerError(f"{exc} | context={json.dumps(failure_context, default=str, ensure_ascii=True)}") from exc
                raise
            except playwright._timeout_error as exc:
                submission_state = "REMOTE_STATE_UNKNOWN" if submission_click_attempted else "NOT_SUBMITTED"
                error = (
                    f"{self.spec.label} browser automation timed out: {exc} | "
                    f"context={json.dumps({'stage': submission_stage, 'submission_performed': bool(submitted), 'submission_click_attempted': bool(submission_click_attempted), 'submission_state': submission_state})}"
                )
                raise BrowserRunnerError(error) from exc
            finally:
                browser.close()
                for temp_file in temp_files:
                    temp_file.unlink(missing_ok=True)

    def run_facebook_selling_audit(self, *, job_id: str, bridge_account: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.spec.marketplace != "facebook":
            raise BrowserRunnerError("Selling audit is implemented only for Facebook")
        playwright = self._load_playwright()
        with playwright as playwright_instance:
            browser = playwright_instance.chromium.launch(headless=self.config.headless)
            context = self._new_context(browser, bridge_account)
            try:
                page = context.new_page()
                page.goto(self.spec.auth_check_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                self._ensure_logged_in(page)
                self._dismiss_optional_dialogs(page)
                page.wait_for_timeout(1200)
                initial_url = str(page.url or "")
                initial_title = str(page.title() or "")
                initial_headings = self._facebook_page_headings(page)
                initial_evidence = self._facebook_selling_page_evidence(page)
                if not initial_evidence["seller_listing_page_detected"]:
                    self._navigate_facebook_to_selling_inventory(page)
                self._ensure_logged_in(page)
                final_url = str(page.url or "")
                final_title = str(page.title() or "")
                headings = self._facebook_page_headings(page)
                evidence = self._facebook_selling_page_evidence(page)
                if not evidence["seller_listing_page_detected"]:
                    raise BrowserRunnerError(
                        "Facebook authenticated session did not reach a verified seller inventory page; "
                        f"url={final_url!r}, title={final_title!r}, headings={headings!r}"
                    )
                all_listings_selected = self._select_facebook_all_listings(page)
                page.wait_for_timeout(1000)
                cards_first_viewport = self._count_visible_facebook_selling_cards(page)
                if (payload or {}).get("inspection_only"):
                    cards, scroll_iterations, reached_end = [], 0, False
                else:
                    cards, scroll_iterations, reached_end = self._collect_all_facebook_selling_cards(page)
                inspection_ids = {str(value).strip() for value in ((payload or {}).get("inspection_remote_ids") or []) if str(value).strip()}
                if inspection_ids:
                    identity_probes = 0
                    category_probe = (payload or {}).get("category_probe") or {}
                    edit_inspections = self._inspect_facebook_existing_listing_edit_forms(page, cards, inspection_ids, category_probe=category_probe)
                else:
                    identity_probes = self._resolve_facebook_card_urls_by_opening_existing_cards(page, cards)
                    edit_inspections = []
                return {
                    "job_id": job_id,
                    "marketplace": "facebook",
                    "status": "selling_audit_completed",
                    "audit_page": {
                        "auth_check_url": self.spec.auth_check_url,
                        "initial_url_after_redirects": initial_url,
                        "initial_page_title": initial_title,
                        "initial_headings": initial_headings,
                        "final_page_url": final_url,
                        "page_title": final_title,
                        "headings": headings,
                        "seller_listing_page_detected": True,
                        "marketplace_seller_navigation_present": evidence["marketplace_seller_navigation_present"],
                        "your_listings_or_selling_ui_present": evidence["your_listings_or_selling_ui_present"],
                        "listing_card_selector_used": evidence["listing_card_selector_used"],
                        "listing_card_count_first_viewport": cards_first_viewport,
                        "all_listings_view_selected": all_listings_selected,
                        "scroll_iterations": scroll_iterations,
                        "explicit_end_of_list": reached_end,
                        "read_only_card_identity_probes": identity_probes,
                        "edit_form_inspection_count": len(edit_inspections),
                        "listing_related_anchor_count": self._count_facebook_listing_anchors(page),
                        "seller_section_text": self._facebook_seller_section_text(page),
                        "seller_dom_diagnostics": self._facebook_seller_dom_diagnostics(page),
                    },
                    "listing_cards": cards,
                    "edit_form_inspections": edit_inspections,
                    "listing_card_count": len(cards),
                    "unique_remote_ids": len({str(card.get("remote_id")) for card in cards if card.get("remote_id")}),
                    "unique_remote_urls": len({str(card.get("url")) for card in cards if card.get("url")}),
                    "session_state": {"session_state": "active", "session_payload": context.storage_state()},
                }
            finally:
                context.close()
                browser.close()

    def run_facebook_existing_listing_category_repair(self, *, job_id: str, bridge_account: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Edit one verified Facebook listing in place, changing only its category."""
        if self.spec.marketplace != "facebook":
            raise BrowserRunnerError("Existing-listing category repair is implemented only for Facebook")
        remote_id = str(payload.get("remote_id") or "").strip()
        expected_title = str(payload.get("expected_title") or "").strip()
        expected_price = str(payload.get("expected_price") or "").strip()
        expected_category = str(payload.get("expected_category") or "").strip()
        target_leaf = str(payload.get("target_category_leaf") or "").strip()
        target_parent = str(payload.get("target_category_parent") or "").strip()
        if not (remote_id.isdigit() and expected_title and expected_price and expected_category and target_leaf and target_parent):
            raise BrowserRunnerError("Category repair requires a numeric remote ID and exact expected/current/target listing facts")
        playwright = self._load_playwright()
        with playwright as playwright_instance:
            browser = playwright_instance.chromium.launch(headless=self.config.headless)
            context = self._new_context(browser, bridge_account)
            try:
                page = context.new_page()
                page.goto(self.spec.auth_check_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                self._ensure_logged_in(page)
                self._dismiss_optional_dialogs(page)
                page.wait_for_timeout(900)
                listing_url = f"https://www.facebook.com/marketplace/item/{remote_id}"
                edit_url = f"https://www.facebook.com/marketplace/edit/?listing_id={remote_id}"
                page.goto(listing_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                page.wait_for_timeout(900)
                public_precondition = page.evaluate("""() => {
                  const body=(document.body?.innerText||'').replace(/\\s+/g,' ').trim();
                  const headings=[...document.querySelectorAll('h1,h2,[role=heading]')].map(e=>(e.innerText||'').trim()).filter(Boolean);
                  return {url:location.href,headings,title_present:body.includes(__EXPECTED_TITLE__),price_present:body.includes(__EXPECTED_PRICE__)};
                }""".replace("__EXPECTED_TITLE__", json.dumps(expected_title)).replace("__EXPECTED_PRICE__", json.dumps(expected_price)))
                if not str(public_precondition.get("url") or "").rstrip("/").endswith("/" + remote_id) or not public_precondition.get("title_present") or not public_precondition.get("price_present"):
                    raise BrowserRunnerError("Direct buyer page did not match the approved remote ID/title/price; no edit was made")
                page.goto(edit_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                page.wait_for_timeout(1800)
                try:
                    page.locator('input[aria-label="Category"][role="combobox"]').wait_for(state="visible", timeout=min(self.config.timeout_ms, 12000))
                except Exception:
                    pass
                if f"listing_id={remote_id}" not in str(page.url or "") or "Edit listing" not in self._page_text(page):
                    raise BrowserRunnerError(
                        "Facebook did not open the exact existing listing edit form; "
                        f"url={page.url!r}, title={page.title()!r}, headings={self._facebook_page_headings(page)!r}"
                    )
                category_input = page.locator('input[aria-label="Category"][role="combobox"]')
                if category_input.count() != 1:
                    raise BrowserRunnerError("Edit form did not expose exactly one Category combobox")
                before = self._facebook_edit_form_snapshot(page)
                if before.get("category") != expected_category or before.get("price") != expected_price:
                    raise BrowserRunnerError("Edit form category or price differed from approved preconditions; no edit was made")

                if before.get("category") == target_leaf:
                    # The approved category is already persisted. Avoid reopening
                    # Facebook's category search, whose asynchronous thumbnails can
                    # pollute photo-preservation snapshots; this run changes delivery only.
                    option_text = f"{target_leaf} {target_parent} · Shipping available (already selected and previously validated)"
                    selected = before
                else:
                    category_input.click()
                    category_input.fill("")
                    category_input.press_sequentially("Home", delay=70)
                    page.wait_for_timeout(1200)
                    options = page.locator('[role="option"]').filter(has_text=target_leaf).filter(has_text=target_parent).filter(has_text="Shipping available")
                    if options.count() != 1:
                        raise BrowserRunnerError(f"Expected one shipping-capable {target_leaf} / {target_parent} category option; found {options.count()}; no edit was saved")
                    option_text = " ".join((options.first.inner_text() or "").split())
                    if target_leaf not in option_text or target_parent not in option_text or "Shipping available" not in option_text:
                        raise BrowserRunnerError("Category suggestion did not prove the exact target and shipping capability")
                    options.first.click()
                    page.wait_for_timeout(500)
                    selected = self._facebook_edit_form_snapshot(page)
                if selected.get("category") not in {target_leaf, target_parent}:
                    raise BrowserRunnerError("Facebook did not set the selected category in the form; no edit was saved")
                pre_save_checks = {
                    "price_preserved": selected.get("price") == before.get("price"),
                    "title_and_description_preserved": selected.get("copy_fingerprint") == before.get("copy_fingerprint"),
                    "photos_preserved": selected.get("image_fingerprint") == before.get("image_fingerprint"),
                    "condition_preserved": selected.get("condition_fingerprint") == before.get("condition_fingerprint"),
                }
                if not all(pre_save_checks.values()):
                    raise BrowserRunnerError(
                        f"Pre-save preservation check failed ({pre_save_checks}); no edit was saved; "
                        f"image_counts={before.get('image_count')}->{selected.get('image_count')}"
                    )

                next_button = page.get_by_role("button", name="Next", exact=True)
                if next_button.count() != 1:
                    raise BrowserRunnerError("Expected exactly one Next button in the existing listing form; no edit was saved")
                next_button.click()
                page.wait_for_timeout(1000)
                delivery_picker = page.get_by_role("combobox", name="Delivery method")
                try:
                    delivery_picker.wait_for(state="visible", timeout=min(self.config.timeout_ms, 8000))
                except Exception:
                    pass
                if delivery_picker.count() != 1:
                    delivery_diagnostic = page.evaluate("""() => ({url:location.href,title:document.title,headings:[...document.querySelectorAll('h1,h2,[role=heading]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>(e.innerText||'').replace(/\\s+/g,' ').trim()).filter(Boolean).slice(0,15),controls:[...document.querySelectorAll('input,button,[role=button],[role=combobox]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>({role:e.getAttribute('role'),label:e.getAttribute('aria-label'),text:(e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,120)})).slice(-30),tail:(document.body?.innerText||'').replace(/\\s+/g,' ').trim().slice(-1000)})""")
                    raise BrowserRunnerError(f"Delivery review did not expose one per-listing Delivery method control after bounded wait; no final save was attempted; evidence={delivery_diagnostic!r}")
                delivery_picker.click()
                page.wait_for_timeout(250)
                def read_delivery_choices() -> list[dict[str, Any]]:
                    return page.evaluate("""() => [...document.querySelectorAll('[role=menuitemcheckbox]')]
                      .filter(e=>!!(e.offsetWidth||e.offsetHeight))
                      .map(e=>({text:(e.innerText||e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim(),checked:e.getAttribute('aria-checked')==='true',disabled:e.getAttribute('aria-disabled')==='true'}))""")
                delivery_choices = read_delivery_choices()
                shipping_choices = [item for item in delivery_choices if re.match(r"^shipping\b", item.get("text", ""), re.I)]
                if len(shipping_choices) != 1 or shipping_choices[0].get("disabled"):
                    raise BrowserRunnerError(f"Delivery menu did not expose one enabled Shipping choice; no final save was attempted; choices={delivery_choices!r}")
                if not shipping_choices[0]["checked"]:
                    shipping_item = page.get_by_role("menuitemcheckbox", name=re.compile(r"^Shipping\b", re.I))
                    if shipping_item.count() != 1:
                        raise BrowserRunnerError("Shipping menu choice was ambiguous; no final save was attempted")
                    shipping_item.click()
                    page.wait_for_timeout(350)
                    delivery_choices = read_delivery_choices()
                    if not delivery_choices:
                        # Facebook may close this multi-select menu after a toggle;
                        # reopen it only to read the persisted-in-form check state.
                        delivery_picker.click()
                        page.wait_for_timeout(250)
                        delivery_choices = read_delivery_choices()
                    shipping_choices = [item for item in delivery_choices if re.match(r"^shipping\b", item.get("text", ""), re.I)]
                if len(shipping_choices) != 1 or not shipping_choices[0]["checked"]:
                    raise BrowserRunnerError(f"Per-listing Shipping choice did not become selected; no final save was attempted; choices={delivery_choices!r}")
                page.keyboard.press("Escape")
                page.wait_for_timeout(200)
                menu_still_open = page.locator('[role="menuitemcheckbox"]').count() > 0
                if menu_still_open:
                    delivery_picker.click()
                    page.wait_for_timeout(250)
                delivery_state = {"url": str(page.url), "choices": delivery_choices, "shipping_selected_before_save": True}
                update_button = page.get_by_role("button", name="Update", exact=True)
                if update_button.count() != 1:
                    update_button = page.get_by_role("button", name="Update listing", exact=True)
                advance_diagnostics: dict[str, Any] = {
                    "next_role_count": page.get_by_role("button", name="Next", exact=True).count(),
                    "next_disabled": page.evaluate("""() => [...document.querySelectorAll('[aria-label="Next"]')]
                      .filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>e.getAttribute('aria-disabled'))"""),
                }
                if "true" in (advance_diagnostics.get("next_disabled") or []):
                    label_picker = page.get_by_role("button", name="Select shipping label", exact=True)
                    if label_picker.count() == 1:
                        label_picker.click()
                        page.wait_for_timeout(1600)
                        label_dialog = page.evaluate("""() => {
                          const visible=e=>!!(e.offsetWidth||e.offsetHeight);
                          const dialog=document.querySelector('[role=dialog][aria-label="Change shipping method"]');
                          const selectors=dialog?[...dialog.querySelectorAll('[role=combobox]')].filter(visible).map(e=>(e.innerText||'').replace(/\\s+/g,' ').trim()).filter(Boolean):[];
                          const checkboxes=dialog?[...dialog.querySelectorAll('[role=checkbox],[role=switch]')].filter(visible).map(e=>({text:(e.innerText||e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim(),checked:e.getAttribute('aria-checked')==='true'})):[];
                          return {dialog_open:!!dialog,selectors,checkboxes};
                        }""")
                        selector_names = {str(value).strip().casefold() for value in (label_dialog.get("selectors") or [])}
                        missing = [name for name, label in (("package weight", "package weight"), ("shipping carrier", "shipping carrier")) if label in selector_names]
                        reason = ", ".join(missing) if missing else "shipping-label setup details"
                        raise BrowserRunnerError(f"Facebook requires {reason} before shipping can be saved; no unsupported value was inferred and no final update was attempted")
                if update_button.count() != 1:
                    advance_button = page.locator('[aria-label="Next"]:visible')
                    advance_diagnostics["visible_next_selector_count"] = advance_button.count()
                    if advance_button.count() == 1 and not advance_button.is_disabled():
                        advance_button.click()
                        page.wait_for_timeout(900)
                        advance_diagnostics["after_next"] = page.evaluate("""() => ({url:location.href,headings:[...document.querySelectorAll('h1,h2,[role=heading]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,12),actions:[...document.querySelectorAll('button,[role=button]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>({text:(e.innerText||'').replace(/\\s+/g,' ').trim(),aria:e.getAttribute('aria-label'),disabled:!!e.disabled})).slice(-20),text:(document.body?.innerText||'').replace(/\\s+/g,' ').trim().slice(-900)})""")
                        if f"listing_id={remote_id}" not in str(page.url or ""):
                            raise BrowserRunnerError("Facebook left the exact existing-listing workflow after delivery review; no final save was attempted")
                        update_button = page.get_by_role("button", name="Update", exact=True)
                        if update_button.count() != 1:
                            update_button = page.get_by_role("button", name="Update listing", exact=True)
                if update_button.count() != 1:
                    raise BrowserRunnerError("Facebook did not expose one exact Update action after review; no final save was attempted")
                update_button.click()
                page.wait_for_timeout(1800)

                # Re-open this same remote ID to verify persisted facts, then inspect its buyer page.
                page.goto(edit_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                page.wait_for_timeout(1100)
                if f"listing_id={remote_id}" not in str(page.url or "") or "Edit listing" not in self._page_text(page):
                    raise BrowserRunnerError("After save, Facebook did not reopen the same remote listing for verification")
                after = self._facebook_edit_form_snapshot(page)
                category_ok = after.get("category") in {target_leaf, target_parent}
                photos_preserved = set(before.get("image_paths") or []).issubset(set(after.get("image_paths") or []))
                preserved = (
                    after.get("price") == before.get("price")
                    and after.get("copy_fingerprint") == before.get("copy_fingerprint")
                    and photos_preserved
                    and after.get("condition_fingerprint") == before.get("condition_fingerprint")
                )
                post_save_shipping = False
                verify_next = page.get_by_role("button", name="Next", exact=True)
                if verify_next.count() == 1:
                    verify_next.click()
                    page.wait_for_timeout(800)
                    verify_picker = page.get_by_role("combobox", name="Delivery method")
                    if verify_picker.count() == 1:
                        verify_picker.click()
                        page.wait_for_timeout(250)
                        verify_choices = page.evaluate("""() => [...document.querySelectorAll('[role=menuitemcheckbox]')]
                          .filter(e=>!!(e.offsetWidth||e.offsetHeight))
                          .map(e=>({text:(e.innerText||e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim(),checked:e.getAttribute('aria-checked')==='true'}))""")
                        verified_shipping = [item for item in verify_choices if re.match(r"^shipping\b", item.get("text", ""), re.I)]
                        post_save_shipping = len(verified_shipping) == 1 and verified_shipping[0]["checked"]
                page.goto(listing_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                page.wait_for_timeout(900)
                public = page.evaluate("""() => {
                  const text=(document.body?.innerText||'').replace(/\\s+/g,' ').trim();
                  const snippets=[];
                  for(const term of ['shipping','ships for','ships to','local pickup','pickup only']){const i=text.toLowerCase().indexOf(term);if(i>=0)snippets.push(text.slice(Math.max(0,i-70),Math.min(text.length,i+180)));}
                  return {url:location.href,title:document.title,shipping_snippets:snippets};
                }""")
                buyer_page_shipping = any("ships for" in value.lower() or "ships to" in value.lower() for value in public.get("shipping_snippets", []))
                shipping_verified = post_save_shipping and buyer_page_shipping
                identity_preserved = str(public.get("url") or "").rstrip("/").endswith("/" + remote_id)
                return {
                    "job_id": job_id,
                    "marketplace": "facebook",
                    "status": "existing_listing_category_repair_completed" if category_ok and preserved and shipping_verified and identity_preserved else "existing_listing_category_repair_verification_incomplete",
                    "publisher_path": "hosted_browser_assist",
                    "remote_id": remote_id,
                    "remote_url": listing_url,
                    "pre_edit_public_headings": public_precondition.get("headings"),
                    "pre_edit_remote_identity_verified": True,
                    "category_before": before.get("category"),
                    "category_after": after.get("category"),
                    "price_preserved": after.get("price") == before.get("price"),
                    "description_and_title_preserved": after.get("copy_fingerprint") == before.get("copy_fingerprint"),
                    "photos_preserved": photos_preserved,
                    "remote_identity_preserved": identity_preserved,
                    "shipping_capable_category_selected": "Shipping available" in option_text,
                    "shipping_verified_on_buyer_page": shipping_verified,
                    "per_listing_shipping_selected_before_save": delivery_state.get("shipping_selected_before_save", False),
                    "per_listing_shipping_selected_after_save": post_save_shipping,
                    "buyer_page_shipping_evidence": public.get("shipping_snippets", []),
                    "category_option_text": option_text,
                    "session_state": {"session_state": "active", "session_payload": context.storage_state()},
                }
            finally:
                context.close()
                browser.close()

    @staticmethod
    def _facebook_edit_form_snapshot(page: Any) -> dict[str, Any]:
        values = page.evaluate("""() => {
          const visible=e=>!!(e.offsetWidth||e.offsetHeight);
          const controls=[...document.querySelectorAll('input,textarea,select,[contenteditable=true]')].filter(visible).map(e=>({
            label:(e.getAttribute('aria-label')||e.labels?.[0]?.innerText||'').trim(),
            value:e.value!==undefined?String(e.value):(e.innerText||'').trim(),
            type:e.getAttribute('type')||e.tagName
          }));
          const category=controls.find(e=>e.label==='Category')?.value||'';
          const price=controls.find(e=>e.label==='Price')?.value||'';
          const copy=controls.filter(e=>/title|description/i.test(e.label)||e.type==='TEXTAREA'||e.type==='contenteditable').map(e=>[e.label,e.value]);
          const condition=controls.filter(e=>/condition/i.test(e.label)).map(e=>[e.label,e.value]);
          const images=[...document.querySelectorAll('img')].filter(e=>{
            const style=getComputedStyle(e);
            return e.naturalWidth>0&&!!(e.offsetWidth||e.offsetHeight)&&style.visibility!=='hidden'&&style.opacity!=='0'&&!e.closest('[role=option],[role=listbox],[role=menu]');
          }).map(e=>{try{const u=new URL(e.currentSrc||e.src);return u.origin+u.pathname}catch{return ''}}).filter(Boolean).sort();
          return {category,price,copy,condition,image_count:images.length,image_paths:images};
        }""")
        copy_fingerprint = hashlib.sha256(json.dumps(values.get("copy") or [], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        condition_fingerprint = hashlib.sha256(json.dumps(values.get("condition") or [], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        image_fingerprint = hashlib.sha256(json.dumps(values.get("image_paths") or [], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return {
            "category": str(values.get("category") or "").strip(),
            "price": str(values.get("price") or "").strip(),
            "copy_fingerprint": copy_fingerprint,
            "condition_fingerprint": condition_fingerprint,
            "image_fingerprint": image_fingerprint,
            "image_count": int(values.get("image_count") or 0),
            "image_paths": values.get("image_paths") or [],
        }

    @staticmethod
    def _facebook_page_headings(page: Any) -> list[str]:
        try:
            values = page.eval_on_selector_all("h1, h2, [role='heading']", "nodes => nodes.map(n => (n.innerText || '').trim()).filter(Boolean)")
            return list(dict.fromkeys(str(value)[:160] for value in values if str(value).strip()))[:20]
        except Exception:
            return []

    @staticmethod
    def _count_visible_facebook_selling_cards(page: Any) -> int:
        try:
            return int(page.evaluate("""() => [...document.querySelectorAll('[role=button][aria-label]')].filter(n => {
              const r=n.getBoundingClientRect(), t=(n.innerText||'');
              return r.width>0 && r.height>0 && r.bottom>0 && r.top<window.innerHeight && /\\$\\s?\\d/.test(t) && /Listed on|Active|Pending|Sold/i.test(t);
            }).length"""))
        except Exception:
            return 0

    def _facebook_selling_page_evidence(self, page: Any) -> dict[str, Any]:
        try:
            body = self._page_text(page).lower()
        except Exception:
            body = ""
        current_url = str(page.url or "").lower()
        seller_ui = any(token in body for token in ("your listings", "selling", "manage your listings", "listed on"))
        seller_route = "/marketplace/you/selling" in current_url or "/marketplace/you/" in current_url
        nav_present = any(token in body for token in ("marketplace", "buy and sell", "selling"))
        selector = '[href*="/marketplace/item/"], [href*="/commerce/listing/"], [href*="listing_id="]'
        try:
            has_cards = page.locator(selector).count() > 0
        except Exception:
            has_cards = False
        detected = seller_route and (seller_ui or has_cards)
        return {
            "seller_listing_page_detected": detected,
            "marketplace_seller_navigation_present": nav_present,
            "your_listings_or_selling_ui_present": seller_ui,
            "listing_card_selector_used": selector,
        }

    def _navigate_facebook_to_selling_inventory(self, page: Any) -> None:
        # Prefer the site's own Marketplace/Selling navigation when the configured
        # destination redirects to a generic landing page.
        for selector in (
            'a:has-text("Marketplace")',
            '[role="link"]:has-text("Marketplace")',
            'a:has-text("Your listings")',
            '[role="link"]:has-text("Your listings")',
            'a:has-text("Selling")',
            '[role="link"]:has-text("Selling")',
        ):
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    locator.click(timeout=2500)
                    page.wait_for_timeout(1000)
                    if "/marketplace/" not in str(page.url or "").lower():
                        continue
                    for child_selector in ('a:has-text("Your listings")', '[role="link"]:has-text("Your listings")', 'a:has-text("Selling")'):
                        child = page.locator(child_selector).first
                        if child.count() and child.is_visible():
                            child.click(timeout=2500)
                            page.wait_for_timeout(1200)
                            break
                    if self._facebook_selling_page_evidence(page)["seller_listing_page_detected"]:
                        return
            except Exception:
                continue
        # Current Facebook's canonical seller inventory route; this fallback is
        # accepted only after the UI identity check above succeeds.
        page.goto(self.spec.auth_check_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
        page.wait_for_timeout(1200)

    @staticmethod
    def _select_facebook_all_listings(page: Any) -> bool:
        for selector in (
            '[role="tab"]:has-text("All listings")',
            '[role="button"]:has-text("All listings")',
            'a:has-text("All listings")',
        ):
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    locator.click(timeout=2500)
                    return True
            except Exception:
                continue
        try:
            label = page.get_by_text("All listings", exact=True).first
            if label.count() and label.is_visible():
                label.click(timeout=2500)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _count_facebook_listing_anchors(page: Any) -> int:
        try:
            return int(page.locator('a[href*="/marketplace/"], a[href*="/commerce/"], a[href*="listing_id="]').count())
        except Exception:
            return 0

    @staticmethod
    def _facebook_seller_dom_diagnostics(page: Any) -> dict[str, Any]:
        try:
            return page.evaluate("""() => {
              const norm = e => (e.innerText || '').replace(/\\s+/g, ' ').trim();
              const labels = [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && /^(all listings|needs attention|your listings)$/i.test(norm(e)));
              const labelInfo = labels.slice(0, 12).map(e => ({text:norm(e), tag:e.tagName, role:e.getAttribute('role'), ariaSelected:e.getAttribute('aria-selected'), parentRole:e.parentElement?.getAttribute('role'), parentText:norm(e.parentElement || {}).slice(0,240)}));
              const candidates = [...document.querySelectorAll('[role=article],[role=row],[role=listitem],[data-testid*="marketplace" i],[data-pagelet*="marketplace" i]')];
              const cardInfo = candidates.slice(0, 30).map(e => ({role:e.getAttribute('role'), testid:e.getAttribute('data-testid'), pagelet:e.getAttribute('data-pagelet'), text:norm(e).slice(0,500)})).filter(x => x.text);
              const visibleText = norm(document.body);
              const start = Math.max(0, visibleText.toLowerCase().indexOf('your listings'));
              const target = 'Smart AI Anti-Snore System Pillow';
              const matches = [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && norm(e).includes(target));
              const ancestors = matches.slice(0,3).map(leaf => { const chain=[]; let e=leaf; for(let i=0;i<8&&e;i++,e=e.parentElement) chain.push({tag:e.tagName,role:e.getAttribute('role'),testid:e.getAttribute('data-testid'),aria:e.getAttribute('aria-label'),text:norm(e).slice(0,900),childCount:e.children.length,anchors:e.querySelectorAll('a[href]').length,buttons:e.querySelectorAll('button,[role=button]').length}); return chain; });
              const scriptHits=[...document.scripts].map(s=>s.textContent||'').filter(s=>s.includes(target)).map(s=>{const i=s.indexOf(target);const excerpt=s.slice(Math.max(0,i-800),i+1000);const ids=[...excerpt.matchAll(/(?:\\"(?:id|listing_id|product_id)\\"|(?:id|listing_id|product_id))\\s*:\\s*\\"?(\\d{8,})/gi)].map(m=>m[1]);return {ids:[...new Set(ids)],excerpt:excerpt.replace(/.{0,80}(?:token|cookie|secret).*/gi,'[redacted]')};}).slice(0,4);
              return {labels:labelInfo, card_candidates:cardInfo, seller_text_excerpt:visibleText.slice(start, start + 12000), listing_href_count:[...document.querySelectorAll('a[href]')].filter(a => /marketplace\\/item|commerce\\/listing|listing_id=/i.test(a.href)).length, target_ancestors:ancestors, target_script_hits:scriptHits};
            }""")
        except Exception:
            return {}

    def _facebook_seller_section_text(self, page: Any) -> str:
        try:
            return page.evaluate("""() => {
              const all = [...document.querySelectorAll('h1,h2,[role=heading]')];
              const heading = all.find(n => /your listings/i.test(n.innerText || ''));
              if (!heading) return '';
              let node = heading;
              for (let i=0; i<4 && node.parentElement; i++, node=node.parentElement) {
                const text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
                if (text.length > 120) return text.slice(0, 2500);
              }
              return (heading.parentElement?.innerText || '').slice(0, 2500);
            }""")
        except Exception:
            return ''

    def _collect_all_facebook_selling_cards(self, page: Any, *, max_iterations: int = 40, stable_iterations: int = 3) -> tuple[list[dict[str, Any]], int, bool]:
        by_id: dict[str, dict[str, Any]] = {}
        stable = 0
        reached_end = False
        iterations = 0
        for iterations in range(1, max_iterations + 1):
            before = len(by_id)
            for card in self._collect_facebook_selling_cards(page, expected_title=""):
                # Facebook may render the same listing in both “Needs attention”
                # and “All listings”; but duplicate-title products can have distinct
                # prices/IDs. Deduplicate by the full row signature, not ID alone.
                key = "|".join(str(card.get(k) or "") for k in ("remote_id", "url", "title", "price", "listed_on"))
                if key:
                    by_id[key] = card
            stable = stable + 1 if len(by_id) == before else 0
            try:
                end_marker = page.get_by_text("No more listings", exact=False).count() or page.get_by_text("You've reached the end", exact=False).count()
            except Exception:
                end_marker = 0
            if end_marker:
                reached_end = True
                break
            if stable >= stable_iterations:
                break
            try:
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(1000)
            except Exception:
                break
        return list(by_id.values()), iterations, reached_end

    def _resolve_facebook_card_urls_by_opening_existing_cards(self, page: Any, cards: list[dict[str, Any]], *, max_probes: int = 40) -> int:
        """Open existing seller cards read-only to reveal their canonical remote URL."""
        probes = 0
        seller_url = self.spec.auth_check_url
        title_counts: dict[str, int] = {}
        id_counts: dict[str, int] = {}
        for card in cards:
            title_key = self._normalize_listing_title(str(card.get("title") or ""))
            if title_key:
                title_counts[title_key] = title_counts.get(title_key, 0) + 1
            remote_id = str(card.get("remote_id") or "").strip()
            if remote_id:
                id_counts[remote_id] = id_counts.get(remote_id, 0) + 1
        for card in cards:
            title_key = self._normalize_listing_title(str(card.get("title") or ""))
            conflicted_identity = bool(
                (card.get("remote_id") and id_counts.get(str(card.get("remote_id")), 0) > 1)
                or title_counts.get(title_key, 0) > 1
            )
            if (card.get("remote_id") or card.get("url")) and not conflicted_identity:
                card["remote_identity_verified"] = True
                continue
            if not card.get("title") or not card.get("price"):
                continue
            if probes >= max_probes:
                break
            title = str(card["title"]).strip()
            price = str(card["price"]).strip()
            try:
                locator = page.get_by_role("button", name=title, exact=True).filter(has_text=price).first
                if not locator.count():
                    continue
                locator.click(timeout=3500)
                page.wait_for_timeout(700)
                probes += 1
                current = str(page.url or "")
                if not card.get("identity_probe_evidence"):
                    try:
                        card["identity_probe_evidence"] = {
                            "opened_url": current,
                            "headings": self._facebook_page_headings(page),
                            "dialogs": page.eval_on_selector_all('[role="dialog"]', "nodes=>nodes.map(n=>(n.innerText||'').replace(/\\s+/g,' ').trim().slice(0,500))"),
                            "listing_links": page.eval_on_selector_all('a[href*="/marketplace/item/"],a[href*="/commerce/listing/"],a[href*="listing_id="]', "nodes=>nodes.slice(0,12).map(n=>({text:(n.innerText||'').trim().slice(0,120),href:n.href||''}))"),
                            "visible_action_labels": page.eval_on_selector_all('button,[role="button"]', "nodes=>nodes.filter(n=>!!(n.offsetWidth||n.offsetHeight)).map(n=>(n.innerText||n.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim()).filter(Boolean).slice(-30)"),
                        }
                    except Exception:
                        card["identity_probe_evidence"] = {"opened_url": current}
                if any(term in title.lower() for term in ("pillow", "access control")):
                    try:
                        edit_button = page.get_by_role("button", name=re.compile("edit listing", re.IGNORECASE)).first
                        if edit_button.count() and edit_button.is_visible():
                            edit_button.click(timeout=3500)
                            page.wait_for_timeout(800)
                            card["edit_view_audit"] = page.evaluate("""() => {
                              const body=(document.body?.innerText||'').replace(/\\s+/g,' ').trim();
                              const terms=['category','shipping','local pickup','pickup','delivery','price','description'];
                              const excerpts=[];
                              for(const term of terms){const i=body.toLowerCase().indexOf(term);if(i>=0)excerpts.push(body.slice(Math.max(0,i-100),Math.min(body.length,i+240)));}
                              const controls=[...document.querySelectorAll('input,textarea,select,[role=combobox],[role=radio],[role=checkbox]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>({tag:e.tagName,type:e.getAttribute('type'),role:e.getAttribute('role'),label:e.getAttribute('aria-label')||e.labels?.[0]?.innerText||'',value:e.value||'',checked:!!e.checked,selected:e.getAttribute('aria-checked')})).slice(0,60);
                              return {url:location.href,headings:[...document.querySelectorAll('h1,h2,[role=heading]')].map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,20),excerpts,controls};
                            }""")
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(250)
                    except Exception:
                        card["edit_view_audit"] = {"state":"not_opened"}
                remote_url = self._normalize_listing_url(current)
                if not remote_url:
                    try:
                        link_candidates = page.eval_on_selector_all(
                            'a[href*="/marketplace/item/"],a[href*="/commerce/listing/"],a[href*="listing_id="]',
                            "nodes=>nodes.map(n=>({href:n.href||'',text:(n.innerText||'').replace(/\\s+/g,' ').trim()}))",
                        )
                        target = self._normalize_listing_title(title)
                        target_price = re.sub(r"[^0-9.]", "", price)
                        matching_links = [
                            item for item in link_candidates
                            if self._normalize_listing_url(str(item.get("href") or ""))
                            and target
                            and target in self._normalize_listing_title(str(item.get("text") or ""))
                            and target_price
                            and target_price in re.sub(r"[^0-9.]", "", str(item.get("text") or ""))
                            and "/marketplace/edit/" not in str(item.get("href") or "").lower()
                        ]
                        remote_url = self._normalize_listing_url(str(matching_links[0].get("href") or "")) if len(matching_links) == 1 else None
                    except Exception:
                        remote_url = None
                if remote_url:
                    card["url"] = remote_url
                    card["remote_id"] = self._extract_listing_id(remote_url)
                    card["remote_identity_verified"] = True
                elif conflicted_identity:
                    card["remote_identity_verified"] = False
                if current != seller_url:
                    try:
                        page.go_back(wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                    except Exception:
                        page.goto(seller_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                    page.wait_for_timeout(800)
                    self._select_facebook_all_listings(page)
                else:
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                    page.wait_for_timeout(250)
            except Exception:
                try:
                    if str(page.url or "") != seller_url:
                        page.goto(seller_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                        page.wait_for_timeout(800)
                        self._select_facebook_all_listings(page)
                except Exception:
                    break
        return probes

    def _inspect_facebook_existing_listing_edit_forms(self, page: Any, cards: list[dict[str, Any]], remote_ids: set[str], *, category_probe: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        inspected: list[dict[str, Any]] = []
        inspected_ids: set[str] = set()
        targets = [card for card in cards if str(card.get("remote_id") or "").strip() in remote_ids]
        known_ids = {str(card.get("remote_id") or "").strip() for card in targets}
        targets.extend({"remote_id": remote_id, "url": f"https://www.facebook.com/marketplace/item/{remote_id}"} for remote_id in sorted(remote_ids - known_ids))
        for card in targets:
            remote_id = str(card.get("remote_id") or "").strip()
            if remote_id not in remote_ids or remote_id in inspected_ids:
                continue
            inspected_ids.add(remote_id)
            title = str(card.get("title") or "").strip()
            price = str(card.get("price") or "").strip()
            try:
                edit_url = f"https://www.facebook.com/marketplace/edit/?listing_id={remote_id}"
                page.goto(edit_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                page.wait_for_timeout(1200)
                try:
                    page.locator('input[aria-label="Category"][role="combobox"]').wait_for(state="visible", timeout=min(self.config.timeout_ms, 12000))
                except Exception:
                    pass
                if f"listing_id={remote_id}" not in str(page.url or ""):
                    inspected.append({"remote_id": remote_id, "title": title, "state": "edit_url_redirected", "observed_url": str(page.url or "")})
                    page.goto(self.spec.auth_check_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                    self._select_facebook_all_listings(page)
                    continue
                evidence = page.evaluate("""() => {
                  const body=(document.body?.innerText||'').replace(/\\s+/g,' ').trim();
                  const terms=['category','shipping','local pickup','pickup','delivery','price','description'];
                  const excerpts=[];
                  for(const term of terms){const i=body.toLowerCase().indexOf(term);if(i>=0)excerpts.push({term,text:body.slice(Math.max(0,i-100),Math.min(body.length,i+260))});}
                  const controls=[...document.querySelectorAll('input,textarea,select,[role=combobox],[role=radio],[role=checkbox]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>({tag:e.tagName,type:e.getAttribute('type'),role:e.getAttribute('role'),label:e.getAttribute('aria-label')||e.labels?.[0]?.innerText||'',value:e.value||'',checked:!!e.checked,selected:e.getAttribute('aria-checked')})).slice(0,60);
                  const categoryWidgets=[...document.querySelectorAll('label,[role=combobox],button,[role=button]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)&&(/category/i.test(e.innerText||'')||/category/i.test(e.getAttribute('aria-label')||''))).map(e=>({tag:e.tagName,role:e.getAttribute('role'),label:e.getAttribute('aria-label')||'',text:(e.innerText||'').trim().slice(0,180),html:e.outerHTML.slice(0,700)})).slice(0,8);
                  const imageCount=[...document.querySelectorAll('img')].filter(e=>e.naturalWidth>0).length;
                  return {url:location.href,headings:[...document.querySelectorAll('h1,h2,[role=heading]')].map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,20),excerpts,controls,category_widgets:categoryWidgets,image_count:imageCount};
                }""")
                if category_probe and str(category_probe.get("remote_id") or "") == remote_id:
                    search_terms = [str(value).strip() for value in (category_probe.get("search_terms") or [category_probe.get("search_term")]) if str(value or "").strip()]
                    category_input = page.locator('input[aria-label="Category"][role="combobox"]')
                    if search_terms and category_input.count() == 1:
                        category_input.click()
                        page.wait_for_timeout(300)
                        evidence["category_probe_before_search"] = page.evaluate("""() => ({
                          expanded:document.querySelector('input[aria-label="Category"][role="combobox"]')?.getAttribute('aria-expanded'),
                          options:[...document.querySelectorAll('[role=option],[role=listbox] *,[role=menuitem]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,30),
                          target_already_present:[...document.querySelectorAll('[role=option],[role=listbox] *,[role=menuitem]')].some(e=>!!(e.offsetWidth||e.offsetHeight)&&/smart home|home automation/i.test(e.innerText||'')),
                          matches:[...document.querySelectorAll('body *')].filter(e=>!!(e.offsetWidth||e.offsetHeight)&&e.children.length<3&&/furniture|smart home|home automation/i.test(e.innerText||'')).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(-20)
                        })""")
                        category_results = []
                        for search_term in search_terms[:6]:
                            category_input.fill("")
                            category_input.press_sequentially(search_term, delay=60)
                            page.wait_for_timeout(1100)
                            category_results.append({"search_term": search_term, **page.evaluate("""() => ({
                              expanded:document.querySelector('input[aria-label="Category"][role="combobox"]')?.getAttribute('aria-expanded'),
                              input_value:document.querySelector('input[aria-label="Category"][role="combobox"]')?.value||'',
                              options:[...document.querySelectorAll('[role=option],[role=listbox] *,[role=menuitem]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,25),
                              matching_nodes:[...document.querySelectorAll('body *')].filter(e=>!!(e.offsetWidth||e.offsetHeight)&&e.children.length<3&&/smart home|home automation|electronics|home improvement/i.test(e.innerText||'')).map(e=>({tag:e.tagName,role:e.getAttribute('role'),text:(e.innerText||'').trim().slice(0,160)})).slice(-15)
                            })""")})
                        evidence["category_probes"] = category_results
                    if category_probe.get("delivery_step") and category_input.count() == 1:
                        current_category = str(category_input.input_value() or "").strip()
                        current_price = next((str(control.get("value") or "").strip() for control in evidence.get("controls", []) if control.get("label") == "Price"), "")
                        expected_category = str(category_probe.get("expected_category") or "").strip()
                        expected_price = str(category_probe.get("expected_price") or "").strip()
                        if current_category != expected_category or current_price != expected_price:
                            evidence["delivery_step_probe"] = {"state": "precondition_mismatch", "category": current_category, "price": current_price}
                        else:
                            next_button = page.get_by_role("button", name="Next", exact=True)
                            if next_button.count() == 1:
                                next_button.click()
                                page.wait_for_timeout(800)
                                evidence["delivery_step_probe"] = page.evaluate("""() => {
                                  const visible=e=>!!(e.offsetWidth||e.offsetHeight);
                                  const controls=[...document.querySelectorAll('input[type=radio],input[type=checkbox],[role=radio],[role=checkbox]')].filter(visible).map(e=>{const p=e.closest('label,[role=radio],[role=checkbox]')||e.parentElement;return {tag:e.tagName,type:e.getAttribute('type'),role:e.getAttribute('role'),text:((e.getAttribute('aria-label')||'')+' '+(p?.innerText||'')).replace(/\\s+/g,' ').trim().slice(0,180),checked:!!e.checked||e.getAttribute('aria-checked')==='true',disabled:!!e.disabled}});
                                  const widgets=[...document.querySelectorAll('input,select,button,[role=combobox],[role=button],[role=radio],[role=checkbox],label')].filter(visible).map(e=>({tag:e.tagName,role:e.getAttribute('role'),type:e.getAttribute('type'),label:e.getAttribute('aria-label')||e.labels?.[0]?.innerText||'',value:e.value||'',text:(e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,140),html:e.outerHTML.slice(0,400)})).filter(e=>/delivery|shipping|local pickup/i.test([e.label,e.value,e.text].join(' '))).slice(0,30);
                                  const body=(document.body?.innerText||'').replace(/\\s+/g,' ').trim();
                                  const excerpts=[];for(const term of ['shipping','ship to','local pickup','meetup']){const i=body.toLowerCase().indexOf(term);if(i>=0)excerpts.push(body.slice(Math.max(0,i-80),Math.min(body.length,i+220)));}
                                  return {url:location.href,headings:[...document.querySelectorAll('h1,h2,[role=heading]')].map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,20),controls,widgets,excerpts,buttons:[...document.querySelectorAll('button,[role=button]')].filter(visible).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(-30)};
                                }""")
                                delivery_picker = page.get_by_role("combobox", name="Delivery method")
                                if delivery_picker.count() == 1:
                                    delivery_picker.click()
                                    page.wait_for_timeout(300)
                                    evidence["delivery_step_probe"]["delivery_method_options"] = page.evaluate("""() => ({
                                      expanded:document.querySelector('[role=combobox][aria-labelledby]')?.getAttribute('aria-expanded'),
                                      options:[...document.querySelectorAll('[role=option],[role=listbox] *,[role=menuitem]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>(e.innerText||'').replace(/\\s+/g,' ').trim()).filter(Boolean).slice(0,30),
                                      delivery_items:[...document.querySelectorAll('[role=menuitemcheckbox]')].filter(e=>!!(e.offsetWidth||e.offsetHeight)).map(e=>({text:(e.innerText||'').replace(/\\s+/g,' ').trim(),checked:e.getAttribute('aria-checked'),selected:e.getAttribute('aria-selected'),tabindex:e.getAttribute('tabindex')})),
                                      matching_nodes:[...document.querySelectorAll('body *')].filter(e=>!!(e.offsetWidth||e.offsetHeight)&&e.children.length<4&&/shipping|local pickup/i.test(e.innerText||'')).map(e=>({tag:e.tagName,role:e.getAttribute('role'),text:(e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,220),html:e.outerHTML.slice(0,600)})).slice(-25),
                                      visible_text:(document.body?.innerText||'').replace(/\\s+/g,' ').trim().slice(-1400)
                                    })""")
                            else:
                                evidence["delivery_step_probe"] = {"state": "next_button_not_unique"}
                public_evidence = {"state": "not_available"}
                listing_url = str(card.get("url") or "")
                if listing_url.startswith("https://www.facebook.com/marketplace/item/"):
                    page.goto(listing_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                    page.wait_for_timeout(900)
                    public_evidence = page.evaluate("""() => {
                      const text=(document.body?.innerText||'').replace(/\\s+/g,' ').trim();
                      const terms=['shipping','ships to','local pickup','pickup only','delivery'];
                      const excerpts=[];
                      for(const term of terms){const i=text.toLowerCase().indexOf(term);if(i>=0)excerpts.push({term,text:text.slice(Math.max(0,i-100),Math.min(text.length,i+220))});}
                      return {url:location.href,title:document.title,excerpts};
                    }""")
                inspected.append({"remote_id": remote_id, "url": card.get("url"), "title": title, "card_price": price, "state": "edit_form_opened_read_only", "evidence": evidence, "public_listing_evidence": public_evidence})
                page.goto(self.spec.auth_check_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                page.wait_for_timeout(700)
                self._select_facebook_all_listings(page)
            except Exception as exc:
                inspected.append({"remote_id": remote_id, "title": title, "state": "inspection_failed", "error": str(exc)[:240]})
                try:
                    page.goto(self.spec.auth_check_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                    page.wait_for_timeout(700)
                    self._select_facebook_all_listings(page)
                except Exception:
                    break
        return inspected

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
        screenshot_errors: list[str] = []

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
                try:
                    page.screenshot(path=str(before_path), full_page=True, timeout=15000)
                except Exception as exc:
                    screenshot_errors.append(f"before_submit:{exc}")
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
                try:
                    page.screenshot(path=str(after_path), full_page=True, timeout=15000)
                except Exception as exc:
                    screenshot_errors.append(f"after_submit:{exc}")
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
                    "screenshot_errors": screenshot_errors,
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
        context.set_default_navigation_timeout(self.config.timeout_ms)
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
        if self._try_fill_first(page, list(self.spec.title_selectors), title):
            pass
        else:
            text_inputs = page.locator('input[type="text"]')
            if text_inputs.count() >= 2:
                title_input = text_inputs.nth(0)
                price_input = text_inputs.nth(1)
                title_input.click()
                title_input.fill(title)
            else:
                self._fill_facebook_labeled_field(page, "Title", title, "input")

        if self._try_fill_first(page, list(self.spec.price_selectors), price):
            pass
        else:
            text_inputs = page.locator('input[type="text"]')
            if text_inputs.count() >= 2:
                price_input = text_inputs.nth(1)
                price_input.click()
                price_input.fill(price)
            else:
                self._fill_facebook_labeled_field(page, "Price", price, "input")

        if description:
            if self._try_fill_first(page, list(self.spec.description_selectors), description):
                return
            textareas = page.locator("textarea")
            if textareas.count():
                description_input = textareas.nth(0)
                description_input.click()
                description_input.fill(description)
            else:
                self._fill_facebook_labeled_field(page, "Description", description, "textarea")

    def _wait_for_facebook_marketplace_form(self, page: Any) -> None:
        selectors = [
            *self.spec.title_selectors,
            *self.spec.price_selectors,
            *self.spec.description_selectors,
            'input[type="text"]',
            'input[type="number"]',
            'input[role="spinbutton"]',
            'textarea',
            'div[contenteditable="true"]',
        ]
        last_error: Exception | None = None
        for selector in selectors:
            try:
                page.wait_for_selector(selector, state="visible", timeout=self.config.timeout_ms)
                return
            except Exception as exc:
                last_error = exc
        raise BrowserRunnerError(
            "Could not locate a Facebook Marketplace listing form after waiting for the page to render"
        ) from last_error

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
        if self.spec.marketplace == "facebook":
            self._apply_facebook_category(page, listing_payload.get("facebook_category") or listing_payload.get("category_hint"))
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

    def _apply_facebook_category(self, page: Any, category: Any) -> None:
        """Choose the explicit, resolver-approved Facebook category.

        Facebook changes its form markup often.  This intentionally tries
        labelled controls first and fails closed when an explicit category was
        supplied but cannot be applied; publishing into Facebook's arbitrary
        default is worse than stopping the job.
        """
        value = str(category or "").strip()
        if not value:
            raise BrowserRunnerError("Facebook category is required; refusing to publish with Marketplace's default category")
        triggers = [
            '[aria-label*="Category" i]',
            '[role="button"]:has-text("Category")',
            'label:has-text("Category")',
        ]
        opened = self._click_first(page, triggers)
        if not opened:
            raise BrowserRunnerError("Could not locate Facebook category control; refusing to publish with an unverified category")
        page.wait_for_timeout(400)
        search_selectors = [
            'input[placeholder*="Search" i]',
            'input[aria-label*="Search" i]',
            'input[role="combobox"]',
        ]
        query = value.split("/")[-1].strip() or value
        if not self._try_fill_first(page, search_selectors, query):
            raise BrowserRunnerError("Could not search Facebook category choices; refusing to publish with an unverified category")
        page.wait_for_timeout(700)
        choices = [
            f'[role="option"]:has-text("{query}")',
            f'[role="button"]:has-text("{query}")',
            f'text={query}',
        ]
        if not self._click_visible_first(page, choices):
            raise BrowserRunnerError(f"Facebook did not offer the approved category '{value}'")
        page.wait_for_timeout(350)
        if "/marketplace/create/" not in str(page.url or "").lower():
            raise BrowserRunnerError(
                "Facebook category selection navigated away from the listing form; refusing to continue or publish."
            )

    def _click_facebook_publish_flow(self, page: Any) -> dict[str, Any]:
        """Advance Facebook's create wizard and click Publish only when actionable.

        Facebook often leaves hidden/covered text clones in the DOM.  A failed
        Publish click is treated as ambiguous (never auto-replayed); absence of
        a Publish control after wizard advancement is a definite pre-submit
        failure.
        """
        for _ in range(5):
            next_step = self._click_facebook_exact_action(page, "next")
            if next_step.get("clicked"):
                page.wait_for_timeout(900)
                continue
            if next_step.get("attempted"):
                return {"submitted": False, "publish_click_attempted": False, "error": next_step.get("error")}
            publish = self._click_facebook_exact_action(page, "publish")
            if publish.get("clicked"):
                return {"submitted": True, "publish_click_attempted": True}
            if publish.get("attempted"):
                return {
                    "submitted": False,
                    "publish_click_attempted": True,
                    "error": publish.get("error") or "A visible Publish control could not be clicked safely.",
                }
            return {"submitted": False, "publish_click_attempted": False}
        return {"submitted": False, "publish_click_attempted": False}

    def _click_facebook_exact_action(self, page: Any, action: str) -> dict[str, Any]:
        selectors = [
            f'button:has-text("{action.title()}")',
            f'[role="button"]:has-text("{action.title()}")',
        ]
        attempted = False
        last_error: str | None = None
        for selector in selectors:
            try:
                matches = page.locator(selector)
                count = matches.count()
            except Exception:
                continue
            for index in range(count):
                candidate = matches.nth(index)
                try:
                    if not candidate.is_visible() or not candidate.is_enabled():
                        continue
                    label = " ".join(str(candidate.inner_text(timeout=1000) or "").split()).casefold()
                    aria = str(candidate.get_attribute("aria-label") or "").strip().casefold()
                    if label != action or (aria and aria != action):
                        continue
                    attempted = True
                    try:
                        candidate.scroll_into_view_if_needed(timeout=1500)
                        candidate.click(timeout=5000)
                        return {"clicked": True, "attempted": action == "publish"}
                    except Exception as exc:
                        last_error = str(exc)
                        continue
                except Exception:
                    continue
        return {"clicked": False, "attempted": action == "publish" and attempted, "error": last_error}

    def _click_visible_first(self, page: Any, selectors: list[str]) -> bool:
        """Click a visible, enabled match, skipping Facebook's hidden text clones.

        Facebook renders several inaccessible/aria-hidden copies of category
        labels in its preview tree. Playwright's `.first` can bind one of those
        copies and time out because the visible option never receives the
        click. Walk matches and use only a currently actionable option.
        """
        for selector in selectors:
            try:
                matches = page.locator(selector)
                count = matches.count()
            except Exception:
                continue
            for index in range(count):
                try:
                    candidate = matches.nth(index)
                    if not candidate.is_visible() or not candidate.is_enabled():
                        continue
                    candidate.click()
                    return True
                except Exception:
                    continue
        return False

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

    def _collect_facebook_selling_cards(self, page: Any, *, expected_title: str) -> list[dict[str, Any]]:
        expected = self._normalize_listing_title(expected_title)
        try:
                    cards = page.eval_on_selector_all(
                '[href*="/marketplace/item/"], [href*="/commerce/listing/"], [href*="listing_id="]',
                """
                (nodes) => nodes.map((node) => {
                  const anchor = node.closest('a') || node;
                  const href = anchor.href || anchor.getAttribute('href') || '';
                  let card = anchor;
                  for (let i = 0; i < 5 && card; i += 1) {
                    const text = (card.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (text) {
                      return { href, text };
                    }
                    card = card.parentElement;
                  }
                  return { href, text: '' };
                }).filter((item) => item.href)
                """,
            )
        except Exception:
            cards = []
        if not cards:
            try:
                cards = page.eval_on_selector_all(
                    '[role="button"][aria-label]',
                    """
                    nodes => nodes.map(node => {
                      const label=(node.getAttribute('aria-label')||'').trim();
                      let card=node;
                      for(let i=0;i<4&&card;i++,card=card.parentElement){
                        const text=(card.innerText||'').replace(/\\s+/g,' ').trim();
                        if(label && text.includes(label) && /Listed on|Active|Pending|Sold/i.test(text) && /\\$\\s?\\d/.test(text)){
                          const anchor=card.querySelector('a[href*="/marketplace/item/"],a[href*="/commerce/listing/"],a[href*="listing_id="]');
                          const scripts=[...document.scripts].map(s=>s.textContent||'');
                          let remoteId=''; let categoryId=''; let status=''; let productTitle=label;
                          for(const source of scripts){
                            const i=source.indexOf(label);
                            if(i<0) continue;
                            const start=Math.max(source.lastIndexOf('"__typename":"GroupCommerceProductItem"',i),source.lastIndexOf('"__typename":"MarketplaceListingRenderable"',i),i-3500);
                            const chunk=source.slice(start,i+5000);
                            const before=source.slice(Math.max(start,i-600),i);
                            const idMatch=[...before.matchAll(/"id"\\s*:\\s*"(\\d{8,})"/g)].pop();
                            remoteId=idMatch?.[1]||'';
                            const titleMatch=chunk.match(/"marketplace_listing_title"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)"/);
                            if(titleMatch){ try { productTitle=JSON.parse('"'+titleMatch[1]+'"'); } catch {} }
                            categoryId=(chunk.match(/"marketplace_listing_category_id"\\s*:\\s*"?(\\d+)/)||[])[1]||'';
                            const pending=(chunk.match(/"is_pending"\\s*:\\s*(true|false)/)||[])[1];
                            const sold=(chunk.match(/"is_sold"\\s*:\\s*(true|false)/)||[])[1];
                            const draft=(chunk.match(/"is_draft"\\s*:\\s*(true|false)/)||[])[1];
                            status=sold==='true'?'Sold':pending==='true'?'Pending':draft==='true'?'Draft':'Active';
                            break;
                          }
                          return {href:anchor?.href||'',text,title:productTitle,remote_id:remoteId,category_id:categoryId,status};
                        }
                      }
                      return null;
                    }).filter(Boolean)
                    """,
                )
            except Exception:
                cards = []

        today = datetime.now().astimezone().strftime("%-m/%-d")
        results: list[dict[str, Any]] = []
        for card in cards or []:
            href = self._normalize_listing_url(str(card.get("href") or ""))
            text = str(card.get("text") or "").replace("\n", " ").strip()
            if not text or not re.search(r"\$\s?\d", text) or not re.search(r"listed on|active|pending|sold|draft", text, flags=re.IGNORECASE):
                continue
            normalized_text = self._normalize_listing_title(text)
            raw_title = str(card.get("title") or "").strip()
            title_matches = bool(expected) and expected in self._normalize_listing_title(raw_title or text)
            date_match = re.search(r"listed on\s+([0-9]{1,2}/[0-9]{1,2})", text, flags=re.IGNORECASE)
            looks_new = bool(date_match and date_match.group(1).strip() == today)
            remote_id = self._extract_listing_id(href) or str(card.get("remote_id") or "").strip() or None
            price_match = re.search(r"(?:\$\s?\d[\d,]*(?:\.\d{2})?)", text)
            status = str(card.get("status") or next((value for value in ("Active", "Available", "Pending", "Sold", "Draft", "Out of stock") if value.lower() in text.lower()), None) or "") or None
            category_match = re.search(r"(?:Category|Listed in)\s*[:·-]?\s*([^$|]{2,100})", text, flags=re.IGNORECASE)
            shipping_match = re.search(r"[^.]{0,50}\b(shipping|ship(?:ping)? available|delivery)\b[^.]{0,50}", text, flags=re.IGNORECASE)
            pickup_match = re.search(r"[^.]{0,50}\b(local pickup|pickup only|pick-up only)\b[^.]{0,50}", text, flags=re.IGNORECASE)
            title = raw_title or text
            if price_match:
                title = text.split(price_match.group(0), 1)[0].strip(" ·-|\n")
            if date_match:
                title = re.sub(r"listed on\s+[0-9]{1,2}/[0-9]{1,2}", "", title, flags=re.IGNORECASE).strip(" ·-|\n")
            results.append(
                {
                    "url": href or (f"https://www.facebook.com/marketplace/item/{remote_id}" if remote_id else None),
                    "remote_id": remote_id,
                    "title": title[:240],
                    "card_text": text[:600],
                    "status": status,
                    "price": price_match.group(0).replace(" ", "") if price_match else None,
                    "category_text": category_match.group(1).strip()[:160] if category_match else (str(card.get("category_id") or "") or None),
                    "fulfillment_text": " ".join(value for value in (shipping_match.group(0).strip() if shipping_match else "", pickup_match.group(0).strip() if pickup_match else "") if value) or None,
                    "shipping_text": shipping_match.group(0).strip()[:160] if shipping_match else None,
                    "local_pickup_text": pickup_match.group(0).strip()[:160] if pickup_match else None,
                    "title_matches": title_matches,
                    "looks_new": looks_new,
                    "listed_on": date_match.group(1).strip() if date_match else None,
                }
            )
        return results

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

    def _normalize_listing_title(self, value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _extract_listing_id(self, listing_url: str | None) -> str | None:
        if not listing_url:
            return None
        match = re.search(r"/marketplace/item/(\d+)", listing_url, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"/commerce/listing/(\d+)", listing_url, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        parsed = urlparse(listing_url)
        query_listing_id = parse_qs(parsed.query or "").get("listing_id") or []
        if query_listing_id:
            return str(query_listing_id[0]).strip() or None
        return None

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
                title_hint = str(
                    raw_payload.get("product_name")
                    or raw_payload.get("title")
                    or payload.get("product_name")
                    or payload.get("title")
                    or ""
                ).strip()
                for asin in normalized_asins:
                    try:
                        # A Vine row with an ASIN must be captured from that
                        # exact product page.  A title search can select a
                        # similarly named but different variation, so it is
                        # only a fallback for imports that have no ASIN.
                        capture = self._capture_amazon_product_by_asin(context, asin=asin)
                        product_url = capture["source_url"]
                        image_urls = capture["image_urls"]
                        title = capture.get("title")
                    except Exception:
                        product_url = f"https://www.amazon.com/dp/{asin}"
                        image_urls = []
                        title = None
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

    def _capture_amazon_product_by_asin(self, context: Any, *, asin: str) -> dict[str, Any]:
        product_url = f"https://www.amazon.com/dp/{asin}"
        item_page = context.new_page()
        try:
            item_page.goto(product_url, wait_until="domcontentloaded")
            item_page.wait_for_timeout(1500)
            image_urls = self._extract_amazon_product_images(item_page)
            title = self._extract_amazon_title(item_page)
            return {
                "source_url": product_url,
                "image_urls": image_urls,
                "title": title,
            }
        finally:
            item_page.close()

    def _capture_amazon_product_by_title(self, context: Any, *, title_hint: str) -> dict[str, Any] | None:
        for query in self._amazon_title_search_variants(title_hint):
            search_url = f"https://www.amazon.com/s?k={quote_plus(query)}"
            search_page = context.new_page()
            try:
                search_page.goto(search_url, wait_until="domcontentloaded")
                search_page.wait_for_timeout(1500)
                candidates = self._collect_amazon_search_candidates(search_page.content())
                if not candidates:
                    continue
                scored = sorted(
                    ((self._score_text_match(title_hint, candidate["title"]), candidate) for candidate in candidates),
                    key=lambda item: item[0],
                    reverse=True,
                )
                best_score, best_candidate = scored[0]
                if best_score < 0.52:
                    continue
                asin = best_candidate["asin"]
                capture = self._capture_amazon_product_by_asin(context, asin=asin)
                if not capture["image_urls"]:
                    continue
                if not capture.get("title"):
                    capture["title"] = best_candidate["title"]
                capture["source_url"] = f"https://www.amazon.com/dp/{asin}"
                return capture
            finally:
                search_page.close()
        return None

    def _amazon_title_search_variants(self, title_hint: str) -> list[str]:
        normalized = self._normalize_text(title_hint)
        tokens = [token for token in normalized.split() if len(token) > 2]
        variants: list[str] = []
        for candidate in [
            title_hint,
            " ".join(tokens[:10]),
            " ".join(tokens[:8]),
            " ".join(tokens[:6]),
            " ".join(tokens[:4]),
        ]:
            cleaned = str(candidate or "").strip()
            if cleaned and cleaned not in variants:
                variants.append(cleaned)
        return variants[:5]

    def _collect_amazon_search_candidates(self, html: str) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in re.finditer(r'data-asin="([A-Z0-9]{10})"', html, flags=re.IGNORECASE):
            asin = match.group(1).upper()
            if asin in seen:
                continue
            seen.add(asin)
            window = html[match.start() : min(len(html), match.start() + 6000)]
            title = None
            for pattern in (
                r'<span[^>]*class="a-size-medium a-color-base a-text-normal"[^>]*>(.*?)</span>',
                r'<span[^>]*class="a-size-base-plus a-color-base a-text-normal"[^>]*>(.*?)</span>',
                r'<h2[^>]*>(.*?)</h2>',
            ):
                title_match = re.search(pattern, window, flags=re.IGNORECASE | re.DOTALL)
                if title_match:
                    title = self._clean_title_text(title_match.group(1))
                    if title:
                        break
            if title:
                candidates.append({"asin": asin, "title": title})
            if len(candidates) >= 24:
                break
        return candidates

    def _clean_title_text(self, value: str | None) -> str:
        text = re.sub(r"<[^>]+>", " ", str(value or ""))
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _score_text_match(self, query: str | None, candidate: str | None) -> float:
        normalized_query = self._normalize_text(query)
        normalized_candidate = self._normalize_text(candidate)
        if not normalized_query or not normalized_candidate:
            return 0.0
        query_tokens = set(normalized_query.split())
        candidate_tokens = set(normalized_candidate.split())
        if not query_tokens or not candidate_tokens:
            return 0.0
        overlap = len(query_tokens & candidate_tokens)
        recall = overlap / len(query_tokens)
        precision = overlap / len(candidate_tokens)
        similarity = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
        return round((0.5 * recall) + (0.2 * precision) + (0.3 * similarity), 4)

    def _normalize_text(self, value: str | None) -> str:
        text = re.sub(r"<[^>]+>", " ", str(value or "").lower())
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

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
