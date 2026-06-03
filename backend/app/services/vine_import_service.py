from __future__ import annotations

import csv
import html
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import ListingStatus, MarketplaceName
from app.models.models import Image, Listing, ProductMediaCache, VineImportBatch, VineImportItem, User
from app.services.amazon_media import AmazonProductMediaProvider
from app.services.amazon_product_discovery import AmazonProductDiscoveryService
from app.services.listing_workspace import normalize_marketplace_data
from app.services.marketplace_field_mapper import build_marketplace_payload
from app.services.storage import LocalStorage
from app.services.vine_parser import ParsedVineRow, parse_vine_csv, parse_vine_pdf, parse_vine_xlsx
from app.services.vine_policy import review_vine_product


class VineImportService:
    """Orchestrates Amazon Vine intake into reviewable PosterPro listing drafts.

    Contract:
    - Import rows from `.xlsx`, `.csv`, or `.pdf` into `VineImportBatch` + `VineImportItem`.
    - Discover product images through a pluggable Amazon discovery layer.
    - Create or reuse draft listings with duplicate protection and source traceability.
    """

    def create_batch_from_upload(
        self,
        db: Session,
        *,
        current_user: User,
        filename: str,
        file_bytes: bytes,
        reference_date: date | None = None,
    ) -> VineImportBatch:
        """Parse a Vine report upload and persist normalized row records."""
        enforce_six_month_lock = self._enforce_six_month_lock_for_user(current_user)
        extension = Path(filename).suffix.lower()
        if extension not in {".xlsx", ".pdf", ".csv"}:
            raise ValueError("Only .xlsx, .csv, and .pdf Vine reports are supported")
        if extension == ".xlsx":
            parsed_rows = parse_vine_xlsx(
                file_bytes,
                reference_date=reference_date,
                enforce_six_month_lock=enforce_six_month_lock,
            )
        elif extension == ".csv":
            parsed_rows = parse_vine_csv(
                file_bytes,
                reference_date=reference_date,
                enforce_six_month_lock=enforce_six_month_lock,
            )
        else:
            parsed_rows = parse_vine_pdf(
                file_bytes,
                reference_date=reference_date,
                enforce_six_month_lock=enforce_six_month_lock,
            )
        batch = VineImportBatch(
            user_id=current_user.id,
            filename=filename,
            source_type=extension.lstrip("."),
            report_year=self._detect_report_year(parsed_rows, filename),
            status="parsed",
            parsed_count=len(parsed_rows),
            stats_json={"pdf_requires_review": extension == ".pdf"},
        )
        db.add(batch)
        db.flush()

        for parsed in parsed_rows:
            policy = review_vine_product(parsed.product_name)
            warnings = list(parsed.parse_warnings or [])
            if (
                not enforce_six_month_lock
                and parsed.eligible_after
                and parsed.eligibility_status == "eligible"
            ):
                warnings.append(
                    f"6-month eligibility date: {parsed.eligible_after.isoformat()} (lock enforcement disabled)"
                )
            item = VineImportItem(
                batch_id=batch.id,
                user_id=current_user.id,
                order_number=parsed.order_number,
                asin=parsed.asin,
                product_name=parsed.product_name,
                order_type=parsed.order_type,
                order_date=parsed.order_date,
                shipped_date=parsed.shipped_date,
                cancelled_date=parsed.cancelled_date,
                estimated_tax_value=parsed.estimated_tax_value,
                eligible_after=parsed.eligible_after,
                eligibility_status=parsed.eligibility_status,
                raw_row_json=parsed.raw_row_json,
                parse_warnings_json=warnings,
                media_status="pending",
                restricted_review_required=policy.restricted_review_required,
                restricted_reasons=policy.restricted_reasons,
                detected_category_guess=policy.detected_category_guess,
                marketplace_allowed_status=policy.marketplace_allowed_status,
                source_confidence=parsed.source_confidence,
                reviewed=extension in {".xlsx", ".csv"},
                brand=parsed.brand,
                category=parsed.category,
                source_status=parsed.status,
                review_deadline=parsed.review_deadline,
                item_url=parsed.item_url,
            )
            db.add(item)

        db.flush()
        self._refresh_batch_stats(db, batch)
        db.commit()
        db.refresh(batch)
        return batch

    def _enforce_six_month_lock_for_user(self, user: User) -> bool:
        settings_json = user.settings_json or {}
        raw = settings_json.get("vine_preferences")
        vine_preferences = raw if isinstance(raw, dict) else {}
        return bool(vine_preferences.get("enforce_six_month_lock", True))

    def fetch_media(self, db: Session, *, batch: VineImportBatch, item_ids: list[int]) -> dict:
        """Resolve Amazon matches and attach cached/fetched media asset ids per row."""
        provider = AmazonProductMediaProvider(db, owner_user_id=batch.user_id)
        discovery = AmazonProductDiscoveryService(provider)
        items = db.execute(select(VineImportItem).where(VineImportItem.batch_id == batch.id, VineImportItem.id.in_(item_ids))).scalars().all()
        fetched = 0
        blocked = 0
        manual_review = 0
        for item in items:
            result = discovery.discover_for_vine_item(asin=item.asin, product_name=item.product_name, manual_url=item.manual_amazon_url)
            item.amazon_match_status = result.get("status")
            item.amazon_match_confidence = result.get("confidence")
            item.amazon_match_asin = result.get("asin") or item.asin
            item.amazon_match_title = result.get("title") or item.product_name
            item.amazon_source_page_url = result.get("source_page_url")
            item.media_status = result.get("image_status") or "blocked"
            item.image_import_status = item.media_status
            item.media_asset_ids_json = result.get("local_asset_ids") or []
            if item.media_status in {"cached", "fetched"}:
                fetched += 1
            elif item.amazon_match_status == "manual_review_needed":
                manual_review += 1
            else:
                blocked += 1
            db.add(item)
        db.commit()
        return {"fetched": fetched, "blocked": blocked, "manual_review_needed": manual_review}

    def create_inventory_records(
        self,
        db: Session,
        *,
        batch: VineImportBatch,
        item_ids: list[int],
        include_locked: bool,
        include_cancelled: bool = False,
    ) -> dict:
        """Create draft listings from Vine rows, reusing existing Vine drafts when possible."""
        items = db.execute(select(VineImportItem).where(VineImportItem.batch_id == batch.id, VineImportItem.id.in_(item_ids))).scalars().all()
        created = 0
        skipped = 0
        reused = 0
        for item in items:
            if item.inventory_item_id:
                skipped += 1
                continue
            if item.eligibility_status == "cancelled" and not include_cancelled:
                skipped += 1
                continue
            if item.eligibility_status.startswith("locked_until_") and not include_locked:
                skipped += 1
                continue
            duplicate = self._find_existing_vine_listing(db, item)
            if duplicate is not None:
                item.inventory_item_id = duplicate.id
                item.listing_id = duplicate.id
                reused += 1
                db.add(item)
                continue
            cached_urls = self._lookup_cached_media_urls(db, item.asin)
            listing = Listing(
                user_id=item.user_id,
                status=ListingStatus.draft,
                title=item.product_name,
                quantity=1,
                condition="Open Box",
                source_type="amazon_vine",
                source_metadata=self._source_metadata(item, batch.id),
                purchase_cost=item.estimated_tax_value,
                suggested_price=item.estimated_tax_value,
                listing_price=item.estimated_tax_value,
                custom_labels=self._build_labels(item, has_photos=bool(cached_urls)),
                needs_review=True,
                restricted_review_required=item.restricted_review_required,
                restricted_reasons=item.restricted_reasons,
                detected_category_guess=item.detected_category_guess,
                marketplace_allowed_status=item.marketplace_allowed_status,
                image_urls=cached_urls,
            )
            db.add(listing)
            db.flush()
            item.inventory_item_id = listing.id
            db.add(item)
            created += 1
        self._refresh_batch_stats(db, batch)
        db.commit()
        return {"created": created, "skipped": skipped, "reused": reused}

    def create_listing_drafts(
        self,
        db: Session,
        *,
        batch: VineImportBatch,
        item_ids: list[int],
        include_cancelled: bool = False,
        fetch_media_first: bool = False,
        require_media_for_asin: bool = False,
        allow_drafts_without_media: bool = False,
    ) -> dict:
        items = db.execute(select(VineImportItem).where(VineImportItem.batch_id == batch.id, VineImportItem.id.in_(item_ids))).scalars().all()
        created = 0
        updated = 0
        skipped = 0
        created_listing_ids: list[int] = []

        provider = AmazonProductMediaProvider(db, owner_user_id=batch.user_id) if fetch_media_first else None
        discovery = AmazonProductDiscoveryService(provider) if provider is not None else None
        for item in items:
            if item.eligibility_status == "cancelled" and not include_cancelled:
                skipped += 1
                continue
            if item.inventory_item_id is None:
                result = self.create_inventory_records(
                    db,
                    batch=batch,
                    item_ids=[item.id],
                    include_locked=True,
                    include_cancelled=include_cancelled,
                )
                if result["created"] == 0 and result.get("reused", 0) == 0:
                    skipped += 1
                    continue
                db.refresh(item)
            listing = db.get(Listing, item.inventory_item_id)
            if listing is None:
                skipped += 1
                continue

            if provider is not None:
                if not item.asin:
                    item.media_status = "missing_asin"
                    item.parse_warnings_json = [*(item.parse_warnings_json or []), "Cannot fetch images without ASIN"]
                else:
                    result = discovery.discover_for_vine_item(
                        asin=item.asin,
                        product_name=item.product_name,
                        manual_url=item.manual_amazon_url,
                    )
                    item.media_status = result.get("status") or item.media_status or "blocked"
                    item.media_asset_ids_json = result.get("local_asset_ids") or item.media_asset_ids_json or []
                    item.amazon_match_status = result.get("status") or item.amazon_match_status
                    item.amazon_match_confidence = result.get("confidence") or item.amazon_match_confidence
                    item.amazon_match_asin = result.get("asin") or item.amazon_match_asin or item.asin
                    item.amazon_match_title = result.get("title") or item.amazon_match_title or item.product_name
                    item.amazon_source_page_url = result.get("source_page_url") or item.amazon_source_page_url
                db.add(item)

            cached_urls = self._lookup_cached_media_urls(db, item.asin)
            if not cached_urls:
                cached_urls = self._try_web_image_fallback(db, item=item)
            if require_media_for_asin and not allow_drafts_without_media and item.asin:
                if settings.amazon_media_lookup_enabled and settings.amazon_media_page_fallback_enabled and not cached_urls:
                    item.parse_warnings_json = [*(item.parse_warnings_json or []), "Draft creation blocked until photos are fetched for this ASIN"]
                    if item.media_status in {None, "pending"}:
                        item.media_status = "blocked"
                    db.add(item)
                    skipped += 1
                    continue

            listing.title = self._generate_title(item.product_name)
            listing.description = self._generate_description(item)
            listing.status = ListingStatus.draft
            listing.needs_review = True
            listing.condition = listing.condition or "Open Box"
            listing.source_type = "amazon_vine"
            listing.source_metadata = self._source_metadata(item, batch.id)
            listing.category_suggestion = item.category or item.detected_category_guess or listing.category_suggestion
            listing.item_specifics = self._build_item_specifics(item, listing.item_specifics)
            listing.tags = self._build_tags(item, listing.tags)
            marketplace_data = normalize_marketplace_data(dict(listing.marketplace_data or {}))
            existing_targets = marketplace_data.get("targets")
            if isinstance(existing_targets, list):
                targets = [str(value).strip().lower() for value in existing_targets if str(value).strip()]
            else:
                targets = []
            for target in (MarketplaceName.ebay.value, MarketplaceName.facebook.value):
                if target not in targets:
                    targets.append(target)
            marketplace_data["targets"] = targets
            marketplace_data["crosspost_mode"] = str(marketplace_data.get("crosspost_mode") or "approval_required")
            marketplace_data["vine_ready_for_approval"] = True
            channels = marketplace_data.get("channels") if isinstance(marketplace_data.get("channels"), dict) else {}
            ebay_channel = dict(channels.get(MarketplaceName.ebay.value) or {})
            ebay_channel["enabled"] = True
            ebay_channel["status"] = "draft_ready"
            ebay_channel["publish_mode"] = str(ebay_channel.get("publish_mode") or "direct_api")
            facebook_channel = dict(channels.get(MarketplaceName.facebook.value) or {})
            facebook_channel["enabled"] = True
            facebook_channel["status"] = "draft_ready"
            facebook_channel["publish_mode"] = str(facebook_channel.get("publish_mode") or "manual_or_provider")
            channels[MarketplaceName.ebay.value] = ebay_channel
            channels[MarketplaceName.facebook.value] = facebook_channel
            marketplace_data["channels"] = channels
            listing.marketplace_data = marketplace_data
            listing.marketplace_data["draft_previews"] = {
                MarketplaceName.ebay.value: build_marketplace_payload(listing, MarketplaceName.ebay.value),
                MarketplaceName.facebook.value: build_marketplace_payload(listing, MarketplaceName.facebook.value),
            }

            if not (listing.image_urls or []):
                if cached_urls:
                    listing.image_urls = cached_urls
                else:
                    listing.custom_labels = list(dict.fromkeys([*(listing.custom_labels or []), "needs_photos"]))
            else:
                listing.custom_labels = [label for label in (listing.custom_labels or []) if label != "needs_photos"] or None

            db.add(listing)
            if not item.listing_id:
                created += 1
                created_listing_ids.append(listing.id)
            else:
                updated += 1
            item.listing_id = listing.id
            db.add(item)
        total_drafts = db.execute(
            select(func.count())
            .select_from(VineImportItem)
            .where(VineImportItem.batch_id == batch.id, VineImportItem.listing_id.is_not(None))
        ).scalar_one()
        batch.drafts_created_count = int(total_drafts or 0)
        batch.status = "imported" if batch.drafts_created_count else batch.status
        db.add(batch)
        db.commit()
        self._update_batch_stats_json(
            db,
            batch=batch,
            updates={"draft_created": created, "draft_updated": updated, "draft_skipped": skipped},
            commit=True,
        )
        return {"created": created, "updated": updated, "skipped": skipped, "created_listing_ids": created_listing_ids}

    def export_problem_rows_csv(self, items: list[VineImportItem]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "product_name", "asin", "order_number", "eligibility_status", "parse_warnings", "restricted_reasons"])
        for item in items:
            if item.eligibility_status == "eligible" and not item.parse_warnings_json and not item.restricted_review_required:
                continue
            writer.writerow(
                [
                    item.id,
                    item.product_name or "",
                    item.asin or "",
                    item.order_number or "",
                    item.eligibility_status,
                    "; ".join(item.parse_warnings_json or []),
                    "; ".join(item.restricted_reasons or []),
                ]
            )
        return output.getvalue()

    def _refresh_batch_stats(self, db: Session, batch: VineImportBatch) -> None:
        items = db.execute(select(VineImportItem).where(VineImportItem.batch_id == batch.id)).scalars().all()
        batch.parsed_count = len(items)
        batch.eligible_count = sum(1 for item in items if item.eligibility_status == "eligible")
        batch.locked_count = sum(1 for item in items if item.eligibility_status.startswith("locked_until_"))
        batch.cancelled_count = sum(1 for item in items if item.eligibility_status == "cancelled")
        batch.error_count = sum(1 for item in items if item.eligibility_status == "invalid")
        db.add(batch)
        self._update_batch_stats_json(
            db,
            batch=batch,
            updates={
                "rows_total": batch.parsed_count,
                "rows_eligible": batch.eligible_count,
                "rows_locked": batch.locked_count,
                "rows_cancelled": batch.cancelled_count,
                "rows_invalid": batch.error_count,
            },
            commit=False,
        )

    def _build_labels(self, item: VineImportItem, *, has_photos: bool) -> list[str]:
        labels = ["amazon_vine", item.eligibility_status]
        if item.restricted_review_required:
            labels.append("restricted_review")
        if not has_photos:
            labels.append("needs_photos")
        return list(dict.fromkeys(labels))

    def _lookup_cached_media_urls(self, db: Session, asin: str | None) -> list[str]:
        if not asin:
            return []
        cache = db.execute(select(ProductMediaCache).where(ProductMediaCache.asin == asin)).scalar_one_or_none()
        if cache is None:
            return []

        gallery_urls = [str(url) for url in (cache.gallery_image_urls_json or []) if url]
        if gallery_urls:
            return gallery_urls

        if cache.primary_image_url:
            return [cache.primary_image_url]

        return []

    def _try_web_image_fallback(self, db: Session, *, item: VineImportItem, limit: int = 3) -> list[str]:
        queries = [
            " ".join(part for part in [item.asin or "", item.product_name or ""] if part).strip(),
            f"{(item.product_name or '').strip()} product".strip(),
            f"{(item.asin or '').strip()} amazon".strip(),
        ]
        candidates: list[str] = []
        for query in queries:
            if not query:
                continue
            candidates.extend(self._search_bing_image_urls(query=query, limit=12))
            if len(candidates) >= 8:
                break
        deduped: list[str] = []
        for candidate in candidates:
            if candidate not in deduped:
                deduped.append(candidate)

        storage = LocalStorage()
        local_urls: list[str] = []
        for source_url in deduped[:12]:
            try:
                local_path = storage.save_from_url(source_url, prefix="vine-search-auto")
                image = Image(user_id=item.user_id, source_url=source_url, local_path=local_path)
                db.add(image)
                db.flush()
                local_urls.append(self._to_public_media_path(local_path))
                if len(local_urls) >= limit:
                    break
            except Exception:
                continue
        if local_urls:
            item.media_status = "fetched"
            db.add(item)
        return local_urls

    def _search_bing_image_urls(self, *, query: str, limit: int = 10) -> list[str]:
        url = f"https://www.bing.com/images/search?q={urllib.parse.quote_plus(query)}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8", errors="ignore")
        except Exception:
            return []

        urls: list[str] = []
        for match in re.finditer(r'class="iusc"[^>]+\sm="([^"]+)"', body):
            raw = html.unescape(match.group(1))
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            for key in ("murl", "turl"):
                candidate = str(payload.get(key) or "").strip()
                if not candidate.startswith("http"):
                    continue
                if candidate in urls:
                    continue
                urls.append(candidate)
                if len(urls) >= limit:
                    return urls
        return urls

    def _to_public_media_path(self, path: str) -> str:
        marker = "/storage/"
        if marker in path:
            return f"/media/{path.split(marker, 1)[1]}"
        return path

    def _source_metadata(self, item: VineImportItem, batch_id: int) -> dict:
        return {
            "asin": item.asin,
            "amazon_match_asin": item.amazon_match_asin,
            "order_number": item.order_number,
            "estimated_tax_value": item.estimated_tax_value,
            "order_date": item.order_date.isoformat() if item.order_date else None,
            "shipped_date": item.shipped_date.isoformat() if item.shipped_date else None,
            "eligible_after": item.eligible_after.isoformat() if item.eligible_after else None,
            "eligibility_status": item.eligibility_status,
            "brand": item.brand,
            "category": item.category,
            "source_status": item.source_status,
            "review_deadline": item.review_deadline.isoformat() if item.review_deadline else None,
            "item_url": item.item_url,
            "manual_amazon_url": item.manual_amazon_url,
            "amazon_source_page_url": item.amazon_source_page_url,
            "raw_row_json": item.raw_row_json or {},
            "batch_id": batch_id,
        }

    def _generate_title(self, product_name: str | None) -> str:
        base = (product_name or "Amazon Vine Item").strip()
        return " ".join(word for word in base.split() if "amazon" not in word.lower())[:80] or "Amazon Vine Item"

    def _generate_description(self, item: VineImportItem) -> str:
        name = (item.product_name or "Item").strip()
        bullet_lines: list[str] = []
        if item.brand:
            bullet_lines.append(f"- Brand: {item.brand}")
        if item.category:
            bullet_lines.append(f"- Category: {item.category}")
        if item.estimated_tax_value is not None:
            bullet_lines.append(f"- Source estimated value: ${float(item.estimated_tax_value):.2f}")
        if item.asin:
            bullet_lines.append(f"- Reference ASIN: {item.asin}")
        if item.amazon_source_page_url:
            bullet_lines.append(f"- Source URL: {item.amazon_source_page_url}")
        elif item.item_url:
            bullet_lines.append(f"- Source URL: {item.item_url}")

        bullets = "\n".join(bullet_lines)
        header = f"{name}\n\n" if name else ""
        return (
            f"{header}"
            "Condition: Open box or unused customer-owned item from an internal Vine intake workflow.\n"
            "Review checklist before publish:\n"
            "- Confirm exact condition, completeness, and accessories.\n"
            "- Verify functional testing notes and photo evidence.\n"
            "- Adjust pricing/marketplace specifics before approval.\n"
            f"{bullets + chr(10) if bullets else ''}"
            "Draft is prepared for manual approval and marketplace handoff."
        )

    def _build_item_specifics(self, item: VineImportItem, existing: dict | None) -> dict:
        specifics = dict(existing or {})
        if item.brand and "Brand" not in specifics:
            specifics["Brand"] = item.brand
        if item.category and "Type" not in specifics:
            specifics["Type"] = item.category
        if item.asin and "Model" not in specifics:
            specifics["Model"] = item.asin
        return specifics

    def _build_tags(self, item: VineImportItem, existing: list[str] | None) -> list[str]:
        tags = [str(tag).strip() for tag in (existing or []) if str(tag).strip()]
        tags.extend(
            [
                "amazon_vine",
                "review_required",
                "draft_preview_ready",
            ]
        )
        if item.brand:
            tags.append(str(item.brand).strip().lower())
        if item.category:
            tags.append(str(item.category).strip().lower().replace(" ", "_"))
        return list(dict.fromkeys(tags))

    def retry_item_discovery(self, db: Session, *, item: VineImportItem) -> dict:
        provider = AmazonProductMediaProvider(db, owner_user_id=item.user_id)
        discovery = AmazonProductDiscoveryService(provider)
        result = discovery.discover_for_item(
            asin=item.asin,
            product_name=item.product_name,
            manual_url=item.manual_amazon_url,
        )
        item.amazon_match_status = result.get("status")
        item.amazon_match_confidence = result.get("confidence")
        item.amazon_match_asin = result.get("asin") or item.asin
        item.amazon_match_title = result.get("title") or item.product_name
        item.amazon_source_page_url = result.get("source_page_url")
        item.media_status = result.get("image_status")
        item.image_import_status = result.get("image_status")
        item.media_asset_ids_json = result.get("local_asset_ids") or []
        item.image_import_error = None if item.media_status in {"cached", "fetched"} else item.media_status
        db.add(item)
        db.commit()
        db.refresh(item)
        return {"item_id": item.id, "match_status": item.amazon_match_status, "image_status": item.image_import_status}

    def _find_existing_vine_listing(self, db: Session, item: VineImportItem) -> Listing | None:
        if item.asin:
            asin_match = db.execute(
                select(Listing).where(
                    Listing.user_id == item.user_id,
                    Listing.source_type == "amazon_vine",
                    Listing.source_metadata.is_not(None),
                )
            ).scalars().all()
            for listing in asin_match:
                source_metadata = dict(listing.source_metadata or {})
                if str(source_metadata.get("asin") or "").upper() == str(item.asin).upper():
                    return listing
        if item.product_name and item.order_date:
            normalized_title = " ".join((item.product_name or "").lower().split())
            candidates = db.execute(
                select(Listing).where(
                    Listing.user_id == item.user_id,
                    Listing.source_type == "amazon_vine",
                    Listing.title.is_not(None),
                )
            ).scalars().all()
            for listing in candidates:
                source_metadata = dict(listing.source_metadata or {})
                listing_date = source_metadata.get("order_date")
                if listing_date != item.order_date.isoformat():
                    continue
                listing_title = " ".join(str(listing.title or "").lower().split())
                if listing_title == normalized_title:
                    return listing
        return None

    def _update_batch_stats_json(self, db: Session, *, batch: VineImportBatch, updates: dict, commit: bool) -> None:
        current = dict(batch.stats_json or {})
        current.update(updates)
        batch.stats_json = current
        db.add(batch)
        if commit:
            db.commit()

    def _detect_report_year(self, rows: list[ParsedVineRow], filename: str) -> int | None:
        for row in rows:
            if row.order_date:
                return row.order_date.year
        for token in Path(filename).stem.replace("-", " ").split():
            if token.isdigit() and len(token) == 4:
                return int(token)
        return None
