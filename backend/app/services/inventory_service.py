from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from celery import chord, group, signature
from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models.models import BulkJob, Listing


class InventorySafetyError(ValueError):
    pass


class InventoryService:
    """Inventory read/write orchestration with oversell safety checks."""

    STALE_AFTER_DAYS = 7
    BULK_BATCH_SIZE = 150

    @staticmethod
    def _normalize_labels(labels: list[str] | None) -> list[str]:
        values = labels or []
        cleaned = sorted({label.strip() for label in values if isinstance(label, str) and label.strip()})
        return cleaned

    @staticmethod
    def _normalized_platform_quantities(platform_quantities: dict | None) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for key, value in (platform_quantities or {}).items():
            if not key:
                continue
            try:
                int_value = int(value)
            except (TypeError, ValueError):
                int_value = 0
            normalized[str(key)] = max(0, int_value)
        return normalized

    def validate_quantity(self, quantity: int, platform_quantities: dict | None) -> None:
        if quantity < 0:
            raise InventorySafetyError("Quantity cannot be negative")

        platform_map = self._normalized_platform_quantities(platform_quantities)
        highest_channel_quantity = max(platform_map.values(), default=0)
        if highest_channel_quantity > quantity:
            raise InventorySafetyError(
                "Quantity is lower than one or more platform quantities; this can cause overselling"
            )

    def build_inventory_query(
        self,
        label: str | None = None,
        multi_quantity_only: bool = False,
        stale: bool = False,
        search: str | None = None,
    ) -> Select:
        stmt = select(Listing)
        if multi_quantity_only:
            stmt = stmt.where(Listing.quantity > 1)

        if stale:
            stale_cutoff = datetime.utcnow() - timedelta(days=self.STALE_AFTER_DAYS)
            stmt = stmt.where(
                (Listing.stale_flag.is_(True))
                | (Listing.last_refreshed.is_(None))
                | (Listing.last_refreshed < stale_cutoff)
            )

        # JSON operators vary by db backend; do in-python for portability.
        if label:
            target = label.strip().lower()
            if target:
                listings = stmt
                return listings

        if search and search.strip():
            term = f"%{search.strip()}%"
            stmt = stmt.where(or_(Listing.title.ilike(term), cast(Listing.id, String).ilike(term)))

        return stmt.order_by(Listing.updated_at.desc())

    def apply_label_filter(self, listings: list[Listing], label: str | None) -> list[Listing]:
        if not label:
            return listings
        target = label.strip().lower()
        if not target:
            return listings
        return [
            listing
            for listing in listings
            if any((existing or "").strip().lower() == target for existing in (listing.custom_labels or []))
        ]

    def refresh_sync_status(self, listing: Listing) -> str:
        platform_map = self._normalized_platform_quantities(listing.platform_quantities)
        if not platform_map:
            return "Not Synced"

        channel_values = list(platform_map.values())
        if all(value == listing.quantity for value in channel_values):
            return "Synced"
        if any(value == 0 for value in channel_values):
            return "Attention"
        return "Partial"

    def update_listing_inventory(
        self,
        listing: Listing,
        quantity: int | None = None,
        platform_quantities: dict | None = None,
        labels_to_add: list[str] | None = None,
        labels_to_remove: list[str] | None = None,
        delist: bool = False,
        relist: bool = False,
    ) -> Listing:
        if quantity is not None:
            listing.quantity = int(quantity)

        if platform_quantities is not None:
            listing.platform_quantities = self._normalized_platform_quantities(platform_quantities)

        self.validate_quantity(listing.quantity, listing.platform_quantities)

        current_labels = set(self._normalize_labels(listing.custom_labels))
        for label in self._normalize_labels(labels_to_add):
            current_labels.add(label)
        for label in self._normalize_labels(labels_to_remove):
            current_labels.discard(label)
        listing.custom_labels = sorted(current_labels)

        if delist:
            listing.platform_quantities = {k: 0 for k in (listing.platform_quantities or {}).keys()}
        elif relist:
            channels = listing.platform_quantities or {}
            listing.platform_quantities = {k: listing.quantity for k in channels.keys()} or listing.platform_quantities

        listing.last_refreshed = datetime.utcnow()
        return listing

    def bulk_update(self, db: Session, listings: list[Listing], payload: dict) -> list[Listing]:
        updated: list[Listing] = []
        for listing in listings:
            self.update_listing_inventory(
                listing,
                quantity=payload.get("quantity"),
                platform_quantities=payload.get("platform_quantities"),
                labels_to_add=payload.get("add_labels"),
                labels_to_remove=payload.get("remove_labels"),
                delist=bool(payload.get("delist")),
                relist=bool(payload.get("relist")),
            )
            db.add(listing)
            updated.append(listing)
        db.commit()
        for listing in updated:
            db.refresh(listing)
        return updated

    def resolve_listing_ids(self, db: Session, listing_ids: list[int] | None = None, filters: dict | None = None) -> list[int]:
        if listing_ids:
            return [int(listing_id) for listing_id in listing_ids]

        filters = filters or {}
        stmt = self.build_inventory_query(
            label=filters.get("label"),
            multi_quantity_only=bool(filters.get("quantity_gt_one")),
            stale=bool(filters.get("stale")),
            search=filters.get("search"),
        )
        listings = db.execute(stmt).scalars().all()
        filtered = self.apply_label_filter(listings, filters.get("label"))
        return [listing.id for listing in filtered]

    def queue_bulk_job(
        self,
        db: Session,
        user_id: int,
        action: str,
        listing_ids: list[int],
        payload: dict | None = None,
        filters: dict | None = None,
    ) -> BulkJob:
        job = BulkJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            action=action,
            status="queued",
            total_items=len(listing_ids),
            processed_items=0,
            errors=[],
            error_count=0,
            payload=payload or {},
            filters=filters or {},
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        if not listing_ids:
            job.status = "completed"
            db.add(job)
            db.commit()
            return job

        chunks = [listing_ids[i : i + self.BULK_BATCH_SIZE] for i in range(0, len(listing_ids), self.BULK_BATCH_SIZE)]
        header = group(
            signature("bulk_process_inventory_chunk", args=[job.id, action, payload or {}, chunk, batch_index])
            for batch_index, chunk in enumerate(chunks)
        )
        callback = signature("bulk_finalize_job", args=[job.id])
        chord(header)(callback)
        return job

    @staticmethod
    def estimate_minutes(total_items: int) -> int:
        if total_items <= 0:
            return 0
        return max(1, math.ceil(total_items / 150))
