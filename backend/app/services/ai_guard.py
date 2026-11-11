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
from typing import Any
from sqlalchemy import select, text
from app.models.models import AIDailyBudget, AIRequestReservation, AIWorkloadAdmission
from app.core.config import settings

_lock = threading.Lock()
_completed: set[str] = set()
_circuit_until = 0.0

# Explicit policy table.  Unknown models are potentially billable and are
# blocked while the paid-spend firewall is disabled; they are never guessed
# into a free pool.
MODEL_POOL_POLICY = {
    # Current complimentary-token offer model IDs (versioned names) and the
    # stable aliases used by PosterPro.  Unknown models fail closed.
    "gpt-5.6-sol": "large",
    "gpt-5.5-2026-04-23": "large",
    "gpt-5.4-2026-03-05": "large",
    "gpt-5.2-2025-12-11": "large",
    "gpt-5.1-2025-11-13": "large",
    "gpt-5.1-codex": "large",
    "gpt-5-codex": "large",
    "gpt-5-2025-08-07": "large",
    "gpt-5-chat-latest": "large",
    "gpt-4.1-2025-04-14": "large",
    "gpt-4o-2024-05-13": "large",
    "gpt-4o-2024-08-06": "large",
    "gpt-4o-2024-11-20": "large",
    "o3-2025-04-16": "large",
    "o1-preview-2024-09-12": "large",
    "o1-2024-12-17": "large",
    "gpt-5.6-terra": "small",
    "gpt-5.6-luna": "small",
    "gpt-5.4-mini-2026-03-17": "small",
    "gpt-5.4-nano-2026-03-17": "small",
    "gpt-5.1-codex-mini": "small",
    "gpt-5-mini-2025-08-07": "small",
    "gpt-5-nano-2025-08-07": "small",
    "gpt-4.1-mini-2025-04-14": "small",
    "gpt-4.1-nano-2025-04-14": "small",
    "gpt-4o-mini-2024-07-18": "small",
    "o4-mini-2025-04-16": "small",
    "o1-mini-2024-09-12": "small",
    "codex-mini-latest": "small",
    # Stable aliases currently used by the application, explicitly mapped to
    # the corresponding eligible family rather than inferred by name.
    "gpt-4o-mini": "small",
    "gpt-4.1-mini": "small",
    "gpt-4.1-nano": "small",
    "gpt-5-mini": "small",
    "gpt-5-nano": "small",
    "o3-mini": "small",
    "o4-mini": "small",
    "gpt-4o": "large",
    "gpt-4.1": "large",
    "gpt-5": "large",
    "o1": "large",
    "o3": "large",
}

AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE = "GOOGLE_PHOTO_TIMELINE"
_SCOPED_CIRCUIT_WORKLOADS = {AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE}


def _scope_pool(workload: str) -> str:
    # ai_daily_budgets.pool is a historical VARCHAR(16); keep this durable
    # accounting key short while workload remains explicit on reservations.
    return "scope:timeline" if workload == AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE else "scope:unknown"


def _scoped_admission(db, *, workload: str | None, estimated_tokens: int, today: date) -> dict | None:
    """Allow only an explicitly enabled workload through an OPEN global circuit.

    Returns scoped budget facts when admitted; ``None`` means deny.  This is
    deliberately exact-match only: normal callers cannot acquire the bypass by
    choosing a similar purpose or operation string.
    """
    if workload not in _SCOPED_CIRCUIT_WORKLOADS:
        return None
    policy = db.get(AIWorkloadAdmission, workload)
    if not policy or not policy.enabled or policy.emergency_stop or int(policy.daily_cap_tokens or 0) <= 0:
        return None
    scope_pool = _scope_pool(workload)
    lock_id = int(hashlib.sha256(f"scope:{today.isoformat()}:{workload}".encode()).hexdigest()[:15], 16) % (2**63 - 1)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    budget = db.execute(select(AIDailyBudget).where(AIDailyBudget.budget_date == today, AIDailyBudget.pool == scope_pool).with_for_update()).scalar_one_or_none()
    if budget is None:
        budget = AIDailyBudget(budget_date=today, pool=scope_pool, safe_ceiling=int(policy.daily_cap_tokens), reserved_tokens=0, actual_tokens=0, waiting_count=0)
        db.add(budget); db.flush()
    remaining = int(budget.safe_ceiling) - int(budget.actual_tokens or 0) - int(budget.reserved_tokens or 0)
    if remaining < estimated_tokens:
        return None
    return {"budget": budget, "remaining": remaining, "scope_pool": scope_pool}


def model_pool(model: str | None) -> str | None:
    return MODEL_POOL_POLICY.get(str(model or "").strip().lower())


def utc_period(now: datetime | None = None) -> tuple[date, datetime, datetime]:
    """Return the current complimentary period using UTC, never host time."""
    current = now or datetime.now(UTC)
    current = current.astimezone(UTC)
    start = datetime.combine(current.date(), datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    return current.date(), start, end


# OpenAI uses HTTP 429 for several materially different conditions.  Keep the
# provider response classified so spend/credit failures are never treated like
# ordinary burst rate limits (and therefore never retried in a tight loop).
_CREDIT_CODES = {
    "credit_balance_exhausted",
    "insufficient_quota",
    "organization_usage_limit_exceeded",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
}


def classify_openai_error(status_code: int, body: Any, headers: Any = None) -> dict[str, Any]:
    """Return safe, structured provider error metadata without secret data."""
    payload = body if isinstance(body, dict) else {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    error_type = str(error.get("type") or "").strip() or None
    error_code = str(error.get("code") or "").strip() or None
    message = str(error.get("message") or "").strip() or str(body or "").strip()
    lowered = f"{error_type or ''} {error_code or ''} {message}".lower()
    if error_code in _CREDIT_CODES or error_type in _CREDIT_CODES or any(
        marker in lowered for marker in ("no credits remaining", "insufficient_quota", "spend limit", "usage limit")
    ):
        category = error_code if error_code in _CREDIT_CODES else error_type if error_type in _CREDIT_CODES else "credit_or_spend_limit"
        retryable = False
    elif int(status_code or 0) == 429:
        category = "rate_limit"
        retryable = True
    else:
        category = "provider_error"
        retryable = False
    request_id = None
    if headers is not None:
        try:
            request_id = headers.get("x-request-id") or headers.get("X-Request-Id")
        except Exception:
            request_id = None
    request_id = request_id or payload.get("request_id")
    return {
        "http_status": int(status_code or 0),
        "error_type": error_type,
        "error_code": error_code,
        "error_message": message[:2000],
        "request_id": str(request_id) if request_id else None,
        "category": category,
        "retryable": retryable,
    }


def format_openai_error(details: dict[str, Any]) -> str:
    """Compact human-readable diagnostic suitable for the non-secret ledger."""
    parts = [f"HTTP {details.get('http_status')}"]
    for label, key in (("type", "error_type"), ("code", "error_code"), ("category", "category"), ("message", "error_message"), ("request_id", "request_id")):
        value = details.get(key)
        if value:
            parts.append(f"{label}={value}")
    return "OpenAI request failed: " + " | ".join(parts)


def provider_circuit_seconds(category: str | None) -> int:
    """Billing/credit failures wait for an operator/provider reset window."""
    if category in _CREDIT_CODES or category == "credit_or_spend_limit":
        return 86400
    return settings.ai_provider_circuit_cooldown_seconds


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


def reserve_durable(
    db,
    *,
    key: str,
    pool: str = "mini",
    model: str | None = None,
    estimated_tokens: int,
    user_id: int | None = None,
    listing_id: int | None = None,
    purpose: str = "unknown",
    budget_date: date | None = None,
    lineage: dict | None = None,
    workload: str | None = None,
    work_item_id: str | None = None,
) -> dict:
    """Atomically reserve today's safe complimentary budget and claim a signature."""
    # A durable circuit is an emergency/operational pause.  It must block at
    # the admission boundary rather than allowing a caller to create another
    # paid reservation and only failing later at the HTTP request.
    today = budget_date or utc_period()[0]
    circuit = provider_circuit_open(db)
    scoped = None
    if circuit:
        scoped = _scoped_admission(db, workload=workload, estimated_tokens=estimated_tokens, today=today)
        if scoped is None:
            return {"status": "PROVIDER_CIRCUIT_OPEN", "error": circuit.get("reason"), "retry_after": circuit.get("retry_after")}
    explicit_pool = "large" if pool == "large" else "mini"
    policy_pool = model_pool(model) if model else explicit_pool
    if model and policy_pool is None:
        return {"status": "NON_ELIGIBLE_MODEL", "model": model, "error": "Model is not in the complimentary eligibility policy"}
    pool = policy_pool or explicit_pool
    if not bool(settings.openai_complimentary_traffic_eligible) and not bool(settings.paid_openai_spend_allowed):
        return {"status": "COMPLIMENTARY_TRAFFIC_NOT_VERIFIED", "error": "Project eligibility for complimentary shared traffic is not verified and paid OpenAI spend is disabled"}
    ceiling = settings.ai_large_daily_safe_ceiling if pool == "large" else settings.ai_mini_daily_safe_ceiling
    # Serialize same-signature claims and first-row budget creation across
    # every worker/process. PostgreSQL is authoritative; Python locks are not.
    lock_id = int(hashlib.sha256(key.encode()).hexdigest()[:15], 16) % (2**63 - 1)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    budget_lock_id = int(hashlib.sha256(f"budget:{today.isoformat()}:{pool}".encode()).hexdigest()[:15], 16) % (2**63 - 1)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": budget_lock_id})
    existing = db.execute(select(AIRequestReservation).where(AIRequestReservation.input_signature == key).with_for_update()).scalar_one_or_none()
    if existing and existing.state in {"RUNNING", "SUCCESS", "RESERVED", "DISPATCHED", "RECONCILED"}:
        return {"status": "DUPLICATE_SUPPRESSED", "reservation_id": existing.id, "state": existing.state}
    budget = db.execute(select(AIDailyBudget).where(AIDailyBudget.budget_date == today, AIDailyBudget.pool == pool).with_for_update()).scalar_one_or_none()
    if budget is None:
        budget = AIDailyBudget(budget_date=today, pool=pool, safe_ceiling=ceiling, reserved_tokens=0, actual_tokens=0, waiting_count=0)
        db.add(budget); db.flush()
    lineage = lineage or {}
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
            existing.listing_id = listing_id; existing.user_id = user_id; existing.purpose = purpose
            existing.item_group_id = lineage.get("item_group_id")
            existing.intake_batch_id = lineage.get("intake_batch_id")
            existing.photo_id = lineage.get("photo_id")
            existing.worker_task_id = lineage.get("worker_task_id")
            existing.model = model; existing.operation = lineage.get("operation")
            existing.next_attempt_at = datetime.combine(today + timedelta(days=1), datetime.min.time())
            existing.error = "complimentary safe ceiling unavailable"
        else:
            existing = AIRequestReservation(input_signature=key, pool=pool, budget_date=today, reserved_tokens=0, state="WAITING_FOR_DAILY_AI_RESET", listing_id=listing_id, user_id=user_id, purpose=purpose, item_group_id=lineage.get("item_group_id"), intake_batch_id=lineage.get("intake_batch_id"), photo_id=lineage.get("photo_id"), worker_task_id=lineage.get("worker_task_id"), model=model, operation=lineage.get("operation"), workload=workload, work_item_id=work_item_id, next_attempt_at=datetime.combine(today + timedelta(days=1), datetime.min.time()), error="complimentary safe ceiling unavailable")
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
        existing.item_group_id = lineage.get("item_group_id")
        existing.intake_batch_id = lineage.get("intake_batch_id")
        existing.photo_id = lineage.get("photo_id")
        existing.worker_task_id = lineage.get("worker_task_id")
        existing.model = model
        existing.operation = lineage.get("operation")
        existing.workload = workload; existing.work_item_id = work_item_id
        existing.attempt_number = int(existing.attempt_number or 0) + 1
        reservation = existing
    else:
        reservation = AIRequestReservation(
            input_signature=key, pool=pool, budget_date=today, reserved_tokens=estimated_tokens,
            state="RESERVED", listing_id=listing_id, user_id=user_id, purpose=purpose,
            item_group_id=lineage.get("item_group_id"), intake_batch_id=lineage.get("intake_batch_id"),
            photo_id=lineage.get("photo_id"), worker_task_id=lineage.get("worker_task_id"),
            model=model, operation=lineage.get("operation"),
            workload=workload, work_item_id=work_item_id,
        )
        db.add(reservation)
    budget.reserved_tokens = int(budget.reserved_tokens or 0) + estimated_tokens
    if scoped:
        scoped["budget"].reserved_tokens = int(scoped["budget"].reserved_tokens or 0) + estimated_tokens
    db.flush(); db.commit()
    return {"status": "RESERVED", "reservation_id": reservation.id, "pool": pool, "reserved_tokens": estimated_tokens,
            "workload": workload, "scoped_remaining": (int(scoped["remaining"]) - estimated_tokens) if scoped else None}


def reconcile_durable(db, reservation_id: int, *, actual_tokens: int = 0, state: str = "RECONCILED", error: str | None = None, usage: dict | None = None, request_id: str | None = None) -> None:
    reservation = db.execute(select(AIRequestReservation).where(AIRequestReservation.id == reservation_id).with_for_update()).scalar_one_or_none()
    if not reservation or reservation.state in {"RECONCILED", "RELEASED"}:
        return
    budget = db.execute(select(AIDailyBudget).where(AIDailyBudget.budget_date == reservation.budget_date, AIDailyBudget.pool == reservation.pool).with_for_update()).scalar_one_or_none()
    if budget:
        budget.reserved_tokens = max(0, int(budget.reserved_tokens or 0) - int(reservation.reserved_tokens or 0))
        budget.actual_tokens = int(budget.actual_tokens or 0) + max(0, int(actual_tokens or 0))
    if reservation.workload in _SCOPED_CIRCUIT_WORKLOADS:
        scope_pool = _scope_pool(reservation.workload)
        scope_budget = db.execute(select(AIDailyBudget).where(AIDailyBudget.budget_date == reservation.budget_date, AIDailyBudget.pool == scope_pool).with_for_update()).scalar_one_or_none()
        if scope_budget is None:
            # Missing scoped accounting is an integrity failure: retain the
            # global circuit and do not pretend the request was reconciled.
            raise RuntimeError("missing scoped workload budget")
        scope_budget.reserved_tokens = max(0, int(scope_budget.reserved_tokens or 0) - int(reservation.reserved_tokens or 0))
        scope_budget.actual_tokens = int(scope_budget.actual_tokens or 0) + max(0, int(actual_tokens or 0))
    usage = usage or {}
    reservation.actual_tokens = int(actual_tokens or 0); reservation.state = state; reservation.error = error
    reservation.input_tokens = usage.get("input_tokens")
    reservation.output_tokens = usage.get("output_tokens")
    reservation.request_id = request_id or usage.get("request_id")
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


def release_waiting_for_reset(db, *, as_of: date | None = None, limit: int = 100, release_newest_intake_only: bool = True) -> int:
    """Move a bounded batch of prior-day waiting work back to retryable state."""
    current = as_of or datetime.now(UTC).date()
    query = select(AIRequestReservation).where(
        AIRequestReservation.state == "WAITING_FOR_DAILY_AI_RESET",
        AIRequestReservation.budget_date < current,
    )
    # Reset must not turn an old quality-repair flood into a fresh paid-AI
    # campaign.  Only reservations explicitly admitted as newest production
    # intake are eligible for automatic release; everything else remains
    # durable and requires an intentional bounded campaign.
    if release_newest_intake_only:
        query = query.where(AIRequestReservation.operation == "newest_intake")
    rows = db.execute(query.order_by(AIRequestReservation.created_at.asc()).limit(limit).with_for_update(skip_locked=True)).scalars().all()
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
