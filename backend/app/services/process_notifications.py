from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.models import IntakeNotification


def create_process_notification(
    db: Session,
    *,
    user_id: int,
    title: str,
    message: str | None = None,
    notification_type: str = "process_event",
    href: str | None = None,
    metadata_json: dict | None = None,
    canonical_item_id: int | None = None,
) -> IntakeNotification | None:
    now = datetime.now(UTC).replace(tzinfo=None)
    def _normalize(value: str | None) -> str:
        return str(value or "").strip().lower().replace("_", " ").replace("-", " ")

    notification_type_normalized = _normalize(notification_type)
    title_normalized = _normalize(title)
    message_normalized = _normalize(message)
    combined = f"{title_normalized} {message_normalized}"
    semantic_listing_block = (
        notification_type_normalized == "listing processing blocked"
        and str(href or "").startswith("/listings/")
        and str(title or "").startswith("Listing #")
    )
    suppress_tokens = (
        "generic_or_caption_identity",
        "needs_image_identification",
        "insufficient_identity_evidence",
        "caption_identity",
    )
    normalized_tokens = [token.replace("_", " ") for token in suppress_tokens]
    if not semantic_listing_block and (any(token.replace("_", " ") in notification_type_normalized for token in suppress_tokens) or any(token in combined for token in normalized_tokens)):
        return None
    if semantic_listing_block:
        # Listing processing notifications are keyed by the stable listing URL
        # and the exact current blocker. A retry in the same blocked state is
        # silent indefinitely; a changed blocker produces one new actionable
        # notice, without relying on a short time window.
        duplicate = db.execute(select(IntakeNotification).where(
            IntakeNotification.user_id == user_id,
            IntakeNotification.notification_type == notification_type,
            IntakeNotification.title == title,
            IntakeNotification.message == message,
            IntakeNotification.href == href,
        ).order_by(IntakeNotification.created_at.desc(), IntakeNotification.id.desc()).limit(1)).scalars().first()
        if duplicate is not None:
            return duplicate
    # Workflow workers can retry the same event repeatedly. Collapse an exact
    # duplicate from the same tenant/item during a short window; keep distinct
    # failures, changed messages, and later recurrences visible.
    duplicate_query = select(IntakeNotification).where(
        IntakeNotification.user_id == user_id,
        IntakeNotification.notification_type == notification_type,
        IntakeNotification.title == title,
        IntakeNotification.message == message,
        IntakeNotification.href == href,
        IntakeNotification.canonical_item_id == canonical_item_id,
        IntakeNotification.created_at >= now - timedelta(minutes=10),
    ).order_by(IntakeNotification.created_at.desc(), IntakeNotification.id.desc()).limit(1)
    duplicate = db.execute(duplicate_query).scalars().first()
    if duplicate is not None:
        return duplicate
    notification = IntakeNotification(
        user_id=user_id,
        canonical_item_id=canonical_item_id,
        notification_type=notification_type,
        title=title,
        message=message,
        href=href,
        metadata_json=metadata_json,
    )
    db.add(notification)
    db.flush()
    return notification


def list_process_notifications(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    offset: int = 0,
    unread_only: bool = False,
) -> list[IntakeNotification]:
    query = select(IntakeNotification).where(IntakeNotification.user_id == user_id)
    if unread_only:
        query = query.where(IntakeNotification.read_at.is_(None))
    query = query.order_by(IntakeNotification.created_at.desc(), IntakeNotification.id.desc()).offset(max(0, offset)).limit(limit)
    return db.execute(query).scalars().all()


def mark_process_notification_read(db: Session, *, user_id: int, notification_id: int) -> IntakeNotification | None:
    notification = db.get(IntakeNotification, notification_id)
    if notification is None or notification.user_id != user_id:
        return None
    if notification.read_at is None:
        from datetime import datetime, UTC

        notification.read_at = datetime.now(UTC)
        db.add(notification)
        db.commit()
        db.refresh(notification)
    return notification


def mark_all_process_notifications_read(db: Session, *, user_id: int) -> int:
    now = datetime.now(UTC).replace(tzinfo=None)
    result = db.execute(
        update(IntakeNotification)
        .where(IntakeNotification.user_id == user_id, IntakeNotification.read_at.is_(None))
        .values(read_at=now, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return int(result.rowcount or 0)
