import json
import os
import re
import signal
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
    # True means the browser reached the end of the provider's visible list.
    # It is deliberately separate from enumeration_complete, which also
    # requires every visible item to have a hydrated URL in this response.
    provider_scan_complete: bool | None = None
    interrupted: bool = False
    interruption_reason: str | None = None
    scroll_rounds: int = 0
    provider_item_count: int | None = None
    discovered_photo_ids: list[str] | None = None
    unhydrated_photo_ids: list[str] | None = None
    unhydrated_media_details: list[dict[str, Any]] | None = None


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

    def enumerate_photo_entries(
        self,
        album_url: str,
        *,
        overall_timeout_seconds: int = 180,
        max_scroll_rounds: int | None = None,
    ) -> GooglePhotoEnumeration:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            html = client.get(album_url).text
        structured_entries = self.extract_photo_entries_from_html(html, include_raw_fallback=False)
        visible_count = self.extract_album_visible_count_from_html(html)

        browser_result = self.extract_photo_entries_via_playwright_result(
            album_url,
            overall_timeout_seconds=overall_timeout_seconds,
            max_scroll_rounds=max_scroll_rounds,
        )
        merged = self._merge_entries(structured_entries, browser_result.entries)
        observed_count = max(visible_count or 0, browser_result.provider_item_count or 0)
        merged_ids = {str(entry.get("source_photo_id") or "") for entry in merged}
        discovered_ids = list(dict.fromkeys(browser_result.discovered_photo_ids or []))
        unhydrated_ids = [photo_id for photo_id in discovered_ids if photo_id not in merged_ids]
        raw_unhydrated_details = browser_result.unhydrated_media_details or []
        detail_by_id = {str(row.get("source_photo_id") or ""): row for row in raw_unhydrated_details}
        unhydrated_details = [detail_by_id.get(photo_id, {"source_photo_id": photo_id}) for photo_id in unhydrated_ids]
        complete = bool(
            browser_result.enumeration_complete
            and (not observed_count or len(merged) >= observed_count)
            and not unhydrated_ids
        )
        # The count embedded in a shared-album page is sometimes a stale or
        # first-page count (observed as 300 while the live virtualized album
        # exposes substantially more photo links). Never truncate provider
        # observations to that lower bound.
        resolved_count = max(observed_count, len(merged)) or None
        return GooglePhotoEnumeration(
            merged,
            enumeration_complete=complete,
            provider_scan_complete=(
                browser_result.provider_scan_complete
                if browser_result.provider_scan_complete is not None
                else browser_result.enumeration_complete
            ),
            interrupted=browser_result.interrupted,
            interruption_reason=browser_result.interruption_reason,
            scroll_rounds=browser_result.scroll_rounds,
            provider_item_count=resolved_count,
            discovered_photo_ids=discovered_ids,
            unhydrated_photo_ids=unhydrated_ids,
            unhydrated_media_details=unhydrated_details,
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
        max_scroll_rounds: int | None = None,
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
round_limit = max(1, min(250, int(sys.argv[3]))) if len(sys.argv) > 3 and sys.argv[3] else 250
started_at = time.monotonic()

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=['--disable-background-networking'])
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        # Google Photos does not populate the tile's source URL until its lazy
        # thumbnail is allowed to load. These are thumbnail-sized image
        # requests (not the original image download performed by intake), so
        # permit them while continuing to block media/font resources. The
        # browser is a short-lived subprocess and is killed as a process group
        # at the bounded scan deadline.
        page.route("**/*", lambda route: route.abort() if route.request.resource_type in {"media", "font"} else route.continue_())
        page.goto(album_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        viewport = page.locator("c-wiz.B6Rt6d.zcLWac.eejsDc").first
        if viewport.count() == 0:
            print("[]")
            raise SystemExit(0)
        client_height = int(viewport.evaluate("(el) => el.clientHeight") or 0)
        # The album's virtualized scroller can grow substantially while it is
        # being traversed. Small steps plus a fixed 250-round ceiling stopped
        # around the first few hundred items before reaching the real bottom.
        step = max(int(client_height * 1.25), 1000) if client_height else 1125
        collected, collected_index, stable_rounds, rounds, position = [], {}, 0, 0, 0
        interrupted, interruption_reason = False, None
        while rounds < round_limit and stable_rounds < 3:
            if time.monotonic() - started_at >= deadline_seconds:
                interrupted, interruption_reason = True, 'deadline'
                break
            viewport.evaluate(
                "(el, pos) => { el.scrollTop = pos; el.dispatchEvent(new Event('scroll', {bubbles: true})); }",
                position,
            )
            page.wait_for_timeout(500)
            tiles = page.evaluate(
                \"\"\"() => Array.from(document.querySelectorAll('a[href*="/photo/"]')).map((link) => {
                    const tile = link.closest('.rtIMgb.fCPuz') || link
                    const preview = link.querySelector('.RY3tic') || tile.querySelector('.RY3tic')
                    const image = link.querySelector('img') || tile.querySelector('img')
                    const background = preview?.style?.backgroundImage || ''
                    const backgroundUrl = background.match(/url\\([\"']?(.*?)[\"']?\\)/)?.[1] || ''
                    const srcsetUrl = (image?.getAttribute('srcset') || image?.getAttribute('data-srcset') || '').split(',')[0]?.trim().split(/\\s+/)[0] || ''
                    const imageUrls = [image?.currentSrc, image?.getAttribute('src'), image?.getAttribute('data-src'), srcsetUrl, preview?.getAttribute('data-latest-bg'), preview?.getAttribute('data-src'), backgroundUrl].filter(Boolean)
                    return {
                        href: link.getAttribute('href') || '',
                        aria: link.getAttribute('aria-label') || '',
                        has_video: !!link.querySelector('video'),
                        image_urls: imageUrls,
                        preview_url: imageUrls[0] || '',
                    }
                })\"\"\"
            )
            new_count = 0
            for tile in tiles:
                key = tile.get('href') or tile.get('preview_url') or ''
                if not key:
                    continue
                existing_index = collected_index.get(key)
                if existing_index is None:
                    collected_index[key] = len(collected)
                    collected.append(tile)
                    new_count += 1
                else:
                    # Virtualized album tiles often expose the photo link
                    # before the lazy thumbnail URL. Merge later observations
                    # for the same href instead of permanently retaining the
                    # first empty preview and discarding the hydrated tile.
                    existing = collected[existing_index]
                    for field, value in tile.items():
                        existing_value = existing.get(field)
                        if (existing_value is None or existing_value == '') and value is not None and value != '':
                            existing[field] = value
            scroll_state = viewport.evaluate("(el) => ({top: el.scrollTop, height: el.scrollHeight})") or {}
            actual_position = int(scroll_state.get("top") or 0)
            scroll_height = int(scroll_state.get("height") or 0)
            at_end = actual_position + client_height >= max(scroll_height - 4, 0)
            stable_rounds = stable_rounds + 1 if at_end and new_count == 0 else 0
            position = actual_position + step
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
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    album_url,
                    str(max(5, overall_timeout_seconds - 15)),
                    str(max_scroll_rounds or 250),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=overall_timeout_seconds)
            except subprocess.TimeoutExpired:
                # subprocess.run(timeout=...) kills only the Python child. Its
                # Chromium grandchildren can survive as orphans and accumulate
                # across scheduled polls, eventually OOM-killing the worker.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                return GooglePhotoEnumeration([], enumeration_complete=False, interrupted=True, interruption_reason="overall_timeout")
        except subprocess.TimeoutExpired:
            return GooglePhotoEnumeration([], enumeration_complete=False, interrupted=True, interruption_reason="overall_timeout")
        except Exception as exc:
            return GooglePhotoEnumeration([], enumeration_complete=False, interrupted=True, interruption_reason=exc.__class__.__name__)
        if process.returncode != 0 or not str(stdout or "").strip():
            error_kind = re.search(r"\b([A-Za-z][A-Za-z0-9_]*(?:Error|Exception))\b", str(stderr or ""))
            safe_stderr = re.sub(r"https?://\S+", "<url>", str(stderr or "")).strip().splitlines()
            detail = safe_stderr[-1][:160] if safe_stderr else ""
            reason = f"playwright_failed:{error_kind.group(1)}" if error_kind else "playwright_failed"
            if detail:
                reason = f"{reason}:{detail}"
            return GooglePhotoEnumeration([], enumeration_complete=False, interrupted=True, interruption_reason=reason)
        try:
            raw_result = json.loads(stdout)
        except json.JSONDecodeError:
            return GooglePhotoEnumeration([], enumeration_complete=False, interrupted=True, interruption_reason="invalid_playwright_output")
        raw_tiles = raw_result.get("tiles", []) if isinstance(raw_result, dict) else raw_result

        collected: dict[str, dict[str, Any]] = {}
        order = 0
        discovered_photo_ids: list[str] = []
        for tile in raw_tiles:
            href = str(tile.get("href") or "") if isinstance(tile, dict) else ""
            id_match = self.PHOTO_PAGE_ID_PATTERN.search(href)
            if id_match:
                discovered_photo_ids.append(str(id_match.group("source_photo_id")))
            entry = self._entry_from_playwright_tile(tile, source_order=order)
            if not entry:
                continue
            order += 1
            collected.setdefault(entry["source_photo_id"], entry)
        hydrated_ids = set(collected)
        unhydrated_media_details: list[dict[str, Any]] = []
        for tile in raw_tiles:
            if not isinstance(tile, dict):
                continue
            match = self.PHOTO_PAGE_ID_PATTERN.search(str(tile.get("href") or ""))
            source_photo_id = str(match.group("source_photo_id")) if match else ""
            if source_photo_id and source_photo_id not in hydrated_ids:
                unhydrated_media_details.append(
                    {
                        "source_photo_id": source_photo_id,
                        "aria": str(tile.get("aria") or "")[:160],
                        "has_video": bool(tile.get("has_video")),
                    }
                )
        result_entries = list(collected.values())
        observed_tiles = int(raw_result.get("visible_provider_count") or 0) if isinstance(raw_result, dict) else 0
        browser_complete = bool(raw_result.get("complete")) if isinstance(raw_result, dict) else False
        # A page can expose all photo hrefs while deferring most thumbnail
        # URLs. Reaching the visual scroll bottom is not a complete media scan
        # if fewer hydrated image references than provider tiles were captured.
        enumeration_complete = browser_complete and (not observed_tiles or len(result_entries) >= observed_tiles)
        return GooglePhotoEnumeration(
            result_entries,
            enumeration_complete=enumeration_complete,
            provider_scan_complete=browser_complete,
            interrupted=bool(raw_result.get("interrupted")) if isinstance(raw_result, dict) else False,
            interruption_reason=raw_result.get("interruption_reason") if isinstance(raw_result, dict) else None,
            scroll_rounds=int(raw_result.get("rounds") or 0) if isinstance(raw_result, dict) else 0,
            provider_item_count=observed_tiles or None,
            discovered_photo_ids=list(dict.fromkeys(discovered_photo_ids)),
            unhydrated_media_details=unhydrated_media_details,
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
        css_url = re.match(r"^url\([\"']?(.*?)[\"']?\)$", cleaned, re.IGNORECASE)
        if css_url:
            cleaned = css_url.group(1).strip()
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
        candidates = tile.get("image_urls") if isinstance(tile.get("image_urls"), list) else []
        candidates = [*candidates, tile.get("preview_url"), tile.get("src"), tile.get("data_src")]
        preview_url = next(
            (
                cleaned
                for candidate in candidates
                if (cleaned := self._clean_google_url(str(candidate or "").strip())).startswith(
                    "https://lh3.googleusercontent.com/"
                )
            ),
            "",
        )
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
