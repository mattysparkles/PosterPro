from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import Image, Listing, ProductMediaCache, VineImportItem


AMAZON_ACCOUNT_PATH = Path("/opt/apps/posterpro/repo/bridge/data/accounts.json")
SEO_DIR = Path(settings.storage_root) / "amazon-vine-seo"
SEO_DIR.mkdir(parents=True, exist_ok=True)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def slugify(text: str, limit: int = 130) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:limit].strip("-") or "amazon-vine-item"


def clean_page_title(page_title: str, fallback: str) -> str:
    title = (page_title or "").strip()
    if title.startswith("Amazon.com: "):
        parts = title.split(": ")
        if len(parts) >= 3:
            title = ": ".join(parts[1:-1]).strip()
        else:
            title = title.removeprefix("Amazon.com: ").strip()
    title = title.replace("&amp;", "&")
    title = re.sub(r"\s+\|\s+Amazon.*$", "", title).strip()
    return title or fallback


def extract_image_urls(page) -> list[str]:
    nodes = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('[data-a-dynamic-image]')).map((el) => ({
          raw: el.getAttribute('data-a-dynamic-image') || null,
          old: el.getAttribute('data-old-hires') || null,
        }))
        """
    )
    urls: list[str] = []
    for node in nodes:
        raw = node.get("raw")
        if raw and raw != "{}":
            try:
                payload = json.loads(raw)
                if payload:
                    best_url = max(payload.items(), key=lambda kv: (kv[1][0] or 0) * (kv[1][1] or 0))[0]
                    urls.append(best_url)
            except Exception:
                pass
        old = node.get("old")
        if old:
            urls.append(old)

    filtered: list[str] = []
    for url in urls:
        if not url:
            continue
        lowered = url.lower()
        if "images/g/01/error" in lowered or "customer-review" in lowered or "aicid=community-reviews" in lowered:
            continue
        if "m.media-amazon.com/images/i/" not in lowered and "images-na.ssl-images-amazon.com/images/i/" not in lowered:
            continue
        if url not in filtered:
            filtered.append(url)
    return filtered


def purpose_for(index: int, total: int) -> str:
    if total == 1 or index == 1:
        return "front-view"
    return f"gallery-{index:02d}"


def load_amazon_session_payload() -> dict:
    data = json.loads(AMAZON_ACCOUNT_PATH.read_text())
    account = data["accounts"]["amazon:amazon-main"]
    return account["session_payload"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair recent Vine image gaps with locally downloaded Amazon media.")
    parser.add_argument("--user-id", type=int, default=2)
    parser.add_argument("--since-order-date", default="2026-06-15")
    parser.add_argument("--max-rows", type=int, default=12)
    args = parser.parse_args()

    session_payload = load_amazon_session_payload()
    since_date = args.since_order_date

    db = SessionLocal()
    updated = []
    blocked = []
    downloaded = 0
    session = httpx.Client(
        timeout=60,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        },
    )

    try:
        rows = db.execute(
            select(Listing, VineImportItem)
            .join(VineImportItem, VineImportItem.listing_id == Listing.id)
            .where(
                Listing.user_id == args.user_id,
                Listing.source_type == "amazon_vine",
                VineImportItem.order_date >= since_date,
                func.coalesce(func.json_array_length(Listing.image_urls), 0) == 0,
            )
            .order_by(Listing.id)
            .limit(args.max_rows)
        ).all()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(storage_state=session_payload, viewport={"width": 1400, "height": 1200})
            context.set_default_timeout(120000)
            try:
                for listing, item in rows:
                    asin = str(item.asin or "").strip()
                    fallback_title = str(item.product_name or listing.title or asin or "Amazon Vine Item").strip()
                    page = context.new_page()
                    try:
                        page.goto(f"https://www.amazon.com/dp/{asin}", wait_until="domcontentloaded", timeout=120000)
                        page.wait_for_timeout(7000)
                        page_title = clean_page_title(page.title() or "", fallback_title)
                        image_urls = extract_image_urls(page)
                        if not image_urls:
                            blocked.append({"listing_id": listing.id, "asin": asin, "title": page_title, "reason": "no_product_image_urls"})
                            continue

                        public_urls: list[str] = []
                        image_rows: list[Image] = []
                        for index, url in enumerate(image_urls, start=1):
                            suffix = Path(urlparse(url).path).suffix or ".jpg"
                            filename = f"{slugify(page_title)}-{asin.lower()}-{purpose_for(index, len(image_urls))}{suffix}"
                            target = SEO_DIR / filename
                            if not target.exists():
                                response = session.get(url, headers={"Referer": page.url})
                                response.raise_for_status()
                                target.write_bytes(response.content)
                                downloaded += 1

                            public_url = "/media/" + str(target.relative_to(Path(settings.storage_root))).replace("\\", "/")
                            public_urls.append(public_url)

                            image = db.execute(
                                select(Image).where(Image.user_id == listing.user_id, Image.source_url == url)
                            ).scalar_one_or_none()
                            if image is None:
                                image = Image(user_id=listing.user_id, source_url=url, local_path=str(target))
                            else:
                                image.local_path = str(target)
                            image.image_metadata = {
                                **(image.image_metadata or {}),
                                "seo_filename": filename,
                                "seo_title": page_title,
                                "purpose": purpose_for(index, len(image_urls)),
                                "source": "amazon_vine_repair",
                                "asin": asin,
                            }
                            db.add(image)
                            db.flush()
                            image_rows.append(image)

                        listing.image_urls = public_urls
                        listing.custom_labels = [label for label in (listing.custom_labels or []) if label != "needs_photos"] or None
                        item.media_status = "fetched"
                        item.image_import_status = "fetched"
                        item.image_import_error = None
                        item.media_asset_ids_json = [image.id for image in image_rows]

                        cache = db.execute(select(ProductMediaCache).where(ProductMediaCache.asin == asin)).scalar_one_or_none()
                        if cache is None:
                            cache = ProductMediaCache(asin=asin, marketplace_region=settings.amazon_marketplace_region.upper())
                        cache.product_url = f"https://www.amazon.com/dp/{asin}"
                        cache.primary_image_url = public_urls[0] if public_urls else None
                        cache.gallery_image_urls_json = public_urls
                        cache.local_asset_ids_json = [image.id for image in image_rows]
                        cache.source_provider = "bridge_browser"
                        cache.fetch_status = "fetched"
                        cache.fetch_error = None
                        cache.fetched_at = datetime.utcnow()
                        db.add(cache)
                        db.add(listing)
                        db.add(item)
                        db.commit()
                        updated.append({"listing_id": listing.id, "asin": asin, "images": len(public_urls), "title": page_title})
                    except Exception as exc:  # noqa: BLE001
                        db.rollback()
                        item.media_status = "blocked"
                        item.image_import_status = "blocked"
                        item.image_import_error = str(exc)
                        db.add(item)
                        db.commit()
                        blocked.append({"listing_id": listing.id, "asin": asin, "title": fallback_title, "reason": str(exc)})
                    finally:
                        page.close()
            finally:
                browser.close()
    finally:
        session.close()
        db.close()

    print(json.dumps({"updated": updated, "blocked": blocked, "downloaded_files": downloaded}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
