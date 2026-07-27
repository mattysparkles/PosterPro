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
from app.services.listing_review import normalize_listing_images
from app.services.marketplace_preflight import MarketplacePreflightService

PIPELINE_VERSION = "media_inventory_recovery_v1"
ITEM_ID_RE = re.compile(r"SP[-_ ]?(\d{8})[-_ ]?(\d{4})", re.I)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


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


class MediaRecoveryService:
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
        if run.draft_creation_state != "enabled":
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
            if status != "needs_grouping_review" and run.draft_creation_state == "enabled":
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
        parent.grouping_status = "superseded"
        parent.analysis_json = {"sequence_split_v1": proposal}
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

    def create_draft(self, db: Session, *, user: User, group: MediaRecoveryItemGroup, facts: dict[str, Any]) -> Listing:
        run = db.get(MediaRecoveryRun, group.run_id)
        if not run or run.draft_creation_state != "enabled":
            raise RuntimeError("This recovery run is frozen pending quality audit; normal listing creation is unaffected")
        existing = db.get(Listing, group.draft_listing_id) if group.draft_listing_id else None
        if existing:
            return existing
        paths = [Path(path) for path in (group.media_paths_json or []) if Path(path).exists()][:12]
        images = normalize_listing_images(
            listing_images=[{"storage_path": f"/media/{path.relative_to(Path('/opt/apps/posterpro/repo/backend/storage')).as_posix()}", "source_platform": "recovered_media", "label": "Recovered original photo", "metadata": {"recovery_path": str(path)}} for path in paths],
            approved=True,
        )
        title = facts["title"][:80]
        item_id = group.recovery_item_id
        description = (
            f"{facts['product_name']} recovered from preserved inventory media. {facts['description']}\n\n"
            f"Included: {facts['included']}. Condition: {facts['condition_notes']}\n\n"
            "Please review all photographs for exact condition, contents, measurements, and compatibility before publishing. "
            "This is an editable recovery draft; no marketplace listing has been created."
        )
        listing = Listing(
            user_id=user.id, status=ListingStatus.draft, title=title, description=description,
            category_suggestion=facts["category"], item_specifics=facts["specifics"], tags=facts["keywords"],
            condition=facts["condition"], quantity=1, estimated_value=facts["suggested_price"],
            suggested_price=facts["suggested_price"], listing_price=facts["suggested_price"], buy_it_now_price=facts["suggested_price"],
            image_urls=[image["storage_path"] for image in images], listing_images=images, raw_photo_path=str(paths[0]) if paths else None,
            storage_unit_name=facts.get("location"), source_type="media_inventory_recovery", needs_review=True,
            condition_data={"item_condition_notes": facts["condition_notes"], "operator_review_required": True, "confidence": facts["confidence"]},
            shipping_profile={"shipping_recommendation": facts["shipping"], "package_weight": facts["shipping_weight"], "package_dimensions": facts["package_dimensions"], "estimated": True},
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
