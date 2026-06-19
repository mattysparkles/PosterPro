from __future__ import annotations

import csv
import io
import hashlib
import json
import re
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import ListingStatus, MarketplaceName
from app.models.models import Listing, ProductMediaCache, VineImportBatch, VineImportItem, User
from app.services.automation_bridge import submit_bridge_job, wait_for_bridge_job
from app.services.amazon_media import AmazonProductMediaProvider
from app.services.amazon_product_discovery import AmazonProductDiscoveryService
from app.services.ebay_service import _clip_specific_value, _derive_color, _derive_item_type, _fallback_aspect_value
from app.services.listing_review import derive_condition_data, derive_shipping_profile, normalize_listing_images
from app.services.listing_workspace import normalize_marketplace_data
from app.services.marketplace_field_mapper import build_marketplace_payload
from app.services.vine_parser import ParsedVineRow, parse_vine_csv, parse_vine_pdf, parse_vine_xlsx
from app.services.vine_parser import parse_date_value
from app.services.vine_policy import review_vine_product


def _is_unsafe_vine_image(image: dict) -> bool:
    path = " ".join(
        str(image.get(key) or "").strip()
        for key in ("storage_path", "source_url", "source_page_url")
    ).lower()
    metadata = image.get("metadata") if isinstance(image.get("metadata"), dict) else {}
    provider = str(metadata.get("provider") or image.get("source_platform") or "").strip().lower()
    return any(
        token in path
        for token in (
            "vine-search-auto",
            "vine-search-fallback",
            "vine-search-last",
            "/vine-search/",
            "vine-search/",
            "vine-search-fallback/",
            "vine-search-last/",
        )
    ) or provider in {"vine_search_auto", "bing_image_search"}


def _sanitize_vine_text(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\bamazon\s+vine\b", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\bvine\s+report\b", "report", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\binternal\s+vine\s+intake\s+workflow\b", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\bvine\b", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s{2,}", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip(" \t\r\n-")


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
        existing_fingerprints = self._load_existing_vine_fingerprints(db, current_user.id)
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
            fingerprint = self._vine_row_fingerprint(parsed)
            duplicate_item = existing_fingerprints.get(fingerprint)
            if duplicate_item is not None:
                duplicate_warning = f"Duplicate of prior Vine import row (matched item {duplicate_item.id})"
                if duplicate_warning not in warnings:
                    warnings.append(duplicate_warning)
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
            resolved_asin = str(result.get("asin") or item.asin or "").strip().upper()
            if resolved_asin and resolved_asin != item.asin:
                item.asin = resolved_asin
            item.amazon_match_status = result.get("status")
            item.amazon_match_confidence = result.get("confidence")
            item.amazon_match_asin = resolved_asin or item.asin
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
        previous_items_by_fingerprint = self._load_existing_vine_fingerprints(db, batch.user_id)
        for item in items:
            if item.inventory_item_id:
                skipped += 1
                continue
            if self._is_duplicate_vine_item(item):
                duplicate_listing = self._resolve_duplicate_listing_from_map(db, item, previous_items_by_fingerprint)
                if duplicate_listing is not None:
                    item.inventory_item_id = duplicate_listing.id
                    item.listing_id = duplicate_listing.id
                    reused += 1
                    db.add(item)
                else:
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
            inventory_specifics, inventory_provenance = self._build_item_specifics(item, title=item.product_name)
            listing = Listing(
                user_id=item.user_id,
                status=ListingStatus.draft,
                title=item.product_name,
                quantity=1,
                condition="Needs review",
                condition_data=derive_condition_data(
                    listing={"condition": None, "source_type": "amazon_vine"},
                    source_type="amazon_vine",
                    source_metadata=self._source_metadata(item, batch.id),
                    existing={"condition_source": "import"},
                ),
                source_type="amazon_vine",
                source_metadata=self._source_metadata(item, batch.id),
                purchase_cost=item.estimated_tax_value,
                suggested_price=item.estimated_tax_value,
                listing_price=item.estimated_tax_value,
                shipping_profile=self._build_estimated_shipping_profile(item, title=item.product_name),
                custom_labels=self._build_labels(item, has_photos=bool(cached_urls)),
                needs_review=True,
                restricted_review_required=item.restricted_review_required,
                restricted_reasons=item.restricted_reasons,
                detected_category_guess=item.detected_category_guess,
                marketplace_allowed_status=item.marketplace_allowed_status,
                image_urls=cached_urls,
                listing_images=self._build_trusted_amazon_listing_images(
                    image_urls=cached_urls,
                    source_page_url=item.amazon_source_page_url or item.item_url or item.manual_amazon_url,
                    asin=item.asin,
                    product_name=item.product_name,
                ),
                item_specifics=inventory_specifics,
            )
            inventory_marketplace_data = normalize_marketplace_data(dict(listing.marketplace_data or {}))
            inventory_marketplace_data["ebay_item_specifics_provenance"] = inventory_provenance
            inventory_marketplace_data["ebay_item_specifics_approximate"] = [
                field for field, source in inventory_provenance.items() if source in {"derived", "approximate", "default"}
            ]
            listing.marketplace_data = inventory_marketplace_data
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

        provider = AmazonProductMediaProvider(db, owner_user_id=batch.user_id)
        discovery = AmazonProductDiscoveryService(provider)
        previous_items_by_fingerprint = self._load_existing_vine_fingerprints(db, batch.user_id)
        for item in items:
            if self._is_duplicate_vine_item(item):
                duplicate_listing = self._resolve_duplicate_listing_from_map(db, item, previous_items_by_fingerprint)
                if duplicate_listing is not None:
                    item.inventory_item_id = duplicate_listing.id
                    item.listing_id = duplicate_listing.id
                    db.add(item)
                    updated += 1
                else:
                    skipped += 1
                continue
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

            result = discovery.discover_for_vine_item(
                asin=item.asin,
                product_name=item.product_name,
                manual_url=item.manual_amazon_url,
            )
            resolved_asin = str(result.get("asin") or item.asin or "").strip().upper()
            if resolved_asin and resolved_asin != item.asin:
                item.asin = resolved_asin
            if not resolved_asin:
                item.media_status = "missing_asin"
                item.parse_warnings_json = [*(item.parse_warnings_json or []), "Cannot fetch images without ASIN"]
            else:
                item.media_status = result.get("status") or item.media_status or "blocked"
                item.media_asset_ids_json = result.get("local_asset_ids") or item.media_asset_ids_json or []
                item.amazon_match_status = result.get("status") or item.amazon_match_status
                item.amazon_match_confidence = result.get("confidence") or item.amazon_match_confidence
                item.amazon_match_asin = resolved_asin or item.amazon_match_asin or item.asin
                item.amazon_match_title = result.get("title") or item.amazon_match_title or item.product_name
                item.amazon_source_page_url = result.get("source_page_url") or item.amazon_source_page_url
            db.add(item)

            cached_urls = self._lookup_cached_media_urls(db, item.asin)
            discovered_urls = [str(url).strip() for url in (result.get("images") or []) if str(url).strip()]
            discovered_description = _sanitize_vine_text(result.get("description") or "")
            if require_media_for_asin and not allow_drafts_without_media and item.asin:
                if not (cached_urls or discovered_urls):
                    item.parse_warnings_json = [*(item.parse_warnings_json or []), "Draft creation blocked until photos are fetched for this ASIN"]
                    if item.media_status in {None, "pending"}:
                        item.media_status = "blocked"
                    db.add(item)
                    skipped += 1
                    continue

            listing.title = self._generate_title(item.product_name)
            listing.description = self._generate_description(item, amazon_description=discovered_description)
            listing.status = ListingStatus.draft
            listing.needs_review = True
            listing.condition = "New"
            listing.condition_data = derive_condition_data(
                listing={"condition": listing.condition, "source_type": "amazon_vine"},
                source_type="amazon_vine",
                source_metadata=self._source_metadata(item, batch.id),
                existing=listing.condition_data,
            )
            listing.source_type = "amazon_vine"
            listing.source_metadata = self._source_metadata(item, batch.id)
            shipping_estimate = self._build_estimated_shipping_profile(item, title=listing.title, description=listing.description, existing=listing.shipping_profile or {})
            listing.shipping_profile = derive_shipping_profile(
                listing={"title": listing.title, "description": listing.description},
                existing=shipping_estimate,
            )
            listing.shipping_profile["estimated_fields"] = shipping_estimate.get("estimated_fields") or []
            listing.shipping_profile["provenance"] = shipping_estimate.get("provenance") or {}
            listing.category_suggestion = item.category or item.detected_category_guess or listing.category_suggestion
            specifics, provenance = self._build_item_specifics(item, listing.title, listing.description, listing.item_specifics)
            listing.item_specifics = specifics
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
            marketplace_data["ebay_item_specifics_provenance"] = provenance
            marketplace_data["ebay_item_specifics_approximate"] = [
                field for field, source in provenance.items() if source in {"derived", "approximate", "default"}
            ]
            listing.marketplace_data = marketplace_data
            listing.marketplace_data["draft_previews"] = {
                MarketplaceName.ebay.value: build_marketplace_payload(listing, MarketplaceName.ebay.value),
                MarketplaceName.facebook.value: build_marketplace_payload(listing, MarketplaceName.facebook.value),
            }

            if not (listing.image_urls or []):
                image_urls_to_use = cached_urls or discovered_urls
                if image_urls_to_use:
                    listing.image_urls = image_urls_to_use
                    listing.listing_images = self._build_trusted_amazon_listing_images(
                        image_urls=image_urls_to_use,
                        source_page_url=result.get("source_page_url") or item.amazon_source_page_url or item.item_url or item.manual_amazon_url,
                        asin=item.asin,
                        product_name=item.product_name,
                    )
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

    def auto_build_batch_drafts(
        self,
        db: Session,
        *,
        batch: VineImportBatch,
        item_ids: list[int] | None = None,
        new_only: bool = True,
        include_cancelled: bool = True,
    ) -> dict:
        query = select(VineImportItem).where(VineImportItem.batch_id == batch.id).order_by(VineImportItem.id.asc())
        if item_ids:
            query = query.where(VineImportItem.id.in_(item_ids))
        items = db.execute(query).scalars().all()
        target_items = [
            item
            for item in items
            if (not new_only or not self._is_duplicate_vine_item(item))
        ]
        target_item_ids = [item.id for item in target_items]
        duplicate_count = sum(1 for item in items if self._is_duplicate_vine_item(item))
        if not target_item_ids:
            return {
                "batch_id": batch.id,
                "processed_item_ids": [],
                "listing_ids": [],
                "new_only": new_only,
                "duplicates_skipped": duplicate_count,
                "draft_result": {"created": 0, "updated": 0, "skipped": 0, "created_listing_ids": []},
                "repair_result": {"updated": 0, "removed_unsafe": 0, "already_present": 0, "missing_asin": 0, "bridge_refetched": 0, "bridge_failed": 0},
            }

        draft_result = self.create_listing_drafts(
            db,
            batch=batch,
            item_ids=target_item_ids,
            include_cancelled=include_cancelled,
            fetch_media_first=False,
            require_media_for_asin=False,
            allow_drafts_without_media=True,
        )

        refreshed_items = db.execute(
            select(VineImportItem).where(VineImportItem.id.in_(target_item_ids)).order_by(VineImportItem.id.asc())
        ).scalars().all()
        draft_listing_ids = sorted({item.listing_id for item in refreshed_items if item.listing_id})
        repair_result = self.repair_vine_listing_images(
            db,
            user_id=batch.user_id,
            batch_id=batch.id,
            listing_ids=draft_listing_ids,
            include_archived=False,
            force_refresh=False,
            use_bridge_session=True,
            only_missing_images=True,
            limit=None,
        ) if draft_listing_ids else {
            "updated": 0,
            "removed_unsafe": 0,
            "already_present": 0,
            "missing_asin": 0,
            "bridge_refetched": 0,
            "bridge_failed": 0,
            "total_vine_listings": 0,
            "processed": 0,
            "include_archived": False,
            "force_refresh": False,
            "listing_ids": [],
            "batch_id": batch.id,
        }

        return {
            "batch_id": batch.id,
            "processed_item_ids": target_item_ids,
            "listing_ids": draft_listing_ids,
            "new_only": new_only,
            "duplicates_skipped": duplicate_count,
            "draft_result": draft_result,
            "repair_result": repair_result,
        }

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
        duplicate_count = sum(1 for item in items if self._is_duplicate_vine_item(item))
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
                "rows_duplicate": duplicate_count,
                "rows_new": max(batch.parsed_count - duplicate_count, 0),
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

    def _build_trusted_amazon_listing_images(
        self,
        *,
        image_urls: list[str] | None,
        source_page_url: str | None,
        asin: str | None,
        product_name: str | None,
    ) -> list[dict]:
        cleaned_urls = [str(url).strip() for url in (image_urls or []) if str(url).strip()]
        if not cleaned_urls:
            return []
        return normalize_listing_images(
            listing_images=[
                {
                    "storage_path": url,
                    "source_page_url": source_page_url,
                    "source_platform": "amazon",
                    "operator_state": "approved",
                    "is_reference": False,
                    "metadata": {
                        "source": "amazon_vine",
                        "asin": asin,
                        "product_name": product_name,
                        "trusted_catalog_match": True,
                    },
                }
                for url in cleaned_urls
            ],
            source_page_url=source_page_url,
            source_platform="amazon",
            default_is_reference=False,
            approved=True,
        )

    def _to_public_media_path(self, path: str) -> str:
        marker = "/storage/"
        if marker in path:
            return f"/media/{path.split(marker, 1)[1]}"
        return path

    def _is_archived_vine_listing(self, listing: Listing) -> bool:
        status = str(listing.status or "").strip().lower()
        return status in {"sold", "closed"} or bool(listing.sold_at)

    def repair_vine_listing_images(
        self,
        db: Session,
        *,
        user_id: int,
        batch_id: int | None = None,
        listing_ids: list[int] | None = None,
        include_archived: bool = False,
        force_refresh: bool = True,
        use_bridge_session: bool = True,
        only_missing_images: bool = False,
        limit: int | None = None,
    ) -> dict:
        query = select(Listing).where(Listing.user_id == user_id, Listing.source_type == "amazon_vine")
        if listing_ids:
            query = query.where(Listing.id.in_(listing_ids))
        listings = db.execute(query).scalars().all()
        provider = AmazonProductMediaProvider(db, owner_user_id=user_id)
        discovery = AmazonProductDiscoveryService(provider)
        updated = 0
        removed_unsafe = 0
        already_present = 0
        missing_asin = 0
        no_cache = 0
        bridge_refetched = 0
        bridge_failed = 0
        processed = 0

        for listing in listings:
            if limit is not None and processed >= max(0, limit):
                break
            if not include_archived and self._is_archived_vine_listing(listing):
                continue

            normalized_images = normalize_listing_images(
                listing_images=listing.listing_images,
                image_urls=listing.image_urls,
                source_url=(listing.source_metadata or {}).get("source_image_url") if isinstance(listing.source_metadata, dict) else None,
                source_page_url=(listing.source_metadata or {}).get("amazon_source_page_url") if isinstance(listing.source_metadata, dict) else None,
                source_platform=listing.source_type or "amazon",
                default_is_reference=True,
                approved=False,
            )
            unsafe_images = [image for image in normalized_images if _is_unsafe_vine_image(image)]
            has_approved_actual = any(
                not image.get("is_reference") and image.get("operator_state") == "approved"
                for image in normalized_images
            )
            if only_missing_images and normalized_images and not unsafe_images and has_approved_actual:
                already_present += 1
                continue
            if not force_refresh and normalized_images and not unsafe_images:
                already_present += 1
                continue

            processed += 1
            item = (
                db.execute(
                    select(VineImportItem)
                    .where((VineImportItem.listing_id == listing.id) | (VineImportItem.inventory_item_id == listing.id))
                    .order_by(VineImportItem.updated_at.desc(), VineImportItem.id.desc())
                )
                .scalars()
                .first()
            )
            source_metadata = dict(listing.source_metadata or {})
            asin = str((item.asin if item and item.asin else source_metadata.get("asin") or "")).strip().upper()
            product_name = str(listing.title or source_metadata.get("product_name") or "").strip() or None
            manual_url = (
                str(source_metadata.get("manual_amazon_url") or source_metadata.get("item_url") or "").strip()
                or (str(item.manual_amazon_url).strip() if item and item.manual_amazon_url else None)
            )
            result = {}
            try:
                result = discovery.discover_for_vine_item(
                    asin=asin or None,
                    product_name=product_name,
                    manual_url=manual_url,
                ) if (asin or product_name or manual_url) else {}
            except Exception:
                result = {}
            resolved_asin = str(result.get("asin") or asin or "").strip().upper()
            if resolved_asin and resolved_asin != asin:
                asin = resolved_asin
                if item and not item.asin:
                    item.asin = resolved_asin
                    db.add(item)

            if not asin:
                missing_asin += 1
                if unsafe_images:
                    removed_unsafe += len(unsafe_images)
                listing.listing_images = [image for image in normalized_images if image not in unsafe_images]
                listing.image_urls = [image.get("storage_path") for image in (listing.listing_images or []) if image.get("operator_state") != "rejected"]
                if not any(image.get("operator_state") != "rejected" for image in (listing.listing_images or [])):
                    listing.image_urls = []
                labels = set(listing.custom_labels or [])
                labels.add("needs_photos")
                listing.custom_labels = sorted(labels)
                db.add(listing)
                continue

            cache = db.execute(
                select(ProductMediaCache).where(
                    ProductMediaCache.asin == asin,
                    ProductMediaCache.marketplace_region == settings.amazon_marketplace_region.upper(),
                )
            ).scalar_one_or_none()
            if cache is not None and cache.fetch_status == "fetched" and cache.source_provider not in {"manual"}:
                gallery_urls = [str(url) for url in (cache.gallery_image_urls_json or []) if str(url).strip()]
            else:
                gallery_urls = []
                if result and result.get("image_status") in {"cached", "fetched"}:
                    gallery_urls = [str(url) for url in (result.get("images") or []) if str(url).strip()]
                if not gallery_urls:
                    cache = db.execute(
                        select(ProductMediaCache).where(
                            ProductMediaCache.asin == asin,
                            ProductMediaCache.marketplace_region == settings.amazon_marketplace_region.upper(),
                        )
                    ).scalar_one_or_none()
                    if cache is not None and cache.fetch_status == "fetched" and cache.source_provider not in {"manual"}:
                        gallery_urls = [str(url) for url in (cache.gallery_image_urls_json or []) if str(url).strip()]

            if not gallery_urls and use_bridge_session:
                cache = self._bridge_capture_for_asin(db, asin, product_name)
                if cache is not None:
                    bridge_refetched += 1
                    gallery_urls = [str(url) for url in (cache.gallery_image_urls_json or []) if str(url).strip()]
                else:
                    bridge_failed += 1

            trusted_catalog_images = self._build_trusted_amazon_listing_images(
                image_urls=gallery_urls,
                source_page_url=item.item_url if item else source_metadata.get("item_url"),
                asin=asin,
                product_name=product_name,
            ) if gallery_urls else []
            refreshed_images = [image for image in normalized_images if not _is_unsafe_vine_image(image)]
            discovered_description = _sanitize_vine_text((result or {}).get("description") or (listing.description or ""))
            if has_approved_actual:
                merged_images = refreshed_images
                if trusted_catalog_images:
                    existing_keys = {
                        f"{str(img.get('storage_path') or '')}|{str(img.get('source_url') or '')}"
                        for img in merged_images
                    }
                    for image in trusted_catalog_images:
                        key = f"{str(image.get('storage_path') or '')}|{str(image.get('source_url') or '')}"
                        if key not in existing_keys:
                            merged_images.append(image)
                            existing_keys.add(key)
                listing.listing_images = normalize_listing_images(listing_images=merged_images)
                listing.image_urls = [image["storage_path"] for image in (listing.listing_images or []) if image.get("operator_state") != "rejected"]
            elif trusted_catalog_images:
                listing.listing_images = normalize_listing_images(listing_images=trusted_catalog_images)
                listing.image_urls = [image["storage_path"] for image in (listing.listing_images or []) if image.get("operator_state") != "rejected"]
            else:
                listing.listing_images = refreshed_images
                listing.image_urls = [image["storage_path"] for image in (listing.listing_images or []) if image.get("operator_state") != "rejected"]

            if discovered_description:
                listing.description = self._generate_description(item, amazon_description=discovered_description)
            else:
                listing.description = _sanitize_vine_text(listing.description) or listing.description
            listing.title = self._generate_title(item.product_name or listing.title)
            listing.condition = "New"
            listing.condition_data = derive_condition_data(
                listing={"condition": listing.condition, "source_type": "amazon_vine"},
                source_type="amazon_vine",
                source_metadata=source_metadata,
                existing=listing.condition_data,
            )

            if unsafe_images:
                removed_unsafe += len(unsafe_images)

            if not any(image.get("operator_state") != "rejected" for image in (listing.listing_images or [])):
                labels = set(listing.custom_labels or [])
                labels.add("needs_photos")
                listing.custom_labels = sorted(labels)
            else:
                labels = [label for label in (listing.custom_labels or []) if label != "needs_photos"]
                listing.custom_labels = labels or None

            db.add(listing)
            updated += 1

        db.commit()
        return {
            "updated": updated,
            "removed_unsafe": removed_unsafe,
            "already_present": already_present,
            "missing_asin": missing_asin,
            "no_cache": no_cache,
            "bridge_refetched": bridge_refetched,
            "bridge_failed": bridge_failed,
            "total_vine_listings": len(listings),
            "processed": processed,
            "include_archived": include_archived,
            "force_refresh": force_refresh,
            "listing_ids": listing_ids or [],
            "batch_id": batch_id,
        }

    def _bridge_capture_for_asin(self, db: Session, asin: str, title_hint: str | None) -> ProductMediaCache | None:
        try:
            bridge_submission = submit_bridge_job(
                job_type="import",
                execution_mode="browser_assist",
                payload={
                    "source_marketplace": "amazon",
                    "asin": asin,
                    "asins": [asin],
                    "payload": {
                        "asin": asin,
                        "product_name": title_hint,
                    },
                },
            )
            bridge_job_id = str((((bridge_submission or {}).get("bridge_response") or {}).get("job_id") or "")).strip()
            if not bridge_job_id:
                return None
            bridge_completion = wait_for_bridge_job(job_id=bridge_job_id, timeout_seconds=45, poll_interval_seconds=1.0)
            if str(bridge_completion.get("status") or "").lower() != "completed":
                return None
            captured = ((bridge_completion.get("result") or {}).get("imported_listings") or [])
            first = captured[0] if captured and isinstance(captured[0], dict) else {}
            captured_urls = [str(url).strip() for url in (first.get("image_urls") or []) if str(url).strip()]
            if not captured_urls:
                return None
            provider = AmazonProductMediaProvider(db, owner_user_id=None)
            provider.cache_gallery_from_remote_urls(
                asin=asin,
                image_urls=captured_urls,
                title_hint=title_hint,
                source_provider="bridge_browser",
            )
            return db.execute(
                select(ProductMediaCache).where(
                    ProductMediaCache.asin == asin,
                    ProductMediaCache.marketplace_region == settings.amazon_marketplace_region.upper(),
                )
            ).scalar_one_or_none()
        except Exception:
            return None

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
        base = _sanitize_vine_text(product_name or "Amazon item")
        cleaned = " ".join(word for word in base.split() if word.lower() not in {"amazon", "vine"})
        cleaned = _sanitize_vine_text(cleaned)
        return cleaned[:80] or "Amazon item"

    def _generate_description(self, item: VineImportItem, *, amazon_description: str | None = None) -> str:
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
        header = _sanitize_vine_text(name)
        body_lines: list[str] = []
        if header:
            body_lines.append(header)
            body_lines.append("")
        if amazon_description:
            body_lines.append(_sanitize_vine_text(amazon_description))
            body_lines.append("")
        body_lines.append("Condition: New.")
        body_lines.append("Review checklist before publish:")
        body_lines.append("- Confirm exact condition, completeness, and accessories.")
        body_lines.append("- Verify functional testing notes and photo evidence.")
        body_lines.append("- Adjust pricing and marketplace specifics before approval.")
        if bullets:
            body_lines.append("")
            body_lines.append(bullets)
        body_lines.append("")
        body_lines.append("Draft is prepared for manual approval and marketplace handoff.")
        return _sanitize_vine_text("\n".join(body_lines))

    def _build_item_specifics(
        self,
        item: VineImportItem,
        title: str | None,
        description: str | None = None,
        existing: dict | None = None,
    ) -> tuple[dict, dict[str, str]]:
        specifics = dict(existing or {})
        provenance: dict[str, str] = {}
        title_text = str(title or item.product_name or "").strip()
        description_text = str(description or "").strip()
        title_context = " ".join(part for part in (title_text, description_text) if part)

        for key, value in list(specifics.items()):
            if str(value or "").strip():
                provenance[str(key)] = provenance.get(str(key), "existing")

        def set_specific(name: str, value: str | None, source: str) -> None:
            text = str(value or "").strip()
            if not text:
                return
            if str(specifics.get(name) or "").strip():
                provenance.setdefault(name, "existing")
                return
            specifics[name] = _clip_specific_value(text)
            provenance[name] = source

        set_specific("Brand", item.brand, "existing")
        set_specific("Type", item.category or item.detected_category_guess or _derive_item_type(title_text), "derived")
        set_specific("Model", item.asin or None, "derived")

        if not str(specifics.get("Brand") or "").strip():
            specifics["Brand"] = "Does Not Apply"
            provenance["Brand"] = "approximate"

        if not str(specifics.get("Type") or "").strip():
            specifics["Type"] = _derive_item_type(title_context) or "Does Not Apply"
            provenance["Type"] = "approximate"

        if not str(specifics.get("Model") or "").strip():
            specifics["Model"] = "Does Not Apply"
            provenance["Model"] = "approximate"

        for aspect_name in [
            "MPN",
            "UPC",
            "EAN",
            "ISBN",
            "Color",
            "Size",
            "Material",
            "Compatible Brand",
            "Compatible Model",
            "Style",
            "Capacity",
            "Voltage",
            "Wattage",
            "Amperage",
            "Department",
            "Pattern",
            "Product Line",
            "Country/Region of Manufacture",
            "Features",
        ]:
            if str(specifics.get(aspect_name) or "").strip():
                provenance.setdefault(aspect_name, "existing")
                continue
            fallback = _fallback_aspect_value(
                type("DraftListing", (), {"title": title_text, "description": description_text, "source_type": "amazon_vine", "source_metadata": {"asin": item.asin or "", "brand": item.brand or "", "category": item.category or item.detected_category_guess or ""}, "item_specifics": specifics, "condition": "New"})(),
                aspect_name,
                title_context,
            )
            if fallback:
                specifics[aspect_name] = _clip_specific_value(fallback)
                provenance[aspect_name] = "approximate"

        return specifics, provenance

    def _build_estimated_shipping_profile(
        self,
        item: VineImportItem,
        *,
        title: str | None,
        description: str | None = None,
        existing: dict | None = None,
    ) -> dict:
        shipping = dict(existing or {})
        title_text = " ".join(part for part in (str(title or item.product_name or "").strip(), str(description or "").strip()) if part).lower()
        category_text = str(item.category or item.detected_category_guess or "").lower()
        tokens = f"{title_text} {category_text}".strip()

        def set_if_missing(key: str, value):
            if shipping.get(key) in (None, "", {}, []):
                shipping[key] = value

        if any(word in tokens for word in ("charger", "adapter", "cable", "kvm", "controller", "mouse", "keyboard", "dock", "hub", "sensor")):
            weight = 1.0
            dimensions = {"length": 10, "width": 8, "height": 4}
        elif any(word in tokens for word in ("bag", "backpack", "duffel", "purse", "wallet", "shoes", "heels", "sandal", "boot", "skate")):
            weight = 2.5
            dimensions = {"length": 14, "width": 12, "height": 6}
        elif any(word in tokens for word in ("laptop", "tablet", "monitor", "screen", "printer", "router", "wifi", "wifi extender")):
            weight = 3.5
            dimensions = {"length": 16, "width": 12, "height": 6}
        elif any(word in tokens for word in ("bottle", "spray", "liquid", "soap", "cleaner")):
            weight = 1.5
            dimensions = {"length": 10, "width": 6, "height": 4}
        else:
            weight = 2.0
            dimensions = {"length": 12, "width": 10, "height": 6}

        set_if_missing("package_weight", weight)
        if not isinstance(shipping.get("package_dimensions"), dict):
            shipping["package_dimensions"] = {}
        package_dimensions = dict(shipping.get("package_dimensions") or {})
        for key, value in dimensions.items():
            if package_dimensions.get(key) in (None, "", 0):
                package_dimensions[key] = value
        shipping["package_dimensions"] = package_dimensions
        set_if_missing("shipping_class_suggestion", "usps_ground_advantage")
        shipping["estimated"] = True
        shipping["manual_measurement_needed"] = False
        shipping["shipping_notes"] = str(
            shipping.get("shipping_notes")
            or "Estimated from Vine title/category for draft readiness. Verify the packed item before final publish."
        ).strip()
        shipping["estimated_fields"] = list(dict.fromkeys([*(shipping.get("estimated_fields") or []), "package_weight", "package_dimensions"]))
        shipping["provenance"] = {
            **(shipping.get("provenance") if isinstance(shipping.get("provenance"), dict) else {}),
            "package_weight": "approximate",
            "package_dimensions": "approximate",
            "shipping_class_suggestion": "default",
        }
        return shipping

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

    def _normalize_vine_fingerprint_value(self, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.2f}"
        if isinstance(value, int):
            return str(value)
        if hasattr(value, "isoformat"):
            try:
                return str(value.isoformat())
            except Exception:
                return str(value)
        text = re.sub(r"\s+", " ", str(value)).strip().lower()
        parsed_date = parse_date_value(text, allow_excel_serial=True)
        if parsed_date is not None:
            return parsed_date.isoformat()
        return text

    def _vine_row_fingerprint(self, row: ParsedVineRow | VineImportItem | dict | None) -> str:
        if row is None:
            return ""
        if isinstance(row, dict):
            payload = row
        elif isinstance(row, VineImportItem):
            payload = dict(row.raw_row_json or {})
            payload.setdefault("Order Number", row.order_number)
            payload.setdefault("ASIN", row.asin)
            payload.setdefault("Product Name", row.product_name)
            payload.setdefault("Order Type", row.order_type)
            payload.setdefault("Order Date", row.order_date)
            payload.setdefault("Shipped Date", row.shipped_date)
            payload.setdefault("Cancelled Date", row.cancelled_date)
            payload.setdefault("Estimated Tax Value", row.estimated_tax_value)
            payload.setdefault("Brand", row.brand)
            payload.setdefault("Category", row.category)
            payload.setdefault("Review Deadline", row.review_deadline)
            payload.setdefault("Item URL", row.item_url)
        else:
            payload = {
                "Order Number": row.order_number,
                "ASIN": row.asin,
                "Product Name": row.product_name,
                "Order Type": row.order_type,
                "Order Date": row.order_date,
                "Shipped Date": row.shipped_date,
                "Cancelled Date": row.cancelled_date,
                "Estimated Tax Value": row.estimated_tax_value,
                "Brand": row.brand,
                "Category": row.category,
                "Review Deadline": row.review_deadline,
                "Item URL": row.item_url,
            }
        normalized = {
            key: self._normalize_vine_fingerprint_value(payload.get(key))
            for key in (
                "Order Number",
                "ASIN",
                "Product Name",
                "Order Type",
                "Order Date",
                "Shipped Date",
                "Cancelled Date",
                "Estimated Tax Value",
                "Brand",
                "Category",
                "Review Deadline",
                "Item URL",
            )
        }
        fingerprint_source = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()

    def _load_existing_vine_fingerprints(self, db: Session, user_id: int) -> dict[str, VineImportItem]:
        items = db.execute(
            select(VineImportItem).where(VineImportItem.user_id == user_id).order_by(VineImportItem.id.asc())
        ).scalars().all()
        fingerprints: dict[str, VineImportItem] = {}
        for item in items:
            fingerprint = self._vine_row_fingerprint(item)
            if fingerprint and fingerprint not in fingerprints:
                fingerprints[fingerprint] = item
        return fingerprints

    def _is_duplicate_vine_item(self, item: VineImportItem) -> bool:
        warnings = [str(warning).strip().lower() for warning in (item.parse_warnings_json or [])]
        return any(warning.startswith("duplicate of prior vine import row") for warning in warnings)

    def _resolve_duplicate_listing_from_map(
        self,
        db: Session,
        item: VineImportItem,
        previous_items_by_fingerprint: dict[str, VineImportItem],
    ) -> Listing | None:
        fingerprint = self._vine_row_fingerprint(item)
        if not fingerprint:
            return None
        candidate = previous_items_by_fingerprint.get(fingerprint)
        if candidate is not None:
            if candidate.listing_id:
                listing = db.get(Listing, candidate.listing_id)
                if listing is not None:
                    return listing
            if candidate.inventory_item_id:
                listing = db.get(Listing, candidate.inventory_item_id)
                if listing is not None:
                    return listing
        return None

    def _detect_report_year(self, rows: list[ParsedVineRow], filename: str) -> int | None:
        for row in rows:
            if row.order_date:
                return row.order_date.year
        for token in Path(filename).stem.replace("-", " ").split():
            if token.isdigit() and len(token) == 4:
                return int(token)
        return None
