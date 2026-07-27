import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx


@dataclass
class GooglePhotoEnumeration:
    entries: list[dict]
    enumeration_complete: bool
    interrupted: bool = False
    interruption_reason: str | None = None
    scroll_rounds: int = 0
    provider_item_count: int | None = None


class GooglePhotosService:
    IMG_PATTERN = re.compile(r'https://lh3\.googleusercontent\.com/[^\s"\']+')
    PHOTO_RECORD_PATTERN = re.compile(
        r'\["(?P<source_photo_id>AF1Q[A-Za-z0-9_-]+)",\["(?P<url>https://lh3\.googleusercontent\.com/[^"]+)"'
        r',(?P<width>\d+),(?P<height>\d+).*?\],(?P<captured_at>\d{13})',
        re.DOTALL,
    )
    META_PATTERN = re.compile(
        r'"(https://lh3\.googleusercontent\.com/[^"]+)"(?:[^{}]{0,400})?"(20\d{2}-\d{2}-\d{2}T[^"]+)"',
        re.IGNORECASE,
    )
    ALBUM_COUNT_PATTERN = re.compile(r',"(?P<share_key>[A-Za-z0-9_-]{20,})",1,(?P<count>\d{1,5}),')
    PHOTO_PAGE_ID_PATTERN = re.compile(r"/photo/(?P<source_photo_id>AF1Q[A-Za-z0-9_-]+)")
    PHOTOS_SHARE_PATH_PATTERN = re.compile(
        r"https://photos\.google\.com/share/(?P<share_id>AF1Q[A-Za-z0-9_-]+)\?key=(?P<share_key>[A-Za-z0-9_-]+)"
    )

    def extract_photo_entries(self, album_url: str) -> list[dict]:
        """Compatibility wrapper. New intake code must consume enumeration state."""
        return self.enumerate_photo_entries(album_url).entries

    def enumerate_photo_entries(self, album_url: str, *, overall_timeout_seconds: int = 180) -> GooglePhotoEnumeration:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            html = client.get(album_url).text
        structured_entries = self.extract_photo_entries_from_html(html, include_raw_fallback=False)
        visible_count = self.extract_album_visible_count_from_html(html)
        if visible_count and len(structured_entries) >= visible_count:
            return GooglePhotoEnumeration(structured_entries[:visible_count], enumeration_complete=True, provider_item_count=visible_count)

        browser_result = self.extract_photo_entries_via_playwright_result(
            album_url,
            overall_timeout_seconds=overall_timeout_seconds,
        )
        merged = self._merge_entries(structured_entries, browser_result.entries)
        complete = bool(browser_result.enumeration_complete and (not visible_count or len(merged) >= visible_count))
        return GooglePhotoEnumeration(
            merged[:visible_count] if visible_count and complete else merged,
            enumeration_complete=complete,
            interrupted=browser_result.interrupted,
            interruption_reason=browser_result.interruption_reason,
            scroll_rounds=browser_result.scroll_rounds,
            provider_item_count=visible_count,
        )

    def extract_photo_entries_from_html(self, html: str, *, include_raw_fallback: bool = True) -> list[dict]:
        html = html or ""

        entries: list[dict] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()

        for index, match in enumerate(self.PHOTO_RECORD_PATTERN.finditer(html)):
            value = self._clean_google_url(match.group("url"))
            source_photo_id = str(match.group("source_photo_id") or "").strip()
            if not value.startswith("https://lh3.googleusercontent.com/"):
                continue
            if not source_photo_id or source_photo_id in seen_ids:
                continue
            seen_ids.add(source_photo_id)
            seen_urls.add(value)
            suggested_basename = f"google-photo-{source_photo_id}"
            captured_at = self._epoch_ms_to_iso(match.group("captured_at"))
            entries.append(
                {
                    "url": value,
                    "source_photo_id": source_photo_id,
                    "suggested_basename": suggested_basename,
                    "original_filename": f"{suggested_basename}.jpg",
                    "source_order": index,
                    "captured_at": captured_at,
                    "uploaded_at": None,
                    "width": int(match.group("width")),
                    "height": int(match.group("height")),
                }
            )

        if include_raw_fallback:
            for index, raw in enumerate(self.IMG_PATTERN.findall(html)):
                value = self._clean_google_url(raw)
                if not value.startswith("https://lh3.googleusercontent.com/pw/"):
                    continue
                if value in seen_urls:
                    continue
                source_photo_id = self._photo_id_from_url(value)
                if not source_photo_id or source_photo_id in seen_ids:
                    continue
                seen_ids.add(source_photo_id)
                seen_urls.add(value)
                suggested_basename = f"google-photo-{source_photo_id}"
                entries.append(
                    {
                        "url": value,
                        "source_photo_id": source_photo_id,
                        "suggested_basename": suggested_basename,
                        "original_filename": f"{suggested_basename}.jpg",
                        "source_order": index,
                        "captured_at": self._captured_at_for_url(html, value),
                        "uploaded_at": None,
                    }
                )
        return entries

    def extract_photo_entries_via_playwright(self, album_url: str) -> list[dict]:
        return self.extract_photo_entries_via_playwright_result(album_url).entries

    def extract_photo_entries_via_playwright_result(
        self,
        album_url: str,
        *,
        overall_timeout_seconds: int = 180,
    ) -> GooglePhotoEnumeration:
        # The child owns the normal deadline so it can serialize discoveries
        # already collected. The parent timeout is an emergency guard only.
        script = """
import json
import sys
import time
from playwright.sync_api import sync_playwright

album_url = sys.argv[1]
deadline_seconds = float(sys.argv[2])
started_at = time.monotonic()

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        page.goto(album_url, wait_until="networkidle", timeout=120000)
        page.wait_for_timeout(2500)
        viewport = page.locator("c-wiz.B6Rt6d.zcLWac.eejsDc").first
        if viewport.count() == 0:
            print("[]")
            raise SystemExit(0)
        client_height = int(viewport.evaluate("(el) => el.clientHeight") or 0)
        step = max(client_height // 2, 600) if client_height else 900
        collected, seen, stable_rounds, rounds, position = [], set(), 0, 0, 0
        interrupted, interruption_reason = False, None
        while rounds < 250 and stable_rounds < 3:
            if time.monotonic() - started_at >= deadline_seconds:
                interrupted, interruption_reason = True, 'deadline'
                break
            viewport.evaluate("(el, pos) => { el.scrollTop = pos; }", position)
            page.wait_for_timeout(900)
            tiles = page.evaluate(
                \"\"\"() => Array.from(document.querySelectorAll('.rtIMgb.fCPuz')).map((tile) => {
                    const link = tile.querySelector('a.p137Zd')
                    const preview = tile.querySelector('.RY3tic')
                    return {
                        href: link?.getAttribute('href') || '',
                        aria: link?.getAttribute('aria-label') || '',
                        preview_url: preview?.getAttribute('data-latest-bg') || '',
                    }
                })\"\"\"
            )
            new_count = 0
            for tile in tiles:
                key = tile.get('href') or tile.get('preview_url') or ''
                if key and key not in seen:
                    seen.add(key); collected.append(tile); new_count += 1
            scroll_height = int(viewport.evaluate("(el) => el.scrollHeight") or 0)
            at_end = position + client_height >= max(scroll_height - 4, 0)
            stable_rounds = stable_rounds + 1 if at_end and new_count == 0 else 0
            position = min(position + step, max(scroll_height, position + step))
            rounds += 1
        print(json.dumps({
            'tiles': collected,
            'complete': stable_rounds >= 3 and not interrupted,
            'interrupted': interrupted,
            'interruption_reason': interruption_reason,
            'rounds': rounds,
            'last_position': position,
            'visible_provider_count': len(collected),
        }))
    finally:
        browser.close()
"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", script, album_url, str(max(5, overall_timeout_seconds - 15))],
                capture_output=True,
                text=True,
                timeout=overall_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return GooglePhotoEnumeration([], enumeration_complete=False, interrupted=True, interruption_reason="overall_timeout")
        except Exception as exc:
            return GooglePhotoEnumeration([], enumeration_complete=False, interrupted=True, interruption_reason=exc.__class__.__name__)
        if result.returncode != 0 or not str(result.stdout or "").strip():
            return GooglePhotoEnumeration([], enumeration_complete=False, interrupted=True, interruption_reason="playwright_failed")
        try:
            raw_result = json.loads(result.stdout)
        except json.JSONDecodeError:
            return GooglePhotoEnumeration([], enumeration_complete=False, interrupted=True, interruption_reason="invalid_playwright_output")
        raw_tiles = raw_result.get("tiles", []) if isinstance(raw_result, dict) else raw_result

        collected: dict[str, dict[str, Any]] = {}
        order = 0
        for tile in raw_tiles:
            entry = self._entry_from_playwright_tile(tile, source_order=order)
            if not entry:
                continue
            order += 1
            collected.setdefault(entry["source_photo_id"], entry)
        return GooglePhotoEnumeration(
            list(collected.values()),
            enumeration_complete=bool(raw_result.get("complete")) if isinstance(raw_result, dict) else False,
            interrupted=bool(raw_result.get("interrupted")) if isinstance(raw_result, dict) else False,
            interruption_reason=raw_result.get("interruption_reason") if isinstance(raw_result, dict) else None,
            scroll_rounds=int(raw_result.get("rounds") or 0) if isinstance(raw_result, dict) else 0,
            provider_item_count=int(raw_result.get("visible_provider_count") or 0) if isinstance(raw_result, dict) else None,
        )

    def extract_image_urls(self, album_url: str) -> list[str]:
        return [entry["url"] for entry in self.extract_photo_entries(album_url)]

    def extract_album_visible_count(self, album_url: str) -> int | None:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            html = client.get(album_url).text
        return self.extract_album_visible_count_from_html(html)

    def extract_album_visible_count_from_html(self, html: str) -> int | None:
        match = self.ALBUM_COUNT_PATTERN.search(html)
        if not match:
            return None
        try:
            return int(match.group("count"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _photo_id_from_url(url: str) -> str:
        path = urlparse(url).path.strip("/")
        if not path:
            return ""
        token = path.split("/")[-1]
        token = token.split("=", 1)[0]
        return token.strip("()[]{}<>,.;:'\"")

    @staticmethod
    def _clean_google_url(value: str) -> str:
        cleaned = unescape(value.replace("\\u003d", "=").replace("\\/", "/")).strip()
        cleaned = cleaned.rstrip(")>]},.;:'\"")
        return cleaned

    def _merge_entries(self, primary: list[dict], secondary: list[dict]) -> list[dict]:
        merged: dict[str, dict[str, Any]] = {}
        for entry in primary:
            merged[str(entry.get("source_photo_id") or entry.get("url") or "")] = dict(entry)
        for entry in secondary:
            key = str(entry.get("source_photo_id") or entry.get("url") or "")
            if not key:
                continue
            if key not in merged:
                merged[key] = dict(entry)
                continue
            current = merged[key]
            for field, value in entry.items():
                if current.get(field) in {None, "", 0} and value not in {None, ""}:
                    current[field] = value
        return sorted(merged.values(), key=lambda item: int(item.get("source_order") or 0))

    def _entry_from_playwright_tile(self, tile: dict[str, Any], *, source_order: int) -> dict[str, Any] | None:
        href = str(tile.get("href") or "").strip()
        preview_url = self._clean_google_url(str(tile.get("preview_url") or "").strip())
        if not preview_url.startswith("https://lh3.googleusercontent.com/"):
            return None
        match = self.PHOTO_PAGE_ID_PATTERN.search(href)
        source_photo_id = str(match.group("source_photo_id") if match else self._photo_id_from_url(preview_url)).strip()
        if not source_photo_id:
            return None
        canonical_url = preview_url.split("=", 1)[0]
        suggested_basename = f"google-photo-{source_photo_id}"
        return {
            "url": canonical_url,
            "source_photo_id": source_photo_id,
            "suggested_basename": suggested_basename,
            "original_filename": f"{suggested_basename}.jpg",
            "source_order": source_order,
            "captured_at": self._captured_at_from_aria(str(tile.get("aria") or "").strip()),
            "uploaded_at": None,
            "width": None,
            "height": None,
            "source_page_url": href,
        }

    @staticmethod
    def _captured_at_from_aria(value: str) -> str | None:
        if not value:
            return None
        cleaned = value.replace("\u202f", " ").replace("\xa0", " ").strip()
        if " - " in cleaned:
            cleaned = cleaned.rsplit(" - ", 1)[-1].strip()
        for fmt in ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y, %I:%M %p"):
            try:
                return datetime.strptime(cleaned, fmt).replace(tzinfo=UTC).isoformat()
            except ValueError:
                continue
        return None

    def _captured_at_for_url(self, html: str, url: str) -> str | None:
        escaped = re.escape(url)
        pattern = re.compile(rf'"{escaped}"(?:.|\n){{0,400}}?"(20\d{{2}}-\d{{2}}-\d{{2}}T[^"]+)"', re.IGNORECASE)
        match = pattern.search(html)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _epoch_ms_to_iso(value: str | None) -> str | None:
        try:
            epoch_ms = int(str(value or "").strip())
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).isoformat()
