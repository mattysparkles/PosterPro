"""Inventory-driven taxonomy coverage and reusable product cohorts.

This report is deliberately suggestive: text matches never become an approved
category mapping or make a listing publishable. Only an operator-confirmed,
current mapping is counted as reusable coverage.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, selectinload

from app.models.models import Listing
from app.services.canonical_readiness import canonical_listing_readiness
from app.services.canonical_taxonomy_mapping import _normalize_name

MARKETPLACES = ("ebay", "facebook", "mercari")


def _identity(listing: Listing) -> tuple[str, str, str]:
    metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    facts = metadata.get("amazon_product_facts") if isinstance(metadata.get("amazon_product_facts"), dict) else {}
    identifiers = {**metadata, **facts}
    for key in ("asin", "upc", "ean", "gtin", "mpn", "oem_part_number"):
        value = re.sub(r"[^a-z0-9]", "", str(identifiers.get(key) or "").casefold())
        if len(value) >= 5:
            return (f"id:{key}:{value}", str(listing.title or "").strip()[:200], f"source identifier {key.upper()}")
    normalized_title = _normalize_name(listing.title or "")
    normalized_category = _normalize_name(listing.category_suggestion or "")
    if len(normalized_title.split()) < 2 or normalized_title in {"unknown product", "untitled listing", "product", "component"}:
        return (f"ambiguous:{listing.id}", str(listing.title or "Untitled listing").strip()[:200],
                "ambiguous title; kept as a single-item cohort")
    identity = f"title:{normalized_category}:{normalized_title}"
    return identity, str(listing.title or "Untitled listing").strip()[:200], "exact normalized title and source category"


def _active(listing: Listing) -> bool:
    labels = {str(value).strip().lower() for value in (listing.custom_labels or [])}
    status = str(getattr(listing.status, "value", listing.status) or "").lower()
    return not labels.intersection({"archived_vine", "archived_sold"}) and status not in {
        "archived", "sold", "posted", "published", "rejected",
    }


def inventory_taxonomy_cohort_report(db: Session, *, owner_user_id: int, limit: int = 30) -> dict[str, Any]:
    """Return tenant-scoped cohort coverage and current canonical readiness."""
    rows = db.query(
        Listing.id.label("id"), Listing.title.label("title"),
        Listing.category_suggestion.label("category_suggestion"),
        Listing.source_metadata.label("source_metadata"), Listing.status.label("status"),
        Listing.source_type.label("source_type"), Listing.custom_labels.label("custom_labels"),
    ).filter(Listing.user_id == owner_user_id).order_by(Listing.id).all()
    rows = [SimpleNamespace(**dict(row._mapping)) for row in rows]
    cohorts: dict[str, dict[str, Any]] = {}
    active_rows = [row for row in rows if _active(row)]
    ready_counts = Counter()
    blocker_counts: dict[str, Counter] = {market: Counter() for market in MARKETPLACES}
    for listing in active_rows:
        identity, label, basis = _identity(listing)
        cohort = cohorts.setdefault(identity, {
            "cohort_key": hashlib.sha256(f"{owner_user_id}:{identity}".encode()).hexdigest()[:24],
            "label": label, "identity_basis": basis, "listing_ids": [], "listing_count": 0,
            "_member_listing_ids": [],
            "statuses": Counter(), "source_types": Counter(), "category_hints": Counter(),
        })
        cohort["listing_count"] += 1
        cohort["_member_listing_ids"].append(int(listing.id))
        if len(cohort["listing_ids"]) < 50:
            cohort["listing_ids"].append(int(listing.id))
        cohort["statuses"][str(getattr(listing.status, "value", listing.status) or "unknown")] += 1
        cohort["source_types"][str(listing.source_type or "unknown")] += 1
        cohort["category_hints"][str(listing.category_suggestion or "(missing)")] += 1
    # The action-oriented Ready to Publish figure is for already approved
    # inventory only. Draft preparation blockers remain on listing details;
    # this avoids running full destination validation for every historical row.
    approved_ids = [int(listing.id) for listing in active_rows
                    if str(getattr(listing.status, "value", listing.status) or "").lower() == "ready"]
    if approved_ids:
        approved_rows = (db.query(Listing).options(selectinload(Listing.marketplace_listings))
                         .filter(Listing.user_id == owner_user_id, Listing.id.in_(approved_ids)).all())
        for listing in approved_rows:
            for market in MARKETPLACES:
                result = canonical_listing_readiness(listing, marketplace=market)
                if bool(result.get("destination_publishable")):
                    ready_counts[market] += 1
                for reason in result.get("blocking_reasons") or []:
                    blocker_counts[market][str(reason)] += 1

    # Concepts are currently keyed by normalized concept name. Count only
    # owner-scoped mappings whose registry category and taxonomy version remain
    # current and verified; references and stale mappings are not coverage.
    confirmed_by_concept: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    try:
        mapping_rows = db.execute(text("""SELECT cp.normalized_name,m.marketplace,m.evidence_json
            FROM canonical_product_concepts cp
            JOIN marketplace_taxonomy_mappings m ON m.concept_id=cp.concept_id AND m.owner_user_id=cp.owner_user_id
            JOIN marketplace_taxonomy_versions v ON v.version_id=m.taxonomy_version_id AND v.is_current=TRUE
            JOIN marketplace_taxonomy_categories c ON c.version_id=m.taxonomy_version_id AND c.category_key=m.category_key
            WHERE cp.owner_user_id=:owner AND m.owner_user_id=:owner AND m.operator_confirmed=TRUE
              AND m.invalidated_at IS NULL AND m.verification_status='operator_confirmed'
              AND c.terminal_status='terminal' AND c.publishability_status='publishable'
              AND c.verification_status IN ('authoritatively_verified','live_browser_verified')
              AND c.staleness_status='fresh'"""), {"owner": owner_user_id}).mappings().all()
        for mapping in mapping_rows:
            evidence = mapping["evidence_json"]
            if isinstance(evidence, str):
                try:
                    evidence = json.loads(evidence)
                except (TypeError, ValueError):
                    evidence = {}
            operator_evidence = evidence.get("operator_evidence") if isinstance(evidence, dict) else {}
            listing_id = (operator_evidence or {}).get("posterpro_listing_id") if isinstance(operator_evidence, dict) else None
            if listing_id is not None:
                try:
                    confirmed_by_concept[str(mapping["normalized_name"])][str(mapping["marketplace"]).lower()].add(int(listing_id))
                except (TypeError, ValueError):
                    pass
    except Exception:
        # Older isolated/test schemas may not have the taxonomy tables. The
        # inventory report remains useful and reports zero confirmed mappings.
        db.rollback()

    ordered = sorted(cohorts.values(), key=lambda item: (-item["listing_count"], item["label"].casefold()))
    for cohort in ordered:
        concept_name = _normalize_name(cohort["label"])
        members = set(cohort.pop("_member_listing_ids", []))
        cohort["confirmed_marketplaces"] = sorted(
            market for market, mapped_listing_ids in confirmed_by_concept.get(concept_name, {}).items()
            if members.intersection(mapped_listing_ids))
        cohort["mapped_listing_count"] = cohort["listing_count"] if cohort["confirmed_marketplaces"] else 0
        cohort["statuses"] = dict(cohort["statuses"])
        cohort["source_types"] = dict(cohort["source_types"])
        cohort["category_hints"] = dict(cohort["category_hints"])
        cohort["destination_candidates"] = "not_generated"

    return {
        "account_user_id": owner_user_id,
        "inventory_count": len(rows),
        "active_inventory_count": len(active_rows),
        "approved_ready_inventory_count": sum(
            1 for listing in active_rows
            if str(getattr(listing.status, "value", listing.status) or "").lower() == "ready"),
        "cohort_count": len(cohorts),
        "approved_destination_publishable_counts": {market: int(ready_counts[market]) for market in MARKETPLACES},
        "confirmed_mapped_inventory_counts": {
            market: sum(item["listing_count"] for item in ordered if market in item["confirmed_marketplaces"])
            for market in MARKETPLACES
        },
        "top_cohorts": ordered[:max(1, min(int(limit), 100))],
        "top_blockers": {
            market: [{"reason": reason, "listing_count": count}
                     for reason, count in blocker_counts[market].most_common(8)]
            for market in MARKETPLACES
        },
        "matching_policy": "Exact source identifier when present; otherwise exact normalized title and source category. Suggestions require operator confirmation.",
    }
