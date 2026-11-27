"""Attach verified-source product imagery as reviewable catalog references.

This service deliberately does not grant image rights or seller-photo status.
It is shared by manual/source-discovery recovery callers and is idempotent by
listing, source URL, and content hash.
"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Image, Listing, VineImageRecoveryJob
from app.services.image_quality import PRIME_LOGO_SHA256, validate_image_bytes
from app.services.storage import LocalStorage


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (re.sub(r"-{2,}", "-", result)[:96].strip("-") or "product-image")


def attach_catalog_reference_candidate(
    db: Session,
    *,
    listing_id: int,
    data: bytes,
    source_url: str,
    source_page_url: str,
    source_title: str,
    source_platform: str,
    identity_state: str,
    identity_evidence: dict,
    storage: LocalStorage | None = None,
) -> dict:
    """Persist and attach one non-seller image candidate without approving it.

    Callers must provide item-specific identity evidence. Rights always remain
    unverified until explicitly reviewed. Existing gallery images are preserved
    and the new candidate is appended; a good or approved primary is untouched.
    """
    listing = db.execute(select(Listing).where(Listing.id == int(listing_id))).scalar_one()
    if listing.source_type != "amazon_vine":
        raise ValueError("listing_not_amazon_vine")
    metadata = dict(listing.source_metadata or {})
    asin = str(metadata.get("asin") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", asin):
        raise ValueError("listing_asin_missing_or_invalid")
    if not isinstance(identity_evidence, dict) or not identity_evidence:
        raise ValueError("identity_evidence_required")
    source_host = (urlparse(source_url).hostname or "").lower()
    page_host = (urlparse(source_page_url).hostname or "").lower()
    if urlparse(source_url).scheme != "https" or not source_host:
        raise ValueError("image_source_must_be_https")
    if urlparse(source_page_url).scheme != "https" or not page_host:
        raise ValueError("product_source_page_must_be_https")

    quality = validate_image_bytes(data, source_url=source_url)
    if quality["verdict"] in {"confirmed_placeholder", "suspected_placeholder", "decode_failed", "unreachable", "wrong_product"}:
        raise ValueError(f"image_rejected:{quality['verdict']}")
    digest = str(quality.get("evidence", {}).get("sha256") or "")
    if not digest or digest == PRIME_LOGO_SHA256:
        raise ValueError("confirmed_prime_placeholder")
    width, height = quality["evidence"].get("dimensions") or [0, 0]
    image_format = str(quality["evidence"].get("format") or "").upper()
    extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(image_format)
    if not extension:
        raise ValueError("unsupported_image_format")

    storage = storage or LocalStorage()
    root = storage.root.resolve()
    target_dir = (root / "catalog-reference").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / f"{_slug(listing.title or source_title)}-{digest[:12]}{extension}").resolve()
    if not target.is_relative_to(root):
        raise ValueError("storage_path_escape")
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError("deterministic_filename_content_conflict")
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
    public_path = f"/media/{target.relative_to(root).as_posix()}"

    image = db.execute(select(Image).where(
        Image.user_id == listing.user_id,
        Image.source_url == source_url,
        Image.local_path == str(target),
    )).scalar_one_or_none()
    if image and str((image.image_metadata or {}).get("sha256") or "") != digest:
        raise ValueError("existing_media_record_hash_conflict")
    now = datetime.now(UTC).replace(tzinfo=None)
    media_metadata = {
        "source": "catalog_reference_candidate",
        "source_platform": source_platform,
        "source_page_url": source_page_url,
        "source_page_title": source_title,
        "asin": asin,
        "listing_id": listing.id,
        "identity_state": identity_state,
        "identity_evidence": identity_evidence,
        "rights_state": "unverified",
        "operator_state": "pending_review",
        "operator_approved": False,
        "seller_photographed": False,
        "marketplace_approved": False,
        "sha256": digest,
        "dimensions": [width, height],
        "format": image_format,
        "acquired_at": now.isoformat(),
        "filename": target.name,
    }
    if not image:
        image = Image(user_id=listing.user_id, source_url=source_url,
                      local_path=str(target), width=width, height=height,
                      image_metadata=media_metadata)
        db.add(image)
        db.flush()

    urls = list(listing.image_urls or [])
    gallery = [dict(row) for row in (listing.listing_images or []) if isinstance(row, dict)]
    already_attached = public_path in urls or any(row.get("storage_path") == public_path for row in gallery)
    if not already_attached:
        display_order = max([int(row.get("display_order", i)) for i, row in enumerate(gallery)] + [-1]) + 1
        urls.append(public_path)
        gallery.append({
            "storage_path": public_path,
            "source_url": source_url,
            "source_page_url": source_page_url,
            "source_platform": source_platform,
            "role": "primary_candidate" if not gallery else "gallery_candidate",
            "operator_state": "suggested",
            "is_reference": True,
            "seller_photographed": False,
            "marketplace_approved": False,
            "display_order": display_order,
            "metadata": {
                "source": "catalog_reference_candidate", "asin": asin,
                "source_title": source_title, "identity_state": identity_state,
                "identity_evidence": identity_evidence, "rights_state": "unverified",
                "operator_review_required": True, "sha256": digest,
                "dimensions": [width, height], "format": image_format,
            },
        })
        listing.image_urls = urls
        listing.listing_images = gallery

    attempt = {
        "source_type": source_platform,
        "source_page_url": source_page_url,
        "source_title": source_title,
        "image_url": source_url,
        "identity_state": identity_state,
        "identity_evidence": identity_evidence,
        "sha256": digest,
        "filename": target.name,
        "status": "candidate_attached_pending_operator_and_rights_review",
        "attempted_at": now.isoformat(),
    }
    recovery = dict(metadata.get("image_recovery") or {})
    attempts = list(recovery.get("source_attempts") or [])
    if not any(row.get("sha256") == digest and row.get("source_page_url") == source_page_url for row in attempts if isinstance(row, dict)):
        attempts.append(attempt)
    recovery.update({"status": "image_candidates_found", "identity_state": identity_state,
                     "rights_state": "unverified", "operator_review_required": True,
                     "source_attempts": attempts})
    metadata["image_recovery"] = recovery
    listing.source_metadata = metadata

    job = db.execute(select(VineImageRecoveryJob).where(
        VineImageRecoveryJob.user_id == listing.user_id,
        VineImageRecoveryJob.asin == asin,
    )).scalar_one_or_none()
    if not job:
        job = VineImageRecoveryJob(user_id=listing.user_id, listing_id=listing.id,
                                  asin=asin, title_hint=listing.title,
                                  status="review_required", attempts=0)
        db.add(job)
        db.flush()
    job.listing_id = listing.id
    job.title_hint = listing.title
    job.status = "review_required"
    job.completed_at = now
    evidence = dict(job.page_evidence_json or {})
    source_attempts = list(evidence.get("source_attempts") or [])
    if not any(row.get("sha256") == digest for row in source_attempts if isinstance(row, dict)):
        source_attempts.append(attempt)
    evidence.update({"requested_asin": asin, "source_url": source_page_url,
                     "source_title": source_title, "collector": "catalog_reference_discovery",
                     "identity_state": identity_state, "identity_evidence": identity_evidence,
                     "rights": "unverified", "source_attempts": source_attempts})
    job.page_evidence_json = evidence
    candidates = list(job.candidates_json or [])
    existing = any(str(row.get("sha256") or "") == digest for row in candidates if isinstance(row, dict))
    if not existing:
        candidates.append({"image_id": image.id, "storage_path": public_path,
                           "source_url": source_url, "source_page_url": source_page_url,
                           "source_title": source_title, "verdict": "review_required",
                           "identity_state": identity_state, "identity_evidence": identity_evidence,
                           "rights_status": "unverified", "operator_state": "pending_review",
                           "sha256": digest, "dimensions": [width, height], "format": image_format,
                           "attached_to_listing": True})
    job.candidates_json = candidates
    db.add(listing)
    db.add(image)
    db.add(job)
    db.flush()
    return {"listing_id": listing.id, "asin": asin, "image_id": image.id,
            "storage_path": public_path, "filename": target.name,
            "sha256": digest, "dimensions": [width, height], "format": image_format,
            "gallery_count": len(listing.listing_images or []),
            "already_attached": already_attached, "job_id": job.id,
            "job_status": job.status, "identity_state": identity_state,
            "rights_state": "unverified", "operator_state": "pending_review"}
