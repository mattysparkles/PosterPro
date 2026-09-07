from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import IntakeNotification, User
from app.services.process_notifications import (
    list_process_notifications,
    mark_all_process_notifications_read,
    mark_process_notification_read,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

class NotificationBulkRequest(BaseModel):
    notification_ids: list[int] = Field(default_factory=list, max_length=500)
    action: str
    select_all: bool = False


def _serialize(notification):
    return {
        "id": notification.id,
        "user_id": notification.user_id,
        "canonical_item_id": notification.canonical_item_id,
        "notification_type": notification.notification_type,
        "title": notification.title,
        "message": notification.message,
        "href": notification.href,
        "metadata_json": notification.metadata_json,
        "read_at": notification.read_at,
        "created_at": notification.created_at,
        "updated_at": notification.updated_at,
    }


@router.get("")
def list_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    unread_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = list_process_notifications(db, user_id=current_user.id, limit=limit, offset=offset, unread_only=unread_only)
    total_query = select(func.count(IntakeNotification.id)).where(IntakeNotification.user_id == current_user.id)
    if unread_only:
        total_query = total_query.where(IntakeNotification.read_at.is_(None))
    total = int(db.execute(total_query).scalar_one())
    return {
        "notifications": [_serialize(notification) for notification in rows],
        "unread_count": int(db.execute(select(func.count(IntakeNotification.id)).where(IntakeNotification.user_id == current_user.id, IntakeNotification.read_at.is_(None))).scalar_one()),
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notification = mark_process_notification_read(db, user_id=current_user.id, notification_id=notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"notification": _serialize(notification)}


@router.post("/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = mark_all_process_notifications_read(db, user_id=current_user.id)
    return {"marked_read": count}


@router.post("/bulk")
def bulk_notifications(
    payload: NotificationBulkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    action = str(payload.action or "").strip().lower()
    if action not in {"read", "archive", "delete"}:
        raise HTTPException(status_code=400, detail="Unsupported notification bulk action")
    query = db.query(IntakeNotification).filter(IntakeNotification.user_id == current_user.id)
    if not payload.select_all:
        query = query.filter(IntakeNotification.id.in_(payload.notification_ids or [-1]))
    rows = query.all()
    if action == "delete":
        for row in rows:
            db.delete(row)
    else:
        from datetime import datetime, timezone
        for row in rows:
            if action == "read":
                row.read_at = row.read_at or datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                metadata = dict(row.metadata_json or {}) if isinstance(row.metadata_json, dict) else {}
                metadata["archived"] = True
                metadata["archived_at"] = datetime.now(timezone.utc).isoformat()
                row.metadata_json = metadata
    db.commit()
    return {"action": action, "affected": len(rows)}
