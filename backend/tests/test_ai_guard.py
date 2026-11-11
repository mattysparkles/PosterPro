from datetime import UTC, datetime, date

from app.models.models import AIDailyBudget, AIProviderCircuit, AIRequestReservation, AIWorkloadAdmission, Listing, User
from app.services.ai_guard import AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE, classify_openai_error, model_pool, utc_period, reserve_durable, release_waiting_for_reset, reconcile_durable
from app.services.listing_processing import ListingProcessingService, PROCESSING_QUALITY_REPAIR_REQUIRED


def test_complimentary_model_policy_is_explicit_and_separate():
    assert model_pool("gpt-4o-mini") == "small"
    assert model_pool("gpt-4o-mini-2024-07-18") == "small"
    assert model_pool("gpt-5.6-sol") == "large"
    assert model_pool("fine-tuned-gpt-4o") is None


def test_credit_error_is_not_retryable_and_preserves_request_id():
    details = classify_openai_error(
        429,
        {"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted", "message": "no credits remaining"}},
        {"x-request-id": "req_test"},
    )
    assert details["category"] == "credit_balance_exhausted"
    assert details["retryable"] is False
    assert details["request_id"] == "req_test"


def test_ordinary_rate_limit_remains_backoff_retryable():
    details = classify_openai_error(429, {"error": {"type": "rate_limit_error", "message": "try again"}})
    assert details["category"] == "rate_limit"
    assert details["retryable"] is True


def test_utc_period_does_not_use_local_midnight():
    period, start, end = utc_period(datetime(2026, 9, 21, 23, 59, tzinfo=UTC))
    assert period.isoformat() == "2026-09-21"
    assert start.isoformat() == "2026-09-21T00:00:00+00:00"
    assert end.isoformat() == "2026-09-22T00:00:00+00:00"


def test_open_circuit_blocks_admission_without_creating_reservation(db_session):
    db_session.add(AIProviderCircuit(id=1, state="OPEN", reason="test pause", retry_after=datetime(2099, 1, 1)))
    db_session.commit()
    result = reserve_durable(db_session, key="circuit-test", model="gpt-4o-mini", estimated_tokens=10_000)
    assert result["status"] == "PROVIDER_CIRCUIT_OPEN"
    assert db_session.query(AIRequestReservation).count() == 0


def test_open_circuit_denies_unrelated_workloads(db_session):
    db_session.add(AIProviderCircuit(id=1, state="OPEN", reason="emergency", retry_after=datetime(2099, 1, 1)))
    db_session.add(AIWorkloadAdmission(workload=AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE, enabled=True, daily_cap_tokens=2_000_000))
    db_session.commit()
    for workload in (None, "LISTING_GENERATION", "PRICING", "PHOTO_ENRICHMENT", "UNKNOWN"):
        result = reserve_durable(db_session, key=f"denied-{workload}", model="gpt-4o-mini", estimated_tokens=10_000, workload=workload)
        assert result["status"] == "PROVIDER_CIRCUIT_OPEN"


def test_timeline_scoped_admission_requires_enabled_policy_and_tracks_both_budgets(db_session):
    circuit = AIProviderCircuit(id=1, state="OPEN", reason="emergency", retry_after=datetime(2099, 1, 1))
    policy = AIWorkloadAdmission(workload=AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE, enabled=False, daily_cap_tokens=20_000)
    db_session.add_all([circuit, policy]); db_session.commit()
    denied = reserve_durable(db_session, key="timeline-disabled", model="gpt-4o-mini", estimated_tokens=10_000, workload=AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE, work_item_id="timeline:1")
    assert denied["status"] == "PROVIDER_CIRCUIT_OPEN"
    policy.enabled = True; db_session.commit()
    admitted = reserve_durable(db_session, key="timeline-enabled", model="gpt-4o-mini", estimated_tokens=10_000, workload=AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE, work_item_id="timeline:1")
    assert admitted["status"] == "RESERVED"
    reservation = db_session.get(AIRequestReservation, admitted["reservation_id"])
    assert reservation.workload == AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE
    reconcile_durable(db_session, admitted["reservation_id"], actual_tokens=8_000, usage={"input_tokens": 7_000, "output_tokens": 1_000})
    scope = db_session.query(AIDailyBudget).filter_by(budget_date=utc_period()[0], pool="scope:timeline").one()
    provider = db_session.query(AIDailyBudget).filter_by(budget_date=utc_period()[0], pool="small").one()
    assert scope.actual_tokens == 8_000 and scope.reserved_tokens == 0
    assert provider.actual_tokens == 8_000 and provider.reserved_tokens == 0
    assert db_session.get(AIProviderCircuit, 1).state == "OPEN"


def test_timeline_cap_denies_overrun(db_session):
    db_session.add_all([
        AIProviderCircuit(id=1, state="OPEN", reason="emergency", retry_after=datetime(2099, 1, 1)),
        AIWorkloadAdmission(workload=AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE, enabled=True, daily_cap_tokens=10_000),
    ])
    db_session.commit()
    assert reserve_durable(db_session, key="timeline-cap-1", model="gpt-4o-mini", estimated_tokens=10_000, workload=AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE)["status"] == "RESERVED"
    assert reserve_durable(db_session, key="timeline-cap-2", model="gpt-4o-mini", estimated_tokens=1, workload=AI_WORKLOAD_GOOGLE_PHOTO_TIMELINE)["status"] == "PROVIDER_CIRCUIT_OPEN"


def test_reset_releases_only_explicit_newest_intake_reservations(db_session):
    yesterday = date(2026, 10, 4)
    legacy = AIRequestReservation(input_signature="legacy-wait", pool="small", budget_date=yesterday, reserved_tokens=0, state="WAITING_FOR_DAILY_AI_RESET", purpose="photo_group_evidence", operation="legacy_repair")
    newest = AIRequestReservation(input_signature="new-wait", pool="small", budget_date=yesterday, reserved_tokens=0, state="WAITING_FOR_DAILY_AI_RESET", purpose="photo_group_evidence", operation="newest_intake")
    db_session.add_all([legacy, newest]); db_session.commit()
    assert release_waiting_for_reset(db_session, as_of=date(2026, 10, 5), limit=10) == 1
    assert db_session.get(AIRequestReservation, legacy.id).state == "WAITING_FOR_DAILY_AI_RESET"
    assert db_session.get(AIRequestReservation, newest.id).state == "FAILED_RETRYABLE"


def test_quality_gate_demotion_is_not_ai_queue_admission(db_session):
    user = User(email="quality-gate@example.com")
    listing = Listing(user=user, title="Used Item", source_type="intake_head_slate", needs_review=True)
    db_session.add_all([user, listing]); db_session.commit()
    result = ListingProcessingService().reconcile_review_contract_rows(db_session, listing_ids={listing.id})
    assert result["invalidated"] == 1
    refreshed = db_session.get(Listing, listing.id)
    assert refreshed.processing_state == PROCESSING_QUALITY_REPAIR_REQUIRED
    assert refreshed.processing_state != "queued"


def test_repair_campaign_has_hard_token_and_listing_cap(db_session):
    user = User(email="campaign@example.com")
    rows = [Listing(user=user, title=f"Item {i}", source_type="media_inventory_recovery", needs_review=False, processing_state=PROCESSING_QUALITY_REPAIR_REQUIRED) for i in range(3)]
    db_session.add_all([user, *rows]); db_session.commit()
    service = ListingProcessingService()
    assert service.admit_ai_repair_campaign(db_session, user_id=user.id, max_listings=10, max_tokens=9_999)["admitted_listing_ids"] == []
    admitted = service.admit_ai_repair_campaign(db_session, user_id=user.id, max_listings=10, max_tokens=10_000)
    assert len(admitted["admitted_listing_ids"]) == 1
