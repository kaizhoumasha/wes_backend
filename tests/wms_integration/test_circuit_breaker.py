from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import DateTime
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.wms_integration.models import WmsCircuitBreakerState, WmsCircuitBreakerStatus
from src.app.wms_integration.repositories import WmsCircuitBreakerRepository
from src.app.wms_integration.services import WmsCircuitBreakerService
from src.utils.timezone import timezone


@pytest.mark.asyncio
async def test_breaker_state_is_shared_by_target_code_and_operation_name(db_session) -> None:
    first = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=30)
    second = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=30)

    opened = await first.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
        evidence_key="ev-open-1",
    )

    decision = await second.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
    )

    assert opened.state == WmsCircuitBreakerStatus.OPEN
    assert decision.allowed is False
    assert decision.state == WmsCircuitBreakerStatus.OPEN
    assert opened.last_evidence_key == "ev-open-1"


@pytest.mark.asyncio
async def test_breaker_open_state_is_shared_after_commit_across_sessions(db_engine) -> None:
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    first = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=30)
    second = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=30)
    now = timezone.now_for_db()

    async with session_factory() as first_db:
        opened = await first.record_failure(
            first_db,
            target_code="WMS_INVENTORY",
            operation_name="reserve_inventory",
            evidence_key="ev-open-commit",
            now=now,
        )
        await first_db.commit()

    async with session_factory() as second_db:
        decision = await second.before_call(
            second_db,
            target_code="WMS_INVENTORY",
            operation_name="reserve_inventory",
            now=now + timedelta(seconds=1),
        )

    assert opened.state == WmsCircuitBreakerStatus.OPEN
    assert decision.allowed is False
    assert decision.reason == "OPEN_FAST_FAIL"
    assert decision.retry_after_seconds == 29


@pytest.mark.asyncio
async def test_closed_failures_reach_threshold_and_open_breaker(db_session) -> None:
    service = WmsCircuitBreakerService(failure_threshold=2, retry_after_seconds=45)
    now = timezone.now_for_db()

    first_failure = await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        evidence_key="ev-fail-1",
        now=now,
    )

    assert first_failure.state == WmsCircuitBreakerStatus.CLOSED
    assert first_failure.failure_count == 1

    second_failure = await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        evidence_key="ev-fail-2",
        now=now + timedelta(seconds=1),
    )

    assert second_failure.state == WmsCircuitBreakerStatus.OPEN
    assert second_failure.failure_count == 2
    assert second_failure.opened_until == now + timedelta(seconds=46)
    assert second_failure.last_failure_at == now + timedelta(seconds=1)
    assert second_failure.last_evidence_key == "ev-fail-2"


@pytest.mark.asyncio
async def test_open_fast_fails_until_retry_after_elapsed(db_session) -> None:
    service = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=60)
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="release_reservation",
        now=now,
    )

    fast_fail = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="release_reservation",
        now=now + timedelta(seconds=10),
    )
    retry_after = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="release_reservation",
        now=now + timedelta(seconds=61),
    )

    assert fast_fail.allowed is False
    assert fast_fail.reason == "OPEN_FAST_FAIL"
    assert fast_fail.retry_after_seconds == 50
    assert retry_after.allowed is True
    assert retry_after.state == WmsCircuitBreakerStatus.HALF_OPEN
    assert retry_after.reason == "OPEN_RETRY_AFTER_ELAPSED"
    assert retry_after.probe_generation == 1


@pytest.mark.asyncio
async def test_breaker_transitions_emit_observability_events(db_session) -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry

    emitted = []
    registry = RuntimeObservabilityRegistry(observers=(emitted.append,))
    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=10,
        observability_emit=registry.emit,
    )
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        evidence_key="ev-breaker-open",
        trace_id="trace-breaker-1",
        now=now,
    )
    await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        trace_id="trace-breaker-1",
        now=now + timedelta(seconds=11),
    )

    assert [event.name for event in emitted] == ["wms_breaker.transition", "wms_breaker.transition"]
    assert [event.attributes["breaker_state"] for event in emitted] == ["OPEN", "HALF_OPEN"]
    assert emitted[0].attributes["trace_id"] == "trace-breaker-1"
    assert emitted[0].attributes["provider_code"] == "WMS_INVENTORY"
    assert emitted[0].attributes["operation_kind"] == "query_inventory"


@pytest.mark.asyncio
async def test_breaker_transition_observability_failure_does_not_abort_state_change(db_session) -> None:
    """observability emit 是 best-effort，失败不能反向中断 breaker 状态机。"""

    def broken_emit(_name: str, _attributes: dict[str, object]) -> None:
        raise RuntimeError("observer unavailable")

    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=10,
        observability_emit=broken_emit,
    )
    now = timezone.now_for_db()

    opened = await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        evidence_key="ev-breaker-observer-fails",
        trace_id="trace-breaker-observer-fails",
        now=now,
    )

    assert opened.state == WmsCircuitBreakerStatus.OPEN
    assert opened.last_evidence_key == "ev-breaker-observer-fails"


@pytest.mark.asyncio
async def test_breaker_before_call_observability_failure_does_not_abort_half_open(db_session) -> None:
    """OPEN -> HALF_OPEN 的观测失败不能中断 before_call 放行。"""

    def broken_emit(_name: str, _attributes: dict[str, object]) -> None:
        raise RuntimeError("observer unavailable")

    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=10,
        observability_emit=broken_emit,
    )
    now = timezone.now_for_db()
    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        evidence_key="ev-breaker-open",
        now=now,
    )

    decision = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        trace_id="trace-breaker-half-open-observer-fails",
        now=now + timedelta(seconds=11),
    )

    assert decision.allowed is True
    assert decision.state == WmsCircuitBreakerStatus.HALF_OPEN
    assert decision.reason == "OPEN_RETRY_AFTER_ELAPSED"


@pytest.mark.asyncio
async def test_breaker_record_success_observability_failure_does_not_abort_close(db_session) -> None:
    """HALF_OPEN -> CLOSED 的观测失败不能中断 record_success 状态推进。"""

    def broken_emit(_name: str, _attributes: dict[str, object]) -> None:
        raise RuntimeError("observer unavailable")

    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=10,
        observability_emit=broken_emit,
    )
    now = timezone.now_for_db()
    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        evidence_key="ev-breaker-open",
        now=now,
    )
    decision = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        now=now + timedelta(seconds=11),
    )

    closed = await service.record_success(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        evidence_key="ev-breaker-close",
        probe_generation=decision.probe_generation,
        trace_id="trace-breaker-close-observer-fails",
        now=now + timedelta(seconds=12),
    )

    assert closed.state == WmsCircuitBreakerStatus.CLOSED
    assert closed.last_evidence_key == "ev-breaker-close"


@pytest.mark.asyncio
async def test_half_open_success_threshold_closes_breaker(db_session) -> None:
    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=30,
        half_open_success_threshold=2,
        half_open_max_attempts=2,
    )
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
        now=now,
    )
    first_probe = await service.before_call(
        db_session,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
        now=now + timedelta(seconds=31),
    )
    first_success = await service.record_success(
        db_session,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
        evidence_key="ev-half-success-1",
        probe_generation=first_probe.probe_generation,
        now=now + timedelta(seconds=32),
    )

    assert first_success.state == WmsCircuitBreakerStatus.HALF_OPEN
    assert first_success.half_open_success_count == 1

    second_probe = await service.before_call(
        db_session,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
        now=now + timedelta(seconds=33),
    )
    second_success = await service.record_success(
        db_session,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
        evidence_key="ev-half-success-2",
        probe_generation=second_probe.probe_generation,
        now=now + timedelta(seconds=34),
    )

    assert second_success.state == WmsCircuitBreakerStatus.CLOSED
    assert second_success.failure_count == 0
    assert second_success.opened_until is None
    assert second_success.half_open_attempt_count == 0
    assert second_success.half_open_success_count == 0
    assert second_success.last_evidence_key == "ev-half-success-2"


@pytest.mark.asyncio
async def test_half_open_failure_reopens_breaker(db_session) -> None:
    service = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=20)
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
        now=now,
    )
    probe = await service.before_call(
        db_session,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
        now=now + timedelta(seconds=21),
    )
    reopened = await service.record_failure(
        db_session,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
        evidence_key="ev-half-fail",
        probe_generation=probe.probe_generation,
        now=now + timedelta(seconds=22),
    )

    assert reopened.state == WmsCircuitBreakerStatus.OPEN
    assert reopened.opened_until == now + timedelta(seconds=42)
    assert reopened.half_open_attempt_count == 0
    assert reopened.half_open_success_count == 0
    assert reopened.last_evidence_key == "ev-half-fail"


@pytest.mark.asyncio
async def test_half_open_ignores_stale_probe_success_until_matching_probe_returns(db_session) -> None:
    service = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=10)
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        now=now,
    )
    probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        now=now + timedelta(seconds=11),
    )

    stale_success = await service.record_success(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        evidence_key="ev-stale-success",
        probe_generation=0,
        now=now + timedelta(seconds=12),
    )

    assert probe.probe_generation == 1
    assert stale_success.state == WmsCircuitBreakerStatus.HALF_OPEN
    assert stale_success.half_open_success_count == 0
    assert stale_success.last_evidence_key is None

    matching_success = await service.record_success(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="query_inventory",
        evidence_key="ev-matching-success",
        probe_generation=probe.probe_generation,
        now=now + timedelta(seconds=13),
    )

    assert matching_success.state == WmsCircuitBreakerStatus.CLOSED
    assert matching_success.last_evidence_key == "ev-matching-success"


@pytest.mark.asyncio
async def test_half_open_ignores_stale_probe_failure(db_session) -> None:
    service = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=10)
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="release_reservation",
        now=now,
    )
    probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="release_reservation",
        now=now + timedelta(seconds=11),
    )

    stale_failure = await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="release_reservation",
        evidence_key="ev-stale-failure",
        probe_generation=0,
        now=now + timedelta(seconds=12),
    )

    assert probe.probe_generation == 1
    assert stale_failure.state == WmsCircuitBreakerStatus.HALF_OPEN
    assert stale_failure.last_failure_at == now
    assert stale_failure.last_evidence_key is None


@pytest.mark.asyncio
async def test_half_open_attempt_limit_blocks_parallel_probe(db_session) -> None:
    service = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=10, half_open_max_attempts=1)
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
        now=now,
    )
    first_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
        now=now + timedelta(seconds=11),
    )
    second_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
        now=now + timedelta(seconds=12),
    )

    assert first_probe.allowed is True
    assert second_probe.allowed is False
    assert second_probe.reason == "HALF_OPEN_TRIAL_IN_PROGRESS"


@pytest.mark.asyncio
async def test_half_open_attempt_limit_uses_committed_state_across_sessions(db_engine) -> None:
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    service = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=10, half_open_max_attempts=1)
    now = timezone.now_for_db()

    async with session_factory() as setup_db:
        await service.record_failure(
            setup_db,
            target_code="WMS_INVENTORY",
            operation_name="confirm_outbound",
            now=now,
        )
        await setup_db.commit()

    async with session_factory() as first_db:
        first_probe = await service.before_call(
            first_db,
            target_code="WMS_INVENTORY",
            operation_name="confirm_outbound",
            now=now + timedelta(seconds=11),
        )
        await first_db.commit()

    async with session_factory() as second_db:
        second_probe = await service.before_call(
            second_db,
            target_code="WMS_INVENTORY",
            operation_name="confirm_outbound",
            now=now + timedelta(seconds=12),
        )

    assert first_probe.allowed is True
    assert first_probe.probe_generation == 1
    assert second_probe.allowed is False
    assert second_probe.reason == "HALF_OPEN_TRIAL_IN_PROGRESS"


@pytest.mark.asyncio
async def test_half_open_probe_expiry_recovers_after_committed_orphan_probe_across_sessions(db_engine) -> None:
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=10,
        half_open_max_attempts=1,
        half_open_probe_timeout_seconds=5,
    )
    now = timezone.now_for_db()

    async with session_factory() as setup_db:
        await service.record_failure(
            setup_db,
            target_code="WMS_INVENTORY",
            operation_name="orphan_probe_recovery",
            now=now,
        )
        await setup_db.commit()

    async with session_factory() as first_db:
        orphan_probe = await service.before_call(
            first_db,
            target_code="WMS_INVENTORY",
            operation_name="orphan_probe_recovery",
            now=now + timedelta(seconds=11),
        )
        await first_db.commit()

    async with session_factory() as second_db:
        blocked_probe = await service.before_call(
            second_db,
            target_code="WMS_INVENTORY",
            operation_name="orphan_probe_recovery",
            now=now + timedelta(seconds=15),
        )

    async with session_factory() as third_db:
        recovered_probe = await service.before_call(
            third_db,
            target_code="WMS_INVENTORY",
            operation_name="orphan_probe_recovery",
            now=now + timedelta(seconds=17),
        )

    assert orphan_probe.allowed is True
    assert orphan_probe.probe_generation == 1
    assert blocked_probe.allowed is False
    assert blocked_probe.reason == "HALF_OPEN_TRIAL_IN_PROGRESS"
    assert recovered_probe.allowed is True
    assert recovered_probe.probe_generation == 2


@pytest.mark.asyncio
async def test_half_open_probe_blocks_parallel_call_before_probe_expires(db_session) -> None:
    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=10,
        half_open_max_attempts=1,
        half_open_probe_timeout_seconds=5,
    )
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_block",
        now=now,
    )
    first_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_block",
        now=now + timedelta(seconds=11),
    )
    second_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_block",
        now=now + timedelta(seconds=15),
    )

    assert first_probe.allowed is True
    assert first_probe.probe_generation == 1
    assert second_probe.allowed is False
    assert second_probe.reason == "HALF_OPEN_TRIAL_IN_PROGRESS"


@pytest.mark.asyncio
async def test_half_open_probe_expiry_allows_new_probe_with_new_generation(db_session) -> None:
    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=10,
        half_open_max_attempts=1,
        half_open_probe_timeout_seconds=5,
    )
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_retry",
        now=now,
    )
    first_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_retry",
        now=now + timedelta(seconds=11),
    )
    retry_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_retry",
        now=now + timedelta(seconds=17),
    )

    assert first_probe.allowed is True
    assert first_probe.probe_generation == 1
    assert retry_probe.allowed is True
    assert retry_probe.probe_generation == 2


@pytest.mark.asyncio
async def test_expired_half_open_probe_results_do_not_change_current_probe_window(db_session) -> None:
    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=10,
        half_open_max_attempts=1,
        half_open_probe_timeout_seconds=5,
    )
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_stale_result",
        now=now,
    )
    old_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_stale_result",
        now=now + timedelta(seconds=11),
    )
    current_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_stale_result",
        now=now + timedelta(seconds=17),
    )

    stale_success = await service.record_success(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_stale_result",
        evidence_key="ev-expired-success",
        probe_generation=old_probe.probe_generation,
        now=now + timedelta(seconds=18),
    )
    stale_failure = await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_stale_result",
        evidence_key="ev-expired-failure",
        probe_generation=old_probe.probe_generation,
        now=now + timedelta(seconds=19),
    )

    assert current_probe.allowed is True
    assert current_probe.probe_generation == 2
    assert stale_success.state == WmsCircuitBreakerStatus.HALF_OPEN
    assert stale_failure.state == WmsCircuitBreakerStatus.HALF_OPEN
    assert stale_failure.half_open_probe_generation == current_probe.probe_generation
    assert stale_failure.last_failure_at == now
    assert stale_failure.last_evidence_key is None


@pytest.mark.asyncio
async def test_current_half_open_probe_results_still_close_or_reopen_after_expiry_recovery(db_session) -> None:
    service = WmsCircuitBreakerService(
        failure_threshold=1,
        retry_after_seconds=10,
        half_open_max_attempts=1,
        half_open_probe_timeout_seconds=5,
    )
    now = timezone.now_for_db()

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_current_success",
        now=now,
    )
    await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_current_success",
        now=now + timedelta(seconds=11),
    )
    success_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_current_success",
        now=now + timedelta(seconds=17),
    )
    closed = await service.record_success(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_current_success",
        evidence_key="ev-current-success",
        probe_generation=success_probe.probe_generation,
        now=now + timedelta(seconds=18),
    )

    await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_current_failure",
        now=now,
    )
    await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_current_failure",
        now=now + timedelta(seconds=11),
    )
    failure_probe = await service.before_call(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_current_failure",
        now=now + timedelta(seconds=17),
    )
    reopened = await service.record_failure(
        db_session,
        target_code="WMS_INVENTORY",
        operation_name="probe_timeout_current_failure",
        evidence_key="ev-current-failure",
        probe_generation=failure_probe.probe_generation,
        now=now + timedelta(seconds=18),
    )

    assert success_probe.probe_generation == 2
    assert closed.state == WmsCircuitBreakerStatus.CLOSED
    assert closed.last_evidence_key == "ev-current-success"
    assert failure_probe.probe_generation == 2
    assert reopened.state == WmsCircuitBreakerStatus.OPEN
    assert reopened.last_evidence_key == "ev-current-failure"


def test_breaker_model_declares_unique_shared_key() -> None:
    table = WmsCircuitBreakerState.__table__
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
        if index.name != "ix_wes_biz_wms_circuit_breaker_state_id"
    }

    assert indexes == {
        "ux_wms_circuit_breaker_target_operation": ("target_code", "operation_name"),
    }


def test_breaker_model_and_migration_define_half_open_probe_expiry_column() -> None:
    column = WmsCircuitBreakerState.__table__.c["half_open_probe_expires_at"]

    assert isinstance(column.type, DateTime)
    assert column.nullable is True

    migration = Path("migrations/versions/20260527_0105_07be7a97f4a6_add_wms_circuit_breaker_state.py")
    migration_text = migration.read_text(encoding="utf-8")

    assert 'sa.Column("half_open_probe_expires_at", sa.DateTime(), nullable=True' in migration_text


def test_breaker_repository_uses_row_lock_for_state_update() -> None:
    statement = WmsCircuitBreakerRepository().build_key_lookup_statement(
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
        for_update=True,
    )

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE" in compiled
