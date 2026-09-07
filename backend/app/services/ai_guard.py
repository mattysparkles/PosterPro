"""Process-local AI cost guard shared by non-intake providers.

Persistent job state remains authoritative for retries; this guard prevents a
single worker process from hammering the provider with identical work while a
provider circuit is open.
"""
import hashlib
import json
import threading
import time
from datetime import datetime, date, UTC, timedelta
from sqlalchemy import select, text
from app.models.models import AIDailyBudget, AIRequestReservation
from app.core.config import settings

_lock = threading.Lock()
_completed: set[str] = set()
_circuit_until = 0.0


def signature(*, purpose: str, payload: object) -> str:
    return hashlib.sha256(json.dumps({"purpose": purpose, "payload": payload}, sort_keys=True, default=str).encode()).hexdigest()


def allow(key: str) -> bool:
    with _lock:
        return key not in _completed and time.time() >= _circuit_until


def mark_completed(key: str) -> None:
    with _lock:
        _completed.add(key)


def open_circuit(seconds: int = 900) -> None:
    global _circuit_until
    with _lock:
        _circuit_until = max(_circuit_until, time.time() + seconds)


def reserve_durable(db, *, key: str, pool: str = "mini", estimated_tokens: int, user_id: int | None = None, listing_id: int | None = None, purpose: str = "unknown", budget_date: date | None = None) -> dict:
    """Atomically reserve today's safe complimentary budget and claim a signature."""
    today = budget_date or datetime.now(UTC).date()
    pool = "large" if pool == "large" else "mini"
    ceiling = settings.ai_large_daily_safe_ceiling if pool == "large" else settings.ai_mini_daily_safe_ceiling
    # Serialize same-signature claims and first-row budget creation across
    # every worker/process. PostgreSQL is authoritative; Python locks are not.
    lock_id = int(hashlib.sha256(key.encode()).hexdigest()[:15], 16) % (2**63 - 1)
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    budget_lock_id = int(hashlib.sha256(f"budget:{today.isoformat()}:{pool}".encode()).hexdigest()[:15], 16) % (2**63 - 1)
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": budget_lock_id})
    existing = db.execute(select(AIRequestReservation).where(AIRequestReservation.input_signature == key).with_for_update()).scalar_one_or_none()
    if existing and existing.state in {"RUNNING", "SUCCESS", "RESERVED", "DISPATCHED", "RECONCILED"}:
        return {"status": "DUPLICATE_SUPPRESSED", "reservation_id": existing.id, "state": existing.state}
    budget = db.execute(select(AIDailyBudget).where(AIDailyBudget.budget_date == today, AIDailyBudget.pool == pool).with_for_update()).scalar_one_or_none()
    if budget is None:
        budget = AIDailyBudget(budget_date=today, pool=pool, safe_ceiling=ceiling, reserved_tokens=0, actual_tokens=0, waiting_count=0)
        db.add(budget); db.flush()
    available = int(budget.safe_ceiling) - int(budget.actual_tokens or 0) - int(budget.reserved_tokens or 0)
    if available < estimated_tokens:
        # Count distinct waiting signatures, not scheduler polls.
        already_waiting = existing is not None and existing.state == "WAITING_FOR_DAILY_AI_RESET"
        if not already_waiting:
            budget.waiting_count = int(budget.waiting_count or 0) + 1
        if existing:
            existing.state = "WAITING_FOR_DAILY_AI_RESET"
            existing.pool = pool; existing.budget_date = today
            existing.reserved_tokens = estimated_tokens; existing.actual_tokens = 0
            existing.next_attempt_at = datetime.combine(today + timedelta(days=1), datetime.min.time())
            existing.error = "complimentary safe ceiling unavailable"
        else:
            existing = AIRequestReservation(input_signature=key, pool=pool, budget_date=today, reserved_tokens=0, state="WAITING_FOR_DAILY_AI_RESET", listing_id=listing_id, user_id=user_id, purpose=purpose, next_attempt_at=datetime.combine(today + timedelta(days=1), datetime.min.time()), error="complimentary safe ceiling unavailable")
            db.add(existing)
        db.commit()
        return {"status": "WAITING_FOR_DAILY_AI_RESET", "reservation_id": existing.id, "pool": pool, "estimated_tokens": estimated_tokens, "remaining": max(0, available), "next_reset_utc": (datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=UTC)).isoformat()}
    if existing:
        # Reuse the durable identity for a legitimate retry after a released,
        # retryable, terminal, or no-progress outcome; attempted != forever.
        existing.pool = pool; existing.budget_date = today
        existing.reserved_tokens = estimated_tokens; existing.actual_tokens = 0
        existing.state = "RESERVED"; existing.listing_id = listing_id
        existing.user_id = user_id; existing.purpose = purpose; existing.error = None
        reservation = existing
    else:
        reservation = AIRequestReservation(input_signature=key, pool=pool, budget_date=today, reserved_tokens=estimated_tokens, state="RESERVED", listing_id=listing_id, user_id=user_id, purpose=purpose)
        db.add(reservation)
    budget.reserved_tokens = int(budget.reserved_tokens or 0) + estimated_tokens; db.flush(); db.commit()
    return {"status": "RESERVED", "reservation_id": reservation.id, "pool": pool, "reserved_tokens": estimated_tokens}


def reconcile_durable(db, reservation_id: int, *, actual_tokens: int = 0, state: str = "RECONCILED", error: str | None = None) -> None:
    reservation = db.execute(select(AIRequestReservation).where(AIRequestReservation.id == reservation_id).with_for_update()).scalar_one_or_none()
    if not reservation or reservation.state in {"RECONCILED", "RELEASED"}:
        return
    budget = db.execute(select(AIDailyBudget).where(AIDailyBudget.budget_date == reservation.budget_date, AIDailyBudget.pool == reservation.pool).with_for_update()).scalar_one_or_none()
    if budget:
        budget.reserved_tokens = max(0, int(budget.reserved_tokens or 0) - int(reservation.reserved_tokens or 0))
        budget.actual_tokens = int(budget.actual_tokens or 0) + max(0, int(actual_tokens or 0))
    reservation.actual_tokens = int(actual_tokens or 0); reservation.state = state; reservation.error = error
    db.commit()


def provider_circuit_open(db) -> dict | None:
    """Return durable circuit state when automatic provider calls are blocked."""
    from app.models.models import AIProviderCircuit
    row = db.execute(select(AIProviderCircuit).where(AIProviderCircuit.id == 1)).scalar_one_or_none()
    if not row or row.state == "CLOSED":
        return None
    now = datetime.now(UTC).replace(tzinfo=None)
    retry_after = row.retry_after
    if retry_after and retry_after <= now:
        # Leave a single half-open window; the caller that wins the row lock
        # should be the only recovery probe.
        row.state = "HALF_OPEN"
        db.commit()
        return None
    return {"state": row.state, "reason": row.reason or row.last_error, "retry_after": retry_after.isoformat() if retry_after else None}


def open_provider_circuit(db, *, reason: str, seconds: int | None = None) -> None:
    from app.models.models import AIProviderCircuit
    row = db.execute(select(AIProviderCircuit).where(AIProviderCircuit.id == 1).with_for_update()).scalar_one_or_none()
    if row is None:
        row = AIProviderCircuit(id=1); db.add(row)
    now = datetime.now(UTC).replace(tzinfo=None)
    row.state = "OPEN"; row.reason = reason[:500]; row.last_error = reason[:1000]
    row.opened_at = now; row.retry_after = now + timedelta(seconds=seconds or settings.ai_provider_circuit_cooldown_seconds)
    db.commit()


def close_provider_circuit(db) -> None:
    from app.models.models import AIProviderCircuit
    row = db.execute(select(AIProviderCircuit).where(AIProviderCircuit.id == 1).with_for_update()).scalar_one_or_none()
    if row and row.state != "CLOSED":
        row.state = "CLOSED"; row.reason = None; row.retry_after = None; row.last_error = None; db.commit()


def release_waiting_for_reset(db, *, as_of: date | None = None, limit: int = 100) -> int:
    """Move a bounded batch of prior-day waiting work back to retryable state."""
    current = as_of or datetime.now(UTC).date()
    rows = db.execute(select(AIRequestReservation).where(AIRequestReservation.state == "WAITING_FOR_DAILY_AI_RESET", AIRequestReservation.budget_date < current).order_by(AIRequestReservation.created_at.asc()).limit(limit).with_for_update(skip_locked=True)).scalars().all()
    for row in rows:
        row.state = "FAILED_RETRYABLE"; row.next_attempt_at = datetime.now(UTC).replace(tzinfo=None); row.error = "daily complimentary budget reset; re-evaluate signature"
    if rows:
        db.commit()
    return len(rows)


def recover_stale_reservations(db, *, stale_minutes: int = 20, limit: int = 100) -> int:
    """Recover reservations whose owner died before reconciliation."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=stale_minutes)
    rows = db.execute(select(AIRequestReservation).where(AIRequestReservation.state.in_(["RESERVED", "RUNNING", "DISPATCHED"]), AIRequestReservation.updated_at < cutoff).order_by(AIRequestReservation.updated_at.asc()).limit(limit).with_for_update(skip_locked=True)).scalars().all()
    count = 0
    for row in rows:
        reconcile_durable(db, row.id, actual_tokens=0, state="FAILED_RETRYABLE", error="stale reservation recovered after worker timeout")
        count += 1
    return count
