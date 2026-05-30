from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import ListingStatus
from app.models.models import Listing, ProductMediaCache, VineImportBatch, VineImportItem, User
from app.services.amazon_media import AmazonProductMediaProvider
from app.services.listing_workspace import normalize_marketplace_data
from app.services.vine_parser import ParsedVineRow, parse_vine_pdf, parse_vine_xlsx
from app.services.vine_policy import review_vine_product


class VineImportService:
    def create_batch_from_upload(
        self,
        db: Session,
        *,
        current_user: User,
        filename: str,
        file_bytes: bytes,
        reference_date: date | None = None,
    ) -> VineImportBatch:
        extension = Path(filename).suffix.lower()
        if extension not in {".xlsx", ".pdf"}:
            raise ValueError("Only .xlsx and .pdf Vine reports are supported")
        parsed_rows = (
            parse_vine_xlsx(file_bytes, reference_date=reference_date)
            if extension == ".xlsx"
            else parse_vine_pdf(file_bytes, reference_date=reference_date)
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
                parse_warnings_json=parsed.parse_warnings,
                media_status="pending",
                restricted_review_required=policy.restricted_review_required,
                restricted_reasons=policy.restricted_reasons,
                detected_category_guess=policy.detected_category_guess,
                marketplace_allowed_status=policy.marketplace_allowed_status,
                source_confidence=parsed.source_confidence,
                reviewed=extension == ".xlsx",
            )
            db.add(item)

        db.flush()
        self._refresh_batch_stats(db, batch)
        db.commit()
        db.refresh(batch)
        return batch

    def fetch_media(self, db: Session, *, batch: VineImportBatch, item_ids: list[int]) -> dict:
        provider = AmazonProductMediaProvider(db, owner_user_id=batch.user_id)
        items = db.execute(select(VineImportItem).where(VineImportItem.batch_id == batch.id, VineImportItem.id.in_(item_ids))).scalars().all()
        fetched = 0
        blocked = 0
        for item in items:
            if not item.asin:
                item.media_status = "missing_asin"
                item.parse_warnings_json = [*(item.parse_warnings_json or []), "Cannot fetch images without ASIN"]
                db.add(item)
                continue
            result = provider.lookup_by_asin(item.asin)
            item.media_status = result.get("status") or "blocked"
            item.media_asset_ids_json = result.get("local_asset_ids") or []
            fetched += 1 if item.media_status in {"cached", "fetched"} else 0
            blocked += 1 if item.media_status not in {"cached", "fetched"} else 0
            db.add(item)
        db.commit()
        return {"fetched": fetched, "blocked": blocked}

    def create_inventory_records(self, db: Session, *, batch: VineImportBatch, item_ids: list[int], include_locked: bool) -> dict:
        items = db.execute(select(VineImportItem).where(VineImportItem.batch_id == batch.id, VineImportItem.id.in_(item_ids))).scalars().all()
        created = 0
        skipped = 0
        for item in items:
            if item.inventory_item_id:
                skipped += 1
                continue
            if item.eligibility_status in {"cancelled", "invalid"}:
                skipped += 1
                continue
            if item.eligibility_status.startswith("locked_until_") and not include_locked:
                skipped += 1
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
                marketplace_data=normalize_marketplace_data(
                    {
                        "source_marketplace": "amazon",
                        "manual_entry": False,
                        "targets": ["ebay", "amazon"],
                    }
                ),
            )
            db.add(listing)
            db.flush()
            item.inventory_item_id = listing.id
            db.add(item)
            created += 1
        self._refresh_batch_stats(db, batch)
        db.commit()
        return {"created": created, "skipped": skipped}

    def create_listing_drafts(
        self,
        db: Session,
        *,
        batch: VineImportBatch,
        item_ids: list[int],
        fetch_media_first: bool = False,
        require_media_for_asin: bool = False,
        allow_drafts_without_media: bool = False,
    ) -> dict:
        items = db.execute(select(VineImportItem).where(VineImportItem.batch_id == batch.id, VineImportItem.id.in_(item_ids))).scalars().all()
        created = 0
        updated = 0
        skipped = 0
        created_listing_ids: list[int] = []
        updated_listing_ids: list[int] = []
        skipped_item_ids: list[int] = []
        listing_ids: list[int] = []

        provider = AmazonProductMediaProvider(db, owner_user_id=batch.user_id) if fetch_media_first else None
        for item in items:
            if item.eligibility_status != "eligible" or item.restricted_review_required or item.source_confidence == "low":
                skipped += 1
                skipped_item_ids.append(int(item.id))
                continue
            if item.inventory_item_id is None:
                result = self.create_inventory_records(db, batch=batch, item_ids=[item.id], include_locked=False)
                if result["created"] == 0:
                    skipped += 1
                    skipped_item_ids.append(int(item.id))
                    continue
                db.refresh(item)
            listing = db.get(Listing, item.inventory_item_id)
            if listing is None:
                skipped += 1
                skipped_item_ids.append(int(item.id))
                continue

            if provider is not None:
                if not item.asin:
                    item.media_status = "missing_asin"
                    item.parse_warnings_json = [*(item.parse_warnings_json or []), "Cannot fetch images without ASIN"]
                else:
                    result = provider.lookup_by_asin(item.asin)
                    item.media_status = result.get("status") or item.media_status or "blocked"
                    item.media_asset_ids_json = result.get("local_asset_ids") or item.media_asset_ids_json or []
                db.add(item)

            cached_urls = self._lookup_cached_media_urls(db, item.asin)
            if require_media_for_asin and not allow_drafts_without_media and item.asin:
                if settings.amazon_media_lookup_enabled and settings.amazon_media_page_fallback_enabled and not cached_urls:
                    item.parse_warnings_json = [*(item.parse_warnings_json or []), "Draft creation blocked until photos are fetched for this ASIN"]
                    if item.media_status in {None, "pending"}:
                        item.media_status = "blocked"
                    db.add(item)
                    skipped += 1
                    skipped_item_ids.append(int(item.id))
                    continue

            listing.title = self._generate_title(item.product_name)
            listing.description = self._generate_description(item.product_name)
            listing.status = ListingStatus.draft
            listing.needs_review = True
            listing.condition = listing.condition or "Open Box"
            listing.source_type = "amazon_vine"
            listing.source_metadata = self._source_metadata(item, batch.id)
            listing.marketplace_data = normalize_marketplace_data(
                {
                    **(listing.marketplace_data or {}),
                    "source_marketplace": "amazon",
                    "manual_entry": False,
                    "targets": ["ebay", "amazon"],
                }
            )

            if not (listing.image_urls or []):
                if cached_urls:
                    listing.image_urls = cached_urls
                else:
                    listing.custom_labels = list(dict.fromkeys([*(listing.custom_labels or []), "needs_photos"]))
            else:
                listing.custom_labels = [label for label in (listing.custom_labels or []) if label != "needs_photos"] or None

            db.add(listing)
            db.flush()
            listing_id = listing.id
            if listing_id is None:
                skipped += 1
                skipped_item_ids.append(int(item.id))
                continue

            listing_id = int(listing_id)
            if not item.listing_id:
                created += 1
                created_listing_ids.append(listing_id)
            else:
                updated += 1
                updated_listing_ids.append(listing_id)
            item.listing_id = listing_id
            db.add(item)
            if listing_id not in listing_ids:
                listing_ids.append(listing_id)
        total_drafts = db.execute(
            select(func.count())
            .select_from(VineImportItem)
            .where(VineImportItem.batch_id == batch.id, VineImportItem.listing_id.is_not(None))
        ).scalar_one()
        batch.drafts_created_count = int(total_drafts or 0)
        batch.status = "imported" if batch.drafts_created_count else batch.status
        db.add(batch)
        db.commit()
        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "listing_ids": listing_ids,
            "created_listing_ids": created_listing_ids,
            "updated_listing_ids": updated_listing_ids,
            "skipped_item_ids": skipped_item_ids,
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
        db.add(batch)

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

    def _source_metadata(self, item: VineImportItem, batch_id: int) -> dict:
        return {
            "asin": item.asin,
            "order_number": item.order_number,
            "estimated_tax_value": item.estimated_tax_value,
            "order_date": item.order_date.isoformat() if item.order_date else None,
            "shipped_date": item.shipped_date.isoformat() if item.shipped_date else None,
            "eligible_after": item.eligible_after.isoformat() if item.eligible_after else None,
            "eligibility_status": item.eligibility_status,
            "batch_id": batch_id,
        }

    def _generate_title(self, product_name: str | None) -> str:
        base = (product_name or "Amazon Vine Item").strip()
        return " ".join(word for word in base.split() if "amazon" not in word.lower())[:80] or "Amazon Vine Item"

    def _generate_description(self, product_name: str | None) -> str:
        name = (product_name or "Item").strip()
        return (
            f"{name}\n\n"
            "Condition: open box or unused customer-owned item from an internal Vine intake workflow. "
            "Manual review is required before publish. Confirm exact condition, completeness, and accessories."
        )

    def _detect_report_year(self, rows: list[ParsedVineRow], filename: str) -> int | None:
        for row in rows:
            if row.order_date:
                return row.order_date.year
        for token in Path(filename).stem.replace("-", " ").split():
            if token.isdigit() and len(token) == 4:
                return int(token)
        return None
