from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import which
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import cv2
import httpx
import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import ListingStatus
from app.models.models import (
    CanonicalItem,
    CanonicalItemFact,
    AutomatedOfferLog,
    IntakeNotification,
    IntakePhoto,
    IntakePhotoBatch,
    IntakeProviderMedia,
    IntakeReconciliationEvent,
    IntakeReconciliationJob,
    IntakeSession,
    IntakeSourceState,
    IntakeSlate,
    IntakeSlateRecoveryCandidate,
    EbayOfferHistory,
    Listing,
    ListingABTestVariant,
    ListingPrediction,
    MarketplaceListing,
    MarketplaceCrosspostJob,
    MarketplaceImportJob,
    MediaRecoveryItemGroup,
    Sale,
    SlateObservation,
    User,
)
from app.services.ebay import EbayService
from app.services.automation_bridge import AutomationBridgeError, submit_bridge_job
from app.services.google_photos import GooglePhotosService
from app.services.google_photos_oauth import get_google_photos_oauth_state, upload_photo_to_album, GooglePhotosOAuthError
from app.services.listing_ai import ListingAIService
from app.services.listing_review import derive_condition_data, derive_shipping_profile, normalize_listing_images, shipping_policy_for_user, summarize_listing_readiness
from app.services.media_lifecycle import purge_listing_media
from app.services.listing_workspace import normalize_marketplace_data
from app.services.photo_enrichment import PhotoEnrichmentService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.storage import LocalStorage

logger = logging.getLogger(__name__)

INTAKE_SETTINGS_KEY = "intake_settings"
SLATE_TYPE = "posterpro_head_slate"
SLATE_VERSION = 1

DEFAULT_INTAKE_SETTINGS = {
    "provider": "google_photos",
    "enabled": False,
    "album_url": "",
    "google_album_id": "",
    "folder_id": "",
    "poll_interval_seconds": 300,
    "default_item_prefix": "SP",
    "default_box_prefix": "BX",
    "default_location": "",
    "default_session_naming_pattern": "{date}-{location}",
    "default_label_copies": 2,
    "auto_increment_item_id": True,
    "auto_increment_box_id": True,
    "keep_same_box_mode": False,
    "exclude_head_slate_from_public_listing_photos": True,
    "internal_box_photos_default": True,
    "auto_draft_listing": True,
    "auto_draft_when_provisional": True,
    "drafting_paused": False,
    "draft_min_public_photos": 1,
    "max_new_items_per_run": None,
    "quiet_period_seconds": 300,
    "integrity_scan_interval_seconds": 86400,
    "require_manual_review_before_publish": True,
    "max_new_photos_per_run": 60,
    "source_poll_lease_seconds": 300,
    "provider_overall_timeout_seconds": 180,
    "download_timeout_seconds": 45,
    "image_seo_filename_pattern": "{item_id}_{seo_title}_{photo_number}",
    "marketplace_defaults": {"targets": ["ebay", "facebook"]},
}

SLATE_DETECTION_VERSION = 3
SLATE_RECOVERY_PIPELINE_VERSION = "deterministic_slate_recovery_v1"
SLATE_RECOVERY_PIPELINE_VERSION_V2 = "deterministic_slate_recovery_v2"
STRICT_ITEM_ID_RE = re.compile(r"^SP-(?:\d{8}|\d{4})-\d{4}$")
ITEM_ID_TEXT_RE = re.compile(r"SP\s*[-\u2010-\u2015\u2212]\s*[0-9OIL|S]{4,8}\s*[-\u2010-\u2015\u2212]\s*[0-9OIL|S]{4}", re.IGNORECASE)
ITEM_ID_LIKE_TEXT_RE = re.compile(r"SP\s*[-\u2010-\u2015\u2212]\s*[^\s-]{8}\s*[-\u2010-\u2015\u2212]\s*[^\s-]{4}", re.IGNORECASE)
TAIL_BOUNDARY_TOKENS = (
    "tail slate",
    "tailslate",
    "tale slate",
    "taleslate",
    "end slate",
    "endslate",
    "late slate",
    "lateslate",
)


@dataclass
class OrderedIntakePhoto:
    photo: IntakePhoto
    sort_key: tuple


class IntakeSlateService:
    def __init__(self) -> None:
        self.google_photos = GooglePhotosService()
        self.storage = LocalStorage()
        self.ai = ListingAIService()
        self.photo_enrichment = PhotoEnrichmentService()
        self.ebay = EbayService()
        self.pricing = PricingIntelligenceService()

    def settings_for_user(self, user: User | None) -> dict[str, Any]:
        raw = ((user.settings_json or {}).get(INTAKE_SETTINGS_KEY) if user else None) or {}
        stored = raw if isinstance(raw, dict) else {}
        return {
            **DEFAULT_INTAKE_SETTINGS,
            **stored,
            "google_photos": get_google_photos_oauth_state(user) if user else {
                "connected": False,
                "connection_state": "not_connected",
                "account_email": None,
                "account_subject": None,
                "account_name": None,
                "has_refresh_token": False,
                "token_expires_at": None,
                "last_connected_at": None,
                "last_error": None,
                "scopes": [],
                "redirect_uri": None,
            },
            "marketplace_defaults": {
                **DEFAULT_INTAKE_SETTINGS["marketplace_defaults"],
                **(stored.get("marketplace_defaults") if isinstance(stored.get("marketplace_defaults"), dict) else {}),
            },
        }

    @staticmethod
    def _drafting_enabled(settings_payload: dict[str, Any] | None) -> bool:
        payload = settings_payload if isinstance(settings_payload, dict) else {}
        return bool(payload.get("auto_draft_listing", True)) and not bool(payload.get("drafting_paused", False))

    def drafting_paused_for_user(self, user: User | None) -> bool:
        return not self._drafting_enabled(self.settings_for_user(user))

    def save_settings(self, *, db: Session, user: User, payload: dict[str, Any]) -> dict[str, Any]:
        merged = {
            **self.settings_for_user(user),
            **payload,
            "marketplace_defaults": {
                **self.settings_for_user(user).get("marketplace_defaults", {}),
                **(payload.get("marketplace_defaults") if isinstance(payload.get("marketplace_defaults"), dict) else {}),
            },
        }
        settings_json = dict(user.settings_json or {})
        settings_json[INTAKE_SETTINGS_KEY] = merged
        user.settings_json = settings_json
        db.add(user)
        db.commit()
        db.refresh(user)
        return self.settings_for_user(user)

    @staticmethod
    def _session_intake_metadata(session: IntakeSession | None) -> dict[str, Any]:
        metadata = session.metadata_json if session and isinstance(session.metadata_json, dict) else {}
        intake = metadata.get("intake") if isinstance(metadata.get("intake"), dict) else {}
        return intake

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        try:
            parsed = int(str(value).strip())
            return parsed if parsed > 0 else None
        except Exception:
            return None

    def _initial_display_number(self, db: Session, *, user_id: int, field: str) -> int:
        column = IntakeSlate.item_id if field == "item" else IntakeSlate.box_id
        values = db.execute(
            select(column).where(IntakeSlate.user_id == user_id)
        ).scalars().all()
        numbers: list[int] = []
        for value in values:
            if not value:
                continue
            parsed = self._coerce_int(str(value).rsplit("-", 1)[-1])
            if parsed is not None:
                numbers.append(parsed)
        return (max(numbers) if numbers else 0) + 1

    def _build_slate_metadata(
        self,
        db: Session,
        *,
        user: User,
        session: IntakeSession,
        slate: IntakeSlate | None,
        payload: dict[str, Any],
        qr_payload: dict[str, Any],
    ) -> dict[str, Any]:
        session_intake = self._session_intake_metadata(session)
        next_item_number = self._coerce_int(session_intake.get("next_item_display_number"))
        next_box_number = self._coerce_int(session_intake.get("next_box_display_number"))
        if next_item_number is None:
            next_item_number = self._initial_display_number(db, user_id=user.id, field="item")
        if next_box_number is None:
            next_box_number = self._initial_display_number(db, user_id=user.id, field="box")
        label_copies = self._coerce_int(payload.get("label_copies")) or self._coerce_int(session_intake.get("label_default_copies")) or int(self.settings_for_user(user).get("default_label_copies") or 2)
        return {
            "canonical_item_uuid": str(session_intake.get("canonical_item_uuid") or uuid4()),
            "display_item_number": next_item_number,
            "display_box_number": next_box_number,
            "current_location": qr_payload.get("location") or session.default_location or "",
            "label_default_copies": label_copies,
            "label_status": "pending",
            "label_print_status": "pending",
            "google_photos": {
                "status": "not_connected",
                "album_url": str(self.settings_for_user(user).get("album_url") or "").strip() or None,
                "album_id": self._album_identifier(self.settings_for_user(user).get("album_url")),
                "last_error": None,
                "last_upload_status": None,
                "last_upload_error": None,
            },
            "voice": {},
            "print_history": [],
            "created_via": "slate_page",
        }

    @staticmethod
    def _merge_nested_metadata(metadata: dict[str, Any] | None, updates: dict[str, Any]) -> dict[str, Any]:
        next_metadata = dict(metadata or {})
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(next_metadata.get(key), dict):
                next_metadata[key] = {**next_metadata[key], **value}
            else:
                next_metadata[key] = value
        return next_metadata

    def list_sessions(self, db: Session, *, user_id: int) -> list[IntakeSession]:
        return db.execute(select(IntakeSession).where(IntakeSession.user_id == user_id).order_by(IntakeSession.updated_at.desc())).scalars().all()

    def get_or_create_session(self, db: Session, *, user: User, payload: dict[str, Any]) -> IntakeSession:
        settings_payload = self.settings_for_user(user)
        requested_session_id = str(payload.get("session_id") or "").strip()
        session_id = requested_session_id or self._default_session_id(settings_payload)
        existing = db.execute(select(IntakeSession).where(IntakeSession.user_id == user.id, IntakeSession.session_id == session_id)).scalar_one_or_none()
        if existing:
            existing.name = payload.get("name") or existing.name
            existing.source_album_id = payload.get("source_album_id") or existing.source_album_id
            existing.source_folder_id = payload.get("source_folder_id") or existing.source_folder_id
            existing.default_location = payload.get("default_location") or existing.default_location or settings_payload.get("default_location")
            existing.item_prefix = payload.get("item_prefix") or existing.item_prefix or settings_payload.get("default_item_prefix")
            existing.box_prefix = payload.get("box_prefix") or existing.box_prefix or settings_payload.get("default_box_prefix")
            existing.status = payload.get("status") or existing.status
            db.add(existing)
            db.commit()
            db.refresh(existing)
            return existing
        session = IntakeSession(
            user_id=user.id,
            session_id=session_id,
            name=payload.get("name") or session_id,
            source_album_id=payload.get("source_album_id") or self._album_identifier(settings_payload.get("album_url")),
            source_folder_id=payload.get("source_folder_id") or settings_payload.get("folder_id") or None,
            default_location=payload.get("default_location") or settings_payload.get("default_location") or None,
            item_prefix=payload.get("item_prefix") or settings_payload.get("default_item_prefix") or "SP",
            box_prefix=payload.get("box_prefix") or settings_payload.get("default_box_prefix") or "BX",
            status=payload.get("status") or "active",
            metadata_json={"created_from": "head_slate_workflow"},
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    def create_slate(self, db: Session, *, user: User, payload: dict[str, Any]) -> tuple[IntakeSlate, dict[str, Any], str]:
        session = self.get_or_create_session(db, user=user, payload=payload)
        session = db.execute(
            select(IntakeSession).where(IntakeSession.id == session.id).with_for_update()
        ).scalar_one()
        settings_payload = self.settings_for_user(user)
        current_location = str(payload.get("location") or session.default_location or self._session_intake_metadata(session).get("current_location") or settings_payload.get("default_location") or "").strip()
        if current_location:
            session.default_location = current_location
        session_intake = self._session_intake_metadata(session)
        display_item_number = self._coerce_int(session_intake.get("next_item_display_number")) or self._initial_display_number(db, user_id=user.id, field="item")
        display_box_number = self._coerce_int(session_intake.get("next_box_display_number")) or self._initial_display_number(db, user_id=user.id, field="box")
        canonical_item_uuid = str(session_intake.get("canonical_item_uuid") or uuid4())
        created_at = datetime.now(UTC).astimezone().isoformat()
        item_prefix = str(payload.get("item_prefix") or session.item_prefix or settings_payload.get("default_item_prefix") or "SP")
        box_prefix = str(payload.get("box_prefix") or session.box_prefix or settings_payload.get("default_box_prefix") or "BX")
        requested_box = str(payload.get("box_id") or "").strip()
        if requested_box:
            box_id = requested_box
        elif bool(payload.get("same_box")) and str(session_intake.get("last_slate_box_id") or "").strip():
            box_id = str(session_intake.get("last_slate_box_id")).strip()
        else:
            box_id = f"{self._slug_token(box_prefix).upper() or 'BX'}-{display_box_number:04d}"
        item_id = str(payload.get("item_id") or "").strip() or self._build_fallback_item_id(
            prefix=item_prefix,
            sequence=display_item_number,
        )
        voice_transcript = str(payload.get("voice_transcript") or "").strip()
        voice_notes = str(payload.get("voice_notes") or "").strip()
        voice_intelligence = payload.get("voice_intelligence") if isinstance(payload.get("voice_intelligence"), dict) else {}
        voice_audio_asset = self._save_data_url_asset(
            data_url=payload.get("voice_audio_data_url"),
            prefix=f"intake-voice/{user.id}",
            suggested_basename=f"{item_id}-voice-note",
        )
        label_copies = self._coerce_int(payload.get("label_copies")) or self._coerce_int(session_intake.get("label_default_copies")) or int(settings_payload.get("default_label_copies") or 2)
        quantity = str(payload.get("quantity") or "").strip()
        qr_payload = {
            "type": SLATE_TYPE,
            "version": SLATE_VERSION,
            "canonical_item_uuid": canonical_item_uuid,
            "display_item_number": display_item_number,
            "display_box_number": display_box_number,
            "session_id": session.session_id,
            "item_id": item_id,
            "box_id": box_id,
            "location": current_location,
            "title": str(payload.get("title") or "").strip(),
            "brand": str(payload.get("brand") or "").strip(),
            "model": str(payload.get("model") or "").strip(),
            "condition": str(payload.get("condition") or "").strip(),
            "notes": str(payload.get("notes") or "").strip(),
            "flaws": str(payload.get("flaws") or "").strip(),
            "weight": str(payload.get("weight") or "").strip(),
            "length": str(payload.get("length") or "").strip(),
            "width": str(payload.get("width") or "").strip(),
            "height": str(payload.get("height") or "").strip(),
            "quantity": quantity,
            "packed": bool(payload.get("mark_packed") or payload.get("packed")),
            "boundary_position": self._normalize_boundary_position(payload),
            "created_at": created_at,
            "label_copies": label_copies,
        }
        slate_metadata = self._build_slate_metadata(db, user=user, session=session, slate=None, payload={**payload, "label_copies": label_copies}, qr_payload=qr_payload)
        slate_metadata = self._merge_nested_metadata(
            slate_metadata,
            {
                "state": {
                    "group_state": "HEAD_SEEN",
                    "draft_state": "CAPTURE_ONLY",
                    "reconciliation_state": "none",
                    "state_updated_at": created_at,
                    "group_revision": 1,
                    "evidence_revision": 1,
                },
                "voice": {
                    "transcript": voice_transcript or None,
                    "notes": voice_notes or None,
                    "intelligence": voice_intelligence or None,
                    "saved_at": created_at,
                    "audio": voice_audio_asset or None,
                },
            },
        )
        existing = db.execute(select(IntakeSlate).where(IntakeSlate.item_id == item_id)).scalar_one_or_none()
        if existing and existing.user_id != user.id:
            raise ValueError("Item ID already exists for another user.")
        if existing is None:
            slate = IntakeSlate(
                user_id=user.id,
                intake_session_id=session.id,
                session_id=session.session_id,
                item_id=item_id,
                box_id=box_id,
                location=qr_payload["location"],
                title=qr_payload["title"] or None,
                brand=qr_payload["brand"] or None,
                model=qr_payload["model"] or None,
                condition=qr_payload["condition"] or None,
                notes=qr_payload["notes"] or None,
                flaws=qr_payload["flaws"] or None,
                weight=qr_payload["weight"] or None,
                length=qr_payload["length"] or None,
                width=qr_payload["width"] or None,
                height=qr_payload["height"] or None,
                packed=bool(qr_payload["packed"]),
                internal_notes=str(payload.get("internal_notes") or "").strip() or None,
                qr_payload_json=qr_payload,
                metadata_json=slate_metadata,
                status="draft",
            )
            db.add(slate)
        else:
            # The stable item ID is deliberately reusable for a correction or
            # supplemental slate. Published listings are protected later in
            # regeneration; a new slate must never create a duplicate item.
            existing.intake_session_id = session.id
            existing.session_id = session.session_id
            existing.box_id = box_id
            existing.location = qr_payload["location"] or existing.location
            existing.title = qr_payload["title"] or existing.title
            existing.brand = qr_payload["brand"] or existing.brand
            existing.model = qr_payload["model"] or existing.model
            existing.condition = qr_payload["condition"] or existing.condition
            existing.notes = qr_payload["notes"] or existing.notes
            existing.flaws = qr_payload["flaws"] or existing.flaws
            existing.weight = qr_payload["weight"] or existing.weight
            existing.length = qr_payload["length"] or existing.length
            existing.width = qr_payload["width"] or existing.width
            existing.height = qr_payload["height"] or existing.height
            existing.packed = bool(qr_payload["packed"])
            existing.internal_notes = str(payload.get("internal_notes") or existing.internal_notes or "").strip() or None
            existing.qr_payload_json = qr_payload
            existing.metadata_json = self._merge_nested_metadata(existing.metadata_json if isinstance(existing.metadata_json, dict) else {}, slate_metadata)
            slate = existing
            db.add(existing)
        session.metadata_json = self._merge_nested_metadata(
            session.metadata_json if isinstance(session.metadata_json, dict) else {},
            {
                "intake": {
                    "canonical_item_uuid": canonical_item_uuid,
                    "current_location": current_location or session.default_location or "",
                    "next_item_display_number": display_item_number + 1,
                    "next_box_display_number": display_box_number + 1,
                    "label_default_copies": label_copies,
                    "last_slate_item_id": item_id,
                    "last_slate_box_id": box_id,
                    "last_slate_at": created_at,
                    "last_group_state": "HEAD_SEEN",
                }
            },
        )
        db.add(session)
        db.commit()
        db.refresh(slate)
        db.refresh(session)
        self._record_slate_observation(
            db,
            user_id=user.id,
            slate=slate,
            photo=None,
            payload=qr_payload,
            source_type="operator_slate",
            operator_confirmed=True,
        )
        db.commit()
        qr_data_url = self.build_qr_data_url(qr_payload)
        return slate, qr_payload, qr_data_url

    def render_slate_preview_asset(
        self,
        payload: dict[str, Any],
        *,
        item_id: str,
        session_id: str | None = None,
    ) -> dict[str, str]:
        """Render a backend-generated slate preview image for review or upload workflows."""
        preview = self._render_slate_preview_image(payload)
        session_token = self._slug_token(session_id or str(payload.get("session_id") or "")) or "session"
        item_token = self._slug_token(item_id) or "item"
        destination_dir = Path(settings.storage_root) / "intake-slates" / session_token
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{item_token}-posterpro-head-slate.png"
        preview.save(destination, format="PNG")
        output = io.BytesIO()
        preview.save(output, format="PNG")
        return {
            "storage_path": self._to_public_media_path(str(destination)),
            "local_path": str(destination),
            "data_url": f"data:image/png;base64,{base64.b64encode(output.getvalue()).decode('ascii')}",
        }

    def render_label_preview_asset(
        self,
        payload: dict[str, Any],
        *,
        item_id: str,
        session_id: str | None = None,
    ) -> dict[str, str]:
        preview = self._render_label_preview_image(payload)
        session_token = self._slug_token(session_id or str(payload.get("session_id") or "")) or "session"
        item_token = self._slug_token(item_id) or "item"
        destination_dir = Path(settings.storage_root) / "intake-labels" / session_token
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{item_token}-posterpro-label.png"
        preview.save(destination, format="PNG")
        output = io.BytesIO()
        preview.save(output, format="PNG")
        return {
            "storage_path": self._to_public_media_path(str(destination)),
            "local_path": str(destination),
            "data_url": f"data:image/png;base64,{base64.b64encode(output.getvalue()).decode('ascii')}",
        }

    def _render_label_preview_image(self, payload: dict[str, Any]) -> Image.Image:
        width = 696
        height = 384
        canvas = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(canvas)

        def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
            candidates = (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            )
            for candidate in candidates:
                try:
                    return ImageFont.truetype(candidate, size=size)
                except Exception:
                    continue
            return ImageFont.load_default()

        title_font = load_font(34, bold=True)
        body_font = load_font(20)
        mono_font = load_font(24, bold=True)
        small_font = load_font(16)
        qr = qrcode.QRCode(border=2, box_size=6)
        qr.add_data(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        qr.make(fit=True)
        qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((180, 180))

        item_label = str(payload.get("display_item_number") or payload.get("item_id") or "—").strip()
        box_label = str(payload.get("display_box_number") or payload.get("box_id") or "—").strip()
        location = str(payload.get("location") or "—").strip()
        title = str(payload.get("title") or "").strip() or "PENDING IDENTIFICATION"
        quantity = str(payload.get("quantity") or payload.get("qty") or 1).strip()
        copies = str(payload.get("label_copies") or 2).strip()
        slate_type = "HEAD SLATE" if str(payload.get("boundary_position") or "start").lower() != "tail" else "TAIL SLATE"

        draw.rounded_rectangle((16, 16, width - 16, height - 16), radius=20, outline="black", width=3)
        draw.text((32, 28), "POSTERPRO", fill="black", font=small_font)
        draw.text((32, 56), slate_type, fill="black", font=title_font)
        draw.text((32, 112), title[:48], fill="black", font=body_font)
        draw.text((32, 150), f"ITEM {item_label}", fill="black", font=mono_font)
        draw.text((32, 188), f"BOX {box_label}", fill="black", font=mono_font)
        draw.text((32, 230), f"LOCATION {location[:36]}", fill="black", font=body_font)
        draw.text((32, 262), f"QTY {quantity}   COPIES {copies}", fill="black", font=body_font)
        draw.text((32, 296), f"SESSION {str(payload.get('session_id') or '—')[:28]}", fill="black", font=small_font)
        canvas.paste(qr_image, (width - 214, 86))
        draw.text((width - 214, 278), "Scan for item record", fill="black", font=small_font)
        return canvas

    def build_voice_intelligence(
        self,
        *,
        db: Any | None = None,
        user: User,
        slate: IntakeSlate | None,
        transcript: str,
        notes: str | None = None,
        current_form: dict[str, Any] | None = None,
        current_session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        form = current_form if isinstance(current_form, dict) else {}
        session = current_session if isinstance(current_session, dict) else {}
        transcript_text = str(transcript or "").strip()
        transcript_lower = transcript_text.lower()
        quantity_hint = None
        for pattern in (
            r"\b(?:quantity|qty|have|got|there are|i have)\s+(\d+)\b",
            r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b",
        ):
            match = re.search(pattern, transcript_lower)
            if not match:
                continue
            value = match.group(1)
            if value.isdigit():
                quantity_hint = int(value)
            else:
                quantity_hint = {
                    "one": 1,
                    "two": 2,
                    "three": 3,
                    "four": 4,
                    "five": 5,
                    "six": 6,
                    "seven": 7,
                    "eight": 8,
                    "nine": 9,
                    "ten": 10,
                }.get(value)
            if quantity_hint:
                break
        condition_hint = None
        for phrase, normalized in (
            ("new open box", "New - Open Box"),
            ("new in box", "New"),
            ("brand new", "New"),
            ("used", "Used"),
        ):
            if phrase in transcript_lower:
                condition_hint = normalized
                break
        marketplace_targets = []
        for marketplace in ("ebay", "facebook", "mercari", "poshmark", "vinted"):
            if marketplace in transcript_lower:
                marketplace_targets.append(marketplace)
        if not marketplace_targets:
            marketplace_targets = list(self.settings_for_user(user).get("marketplace_defaults", {}).get("targets") or ["ebay", "facebook"])
        source_metadata = {
            "session": session,
            "slate": {
                "item_id": slate.item_id if slate else None,
                "box_id": slate.box_id if slate else None,
                "location": slate.location if slate else None,
                "title": slate.title if slate else None,
            } if slate else {},
            "voice": {"transcript": transcript_text, "notes": notes},
        }
        image_signals = {
            "title_hint": transcript_text or str(form.get("title") or "").strip(),
            "source_type": "intake_voice",
            "image_count": 0,
            "existing_specifics": {
                "Brand": str(form.get("brand") or "").strip(),
                "Model": str(form.get("model") or "").strip(),
                "Type": str(form.get("title") or "").strip(),
                "UPC": str(form.get("upc") or "").strip(),
            },
            "existing_condition": str(form.get("condition") or condition_hint or "").strip(),
            "quantity": quantity_hint,
            "custom_labels": [slate.item_id if slate else None, slate.box_id if slate else None],
            "source_metadata": source_metadata,
            "voice_transcript": transcript_text,
            "voice_notes": notes,
            "photo_keywords": [word for word in self._slug_token(transcript_text).split("-") if word],
            "marketplace_targets": marketplace_targets,
        }
        generated = self.ai.generate(image_signals, db=db, user_id=user.id, listing_id=slate.listing_id if slate else None)
        generated["voice_transcript"] = transcript_text
        generated["voice_notes"] = notes
        generated["confidence"] = generated.get("confidence") or 0.5
        generated["marketplace_targets"] = marketplace_targets
        return generated

    def transcribe_voice_audio(
        self,
        *,
        voice_audio_data_url: str,
        fallback_transcript: str | None = None,
    ) -> dict[str, Any]:
        fallback = str(fallback_transcript or "").strip()
        audio_bytes, mime_type = self._parse_data_url(voice_audio_data_url)
        if not audio_bytes:
            return {
                "transcript": fallback,
                "used_fallback": bool(fallback),
                "audio_present": False,
                "transcription_source": "fallback" if fallback else "missing_audio",
            }
        if not settings.openai_api_key:
            return {
                "transcript": fallback,
                "used_fallback": bool(fallback),
                "audio_present": True,
                "transcription_source": "fallback" if fallback else "missing_openai_api_key",
                "error": "OPENAI_API_KEY is not configured",
            }
        extension = {
            "audio/webm": ".webm",
            "audio/mp4": ".m4a",
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/ogg": ".ogg",
        }.get(mime_type or "", ".webm")
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(suffix=extension, delete=True) as temp_audio:
            temp_audio.write(audio_bytes)
            temp_audio.flush()
            transcription = self._transcribe_audio_file(Path(temp_audio.name))
        transcript = str((transcription or {}).get("text") or "").strip() or fallback
        return {
            "transcript": transcript,
            "used_fallback": transcript == fallback and bool(fallback),
            "audio_present": True,
            "transcription_source": "openai" if transcript and transcript != fallback else ("fallback" if fallback else "openai"),
            "model_used": (transcription or {}).get("model"),
            "language": (transcription or {}).get("language"),
        }

    def _transcribe_audio_file(self, audio_path: Path) -> dict[str, Any]:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        data = {"model": "whisper-1"}
        with audio_path.open("rb") as handle:
            files = {"file": (audio_path.name, handle, "application/octet-stream")}
            with httpx.Client(timeout=60) as client:
                response = client.post("https://api.openai.com/v1/audio/transcriptions", headers=headers, data=data, files=files)
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {"text": str(response.text or "").strip()}

    @staticmethod
    def _parse_data_url(data_url: str | None) -> tuple[bytes | None, str | None]:
        raw = str(data_url or "").strip()
        if not raw.startswith("data:"):
            return None, None
        header, _, payload = raw.partition(",")
        if not payload:
            return None, None
        mime = header[5:].split(";", 1)[0] or "application/octet-stream"
        try:
            return base64.b64decode(payload), mime
        except Exception:
            return None, None

    def _save_data_url_asset(self, *, data_url: str | None, prefix: str, suggested_basename: str) -> dict[str, str] | None:
        data, mime = self._parse_data_url(data_url)
        if not data:
            return None
        extension = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "audio/webm": ".webm",
            "audio/mp4": ".m4a",
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/ogg": ".ogg",
        }.get(mime or "", ".bin")
        path = self.storage.save_bytes(data, extension=extension, prefix=prefix)
        return {
            "path": path,
            "public_path": self._to_public_media_path(path),
            "mime_type": mime or None,
            "filename": f"{self._slug_token(suggested_basename, max_length=120) or 'asset'}{extension}",
        }

    def queue_rendered_slate_upload(
        self,
        db: Session,
        *,
        user: User,
        slate: IntakeSlate,
        qr_payload: dict[str, Any],
        rendered_asset: dict[str, str],
        suppress_notifications: bool = False,
    ) -> dict[str, Any]:
        settings_payload = self.settings_for_user(user)
        target_album_url = str(settings_payload.get("album_url") or settings_payload.get("folder_id") or "").strip()
        asset_payload = {
            "storage_path": rendered_asset.get("storage_path"),
            "local_path": rendered_asset.get("local_path"),
            "data_url": rendered_asset.get("data_url"),
        }
        if not target_album_url:
            result = {
                "status": "BRIDGE_UPLOAD_SKIPPED",
                "reason": "No intake Google Photos album or Drive link is configured.",
                "job_type": "google_photos_upload",
                "execution_mode": "browser_assist",
                "target_album_url": None,
                "rendered_asset": asset_payload,
            }
            if not suppress_notifications:
                db.add(
                    IntakeNotification(
                        user_id=user.id,
                        notification_type="intake_slate_bridge_upload_skipped",
                        title="Rendered slate upload skipped",
                        message="No intake Google Photos album or Drive link is configured, so the rendered slate was not queued for upload.",
                        href="/intake/slate",
                        metadata_json={"item_id": slate.item_id, "session_id": slate.session_id, "target_album_url": None},
                    )
                )
                db.commit()
            return result

        # Use the real Google Photos API first. The automation bridge does not
        # implement a google_photos_upload job (it only handles marketplace
        # import/crosspost jobs), so routing a Slate there can never upload it.
        # Shared photos.app.goo.gl links contain a browser token, not the
        # durable REST album ID. Let the API create/return an app-owned album
        # and persist that ID after the first successful upload.
        album_id = str(settings_payload.get("google_album_id") or "").strip() or (None if "photos.app.goo.gl" in target_album_url else self._album_identifier(target_album_url))
        local_path = rendered_asset.get("local_path") or rendered_asset.get("storage_path")
        try:
            if not local_path:
                raise GooglePhotosOAuthError("Rendered Slate asset is unavailable")
            asset_path = Path(str(local_path))
            if not asset_path.is_file():
                raise GooglePhotosOAuthError("Rendered Slate asset is not available on the server")
            upload = upload_photo_to_album(
                user=user,
                db=db,
                album_id=album_id,
                image_bytes=asset_path.read_bytes(),
                filename=rendered_asset.get("filename") or f"posterpro-slate-{slate.item_id}.png",
                description=f"PosterPro HEAD Slate {slate.item_id} / Box {slate.box_id}",
                album_title="PosterPro",
            )
            # Keep the durable API album identity alongside the operator's
            # share URL so retries never create another album.
            settings_json = dict(getattr(user, "settings_json", None) or {})
            intake_settings = dict(settings_json.get(INTAKE_SETTINGS_KEY) or {})
            intake_settings["google_album_id"] = upload.get("album_id")
            if upload.get("album_product_url"):
                intake_settings["google_album_url"] = upload.get("album_product_url")
            settings_json[INTAKE_SETTINGS_KEY] = intake_settings
            user.settings_json = settings_json
            db.add(user)
            db.commit()
            result = {
                "status": "UPLOADED",
                "job_type": "google_photos_upload",
                "execution_mode": "google_photos_api",
                "target_album_url": target_album_url,
                "album_id": upload.get("album_id"),
                "google_album_url": upload.get("album_product_url"),
                "google_media_id": upload.get("media_id"),
                "google_media_item": upload.get("media_item"),
                "rendered_asset": asset_payload,
            }
            if not suppress_notifications:
                db.add(IntakeNotification(user_id=user.id, notification_type="intake_slate_google_upload_succeeded", title="Slate uploaded to Google Photos", message=f"{slate.item_id} was uploaded to the PosterPro Google Photos album.", href="/intake/slate", metadata_json={"item_id": slate.item_id, "google_media_id": upload.get("media_id"), "album_id": upload.get("album_id")}))
                db.commit()
            return result
        except (GooglePhotosOAuthError, OSError) as exc:
            # Keep the server-rendered Slate and return a truthful failure. A
            # retry endpoint can re-run the same transaction without making a
            # second intake item or consuming another sequence number.
            direct_error = str(exc)
            if "401" in direct_error:
                direct_error = f"{direct_error} Reconnect Google Photos to refresh the account authorization."

        # Do not submit an unsupported bridge job and report it as queued. The
        # generated image remains saved locally and can be retried explicitly.
        result = {
            "status": "UPLOAD_FAILED",
            "job_type": "google_photos_upload",
            "execution_mode": "google_photos_api",
            "target_album_url": target_album_url,
            "album_id": album_id,
            "rendered_asset": asset_payload,
            "error": direct_error,
        }
        if not suppress_notifications:
            db.add(IntakeNotification(user_id=user.id, notification_type="intake_slate_google_upload_failed", title="Slate Google Photos upload failed", message=direct_error, href="/intake/slate", metadata_json={"item_id": slate.item_id, "album_id": album_id, "error": direct_error}))
            db.commit()
        return result

        try:
            bridge_result = submit_bridge_job(
                job_type="google_photos_upload",
                execution_mode="browser_assist",
                payload={
                    "source_marketplace": "google_photos",
                    "operation": "upload_rendered_slate",
                    "target_album_url": target_album_url,
                    "session_id": slate.session_id,
                    "item_id": slate.item_id,
                    "box_id": slate.box_id,
                    "location": slate.location,
                    "qr_payload": qr_payload,
                    "rendered_asset": asset_payload,
                    "asset_kind": "posterpro_head_slate",
                    "upload_label": f"{slate.item_id} PosterPro Slate",
                },
            )
        except AutomationBridgeError as exc:
            result = {
                "status": "BRIDGE_UPLOAD_FAILED",
                "job_type": "google_photos_upload",
                "execution_mode": "browser_assist",
                "target_album_url": upload.get("album_product_url") or target_album_url,
                "rendered_asset": asset_payload,
                "error": str(exc),
            }
            if not suppress_notifications:
                db.add(
                    IntakeNotification(
                        user_id=user.id,
                        notification_type="intake_slate_bridge_upload_failed",
                        title="Rendered slate upload failed",
                        message=str(exc),
                        href="/intake/slate",
                        metadata_json={"item_id": slate.item_id, "session_id": slate.session_id, "target_album_url": target_album_url, "error": str(exc)},
                    )
                )
                db.commit()
            return result
        result = {
            "status": str(bridge_result.get("status") or "").strip() or "BRIDGE_UPLOAD_QUEUED",
            "job_type": "google_photos_upload",
            "execution_mode": "browser_assist",
            "target_album_url": target_album_url,
            "rendered_asset": asset_payload,
            "bridge_submission": bridge_result,
            "direct_upload_error": direct_error,
        }
        if not suppress_notifications:
            db.add(
                IntakeNotification(
                    user_id=user.id,
                    notification_type="intake_slate_bridge_upload_queued",
                    title="Rendered slate queued for Google Photos upload",
                    message=f"{slate.item_id} was queued for bridge-assisted upload to the intake Google Photos album.",
                    href="/intake/slate",
                    metadata_json={"item_id": slate.item_id, "session_id": slate.session_id, "target_album_url": target_album_url, "bridge_status": result["status"]},
                )
            )
            db.commit()
        return result

    def retry_rendered_slate_upload(
        self,
        db: Session,
        *,
        user: User,
        slate_id: int,
    ) -> dict[str, Any]:
        slate = db.get(IntakeSlate, int(slate_id))
        if slate is None or slate.user_id != user.id:
            raise ValueError("Slate not found.")
        if not isinstance(slate.qr_payload_json, dict) or not slate.qr_payload_json:
            raise ValueError("Slate does not have a stored QR payload to render.")
        rendered_asset = self.render_slate_preview_asset(
            slate.qr_payload_json,
            item_id=slate.item_id,
            session_id=slate.session_id,
        )
        return self.queue_rendered_slate_upload(
            db,
            user=user,
            slate=slate,
            qr_payload=slate.qr_payload_json,
            rendered_asset=rendered_asset,
        )

    def next_item_id(self, db: Session, *, user_id: int, prefix: str = "SP") -> str:
        today = datetime.now(UTC).astimezone().strftime("%Y%m%d")
        prefix_value = self._slug_token(prefix).upper() or "SP"
        existing = db.execute(
            select(IntakeSlate.item_id).where(
                IntakeSlate.user_id == user_id,
                IntakeSlate.item_id.like(f"{prefix_value}-{today}-%"),
            )
        ).scalars().all()
        max_seq = 0
        for value in existing:
            try:
                max_seq = max(max_seq, int(str(value).rsplit("-", 1)[-1]))
            except Exception:
                continue
        return f"{prefix_value}-{today}-{max_seq + 1:04d}"

    def next_box_id(self, db: Session, *, user_id: int, prefix: str = "BX") -> str:
        prefix_value = self._slug_token(prefix).upper() or "BX"
        existing = db.execute(
            select(IntakeSlate.box_id).where(
                IntakeSlate.user_id == user_id,
                IntakeSlate.box_id.like(f"{prefix_value}-%"),
            )
        ).scalars().all()
        max_seq = 0
        for value in existing:
            try:
                max_seq = max(max_seq, int(str(value).rsplit("-", 1)[-1]))
            except Exception:
                continue
        return f"{prefix_value}-{max_seq + 1:04d}"

    def monitor_google_album(self, db: Session, *, user: User) -> dict[str, Any]:
        settings_payload = self.settings_for_user(user)
        source_url = str(settings_payload.get("album_url") or settings_payload.get("folder_id") or "").strip()
        if not source_url:
            raise ValueError("No intake Google Photos album or Drive link is configured.")
        source_state = self._source_state_for(
            db,
            user_id=user.id,
            provider="google_photos",
            source_key=self._album_identifier(source_url),
        )
        lease_owner = f"backend-intake-monitor:{os.getpid()}"
        if not self._claim_source_poll_lease(
            db,
            source_state=source_state,
            owner=lease_owner,
            seconds=max(30, int(settings_payload.get("source_poll_lease_seconds") or 300)),
        ):
            return {"skipped": True, "reason": "source_poll_already_running", "source_state_id": source_state.id}
        source_state.last_poll_started_at = datetime.now(UTC)
        source_state.enumeration_generation = int(source_state.enumeration_generation or 0) + 1
        source_state.scan_complete = False
        db.add(source_state)
        db.commit()
        try:
            try:
                enumeration = self.google_photos.enumerate_photo_entries(
                    source_url,
                    overall_timeout_seconds=max(30, int(settings_payload.get("provider_overall_timeout_seconds") or 180)),
                )
            except TypeError:
                # Legacy adapter fakes and third-party adapters that still
                # implement the original single-argument contract remain safe.
                enumeration = self.google_photos.enumerate_photo_entries(source_url)
        except Exception as exc:
            self._release_source_poll_lease(db, source_state=source_state, owner=lease_owner, error=str(exc))
            raise
        entries = enumeration.entries
        self._heartbeat_source_poll_lease(db, source_state=source_state, owner=lease_owner)
        discovered_by_source_id, discovery_created, discovery_changed = self._persist_provider_discoveries(
            db,
            user=user,
            source_state=source_state,
            source_url=source_url,
            entries=entries,
        )
        imported = 0
        duplicates = 0
        changed = 0
        slates = 0
        assigned = 0
        recovered_slates = 0
        failed_downloads = 0
        download_errors: list[dict[str, str]] = []
        affected_media: list[tuple[int, str]] = []
        budget_exhausted = False
        run_budget = max(1, int(settings_payload.get("max_new_photos_per_run") or 60))
        processing_records = self._claim_provider_media_for_processing(
            db,
            user_id=user.id,
            source_state=source_state,
            worker_id=lease_owner,
            limit=run_budget,
        )
        for provider_media in processing_records:
            if not self._heartbeat_source_poll_lease(db, source_state=source_state, owner=lease_owner):
                break
            entry = self._provider_entry(provider_media)
            source_photo_id = str(entry.get("source_photo_id") or "").strip()
            if not source_photo_id:
                continue
            existing = db.execute(
                select(IntakePhoto).where(
                    IntakePhoto.user_id == user.id,
                    IntakePhoto.source_provider == "google_photos",
                    IntakePhoto.source_photo_id == source_photo_id,
                )
            ).scalar_one_or_none()
            if existing:
                if self._source_fingerprint(entry) == self._stored_source_fingerprint(existing):
                    if not self._verify_image_file(existing.local_path):
                        self._update_existing_provider_photo(
                            db,
                            photo=existing,
                            entry=entry,
                            source_url=source_url,
                            user_id=user.id,
                        )
                        changed += 1
                        affected_media.append((existing.id, "stale_local_path_repaired"))
                    duplicates += 1
                    provider_media.processing_status = "processed"
                    provider_media.intake_photo_id = existing.id
                    provider_media.processing_lease_owner = None
                    provider_media.processing_lease_expires_at = None
                    db.add(provider_media)
                    continue
                self._update_existing_provider_photo(
                    db,
                    photo=existing,
                    entry=entry,
                    source_url=source_url,
                    user_id=user.id,
                )
                changed += 1
                affected_media.append((existing.id, "provider_changed"))
                provider_media.processing_status = "processed"
                provider_media.intake_photo_id = existing.id
                provider_media.processing_lease_owner = None
                provider_media.processing_lease_expires_at = None
                db.add(provider_media)
                continue
            logger.info("intake_new_image_detected", extra={"user_id": user.id, "source_photo_id": source_photo_id})
            try:
                local_path = self.storage.save_from_url(
                    str(entry.get("url") or ""),
                    prefix="intake-google-photos",
                    suggested_basename=str(entry.get("suggested_basename") or source_photo_id),
                )
            except Exception as exc:
                failed_downloads += 1
                download_errors.append({"source_photo_id": source_photo_id, "url": str(entry.get("url") or ""), "error": str(exc)})
                logger.warning(
                    "intake_image_download_failed",
                    extra={"user_id": user.id, "source_photo_id": source_photo_id, "error": str(exc)},
                )
                provider_media.processing_status = "retry"
                provider_media.processing_error = str(exc)[:2000]
                provider_media.retry_count = int(provider_media.retry_count or 0) + 1
                provider_media.processing_lease_owner = None
                provider_media.processing_lease_expires_at = None
                db.add(provider_media)
                continue
            logger.info("intake_image_downloaded", extra={"user_id": user.id, "source_photo_id": source_photo_id, "local_path": local_path})
            content_hash = self._hash_file(local_path)
            detection = self.classify_photo_for_intake(local_path)
            qr_payload = detection.get("detected_slate_payload") or (
                self.decode_slate_payload_isolated(local_path)
                if (detection.get("is_qr_candidate") or detection.get("is_probable_slate"))
                else None
            )
            metadata = {
                "album_url": source_url,
                "source_url": entry.get("url"),
                "source_order": entry.get("source_order"),
                "capture_time": entry.get("captured_at"),
                "slate_detection_version": SLATE_DETECTION_VERSION,
                "slate_detection_checked_at": datetime.now(UTC).isoformat(),
                "slate_detection_result": "matched" if qr_payload else ("probable_slate_candidate" if detection.get("is_probable_slate") else "no_match"),
                "slate_detection": detection,
                "timeline": self._timeline_metadata(entry),
                "source_fingerprint": self._source_fingerprint(entry),
            }
            photo = IntakePhoto(
                user_id=user.id,
                source_provider="google_photos",
                source_photo_id=source_photo_id,
                source_album_id=self._album_identifier(source_url),
                source_folder_id=str(settings_payload.get("folder_id") or "").strip() or None,
                original_filename=str(entry.get("original_filename") or "").strip() or None,
                local_path=local_path,
                downloaded_url=str(entry.get("url") or "").strip() or None,
                content_hash=content_hash,
                captured_at=self._parse_datetime(entry.get("captured_at")),
                uploaded_at=self._parse_datetime(entry.get("uploaded_at")),
                imported_at=datetime.now(UTC),
                image_type="slate" if qr_payload else ("slate_candidate" if detection.get("is_probable_slate") else "product"),
                is_slate=bool(qr_payload),
                is_public_listing_candidate=not bool(qr_payload) and not bool(detection.get("is_probable_slate")),
                is_internal_only=bool(qr_payload) or bool(detection.get("is_probable_slate")),
                metadata_json=metadata,
            )
            db.add(photo)
            db.flush()
            provider_media.processing_status = "processed"
            provider_media.processing_error = None
            provider_media.intake_photo_id = photo.id
            provider_media.processing_lease_owner = None
            provider_media.processing_lease_expires_at = None
            db.add(provider_media)
            imported += 1
            affected_media.append((photo.id, "provider_new"))
            if qr_payload:
                slates += 1
                self._upsert_slate_from_qr(db, user=user, qr_payload=qr_payload, photo=photo)
        db.commit()
        captured_times = [self._parse_datetime(entry.get("captured_at")) for entry in entries]
        captured_times = [value for value in captured_times if value is not None]
        source_state = db.get(IntakeSourceState, source_state.id) or source_state
        source_state.last_poll_completed_at = datetime.now(UTC)
        processing_backlog = self._provider_processing_backlog(db, user_id=user.id, source_state_id=source_state.id)
        budget_exhausted = processing_backlog > 0 and len(processing_records) >= run_budget
        source_state.enumerated_count = len(entries)
        source_state.new_count = imported
        source_state.changed_count = changed + discovery_changed
        source_state.skipped_budget_count = processing_backlog
        source_state.enumeration_complete = bool(enumeration.enumeration_complete)
        source_state.enumeration_interrupted = bool(enumeration.interrupted)
        source_state.discovery_persisted_count = len(discovered_by_source_id)
        source_state.provider_visible_count = enumeration.provider_item_count
        source_state.processing_backlog_count = processing_backlog
        source_state.processing_complete = processing_backlog == 0
        source_state.reconciliation_backlog_count = self._reconciliation_backlog(db, user_id=user.id, source_state_id=source_state.id)
        source_state.source_caught_up = bool(
            source_state.enumeration_complete
            and not source_state.enumeration_interrupted
            and source_state.processing_complete
            and source_state.reconciliation_backlog_count == 0
        )
        source_state.scan_complete = source_state.enumeration_complete
        source_state.last_complete_page = (
            "caught_up" if source_state.source_caught_up
            else "complete_processing_backlog" if source_state.enumeration_complete and processing_backlog
            else "complete" if source_state.scan_complete
            else ("interrupted" if enumeration.interrupted else "budget_exhausted" if budget_exhausted else "enumeration_incomplete")
        )
        source_state.last_full_enumeration_at = datetime.now(UTC) if enumeration.enumeration_complete else source_state.last_full_enumeration_at
        source_state.enumeration_status = source_state.last_complete_page
        source_state.enumeration_progress_json = {
            "scroll_rounds": enumeration.scroll_rounds,
            "provider_visible_count": enumeration.provider_item_count,
            "returned_entries": len(entries),
            "discovery_created": discovery_created,
            "discovery_changed": discovery_changed,
        }
        source_state.oldest_capture_at = min(captured_times) if captured_times else source_state.oldest_capture_at
        source_state.newest_capture_at = max(captured_times) if captured_times else source_state.newest_capture_at
        db.add(source_state)
        db.commit()
        recovered_slates = self.recover_existing_slates(db, user=user, limit=75)
        jobs: list[IntakeReconciliationJob] = []
        if affected_media:
            jobs.append(
                self.enqueue_reconciliation_interval_job(
                    db,
                    user_id=user.id,
                    source_state=source_state,
                    affected_media=affected_media,
                )
            )
        db.commit()
        # The durable job table is the source of truth. Processing immediately
        # keeps the current polling loop responsive without requiring an extra
        # broker round trip; the same claim/lease path is used by the worker.
        processed = self.process_reconciliation_jobs(
            db,
            user_id=user.id,
            worker_id="backend-intake-monitor",
            limit=max(1, len(jobs)),
        )
        stabilization = self.refresh_drafts_until_stable(db, user=user, max_passes=2)
        assigned = sum(int((job.get("result") or {}).get("assigned_photos") or 0) for job in processed)
        drafted = sum(int((job.get("result") or {}).get("drafts_created") or 0) for job in processed)
        source_state = db.get(IntakeSourceState, source_state.id) or source_state
        source_state.reconciliation_backlog_count = self._reconciliation_backlog(db, user_id=user.id, source_state_id=source_state.id)
        source_state.source_caught_up = bool(
            source_state.enumeration_complete
            and not source_state.enumeration_interrupted
            and source_state.processing_backlog_count == 0
            and source_state.reconciliation_backlog_count == 0
        )
        self._release_source_poll_lease(db, source_state=source_state, owner=lease_owner)
        if source_state.processing_backlog_count > 0:
            # The discovery transaction has committed. A successor worker
            # chunk drains the backlog without another operator import action.
            from app.workers.tasks import drain_intake_provider_media_task

            drain_intake_provider_media_task.apply_async(args=[user.id], countdown=2)
        self.save_settings(
            db=db,
            user=user,
            payload={
                **settings_payload,
                "last_synced_at": datetime.now(UTC).isoformat(),
                "last_imported_count": imported,
                "last_monitor_result": {
                    "scanned": len(entries),
                    "discovery_persisted_count": len(discovered_by_source_id),
                    "processing_backlog_count": source_state.processing_backlog_count,
                    "imported": imported,
                    "changed": changed,
                    "run_budget": run_budget,
                    "duplicates": duplicates,
                    "slates_detected": slates,
                    "assigned_photos": assigned,
                    "recovered_slates": recovered_slates,
                    "drafts_created": drafted,
                    "reconciliation_jobs": len(jobs),
                    "reconciliation_processed": len(processed),
                    "draft_stabilization": stabilization,
                    "failed_downloads": failed_downloads,
                    "remaining_unseen_estimate": max((enumeration.provider_item_count or len(entries)) - len(discovered_by_source_id), 0),
                    "download_errors": download_errors[:20],
                    "enumeration_complete": enumeration.enumeration_complete,
                    "enumeration_interrupted": enumeration.interrupted,
                    "enumeration_interruption_reason": enumeration.interruption_reason,
                    "scroll_rounds": enumeration.scroll_rounds,
                    "provider_item_count": enumeration.provider_item_count,
                },
                "last_error": None if failed_downloads == 0 else f"{failed_downloads} intake image downloads failed",
            },
        )
        logger.info(
            "intake_monitor_complete",
            extra={
                "user_id": user.id,
                "imported": imported,
                "changed": changed,
                "duplicates": duplicates,
                "slates": slates,
                "recovered_slates": recovered_slates,
                "drafted": drafted,
                "failed_downloads": failed_downloads,
            },
        )
        return {
            "scanned": len(entries),
            "imported": imported,
            "changed": changed,
            "duplicates": duplicates,
            "slates_detected": slates,
            "assigned_photos": assigned,
            "drafts_created": drafted,
            "reconciliation_jobs": len(jobs),
            "reconciliation_processed": len(processed),
            "draft_stabilization": stabilization,
            "failed_downloads": failed_downloads,
            "download_errors": download_errors,
            "enumeration_complete": enumeration.enumeration_complete,
            "enumeration_interrupted": enumeration.interrupted,
            "enumeration_interruption_reason": enumeration.interruption_reason,
            "discovery_persisted_count": len(discovered_by_source_id),
            "provider_visible_count": enumeration.provider_item_count,
            "processing_backlog_count": source_state.processing_backlog_count,
            "reconciliation_backlog_count": source_state.reconciliation_backlog_count,
            "processing_complete": source_state.processing_complete,
            "source_caught_up": source_state.source_caught_up,
        }

    def _timeline_metadata(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Persist provider-neutral ordering evidence alongside an immutable media observation."""
        return {
            "ordering_version": 1,
            "capture_time": entry.get("captured_at"),
            "provider_created_at": entry.get("uploaded_at") or entry.get("created_at"),
            "source_order": entry.get("source_order"),
            "original_filename": entry.get("original_filename"),
            "source_page_url": entry.get("source_page_url"),
        }

    def _timeline_sort_key(self, photo: IntakePhoto) -> tuple:
        """Capture chronology always wins over arrival order.

        Google Photos and future adapters can add higher-precision fields under
        metadata_json.timeline without changing the grouping engine.
        """
        metadata = photo.metadata_json if isinstance(photo.metadata_json, dict) else {}
        timeline = metadata.get("timeline") if isinstance(metadata.get("timeline"), dict) else {}
        capture = photo.captured_at or self._parse_datetime(timeline.get("capture_time"))
        provider_created = photo.uploaded_at or self._parse_datetime(timeline.get("provider_created_at"))
        filename = str(photo.original_filename or timeline.get("original_filename") or "").lower()
        source_order = timeline.get("source_order")
        try:
            source_order_key = int(source_order)
        except (TypeError, ValueError):
            source_order_key = 2**31 - 1
        # Prefixing each timestamp with a presence bit prevents unknown times
        # from being mistaken for early captures.
        return (
            0 if capture else 1,
            capture.astimezone(UTC).isoformat() if capture else "",
            source_order_key,
            self._natural_filename_key(filename),
            0 if provider_created else 1,
            provider_created.astimezone(UTC).isoformat() if provider_created else "",
            (photo.imported_at or photo.created_at or datetime.max.replace(tzinfo=UTC)).astimezone(UTC).isoformat() if (photo.imported_at or photo.created_at) else "",
            str(photo.source_photo_id or ""),
            photo.id or 0,
        )

    @staticmethod
    def _natural_filename_key(value: str) -> tuple:
        import re

        return tuple(int(token) if token.isdigit() else token for token in re.split(r"(\d+)", value or ""))

    def _ordered_photos(self, db: Session, *, user_id: int) -> list[IntakePhoto]:
        rows = db.execute(select(IntakePhoto).where(IntakePhoto.user_id == user_id)).scalars().all()
        return sorted(
            [row for row in rows if not self._timeline_photo_deleted(row)],
            key=self._timeline_sort_key,
        )

    @staticmethod
    def _timeline_photo_deleted(photo: IntakePhoto) -> bool:
        return bool((photo.metadata_json or {}).get("timeline_deleted"))

    @staticmethod
    def _verify_image_file(path: str | Path) -> bool:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
            return True
        except Exception:
            return False

    def repair_google_timeline_media(
        self,
        db: Session,
        *,
        user: User,
        limit: int = 250,
        force_provider_refresh: bool = True,
    ) -> dict[str, Any]:
        """Durably restore missing Google Timeline files without altering chronology."""
        rows = db.execute(
            select(IntakePhoto).where(
                IntakePhoto.user_id == user.id,
                IntakePhoto.source_provider == "google_photos",
            ).order_by(IntakePhoto.id.asc())
        ).scalars().all()
        active = [row for row in rows if not self._timeline_photo_deleted(row)]
        provider_rows = db.execute(
            select(IntakeProviderMedia).where(
                IntakeProviderMedia.user_id == user.id,
                IntakeProviderMedia.provider == "google_photos",
            )
        ).scalars().all()
        provider_by_id = {str(row.provider_media_id): row for row in provider_rows}
        report: dict[str, Any] = {
            "total_google_timeline_photos": len(active),
            "valid_local_before": 0,
            "repaired_local": 0,
            "provider_fallback_temporarily_used": 0,
            "stale_local_path_provider_available": 0,
            "stale_local_path_provider_failed": 0,
            "missing_both": 0,
            "invalid_image": 0,
            "failed_photo_ids": [],
        }
        repair_rows: list[IntakePhoto] = []
        for photo in active:
            path = Path(photo.local_path or "")
            if path.is_file():
                if self._verify_image_file(path):
                    report["valid_local_before"] += 1
                    continue
                report["invalid_image"] += 1
            repair_rows.append(photo)

        source_settings = self.settings_for_user(user)
        album_url = str(source_settings.get("album_url") or "").strip()
        entries_by_id: dict[str, dict[str, Any]] = {}
        provider_refreshed = False

        def current_urls(photo: IntakePhoto) -> list[str]:
            provider_row = provider_by_id.get(str(photo.source_photo_id))
            values = [
                (provider_row.provider_url if provider_row else None),
                (provider_row.preview_url if provider_row else None),
                photo.downloaded_url,
            ]
            seen: set[str] = set()
            result = []
            for value in values:
                url = str(value or "").strip()
                if url and url not in seen:
                    seen.add(url)
                    result.append(url)
            return result

        def refresh_provider_entries() -> None:
            nonlocal provider_refreshed, entries_by_id
            if provider_refreshed or not force_provider_refresh or not album_url:
                return
            provider_refreshed = True
            try:
                enumeration = self.google_photos.enumerate_photo_entries(
                    album_url,
                    overall_timeout_seconds=int(source_settings.get("provider_overall_timeout_seconds") or 180),
                )
                entries_by_id = {
                    str(entry.get("source_photo_id") or ""): entry
                    for entry in (enumeration.entries or [])
                    if entry.get("source_photo_id") and entry.get("url")
                }
                report["provider_enumerated"] = len(entries_by_id)
                report["provider_enumeration_complete"] = bool(enumeration.enumeration_complete)
            except Exception as exc:
                report["provider_enumeration_error"] = str(exc)[:300]

        refreshed_count = 0
        for photo in repair_rows[: max(1, int(limit))]:
            provider_row = provider_by_id.get(str(photo.source_photo_id))
            urls = current_urls(photo)
            fresh_entry = entries_by_id.get(str(photo.source_photo_id))
            if fresh_entry and fresh_entry.get("url"):
                urls.insert(0, str(fresh_entry["url"]))
            success_path = None
            success_url = None
            for url in dict.fromkeys(urls):
                try:
                    candidate = self.storage.save_from_url(
                        url,
                        prefix="intake-google-photos",
                        suggested_basename=str(photo.source_photo_id or photo.id),
                    )
                    if self._verify_image_file(candidate):
                        success_path, success_url = candidate, url
                        break
                    report["invalid_image"] += 1
                except Exception:
                    continue
            if success_path is None and not provider_refreshed:
                refresh_provider_entries()
                fresh_entry = entries_by_id.get(str(photo.source_photo_id))
                fresh_url = str((fresh_entry or {}).get("url") or "").strip()
                if fresh_url and fresh_url not in urls:
                    try:
                        candidate = self.storage.save_from_url(
                            fresh_url,
                            prefix="intake-google-photos",
                            suggested_basename=str(photo.source_photo_id or photo.id),
                        )
                        if self._verify_image_file(candidate):
                            success_path, success_url = candidate, fresh_url
                        else:
                            report["invalid_image"] += 1
                    except Exception:
                        pass
            if success_path:
                metadata = dict(photo.metadata_json or {})
                metadata["timeline_media_repair"] = {
                    "status": "repaired",
                    "repaired_at": datetime.now(UTC).isoformat(),
                    "storage_root_current": True,
                    "source_provider": "google_photos",
                    "provider_media_id": photo.source_photo_id,
                }
                photo.local_path = success_path
                photo.downloaded_url = success_url or photo.downloaded_url
                photo.content_hash = self._hash_file(success_path)
                photo.metadata_json = metadata
                db.add(photo)
                report["repaired_local"] += 1
                refreshed_count += 1
                continue
            provider_values = current_urls(photo)
            if not provider_values and photo.source_photo_id not in entries_by_id:
                report["missing_both"] += 1
            else:
                report["stale_local_path_provider_failed"] += 1
                report["failed_photo_ids"].append(photo.id)
            if provider_values:
                report["stale_local_path_provider_available"] += 1
                report["provider_fallback_temporarily_used"] += 1
            metadata = dict(photo.metadata_json or {})
            metadata["timeline_media_repair"] = {
                "status": "unavailable",
                "checked_at": datetime.now(UTC).isoformat(),
                "provider_url_present": bool(provider_values or photo.source_photo_id in entries_by_id),
            }
            photo.metadata_json = metadata
            db.add(photo)
        db.commit()
        report["processed_for_repair"] = min(len(repair_rows), max(1, int(limit)))
        report["remaining_unprocessed"] = max(0, len(repair_rows) - report["processed_for_repair"])
        return report

    def _source_state_for(self, db: Session, *, user_id: int, provider: str, source_key: str) -> IntakeSourceState:
        state = db.execute(
            select(IntakeSourceState).where(
                IntakeSourceState.user_id == user_id,
                IntakeSourceState.provider == provider,
                IntakeSourceState.source_key == source_key,
            )
        ).scalar_one_or_none()
        if state is None:
            state = IntakeSourceState(user_id=user_id, provider=provider, source_key=source_key)
            db.add(state)
            db.flush()
        return state

    def _claim_source_poll_lease(self, db: Session, *, source_state: IntakeSourceState, owner: str, seconds: int = 300) -> bool:
        """Acquire one database-backed enumeration lease per provider source."""
        # PostgreSQL deployment uses timestamp-without-time-zone for the
        # existing intake tables, so compare leases in that same UTC-naive form.
        now = datetime.utcnow()
        state = db.get(IntakeSourceState, source_state.id) or source_state
        expires = state.poll_lease_expires_at
        if state.poll_lease_owner and expires and expires > now and state.poll_lease_owner != owner:
            return False
        state.poll_lease_owner = owner
        state.poll_lease_acquired_at = now
        state.poll_lease_heartbeat_at = now
        state.poll_lease_expires_at = now + timedelta(seconds=seconds)
        state.poll_cancellation_requested = False
        state.enumeration_status = "running"
        db.add(state)
        db.commit()
        return True

    def _release_source_poll_lease(self, db: Session, *, source_state: IntakeSourceState, owner: str, error: str | None = None) -> None:
        state = db.get(IntakeSourceState, source_state.id) or source_state
        if state.poll_lease_owner != owner:
            return
        state.poll_lease_owner = None
        state.poll_lease_expires_at = None
        state.poll_lease_heartbeat_at = datetime.utcnow()
        if error:
            state.poll_error = error[:2000]
            state.consecutive_failures = int(state.consecutive_failures or 0) + 1
            state.enumeration_status = "failed"
        else:
            state.poll_error = None
            state.consecutive_failures = 0
        db.add(state)
        db.commit()

    def _heartbeat_source_poll_lease(self, db: Session, *, source_state: IntakeSourceState, owner: str, seconds: int = 300) -> bool:
        state = db.get(IntakeSourceState, source_state.id) or source_state
        if state.poll_lease_owner != owner or state.poll_cancellation_requested:
            return False
        now = datetime.utcnow()
        state.poll_lease_heartbeat_at = now
        state.poll_lease_expires_at = now + timedelta(seconds=seconds)
        db.add(state)
        db.commit()
        return True

    def _persist_provider_discoveries(
        self,
        db: Session,
        *,
        user: User,
        source_state: IntakeSourceState,
        source_url: str,
        entries: list[dict[str, Any]],
    ) -> tuple[dict[str, IntakeProviderMedia], int, int]:
        """Durably record every enumerated provider item before asset work begins."""
        records: dict[str, IntakeProviderMedia] = {}
        created = changed = 0
        for entry in entries:
            source_photo_id = str(entry.get("source_photo_id") or "").strip()
            if not source_photo_id:
                continue
            fingerprint = self._source_fingerprint(entry)
            record = db.execute(
                select(IntakeProviderMedia).where(
                    IntakeProviderMedia.user_id == user.id,
                    IntakeProviderMedia.provider == "google_photos",
                    IntakeProviderMedia.source_key == source_state.source_key,
                    IntakeProviderMedia.provider_media_id == source_photo_id,
                )
            ).scalar_one_or_none()
            if record is None:
                record = IntakeProviderMedia(
                    user_id=user.id,
                    source_state_id=source_state.id,
                    provider="google_photos",
                    source_key=source_state.source_key,
                    provider_media_id=source_photo_id,
                    metadata_fingerprint=fingerprint,
                    processing_status="discovered",
                    first_seen_at=datetime.now(UTC),
                )
                created += 1
            elif record.metadata_fingerprint != fingerprint:
                record.metadata_fingerprint = fingerprint
                if record.processing_status == "processed":
                    record.processing_status = "changed"
                changed += 1
            record.provider_url = str(entry.get("url") or "") or None
            record.preview_url = str(entry.get("preview_url") or entry.get("url") or "") or None
            record.original_filename = str(entry.get("original_filename") or "") or None
            record.provider_order = entry.get("source_order") if isinstance(entry.get("source_order"), int) else None
            record.captured_at = self._parse_datetime(entry.get("captured_at"))
            record.uploaded_at = self._parse_datetime(entry.get("uploaded_at"))
            record.discovery_generation = int(source_state.enumeration_generation or 0)
            record.last_seen_at = datetime.now(UTC)
            record.metadata_json = {"entry": entry, "source_url": source_url}
            existing_photo = db.execute(
                select(IntakePhoto).where(
                    IntakePhoto.user_id == user.id,
                    IntakePhoto.source_provider == "google_photos",
                    IntakePhoto.source_photo_id == source_photo_id,
                )
            ).scalar_one_or_none()
            if existing_photo and record.processing_status == "discovered":
                record.processing_status = "processed"
                record.intake_photo_id = existing_photo.id
            db.add(record)
            records[source_photo_id] = record
        db.commit()
        return records, created, changed

    @staticmethod
    def _provider_entry(provider_media: IntakeProviderMedia) -> dict[str, Any]:
        metadata = provider_media.metadata_json if isinstance(provider_media.metadata_json, dict) else {}
        entry = metadata.get("entry") if isinstance(metadata.get("entry"), dict) else {}
        return {**entry, "source_photo_id": provider_media.provider_media_id, "url": entry.get("url") or provider_media.provider_url}

    def _claim_provider_media_for_processing(
        self,
        db: Session,
        *,
        user_id: int,
        source_state: IntakeSourceState,
        worker_id: str,
        limit: int,
    ) -> list[IntakeProviderMedia]:
        now = datetime.utcnow()
        statement = (
            select(IntakeProviderMedia)
            .where(
                IntakeProviderMedia.user_id == user_id,
                IntakeProviderMedia.source_state_id == source_state.id,
                IntakeProviderMedia.processing_status.in_(["discovered", "changed", "retry"]),
                or_(IntakeProviderMedia.processing_lease_expires_at.is_(None), IntakeProviderMedia.processing_lease_expires_at < now),
            )
            .order_by(IntakeProviderMedia.id.asc())
            .limit(max(1, limit))
        )
        # PostgreSQL workers claim rows with SKIP LOCKED; SQLite fixtures use
        # the same deterministic query without a backend-specific lock clause.
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        rows = db.execute(statement).scalars().all()
        for row in rows:
            row.processing_status = "processing"
            row.processing_lease_owner = worker_id
            row.processing_lease_expires_at = now + timedelta(minutes=10)
            db.add(row)
        db.commit()
        return rows

    @staticmethod
    def _provider_processing_backlog(db: Session, *, user_id: int, source_state_id: int) -> int:
        return int(db.execute(
            select(func.count(IntakeProviderMedia.id)).where(
                IntakeProviderMedia.user_id == user_id,
                IntakeProviderMedia.source_state_id == source_state_id,
                IntakeProviderMedia.processing_status.in_(["discovered", "changed", "retry", "processing"]),
            )
        ).scalar() or 0)

    @staticmethod
    def _reconciliation_backlog(db: Session, *, user_id: int, source_state_id: int) -> int:
        return int(db.execute(
            select(func.count(IntakeReconciliationJob.id)).where(
                IntakeReconciliationJob.user_id == user_id,
                IntakeReconciliationJob.source_state_id == source_state_id,
                IntakeReconciliationJob.status.in_(["queued", "running", "retry"]),
            )
        ).scalar() or 0)

    @staticmethod
    def _source_fingerprint(entry: dict[str, Any]) -> str:
        """Detect provider metadata changes without treating arrival time as chronology."""
        stable = {
            "source_photo_id": str(entry.get("source_photo_id") or ""),
            "url": str(entry.get("url") or ""),
            "captured_at": str(entry.get("captured_at") or ""),
            "uploaded_at": str(entry.get("uploaded_at") or ""),
            "original_filename": str(entry.get("original_filename") or ""),
        }
        return hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _stored_source_fingerprint(photo: IntakePhoto) -> str:
        metadata = photo.metadata_json if isinstance(photo.metadata_json, dict) else {}
        return str(metadata.get("source_fingerprint") or "")

    def _update_existing_provider_photo(
        self,
        db: Session,
        *,
        photo: IntakePhoto,
        entry: dict[str, Any],
        source_url: str,
        user_id: int,
    ) -> None:
        """Upsert changed provider metadata and retain the existing observation ID."""
        previous_metadata = dict(photo.metadata_json or {})
        prior_url = str(photo.downloaded_url or "")
        next_url = str(entry.get("url") or "").strip()
        if next_url and (next_url != prior_url or not self._verify_image_file(photo.local_path)):
            try:
                local_path = self.storage.save_from_url(
                    next_url,
                    prefix="intake-google-photos",
                    suggested_basename=str(entry.get("suggested_basename") or photo.source_photo_id),
                )
                photo.local_path = local_path
                photo.downloaded_url = next_url
                photo.content_hash = self._hash_file(local_path)
                detection = self.classify_photo_for_intake(local_path)
                qr_payload = self.decode_slate_payload_isolated(local_path) if detection.get("is_qr_candidate") else None
                if qr_payload:
                    photo.is_slate = True
                    photo.is_internal_only = True
                    photo.is_public_listing_candidate = False
                    photo.image_type = "slate"
                    user = db.get(User, user_id)
                    if user:
                        self._upsert_slate_from_qr(db, user=user, qr_payload=qr_payload, photo=photo)
            except Exception as exc:
                previous_metadata["provider_update_download_error"] = str(exc)
                logger.warning("intake_provider_media_refresh_failed", extra={"user_id": user_id, "photo_id": photo.id, "error": str(exc)})
        photo.captured_at = self._parse_datetime(entry.get("captured_at")) or photo.captured_at
        photo.uploaded_at = self._parse_datetime(entry.get("uploaded_at")) or photo.uploaded_at
        photo.original_filename = str(entry.get("original_filename") or "").strip() or photo.original_filename
        previous_metadata.update(
            {
                "album_url": source_url,
                "source_url": entry.get("url"),
                "source_order": entry.get("source_order"),
                "timeline": self._timeline_metadata(entry),
                "source_fingerprint": self._source_fingerprint(entry),
                "provider_changed_at": datetime.now(UTC).isoformat(),
            }
        )
        photo.metadata_json = previous_metadata
        db.add(photo)

    def enqueue_reconciliation_job(
        self,
        db: Session,
        *,
        user_id: int,
        source_state: IntakeSourceState | None,
        photo_id: int,
        event_kind: str,
    ) -> IntakeReconciliationJob:
        photo = db.get(IntakePhoto, photo_id)
        if not photo or photo.user_id != user_id:
            raise ValueError("Intake media observation not found.")
        fingerprint = self._stored_source_fingerprint(photo) or str(photo.content_hash or "")
        idempotency_key = hashlib.sha256(f"{event_kind}:{photo.id}:{fingerprint}".encode("utf-8")).hexdigest()
        existing = db.execute(
            select(IntakeReconciliationJob).where(
                IntakeReconciliationJob.user_id == user_id,
                IntakeReconciliationJob.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        job = IntakeReconciliationJob(
            user_id=user_id,
            source_state_id=source_state.id if source_state else None,
            source_media_id=photo.id,
            job_type="reconcile_media",
            idempotency_key=idempotency_key,
            interval_key=f"media:{photo.id}",
            status="queued",
            payload_json={"event_kind": event_kind, "source_photo_id": photo.source_photo_id},
            run_after=datetime.now(UTC),
        )
        db.add(job)
        db.flush()
        return job

    def enqueue_reconciliation_interval_job(
        self,
        db: Session,
        *,
        user_id: int,
        source_state: IntakeSourceState,
        affected_media: list[tuple[int, str]],
    ) -> IntakeReconciliationJob:
        """Collapse one provider poll into a single recoverable reconciliation job."""
        photo_ids = sorted({int(photo_id) for photo_id, _event_kind in affected_media})
        observations = [db.get(IntakePhoto, photo_id) for photo_id in photo_ids]
        observations = [photo for photo in observations if photo and photo.user_id == user_id]
        if not observations:
            raise ValueError("No valid provider observations were supplied for reconciliation.")
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    {
                        "id": photo.id,
                        "fingerprint": self._stored_source_fingerprint(photo),
                    }
                    for photo in observations
                ],
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        idempotency_key = hashlib.sha256(
            f"provider_poll:{source_state.id}:{fingerprint}".encode("utf-8")
        ).hexdigest()
        existing = db.execute(
            select(IntakeReconciliationJob).where(
                IntakeReconciliationJob.user_id == user_id,
                IntakeReconciliationJob.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        latest = max(observations, key=self._timeline_sort_key)
        job = IntakeReconciliationJob(
            user_id=user_id,
            source_state_id=source_state.id,
            source_media_id=latest.id,
            job_type="reconcile_provider_poll",
            idempotency_key=idempotency_key,
            interval_key=f"source:{source_state.id}:poll",
            status="queued",
            payload_json={
                "event_kinds": sorted({kind for _photo_id, kind in affected_media}),
                "affected_photo_ids": photo_ids,
                "source_media_id": latest.id,
            },
            run_after=datetime.now(UTC),
        )
        db.add(job)
        db.flush()
        return job

    def _claim_reconciliation_job(self, db: Session, *, user_id: int | None, worker_id: str) -> IntakeReconciliationJob | None:
        now = datetime.now(UTC)
        query = select(IntakeReconciliationJob).where(
            IntakeReconciliationJob.status.in_(["queued", "retry", "running"]),
            or_(IntakeReconciliationJob.run_after.is_(None), IntakeReconciliationJob.run_after <= now),
            or_(IntakeReconciliationJob.lease_expires_at.is_(None), IntakeReconciliationJob.lease_expires_at < now),
        ).order_by(IntakeReconciliationJob.created_at.asc(), IntakeReconciliationJob.id.asc())
        if user_id is not None:
            query = query.where(IntakeReconciliationJob.user_id == user_id)
        job = db.execute(query.with_for_update(skip_locked=True)).scalars().first()
        if job is None:
            return None
        job.status = "running"
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(minutes=5)
        job.heartbeat_at = now
        job.progress = max(job.progress or 0, 5)
        db.add(job)
        db.commit()
        return job

    def process_reconciliation_jobs(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        worker_id: str,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Claim and complete durable jobs. Safe for monitor and Celery workers alike."""
        results: list[dict[str, Any]] = []
        for _ in range(max(1, limit)):
            job = self._claim_reconciliation_job(db, user_id=user_id, worker_id=worker_id)
            if job is None:
                break
            try:
                reconciliation = self.reconcile_timeline(
                    db,
                    user_id=job.user_id,
                    photo_id=job.source_media_id,
                )
                drafts_updated = self.refresh_drafts_for_reconciled_items(
                    db,
                    user_id=job.user_id,
                    item_ids=(reconciliation.get("result") or {}).get("affected_item_ids") or [],
                )
                job.status = "completed"
                job.progress = 100
                job.result_json = {**reconciliation, "drafts_created": drafts_updated}
                job.acknowledged_at = datetime.now(UTC)
                job.lease_owner = None
                job.lease_expires_at = None
                job.heartbeat_at = datetime.now(UTC)
                db.add(job)
                if job.source_state_id:
                    state = db.get(IntakeSourceState, job.source_state_id)
                    if state:
                        state.last_successful_poll_at = datetime.now(UTC)
                        state.poll_error = None
                        state.consecutive_failures = 0
                        db.add(state)
                db.commit()
                results.append({"job_id": job.id, "result": job.result_json})
            except Exception as exc:
                db.rollback()
                job = db.get(IntakeReconciliationJob, job.id)
                if job is None:
                    continue
                job.retry_count = int(job.retry_count or 0) + 1
                job.last_error = str(exc)
                job.lease_owner = None
                job.lease_expires_at = None
                job.heartbeat_at = datetime.now(UTC)
                job.status = "dead_letter" if job.retry_count >= job.max_retries else "retry"
                job.run_after = datetime.now(UTC) + timedelta(seconds=min(300, 15 * (2 ** min(job.retry_count, 4))))
                db.add(job)
                if job.source_state_id:
                    state = db.get(IntakeSourceState, job.source_state_id)
                    if state:
                        state.poll_error = str(exc)
                        state.consecutive_failures = int(state.consecutive_failures or 0) + 1
                        db.add(state)
                db.commit()
                logger.exception("intake_reconciliation_job_failed", extra={"job_id": job.id, "user_id": job.user_id})
                results.append({"job_id": job.id, "error": str(exc), "status": job.status})
        return results

    def rebuild_batches_for_user(self, db: Session, *, user_id: int) -> dict[str, Any]:
        photos = self._ordered_photos(db, user_id=user_id)
        batches_by_item: dict[str, IntakePhotoBatch] = {}
        closed_item_ids: set[str] = set()
        slates_by_item: dict[str, IntakeSlate] = {
            slate.item_id: slate
            for slate in db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id)).scalars().all()
        }

        # Stream grouping is the heart of intake. We walk the full ordered photo stream and
        # keep a single current item boundary open. Every photo after a slate belongs to that
        # slate's item until the next slate appears. Rebuilding from the persisted stream keeps
        # grouping deterministic across restarts and manual corrections.
        current_item_id: str | None = None
        assigned = 0
        pending_unassigned: list[IntakePhoto] = []
        for photo in photos:
            if photo.is_slate:
                previous_item_id = current_item_id
                qr_payload = self._coerce_qr_payload(photo.metadata_json)
                item_id = str((qr_payload or {}).get("item_id") or photo.item_id or "").strip()
                boundary_position = self._boundary_position_from_payload(qr_payload)
                current_item_id = item_id or None
                if previous_item_id and current_item_id and previous_item_id != current_item_id:
                    previous_batch = batches_by_item.get(previous_item_id)
                    if previous_batch is None:
                        previous_batch = self._ensure_batch(
                            db,
                            user_id=user_id,
                            item_id=previous_item_id,
                            slate=slates_by_item.get(previous_item_id),
                        )
                        batches_by_item[previous_item_id] = previous_batch
                    previous_batch.metadata_json = {
                        **(previous_batch.metadata_json or {}),
                        "stream_closed": True,
                        "closed_by_boundary_photo_id": photo.id,
                    }
                    db.add(previous_batch)
                    closed_item_ids.add(previous_item_id)
                if not current_item_id:
                    photo.item_id = None
                    photo.batch_id = None
                    db.add(photo)
                    continue
                photo.item_id = current_item_id
                batch = batches_by_item.get(current_item_id)
                if batch is None:
                    batch = self._ensure_batch(db, user_id=user_id, item_id=current_item_id, slate=slates_by_item.get(current_item_id))
                    batches_by_item[current_item_id] = batch
                if boundary_position == "tail":
                    if pending_unassigned:
                        for pending_photo in pending_unassigned:
                            pending_metadata = dict(pending_photo.metadata_json or {})
                            pending_metadata["tail_assigned_item_id"] = current_item_id
                            pending_photo.metadata_json = pending_metadata
                            pending_photo.item_id = current_item_id
                            pending_photo.batch_id = batch.id
                            pending_photo.is_public_listing_candidate = not bool(pending_photo.is_internal_only)
                            pending_photo.image_type = pending_photo.image_type or "product"
                            assigned += 1
                            db.add(pending_photo)
                        batch.metadata_json = {
                            **(batch.metadata_json or {}),
                            "tail_boundary_used": True,
                            "tail_boundary_photo_ids": [pending_photo.id for pending_photo in pending_unassigned],
                        }
                        pending_unassigned = []
                    elif any((row.batch_id and row.batch_id != batch.id and not row.is_slate) for row in photos if row.id < photo.id):
                        batch.metadata_json = {
                            **(batch.metadata_json or {}),
                            "tail_boundary_conflict": True,
                        }
                photo.batch_id = batch.id
                photo.is_public_listing_candidate = False
                photo.is_internal_only = True
                photo.image_type = "slate"
                db.add(photo)
                continue
            manual_item_id = self._manual_item_id(photo.metadata_json)
            if manual_item_id:
                batch = batches_by_item.get(manual_item_id)
                if batch is None:
                    batch = self._ensure_batch(db, user_id=user_id, item_id=manual_item_id, slate=slates_by_item.get(manual_item_id))
                    batches_by_item[manual_item_id] = batch
                photo.item_id = manual_item_id
                photo.batch_id = batch.id
                photo.is_public_listing_candidate = not bool(photo.is_internal_only)
                photo.image_type = photo.image_type or "product"
                assigned += 1
                db.add(photo)
                continue
            if current_item_id:
                batch = batches_by_item.get(current_item_id)
                if batch is None:
                    batch = self._ensure_batch(db, user_id=user_id, item_id=current_item_id, slate=slates_by_item.get(current_item_id))
                    batches_by_item[current_item_id] = batch
                photo.item_id = current_item_id
                photo.batch_id = batch.id
                photo.is_public_listing_candidate = not bool(photo.is_internal_only)
                photo.image_type = photo.image_type or "product"
                assigned += 1
            else:
                photo.item_id = None
                photo.batch_id = None
                if not photo.is_slate:
                    photo.image_type = photo.image_type or "unassigned"
                    pending_unassigned.append(photo)
            db.add(photo)
        db.flush()

        all_batches = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == user_id)).scalars().all()
        for batch in all_batches:
            batch_photos = [photo for photo in photos if photo.batch_id == batch.id and not self._timeline_photo_deleted(photo)]
            batch.photo_count = len(batch_photos)
            batch.public_photo_count = len([photo for photo in batch_photos if photo.is_public_listing_candidate and not photo.is_slate])
            batch.internal_photo_count = len([photo for photo in batch_photos if photo.is_internal_only or photo.is_slate])
            batch.first_photo_id = batch_photos[0].id if batch_photos else None
            batch.last_photo_id = batch_photos[-1].id if batch_photos else None
            batch.metadata_json = self._batch_state_snapshot(
                batch,
                batch_photos,
                stream_closed=batch.item_id in closed_item_ids,
            )
            batch.status = self._batch_status(batch, batch_photos)
            db.add(batch)
        db.commit()
        return {"assigned_photos": assigned, "batch_count": len(all_batches)}

    def reconcile_timeline(
        self,
        db: Session,
        *,
        user_id: int,
        photo_id: int | None = None,
        full_integrity_scan: bool = False,
    ) -> dict[str, Any]:
        """Reconcile the slate-bounded interval affected by a changed observation.

        The compatibility projection is rebuilt deterministically today. The
        interval is still persisted so the worker/UI can explain exactly why a
        reconciliation occurred and later narrow the physical work further.
        """
        trigger_photo_id = photo_id
        photos = self._ordered_photos(db, user_id=user_id)
        target_index = next((index for index, photo in enumerate(photos) if photo.id == trigger_photo_id), None)
        if trigger_photo_id and target_index is None:
            raise ValueError("Intake photo not found.")
        if full_integrity_scan or target_index is None:
            start_index, end_index = 0, max(len(photos) - 1, 0)
            event_type = "full_integrity_scan"
        else:
            start_index = target_index
            while start_index > 0 and not photos[start_index].is_slate:
                start_index -= 1
            end_index = target_index
            while end_index < len(photos) - 1 and not photos[end_index + 1].is_slate:
                end_index += 1
            if end_index < len(photos) - 1:
                end_index += 1  # Include the following boundary.
            event_type = "bounded_timeline_reconciliation"
        interval = photos[start_index : end_index + 1] if photos else []
        before_assignments = {
            photo.id: {"item_id": photo.item_id, "batch_id": photo.batch_id}
            for photo in interval
        }
        result = self.rebuild_batches_for_user(db, user_id=user_id)
        refreshed = {photo.id: photo for photo in self._ordered_photos(db, user_id=user_id)}
        changed_photo_ids: list[int] = []
        affected_item_ids: set[str] = set()
        for interval_photo_id, before in before_assignments.items():
            photo = refreshed.get(interval_photo_id)
            if photo is None:
                continue
            if before["item_id"]:
                affected_item_ids.add(str(before["item_id"]))
            if photo.item_id:
                affected_item_ids.add(str(photo.item_id))
            if before != {"item_id": photo.item_id, "batch_id": photo.batch_id}:
                changed_photo_ids.append(interval_photo_id)
        target = refreshed.get(trigger_photo_id) if trigger_photo_id else None
        if target and target.item_id:
            affected_item_ids.add(str(target.item_id))
        result = {
            **result,
            "affected_item_ids": sorted(affected_item_ids),
            "moved_photo_ids": changed_photo_ids,
        }
        event = IntakeReconciliationEvent(
            user_id=user_id,
            event_type=event_type,
            status="completed",
            source_media_id=trigger_photo_id,
            interval_json={
                "first_photo_id": interval[0].id if interval else None,
                "last_photo_id": interval[-1].id if interval else None,
                "photo_count": len(interval),
                "ordering": "capture_time_then_stable_fallbacks",
            },
            details_json={
                **result,
                "assignment_changes": len(changed_photo_ids),
                "late_arrival": bool(
                    target and target.captured_at and target.imported_at
                    and self._as_utc(target.captured_at) < self._as_utc(target.imported_at)
                ),
            },
        )
        db.add(event)
        target_is_late = bool(
            target
            and target.item_id
            and target.captured_at
            and target.imported_at
            and self._as_utc(target.captured_at) < self._as_utc(target.imported_at)
        )
        if target_is_late:
            item = self._canonical_item_for(db, user_id=user_id, item_id=target.item_id)
            target_batch = db.get(IntakePhotoBatch, target.batch_id) if target and target.batch_id else None
            if target_batch is not None:
                target_photos = sorted(
                    db.execute(select(IntakePhoto).where(IntakePhoto.batch_id == target_batch.id)).scalars().all(),
                    key=self._timeline_sort_key,
                )
                target_batch.metadata_json = self._batch_state_snapshot(
                    target_batch,
                    target_photos,
                    stream_closed=self._batch_is_closed(target_batch),
                    draft_state="RECONCILING",
                    reconciliation_required=True,
                )
                db.add(target_batch)
            self._create_intake_notification(
                db,
                user_id=user_id,
                canonical_item_id=item.id,
                notification_type="late_photo_added",
                title="Late intake photo added to an existing item",
                message=f"{target.item_id} received a photo placed by capture time, not upload order.",
                href="/intake/queue",
            )
        db.commit()
        return {"event_id": event.id, "interval": event.interval_json, "result": result}

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def refresh_drafts_for_reconciled_items(self, db: Session, *, user_id: int, item_ids: list[str]) -> int:
        """Create missing drafts and refresh only media for existing draft listings.

        Listing copy is deliberately left alone here. Late media must update the
        review draft without overwriting an operator's title, description, or
        locked facts. A deliberate regeneration remains an operator action.
        """
        created = self.create_drafts_for_ready_batches(
            db,
            user_id=user_id,
            max_new_items_per_run=self._parse_optional_int(self.settings_for_user(db.get(User, user_id)).get("max_new_items_per_run")),
        )
        refreshed = 0
        for item_id in {str(value).strip() for value in item_ids if str(value).strip()}:
            batch = db.execute(
                select(IntakePhotoBatch).where(
                    IntakePhotoBatch.user_id == user_id,
                    IntakePhotoBatch.item_id == item_id,
                )
            ).scalar_one_or_none()
            if not batch or not batch.draft_listing_id:
                continue
            slate = db.execute(
                select(IntakeSlate).where(IntakeSlate.user_id == user_id, IntakeSlate.item_id == item_id)
            ).scalar_one_or_none()
            photos = sorted(
                db.execute(select(IntakePhoto).where(IntakePhoto.batch_id == batch.id)).scalars().all(),
                key=self._timeline_sort_key,
            )
            public_photos = [photo for photo in photos if photo.is_public_listing_candidate and not photo.is_slate and not self._timeline_photo_deleted(photo)]
            if not public_photos:
                continue
            listing = db.get(Listing, batch.draft_listing_id)
            if listing is None:
                continue
            if self._listing_is_externally_active(db, listing):
                self._record_external_listing_reconciliation_review(
                    db,
                    listing=listing,
                    slate=slate,
                    batch=batch,
                    photos=public_photos,
                )
                continue
            batch.metadata_json = self._batch_state_snapshot(
                batch,
                photos,
                stream_closed=self._batch_is_closed(batch),
                draft_state="RECONCILING",
                reconciliation_required=True,
            )
            db.add(batch)
            public_urls, listing_images = self._materialize_listing_images(
                item_id=item_id,
                title=listing.title or (slate.title if slate else item_id),
                photos=public_photos,
            )
            listing.image_urls = public_urls
            listing.listing_images = normalize_listing_images(listing_images=listing_images, approved=True)
            source_metadata = dict(listing.source_metadata or {})
            intake_metadata = dict(source_metadata.get("intake") or {})
            intake_metadata["photo_ids"] = [photo.id for photo in public_photos]
            intake_metadata["last_reconciled_at"] = datetime.now(UTC).isoformat()
            source_metadata["intake"] = intake_metadata
            listing.source_metadata = source_metadata
            db.add(listing)
            item = self._canonical_item_for(db, user_id=user_id, item_id=item_id, slate=slate)
            self._create_intake_notification(
                db,
                user_id=user_id,
                canonical_item_id=item.id,
                notification_type="draft_media_updated",
                title="Intake draft updated with reconciled photos",
                message=f"{item_id} received newly reconciled product photos. Listing text was preserved for review.",
                href=f"/listings/{listing.id}",
            )
            batch.metadata_json = self._batch_state_snapshot(
                batch,
                photos,
                stream_closed=self._batch_is_closed(batch),
                draft_state="DRAFT_CREATED",
                reconciliation_required=False,
            )
            db.add(batch)
            refreshed += 1
        db.commit()
        return created + refreshed

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value) or "").upper()

    def _listing_is_externally_active(self, db: Session, listing: Listing) -> bool:
        if listing.ebay_listing_id or listing.sold_at:
            return True
        if self._enum_value(listing.ebay_publish_status) in {"POSTING", "POSTED"}:
            return True
        if self._enum_value(listing.status) in {"POSTED", "PUBLISHED"}:
            return True
        marketplace_statuses = db.execute(
            select(MarketplaceListing.status).where(MarketplaceListing.listing_id == listing.id)
        ).scalars().all()
        return any(self._enum_value(status) in {"PENDING", "PUBLISHED", "UPDATED", "SOLD", "CLOSED"} for status in marketplace_statuses)

    def _is_clearly_bad_google_photos_listing(self, db: Session, listing: Listing) -> bool:
        source_type = str(listing.source_type or "").strip().lower()
        if source_type not in {"google_photos_album", "media_inventory_recovery"}:
            return False
        if self._listing_is_externally_active(db, listing):
            return False
        title = str(listing.title or "").strip().lower()
        description = str(listing.description or "").strip().lower()
        category = str(listing.category_suggestion or "").strip().lower()
        text = " ".join(part for part in (title, description, category) if part).strip()
        if not text:
            return True
        generic_markers = (
            "google photos intake draft",
            "generated from monitored google photos album intake",
            "recovered from preserved inventory photos",
            "recovered photographed inventory item",
            "item needs operator review",
            "posterpro slate",
            "general resale > identity review required",
            "collectibles > cameras",
        )
        if any(marker in text for marker in generic_markers):
            return True
        if len(listing.image_urls or []) <= 1 and "review the attached photos" in text and not title:
            return True
        return False

    def _delete_listing_graph(self, db: Session, *, listing: Listing) -> dict[str, Any]:
        media_cleanup = purge_listing_media(db, listing)
        db.execute(update(MediaRecoveryItemGroup).where(MediaRecoveryItemGroup.draft_listing_id == listing.id).values(draft_listing_id=None))
        db.execute(update(IntakePhotoBatch).where(IntakePhotoBatch.draft_listing_id == listing.id).values(draft_listing_id=None))
        db.execute(delete(MarketplaceListing).where(MarketplaceListing.listing_id == listing.id))
        db.execute(delete(MarketplaceCrosspostJob).where(MarketplaceCrosspostJob.listing_id == listing.id))
        db.execute(delete(ListingPrediction).where(ListingPrediction.listing_id == listing.id))
        db.execute(delete(ListingABTestVariant).where(ListingABTestVariant.listing_id == listing.id))
        db.execute(update(Sale).where(Sale.listing_id == listing.id).values(listing_id=None))
        db.execute(update(EbayOfferHistory).where(EbayOfferHistory.listing_id == listing.id).values(listing_id=None))
        db.execute(update(AutomatedOfferLog).where(AutomatedOfferLog.listing_id == listing.id).values(listing_id=None))
        db.execute(update(MarketplaceImportJob).where(MarketplaceImportJob.created_listing_id == listing.id).values(created_listing_id=None))
        db.delete(listing)
        return media_cleanup

    def purge_and_regenerate_bad_google_photos_drafts(
        self,
        db: Session,
        *,
        user: User | None = None,
        rebuild_user: User | None = None,
    ) -> dict[str, Any]:
        query = select(Listing).where(
            Listing.source_type.in_(("google_photos_album", "media_inventory_recovery")),
        )
        if user is not None and not getattr(user, "is_admin", False):
            query = query.where(Listing.user_id == user.id)
        listings = db.execute(query).scalars().all()
        candidates = [listing for listing in listings if self._is_clearly_bad_google_photos_listing(db, listing)]
        preserved = [listing for listing in listings if listing not in candidates]
        purged_details: list[dict[str, Any]] = []
        for listing in candidates:
            media_cleanup = self._delete_listing_graph(db, listing=listing)
            purged_details.append({"listing_id": listing.id, "media_cleanup": media_cleanup})
            db.commit()
        rebuild_owner = rebuild_user or user
        rebuilt = self.monitor_google_album(db, user=rebuild_owner) if purged_details and rebuild_owner else {"scanned": 0, "imported": 0, "drafts_created": 0, "duplicates": 0}
        return {
            "scanned_google_photos_listings": len(listings),
            "purged_count": len(purged_details),
            "purged_listing_ids": [row["listing_id"] for row in purged_details],
            "preserved_count": len(preserved),
            "rebuild_result": rebuilt,
        }

    def _listing_refresh_signature(self, listing: Listing) -> str:
        payload = {
            "title": listing.title,
            "description": listing.description,
            "category_suggestion": listing.category_suggestion,
            "item_specifics": listing.item_specifics,
            "image_urls": list(listing.image_urls or []),
            "listing_images": listing.listing_images or [],
            "condition": listing.condition,
            "shipping_profile": listing.shipping_profile,
            "suggested_price": listing.suggested_price,
            "listing_price": listing.listing_price,
            "buy_it_now_price": listing.buy_it_now_price,
            "custom_labels": list(listing.custom_labels or []),
            "storage_unit_name": listing.storage_unit_name,
            "raw_photo_path": listing.raw_photo_path,
            "source_type": listing.source_type,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _record_external_listing_reconciliation_review(
        self,
        db: Session,
        *,
        listing: Listing,
        slate: IntakeSlate | None,
        batch: IntakePhotoBatch,
        photos: list[IntakePhoto],
    ) -> None:
        """Persist a proposed delta without mutating an external listing snapshot."""
        item = self._canonical_item_for(db, user_id=batch.user_id, item_id=batch.item_id, slate=slate)
        before = {
            "image_urls": list(listing.image_urls or []),
            "listing_image_count": len(listing.listing_images or []),
            "title": listing.title,
            "description": listing.description,
            "price": listing.listing_price,
            "quantity": listing.quantity,
            "category": listing.category_suggestion,
            "condition": listing.condition,
        }
        proposed = {
            "intake_photo_ids": [photo.id for photo in photos],
            "public_photo_count": len(photos),
            "media_only": True,
            "requires_explicit_marketplace_update": True,
        }
        db.add(
            IntakeReconciliationEvent(
                user_id=batch.user_id,
                event_type="external_listing_update_required",
                status="needs_review",
                canonical_item_id=item.id,
                interval_json={"batch_id": batch.id, "listing_id": listing.id},
                details_json={"before": before, "proposed": proposed, "reason": "late_media_or_slate"},
            )
        )
        self._create_intake_notification(
            db,
            user_id=batch.user_id,
            canonical_item_id=item.id,
            notification_type="external_listing_update_required",
            title="External listing needs an explicit intake update review",
            message=f"{batch.item_id} has reconciled media. PosterPro preserved the published or queued listing snapshot.",
            href=f"/listings/{listing.id}",
        )

    def timeline_items(self, db: Session, *, user_id: int, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        photos = self._ordered_photos(db, user_id=user_id)
        if limit is not None:
            if not isinstance(limit, int):
                limit = 500
            if not isinstance(offset, int):
                offset = 0
            photos = photos[max(0, offset): max(0, offset) + max(1, limit)]
        return [
            {
                "photo": photo,
                "timeline_key": [str(value) for value in self._timeline_sort_key(photo)[:6]],
                "late_arrival": bool(
                    photo.captured_at and photo.imported_at and photo.captured_at.replace(tzinfo=UTC) < photo.imported_at.replace(tzinfo=UTC)
                ),
            }
            for photo in photos
        ]

    def set_canonical_fact(
        self,
        db: Session,
        *,
        user_id: int,
        item_id: str,
        field_name: str,
        value: Any,
        lock: bool = True,
    ) -> CanonicalItemFact:
        item = self._canonical_item_for(db, user_id=user_id, item_id=item_id)
        fact = self._upsert_canonical_fact(
            db,
            item=item,
            field_name=field_name,
            value=value,
            source_type="operator_edit",
            source_identifier="intake_api",
            confidence=1.0,
            value_status="verified",
            precedence=100,
            effective_at=datetime.now(UTC),
            is_locked=lock,
        )
        if fact is None:
            raise ValueError("A value is required for a canonical item fact.")
        db.commit()
        return fact

    def backfill_canonical_items(self, db: Session, *, user_id: int) -> dict[str, int]:
        """Idempotently seed canonical records from the live Head Slate projection."""
        slates = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id)).scalars().all()
        created_or_linked = 0
        observations = 0
        for slate in slates:
            photo = db.get(IntakePhoto, slate.slate_image_id) if slate.slate_image_id else None
            before = db.execute(
                select(CanonicalItem).where(CanonicalItem.user_id == user_id, CanonicalItem.item_id == slate.item_id)
            ).scalar_one_or_none()
            payload = slate.qr_payload_json if isinstance(slate.qr_payload_json, dict) else {
                "item_id": slate.item_id,
                "box_id": slate.box_id,
                "location": slate.location,
                "title": slate.title,
                "brand": slate.brand,
                "model": slate.model,
                "condition": slate.condition,
                "notes": slate.notes,
                "flaws": slate.flaws,
                "weight": slate.weight,
                "length": slate.length,
                "width": slate.width,
                "height": slate.height,
                "packed": slate.packed,
                "created_at": slate.created_at.isoformat() if slate.created_at else None,
            }
            self._record_slate_observation(
                db,
                user_id=user_id,
                slate=slate,
                photo=photo,
                payload=payload,
                source_type="backfill_slate",
                operator_confirmed=True,
            )
            if before is None:
                created_or_linked += 1
            if photo is not None:
                observations += 1
        db.commit()
        return {"slates_scanned": len(slates), "canonical_items_created": created_or_linked, "photo_observations_linked": observations}

    def assign_unassigned_photos_to_item(
        self,
        db: Session,
        *,
        user_id: int,
        item_id: str,
        photo_ids: list[int] | None = None,
        mark_ready_for_draft: bool = True,
    ) -> IntakePhotoBatch:
        normalized_item_id = str(item_id or "").strip()
        if not normalized_item_id:
            raise ValueError("Item ID is required.")
        slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id, IntakeSlate.item_id == normalized_item_id)).scalar_one_or_none()
        if slate is None:
            raise ValueError("Target slate was not found.")
        query = select(IntakePhoto).where(
            IntakePhoto.user_id == user_id,
            IntakePhoto.batch_id.is_(None),
            or_(IntakePhoto.is_slate.is_(False), IntakePhoto.is_slate.is_(None)),
        )
        if photo_ids:
            query = query.where(IntakePhoto.id.in_(photo_ids))
        rows = sorted(db.execute(query).scalars().all(), key=self._timeline_sort_key)
        if not rows:
            raise ValueError("No unassigned photos matched that request.")
        batch = self._ensure_batch(db, user_id=user_id, item_id=normalized_item_id, slate=slate)
        for photo in rows:
            metadata = dict(photo.metadata_json or {})
            metadata["manual_item_id"] = normalized_item_id
            metadata["manual_assignment_locked"] = True
            photo.metadata_json = metadata
            photo.item_id = normalized_item_id
            photo.batch_id = batch.id
            photo.is_slate = False
            photo.is_public_listing_candidate = not bool(photo.is_internal_only)
            photo.image_type = photo.image_type or "product"
            db.add(photo)
        db.flush()
        self.rebuild_batches_for_user(db, user_id=user_id)
        batch = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == user_id, IntakePhotoBatch.item_id == normalized_item_id)).scalar_one()
        if mark_ready_for_draft and not batch.draft_listing_id and self._batch_is_closed(batch):
            photos = sorted(db.execute(select(IntakePhoto).where(IntakePhoto.batch_id == batch.id)).scalars().all(), key=self._timeline_sort_key)
            public_photos = [photo for photo in photos if photo.is_public_listing_candidate and not photo.is_slate and not self._timeline_photo_deleted(photo)]
            if public_photos:
                self._create_or_update_listing_from_batch(db, slate=slate, batch=batch, photos=public_photos, force_regenerate=True)
                db.commit()
                batch = db.get(IntakePhotoBatch, batch.id)
        return batch

    def apply_photo_boundaries(
        self,
        db: Session,
        *,
        user_id: int,
        boundaries: list[dict[str, Any]],
        mark_ready_for_draft: bool = True,
    ) -> dict[str, Any]:
        cleaned_boundaries: list[tuple[int, str]] = []
        seen_photo_ids: set[int] = set()
        for row in boundaries or []:
            photo_id = int(row.get("photo_id") or 0)
            item_id = str(row.get("item_id") or "").strip()
            if not photo_id or not item_id or photo_id in seen_photo_ids:
                continue
            seen_photo_ids.add(photo_id)
            cleaned_boundaries.append((photo_id, item_id))
        if not cleaned_boundaries:
            raise ValueError("Select one or more boundary photos first.")

        item_ids = {item_id for _, item_id in cleaned_boundaries}
        slates = {
            slate.item_id: slate
            for slate in db.execute(
                select(IntakeSlate).where(
                    IntakeSlate.user_id == user_id,
                    IntakeSlate.item_id.in_(item_ids),
                )
            ).scalars().all()
        }
        missing_item_ids = sorted(item_id for item_id in item_ids if item_id not in slates)
        if missing_item_ids:
            raise ValueError(f"Saved slate not found for: {', '.join(missing_item_ids)}")

        photos = {
            photo.id: photo
            for photo in db.execute(
                select(IntakePhoto).where(
                    IntakePhoto.user_id == user_id,
                    IntakePhoto.id.in_([photo_id for photo_id, _ in cleaned_boundaries]),
                )
            ).scalars().all()
        }
        missing_photo_ids = [str(photo_id) for photo_id, _ in cleaned_boundaries if photo_id not in photos]
        if missing_photo_ids:
            raise ValueError(f"Photo not found: {', '.join(missing_photo_ids)}")

        for photo_id, item_id in cleaned_boundaries:
            photo = photos[photo_id]
            metadata = dict(photo.metadata_json or {})
            metadata["manual_item_id"] = item_id
            metadata["manual_assignment_locked"] = True
            metadata["manual_boundary_marked"] = True
            metadata["manual_boundary_marked_at"] = datetime.now(UTC).isoformat()
            photo.metadata_json = metadata
            photo.item_id = item_id
            photo.is_slate = True
            photo.is_internal_only = True
            photo.is_public_listing_candidate = False
            photo.image_type = "slate"
            db.add(photo)

        db.commit()
        self.rebuild_batches_for_user(db, user_id=user_id)
        drafted = 0
        if mark_ready_for_draft:
            drafted = self.create_drafts_for_ready_batches(db, user_id=user_id)
        return {
            "boundaries_applied": len(cleaned_boundaries),
            "drafts_created": drafted,
        }

    def reconcile_marked_slate_photos(self, db: Session, *, user: User, mark_ready_for_draft: bool = True) -> dict[str, Any]:
        photos = [photo for photo in self._ordered_photos(db, user_id=user.id) if photo.is_slate]
        fallback = self._rebuild_manual_marked_boundaries(db, user=user)
        photos = [photo for photo in self._ordered_photos(db, user_id=user.id) if photo.is_slate]
        decode_limit = 8
        decoded = 0
        no_match = 0
        slates_created_or_updated = 0
        decode_candidates = photos[:decode_limit]
        for photo in decode_candidates:
            payload = self.decode_slate_payload_isolated(photo.local_path)
            metadata = dict(photo.metadata_json or {})
            metadata["slate_detection_checked_at"] = datetime.now(UTC).isoformat()
            if payload:
                metadata["slate_detection_result"] = "matched"
                metadata["qr_payload"] = payload
                photo.metadata_json = metadata
                before_item_id = str(photo.item_id or "").strip()
                slate = self._upsert_slate_from_qr(db, user=user, qr_payload=payload, photo=photo)
                if slate and slate.item_id != before_item_id:
                    metadata["reconciled_item_id"] = slate.item_id
                    photo.metadata_json = metadata
                    db.add(photo)
                decoded += 1
                slates_created_or_updated += 1
            else:
                metadata["slate_detection_result"] = "no_match"
                photo.metadata_json = metadata
                db.add(photo)
                no_match += 1
        db.commit()
        grouping_result = self.rebuild_batches_for_user(db, user_id=user.id)
        self._close_final_batch_for_snapshot(db, user_id=user.id)
        drafted = 0
        if mark_ready_for_draft:
            drafted = self.regenerate_drafts_for_closed_batches(db, user_id=user.id)
        return {
            "marked_slate_photos": len(photos),
            "decode_candidates": len(decode_candidates),
            "decode_skipped": max(len(photos) - len(decode_candidates), 0),
            "decoded": decoded,
            "no_match": no_match,
            "slates_created_or_updated": slates_created_or_updated,
            "fallback": fallback,
            "grouping_result": grouping_result,
            "drafts_created": drafted,
        }

    def create_drafts_for_ready_batches(self, db: Session, *, user_id: int, max_new_items_per_run: int | None = None) -> int:
        user = db.get(User, user_id)
        intake_settings = self.settings_for_user(user)
        if not self._drafting_enabled(intake_settings):
            return 0
        batches = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == user_id).order_by(IntakePhotoBatch.updated_at.asc())).scalars().all()
        created = 0
        for batch in batches:
            if max_new_items_per_run is not None and created >= max(0, int(max_new_items_per_run)):
                break
            if batch.draft_listing_id:
                continue
            slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id, IntakeSlate.item_id == batch.item_id)).scalar_one_or_none()
            if slate is None:
                continue
            photos = sorted(db.execute(select(IntakePhoto).where(IntakePhoto.batch_id == batch.id)).scalars().all(), key=self._timeline_sort_key)
            public_photos = [photo for photo in photos if photo.is_public_listing_candidate and not photo.is_slate and not self._timeline_photo_deleted(photo)]
            if not self._batch_is_draftable(batch=batch, photos=public_photos, intake_settings=intake_settings):
                continue
            batch.metadata_json = self._batch_state_snapshot(
                batch,
                photos,
                stream_closed=self._batch_is_closed(batch),
                draft_state="DRAFTING",
            )
            db.add(batch)
            db.flush()
            listing = self._create_or_update_listing_from_batch(db, slate=slate, batch=batch, photos=public_photos)
            if listing:
                batch.metadata_json = self._batch_state_snapshot(
                    batch,
                    photos,
                    stream_closed=self._batch_is_closed(batch),
                    draft_state="DRAFT_CREATED",
                )
                db.add(batch)
                created += 1
        db.commit()
        return created

    def _batch_is_draftable(self, *, batch: IntakePhotoBatch, photos: list[IntakePhoto], intake_settings: dict[str, Any]) -> bool:
        if len(photos) < max(1, int(intake_settings.get("draft_min_public_photos") or 1)):
            return False
        if self._batch_is_closed(batch):
            return True
        if not intake_settings.get("auto_draft_when_provisional", True):
            return False
        quiet_period = max(0, int(intake_settings.get("quiet_period_seconds") or 300))
        latest = max((photo.captured_at or photo.imported_at or photo.created_at for photo in photos), default=None)
        if latest is None:
            return False
        latest_utc = latest.replace(tzinfo=UTC) if latest.tzinfo is None else latest.astimezone(UTC)
        return (datetime.now(UTC) - latest_utc).total_seconds() >= quiet_period

    def queue_items(self, db: Session, *, user_id: int) -> dict[str, Any]:
        batches = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == user_id).order_by(IntakePhotoBatch.updated_at.desc())).scalars().all()
        slates = {
            slate.item_id: slate
            for slate in db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id)).scalars().all()
        }
        listings = {
            listing.id: listing
            for listing in db.execute(select(Listing).where(Listing.user_id == user_id)).scalars().all()
        }
        photos = [
            photo for photo in db.execute(select(IntakePhoto).where(IntakePhoto.user_id == user_id)).scalars().all()
            if not self._timeline_photo_deleted(photo)
        ]
        photos_by_batch: dict[int, list[IntakePhoto]] = {}
        for photo in photos:
            if photo.batch_id:
                photos_by_batch.setdefault(photo.batch_id, []).append(photo)
        unassigned = [photo for photo in photos if not photo.batch_id and not photo.is_slate]
        slate_candidates = [
            photo
            for photo in photos
            if not photo.is_slate and isinstance(photo.metadata_json, dict)
            and str(photo.metadata_json.get("slate_detection_result") or "").strip() == "probable_slate_candidate"
        ]
        items = []
        for batch in batches:
            slate = slates.get(batch.item_id)
            listing = listings.get(batch.draft_listing_id) if batch.draft_listing_id else None
            batch_photos = sorted(photos_by_batch.get(batch.id, []), key=self._timeline_sort_key)
            items.append({
                "batch": batch,
                "slate": slate,
                "listing": listing,
                "photos": batch_photos,
                "first_public_photo": next((photo for photo in batch_photos if photo.is_public_listing_candidate), None),
                "warnings": self._batch_warnings(slate=slate, batch=batch, listing=listing, photos=batch_photos),
            })
        return {
            "batches": items,
            "unassigned_photos": sorted(unassigned, key=self._timeline_sort_key),
            "available_slates": sorted(slates.values(), key=lambda item: ((item.updated_at or item.created_at or datetime.min).isoformat(), item.id), reverse=True),
            "slate_candidates": sorted(slate_candidates, key=self._timeline_sort_key),
        }

    def update_slate(self, db: Session, *, user_id: int, slate_id: int, payload: dict[str, Any]) -> IntakeSlate:
        slate = db.get(IntakeSlate, slate_id)
        if not slate or slate.user_id != user_id:
            raise ValueError("Slate not found.")
        if payload.get("item_id") and str(payload.get("item_id")).strip() != slate.item_id:
            duplicate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id, IntakeSlate.item_id == str(payload.get("item_id")).strip())).scalar_one_or_none()
            if duplicate:
                raise ValueError("Item ID already exists.")
            if slate.listing_id:
                raise ValueError("Cannot change item ID after a listing was created.")
        for field in ("item_id", "box_id", "location", "title", "brand", "model", "condition", "notes", "flaws", "weight", "length", "width", "height", "internal_notes", "status"):
            if field in payload and payload.get(field) is not None:
                setattr(slate, field, str(payload.get(field)).strip() or None)
        if payload.get("packed") is not None:
            slate.packed = bool(payload.get("packed"))
        if isinstance(slate.qr_payload_json, dict):
            qr_payload = dict(slate.qr_payload_json)
            for key in ("item_id", "box_id", "location", "title", "brand", "model", "condition", "notes", "flaws", "weight", "length", "width", "height"):
                if getattr(slate, key, None) is not None:
                    qr_payload[key] = getattr(slate, key) or ""
            qr_payload["packed"] = bool(slate.packed)
            if payload.get("boundary_position") is not None:
                qr_payload["boundary_position"] = self._normalize_boundary_position(payload)
            slate.qr_payload_json = qr_payload
        db.add(slate)
        db.commit()
        db.refresh(slate)
        self._record_slate_observation(
            db,
            user_id=user_id,
            slate=slate,
            photo=None,
            payload=slate.qr_payload_json if isinstance(slate.qr_payload_json, dict) else {},
            source_type="operator_edit",
            operator_confirmed=True,
        )
        db.commit()
        self.rebuild_batches_for_user(db, user_id=user_id)
        return slate

    def update_photo(self, db: Session, *, user_id: int, photo_id: int, payload: dict[str, Any]) -> IntakePhoto:
        photo = db.get(IntakePhoto, photo_id)
        if not photo or photo.user_id != user_id:
            raise ValueError("Photo not found.")
        if payload.get("item_id") is not None:
            photo.item_id = str(payload.get("item_id")).strip() or None
        if payload.get("batch_id") is not None:
            photo.batch_id = int(payload.get("batch_id")) if payload.get("batch_id") else None
        for field in ("is_slate", "is_public_listing_candidate", "is_internal_only"):
            if payload.get(field) is not None:
                setattr(photo, field, bool(payload.get(field)))
        if payload.get("image_type") is not None:
            photo.image_type = str(payload.get("image_type")).strip() or None
        db.add(photo)
        db.commit()
        db.refresh(photo)
        self.rebuild_batches_for_user(db, user_id=user_id)
        user = db.get(User, user_id)
        if user and self._drafting_enabled(self.settings_for_user(user)):
            self.create_drafts_for_ready_batches(db, user_id=user_id)
        photo = db.get(IntakePhoto, photo_id) or photo
        return photo

    def split_batch(self, db: Session, *, user_id: int, batch_id: int, photo_ids: list[int], new_item_id: str | None, new_box_id: str | None, location: str | None) -> IntakePhotoBatch:
        batch = db.get(IntakePhotoBatch, batch_id)
        if not batch or batch.user_id != user_id:
            raise ValueError("Batch not found.")
        item_id = str(new_item_id or "").strip() or self.next_item_id(db, user_id=user_id)
        if db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id, IntakeSlate.item_id == item_id)).scalar_one_or_none():
            raise ValueError("New item ID already exists.")
        slate = IntakeSlate(user_id=user_id, intake_session_id=batch.intake_session_id, session_id=batch.session_id, item_id=item_id, box_id=new_box_id, location=location, status="manual_review")
        db.add(slate)
        db.flush()
        new_batch = self._ensure_batch(db, user_id=user_id, item_id=item_id, slate=slate)
        for photo in db.execute(select(IntakePhoto).where(IntakePhoto.user_id == user_id, IntakePhoto.id.in_(photo_ids))).scalars().all():
            photo.item_id = item_id
            photo.batch_id = new_batch.id
            db.add(photo)
        db.commit()
        self.rebuild_batches_for_user(db, user_id=user_id)
        return new_batch

    def merge_batches(self, db: Session, *, user_id: int, source_batch_ids: list[int], target_item_id: str | None) -> IntakePhotoBatch:
        batches = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == user_id, IntakePhotoBatch.id.in_(source_batch_ids))).scalars().all()
        if len(batches) < 2:
            raise ValueError("Select at least two batches to merge.")
        target = None
        if target_item_id:
            target = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == user_id, IntakePhotoBatch.item_id == str(target_item_id).strip())).scalar_one_or_none()
        target = target or batches[0]
        for batch in batches:
            if batch.id == target.id:
                continue
            photos = db.execute(select(IntakePhoto).where(IntakePhoto.batch_id == batch.id)).scalars().all()
            for photo in photos:
                photo.item_id = target.item_id
                photo.batch_id = target.id
                db.add(photo)
            db.delete(batch)
        db.commit()
        self.rebuild_batches_for_user(db, user_id=user_id)
        return db.get(IntakePhotoBatch, target.id)

    def regenerate_batch_listing(self, db: Session, *, user_id: int, batch_id: int, force: bool = False) -> Listing:
        batch = db.get(IntakePhotoBatch, batch_id)
        if not batch or batch.user_id != user_id:
            raise ValueError("Batch not found.")
        if batch.draft_listing_id and not force:
            listing = db.get(Listing, batch.draft_listing_id)
            if listing:
                return listing
        slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id, IntakeSlate.item_id == batch.item_id)).scalar_one_or_none()
        photos = sorted(db.execute(select(IntakePhoto).where(IntakePhoto.batch_id == batch.id)).scalars().all(), key=self._timeline_sort_key)
        public_photos = [photo for photo in photos if photo.is_public_listing_candidate and not photo.is_slate and not self._timeline_photo_deleted(photo)]
        listing = self._create_or_update_listing_from_batch(db, slate=slate, batch=batch, photos=public_photos, force_regenerate=force)
        db.commit()
        return listing

    def export_csv(self, db: Session, *, user_id: int) -> str:
        batches = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == user_id).order_by(IntakePhotoBatch.updated_at.desc())).scalars().all()
        slates = {row.item_id: row for row in db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id)).scalars().all()}
        listings = {row.id: row for row in db.execute(select(Listing).where(Listing.user_id == user_id)).scalars().all()}
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "Item ID", "Box ID", "Location", "Title", "Brand", "Model", "Condition", "Notes", "Flaws", "Session ID",
            "Packed", "Weight", "Length", "Width", "Height", "Created At", "Photo Count", "Draft Listing ID", "Listing Status",
            "Marketplace Status", "Sold Status", "Sold Date", "Tracking Number",
        ])
        for batch in batches:
            slate = slates.get(batch.item_id)
            listing = listings.get(batch.draft_listing_id) if batch.draft_listing_id else None
            marketplace_status = ""
            if listing and isinstance(listing.marketplace_data, dict):
                marketplace_status = str((listing.marketplace_data or {}).get("status") or (listing.ebay_publish_status or ""))
            writer.writerow([
                batch.item_id,
                slate.box_id if slate else "",
                slate.location if slate else "",
                slate.title if slate else (listing.title if listing else ""),
                slate.brand if slate else "",
                slate.model if slate else "",
                slate.condition if slate else (listing.condition if listing else ""),
                slate.notes if slate else "",
                slate.flaws if slate else "",
                batch.session_id or (slate.session_id if slate else ""),
                bool(slate.packed) if slate else False,
                slate.weight if slate else "",
                slate.length if slate else "",
                slate.width if slate else "",
                slate.height if slate else "",
                slate.created_at.isoformat() if slate and slate.created_at else "",
                batch.photo_count,
                batch.draft_listing_id or "",
                str(listing.status) if listing else "",
                marketplace_status,
                "SOLD" if listing and listing.sold_at else "",
                listing.sold_at.isoformat() if listing and listing.sold_at else "",
                str(((listing.marketplace_data or {}).get("tracking_number") or "")) if listing else "",
            ])
        return buffer.getvalue()

    def build_qr_data_url(self, payload: dict[str, Any]) -> str:
        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(output.getvalue()).decode('ascii')}"

    def _render_slate_preview_image(self, payload: dict[str, Any]) -> Image.Image:
        slate_type = "TAIL SLATE" if str(payload.get("boundary_position") or "start").lower() == "tail" else "HEAD SLATE"
        head_mode = slate_type == "HEAD SLATE"
        background = (239, 255, 220) if head_mode else (255, 232, 245)
        accent = (0, 202, 170) if head_mode else (242, 84, 165)
        accent2 = (67, 212, 255) if head_mode else (255, 154, 38)
        canvas = Image.new("RGB", (1600, 1000), background)
        draw = ImageDraw.Draw(canvas)
        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        qr.make(fit=True)
        qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        resampling = getattr(Image, "Resampling", Image)
        qr_image = qr_image.resize((420, 420), resampling.LANCZOS)

        def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
            candidates = (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            )
            for candidate in candidates:
                try:
                    return ImageFont.truetype(candidate, size=size)
                except Exception:
                    continue
            return ImageFont.load_default()

        # Keep both brand and boundary labels inside their colored header
        # blocks at the actual 1600px render size (no clipped final letters).
        title_font = load_font(38, bold=True)
        field_font = load_font(28)
        small_font = load_font(20)
        big_font = load_font(74, bold=True)

        def text_value(key: str) -> str:
            return str(payload.get(key) or "—").strip() or "—"

        def draw_field(x: int, y: int, label: str, value: str, width: int = 700) -> int:
            draw.text((x, y), f"{label}:", fill="black", font=field_font)
            text_y = y + 34
            lines = []
            current = ""
            for token in value.split():
                trial = f"{current} {token}".strip()
                if draw.textbbox((0, 0), trial, font=field_font)[2] - draw.textbbox((0, 0), trial, font=field_font)[0] <= width:
                    current = trial
                else:
                    if current:
                        lines.append(current)
                    current = token
            if current:
                lines.append(current)
            if not lines:
                lines = ["—"]
            for line in lines:
                draw.text((x + 4, text_y), line, fill="black", font=field_font)
                text_y += 34
            return text_y + 8

        draw.rounded_rectangle((38, 38, 1562, 962), radius=28, outline=accent, width=8)
        draw.rounded_rectangle((56, 56, 1544, 944), radius=22, outline=accent2, width=4)
        draw.rectangle((56, 56, 360, 126), fill=accent)
        draw.rectangle((1160, 56, 1544, 126), fill=accent2)
        draw.text((82, 70), "POSTERPRO", fill="white", font=title_font)
        draw.text((1180, 70), slate_type, fill="white", font=title_font)
        draw.text((88, 150), text_value("session_id"), fill="black", font=big_font)
        display_item = text_value("display_item_number")
        display_box = text_value("display_box_number")
        draw.text((88, 240), f"Item: {display_item}   Box: {display_box}", fill="black", font=field_font)
        draw.text((88, 272), f"Canonical: {text_value('item_id')} / {text_value('box_id')}", fill="black", font=small_font)
        draw.text((88, 316), f"Location: {text_value('location')}", fill="black", font=field_font)
        draw.text((88, 360), f"Condition: {text_value('condition')}", fill="black", font=field_font)
        draw.text((88, 404), f"Boundary: {text_value('boundary_position').upper()}", fill="black", font=field_font)
        draw.text((88, 448), f"Date: {text_value('created_at')}", fill="black", font=field_font)

        y = 520
        for label in ("title", "brand", "model", "notes", "flaws", "weight", "length", "width", "height"):
            value = text_value(label)
            if label == "title":
                display = "Title"
            elif label == "brand":
                display = "Brand"
            elif label == "model":
                display = "Model"
            elif label == "notes":
                display = "Notes"
            elif label == "flaws":
                display = "Flaws"
            elif label == "weight":
                display = "Weight"
            elif label == "length":
                display = "Length"
            elif label == "width":
                display = "Width"
            else:
                display = "Height"
            y = draw_field(88, y, display, value, width=760)
            if y > 820:
                break

        qr_x = 1110
        qr_y = 132
        draw.rectangle((qr_x - 18, qr_y - 18, qr_x + 438, qr_y + 438), outline="black", width=3)
        canvas.paste(qr_image, (qr_x, qr_y))
        draw.text((1040, 586), "Use this image as the photographed boundary marker.", fill="black", font=small_font)
        draw.text((1040, 620), "Keep the QR and header visible for best intake recognition.", fill="black", font=small_font)
        draw.text((88, 884), "Bright color blocks + large QR help the grouping engine and humans spot the slate instantly.", fill="black", font=small_font)
        return canvas

    def decode_slate_payload(self, image_path: str) -> dict[str, Any] | None:
        image = self._load_image_for_cv(str(image_path))
        if image is None:
            return None
        for value in self._decode_qr_values(image):
            payload = self._coerce_slate_json(value)
            if payload:
                return payload
        payload = self._decode_slate_payload_from_text(image)
        if payload:
            return payload
        return None

    def decode_slate_payload_isolated(self, image_path: str, *, timeout_seconds: int = 4) -> dict[str, Any] | None:
        backend_root = str(Path(__file__).resolve().parents[2])
        env = {
            **os.environ,
            "PYTHONPATH": backend_root,
        }
        helper = (
            "import json, sys; "
            "from app.services.intake_slate import IntakeSlateService; "
            "payload = IntakeSlateService().decode_slate_payload(sys.argv[1]); "
            "print(json.dumps(payload or {}))"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-c", helper, str(image_path)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
                check=False,
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        try:
            payload = json.loads(str(completed.stdout or "").strip() or "{}")
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) and payload else None

    def regenerate_drafts_for_closed_batches(self, db: Session, *, user_id: int) -> int:
        user = db.get(User, user_id)
        if not self._drafting_enabled(self.settings_for_user(user)):
            return 0
        batches = db.execute(
            select(IntakePhotoBatch)
            .where(IntakePhotoBatch.user_id == user_id)
            .order_by(IntakePhotoBatch.updated_at.asc(), IntakePhotoBatch.id.asc())
        ).scalars().all()
        refreshed = 0
        for batch in batches:
            if not self._batch_is_closed(batch):
                continue
            slate = db.execute(
                select(IntakeSlate).where(
                    IntakeSlate.user_id == user_id,
                    IntakeSlate.item_id == batch.item_id,
                )
            ).scalar_one_or_none()
            if slate is None:
                continue
            photos = sorted(db.execute(select(IntakePhoto).where(IntakePhoto.batch_id == batch.id)).scalars().all(), key=self._timeline_sort_key)
            public_photos = [photo for photo in photos if photo.is_public_listing_candidate and not photo.is_slate and not self._timeline_photo_deleted(photo)]
            if not public_photos:
                continue
            listing = self._create_or_update_listing_from_batch(
                db,
                slate=slate,
                batch=batch,
                photos=public_photos,
                force_regenerate=True,
            )
            if listing:
                refreshed += 1
        db.commit()
        return refreshed

    def _create_or_update_listing_from_batch(self, db: Session, *, slate: IntakeSlate | None, batch: IntakePhotoBatch, photos: list[IntakePhoto], force_regenerate: bool = False) -> Listing | None:
        if not photos:
            return None
        user = db.get(User, batch.user_id)
        if user is None:
            logger.warning("intake_draft_owner_missing", extra={"batch_id": batch.id, "item_id": batch.item_id})
            return None
        listing = db.get(Listing, batch.draft_listing_id) if batch.draft_listing_id else None
        if listing is None and slate and slate.listing_id:
            listing = db.get(Listing, slate.listing_id)
        if listing is not None and self._listing_is_externally_active(db, listing):
            self._record_external_listing_reconciliation_review(
                db,
                listing=listing,
                slate=slate,
                batch=batch,
                photos=photos,
            )
            return listing
        try:
            group_evidence = self.photo_enrichment.enrich_group([photo.local_path for photo in photos])
        except Exception as exc:  # noqa: BLE001
            logger.warning("intake_group_enrichment_failed", extra={"batch_id": batch.id, "item_id": batch.item_id, "error": str(exc)})
            group_evidence = {}
        group_synthesis = group_evidence.get("group_synthesis") if isinstance(group_evidence, dict) else {}
        evidence_identity = group_synthesis.get("identity") if isinstance(group_synthesis, dict) else {}
        title_hint = (
            (slate.title if slate and slate.title else "").strip()
            or str((evidence_identity or {}).get("title") or "").strip()
            or f"{(slate.brand or '').strip()} {(slate.model or '').strip()}".strip()
            or batch.item_id
        )
        public_urls, listing_images = self._materialize_listing_images(item_id=batch.item_id, title=title_hint, photos=photos)
        photo_signals = self._extract_photo_signals(photos)
        photo_signals["group_synthesis"] = group_synthesis if isinstance(group_synthesis, dict) else {}
        photo_signals["photo_evidence"] = group_evidence.get("photo_evidence") or []
        if isinstance(group_synthesis, dict):
            supporting = group_synthesis.get("supporting_media_ids") or {}
            if isinstance(supporting, dict):
                photo_signals["supporting_media_ids"] = supporting
        if listing is None:
            listing = Listing(user_id=batch.user_id, status=ListingStatus.draft)
        image_count = len(public_urls)
        generated = self.ai.generate(
            {
                "title_hint": title_hint,
                "source_type": "intake_head_slate",
                "image_count": image_count,
                "storage_unit_name": slate.location if slate else None,
                "existing_specifics": listing.item_specifics or {},
                "existing_condition": slate.condition if slate else listing.condition,
                "custom_labels": [batch.item_id, slate.box_id if slate and slate.box_id else "", slate.location if slate and slate.location else ""],
                "brand": slate.brand if slate else None,
                "model": slate.model if slate else None,
                "notes": slate.notes if slate else None,
                "flaws": slate.flaws if slate else None,
                "detected_identifiers": photo_signals.get("detected_identifiers") or [],
                "barcode_candidates": photo_signals.get("barcode_candidates") or [],
                "photo_keywords": photo_signals.get("photo_keywords") or [],
                "group_synthesis": group_synthesis if isinstance(group_synthesis, dict) else {},
                "quality_gate": (group_synthesis or {}).get("quality_gate") if isinstance(group_synthesis, dict) else None,
            }
        )
        price_data = self.ebay.enrich_price(generated.get("title") or title_hint, None)
        listing.title = (slate.title or generated.get("title") or title_hint)[:255] if slate and slate.title else (generated.get("title") or title_hint)[:255]
        listing.description = self._compose_description(slate=slate, generated=generated)
        listing.category_suggestion = generated.get("category_suggestion") or listing.category_suggestion
        listing.item_specifics = self._merged_specifics(slate=slate, generated=generated)
        if photo_signals.get("barcode_candidates") and not (listing.item_specifics or {}).get("UPC"):
            listing.item_specifics = {
                **(listing.item_specifics or {}),
                "UPC": str(photo_signals["barcode_candidates"][0]),
            }
        listing.tags = generated.get("tags") or listing.tags or []
        listing.estimated_value = generated.get("estimated_value") or listing.estimated_value
        listing.suggested_price = float((generated.get("estimated_value") or listing.suggested_price or 24.0))
        listing.listing_price = listing.listing_price or listing.suggested_price
        listing.buy_it_now_price = listing.buy_it_now_price or listing.listing_price
        listing.image_urls = public_urls
        listing.listing_images = normalize_listing_images(listing_images=listing_images, approved=True)
        listing.raw_photo_path = photos[0].local_path
        listing.storage_unit_name = slate.location if slate else listing.storage_unit_name
        listing.source_type = "intake_head_slate"
        listing.source_metadata = {
            **(listing.source_metadata or {}),
            "intake": {
                "item_id": batch.item_id,
                "box_id": slate.box_id if slate else None,
                "location": slate.location if slate else None,
                "session_id": batch.session_id,
                "intake_date": (slate.qr_payload_json or {}).get("created_at") if slate and isinstance(slate.qr_payload_json, dict) else None,
                "photo_ids": [photo.id for photo in photos],
            },
            "listing_intelligence": {
                "title": generated.get("title"),
                "category_suggestion": generated.get("category_suggestion"),
                "condition": generated.get("condition"),
                "item_specifics": generated.get("item_specifics") or {},
                "tags": generated.get("tags") or [],
                "estimated_value": generated.get("estimated_value"),
                "missing_information": generated.get("missing_information") or [],
                "photo_notes": generated.get("photo_notes") or [],
                "research_queries": generated.get("research_queries") or [],
                "draft_quality": generated.get("draft_quality"),
                "generation_source": generated.get("generation_source"),
                "model_used": generated.get("model_used"),
                "group_synthesis": group_synthesis if isinstance(group_synthesis, dict) else {},
                "photo_evidence": photo_signals.get("photo_evidence") or [],
                "detected_identifiers": photo_signals.get("detected_identifiers") or [],
                "barcode_candidates": photo_signals.get("barcode_candidates") or [],
                "photo_keywords": photo_signals.get("photo_keywords") or [],
            },
        }
        listing.condition = (slate.condition if slate and slate.condition else generated.get("condition") or listing.condition or "Needs review")[:64]
        listing.condition_data = derive_condition_data(
            listing={"condition": listing.condition, "source_type": listing.source_type},
            source_type=listing.source_type,
            source_metadata=listing.source_metadata,
            existing={
                **(listing.condition_data or {}),
                "item_condition_notes": self._condition_notes(slate=slate),
                "operator_review_required": True,
            },
        )
        listing.shipping_profile = derive_shipping_profile(
            listing={"title": listing.title, "description": listing.description},
            item_specifics=listing.item_specifics,
            existing={
                **(listing.shipping_profile or {}),
                "package_weight": slate.weight if slate and slate.weight else (listing.shipping_profile or {}).get("package_weight"),
                "package_dimensions": {
                    "length": slate.length if slate and slate.length else ((listing.shipping_profile or {}).get("package_dimensions") or {}).get("length"),
                    "width": slate.width if slate and slate.width else ((listing.shipping_profile or {}).get("package_dimensions") or {}).get("width"),
                    "height": slate.height if slate and slate.height else ((listing.shipping_profile or {}).get("package_dimensions") or {}).get("height"),
                },
            },
            policy=shipping_policy_for_user(user),
        )
        listing.quantity = max(1, int(listing.quantity or 1))
        listing.platform_quantities = listing.platform_quantities or {"inventory": listing.quantity}
        listing.custom_labels = self._merged_labels(listing.custom_labels or [], [batch.item_id, slate.box_id if slate and slate.box_id else None, slate.location if slate and slate.location else None])
        db.add(listing)
        db.flush()
        pricing_analysis = self.pricing.recommend_price(
            db,
            listing.id or 0,
            external_comparables=price_data.get("comparables") or [],
            estimated_value_override=generated.get("estimated_value"),
        )
        recommended_price = self._safe_float(
            pricing_analysis.get("recommended_price"),
            float(listing.suggested_price or generated.get("estimated_value") or 24.0),
        )
        listing.suggested_price = recommended_price
        listing.listing_price = float(listing.listing_price or recommended_price)
        listing.buy_it_now_price = float(listing.buy_it_now_price or recommended_price)
        listing.marketplace_data = normalize_marketplace_data(
            {
                **(listing.marketplace_data or {}),
                "targets": list(((self.settings_for_user(db.get(User, batch.user_id)) or {}).get("marketplace_defaults") or {}).get("targets") or ["ebay", "facebook"]),
                "crosspost_mode": "approval_required",
                "pricing_analysis": pricing_analysis,
                "intake_head_slate": {
                    "item_id": batch.item_id,
                    "box_id": slate.box_id if slate else None,
                    "location": slate.location if slate else None,
                    "session_id": batch.session_id,
                    "packed": bool(slate.packed) if slate else False,
                },
            }
        )
        listing.needs_review = True
        listing.status = ListingStatus.draft
        db.add(listing)
        batch.draft_listing_id = listing.id
        batch.metadata_json = self._batch_state_snapshot(
            batch,
            photos,
            stream_closed=self._batch_is_closed(batch),
            draft_state="DRAFT_CREATED",
            reconciliation_required=bool((batch.metadata_json or {}).get("reconciliation_required")),
        )
        if slate:
            slate.listing_id = listing.id
            slate.status = "draft_created"
            db.add(slate)
        batch.status = "drafted"
        db.add(batch)
        item = self._canonical_item_for(db, user_id=batch.user_id, item_id=batch.item_id, slate=slate)
        item.current_listing_id = listing.id
        item.status = "stable" if self._batch_is_closed(batch) else "provisional"
        item.confidence = self._listing_confidence(generated=generated, photo_count=len(photos), batch=batch)
        db.add(item)
        self._create_intake_notification(
            db,
            user_id=batch.user_id,
            canonical_item_id=item.id,
            notification_type="draft_ready",
            title="New intake draft ready for review",
            message=f"{listing.title or batch.item_id} is ready for marketplace review.",
            href=f"/listings/{listing.id}",
        )
        return listing

    def sync_google_album_truth(self, db: Session, *, user: User) -> dict[str, Any]:
        settings_payload = self.settings_for_user(user)
        source_url = str(settings_payload.get("album_url") or settings_payload.get("folder_id") or "").strip()
        if not source_url:
            raise ValueError("No intake Google Photos album or Drive link is configured.")
        entries = self.google_photos.extract_photo_entries(source_url)
        current_ids = {
            str(entry.get("source_photo_id") or "").strip()
            for entry in entries
            if str(entry.get("source_photo_id") or "").strip()
        }
        album_id = self._album_identifier(source_url)
        rows = db.execute(
            select(IntakePhoto).where(
                IntakePhoto.user_id == user.id,
                IntakePhoto.source_provider == "google_photos",
                IntakePhoto.source_album_id == album_id,
            )
        ).scalars().all()
        stale_found = 0
        removed = 0
        preserved = 0
        for photo in rows:
            if str(photo.source_photo_id or "").strip() in current_ids:
                continue
            stale_found += 1
            # Only prune ungrouped non-slate photos automatically. Anything already grouped,
            # manually assigned, or acting as a slate boundary stays preserved for review.
            has_provider_reference = bool(
                db.execute(
                    select(IntakeProviderMedia.id).where(
                        IntakeProviderMedia.user_id == user.id,
                        IntakeProviderMedia.intake_photo_id == photo.id,
                    )
                ).first()
            )
            if photo.batch_id or photo.is_slate or photo.item_id or photo.is_internal_only or has_provider_reference:
                metadata = dict(photo.metadata_json or {})
                metadata["album_truth_missing"] = True
                metadata["album_truth_checked_at"] = datetime.now(UTC).isoformat()
                metadata["album_truth_action"] = "preserved_for_review"
                if has_provider_reference:
                    metadata["album_truth_preserved_reason"] = "provider_media_reference"
                photo.metadata_json = metadata
                db.add(photo)
                preserved += 1
                continue
            metadata = dict(photo.metadata_json or {})
            metadata["album_truth_missing"] = True
            metadata["album_truth_checked_at"] = datetime.now(UTC).isoformat()
            metadata["album_truth_action"] = "removed"
            photo.metadata_json = metadata
            try:
                resolved = Path(photo.local_path)
                if resolved.exists():
                    resolved.unlink(missing_ok=True)
            except Exception:
                pass
            db.delete(photo)
            removed += 1
        db.commit()
        refreshed_user = db.get(User, user.id) or user
        self.save_settings(
            db=db,
            user=refreshed_user,
            payload={
                **settings_payload,
                "last_truth_sync_at": datetime.now(UTC).isoformat(),
                "last_truth_sync_result": {
                    "album_visible_count": len(entries),
                    "tracked_photo_count": len(rows),
                    "stale_found": stale_found,
                    "removed": removed,
                    "preserved": preserved,
                },
            },
        )
        return {
            "album_visible_count": len(entries),
            "tracked_photo_count": len(rows),
            "stale_found": stale_found,
            "removed": removed,
            "preserved": preserved,
        }

    def run_integrity_scan(self, db: Session, *, user: User) -> dict[str, Any]:
        """Re-enumerate a source and reconcile its observed truth safely.

        Missing source media is marked for review instead of deleted when it is
        part of an item, slate boundary, or listing. This keeps integrity scans
        repeatable and prevents a provider-side removal from damaging history.
        """
        settings_payload = self.settings_for_user(user)
        source_url = str(settings_payload.get("album_url") or settings_payload.get("folder_id") or "").strip()
        if not source_url:
            raise ValueError("No intake Google Photos album or Drive link is configured.")
        monitor_result = self.monitor_google_album(db, user=user)
        truth_result = self.sync_google_album_truth(db, user=user)
        source_state = self._source_state_for(
            db,
            user_id=user.id,
            provider="google_photos",
            source_key=self._album_identifier(source_url),
        )
        source_state.last_integrity_scan_at = datetime.now(UTC)
        source_state.metadata_json = {
            **(source_state.metadata_json or {}),
            "last_integrity_result": {
                "monitor": monitor_result,
                "truth": truth_result,
            },
        }
        db.add(source_state)
        db.add(
            IntakeReconciliationEvent(
                user_id=user.id,
                event_type="integrity_scan",
                status="completed",
                interval_json={"source_key": source_state.source_key},
                details_json={"monitor": monitor_result, "truth": truth_result},
            )
        )
        db.commit()
        return {"monitor": monitor_result, "truth": truth_result}

    def _materialize_listing_images(self, *, item_id: str, title: str, photos: list[IntakePhoto]) -> tuple[list[str], list[dict[str, Any]]]:
        public_urls: list[str] = []
        listing_images: list[dict[str, Any]] = []
        seo_title = self._slug_token(title, max_length=120) or "item"
        destination_dir = Path(settings.storage_root) / "intake-items" / item_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        # An operator-selected primary photo wins; otherwise chronology is the
        # deterministic fallback used by marketplace payload generation.
        photos = [photo for photo in photos if not photo.is_slate and not self._timeline_photo_deleted(photo)]
        photos = sorted(photos, key=lambda photo: (0 if (photo.metadata_json or {}).get("timeline_primary") else 1, self._timeline_sort_key(photo)))
        total = len(photos)
        for index, photo in enumerate(photos, start=1):
            source = Path(photo.local_path)
            extension = source.suffix.lower() or ".jpg"
            purpose = self._listing_photo_purpose(photo=photo, index=index, total=total)
            filename = f"{self._slug_token(f'{seo_title} {purpose} {item_id}', max_length=140) or 'item'}-{index:02d}{extension}"
            target = destination_dir / filename
            if not target.exists() and source.exists():
                self._prepare_listing_image(source=source, target=target)
            public_path = self._to_public_media_path(str(target))
            public_urls.append(public_path)
            listing_images.append(
                {
                    "storage_path": public_path,
                    "source_url": photo.downloaded_url,
                    "source_platform": photo.source_provider,
                    "source_page_url": ((photo.metadata_json or {}).get("album_url") if isinstance(photo.metadata_json, dict) else None),
                    "role": "primary" if index == 1 else "alternate_angle",
                    "confidence": 1.0,
                    "operator_state": "approved",
                    "display_order": index - 1,
                    "is_reference": False,
                    "label": f"Intake photo {index}",
                    "metadata": {
                        "item_id": item_id,
                        "intake_photo_id": photo.id,
                        "seo_filename": filename,
                        "alt_text": f"{title} photo {index}",
                        "internal_caption": f"{item_id} product photo {index}",
                        "original_local_path": photo.local_path,
                        "image_processing": "posterpro_intake_enhance_v1",
                    },
                }
            )
        return public_urls, listing_images

    def refresh_drafts_until_stable(self, db: Session, *, user: User, max_passes: int = 2) -> dict[str, Any]:
        """Continue batch regrouping and draft refreshes for a bounded number of passes."""
        totals = {
            "passes": 0,
            "recovered_slates": 0,
            "rebuilt_groups": 0,
            "refreshed_drafts": 0,
            "created_drafts": 0,
            "regenerated_drafts": 0,
        }
        if not self._drafting_enabled(self.settings_for_user(user)):
            return totals
        limit = max(1, min(int(max_passes or 1), 3))
        max_new_items_per_run = self._parse_optional_int(self.settings_for_user(user).get("max_new_items_per_run"))
        for _ in range(limit):
            pass_totals = {
                "recovered_slates": self.recover_existing_slates(db, user=user, limit=75),
                "rebuilt_groups": 0,
                "refreshed_drafts": 0,
                "created_drafts": 0,
                "regenerated_drafts": 0,
            }
            grouping_result = self.rebuild_batches_for_user(db, user_id=user.id)
            pass_totals["rebuilt_groups"] = int(grouping_result.get("assigned_photos") or 0)
            pass_totals["refreshed_drafts"] = self.refresh_existing_draft_listings_for_user(db, user_id=user.id)
            pass_totals["created_drafts"] = self.create_drafts_for_ready_batches(db, user_id=user.id, max_new_items_per_run=max_new_items_per_run)
            pass_totals["regenerated_drafts"] = self.regenerate_drafts_for_closed_batches(db, user_id=user.id)
            totals["passes"] += 1
            for key in ("recovered_slates", "rebuilt_groups", "refreshed_drafts", "created_drafts", "regenerated_drafts"):
                totals[key] += int(pass_totals[key] or 0)
            if not any(pass_totals.values()):
                break
        return totals

    def refresh_existing_draft_listings_for_user(self, db: Session, *, user_id: int) -> int:
        """Refresh existing drafts using the full current batch photo set."""
        user = db.get(User, user_id)
        if not self._drafting_enabled(self.settings_for_user(user)):
            return 0
        batches = db.execute(
            select(IntakePhotoBatch)
            .where(
                IntakePhotoBatch.user_id == user_id,
                IntakePhotoBatch.draft_listing_id.is_not(None),
            )
            .order_by(IntakePhotoBatch.updated_at.asc(), IntakePhotoBatch.id.asc())
        ).scalars().all()
        refreshed = 0
        for batch in batches:
            slate = db.execute(
                select(IntakeSlate).where(
                    IntakeSlate.user_id == user_id,
                    IntakeSlate.item_id == batch.item_id,
                )
            ).scalar_one_or_none()
            photos = sorted(db.execute(select(IntakePhoto).where(IntakePhoto.batch_id == batch.id)).scalars().all(), key=self._timeline_sort_key)
            public_photos = [photo for photo in photos if photo.is_public_listing_candidate and not photo.is_slate and not self._timeline_photo_deleted(photo)]
            if not public_photos:
                continue
            listing = db.get(Listing, batch.draft_listing_id)
            if listing is None or self._listing_is_externally_active(db, listing):
                continue
            before_signature = self._listing_refresh_signature(listing)
            refreshed_listing = self._create_or_update_listing_from_batch(
                db,
                slate=slate,
                batch=batch,
                photos=public_photos,
                force_regenerate=True,
            )
            if refreshed_listing is None:
                continue
            after_signature = self._listing_refresh_signature(refreshed_listing)
            if before_signature != after_signature:
                refreshed += 1
        if refreshed:
            db.commit()
        return refreshed

    def recover_existing_slates(self, db: Session, *, user: User, limit: int | None = None) -> int:
        query = select(IntakePhoto).where(
            IntakePhoto.user_id == user.id,
            IntakePhoto.is_slate.is_(False),
        )
        photos = sorted(db.execute(query).scalars().all(), key=self._timeline_sort_key)
        pending: list[IntakePhoto] = []
        for photo in photos:
            metadata = photo.metadata_json if isinstance(photo.metadata_json, dict) else {}
            if int(metadata.get("slate_detection_version") or 0) >= SLATE_DETECTION_VERSION:
                continue
            pending.append(photo)
            if isinstance(limit, int) and limit > 0 and len(pending) >= limit:
                break
        recovered = 0
        touched = 0
        for photo in pending:
            payload = self.decode_slate_payload_isolated(photo.local_path) or self.decode_slate_payload(photo.local_path)
            if payload is None and not self.classify_photo_for_intake(photo.local_path).get("is_probable_slate"):
                metadata = dict(photo.metadata_json or {})
                metadata["slate_detection_version"] = SLATE_DETECTION_VERSION
                metadata["slate_detection_checked_at"] = datetime.now(UTC).isoformat()
                metadata["slate_detection_result"] = "no_match"
                photo.metadata_json = metadata
                db.add(photo)
                touched += 1
                continue
            metadata = dict(photo.metadata_json or {})
            metadata["slate_detection_version"] = SLATE_DETECTION_VERSION
            metadata["slate_detection_checked_at"] = datetime.now(UTC).isoformat()
            metadata["slate_detection_result"] = "matched" if payload else "no_match"
            photo.metadata_json = metadata
            db.add(photo)
            touched += 1
            if not payload:
                continue
            logger.info(
                "intake_existing_slate_recovered",
                extra={"user_id": user.id, "photo_id": photo.id, "item_id": str(payload.get("item_id") or "")},
            )
            self._upsert_slate_from_qr(db, user=user, qr_payload=payload, photo=photo)
            recovered += 1
        if recovered or touched:
            db.commit()
        return recovered

    def normalize_recovery_item_id(self, raw_value: Any) -> str | None:
        """Normalize only a complete SP item-ID token, with OCR repair limited to digit segments."""
        raw = str(raw_value or "")
        compact = re.sub(r"\s*[-\u2010-\u2015\u2212]\s*", "-", raw.strip()).upper()
        if STRICT_ITEM_ID_RE.fullmatch(compact):
            return compact
        matched = ITEM_ID_TEXT_RE.search(raw)
        if not matched:
            return None
        token = re.sub(r"\s*[-\u2010-\u2015\u2212]\s*", "-", matched.group(0).strip()).upper()
        parts = token.split("-")
        if len(parts) != 3 or parts[0] != "SP":
            return None
        repaired = f"SP-{parts[1].translate(str.maketrans({'O': '0', 'I': '1', 'L': '1', '|': '1', 'S': '5'}))}-{parts[2].translate(str.maketrans({'O': '0', 'I': '1', 'L': '1', '|': '1', 'S': '5'}))}"
        return repaired if STRICT_ITEM_ID_RE.fullmatch(repaired) else None

    def evaluate_slate_recovery_candidate(
        self,
        db: Session,
        *,
        user_id: int,
        photo: IntakePhoto,
        pipeline_version: str = SLATE_RECOVERY_PIPELINE_VERSION,
    ) -> IntakeSlateRecoveryCandidate:
        """Evaluate persisted evidence only; this method never mutates the source photo or item graph."""
        if photo.user_id != user_id:
            raise ValueError("Intake photo does not belong to user.")
        if pipeline_version == SLATE_RECOVERY_PIPELINE_VERSION_V2:
            return self._evaluate_slate_recovery_candidate_v2(
                db, user_id=user_id, photo=photo, pipeline_version=pipeline_version
            )
        metadata = photo.metadata_json if isinstance(photo.metadata_json, dict) else {}
        qr_payload = self._coerce_qr_payload(metadata)
        ocr_text = self._stored_ocr_text(metadata)
        evidence_sources: list[dict[str, Any]] = []
        raw_candidates: list[tuple[str, str]] = []

        if isinstance(qr_payload, dict):
            raw_candidates.append(("stored_qr_payload", str(qr_payload.get("item_id") or "")))
            evidence_sources.append({"source": "stored_qr_payload", "present": True})
        for key in ("item_id", "manual_item_id", "detected_item_id", "slate_item_id"):
            value = metadata.get(key)
            if value:
                raw_candidates.append((f"metadata.{key}", str(value)))
        if photo.item_id:
            raw_candidates.append(("photo.item_id", photo.item_id))
        if ocr_text:
            raw_candidates.append(("stored_ocr_text", ocr_text))
        if photo.original_filename:
            raw_candidates.append(("original_filename", photo.original_filename))

        raw_item_id: str | None = None
        normalized_item_id: str | None = None
        item_source: str | None = None
        for source, candidate in raw_candidates:
            extracted_raw_item_id = self._extract_recovery_raw_item_id(candidate)
            if extracted_raw_item_id and raw_item_id is None:
                raw_item_id = extracted_raw_item_id
            normalized = self.normalize_recovery_item_id(extracted_raw_item_id or candidate)
            if normalized:
                raw_item_id, normalized_item_id, item_source = extracted_raw_item_id or candidate, normalized, source
                break

        observed = db.execute(
            select(SlateObservation).where(SlateObservation.user_id == user_id, SlateObservation.media_id == photo.id)
        ).scalar_one_or_none()
        if photo.is_slate or qr_payload or observed:
            classification, classification_confidence = "confirmed_slate", 1.0 if photo.is_slate else 0.98
        elif normalized_item_id:
            classification, classification_confidence = "probable_slate", 0.85
        elif ocr_text or metadata.get("slate_detection_result") in {"probable_slate_candidate", "matched"}:
            classification, classification_confidence = "unresolved", 0.45
        else:
            classification, classification_confidence = "not_slate", 0.95

        canonical = slate = batch = None
        match_status = "unresolved"
        if normalized_item_id:
            canonical = db.execute(select(CanonicalItem).where(CanonicalItem.user_id == user_id, CanonicalItem.item_id == normalized_item_id)).scalar_one_or_none()
            slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id, IntakeSlate.item_id == normalized_item_id)).scalar_one_or_none()
            if slate:
                batch = db.execute(
                    select(IntakePhotoBatch).where(
                        IntakePhotoBatch.user_id == user_id,
                        IntakePhotoBatch.item_id == normalized_item_id,
                        IntakePhotoBatch.slate_id == slate.id,
                    ).order_by(IntakePhotoBatch.id.asc())
                ).scalars().first()
            if batch is None:
                batch = db.execute(
                    select(IntakePhotoBatch).where(
                        IntakePhotoBatch.user_id == user_id,
                        IntakePhotoBatch.item_id == normalized_item_id,
                    ).order_by(IntakePhotoBatch.id.asc())
                ).scalars().first()
            match_status = "exact_match" if canonical else "no_match"

        payload_values = qr_payload or {}
        candidate = db.execute(
            select(IntakeSlateRecoveryCandidate).where(
                IntakeSlateRecoveryCandidate.user_id == user_id,
                IntakeSlateRecoveryCandidate.intake_photo_id == photo.id,
                IntakeSlateRecoveryCandidate.pipeline_version == pipeline_version,
            )
        ).scalar_one_or_none()
        values = {
            "classification": classification,
            "raw_item_id": raw_item_id,
            "normalized_item_id": normalized_item_id,
            "box_id": self._recovery_string(payload_values.get("box_id") or metadata.get("box_id")),
            "location": self._recovery_string(payload_values.get("location") or metadata.get("location")),
            "quantity": self._recovery_string(payload_values.get("quantity") or metadata.get("quantity")),
            "condition": self._recovery_string(payload_values.get("condition") or metadata.get("condition")),
            "notes": self._recovery_string(payload_values.get("notes") or metadata.get("notes")),
            "stored_qr_payload_json": qr_payload,
            "stored_ocr_text": ocr_text,
            "evidence_json": {
                "photo_is_slate": bool(photo.is_slate), "source_candidates": evidence_sources,
                "item_id_source": item_source, "raw_candidate_sources": [source for source, _ in raw_candidates],
                "matched_slate_observation_id": observed.id if observed else None,
                "no_source_photo_mutation": True,
            },
            "classification_confidence": classification_confidence,
            "item_id_confidence": 1.0 if item_source == "stored_qr_payload" else (0.9 if normalized_item_id else None),
            "match_status": match_status,
            "matched_canonical_item_id": canonical.id if canonical else None,
            "matched_intake_slate_id": slate.id if slate else None,
            "matched_batch_id": batch.id if batch else None,
            "review_status": "pending",
            "accepted_rejected_state": "unreviewed",
        }
        if candidate is None:
            candidate = IntakeSlateRecoveryCandidate(user_id=user_id, intake_photo_id=photo.id, pipeline_version=pipeline_version, **values)
        else:
            for field, value in values.items():
                setattr(candidate, field, value)
        db.add(candidate)
        return candidate

    def _evaluate_slate_recovery_candidate_v2(
        self,
        db: Session,
        *,
        user_id: int,
        photo: IntakePhoto,
        pipeline_version: str,
    ) -> IntakeSlateRecoveryCandidate:
        """V2 rejects grouping/assignment state as slate or Item-ID evidence.

        The only Item ID inputs are evidence stored on this photo (QR, OCR,
        generated-slate payload, filename) or a slate observation directly
        tied to this photo. Existing assignment context is recorded only to
        explain that it was deliberately excluded.
        """
        metadata = photo.metadata_json if isinstance(photo.metadata_json, dict) else {}
        qr_payload = self._coerce_qr_payload(metadata)
        ocr_text = self._stored_ocr_text(metadata)
        detection = metadata.get("slate_detection") if isinstance(metadata.get("slate_detection"), dict) else {}
        observed = db.execute(
            select(SlateObservation).where(SlateObservation.user_id == user_id, SlateObservation.media_id == photo.id)
        ).scalar_one_or_none()
        generated_payload = self._generated_slate_payload(metadata)

        independent_candidates: list[tuple[str, str]] = []
        if qr_payload and qr_payload.get("item_id"):
            independent_candidates.append(("stored_qr_payload", str(qr_payload["item_id"])))
        if observed and observed.item_id:
            independent_candidates.append(("direct_slate_observation", str(observed.item_id)))
        if generated_payload and generated_payload.get("item_id"):
            independent_candidates.append(("generated_slate_metadata", str(generated_payload["item_id"])))
        if ocr_text:
            independent_candidates.append(("stored_ocr_text", ocr_text))
        if photo.original_filename:
            independent_candidates.append(("original_filename", photo.original_filename))

        raw_item_id: str | None = None
        normalized_item_id: str | None = None
        item_source: str | None = None
        for source, value in independent_candidates:
            extracted = self._extract_recovery_raw_item_id(value)
            if extracted and raw_item_id is None:
                raw_item_id = extracted
            normalized = self.normalize_recovery_item_id(extracted or value)
            if normalized:
                raw_item_id, normalized_item_id, item_source = extracted or value, normalized, source
                break

        strong_sources: list[str] = []
        if photo.is_slate:
            strong_sources.append("existing_is_slate")
        if item_source == "stored_qr_payload":
            strong_sources.append("valid_stored_qr_payload")
        if observed:
            strong_sources.append("direct_slate_observation")
        if generated_payload:
            strong_sources.append("generated_slate_metadata")

        weak_sources: list[str] = []
        if item_source == "stored_ocr_text":
            weak_sources.append("stored_ocr_item_id")
        if item_source == "original_filename":
            weak_sources.append("filename_item_id")
        if detection.get("is_qr_candidate"):
            weak_sources.append("stored_qr_candidate_region")
        if float(detection.get("layout_score") or 0) >= 0.72:
            weak_sources.append("stored_slate_layout")
        if metadata.get("slate_detection_result") == "probable_slate_candidate":
            weak_sources.append("stored_probable_slate_detection")
        weak_sources = list(dict.fromkeys(weak_sources))

        if strong_sources:
            classification, classification_confidence = "confirmed_slate", 1.0 if photo.is_slate else 0.98
        elif normalized_item_id and len(weak_sources) >= 2:
            classification, classification_confidence = "probable_slate", 0.80
        elif independent_candidates or weak_sources:
            classification, classification_confidence = "unresolved", 0.45
        else:
            classification, classification_confidence = "not_slate", 0.99

        canonical = slate = batch = None
        match_status = "unresolved"
        if normalized_item_id:
            canonical = db.execute(select(CanonicalItem).where(CanonicalItem.user_id == user_id, CanonicalItem.item_id == normalized_item_id)).scalar_one_or_none()
            slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id, IntakeSlate.item_id == normalized_item_id)).scalar_one_or_none()
            batch = db.execute(
                select(IntakePhotoBatch).where(
                    IntakePhotoBatch.user_id == user_id, IntakePhotoBatch.item_id == normalized_item_id
                ).order_by(IntakePhotoBatch.id.asc())
            ).scalars().first()
            match_status = "exact_match" if canonical else "no_match"

        candidate = db.execute(
            select(IntakeSlateRecoveryCandidate).where(
                IntakeSlateRecoveryCandidate.user_id == user_id,
                IntakeSlateRecoveryCandidate.intake_photo_id == photo.id,
                IntakeSlateRecoveryCandidate.pipeline_version == pipeline_version,
            )
        ).scalar_one_or_none()
        rejected = ["photo.item_id", "photo.batch_id"]
        rejected.extend(f"metadata.{key}" for key in ("item_id", "manual_item_id", "detected_item_id", "slate_item_id") if metadata.get(key))
        values = {
            "classification": classification,
            "raw_item_id": raw_item_id,
            "normalized_item_id": normalized_item_id,
            "box_id": self._recovery_string((qr_payload or generated_payload or {}).get("box_id")),
            "location": self._recovery_string((qr_payload or generated_payload or {}).get("location")),
            "quantity": self._recovery_string((qr_payload or generated_payload or {}).get("quantity")),
            "condition": self._recovery_string((qr_payload or generated_payload or {}).get("condition")),
            "notes": self._recovery_string((qr_payload or generated_payload or {}).get("notes")),
            "stored_qr_payload_json": qr_payload,
            "stored_ocr_text": ocr_text,
            "evidence_json": {
                "pipeline": pipeline_version,
                "item_id_evidence_source": item_source,
                "classification_evidence_sources": {"strong": strong_sources, "weak": weak_sources},
                "assignment_context_used_for_matching": {"used": False, "photo_item_id_present": bool(photo.item_id), "batch_id_present": photo.batch_id is not None},
                "independent_evidence": {"qr_payload": bool(qr_payload), "ocr_text": bool(ocr_text), "direct_slate_observation_id": observed.id if observed else None, "generated_slate_metadata": bool(generated_payload), "filename": photo.original_filename if item_source == "original_filename" else None},
                "circular_evidence_rejected": rejected,
                "no_source_photo_mutation": True,
            },
            "classification_confidence": classification_confidence,
            "item_id_confidence": 1.0 if item_source in {"stored_qr_payload", "direct_slate_observation", "generated_slate_metadata"} else (0.8 if normalized_item_id else None),
            "match_status": match_status,
            "matched_canonical_item_id": canonical.id if canonical else None,
            "matched_intake_slate_id": slate.id if slate else None,
            "matched_batch_id": batch.id if batch else None,
            "review_status": "pending",
            "accepted_rejected_state": "unreviewed",
        }
        if candidate is None:
            candidate = IntakeSlateRecoveryCandidate(user_id=user_id, intake_photo_id=photo.id, pipeline_version=pipeline_version, **values)
        else:
            for field, value in values.items():
                setattr(candidate, field, value)
        db.add(candidate)
        return candidate

    def run_slate_recovery_candidates(
        self, db: Session, *, user_id: int, photo_ids: list[int] | None = None,
        limit: int | None = None, pipeline_version: str = SLATE_RECOVERY_PIPELINE_VERSION,
    ) -> dict[str, int]:
        query = select(IntakePhoto).where(IntakePhoto.user_id == user_id).order_by(IntakePhoto.id.asc())
        if photo_ids is not None:
            unique_ids = list(dict.fromkeys(int(photo_id) for photo_id in photo_ids))
            if not unique_ids:
                return self._empty_slate_recovery_counts()
            query = query.where(IntakePhoto.id.in_(unique_ids))
        if limit:
            query = query.limit(limit)
        photos = db.execute(query).scalars().all()
        counts = self._empty_slate_recovery_counts()
        for photo in photos:
            try:
                candidate = self.evaluate_slate_recovery_candidate(db, user_id=user_id, photo=photo, pipeline_version=pipeline_version)
                counts["evaluated"] += 1
                counts[{"confirmed_slate": "confirmed_slates", "probable_slate": "probable_slates", "not_slate": "not_slates", "unresolved": "unresolved"}[candidate.classification]] += 1
                if candidate.normalized_item_id:
                    counts["valid_item_ids"] += 1
                    if candidate.match_status == "exact_match":
                        counts["exact_existing_item_matches"] += 1
                    elif candidate.match_status == "no_match":
                        counts["no_matches"] += 1
                elif candidate.raw_item_id:
                    counts["invalid_item_ids"] += 1
            except Exception:
                logger.exception("intake_slate_recovery_candidate_failed", extra={"user_id": user_id, "photo_id": photo.id})
                counts["errors"] += 1
        db.commit()
        return counts

    @staticmethod
    def _extract_recovery_raw_item_id(value: Any) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        return ITEM_ID_LIKE_TEXT_RE.search(raw).group(0) if ITEM_ID_LIKE_TEXT_RE.search(raw) else None

    @staticmethod
    def _generated_slate_payload(metadata: dict[str, Any]) -> dict[str, Any] | None:
        payload = metadata.get("generated_slate_payload") or metadata.get("slate_payload")
        if isinstance(payload, dict):
            return payload
        if metadata.get("generated_slate") is True or metadata.get("slate_generated") is True:
            return metadata
        return None

    @staticmethod
    def _stored_ocr_text(metadata: dict[str, Any]) -> str | None:
        for key in ("ocr_text", "ocr", "extracted_ocr_text", "slate_ocr_text"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                text = value.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return None

    @staticmethod
    def _recovery_string(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _empty_slate_recovery_counts() -> dict[str, int]:
        return {"evaluated": 0, "confirmed_slates": 0, "probable_slates": 0, "not_slates": 0,
                "unresolved": 0, "valid_item_ids": 0, "invalid_item_ids": 0, "exact_existing_item_matches": 0,
                "no_matches": 0, "errors": 0}

    def _canonical_item_for(self, db: Session, *, user_id: int, item_id: str, slate: IntakeSlate | None = None) -> CanonicalItem:
        normalized = str(item_id or "").strip()
        item = db.execute(
            select(CanonicalItem).where(CanonicalItem.user_id == user_id, CanonicalItem.item_id == normalized)
        ).scalar_one_or_none()
        if item is None:
            item = CanonicalItem(
                user_id=user_id,
                item_id=normalized,
                inventory_sku=normalized,
                current_slate_id=slate.id if slate else None,
                current_listing_id=slate.listing_id if slate else None,
                status="provisional",
                metadata_json={"identity_source": "head_slate"},
            )
            db.add(item)
            db.flush()
        elif slate:
            item.current_slate_id = slate.id
            item.current_listing_id = slate.listing_id or item.current_listing_id
            db.add(item)
        return item

    @staticmethod
    def _listing_confidence(*, generated: dict[str, Any], photo_count: int, batch: IntakePhotoBatch) -> float:
        score = 0.45 + min(photo_count, 6) * 0.05
        if generated.get("category_suggestion"):
            score += 0.08
        if generated.get("item_specifics"):
            score += 0.08
        if not (generated.get("missing_information") or []):
            score += 0.08
        if batch.metadata_json and batch.metadata_json.get("tail_boundary_used"):
            score -= 0.12
        return round(max(0.1, min(score, 0.98)), 2)

    def _create_intake_notification(
        self,
        db: Session,
        *,
        user_id: int,
        canonical_item_id: int | None,
        notification_type: str,
        title: str,
        message: str,
        href: str,
    ) -> None:
        existing = db.execute(
            select(IntakeNotification).where(
                IntakeNotification.user_id == user_id,
                IntakeNotification.canonical_item_id == canonical_item_id,
                IntakeNotification.notification_type == notification_type,
                IntakeNotification.read_at.is_(None),
            )
        ).scalar_one_or_none()
        if existing:
            existing.title = title
            existing.message = message
            existing.href = href
            db.add(existing)
            return
        db.add(
            IntakeNotification(
                user_id=user_id,
                canonical_item_id=canonical_item_id,
                notification_type=notification_type,
                title=title,
                message=message,
                href=href,
            )
        )

    def _upsert_canonical_fact(
        self,
        db: Session,
        *,
        item: CanonicalItem,
        field_name: str,
        value: Any,
        source_type: str,
        source_identifier: str | None,
        confidence: float,
        value_status: str,
        precedence: int,
        effective_at: datetime | None,
        is_locked: bool = False,
    ) -> CanonicalItemFact | None:
        if value in (None, "", [], {}):
            return None
        normalized = json.dumps(value, sort_keys=True, default=str) if isinstance(value, (dict, list)) else str(value).strip()
        current = db.execute(
            select(CanonicalItemFact)
            .where(
                CanonicalItemFact.canonical_item_id == item.id,
                CanonicalItemFact.field_name == field_name,
                CanonicalItemFact.is_current.is_(True),
            )
            .order_by(CanonicalItemFact.precedence.desc(), CanonicalItemFact.updated_at.desc(), CanonicalItemFact.id.desc())
        ).scalar_one_or_none()
        if current and current.normalized_value == normalized:
            return current
        if current and current.is_locked:
            # Preserve the new evidence, but never silently override an operator lock.
            conflict = CanonicalItemFact(
                canonical_item_id=item.id,
                field_name=field_name,
                value_json=value,
                normalized_value=normalized,
                source_type=source_type,
                source_identifier=source_identifier,
                confidence=confidence,
                value_status=value_status,
                precedence=precedence,
                is_current=False,
                conflict_state="blocked_by_operator_lock",
                effective_at=effective_at,
            )
            db.add(conflict)
            return conflict
        if current and current.precedence > precedence:
            conflict = CanonicalItemFact(
                canonical_item_id=item.id,
                field_name=field_name,
                value_json=value,
                normalized_value=normalized,
                source_type=source_type,
                source_identifier=source_identifier,
                confidence=confidence,
                value_status=value_status,
                precedence=precedence,
                is_current=False,
                conflict_state="lower_precedence_evidence",
                effective_at=effective_at,
            )
            db.add(conflict)
            return conflict
        if current:
            current.is_current = False
            db.add(current)
        fact = CanonicalItemFact(
            canonical_item_id=item.id,
            field_name=field_name,
            value_json=value,
            normalized_value=normalized,
            source_type=source_type,
            source_identifier=source_identifier,
            confidence=confidence,
            value_status=value_status,
            precedence=precedence,
            is_locked=is_locked,
            is_current=True,
            effective_at=effective_at,
        )
        db.add(fact)
        return fact

    def _record_slate_observation(
        self,
        db: Session,
        *,
        user_id: int,
        slate: IntakeSlate,
        photo: IntakePhoto | None,
        payload: dict[str, Any],
        source_type: str,
        operator_confirmed: bool,
    ) -> CanonicalItem:
        item = self._canonical_item_for(db, user_id=user_id, item_id=slate.item_id, slate=slate)
        existing = None
        if photo:
            existing = db.execute(select(SlateObservation).where(SlateObservation.media_id == photo.id)).scalar_one_or_none()
        else:
            existing = db.execute(
                select(SlateObservation).where(
                    SlateObservation.intake_slate_id == slate.id,
                    SlateObservation.media_id.is_(None),
                )
            ).scalar_one_or_none()
        capture_at = (photo.captured_at if photo else None) or self._parse_datetime(payload.get("created_at"))
        if existing is None:
            observation = SlateObservation(
                user_id=user_id,
                canonical_item_id=item.id,
                intake_slate_id=slate.id,
                media_id=photo.id if photo else None,
                item_id=slate.item_id,
                observation_type=("supplemental" if item.current_slate_id and item.current_slate_id != slate.id else "original"),
                template_version=int(payload.get("version") or SLATE_VERSION),
                confidence=1.0 if operator_confirmed else 0.92,
                capture_timestamp=capture_at,
                raw_qr_json=payload if source_type != "operator_edit" else None,
                parsed_values_json=payload,
                reconciliation_status="resolved",
                operator_confirmed=operator_confirmed,
            )
            db.add(observation)
        else:
            existing.canonical_item_id = item.id
            existing.intake_slate_id = slate.id
            existing.item_id = slate.item_id
            existing.parsed_values_json = payload
            existing.capture_timestamp = capture_at
            db.add(existing)
        # A decoded PosterPro slate is operator-authored input even if QR
        # recovery itself was automatic. Equal-precedence later observations
        # may update mutable operational fields such as box and location.
        precedence = 70 if (operator_confirmed or source_type == "slate_qr") else 60
        for field_name in ("box_id", "location", "title", "brand", "model", "condition", "notes", "flaws", "weight", "length", "width", "height", "packed"):
            self._upsert_canonical_fact(
                db,
                item=item,
                field_name=field_name,
                value=payload.get(field_name),
                source_type=source_type,
                source_identifier=str(photo.id) if photo else str(slate.id),
                confidence=1.0 if operator_confirmed else 0.96,
                value_status="verified" if (operator_confirmed or source_type == "slate_qr") else "inferred",
                precedence=precedence,
                effective_at=capture_at,
            )
        item.current_slate_id = slate.id
        item.current_listing_id = slate.listing_id or item.current_listing_id
        db.add(item)
        return item

    def _upsert_slate_from_qr(self, db: Session, *, user: User, qr_payload: dict[str, Any], photo: IntakePhoto) -> IntakeSlate:
        session = self.get_or_create_session(
            db,
            user=user,
            payload={
                "session_id": qr_payload.get("session_id"),
                "default_location": qr_payload.get("location"),
            },
        )
        slate = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user.id, IntakeSlate.item_id == str(qr_payload.get("item_id") or "").strip())).scalar_one_or_none()
        if slate is None:
            slate = IntakeSlate(
                user_id=user.id,
                intake_session_id=session.id,
                session_id=session.session_id,
                item_id=str(qr_payload.get("item_id") or "").strip(),
                box_id=str(qr_payload.get("box_id") or "").strip() or None,
                location=str(qr_payload.get("location") or "").strip() or None,
                title=str(qr_payload.get("title") or "").strip() or None,
                brand=str(qr_payload.get("brand") or "").strip() or None,
                model=str(qr_payload.get("model") or "").strip() or None,
                condition=str(qr_payload.get("condition") or "").strip() or None,
                notes=str(qr_payload.get("notes") or "").strip() or None,
                flaws=str(qr_payload.get("flaws") or "").strip() or None,
                weight=str(qr_payload.get("weight") or "").strip() or None,
                length=str(qr_payload.get("length") or "").strip() or None,
                width=str(qr_payload.get("width") or "").strip() or None,
                height=str(qr_payload.get("height") or "").strip() or None,
                packed=bool(qr_payload.get("packed")),
                qr_payload_json=qr_payload,
                status="captured",
            )
            db.add(slate)
            db.flush()
        else:
            slate.intake_session_id = session.id
            slate.session_id = session.session_id
            slate.box_id = str(qr_payload.get("box_id") or "").strip() or slate.box_id
            slate.location = str(qr_payload.get("location") or "").strip() or slate.location
            slate.title = str(qr_payload.get("title") or "").strip() or slate.title
            slate.brand = str(qr_payload.get("brand") or "").strip() or slate.brand
            slate.model = str(qr_payload.get("model") or "").strip() or slate.model
            slate.condition = str(qr_payload.get("condition") or "").strip() or slate.condition
            slate.notes = str(qr_payload.get("notes") or "").strip() or slate.notes
            slate.flaws = str(qr_payload.get("flaws") or "").strip() or slate.flaws
            slate.weight = str(qr_payload.get("weight") or "").strip() or slate.weight
            slate.length = str(qr_payload.get("length") or "").strip() or slate.length
            slate.width = str(qr_payload.get("width") or "").strip() or slate.width
            slate.height = str(qr_payload.get("height") or "").strip() or slate.height
            slate.packed = bool(qr_payload.get("packed"))
            slate.qr_payload_json = qr_payload
            slate.status = "captured"
            db.add(slate)
        self._set_best_slate_image(db, slate=slate, photo=photo, qr_payload=qr_payload)
        photo.item_id = slate.item_id
        photo.is_slate = True
        photo.is_public_listing_candidate = False
        photo.is_internal_only = True
        photo.image_type = "slate"
        photo.metadata_json = {**(photo.metadata_json or {}), "qr_payload": qr_payload}
        db.add(photo)
        self._record_slate_observation(
            db,
            user_id=user.id,
            slate=slate,
            photo=photo,
            payload=qr_payload,
            source_type="slate_qr",
            operator_confirmed=False,
        )
        return slate

    def _set_best_slate_image(self, db: Session, *, slate: IntakeSlate, photo: IntakePhoto, qr_payload: dict[str, Any] | None = None) -> None:
        """Keep the best-quality capture as the primary slate image."""
        existing_photo = db.get(IntakePhoto, slate.slate_image_id) if slate.slate_image_id else None
        new_score = self._slate_photo_quality_score(photo, qr_payload=qr_payload)
        current_score = self._slate_photo_quality_score(
            existing_photo,
            qr_payload=slate.qr_payload_json if isinstance(slate.qr_payload_json, dict) else None,
        )
        if existing_photo is None or new_score >= current_score:
            if existing_photo is not None and existing_photo.id != photo.id:
                existing_metadata = dict(existing_photo.metadata_json or {})
                existing_metadata["duplicate_slate_of_photo_id"] = photo.id
                existing_metadata["duplicate_slate_item_id"] = slate.item_id
                existing_metadata["duplicate_slate_replaced_by_photo_id"] = photo.id
                existing_metadata["duplicate_slate_quality_score"] = round(current_score, 3)
                existing_photo.metadata_json = existing_metadata
                db.add(existing_photo)
            slate.slate_image_id = photo.id
            db.add(slate)
            return
        duplicate_metadata = dict(photo.metadata_json or {})
        duplicate_metadata["duplicate_slate_of_photo_id"] = existing_photo.id
        duplicate_metadata["duplicate_slate_item_id"] = slate.item_id
        duplicate_metadata["duplicate_slate_kept_photo_id"] = existing_photo.id
        duplicate_metadata["duplicate_slate_quality_score"] = round(new_score, 3)
        photo.metadata_json = duplicate_metadata
        db.add(photo)

    def _slate_photo_quality_score(self, photo: IntakePhoto | None, *, qr_payload: dict[str, Any] | None = None) -> float:
        if photo is None:
            return -1.0
        metadata = photo.metadata_json if isinstance(photo.metadata_json, dict) else {}
        detection = metadata.get("slate_detection") if isinstance(metadata.get("slate_detection"), dict) else {}
        score = self._focus_score(photo.local_path) / 100.0
        if photo.is_slate:
            score += 5.0
        if qr_payload or metadata.get("qr_payload"):
            score += 3.0
        if self._stored_ocr_text(metadata):
            score += 2.0
        if str(metadata.get("slate_detection_result") or "").strip() == "matched":
            score += 2.0
        elif str(metadata.get("slate_detection_result") or "").strip() == "probable_slate_candidate":
            score += 1.0
        score += min(2.0, float(detection.get("layout_score") or 0.0) * 2.0)
        return score

    def _prepare_listing_image(self, *, source: Path, target: Path) -> None:
        if not source.exists():
            return
        try:
            with Image.open(source) as img:
                prepared = ImageOps.exif_transpose(img).convert("RGB")
                prepared = ImageOps.autocontrast(prepared, cutoff=1)
                prepared = ImageEnhance.Contrast(prepared).enhance(1.06)
                prepared = ImageEnhance.Sharpness(prepared).enhance(1.08)
                if target.suffix.lower() in {".png", ".webp"}:
                    prepared.save(target)
                else:
                    prepared.save(target, format="JPEG", quality=92, optimize=True)
                return
        except Exception:
            shutil.copy2(source, target)

    def _rebuild_manual_marked_boundaries(self, db: Session, *, user: User) -> dict[str, Any]:
        ordered = self._ordered_photos(db, user_id=user.id)
        if not ordered:
            return {"runs": 0, "deduplicated": 0, "fallback_slates_created": 0}

        session = self.get_or_create_session(
            db,
            user=user,
            payload={"default_location": self.settings_for_user(user).get("default_location")},
        )
        item_prefix = str(session.item_prefix or self.settings_for_user(user).get("default_item_prefix") or "SP")
        existing_slates = db.execute(
            select(IntakeSlate)
            .where(IntakeSlate.user_id == user.id)
            .order_by(IntakeSlate.created_at.asc(), IntakeSlate.id.asc())
        ).scalars().all()
        slates_by_item_id = {slate.item_id: slate for slate in existing_slates}
        today_token = datetime.now(UTC).astimezone().strftime("%Y%m%d")
        reusable_manual_slates = [
            slate for slate in existing_slates
            if str(slate.item_id or "").startswith(f"{self._slug_token(item_prefix).upper()}-{today_token}-")
        ]
        next_item_index = len(reusable_manual_slates) + 1 if reusable_manual_slates else 1

        runs: list[list[IntakePhoto]] = []
        current_run: list[IntakePhoto] = []
        for photo in ordered:
            if photo.is_slate:
                current_run.append(photo)
                continue
            if current_run:
                runs.append(current_run)
                current_run = []
        if current_run:
            runs.append(current_run)

        fallback_created = 0
        deduplicated = 0
        kept_item_ids: list[str] = []

        for run_index, run in enumerate(runs, start=1):
            primary = max(run, key=self._manual_slate_quality_score)
            qr_payload = self._coerce_qr_payload(primary.metadata_json)
            primary_item_id = str((qr_payload or {}).get("item_id") or "").strip()
            if not primary_item_id:
                if run_index <= len(reusable_manual_slates):
                    primary_item_id = str(reusable_manual_slates[run_index - 1].item_id)
                else:
                    while True:
                        candidate = self._build_fallback_item_id(prefix=item_prefix, sequence=next_item_index)
                        next_item_index += 1
                        if candidate not in slates_by_item_id:
                            primary_item_id = candidate
                            break
                fallback_payload = self._build_manual_fallback_payload(
                    session_id=session.session_id,
                    item_id=primary_item_id,
                    photo=primary,
                    user=user,
                )
                slate = self._upsert_slate_from_qr(db, user=user, qr_payload=fallback_payload, photo=primary)
                slates_by_item_id[primary_item_id] = slate
                fallback_created += 1
                primary_metadata = dict(primary.metadata_json or {})
                primary_metadata["manual_fallback_slate"] = True
                primary_metadata["manual_fallback_generated_at"] = datetime.now(UTC).isoformat()
                primary.metadata_json = primary_metadata
                db.add(primary)
            else:
                primary.item_id = primary_item_id
                primary.is_slate = True
                primary.is_internal_only = True
                primary.is_public_listing_candidate = False
                primary.image_type = "slate"
                db.add(primary)

            kept_item_ids.append(primary_item_id)

            for duplicate in run:
                if duplicate.id == primary.id:
                    continue
                deduplicated += 1
                duplicate_metadata = dict(duplicate.metadata_json or {})
                duplicate_metadata["duplicate_slate_of_photo_id"] = primary.id
                duplicate_metadata["duplicate_slate_item_id"] = primary_item_id
                duplicate_metadata["manual_assignment_locked"] = True
                duplicate_metadata["manual_item_id"] = primary_item_id
                duplicate_metadata["duplicate_slate_demoted_at"] = datetime.now(UTC).isoformat()
                duplicate.metadata_json = duplicate_metadata
                duplicate.item_id = primary_item_id
                duplicate.is_slate = False
                duplicate.is_internal_only = True
                duplicate.is_public_listing_candidate = False
                duplicate.image_type = "internal_duplicate_slate"
                db.add(duplicate)

        if kept_item_ids:
            for slate in existing_slates:
                if slate.item_id in kept_item_ids or slate.listing_id:
                    continue
                if slate.status in {"captured", "draft", "manual_review"}:
                    slate.status = "superseded"
                    db.add(slate)

        db.commit()
        return {
            "runs": len(runs),
            "deduplicated": deduplicated,
            "fallback_slates_created": fallback_created,
        }

    def _close_final_batch_for_snapshot(self, db: Session, *, user_id: int) -> None:
        batches = db.execute(
            select(IntakePhotoBatch)
            .where(IntakePhotoBatch.user_id == user_id)
            .order_by(IntakePhotoBatch.last_photo_id.desc().nullslast(), IntakePhotoBatch.id.desc())
        ).scalars().all()
        if not batches:
            return
        latest = batches[0]
        latest.metadata_json = {
            **(latest.metadata_json or {}),
            "stream_closed": True,
            "snapshot_closed_at": datetime.now(UTC).isoformat(),
        }
        if latest.status == "open":
            latest.status = "ready_for_draft"
        db.add(latest)
        db.commit()

    def _build_manual_fallback_payload(self, *, session_id: str, item_id: str, photo: IntakePhoto, user: User) -> dict[str, Any]:
        settings_payload = self.settings_for_user(user)
        created_at = (photo.captured_at or photo.imported_at or datetime.now(UTC)).astimezone().isoformat()
        location = str(settings_payload.get("default_location") or "").strip()
        return {
            "type": SLATE_TYPE,
            "version": SLATE_VERSION,
            "session_id": session_id,
            "item_id": item_id,
            "box_id": "",
            "location": location,
            "title": "",
            "brand": "",
            "model": "",
            "condition": "",
            "notes": "",
            "flaws": "",
            "weight": "",
            "length": "",
            "width": "",
            "height": "",
            "packed": False,
            "boundary_position": "head",
            "created_at": created_at,
        }

    def _build_fallback_item_id(self, *, prefix: str, sequence: int) -> str:
        today = datetime.now(UTC).astimezone().strftime("%Y%m%d")
        prefix_value = self._slug_token(prefix).upper() or "SP"
        return f"{prefix_value}-{today}-{sequence:04d}"

    def _manual_slate_quality_score(self, photo: IntakePhoto) -> float:
        metadata = photo.metadata_json if isinstance(photo.metadata_json, dict) else {}
        detection = metadata.get("slate_detection") if isinstance(metadata.get("slate_detection"), dict) else {}
        score = 0.0
        if str(metadata.get("slate_detection_result") or "").strip() == "matched":
            score += 100.0
        if detection.get("is_qr_candidate"):
            score += 20.0
        score += float(detection.get("layout_score") or 0.0) * 10.0
        score += self._focus_score(photo.local_path) / 100.0
        return score

    def _focus_score(self, image_path: str | None) -> float:
        image = self._load_image_for_cv(str(image_path or ""))
        if image is None:
            return 0.0
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            return float(cv2.Laplacian(gray, cv2.CV_64F).var())
        except Exception:
            return 0.0

    def _coerce_slate_json(self, value: str | None) -> dict[str, Any] | None:
        if not value:
            return None
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        if str(payload.get("type") or "") != SLATE_TYPE:
            return None
        required = ("type", "version", "item_id", "session_id", "created_at")
        if any(not str(payload.get(field) or "").strip() for field in required):
            return None
        return payload

    def _decode_qr_values(self, image: np.ndarray) -> list[str]:
        detector = cv2.QRCodeDetector()
        seen: list[str] = []
        # The generated Slate is already a clean QR image.  Try it before
        # constructing the expensive photographed-image variant set, and stop
        # as soon as the decoded QR is a valid PosterPro payload.
        for decoded in self._decode_qr_variant(detector, image):
            value = str(decoded or "").strip()
            if not value:
                continue
            if self._coerce_slate_json(value):
                return [value]
            if value not in seen:
                seen.append(value)
        for variant in self._qr_variants(image):
            for decoded in self._decode_qr_variant(detector, variant):
                value = str(decoded or "").strip()
                if not value:
                    continue
                if self._coerce_slate_json(value):
                    return [value]
                if value not in seen:
                    seen.append(value)
        return seen

    def _decode_qr_variant(self, detector: cv2.QRCodeDetector, image: np.ndarray) -> list[str]:
        values: list[str] = []

        def record(value: str | None) -> bool:
            normalized = str(value or "").strip()
            if normalized and normalized not in values:
                values.append(normalized)
            return bool(normalized and self._coerce_slate_json(normalized))

        try:
            value, _, _ = detector.detectAndDecode(image)
            if record(value):
                return values
        except Exception:
            pass
        try:
            ok, decoded_info, _, _ = detector.detectAndDecodeMulti(image)
            if ok and decoded_info is not None:
                for item in decoded_info:
                    if record(str(item)):
                        return values
        except Exception:
            pass
        try:
            value, _, _ = detector.detectAndDecodeCurved(image)
            if record(value):
                return values
        except Exception:
            pass
        return values

    def _decode_slate_payload_from_text(self, image: np.ndarray) -> dict[str, Any] | None:
        for source in [image, *self._screen_and_qr_crops(image), *self._rectified_screen_crops(image), *self._qr_anchor_crops(image)]:
            for text in self._ocr_text_variants(source):
                payload = self._coerce_slate_text(text)
                if payload:
                    return payload
        return None

    def _qr_variants(self, image: np.ndarray, *, include_anchor_crops: bool = True) -> list[np.ndarray]:
        variants: list[np.ndarray] = []

        def add(frame: np.ndarray | None) -> None:
            if frame is None:
                return
            variants.append(frame)
            for rotation in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE):
                try:
                    variants.append(cv2.rotate(frame, rotation))
                except Exception:
                    continue

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(gray)
        add(image)
        add(gray)
        add(cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC))
        add(cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC))
        add(clahe)
        add(cv2.resize(clahe, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC))
        add(cv2.GaussianBlur(gray, (5, 5), 0))
        add(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
        add(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11))

        try:
            pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            pil_image = ImageOps.exif_transpose(pil_image)
            boosted = ImageEnhance.Contrast(ImageOps.autocontrast(pil_image, cutoff=1)).enhance(1.2)
            add(cv2.cvtColor(np.array(boosted), cv2.COLOR_RGB2BGR))
        except Exception:
            pass

        for crop in self._screen_and_qr_crops(image):
            add(crop)
            try:
                crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                add(cv2.resize(crop_gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC))
                add(cv2.threshold(crop_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
                add(cv2.adaptiveThreshold(crop_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9))
            except Exception:
                continue
        # Anchor crops are derived from QR detections in base variants.  The
        # anchor-crop detector must not ask for anchor crops itself: doing so
        # recursively expands image variants until the worker/test process is
        # OOM-killed.  The outer decoder still gets these additional variants.
        if include_anchor_crops:
            for crop in self._qr_anchor_crops(image):
                add(crop)
                try:
                    crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    add(cv2.resize(crop_gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC))
                    add(cv2.threshold(crop_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
                    add(cv2.adaptiveThreshold(crop_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9))
                except Exception:
                    continue

        return variants

    def _screen_and_qr_crops(self, image: np.ndarray) -> list[np.ndarray]:
        crops: list[np.ndarray] = []
        height, width = image.shape[:2]
        image_area = float(max(height * width, 1))

        def append_crop(x: int, y: int, w: int, h: int, pad_ratio: float = 0.08) -> None:
            if w <= 0 or h <= 0:
                return
            pad_x = int(w * pad_ratio)
            pad_y = int(h * pad_ratio)
            left = max(0, x - pad_x)
            top = max(0, y - pad_y)
            right = min(width, x + w + pad_x)
            bottom = min(height, y + h + pad_y)
            crop = image[top:bottom, left:right]
            if crop.size:
                crops.append(crop)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Find the bright photographed screen rectangle first. The slate occupies a bright
        # laptop display within a darker real-world scene, so cropping to that screen region
        # materially improves both QR detection and text fallback.
        bright_mask = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)[1]
        contours, _ = cv2.findContours(bright_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        bright_candidates: list[tuple[float, tuple[int, int, int, int]]] = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < image_area * 0.08:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            ratio = w / max(h, 1)
            if 0.75 <= ratio <= 2.4:
                bright_candidates.append((area, (x, y, w, h)))
        for _, (x, y, w, h) in sorted(bright_candidates, key=lambda item: item[0], reverse=True)[:2]:
            append_crop(x, y, w, h, pad_ratio=0.06)

        # Then find square-ish high-contrast regions that are likely the QR block itself.
        inverted = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        contours, _ = cv2.findContours(inverted, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        qr_candidates: list[tuple[float, tuple[int, int, int, int]]] = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < image_area * 0.01:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            ratio = w / max(h, 1)
            if 0.7 <= ratio <= 1.3:
                qr_candidates.append((area, (x, y, w, h)))
        for _, (x, y, w, h) in sorted(qr_candidates, key=lambda item: item[0], reverse=True)[:3]:
            append_crop(x, y, w, h, pad_ratio=0.18)

        return crops

    def _load_image_for_cv(self, image_path: str) -> np.ndarray | None:
        try:
            with Image.open(str(image_path)) as img:
                prepared = ImageOps.exif_transpose(img).convert("RGB")
                return cv2.cvtColor(np.array(prepared), cv2.COLOR_RGB2BGR)
        except Exception:
            return None

    def public_media_url(self, path: str | None) -> str:
        return self._to_public_media_path(str(path or ""))

    def classify_photo_for_intake(self, image_path: str) -> dict[str, Any]:
        image = self._load_image_for_cv(image_path)
        if image is None:
            return {
                "is_qr_candidate": False,
                "is_probable_slate": False,
                "has_slate_text": False,
                "detected_slate_payload": None,
                "layout_score": 0.0,
                "reason": ["unreadable_image"],
            }
        qr_candidate = self._detect_qr_presence(image)
        layout = self._score_slate_layout(image)
        detected_payload = self._decode_slate_payload_from_text(image)
        ocr_texts = self._ocr_text_variants(image)
        if not ocr_texts:
            ocr_texts = []
        for source in self._screen_and_qr_crops(image):
            if len(ocr_texts) >= 12:
                break
            for text in self._ocr_text_variants(source):
                if text not in ocr_texts:
                    ocr_texts.append(text)
                    if len(ocr_texts) >= 12:
                        break
        for source in self._rectified_screen_crops(image):
            if len(ocr_texts) >= 18:
                break
            for text in self._ocr_text_variants(source):
                if text not in ocr_texts:
                    ocr_texts.append(text)
                    if len(ocr_texts) >= 18:
                        break
        marker_count = self._count_slate_markers(ocr_texts)
        has_slate_text = bool(detected_payload) or marker_count >= 2
        probable = bool(detected_payload) or (
            marker_count >= 2 and layout["layout_score"] >= 0.52
        ) or (
            marker_count >= 3 and layout["layout_score"] >= 0.42
        ) or (
            layout["layout_score"] >= 0.9 and marker_count >= 1
        )
        return {
            "is_qr_candidate": qr_candidate,
            "is_probable_slate": probable,
            "has_slate_text": has_slate_text,
            "detected_slate_payload": detected_payload,
            "ocr_marker_count": marker_count,
            "layout_score": layout["layout_score"],
            "bright_ratio": layout["bright_ratio"],
            "dark_ratio": layout["dark_ratio"],
            "square_contours": layout["square_contours"],
            "reason": [
                *layout["reason"],
                "qr_present" if qr_candidate else "qr_absent",
                f"ocr_markers={marker_count}",
                "posterpro_slate_text" if has_slate_text else "no_slate_text",
            ],
        }

    def _looks_like_slate_candidate(self, image: np.ndarray) -> bool:
        return bool(self.classify_photo_for_intake_from_image(image).get("is_probable_slate"))

    def classify_photo_for_intake_from_image(self, image: np.ndarray) -> dict[str, Any]:
        layout = self._score_slate_layout(image)
        qr_candidate = self._detect_qr_presence(image)
        detected_payload = self._decode_slate_payload_from_text(image)
        ocr_texts = self._ocr_text_variants(image)
        if not ocr_texts:
            ocr_texts = []
        for source in self._screen_and_qr_crops(image):
            if len(ocr_texts) >= 12:
                break
            for text in self._ocr_text_variants(source):
                if text not in ocr_texts:
                    ocr_texts.append(text)
                    if len(ocr_texts) >= 12:
                        break
        for source in self._rectified_screen_crops(image):
            if len(ocr_texts) >= 18:
                break
            for text in self._ocr_text_variants(source):
                if text not in ocr_texts:
                    ocr_texts.append(text)
                    if len(ocr_texts) >= 18:
                        break
        marker_count = self._count_slate_markers(ocr_texts)
        has_slate_text = bool(detected_payload) or marker_count >= 2
        probable = bool(detected_payload) or (
            marker_count >= 2 and layout["layout_score"] >= 0.52
        ) or (
            marker_count >= 3 and layout["layout_score"] >= 0.42
        ) or (
            layout["layout_score"] >= 0.9 and marker_count >= 1
        )
        return {
            "is_qr_candidate": qr_candidate,
            "is_probable_slate": probable,
            "has_slate_text": has_slate_text,
            "detected_slate_payload": detected_payload,
            "ocr_marker_count": marker_count,
            "layout_score": layout["layout_score"],
            "bright_ratio": layout["bright_ratio"],
            "dark_ratio": layout["dark_ratio"],
            "square_contours": layout["square_contours"],
            "reason": [
                *layout["reason"],
                "qr_present" if qr_candidate else "qr_absent",
                f"ocr_markers={marker_count}",
                "posterpro_slate_text" if has_slate_text else "no_slate_text",
            ],
        }

    def _detect_qr_presence(self, image: np.ndarray) -> bool:
        detector = cv2.QRCodeDetector()
        for variant in self._qr_variants(image):
            try:
                ok, points = detector.detect(variant)
                if ok and points is not None:
                    return True
            except Exception:
                continue
        return False

    def _score_slate_layout(self, image: np.ndarray) -> dict[str, Any]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        mean_luma = float(gray.mean())
        bright_ratio = float((gray > 220).mean())
        dark_ratio = float((gray < 90).mean())
        contrast = float(gray.std())
        threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        image_area = float(gray.shape[0] * gray.shape[1] or 1)
        square_contours = 0
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < image_area * 0.004:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            ratio = w / max(h, 1)
            if 0.72 <= ratio <= 1.35:
                square_contours += 1
        score = 0.0
        if mean_luma >= 170:
            score += 0.35
        if bright_ratio >= 0.3:
            score += 0.2
        if contrast >= 30:
            score += 0.15
        if square_contours >= 1:
            score += min(0.25, 0.12 * square_contours)
        if 0.02 <= dark_ratio <= 0.28:
            score += 0.05
        if mean_luma >= 195 and bright_ratio >= 0.45:
            score += 0.05
        return {
            "layout_score": round(min(score, 1.0), 3),
            "bright_ratio": round(bright_ratio, 3),
            "dark_ratio": round(dark_ratio, 3),
            "square_contours": square_contours,
            "reason": [
                f"mean_luma={round(mean_luma, 1)}",
                f"bright_ratio={round(bright_ratio, 3)}",
                f"dark_ratio={round(dark_ratio, 3)}",
                f"square_contours={square_contours}",
                f"contrast={round(contrast, 1)}",
            ],
        }

    def _ocr_text_variants(self, image: np.ndarray) -> list[str]:
        if which("tesseract") is None:
            return []
        try:
            import pytesseract
        except Exception:
            return []

        texts: list[str] = []
        for source in [image, *self._screen_and_qr_crops(image), *self._rectified_screen_crops(image)]:
            for variant in self._text_variants(source):
                try:
                    text = pytesseract.image_to_string(
                        variant,
                        config="--oem 3 --psm 6",
                    )
                except Exception:
                    continue
                cleaned = str(text or "").strip()
                if cleaned and cleaned not in texts:
                    texts.append(cleaned)
        return texts

    def _rectified_screen_crops(self, image: np.ndarray) -> list[np.ndarray]:
        crops: list[np.ndarray] = []
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        except Exception:
            return crops
        bright_mask = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)[1]
        contours, _ = cv2.findContours(bright_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        try:
            edges = cv2.Canny(gray, 60, 170)
            kernel = np.ones((5, 5), np.uint8)
            edges = cv2.dilate(edges, kernel, iterations=2)
            edge_contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        except Exception:
            edge_contours = []
        image_area = float(max(gray.shape[0] * gray.shape[1], 1))
        candidates: list[tuple[float, np.ndarray]] = []
        for contour in [*contours, *edge_contours]:
            area = float(cv2.contourArea(contour))
            if area < image_area * 0.08:
                continue
            rect = cv2.minAreaRect(contour)
            box = cv2.boxPoints(rect)
            box = np.array(box, dtype="float32")
            w = max(int(rect[1][0]), int(rect[1][1]))
            h = min(int(rect[1][0]), int(rect[1][1]))
            if min(w, h) <= 0:
                continue
            ratio = w / max(h, 1)
            if 0.55 <= ratio <= 2.8:
                candidates.append((area, box))
        for _, box in sorted(candidates, key=lambda item: item[0], reverse=True)[:4]:
            ordered = self._order_points(box)
            warped = self._warp_quad(image, ordered)
            if warped is not None and warped.size:
                crops.append(warped)
        return crops

    def _qr_anchor_crops(self, image: np.ndarray) -> list[np.ndarray]:
        crops: list[np.ndarray] = []
        detector = cv2.QRCodeDetector()
        # Anchor crops are derived from QR detections in base variants; do not
        # recursively request another anchor-crop expansion here.
        for variant in self._qr_variants(image, include_anchor_crops=False):
            try:
                ok, points = detector.detect(variant)
            except Exception:
                continue
            if not ok or points is None:
                continue
            try:
                pts = np.array(points, dtype="float32").reshape(-1, 2)
                x, y, w, h = cv2.boundingRect(pts.astype("int32"))
            except Exception:
                continue
            if w <= 0 or h <= 0:
                continue
            pad_x = max(int(w * 2.4), 120)
            pad_y = max(int(h * 2.4), 120)
            left = max(0, x - pad_x)
            top = max(0, y - pad_y)
            right = min(variant.shape[1], x + w + pad_x)
            bottom = min(variant.shape[0], y + h + pad_y)
            crop = variant[top:bottom, left:right]
            if crop.size:
                crops.append(crop)
        return crops

    @staticmethod
    def _order_points(points: np.ndarray) -> np.ndarray:
        rect = np.zeros((4, 2), dtype="float32")
        s = points.sum(axis=1)
        rect[0] = points[np.argmin(s)]
        rect[2] = points[np.argmax(s)]
        diff = np.diff(points, axis=1)
        rect[1] = points[np.argmin(diff)]
        rect[3] = points[np.argmax(diff)]
        return rect

    @staticmethod
    def _warp_quad(image: np.ndarray, points: np.ndarray) -> np.ndarray | None:
        try:
            (tl, tr, br, bl) = points
            width_a = np.linalg.norm(br - bl)
            width_b = np.linalg.norm(tr - tl)
            height_a = np.linalg.norm(tr - br)
            height_b = np.linalg.norm(tl - bl)
            max_width = max(int(width_a), int(width_b))
            max_height = max(int(height_a), int(height_b))
            if max_width <= 0 or max_height <= 0:
                return None
            dst = np.array(
                [[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]],
                dtype="float32",
            )
            matrix = cv2.getPerspectiveTransform(points, dst)
            return cv2.warpPerspective(image, matrix, (max_width, max_height))
        except Exception:
            return None

    def _text_variants(self, image: np.ndarray) -> list[np.ndarray]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        enlarged = cv2.resize(gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        threshold = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        adaptive = cv2.adaptiveThreshold(enlarged, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9)
        blur = cv2.GaussianBlur(enlarged, (3, 3), 0)
        sharpen = cv2.filter2D(enlarged, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32))
        return [gray, enlarged, threshold, adaptive, blur, sharpen]

    @staticmethod
    def _count_slate_markers(texts: list[str]) -> int:
        markers = {
            "POSTERPRO SLATE",
            "SESSION:",
            "SESSION ID:",
            "ITEM ID:",
            "ITEM:",
            "BOX:",
            "BOX ID:",
            "LOC:",
            "LOCATION:",
            "TITLE:",
            "BRAND:",
            "MODEL:",
            "CONDITION:",
            "NOTES:",
            "WEIGHT:",
            "LENGTH:",
            "WIDTH:",
            "HEIGHT:",
            "PACKED:",
        }
        hits = 0
        seen: set[str] = set()
        for text in texts:
            upper = str(text or "").upper()
            for marker in markers:
                if marker in upper and marker not in seen:
                    seen.add(marker)
                    hits += 1
        return hits

    def _coerce_slate_text(self, value: str | None) -> dict[str, Any] | None:
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text.replace("\u202f", " ").replace("\xa0", " ")
        upper = normalized.upper()
        if "POSTERPRO" not in upper and "HEAD SLATE" not in upper:
            return None

        field_map = {
            "SESSION": self._extract_text_field(normalized, "SESSION"),
            "ITEM": self._extract_item_id_from_text(normalized) or self._extract_text_field(normalized, "ITEM"),
            "TITLE": self._extract_text_field(normalized, "TITLE"),
            "BRAND": self._extract_text_field(normalized, "BRAND"),
            "MODEL": self._extract_text_field(normalized, "MODEL"),
            "CONDITION": self._extract_text_field(normalized, "CONDITION"),
            "NOTES": self._extract_text_field(normalized, "NOTES"),
            "WEIGHT": self._extract_text_field(normalized, "WEIGHT"),
            "LENGTH": self._extract_text_field(normalized, "LENGTH"),
            "WIDTH": self._extract_text_field(normalized, "WIDTH"),
            "HEIGHT": self._extract_text_field(normalized, "HEIGHT"),
            "PACKED": self._extract_text_field(normalized, "PACKED"),
            "DATE": self._extract_text_field(normalized, "DATE"),
        }
        box_id, location_value = self._extract_box_location_fields(normalized)
        item_id = field_map["ITEM"]
        session_id = field_map["SESSION"]
        created_at = self._coerce_text_created_at(field_map["DATE"])
        if not item_id or not session_id:
            return None
        if not created_at:
            created_at = datetime.now(UTC).isoformat()

        location = location_value
        title = field_map["TITLE"] or ""
        raw_boundary = "tail" if any(token in upper for token in TAIL_BOUNDARY_TOKENS) else "start"
        return {
            "type": SLATE_TYPE,
            "version": SLATE_VERSION,
            "session_id": session_id,
            "item_id": item_id,
            "box_id": box_id,
            "location": location,
            "title": title,
            "brand": field_map["BRAND"] or "",
            "model": field_map["MODEL"] or "",
            "condition": field_map["CONDITION"] or "",
            "notes": field_map["NOTES"] or "",
            "flaws": "",
            "weight": field_map["WEIGHT"] or "",
            "length": field_map["LENGTH"] or "",
            "width": field_map["WIDTH"] or "",
            "height": field_map["HEIGHT"] or "",
            "packed": str(field_map["PACKED"] or "").strip().lower() in {"1", "true", "yes", "y", "packed"},
            "boundary_position": raw_boundary,
            "created_at": created_at,
        }

    @staticmethod
    def _extract_item_id_from_text(text: str) -> str:
        for line in re.split(r"[\r\n]+", str(text or "")):
            candidate = line.strip()
            if not candidate:
                continue
            normalized = re.sub(r"\s*[-\u2010-\u2015\u2212]\s*", "-", candidate).upper()
            if STRICT_ITEM_ID_RE.fullmatch(normalized):
                return normalized
            normalized = normalized.replace(" ", "")
            if STRICT_ITEM_ID_RE.fullmatch(normalized):
                return normalized
            repaired = re.sub(r"[^A-Z0-9-]", "", normalized)
            if STRICT_ITEM_ID_RE.fullmatch(repaired):
                return repaired
        return ""

    @staticmethod
    def _extract_box_location_fields(text: str) -> tuple[str, str]:
        patterns = (
            re.compile(
                r"BOX\s*[:\-]\s*(?P<box>.+?)\s+(?:LOC|LOCATION)\s*[:\-]\s*(?P<location>.+?)(?:\s{2,}|$)",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"BOX ID\s*[:\-]\s*(?P<box>.+?)\s+(?:LOC|LOCATION)\s*[:\-]\s*(?P<location>.+?)(?:\s{2,}|$)",
                re.IGNORECASE | re.DOTALL,
            ),
        )
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                box = str(match.group("box") or "").strip()
                location = str(match.group("location") or "").strip()
                return box.splitlines()[0].strip(), location.splitlines()[0].strip()
        box = IntakeSlateService._extract_text_field(text, "BOX")
        location = IntakeSlateService._extract_text_field(text, "LOC") or IntakeSlateService._extract_text_field(text, "LOCATION")
        if box and location and location in box:
            box = box.split(location, 1)[0].strip()
        return box, location

    @staticmethod
    def _extract_text_field(text: str, label: str) -> str:
        aliases = {
            "SESSION": ("SESSION", "SESSION ID"),
            "ITEM": ("ITEM", "ITEM ID"),
            "BOX": ("BOX", "BOX ID"),
            "LOC": ("LOC", "LOCATION"),
            "LOCATION": ("LOCATION", "LOC"),
            "TITLE": ("TITLE", "TITLE / ITEM NAME", "ITEM NAME"),
            "BRAND": ("BRAND",),
            "MODEL": ("MODEL",),
            "CONDITION": ("CONDITION",),
            "NOTES": ("NOTES",),
            "WEIGHT": ("WEIGHT",),
            "LENGTH": ("LENGTH",),
            "WIDTH": ("WIDTH",),
            "HEIGHT": ("HEIGHT",),
            "PACKED": ("PACKED",),
            "DATE": ("DATE",),
        }.get(label.upper(), (label,))
        for alias in aliases:
            pattern = re.compile(rf"(?:^|\n)\s*{re.escape(alias)}\s*[:\-]\s*(.+)", re.IGNORECASE)
            match = pattern.search(text)
            if not match:
                continue
            value = str(match.group(1) or "").strip()
            value = value.splitlines()[0].strip()
            return value
        return ""

    @staticmethod
    def _coerce_text_created_at(value: str | None) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        cleaned = raw.replace("\u202f", " ").replace("\xa0", " ")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%b %d, %Y, %I:%M:%S %p",
            "%b %d, %Y, %I:%M %p",
        ):
            try:
                parsed = datetime.strptime(cleaned, fmt)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC).isoformat()
            except ValueError:
                continue
        return None

    def _ensure_batch(self, db: Session, *, user_id: int, item_id: str, slate: IntakeSlate | None) -> IntakePhotoBatch:
        existing = db.execute(select(IntakePhotoBatch).where(IntakePhotoBatch.user_id == user_id, IntakePhotoBatch.item_id == item_id)).scalar_one_or_none()
        if existing:
            if slate:
                existing.slate_id = slate.id
                existing.intake_session_id = slate.intake_session_id
                existing.session_id = slate.session_id
                db.add(existing)
            return existing
        batch = IntakePhotoBatch(
            user_id=user_id,
            intake_session_id=slate.intake_session_id if slate else None,
            session_id=slate.session_id if slate else None,
            item_id=item_id,
            slate_id=slate.id if slate else None,
            status="collecting",
        )
        db.add(batch)
        db.flush()
        return batch

    def _batch_status(self, batch: IntakePhotoBatch, batch_photos: list[IntakePhoto]) -> str:
        batch_photos = [photo for photo in batch_photos if not self._timeline_photo_deleted(photo)]
        if not batch_photos:
            return "empty"
        if batch.draft_listing_id:
            return "drafted"
        if not self._batch_is_closed(batch):
            return "collecting"
        if not batch.slate_id:
            return "needs_slate_review"
        if len([photo for photo in batch_photos if photo.is_public_listing_candidate]) == 0:
            return "needs_product_photos"
        return "ready_for_draft"

    def _batch_is_closed(self, batch: IntakePhotoBatch) -> bool:
        return bool(isinstance(batch.metadata_json, dict) and batch.metadata_json.get("stream_closed"))

    def _batch_state_snapshot(
        self,
        batch: IntakePhotoBatch,
        batch_photos: list[IntakePhoto],
        *,
        stream_closed: bool | None = None,
        draft_state: str | None = None,
        group_state: str | None = None,
        reconciliation_required: bool | None = None,
    ) -> dict[str, Any]:
        metadata = dict(batch.metadata_json or {})
        now = datetime.now(UTC).isoformat()
        batch_photos = [photo for photo in batch_photos if not self._timeline_photo_deleted(photo)]
        photo_ids = [int(photo.id) for photo in batch_photos if photo.id is not None]
        public_photo_ids = [int(photo.id) for photo in batch_photos if photo.id is not None and photo.is_public_listing_candidate and not photo.is_slate]
        previous_photo_ids = [int(value) for value in (metadata.get("observed_photo_ids") or []) if str(value).strip().isdigit()]
        if photo_ids != previous_photo_ids:
            metadata["group_revision"] = int(metadata.get("group_revision") or 0) + 1
            metadata["evidence_revision"] = int(metadata.get("evidence_revision") or 0) + 1
            metadata["last_evidence_change_at"] = now
        metadata["observed_photo_ids"] = photo_ids
        metadata["public_photo_ids"] = public_photo_ids
        metadata["stream_closed"] = bool(stream_closed if stream_closed is not None else metadata.get("stream_closed"))
        metadata["reconciliation_required"] = bool(reconciliation_required if reconciliation_required is not None else metadata.get("reconciliation_required"))
        if group_state:
            metadata["group_state"] = group_state
        elif batch.draft_listing_id:
            metadata["group_state"] = "FINALIZED"
        elif not batch_photos:
            metadata["group_state"] = "HEAD_SEEN" if batch.slate_id else "COLLECTING_PHOTOS"
        elif metadata["stream_closed"]:
            metadata["group_state"] = "SETTLING" if metadata["reconciliation_required"] else "GROUP_CLOSED"
        elif public_photo_ids:
            metadata["group_state"] = "GROUP_PROVISIONAL"
        else:
            metadata["group_state"] = "COLLECTING_PHOTOS"
        if draft_state:
            metadata["draft_state"] = draft_state
        elif batch.draft_listing_id:
            metadata["draft_state"] = "DRAFT_CREATED"
        elif metadata["stream_closed"] and public_photo_ids:
            metadata["draft_state"] = "DRAFT_QUEUED"
        else:
            metadata["draft_state"] = "CAPTURE_ONLY"
        metadata["state_updated_at"] = now
        metadata["settling"] = metadata["group_state"] in {"SETTLING", "RECONCILING"}
        return metadata

    def _batch_warnings(self, *, slate: IntakeSlate | None, batch: IntakePhotoBatch, listing: Listing | None, photos: list[IntakePhoto]) -> list[str]:
        warnings: list[str] = []
        if slate is None:
            warnings.append("No slate metadata is linked to this batch.")
        if not self._batch_is_closed(batch):
            warnings.append("This intake batch is still open. Mark the next slate boundary before treating it as a complete item.")
        if batch.public_photo_count == 0:
            warnings.append("No public product photos are assigned yet.")
        if isinstance(batch.metadata_json, dict) and batch.metadata_json.get("tail_boundary_used"):
            warnings.append("This batch was recovered from a tail slate captured after the product photos. Verify grouping before publish.")
        if isinstance(batch.metadata_json, dict) and batch.metadata_json.get("tail_boundary_conflict"):
            warnings.append("Tail slate arrived after another active photo stream. Manual split or reassignment may still be required.")
        if listing is not None:
            readiness = summarize_listing_readiness(
                listing_images=listing.listing_images,
                condition_data=listing.condition_data,
                shipping_profile=listing.shipping_profile,
                listing={
                    "category_id": listing.category_id,
                    "category_suggestion": listing.category_suggestion,
                    "listing_price": listing.listing_price,
                    "suggested_price": listing.suggested_price,
                },
            )
            warnings.extend(readiness.get("blockers") or [])
            warnings.extend(readiness.get("warnings") or [])
        return warnings

    def _default_session_id(self, settings_payload: dict[str, Any]) -> str:
        template = str(settings_payload.get("default_session_naming_pattern") or DEFAULT_INTAKE_SETTINGS["default_session_naming_pattern"])
        date_token = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
        location_token = self._slug_token(str(settings_payload.get("default_location") or "location")).upper() or "LOCATION"
        return template.replace("{date}", date_token).replace("{location}", location_token)

    def _resolve_box_id(self, db: Session, *, user_id: int, requested: str, prefix: str, same_box: bool, increment_box: bool) -> str:
        if requested:
            return requested
        if same_box:
            latest = db.execute(select(IntakeSlate).where(IntakeSlate.user_id == user_id).order_by(IntakeSlate.updated_at.desc())).scalar_one_or_none()
            if latest and latest.box_id:
                return latest.box_id
        if increment_box or not same_box:
            return self.next_box_id(db, user_id=user_id, prefix=prefix)
        return self.next_box_id(db, user_id=user_id, prefix=prefix)

    def _compose_description(self, *, slate: IntakeSlate | None, generated: dict[str, Any]) -> str:
        parts = [str(generated.get("description") or "").strip()]
        if slate and slate.notes:
            parts.append(f"Notes: {slate.notes}")
        if slate and slate.flaws:
            parts.append(f"Condition / flaws: {slate.flaws}")
        if slate and isinstance(slate.qr_payload_json, dict) and self._boundary_position_from_payload(slate.qr_payload_json) == "tail":
            parts.append("PosterPro note: item photos were recovered from a tail slate sequence and should be visually reviewed before publish.")
        return "\n\n".join(part for part in parts if part).strip()

    def _merged_specifics(self, *, slate: IntakeSlate | None, generated: dict[str, Any]) -> dict[str, Any]:
        specifics = dict(generated.get("item_specifics") or {})
        if slate and slate.brand:
            specifics["Brand"] = slate.brand
        if slate and slate.model:
            specifics["Model"] = slate.model
        if slate and slate.box_id:
            specifics.setdefault("PosterPro Box ID", slate.box_id)
        return specifics

    def _condition_notes(self, *, slate: IntakeSlate | None) -> str | None:
        if slate and slate.flaws:
            return slate.flaws
        if slate and slate.notes:
            return slate.notes
        return None

    def _extract_photo_signals(self, photos: list[IntakePhoto]) -> dict[str, list[str]]:
        barcode_candidates: list[str] = []
        photo_keywords: list[str] = []
        for photo in photos:
            filename = Path(photo.local_path or photo.original_filename or "").stem
            tokens = [token for token in self._slug_token(filename).split("-") if token]
            for token in tokens[:8]:
                if token not in photo_keywords:
                    photo_keywords.append(token)
            for code in self._decode_barcodes(photo.local_path):
                if code not in barcode_candidates:
                    barcode_candidates.append(code)
        return {
            "barcode_candidates": barcode_candidates[:5],
            "detected_identifiers": barcode_candidates[:5],
            "photo_keywords": photo_keywords[:12],
        }

    def _decode_barcodes(self, image_path: str | None) -> list[str]:
        if not image_path or not hasattr(cv2, "barcode_BarcodeDetector"):
            return []
        try:
            image = cv2.imread(str(image_path))
            if image is None:
                return []
            detector = cv2.barcode_BarcodeDetector()
            ok, decoded_info, *_ = detector.detectAndDecode(image)
            if not ok:
                return []
            return [str(value).strip() for value in (decoded_info or []) if str(value).strip()]
        except Exception:
            return []

    def _merged_labels(self, current: list[str], extra: list[str | None]) -> list[str]:
        values: list[str] = []
        for item in [*(current or []), *extra]:
            value = str(item or "").strip()
            if value and value not in values:
                values.append(value)
        return values

    @staticmethod
    def _listing_photo_purpose(*, photo: IntakePhoto, index: int, total: int) -> str:
        name_bits = " ".join(
            part
            for part in [
                str(photo.original_filename or ""),
                str(photo.source_photo_id or ""),
                str(photo.image_type or ""),
            ]
            if part
        ).lower()
        if any(token in name_bits for token in ("label", "barcode", "qr", "upc", "ean", "gtin", "isbn", "mpn", "model")):
            return "label-detail-view"
        if any(token in name_bits for token in ("packaging", "box", "carton", "package")):
            return "packaging-view"
        if any(token in name_bits for token in ("damage", "flaw", "scratch", "wear")):
            return "condition-detail-view"
        if index == 1:
            return "front-view"
        if index == 2:
            return "alternate-view"
        if index == total:
            return "detail-view"
        return "product-view"

    def _to_public_media_path(self, path: str) -> str:
        storage_root = Path(settings.storage_root).resolve()
        resolved = Path(path).resolve()
        try:
            relative = resolved.relative_to(storage_root)
            return f"/media/{relative.as_posix()}"
        except ValueError:
            return path

    def _hash_file(self, path: str) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except Exception:
            return None

    def _safe_float(self, value: Any, fallback: float) -> float:
        try:
            parsed = float(value)
            return round(parsed, 2) if parsed > 0 else fallback
        except (TypeError, ValueError):
            return fallback

    def _coerce_qr_payload(self, metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(metadata, dict):
            return None
        payload = metadata.get("qr_payload")
        return payload if isinstance(payload, dict) else None

    def _boundary_position_from_payload(self, payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return "start"
        value = str(payload.get("boundary_position") or "").strip().lower()
        if value in {"tail", "end", "late"}:
            return "tail"
        text_blob = " ".join(str(payload.get(key) or "") for key in ("title", "notes", "internal_notes", "boundary_position")).lower()
        collapsed = "".join(char for char in text_blob if char.isalpha())
        if any(token in text_blob for token in TAIL_BOUNDARY_TOKENS) or "tailslate" in collapsed or "taleslate" in collapsed:
            return "tail"
        return "start"

    def _normalize_boundary_position(self, payload: dict[str, Any]) -> str:
        value = str(payload.get("boundary_position") or "").strip().lower()
        if value in {"tail", "end", "late"}:
            return "tail"
        text_blob = " ".join(str(payload.get(key) or "") for key in ("title", "notes", "internal_notes")).lower()
        if any(token in text_blob for token in TAIL_BOUNDARY_TOKENS):
            return "tail"
        return "start"

    def _manual_item_id(self, metadata: dict[str, Any] | None) -> str | None:
        if not isinstance(metadata, dict):
            return None
        value = str(metadata.get("manual_item_id") or "").strip()
        return value or None

    def _slug_token(self, value: str | None, *, max_length: int = 80) -> str:
        raw = "".join(char.lower() if char.isalnum() else "-" for char in str(value or ""))
        while "--" in raw:
            raw = raw.replace("--", "-")
        return raw.strip("-")[:max_length]

    @staticmethod
    def _parse_optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _album_identifier(self, url: str | None) -> str | None:
        parsed = urlparse(str(url or "").strip())
        if not parsed.path:
            return None
        return parsed.path.strip("/") or None
