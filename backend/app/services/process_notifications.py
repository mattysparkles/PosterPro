from __future__ import annotations

from sqlalchemy import select
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
    def _normalize(value: str | None) -> str:
        return str(value or "").strip().lower().replace("_", " ").replace("-", " ")

    notification_type_normalized = _normalize(notification_type)
    title_normalized = _normalize(title)
    message_normalized = _normalize(message)
    combined = f"{title_normalized} {message_normalized}"
    suppress_tokens = (
        "listing_processing_blocked",
        "generic_or_caption_identity",
        "needs_image_identification",
        "insufficient_identity_evidence",
        "caption_identity",
    )
    normalized_tokens = [token.replace("_", " ") for token in suppress_tokens]
    if any(token.replace("_", " ") in notification_type_normalized for token in suppress_tokens) or any(token in combined for token in normalized_tokens):
        return None
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
    notifications = list_process_notifications(db, user_id=user_id, unread_only=True, limit=250)
    count = 0
    if not notifications:
        return count
    from datetime import datetime, UTC

    for notification in notifications:
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            db.add(notification)
            count += 1
    db.commit()
    return count
