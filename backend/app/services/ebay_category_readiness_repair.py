"""Repair taxonomy-caused eBay readiness blockers without inventing facts."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.enums import ListingStatus
from app.models.models import Listing
from app.services.ebay_taxonomy import (
    category_by_id,
    listing_has_motors_semantics,
    resolve_local_category,
)


SUSPICIOUS_ASPECTS = {"original/reproduction", "original reproduction"}


def _facts(listing: Listing) -> list[str]:
    source = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    facts = source.get("amazon_product_facts") if isinstance(source.get("amazon_product_facts"), dict) else source.get("source_facts") or {}
    specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
    description = str(listing.description or "").lower()
    title = str(listing.title or "").lower()
    derived = []
    # Pull product nouns from source descriptions when the slate/import title
    # is only a generic label. These are evidence phrases, not provenance or
    # fitment guesses.
    for phrase, evidence in (
        ("right reflector", ("reflector", "toyota rav4")),
        ("tpms valve stem kit", ("tpms", "valve stem")),
        ("ignition coil", ("ignition coil",)),
        ("windshield wiper blade", ("wiper blade",)),
        ("jigsaw puzzle", ("puzzle", "piece")),
    ):
        if all(term in description for term in evidence):
            derived.append(phrase)
    for phrase, evidence in (
        ("jigsaw puzzle", ("puzzle",)),
        ("women's wallet", ("wallet",)),
        ("clutch handbag", ("clutch", "leather")),
        ("tote handbag", ("tote bag",)),
        ("crossbody handbag", ("crossbody bag",)),
        ("evening handbag", ("evening bag",)),
    ):
        if all(term in title for term in evidence):
            derived.append(phrase)
    return [str(value) for value in (
        facts.get("product_type") if isinstance(facts, dict) else None,
        facts.get("category") if isinstance(facts, dict) else None,
        specifics.get("Type"), specifics.get("Brand"), specifics.get("Model"), specifics.get("MPN"),
        *derived,
    ) if str(value or "").strip()]


def _set_category(listing: Listing, candidate: dict[str, Any], *, reason: str) -> None:
    source = dict(listing.source_metadata or {})
    history = list(source.get("category_history") or [])
    history.append({
        "category_id": str(listing.category_id or ""), "reason": reason,
        "recorded_at": datetime.now(UTC).isoformat(),
    })
    source["category_history"] = history[-20:]
    source["taxonomy_marketplace"] = str(candidate.get("marketplace") or "EBAY_US")
    source["taxonomy_tree_id"] = str(candidate.get("category_tree_id") or "0")
    source["category_validation"] = {
        "status": "valid_local", "source": "required_aspect_semantic_repair",
        "category_id": str(candidate.get("category_id") or ""),
        "category_name": candidate.get("category_name"), "full_path": candidate.get("full_path"),
        "tree_id": source["taxonomy_tree_id"], "marketplace": source["taxonomy_marketplace"],
        "checked_at": datetime.now(UTC).isoformat(),
    }
    listing.source_metadata = source
    listing.category_id = str(candidate["category_id"])
    listing.category_suggestion = str(candidate.get("full_path") or candidate.get("category_name") or "")


async def repair_ebay_category_readiness(db, listing: Listing, *, reason: str, account=None) -> dict[str, Any]:
    """Repair false category blockers and quarantine true unsupported Motors.

    Candidate acceptance refreshes live eBay aspect metadata.  That makes a
    cached Original/Reproduction requirement a feedback signal, not a reason
    to ask an operator to fabricate provenance for a modern product.
    """
    from app.services.ebay_service import _cached_category_aspects, get_or_refresh_account
    from app.services.ebay_taxonomy import semantic_guard_flags

    source_state = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    archive_state = marketplace_data.get("archive_state") if isinstance(marketplace_data.get("archive_state"), dict) else {}
    labels = str(listing.custom_labels or "").lower()
    if (
        str(archive_state.get("status") or "").lower() in {"archived", "unpublished"}
        or any(label in labels for label in ("archived_vine", "archived_ebay", "archived_sold"))
        or listing.sold_at is not None
        or int(listing.quantity or 0) <= 0
    ):
        return {"classification": "ARCHIVED_OR_SOLD_SKIPPED", "status": "UNCHANGED", "changed": False}

    title = str(listing.title or "")
    source_metadata = source_state
    validation = source_metadata.get("category_validation") if isinstance(source_metadata.get("category_validation"), dict) else {}
    same_category_validation = str(validation.get("category_id") or "") == str(listing.category_id or "")
    current_tree = str(source_metadata.get("taxonomy_tree_id") or (validation.get("tree_id") if same_category_validation else "") or "")
    current_marketplace = str(source_metadata.get("taxonomy_marketplace") or (validation.get("marketplace") if same_category_validation else "") or "")
    if not current_tree and not current_marketplace:
        current_tree, current_marketplace = "0", "EBAY_US"
    current = category_by_id(db, str(listing.category_id or ""), current_tree, current_marketplace) if listing.category_id else None
    current_tree = str((current or {}).get("category_tree_id") or current_tree)
    motors = current_tree == "100"
    history = list(source_metadata.get("category_history") or [])
    current_flags = semantic_guard_flags(title, str((current or {}).get("full_path") or "")) if current else ["category_ownership_unverified"]
    current_path = str((current or {}).get("full_path") or "").lower()
    source_text = " ".join((title, str(listing.description or ""), " ".join(_facts(listing)))).lower()
    if any(term in source_text for term in ("toyota rav4", "motorcraft", "ford", "rav4")) and any(branch in current_path for branch in ("atv, side-by-side", "motorcycle & scooter", "commercial truck parts")):
        current_flags.append("passenger_vehicle_fitment_in_wrong_motors_vehicle_branch")
    if (
        "atv, side-by-side" in current_path
        and any(term in source_text for term in ("automotive", "car & truck", "tpms"))
        and not any(term in source_text for term in ("atv", "utv", "side-by-side", "motorcycle", "powersports"))
    ):
        current_flags.append("generic_car_part_in_atv_motors_category")
    true_motors = motors and listing_has_motors_semantics(listing)
    if motors and not true_motors:
        current_flags.append("motors_tree_without_correlated_product_evidence")

    # A true Motors part is a publisher-routing decision, not a generic
    # product-data blocker. Keep it in the normal approval lifecycle; the
    # publish worker selects Trading API after canonical approval.
    if true_motors and not current_flags:
        # Materialize the fully qualified identity before routing. A nested
        # validation record may be the only surviving provenance on older
        # listings; preserve it at the canonical top level now.
        if current:
            source_metadata = dict(listing.source_metadata or {})
            source_metadata["taxonomy_marketplace"] = str(current.get("marketplace") or "EBAY_MOTORS_US")
            source_metadata["taxonomy_tree_id"] = str(current.get("category_tree_id") or "100")
            source_metadata["taxonomy_tree_version"] = str(current.get("category_tree_version") or "")
            source_metadata["category_path"] = str(current.get("full_path") or "")
            source_metadata["category_validation"] = {
                "status": "valid_local", "category_id": str(current.get("category_id") or listing.category_id or ""),
                "category_name": current.get("category_name"), "full_path": current.get("full_path"),
                "marketplace": source_metadata["taxonomy_marketplace"], "tree_id": source_metadata["taxonomy_tree_id"],
                "tree_version": source_metadata["taxonomy_tree_version"], "source": "qualified_local_taxonomy_revalidation",
                "checked_at": datetime.now(UTC).isoformat(),
            }
            listing.source_metadata = source_metadata
        approved = bool(source_metadata.get("operator_approved_at"))
        listing.processing_state = "ready" if approved else "complete"
        listing.processing_stage = "ebay_motors_publisher"
        listing.processing_blocking_reason = None
        listing.processing_error_stage = None
        listing.needs_review = not approved
        if approved:
            listing.status = ListingStatus.ready
        md = dict(listing.marketplace_data or {})
        md.pop("ebay_preflight", None)
        cached = md.get("marketplace_preflight") if isinstance(md.get("marketplace_preflight"), dict) else {}
        if isinstance(cached.get("by_marketplace"), dict):
            cached = dict(cached)
            by_marketplace = dict(cached["by_marketplace"])
            by_marketplace.pop("ebay", None)
            cached["by_marketplace"] = by_marketplace
            md["marketplace_preflight"] = cached
        listing.marketplace_data = md
        return {"classification": "TRUE_MOTORS", "status": "ROUTED_TRADING_API_MOTORS", "changed": False}

    if motors:
        # A tree-100 category with no structured Motors corroboration is a
        # false/unsupported ownership signal. Re-resolve the ordinary product
        # into EBAY_US; do not let a prior blocker preserve Motors ownership.
        history.append({"category_id": str(listing.category_id or ""), "reason": "false_motors_tree_ownership", "recorded_at": datetime.now(UTC).isoformat()})
        source_metadata["category_history"] = history[-20:]
        listing.source_metadata = source_metadata
    candidates = resolve_local_category(db, title, _facts(listing), limit=30)
    # Reconsideration deliberately favors EBAY_US for contemporary products
    # and every false Motors signal.  We never guess Original/Reproduction.
    resolve_as_motors = true_motors or listing_has_motors_semantics(listing)
    target_tree = "100" if resolve_as_motors else "0"
    target_marketplace = "EBAY_MOTORS_US" if resolve_as_motors else "EBAY_US"
    candidates = [row for row in candidates if row.get("leaf")
                  and str(row.get("category_tree_id") or "0") == target_tree
                  and str(row.get("marketplace") or "") == target_marketplace]
    candidates = [row for row in candidates if float(row.get("score") or 0) >= 18 and (int(row.get("title_name_matches") or 0) >= 1 or row.get("_concept_match"))]
    if not candidates:
        listing.processing_state = "needs_attention"
        listing.processing_stage = "category_revalidation"
        listing.processing_blocking_reason = "CATEGORY_REVALIDATION_REQUIRED: no sufficiently supported EBAY_US category candidate was found; the saved Motors category was not trusted."
        listing.processing_error_stage = "category_resolution"
        listing.needs_review = False
        md = dict(listing.marketplace_data or {})
        md.pop("ebay_preflight", None)
        cached = md.get("marketplace_preflight") if isinstance(md.get("marketplace_preflight"), dict) else {}
        by_marketplace = cached.get("by_marketplace") if isinstance(cached.get("by_marketplace"), dict) else {}
        if by_marketplace:
            cached = dict(cached)
            by_marketplace = dict(by_marketplace)
            by_marketplace.pop("ebay", None)
            cached["by_marketplace"] = by_marketplace
            md["marketplace_preflight"] = cached
        listing.marketplace_data = md
        return {"classification": "FALSE_MOTORS_CATEGORY" if motors else "CATEGORY_REVALIDATION_REQUIRED", "status": "NO_SAFE_NON_MOTORS_CATEGORY", "changed": False}
    account = account or await get_or_refresh_account(listing.user_id, db)
    live_validation_failures: list[dict[str, str]] = []
    for candidate in candidates[:8]:
        try:
            aspects, source, available = await _cached_category_aspects(
                db, account, str(candidate["category_id"]), marketplace_id=target_marketplace, taxonomy_tree_id=target_tree,
                force_refresh=True, allow_live_refresh=True,
            )
        except Exception as exc:
            live_validation_failures.append({"category_id": str(candidate.get("category_id") or ""), "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
            continue
        required = {
            str(row.get("localizedAspectName") or "").strip().lower()
            for row in (aspects or {}).get("aspects") or []
            if bool((row.get("aspectConstraint") or {}).get("aspectRequired"))
        }
        if required & SUSPICIOUS_ASPECTS:
            live_validation_failures.append({"category_id": str(candidate.get("category_id") or ""), "error": "live category still requires Original/Reproduction; category rejected as semantically suspect"})
            continue
        _set_category(listing, candidate, reason=reason)
        md = dict(listing.marketplace_data or {})
        md.pop("ebay_preflight", None)
        cached_preflight = md.get("marketplace_preflight") if isinstance(md.get("marketplace_preflight"), dict) else {}
        if isinstance(cached_preflight.get("by_marketplace"), dict):
            cached_preflight = dict(cached_preflight)
            by_marketplace = dict(cached_preflight["by_marketplace"])
            by_marketplace.pop("ebay", None)
            cached_preflight["by_marketplace"] = by_marketplace
            md["marketplace_preflight"] = cached_preflight
        md["category_repair"] = {"reason": reason, "live_aspect_source": source, "at": datetime.now(UTC).isoformat()}
        listing.marketplace_data = md
        listing.processing_blocking_reason = None
        listing.processing_error_stage = None
        listing.processing_next_retry_at = None
        listing.processing_state = "queued"
        listing.processing_stage = "category_resolution"
        listing.needs_review = False
        if true_motors:
            approved = bool((listing.source_metadata or {}).get("operator_approved_at"))
            listing.processing_state = "ready" if approved else "complete"
            listing.processing_stage = "ebay_motors_publisher"
            listing.processing_blocking_reason = None
            listing.processing_error_stage = None
            listing.needs_review = not approved
            if approved:
                listing.status = ListingStatus.ready
            return {"classification": "TRUE_MOTORS" if true_motors else ("FALSE_MOTORS_CATEGORY" if motors else "ORIGINAL_REPRODUCTION_CATEGORY_MISMATCH"), "status": "ROUTED_TRADING_API_MOTORS" if true_motors else "REPAIRED", "changed": True, "category_id": listing.category_id, "live_aspect_source": source, "required_aspects": sorted(required)}
        return {
            "classification": "FALSE_MOTORS_CATEGORY" if motors else "CATEGORY_REVALIDATED",
            "status": "REPAIRED", "changed": True, "category_id": listing.category_id,
            "live_aspect_source": source, "required_aspects": sorted(required),
        }
    listing.processing_state = "needs_attention"
    listing.processing_stage = "category_revalidation"
    listing.processing_blocking_reason = "CATEGORY_REVALIDATION_REQUIRED: candidate categories could not be validated against current eBay taxonomy aspects."
    listing.processing_error_stage = "category_resolution"
    listing.needs_review = False
    md = dict(listing.marketplace_data or {})
    md.pop("ebay_preflight", None)
    listing.marketplace_data = md
    return {"classification": "FALSE_MOTORS_CATEGORY" if motors else "CATEGORY_REVALIDATION_REQUIRED", "status": "NO_LIVE_VALIDATED_CATEGORY", "changed": False, "live_validation_failures": live_validation_failures[:8]}
