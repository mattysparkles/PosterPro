"""Candidate-only reconstruction from preserved media.

This service never creates IntakePhoto, IntakePhotoBatch, IntakeSlate, or
CanonicalItem rows.  It stages an auditable manifest first and only creates a
normal *draft* Listing for a selected, directory-bounded recovery group.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from PIL import ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import ListingStatus
from app.models.models import (
    IntakeNotification, Listing, MediaRecoveryItemGroup, MediaRecoveryMedia,
    MediaRecoveryRun, User,
)
from app.services.listing_review import derive_shipping_profile, normalize_listing_images, shipping_policy_for_user
from app.services.listing_ai import build_listing_description
from app.services.marketplace_preflight import MarketplacePreflightService

PIPELINE_VERSION = "media_inventory_recovery_v1"
ITEM_ID_RE = re.compile(r"SP[-_ ]?(\d{8})[-_ ]?(\d{4})", re.I)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
SLATE_FINAL_DISPOSITIONS = {"confirmed_slate", "probable_slate", "specific_unresolved_boundary_review"}
EXCLUDED_FINAL_DISPOSITIONS = SLATE_FINAL_DISPOSITIONS | {"exact_duplicate", "corrupt", "unusable_blur", "non_inventory"}
_GENERIC_TITLE_WORDS = {
    "packaging",
    "package",
    "packages",
    "boxed",
    "box",
    "boxes",
    "label",
    "labels",
    "photo",
    "photos",
    "image",
    "images",
    "view",
    "angle",
    "front",
    "rear",
    "side",
    "top",
    "bottom",
    "screen",
    "slate",
    "duplicate",
    "bundle",
    "bundle",
}
_DESCRIPTOR_WORDS = {"dual", "single", "double", "triple", "quad", "pair", "two", "three", "head", "heads", "headed"}


def _item_id(value: str) -> str | None:
    match = ITEM_ID_RE.search(value or "")
    return f"SP-{match.group(1)}-{match.group(2)}" if match else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _phash(image: Image.Image) -> str:
    # A deterministic dHash without an optional third-party package.
    gray = ImageOps.grayscale(image).resize((9, 8))
    pixels = list(gray.getdata())
    bits = "".join("1" if pixels[row * 9 + col] > pixels[row * 9 + col + 1] else "0" for row in range(8) for col in range(8))
    return f"{int(bits, 2):016x}"


def _normalize_identity_title(value: str | None) -> str | None:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    if not text:
        return None
    tokens = [token for token in text.split() if token not in _GENERIC_TITLE_WORDS]
    normalized: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _DESCRIPTOR_WORDS and index + 1 < len(tokens) and tokens[index + 1] in {"head", "heads", "headed"}:
            index += 2
            continue
        if token in {"head", "heads", "headed"} and normalized and normalized[-1] in _DESCRIPTOR_WORDS:
            index += 1
            continue
        normalized.append(token)
        index += 1
    return " ".join(normalized).strip() or None


def _title_looks_like_bare_identifier(value: str | None) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    if not text:
        return False
    tokens = [token for token in text.split() if token]
    if not tokens:
        return False
    alpha_tokens = [token for token in tokens if any(char.isalpha() for char in token)]
    if not alpha_tokens:
        return True
    if len(tokens) <= 3 and all(len(token) <= 5 for token in tokens):
        return True
    if len(alpha_tokens) == 1 and len(tokens) <= 4 and any(token.isdigit() or re.fullmatch(r"[0-9a-z]+", token) for token in tokens):
        return True
    return False


def _normalize_identity_key(value: str | None) -> str | None:
    text = re.sub(r"[^a-z0-9]+", "", str(value or "").lower()).strip()
    return text or None


def _product_first_description(*, title: str, description: str, package_weight: str | None = None, package_dimensions: dict[str, Any] | None = None) -> str:
    description_text = re.sub(r"^(the\s+)?(image|photo|picture)s?\s+(shows|show)\s+", "", str(description or "").strip(), flags=re.I)
    description_text = re.sub(r"^this\s+(image|photo|picture)\s+(shows|show)\s+", "", description_text, flags=re.I)
    description_text = description_text.strip().rstrip(".")
    package_parts: list[str] = []
    if package_weight:
        package_parts.append(f"Package weight estimate: {package_weight}.")
    if isinstance(package_dimensions, dict) and any(package_dimensions.get(key) for key in ("length", "width", "height")):
        dims = " × ".join(str(package_dimensions.get(key)) for key in ("length", "width", "height") if package_dimensions.get(key))
        if dims:
            package_parts.append(f"Package dimensions estimate: {dims}.")
    review_line = "Review the attached photos for exact condition, completeness, measurements, and compatibility before publishing."
    parts = [
        f"{title}.",
        description_text,
        *package_parts,
        review_line,
    ]
    return " ".join(part for part in parts if part).strip()


def _child_sequence_span(parent: MediaRecoveryItemGroup, child: MediaRecoveryItemGroup) -> tuple[int, int]:
    parent_paths = list(parent.media_paths_json or [])
    index = {path: position for position, path in enumerate(parent_paths)}
    child_indices = [index[path] for path in (child.media_paths_json or []) if path in index]
    if not child_indices:
        return (10**9, 10**9)
    return (min(child_indices), max(child_indices))


def _child_identity_summary(db: Session, child: MediaRecoveryItemGroup) -> dict[str, Any]:
    listing = db.get(Listing, child.draft_listing_id) if child.draft_listing_id else None
    analysis = child.analysis_json or {}
    synthesis = analysis.get("recovery_full_group_evidence_v3")
    proposal = analysis.get("sequence_proposal") if isinstance(analysis.get("sequence_proposal"), dict) else {}
    identity = synthesis.get("identity") if isinstance(synthesis, dict) else {}
    source_metadata = (listing.source_metadata or {}) if listing else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    specifics = (listing.item_specifics or {}) if listing and isinstance(listing.item_specifics, dict) else {}

    title = (
        identity.get("title")
        or proposal.get("title")
        or (listing.title if listing else None)
        or recovery.get("identity_title")
        or recovery.get("title")
    )
    brand = (
        identity.get("brand")
        or proposal.get("brand")
        or specifics.get("Brand")
        or specifics.get("brand")
        or recovery.get("brand")
    )
    model = (
        identity.get("model")
        or proposal.get("model")
        or specifics.get("Model")
        or specifics.get("model")
        or recovery.get("model")
    )
    mpn = (
        identity.get("mpn")
        or proposal.get("mpn")
        or specifics.get("MPN")
        or specifics.get("Manufacturer Part Number")
        or recovery.get("mpn")
    )
    identifier = identity.get("identifier") or proposal.get("identifier") or specifics.get("UPC") or specifics.get("EAN") or specifics.get("GTIN") or recovery.get("identifier")
    normalized_title = _normalize_identity_title(str(title) if title else None)
    bare_identifier_title = _title_looks_like_bare_identifier(str(title) if title else None)
    barcode_key = _normalize_identity_key(str(identifier) if identifier else None)
    model_key = _normalize_identity_key(" ".join(part for part in (brand, model or mpn) if part))
    if barcode_key:
        key_kind, key = "barcode", barcode_key
    elif model_key:
        key_kind, key = "model", model_key
    elif normalized_title:
        key_kind, key = "title", normalized_title
    else:
        key_kind, key = None, None
    return {
        "listing_id": listing.id if listing else None,
        "title": title,
        "normalized_title": normalized_title,
        "title_tokens": [token for token in re.split(r"[^a-z0-9]+", str(title or "").lower()) if token],
        "brand": brand,
        "model": model,
        "mpn": mpn,
        "identifier": identifier,
        "key_kind": key_kind,
        "key": key,
        "generic_title": normalized_title is None or bare_identifier_title,
        "bare_identifier_title": bare_identifier_title,
        "span": None,
    }


def _child_specificity_score(summary: dict[str, Any]) -> float:
    score = 0.0
    title = str(summary.get("title") or "").strip()
    normalized_title = str(summary.get("normalized_title") or "").strip()
    title_tokens = summary.get("title_tokens") if isinstance(summary.get("title_tokens"), list) else []
    key_kind = str(summary.get("key_kind") or "").strip().lower()
    brand = str(summary.get("brand") or "").strip()
    model = str(summary.get("model") or "").strip()
    mpn = str(summary.get("mpn") or "").strip()
    identifier = str(summary.get("identifier") or "").strip()

    if title and normalized_title:
        score += min(len(title_tokens), 8) * 0.6
    if normalized_title and not summary.get("generic_title"):
        score += 3.0
    if title and any(token in normalized_title for token in ("label", "packaging", "box", "package")):
        score -= 2.0
    if key_kind == "barcode":
        score += 1.5
    elif key_kind == "model":
        score += 2.5
    elif key_kind == "title":
        score += 1.2
    if brand and brand.lower() not in {"unknown", "n/a", "na"}:
        score += 1.0
    if model and model.lower() not in {"unknown", "n/a", "na", "not specified", "not visible"}:
        score += 1.2
    if mpn and mpn.lower() not in {"unknown", "n/a", "na", "not specified", "not visible"}:
        score += 1.0
    if identifier and identifier.lower() not in {"unknown", "n/a", "na", "not specified", "not visible"}:
        score += 0.9
    if title and re.fullmatch(r"[0-9a-z][0-9a-z\-\s_/]{2,}", title.lower()) and not any(token.isalpha() for token in title_tokens):
        score -= 2.5
    if summary.get("generic_title"):
        score -= 2.0
    return score


def _merge_child_group_analysis(
    db: Session,
    *,
    parent: MediaRecoveryItemGroup | None,
    winner: MediaRecoveryItemGroup,
    losers: list[MediaRecoveryItemGroup],
    reason: str,
) -> None:
    winner_analysis = dict(winner.analysis_json or {})
    merge_record = {
        "merged_child_group_ids": [group.id for group in losers],
        "merged_child_recovery_item_ids": [group.recovery_item_id for group in losers],
        "merge_reason": reason,
    }
    winner_merge = dict(winner_analysis.get("sequence_consolidation_v1") or {})
    winner_merge.setdefault("merged_from", [])
    for group in losers:
        winner_merge["merged_from"].append({
            "child_group_id": group.id,
            "recovery_item_id": group.recovery_item_id,
            "draft_listing_id": group.draft_listing_id,
            "grouping_status": group.grouping_status,
            "grouping_confidence": group.grouping_confidence,
        })
    winner_merge["merge_reason"] = reason
    winner_analysis["sequence_consolidation_v1"] = winner_merge
    winner.analysis_json = winner_analysis

    combined_paths = list(dict.fromkeys([*(winner.media_paths_json or []), *[path for loser in losers for path in (loser.media_paths_json or [])]]))
    winner.media_paths_json = combined_paths
    winner.grouping_status = "confirmed" if any(group.grouping_status == "confirmed" for group in [winner, *losers]) else winner.grouping_status
    winner.grouping_confidence = max([float(value) for value in [winner.grouping_confidence, *[loser.grouping_confidence for loser in losers]] if value is not None] or [winner.grouping_confidence or 0.0])
    winner.evidence_json = {
        **(winner.evidence_json or {}),
        "merged_child_group_ids": [group.id for group in losers],
        "merged_child_recovery_item_ids": [group.recovery_item_id for group in losers],
        "merge_reason": reason,
        "merge_source_parent_group_ids": sorted({group.parent_group_id for group in [winner, *losers] if group.parent_group_id is not None} | ({parent.id} if parent else set())),
    }
    source_parent_ids = sorted({group.parent_group_id for group in [winner, *losers] if group.parent_group_id is not None} | ({parent.id} if parent else set()))

    winner_listing = db.get(Listing, winner.draft_listing_id) if winner.draft_listing_id else None
    if winner_listing:
        source = dict(winner_listing.source_metadata or {})
        recovery = dict(source.get("recovery") or {})
        recovery["sequence_consolidation_v1"] = merge_record
        source["recovery"] = recovery
        winner_listing.source_metadata = source

    for loser in losers:
        loser.grouping_status = "superseded"
        loser.grouping_confidence = min(float(loser.grouping_confidence or 0.0), 0.35)
        loser.evidence_json = {
            **(loser.evidence_json or {}),
            "merged_into_child_group_id": winner.id,
            "merged_into_recovery_item_id": winner.recovery_item_id,
            "merge_reason": reason,
            "merge_source_parent_group_ids": source_parent_ids,
        }
        loser.analysis_json = {
            **(loser.analysis_json or {}),
            "sequence_consolidation_v1": {
                "merged_into_child_group_id": winner.id,
                "merged_into_recovery_item_id": winner.recovery_item_id,
                "draft_listing_id": winner.draft_listing_id,
                "merge_reason": reason,
                "merge_source_parent_group_ids": source_parent_ids,
            },
        }
        if loser.draft_listing_id and loser.draft_listing_id != winner.draft_listing_id:
            loser_listing = db.get(Listing, loser.draft_listing_id)
            if loser_listing:
                source = dict(loser_listing.source_metadata or {})
                recovery = dict(source.get("recovery") or {})
                recovery["merged_into_recovery_group_id"] = winner.id
                recovery["merged_into_recovery_item_id"] = winner.recovery_item_id
                recovery["merge_reason"] = reason
                source["recovery"] = recovery
                loser_listing.source_metadata = source


def consolidate_sibling_children_by_evidence(db: Session, *, parent: MediaRecoveryItemGroup) -> dict[str, int]:
    children = db.execute(
        select(MediaRecoveryItemGroup).where(
            MediaRecoveryItemGroup.parent_group_id == parent.id,
            MediaRecoveryItemGroup.grouping_status != "superseded",
        ).order_by(MediaRecoveryItemGroup.id)
    ).scalars().all()
    if len(children) < 2:
        return {"merged_clusters": 0, "merged_groups": 0}

    summaries = []
    for child in children:
        summary = _child_identity_summary(db, child)
        summary["child"] = child
        summary["span"] = _child_sequence_span(parent, child)
        summaries.append(summary)
    summaries.sort(key=lambda item: (item["span"][0], item["span"][1], item["child"].id))

    clusters: list[list[dict[str, Any]]] = []
    for summary in summaries:
        if not clusters:
            clusters.append([summary])
            continue
        previous = clusters[-1]
        leader = previous[-1]
        same_key = summary["key"] is not None and summary["key"] == leader["key"] and summary["key_kind"] == leader["key_kind"]
        same_normalized_title = summary["normalized_title"] and leader["normalized_title"] and summary["normalized_title"] == leader["normalized_title"]
        adjacent = summary["span"][0] <= leader["span"][1] + 1
        one_generic = summary["generic_title"] or leader["generic_title"]
        if same_key or same_normalized_title or (adjacent and one_generic):
            previous.append(summary)
        else:
            clusters.append([summary])

    merged_clusters = merged_groups = 0
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        strong = [item for item in cluster if item["key"] is not None]
        if not strong and not all(item["normalized_title"] for item in cluster):
            continue
        winner = max(
            (item["child"] for item in cluster),
            key=lambda child: (
                _child_specificity_score(next(item for item in cluster if item["child"].id == child.id)),
                bool(child.draft_listing_id),
                float(child.grouping_confidence or 0.0),
                len(child.media_paths_json or []),
                -child.id,
            ),
        )
        losers = [item["child"] for item in cluster if item["child"].id != winner.id]
        reason = "shared_identity_evidence" if strong else "normalized_title_match"
        _merge_child_group_analysis(db, parent=parent, winner=winner, losers=losers, reason=reason)
        merged_clusters += 1
        merged_groups += len(losers)

    if merged_groups:
        parent.analysis_json = {
            **(parent.analysis_json or {}),
            "sequence_consolidation_v1": {
                "merged_clusters": merged_clusters,
                "merged_groups": merged_groups,
            },
        }
        db.flush()
    return {"merged_clusters": merged_clusters, "merged_groups": merged_groups}


def consolidate_boundary_parents_by_evidence(db: Session, *, run: MediaRecoveryRun) -> dict[str, int]:
    parents = db.execute(
        select(MediaRecoveryItemGroup).where(
            MediaRecoveryItemGroup.run_id == run.id,
            MediaRecoveryItemGroup.parent_group_id.is_(None),
        ).order_by(MediaRecoveryItemGroup.id)
    ).scalars().all()
    if len(parents) < 2:
        return {"merged_clusters": 0, "merged_groups": 0}

    summaries: list[dict[str, Any]] = []
    for parent in parents:
        children = db.execute(
            select(MediaRecoveryItemGroup).where(
                MediaRecoveryItemGroup.parent_group_id == parent.id,
                MediaRecoveryItemGroup.grouping_status != "superseded",
            ).order_by(MediaRecoveryItemGroup.id)
        ).scalars().all()
        if not children:
            continue
        child_summaries = []
        for child in children:
            summary = _child_identity_summary(db, child)
            summary["child"] = child
            summary["span"] = _child_sequence_span(parent, child)
            child_summaries.append(summary)
        child_summaries.sort(key=lambda item: (item["span"][0], item["span"][1], item["child"].id))
        summaries.append({"parent": parent, "first": child_summaries[0], "last": child_summaries[-1]})

    merged_clusters = merged_groups = 0
    for left, right in zip(summaries, summaries[1:]):
        left_last = left["last"]
        right_first = right["first"]
        if left_last["key"] is None and right_first["key"] is None:
            continue
        same_key = left_last["key"] is not None and left_last["key"] == right_first["key"] and left_last["key_kind"] == right_first["key_kind"]
        same_normalized_title = left_last["normalized_title"] and right_first["normalized_title"] and left_last["normalized_title"] == right_first["normalized_title"]
        one_generic = left_last["generic_title"] or right_first["generic_title"]
        if not (same_key or (same_normalized_title and not one_generic)):
            continue
        winner = max(
            (left_last["child"], right_first["child"]),
            key=lambda child: (
                _child_specificity_score(left_last if child.id == left_last["child"].id else right_first),
                bool(child.draft_listing_id),
                float(child.grouping_confidence or 0.0),
                len(child.media_paths_json or []),
                -child.id,
            ),
        )
        losers = [child for child in (left_last["child"], right_first["child"]) if child.id != winner.id]
        reason = "boundary_shared_identity_evidence" if same_key else "boundary_title_alignment"
        _merge_child_group_analysis(db, parent=None, winner=winner, losers=losers, reason=reason)
        merged_clusters += 1
        merged_groups += len(losers)
        # If a boundary child was the only active child under its parent, the
        # parent itself becomes superseded so the interval no longer advertises
        # a false split.
        for parent_summary, child in ((left, left_last["child"]), (right, right_first["child"])):
            parent = parent_summary["parent"]
            active_children = db.execute(
                select(MediaRecoveryItemGroup).where(
                    MediaRecoveryItemGroup.parent_group_id == parent.id,
                    MediaRecoveryItemGroup.grouping_status != "superseded",
                )
            ).scalars().all()
            if not active_children:
                parent.grouping_status = "superseded"
                parent.analysis_json = {
                    **(parent.analysis_json or {}),
                    "sequence_consolidation_v1": {
                        "merged_into_neighboring_parent": True,
                        "merge_reason": reason,
                    },
                }
    if merged_groups:
        db.flush()
    return {"merged_clusters": merged_clusters, "merged_groups": merged_groups}


class MediaRecoveryService:
    @staticmethod
    def _draft_creation_allowed(run: MediaRecoveryRun | None) -> bool:
        return bool(run and run.draft_creation_state in {"enabled", "validation_sample_only"})

    def build_sequence_contact_sheet(self, paths: list[str]) -> str:
        """Create a temporary, labelled visual sequence; originals remain untouched."""
        tile_width, tile_height, columns = 280, 230, 4
        rows = max(1, (len(paths) + columns - 1) // columns)
        sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "white")
        draw = ImageDraw.Draw(sheet)
        for index, raw_path in enumerate(paths):
            try:
                with Image.open(raw_path) as source:
                    image = ImageOps.exif_transpose(source).convert("RGB")
                    image.thumbnail((tile_width - 12, tile_height - 34))
                    x = (index % columns) * tile_width + (tile_width - image.width) // 2
                    y = (index // columns) * tile_height + 25
                    sheet.paste(image, (x, y))
                    draw.text(((index % columns) * tile_width + 6, (index // columns) * tile_height + 5), f"[{index}]", fill="black")
            except Exception:
                draw.text(((index % columns) * tile_width + 6, (index // columns) * tile_height + 5), f"[{index}] unreadable", fill="red")
        target = Path("/tmp") / f"posterpro-recovery-sequence-{hashlib.sha256('|'.join(paths).encode()).hexdigest()[:16]}.jpg"
        sheet.save(target, quality=88)
        return str(target)

    @staticmethod
    def _proposal_facts(proposal: dict[str, Any], recovery_item_id: str) -> dict[str, Any]:
        title = str(proposal.get("title") or "Photographed inventory item requiring identity review").strip()[:80]
        description = str(proposal.get("description") or "Recovered from a bounded chronological photo sequence; review the attached original images before publishing.").strip()
        value = proposal.get("estimated_value")
        try:
            price = round(float(value), 2) if float(value) > 0 else 19.99
        except (TypeError, ValueError):
            price = 19.99
        specifics = proposal.get("specifics") if isinstance(proposal.get("specifics"), dict) else {}
        tags = proposal.get("tags") if isinstance(proposal.get("tags"), list) else []
        confidence = MediaRecoveryService._confidence(proposal.get("confidence"), default=0.35)
        return {
            "title": title, "product_name": title, "category": str(proposal.get("category") or "General resale > Identity review required"),
            "specifics": {**{str(key).title(): str(value) for key, value in specifics.items() if value}, "Recovery SKU": recovery_item_id},
            "keywords": [str(tag) for tag in tags if str(tag).strip()] or ["recovered inventory", "review required"],
            "description": description, "included": "Components visible in attached original photos", "condition": str(proposal.get("condition") or "Used")[:64],
            "condition_notes": "Condition, operation, completeness, measurements, and compatibility require operator photo review.",
            "suggested_price": price, "quick_sale_price": round(price * .8, 2), "price_range": f"${round(price*.75,2)}–${round(price*1.25,2)}",
            "pricing_explanation": "Conservative image-evidence estimate; confirm against sold comparables before publish.",
            "shipping": "Measure and pack before publish; provisional ground-service recommendation.", "shipping_weight": "3 lb (estimated)",
            "package_dimensions": {"length": 12, "width": 10, "height": 8}, "confidence": confidence,
            "field_confidence": {"grouping": confidence, "identity": confidence, "condition": 0.35, "price": 0.3},
            "warnings": ["Review sequence boundary, condition, dimensions, and completeness before publish."], "alternatives": [],
        }

    @staticmethod
    def _confidence(value: Any, *, default: float) -> float:
        labels = {"high": 0.85, "medium": 0.6, "low": 0.35}
        try:
            parsed = labels.get(str(value).strip().lower(), float(value))
        except (TypeError, ValueError):
            parsed = default
        return max(0.0, min(1.0, float(parsed)))

    def split_review_group(self, db: Session, *, run: MediaRecoveryRun, user: User, parent: MediaRecoveryItemGroup, proposal: dict[str, Any]) -> dict[str, int]:
        """Persist a validated sequence split and draft each defensible child."""
        if not self._draft_creation_allowed(run):
            raise RuntimeError("Recovery grouping is frozen pending the full-group quality audit")
        paths = list(parent.media_paths_json or [])
        groups = proposal.get("groups") if isinstance(proposal.get("groups"), list) else []
        slate_indices = {int(value) for value in (proposal.get("slate_indices") or []) if isinstance(value, int) or str(value).isdigit()}
        covered: set[int] = set()
        created = drafted = unresolved = 0
        for ordinal, raw in enumerate(groups, start=1):
            if not isinstance(raw, dict):
                continue
            try:
                start, end = int(raw.get("start")), int(raw.get("end"))
            except (TypeError, ValueError):
                continue
            start, end = max(0, start), min(len(paths) - 1, end)
            indices = [index for index in range(start, end + 1) if index not in slate_indices and index not in covered]
            if not indices:
                continue
            covered.update(indices)
            child_id = f"{parent.recovery_item_id}-I{ordinal:03d}"
            confidence = self._confidence(raw.get("confidence"), default=0.2)
            review_required = bool(raw.get("review_required")) or confidence < 0.5
            status = "confirmed" if confidence >= .8 and not review_required else ("probable" if confidence >= .5 else "needs_grouping_review")
            child = db.execute(select(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.run_id == run.id, MediaRecoveryItemGroup.recovery_item_id == child_id)).scalar_one_or_none()
            if child is None:
                child = MediaRecoveryItemGroup(run_id=run.id, parent_group_id=parent.id, recovery_item_id=child_id, grouping_status=status)
                db.add(child)
                created += 1
            child.grouping_status, child.grouping_confidence = status, confidence
            child.media_paths_json = [paths[index] for index in indices]
            child.evidence_json = {"parent_group_id": parent.id, "sequence_range": [start, end], "boundary_reason": raw.get("boundary_reason"), "unresolved": review_required}
            child.analysis_json = {"sequence_proposal": raw}
            for path in child.media_paths_json:
                media = db.execute(select(MediaRecoveryMedia).where(MediaRecoveryMedia.run_id == run.id, MediaRecoveryMedia.absolute_path == path)).scalar_one_or_none()
                if media:
                    media.assigned_recovery_item_id = child_id
                    media.processing_state = "split_grouped"
                    media.final_disposition = "assigned_to_item" if status != "needs_grouping_review" else "specific_unresolved_boundary_review"
            if status != "needs_grouping_review" and self._draft_creation_allowed(run):
                listing = self.create_draft(db, user=user, group=child, facts=self._proposal_facts(raw, child_id))
                drafted += 1 if listing else 0
            else:
                unresolved += 1
        # Any malformed or omitted range becomes its own small explicit review child.
        remaining = [index for index in range(len(paths)) if index not in covered and index not in slate_indices]
        for offset in range(0, len(remaining), 8):
            indices = remaining[offset:offset + 8]
            if not indices:
                continue
            child_id = f"{parent.recovery_item_id}-U{offset // 8 + 1:03d}"
            child = db.execute(select(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.run_id == run.id, MediaRecoveryItemGroup.recovery_item_id == child_id)).scalar_one_or_none()
            if child is None:
                child = MediaRecoveryItemGroup(run_id=run.id, parent_group_id=parent.id, recovery_item_id=child_id, grouping_status="needs_grouping_review")
                db.add(child)
                created += 1
            child.grouping_status, child.grouping_confidence = "needs_grouping_review", 0.15
            child.media_paths_json = [paths[index] for index in indices]
            child.evidence_json = {"parent_group_id": parent.id, "reason": "sequence analyzer did not resolve this exact bounded range", "sequence_range": [indices[0], indices[-1]]}
            for path in child.media_paths_json:
                media = db.execute(select(MediaRecoveryMedia).where(MediaRecoveryMedia.run_id == run.id, MediaRecoveryMedia.absolute_path == path)).scalar_one_or_none()
                if media:
                    media.assigned_recovery_item_id, media.processing_state, media.final_disposition = child_id, "split_review", "specific_unresolved_boundary_review"
            unresolved += 1
        for index in slate_indices:
            if 0 <= index < len(paths):
                media = db.execute(select(MediaRecoveryMedia).where(MediaRecoveryMedia.run_id == run.id, MediaRecoveryMedia.absolute_path == paths[index])).scalar_one_or_none()
                if media:
                    media.processing_state, media.final_disposition, media.assigned_recovery_item_id = "slate_analyzed", "probable_slate", None
        merged = {
            **consolidate_sibling_children_by_evidence(db, parent=parent),
            **consolidate_boundary_parents_by_evidence(db, run=run),
        }
        parent.grouping_status = "superseded"
        parent.analysis_json = {
            "sequence_split_v1": proposal,
            "sequence_consolidation_v1": merged,
        }
        db.commit()
        return {"children": created, "drafts": drafted, "unresolved": unresolved}
    def manifest(self, db: Session, *, user: User, roots: list[Path], run_key: str) -> MediaRecoveryRun:
        run = db.execute(select(MediaRecoveryRun).where(MediaRecoveryRun.run_key == run_key)).scalar_one_or_none()
        if run is None:
            run = MediaRecoveryRun(user_id=user.id, run_key=run_key, pipeline_version=PIPELINE_VERSION, source_roots_json=[str(root) for root in roots])
            db.add(run)
            db.flush()
        existing = {row.absolute_path: row for row in db.execute(select(MediaRecoveryMedia).where(MediaRecoveryMedia.run_id == run.id)).scalars()}
        digest_owner: dict[str, int] = {row.sha256: row.id for row in existing.values()}
        count = 0
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file() and candidate.suffix.lower() in IMAGE_SUFFIXES):
                absolute = str(path.resolve())
                count += 1
                stat = path.stat()
                digest = _sha256(path)
                try:
                    with Image.open(path) as image:
                        exif = image.getexif()
                        capture = exif.get(36867) or exif.get(306)
                        metadata = {
                            "filename": path.name, "extension": path.suffix.lower(), "size_bytes": stat.st_size,
                            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                            "width": image.width, "height": image.height, "orientation": exif.get(274),
                            "exif_capture_time": str(capture) if capture else None, "exif_subsecond": exif.get(37521),
                            "source_photo_token": path.stem.rsplit("_", 1)[-1], "possible_item_id": _item_id(str(path)),
                            "item_directory_id": next((_item_id(part) for part in path.parts if _item_id(part)), None),
                            "likely_kind": "thumbnail" if min(image.size) < 100 else ("slate" if "slate" in path.name.lower() else "original"),
                            "readable": True,
                        }
                        perceptual_hash = _phash(image)
                except Exception as exc:
                    metadata = {"filename": path.name, "extension": path.suffix.lower(), "size_bytes": stat.st_size, "readable": False, "read_error": type(exc).__name__}
                    perceptual_hash = None
                row = existing.get(absolute)
                if row is None:
                    row = MediaRecoveryMedia(run_id=run.id, absolute_path=absolute, relative_path=str(path.relative_to(root)), sha256=digest)
                    db.add(row)
                    db.flush()
                row.sha256, row.perceptual_hash, row.file_metadata_json = digest, perceptual_hash, metadata
                row.duplicate_of_media_id = digest_owner.get(digest) if digest_owner.get(digest) != row.id else None
                digest_owner.setdefault(digest, row.id)
        run.imported_media_count = count
        run.processing_status = "manifested"
        db.commit()
        return run

    def group_item_directories(self, db: Session, *, run: MediaRecoveryRun) -> list[MediaRecoveryItemGroup]:
        media = db.execute(select(MediaRecoveryMedia).where(MediaRecoveryMedia.run_id == run.id)).scalars().all()
        grouped: dict[str, list[MediaRecoveryMedia]] = defaultdict(list)
        for row in media:
            item = _item_id(str((row.file_metadata_json or {}).get("item_directory_id") or ""))
            if item:
                grouped[item].append(row)
        groups: list[MediaRecoveryItemGroup] = []
        for item, rows in sorted(grouped.items()):
            usable = [row for row in rows if (row.file_metadata_json or {}).get("readable") and not row.duplicate_of_media_id and (row.file_metadata_json or {}).get("likely_kind") != "thumbnail"]
            status = "confirmed" if len(usable) >= 2 and len(usable) <= 40 else "needs_grouping_review"
            existing = db.execute(select(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.run_id == run.id, MediaRecoveryItemGroup.recovery_item_id == item)).scalar_one_or_none()
            group = existing or MediaRecoveryItemGroup(run_id=run.id, recovery_item_id=item, grouping_status=status)
            group.grouping_status = status
            group.grouping_confidence = 0.96 if status == "confirmed" else 0.55
            group.media_paths_json = [row.absolute_path for row in usable]
            group.evidence_json = {"sources": ["item_specific_directory", "filename_item_id"], "total_media": len(rows), "unique_usable_media": len(usable), "open_group_safeguard": len(usable) > 40}
            if not existing:
                db.add(group)
            groups.append(group)
            for row in rows:
                row.processing_state = "grouped"
                row.final_disposition = "assigned_to_item" if not row.duplicate_of_media_id else "exact_duplicate"
                row.assigned_recovery_item_id = item
        # Nothing disappears simply because it did not survive in an
        # item-specific folder.  Preserve unassigned photos as small,
        # actionable chronological review intervals instead of one open group.
        assigned_hashes = {row.sha256 for rows in grouped.values() for row in rows}
        unassigned = [row for row in media if not _item_id(str((row.file_metadata_json or {}).get("item_directory_id") or ""))]
        unassigned.sort(key=lambda row: ((row.file_metadata_json or {}).get("exif_capture_time") or (row.file_metadata_json or {}).get("modified_at") or "", row.absolute_path))
        for offset in range(0, len(unassigned), 40):
            rows = unassigned[offset:offset + 40]
            key = f"RECOVERY-REVIEW-{offset // 40 + 1:04d}"
            existing = db.execute(select(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.run_id == run.id, MediaRecoveryItemGroup.recovery_item_id == key)).scalar_one_or_none()
            group = existing or MediaRecoveryItemGroup(run_id=run.id, recovery_item_id=key, grouping_status="needs_grouping_review")
            group.grouping_status = "needs_grouping_review"
            group.grouping_confidence = 0.2
            group.media_paths_json = [row.absolute_path for row in rows]
            group.evidence_json = {"sources": ["preserved_google_photos"], "reason": "No independent item-directory boundary; bounded to 40 images to prevent open-ended grouping.", "potential_exact_duplicate_count": sum(1 for row in rows if row.sha256 in assigned_hashes)}
            if not existing:
                db.add(group)
                groups.append(group)
            for row in rows:
                row.processing_state = "review_ready"
                row.final_disposition = "exact_duplicate" if row.sha256 in assigned_hashes or row.duplicate_of_media_id else "grouping_review_required"
                row.assigned_recovery_item_id = None
        db.flush()
        run.group_count = len(db.execute(select(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.run_id == run.id)).scalars().all())
        run.processing_status = "grouped"
        db.commit()
        return groups

    def _usable_listing_media_paths(self, db: Session, *, group: MediaRecoveryItemGroup) -> list[Path]:
        raw_paths = [Path(path) for path in (group.media_paths_json or []) if isinstance(path, str) and path.strip()]
        if not raw_paths:
            return []
        media_rows = db.execute(
            select(MediaRecoveryMedia).where(
                MediaRecoveryMedia.run_id == group.run_id,
                MediaRecoveryMedia.absolute_path.in_([str(path) for path in raw_paths]),
            )
        ).scalars().all()
        by_path = {row.absolute_path: row for row in media_rows}
        usable: list[Path] = []
        for path in raw_paths:
            row = by_path.get(str(path))
            if row is not None:
                disposition = str(row.final_disposition or "").strip().lower()
                likely_kind = str((row.file_metadata_json or {}).get("likely_kind") or "").strip().lower()
                detection_result = str((row.file_metadata_json or {}).get("slate_detection_result") or "").strip().lower()
                if disposition in EXCLUDED_FINAL_DISPOSITIONS or likely_kind == "slate" or detection_result in {"probable_slate_candidate", "matched"}:
                    continue
            elif "slate" in path.name.lower():
                continue
            if path.exists():
                usable.append(path)
        return usable[:12]

    def create_draft(self, db: Session, *, user: User, group: MediaRecoveryItemGroup, facts: dict[str, Any]) -> Listing:
        run = db.get(MediaRecoveryRun, group.run_id)
        if not self._draft_creation_allowed(run):
            raise RuntimeError("This recovery run is frozen pending quality audit; normal listing creation is unaffected")
        existing = db.get(Listing, group.draft_listing_id) if group.draft_listing_id else None
        if existing:
            return existing
        paths = self._usable_listing_media_paths(db, group=group)
        if not paths:
            raise RuntimeError("No usable product photos remain after slate and duplicate filtering")
        title = facts["title"][:80]
        title_lower = title.lower()
        description_lower = str(facts.get("description") or "").lower()
        if "posterpro slate" in title_lower or "posterpro slate" in description_lower:
            raise RuntimeError("Slate preview assets cannot be turned into a product listing")
        images = normalize_listing_images(
            listing_images=[{"storage_path": f"/media/{path.relative_to(Path('/opt/apps/posterpro/repo/backend/storage')).as_posix()}", "source_platform": "recovered_media", "label": "Recovered original photo", "metadata": {"recovery_path": str(path)}} for path in paths],
            approved=True,
        )
        item_id = group.recovery_item_id
        description = build_listing_description(
            title=title,
            item_specifics=facts.get("specifics") if isinstance(facts.get("specifics"), dict) else None,
            included=str(facts.get("included") or "").strip() or None,
            condition_notes=str(facts.get("condition_notes") or "").strip() or None,
            photo_notes=[str(note) for note in (facts.get("warnings") or []) if str(note).strip()],
        )
        package_weight = str(facts.get("shipping_weight") or "").strip() or None
        package_dimensions = facts.get("package_dimensions") if isinstance(facts.get("package_dimensions"), dict) else None
        shipping_profile = derive_shipping_profile(
            listing={"title": title, "description": description, "listing_price": facts["suggested_price"]},
            existing={
                "package_weight": package_weight,
                "package_dimensions": package_dimensions or {},
            },
            policy=shipping_policy_for_user(user),
        )
        shipping_profile.update(
            {
                "shipping_recommendation": facts["shipping"],
                "package_weight": package_weight,
                "package_dimensions": package_dimensions,
                "estimated": True,
            }
        )
        listing = Listing(
            user_id=user.id, status=ListingStatus.draft, title=title, description=description,
            category_suggestion=facts["category"], item_specifics=facts["specifics"], tags=facts["keywords"],
            condition=facts["condition"], quantity=1, estimated_value=facts["suggested_price"],
            suggested_price=facts["suggested_price"], listing_price=facts["suggested_price"], buy_it_now_price=facts["suggested_price"],
            image_urls=[image["storage_path"] for image in images], listing_images=images, raw_photo_path=str(paths[0]) if paths else None,
            storage_unit_name=facts.get("location"), source_type="media_inventory_recovery", needs_review=True,
            condition_data={"item_condition_notes": facts["condition_notes"], "operator_review_required": True, "confidence": facts["confidence"]},
            shipping_profile=shipping_profile,
            source_metadata={"recovery": {"pipeline_version": PIPELINE_VERSION, "item_id": item_id, "grouping_confidence": group.grouping_confidence, "grouping_status": group.grouping_status, "research_sources": ["direct product packaging and preserved item media"], "alternative_identities": facts.get("alternatives", []), "field_confidence": facts["field_confidence"], "estimated_field_warnings": facts["warnings"], "quick_sale_price": facts["quick_sale_price"], "expected_price_range": facts["price_range"], "pricing_explanation": facts["pricing_explanation"], "box_id": facts.get("box_id"), "evidence": "item-specific recovery directory and direct product packaging/photos"}},
            marketplace_data={"pricing_analysis": {"price_confidence": facts["confidence"], "quick_sale_price": facts["quick_sale_price"], "expected_price_range": facts["price_range"], "explanation": facts["pricing_explanation"]}},
        )
        db.add(listing)
        db.flush()
        # Marketplace preflight includes a datetime audit field.  Recovery
        # metadata is JSON, so retain the complete result in a portable form.
        preflight = json.loads(json.dumps(MarketplacePreflightService().preflight_listing(db, listing, "ebay"), default=str))
        listing.marketplace_data = {**(listing.marketplace_data or {}), "ebay_preflight": preflight, "ready_for_ebay_review": preflight["status"] in {"ready", "ready_with_warnings"}, "needs_ebay_review": True}
        group.analysis_json = facts
        group.draft_listing_id = listing.id
        db.commit()
        return listing

    def finalize(self, db: Session, *, run: MediaRecoveryRun, draft_count: int) -> None:
        run.draft_count = draft_count
        run.processing_status = "completed"
        run.result_json = {"completed_at": datetime.now(timezone.utc).isoformat(), "draft_count": draft_count, "publication_actions": 0}
        db.add(IntakeNotification(user_id=run.user_id, notification_type="media_recovery_drafts_ready", title=f"{draft_count} recovered inventory drafts are ready for review.", message="Recovered drafts are candidate-only and have not been published.", href="/listings", metadata_json={"run_key": run.run_key, "draft_count": draft_count}))
        db.commit()
