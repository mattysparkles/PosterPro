"""Read-only listing quality audit for operator/admin review.

This intentionally performs no writes, preflight calls, or marketplace calls.
It reports local data-quality signals so cleanup scope can be measured before
any repair is authorized.
"""
from __future__ import annotations

import argparse
import json
import re

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import Listing

NOISE = re.compile(r"amazon|refund|replacement|free return|delivery|seller|return policy|breadcrumb", re.I)


def _source_fact_values(row: Listing) -> list[str]:
    metadata = row.source_metadata if isinstance(row.source_metadata, dict) else {}
    facts = metadata.get("amazon_product_facts") if isinstance(metadata.get("amazon_product_facts"), dict) else metadata.get("source_facts")
    if not isinstance(facts, dict):
        return []
    values: list[str] = []
    for value in facts.get("feature_bullets") or []:
        text = " ".join(str(value or "").split()).strip()
        if len(text) >= 14 and not NOISE.search(text):
            values.append(text)
    specs = facts.get("specifications") if isinstance(facts.get("specifications"), dict) else {}
    for key, value in specs.items():
        text = " ".join(f"{key} {value}".split()).strip()
        if text and not NOISE.search(text):
            values.append(text)
    return values[:16]


def _bucket(row: Listing) -> str:
    if row.sold_at is not None or int(row.quantity or 1) <= 0:
        return "SOLD"
    if str(row.ebay_listing_id or "").strip() or str(row.ebay_publish_status or "").upper() == "POSTED":
        return "PUBLISHED"
    state = str(row.processing_state or "").lower()
    if state in {"processing", "queued", "enriching"}:
        return "PROCESSING"
    if state in {"needs_attention", "blocked", "failed", "error"}:
        return "NEEDS_ATTENTION"
    if row.needs_review:
        return "NEEDS_REVIEW"
    return "DRAFT"


def audit(limit: int | None = None, source_type: str | None = None, details: bool = False) -> dict:
    with SessionLocal() as db:
        query = select(Listing).order_by(Listing.id.desc())
        if source_type:
            query = query.where(Listing.source_type == source_type)
        if limit:
            query = query.limit(limit)
        rows = db.execute(query).scalars().all()

        totals: dict[str, int] = {}
        flags: dict[str, dict[str, int]] = {}
        detail_rows: list[dict] = []
        for row in rows:
            bucket = _bucket(row)
            totals[bucket] = totals.get(bucket, 0) + 1
            category = str(row.category_suggestion or "").strip()
            description = str(row.canonical_description or row.description or "").strip()
            specifics = row.item_specifics if isinstance(row.item_specifics, dict) else {}
            image_count = len(row.image_urls or [])
            source_facts = _source_fact_values(row)
            description_lower = description.lower()
            covered_facts = sum(1 for fact in source_facts if any(token in description_lower for token in re.findall(r"[a-z0-9]{4,}", fact.lower())[:4]))
            checks = {
                "missing_category_id": not str(row.category_id or "").strip(),
                "suspicious_category": bool(NOISE.search(category)) or category.lower() in {"other", "other > needs category review"},
                "thin_description": len(description.split()) < 18,
                "missing_description": not description,
                "missing_images": image_count == 0,
                "missing_item_specifics": not bool(specifics),
                "source_fact_coverage": bool(source_facts) and covered_facts < max(1, min(3, len(source_facts) // 3)),
                "description_source_noise": bool(NOISE.search(description)),
            }
            for name, flagged in checks.items():
                if flagged:
                    flags.setdefault(name, {})[bucket] = flags.setdefault(name, {}).get(bucket, 0) + 1
            if details:
                detail_rows.append({
                    "listing_id": row.id,
                    "source_title": (
                        (row.source_metadata or {}).get("source_title")
                        or (row.source_metadata or {}).get("product_name")
                        or ((row.source_metadata or {}).get("amazon_product_facts") or {}).get("title")
                    ) if isinstance(row.source_metadata, dict) else None,
                    "title": row.title,
                    "category": category,
                    "category_id": row.category_id,
                    "description_chars": len(description),
                    "description_words": len(description.split()),
                    "image_count": image_count,
                    "processing_state": row.processing_state,
                    "needs_review": bool(row.needs_review),
                    "queue": bucket,
                    "published": bool(row.ebay_listing_id or str(row.ebay_publish_status or "").upper() == "POSTED"),
                    "flags": [name for name, flagged in checks.items() if flagged],
                    "source_fact_count": len(source_facts),
                    "source_facts_covered": covered_facts,
                })
        result = {"read_only": True, "rows_audited": len(rows), "by_state": totals, "flags": flags}
        if details:
            result["items"] = detail_rows
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--source-type")
    parser.add_argument("--details", action="store_true", help="Include item-level fields and quality flags")
    args = parser.parse_args()
    print(json.dumps(audit(limit=args.limit, source_type=args.source_type, details=args.details), indent=2, sort_keys=True))
