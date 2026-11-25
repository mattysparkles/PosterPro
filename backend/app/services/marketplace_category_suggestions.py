"""Persist review-only, marketplace-specific category suggestions."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.models.models import Listing
from app.services.inventory_taxonomy_cohorts import _identity
from app.services.marketplace_taxonomy_registry import rank_taxonomy_candidates

MARKETPLACES = ("facebook", "ebay", "mercari")


def _value_map(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {"".join(ch for ch in str(key).casefold() if ch.isalnum()): item for key, item in value.items()}


def _candidate_attributes(candidate: dict[str, Any], specifics: dict[str, Any]) -> tuple[list[str] | None, list[str]]:
    required = candidate.get("required_aspects")
    if not isinstance(required, list):
        return None, []
    present = _value_map(specifics)
    names = []
    for item in required:
        if not isinstance(item, dict):
            continue
        name = str(item.get("localizedAspectName") or item.get("name") or item.get("attribute_name") or "").strip()
        if name:
            names.append(name)
    missing = [name for name in names if not str(present.get("".join(ch for ch in name.casefold() if ch.isalnum())) or "").strip()]
    return names, missing


def _identity_evidence(listing: Listing) -> list[str]:
    metadata = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    facts = metadata.get("amazon_product_facts") if isinstance(metadata.get("amazon_product_facts"), dict) else {}
    specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
    data = {**metadata, **facts, **specifics}
    values: list[str] = []
    for key in (
        "brand", "manufacturer", "model", "model_number", "mpn", "product_type",
        "product_group", "size", "color", "colour", "variation", "bundle_quantity",
        "pack_quantity", "number_of_items", "asin", "upc", "ean", "gtin",
    ):
        value = data.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            values.append(f"{key}: {str(value).strip()[:160]}")
    if listing.category_suggestion:
        values.append(f"existing category: {str(listing.category_suggestion)[:160]}")
    return values[:30]


def build_suggestions(db, listing: Listing, *, owner_user_id: int,
                      marketplaces: list[str] | None = None, limit: int = 3) -> dict[str, Any]:
    """Rank current registry candidates without assigning/publishing categories."""
    if int(listing.user_id) != int(owner_user_id):
        raise ValueError("Listing does not belong to this operator")
    identity = _identity(listing)[0]
    specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
    existing_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    existing_maps = existing_data.get("marketplace_category_mappings") if isinstance(existing_data.get("marketplace_category_mappings"), dict) else {}
    evidence = _identity_evidence(listing)
    product_name = str(listing.title or listing.suggested_title or "").strip()
    canonical = str(listing.category_suggestion or product_name).strip()
    selected_marketplaces = list(dict.fromkeys(str(value).lower() for value in (marketplaces or MARKETPLACES)))
    if not selected_marketplaces or any(value not in MARKETPLACES for value in selected_marketplaces):
        raise ValueError("Unsupported marketplace selected for category suggestion")
    ranked = rank_taxonomy_candidates(
        db, canonical_concept=canonical, product_evidence=[product_name, *evidence],
        marketplaces=selected_marketplaces, limit=limit, owner_user_id=owner_user_id,
    )
    suggestions: dict[str, Any] = {}
    for marketplace_result in ranked.get("marketplaces") or []:
        marketplace = str(marketplace_result.get("marketplace") or "").lower()
        if marketplace not in MARKETPLACES:
            continue
        current = existing_maps.get(marketplace) if isinstance(existing_maps.get(marketplace), dict) else {}
        if current.get("verification_status") == "operator_confirmed":
            # The operator's assignment remains authoritative; suggestions are
            # never used to replace or downgrade it.
            suggestions[marketplace] = {
                "state": "confirmed_mapping_preserved", "mapping_id": current.get("mapping_id"),
                "category_id": current.get("category_id"), "category_path": current.get("category_path"),
                "taxonomy_version_id": current.get("taxonomy_version_id"),
            }
            continue
        candidates = []
        for candidate in marketplace_result.get("candidates") or []:
            required, missing = _candidate_attributes(candidate, specifics)
            verified = candidate.get("verification_status") in {"authoritatively_verified", "live_browser_verified"}
            fresh = candidate.get("staleness_status") == "fresh"
            terminal = candidate.get("terminal_status") == "terminal"
            publishable = candidate.get("publishability_status") == "publishable"
            native_id = candidate.get("category_id")
            candidates.append({
                "category_id": native_id,
                "category_key": candidate.get("category_key"),
                "category_path": candidate.get("full_path"),
                "taxonomy_version_id": candidate.get("registry_version_id"),
                "taxonomy_version": candidate.get("taxonomy_version"),
                "taxonomy_source": candidate.get("data_source"),
                "verification_status": candidate.get("verification_status"),
                "staleness_status": candidate.get("staleness_status"),
                "terminal_status": candidate.get("terminal_status"),
                "publishability_status": candidate.get("publishability_status"),
                "retrieval_score": candidate.get("candidate_retrieval_score"),
                # Text retrieval scores are not calibrated match probabilities.
                "confidence_kind": "uncalibrated_retrieval_score",
                "required_attributes": required,
                "missing_attributes": missing,
                "required_attributes_status": "unverified" if required is None else "review_required" if missing else "candidate_fields_present",
                "review_required": True,
                "selectable_for_confirmation": bool(native_id and verified and fresh and terminal and publishable),
                "reference_only": not bool(verified and fresh and terminal and publishable),
                "shipping_compatibility": "unknown",
            })
        suggestions[marketplace] = {
            "state": "review_required" if candidates else "no_verified_taxonomy_candidate",
            "identity_key_hash": hashlib.sha256(identity.encode()).hexdigest(),
            "taxonomy_versions": marketplace_result.get("taxonomy_versions") or [],
            "candidates": candidates,
            "reason": marketplace_result.get("status"),
        }
    return {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "product_identity": identity,
        "marketplaces": suggestions,
        "assignment_state": "suggestions_only_operator_confirmation_required",
        "published_or_queued": False,
    }


def persist_suggestions(listing: Listing, result: dict[str, Any]) -> None:
    data = dict(listing.marketplace_data) if isinstance(listing.marketplace_data, dict) else {}
    prior = data.get("marketplace_category_suggestions") if isinstance(data.get("marketplace_category_suggestions"), dict) else {}
    preserved: dict[str, Any] = {}
    # Preserve an explicit approve/reject decision if a repeated/recovered job
    # generated the same candidate/version again.
    for marketplace, suggestion in (result.get("marketplaces") or {}).items():
        prior_market = prior.get(marketplace) if isinstance(prior.get(marketplace), dict) else {}
        prior_candidates = prior_market.get("candidates") if isinstance(prior_market.get("candidates"), list) else []
        decisions = {
            (str(item.get("taxonomy_version_id")), str(item.get("category_key"))): item.get("operator_decision")
            for item in prior_candidates if isinstance(item, dict) and item.get("operator_decision") in {"approved", "rejected"}
        }
        updated = dict(suggestion) if isinstance(suggestion, dict) else {}
        candidates = updated.get("candidates") if isinstance(updated.get("candidates"), list) else []
        updated["candidates"] = [
            {**candidate, **({"operator_decision": decisions[(str(candidate.get("taxonomy_version_id")), str(candidate.get("category_key")))]}
                if (str(candidate.get("taxonomy_version_id")), str(candidate.get("category_key"))) in decisions else {})}
            for candidate in candidates if isinstance(candidate, dict)
        ]
        preserved[marketplace] = updated
    data["marketplace_category_suggestions"] = {
        **result, "marketplaces": preserved,
    }
    listing.marketplace_data = data
