from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Listing, ListingABTestVariant


class ListingOptimizerService:
    def optimize_listing(self, db: Session, listing_id: int) -> dict:
        listing = db.get(Listing, listing_id)
        if not listing:
            raise ValueError("Listing not found")

        base_title = listing.title or ""
        tags = listing.tags or []
        missing_attributes = []
        if not listing.condition:
            missing_attributes.append("condition")
        if not listing.category_suggestion:
            missing_attributes.append("category")
        if len(tags) < 3:
            missing_attributes.append("keywords")

        suggested_keywords = list(dict.fromkeys(tags + ["authentic", "fast shipping", "clean condition"]))[:8]
        improved_title = f"{base_title} | {listing.category_suggestion or 'Top Seller'} | Fast Ship".strip(" |")[:255]

        variants = self.create_ab_variants(db, listing_id, improved_title, listing.description)
        return {
            "listing_id": listing_id,
            "suggested_title": improved_title,
            "suggested_keywords": suggested_keywords,
            "missing_attributes": missing_attributes,
            "ab_test_variants": variants,
            "visibility_assessment": "low" if len(tags) < 3 else "moderate",
        }

    def create_ab_variants(self, db: Session, listing_id: int, improved_title: str, description: str | None) -> list[dict]:
        existing = db.execute(
            select(ListingABTestVariant).where(ListingABTestVariant.listing_id == listing_id)
        ).scalars().all()
        if existing:
            return [self._variant_dict(v) for v in existing]

        variant_a = ListingABTestVariant(
            listing_id=listing_id,
            variant_label="A",
            title=improved_title,
            description=description,
            is_active=True,
        )
        variant_b = ListingABTestVariant(
            listing_id=listing_id,
            variant_label="B",
            title=f"{improved_title} - Limited Deal"[:255],
            description=description,
            is_active=False,
        )
        db.add_all([variant_a, variant_b])
        db.commit()
        db.refresh(variant_a)
        db.refresh(variant_b)
        return [self._variant_dict(variant_a), self._variant_dict(variant_b)]

    @staticmethod
    def _variant_dict(variant: ListingABTestVariant) -> dict:
        ctr = (variant.clicks / variant.impressions) if variant.impressions else 0
        return {
            "id": variant.id,
            "label": variant.variant_label,
            "title": variant.title,
            "is_active": variant.is_active,
            "impressions": variant.impressions,
            "clicks": variant.clicks,
            "conversions": variant.conversions,
            "ctr": round(ctr, 3),
        }
