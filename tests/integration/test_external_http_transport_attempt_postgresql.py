"""EXTERNAL_HTTP typed attempt evidence 的 PostgreSQL 往返合同。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlmodel import select

from src.app.effect_ledger_status import DispatchAttemptStatus
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
    workline_dispatch_attempt_service,
)
from src.app.runtime.orchestration.services.intent.operation_service import _transition_sandbox_outbox_to_sent
from src.app.sys.dispatch_concurrency import DispatchPolicyRegistry, FairDispatchScheduler
from src.app.sys.external_http_transport import ExternalHttpTransportPhase, ExternalHttpTransportResult
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.sys.repositories import SystemOutboxRepository
from src.app.workline.models.workline import LineType, WorkLine
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_value
from tests.support.external_http import frozen_outbox_namespace

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _external_http_outbox(
    *,
    dispatch_key: str,
    status: SystemOutboxStatus,
    lease_owner_token: str | None = None,
    lease_expires_at: datetime | None = None,
    workline_id: int | None = None,
    session_id: int | None = None,
    operation_domain: str = "T8E_INTEGRATION",
    provider_profile_identity: str = "t8e.external-http.profile",
    operation_identity: str = "t8e.external-http.operation",
) -> SystemOutbox:
    projection = {"request_id": dispatch_key}
    frozen = frozen_outbox_namespace(
        projection,
        target_code="WMS_RCS_BIN_OPERATION",
        target_url="https://wms.example/rack-operation",
        provider_profile_identity=provider_profile_identity,
        operation_identity=operation_identity,
    )
    return SystemOutbox(
        **vars(frozen),
        session_id=session_id,
        workline_id=workline_id,
        operation_domain=operation_domain,
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=dispatch_key,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        status=status,
        lease_owner_token=lease_owner_token,
        lease_expires_at=lease_expires_at,
    )


@pytest.mark.asyncio
async def test_ambiguous_external_http_attempt_evidence_round_trips(integration_db_session: AsyncSession) -> None:
    dispatch_key = f"typed-transport-attempt:{uuid4().hex}"
    outbox = _external_http_outbox(
        dispatch_key=dispatch_key,
        status=SystemOutboxStatus.DISPATCHING,
        operation_domain="HANDLING",
        provider_profile_identity="wms.legacy-transport.production",
        operation_identity="wms.transport.handling@v1",
        lease_owner_token="integration-attempt-owner",
        lease_expires_at=timezone.now_for_db() + timedelta(minutes=5),
    )
    integration_db_session.add(outbox)
    await integration_db_session.flush()
    attempt = await workline_dispatch_attempt_service.create_attempt(
        integration_db_session,
        outbox=outbox,
        auto_commit=False,
    )
    transport_result = ExternalHttpTransportResult.ambiguous(
        phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
        error_code="READ_TIMEOUT",
        error_message="response timeout",
    )
    await workline_dispatch_attempt_service.finalize_external_http_attempt_record(
        integration_db_session,
        attempt=attempt,
        lease_owner_token="integration-attempt-owner",
        result=transport_result,
        outbox_finalization="unknown",
        auto_commit=False,
    )
    await integration_db_session.flush()
    attempt_id = attempt.id
    integration_db_session.expire_all()

    persisted = await integration_db_session.scalar(
        select(WorklineDispatchAttempt).where(WorklineDispatchAttempt.id == attempt_id)
    )

    assert persisted is not None
    assert persisted.status is DispatchAttemptStatus.UNKNOWN
    assert persisted.transport_outcome == "AMBIGUOUS"
    assert persisted.transport_phase == "AWAITING_RESPONSE"
    assert persisted.protocol_result == "NOT_AVAILABLE"
    assert persisted.safe_to_retry is False
    assert persisted.http_status_code is None
    assert persisted.response_json == {
        "transport": transport_result.evidence_json(),
        "outbox_finalization": "unknown",
    }


@pytest.mark.asyncio
async def test_evidence_recovery_requires_current_unexpired_postgresql_lease(
    integration_db_session: AsyncSession,
) -> None:
    repository = SystemOutboxRepository()
    now = timezone.now_for_db()
    prefix = uuid4().hex
    expired = _external_http_outbox(
        dispatch_key=f"evidence-recovery:{prefix}:expired",
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token="evidence-owner-expired",
        lease_expires_at=now - timedelta(seconds=1),
    )
    already_fenced = _external_http_outbox(
        dispatch_key=f"evidence-recovery:{prefix}:already-fenced",
        status=SystemOutboxStatus.UNKNOWN,
        lease_owner_token="evidence-owner-fenced",
    )
    already_fenced.last_error = "STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED"
    already_fenced.finished_at = now - timedelta(seconds=2)
    different_owner = _external_http_outbox(
        dispatch_key=f"evidence-recovery:{prefix}:different-owner",
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token="replacement-owner",
        lease_expires_at=now + timedelta(minutes=5),
    )
    current = _external_http_outbox(
        dispatch_key=f"evidence-recovery:{prefix}:current",
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token="evidence-owner-current",
        lease_expires_at=now + timedelta(minutes=5),
    )
    integration_db_session.add_all([expired, already_fenced, different_owner, current])
    await integration_db_session.flush()
    fenced_evidence = (already_fenced.last_error, already_fenced.finished_at, already_fenced.attempt_count)

    assert (
        await repository.mark_evidence_persistence_unknown(
            integration_db_session,
            expired.id,
            "LATE_RECOVERY",
            lease_owner_token="evidence-owner-expired",
        )
        is None
    )
    assert (
        await repository.mark_evidence_persistence_unknown(
            integration_db_session,
            already_fenced.id,
            "LATE_RECOVERY",
            lease_owner_token="evidence-owner-fenced",
        )
        is None
    )
    assert (
        await repository.mark_evidence_persistence_unknown(
            integration_db_session,
            different_owner.id,
            "STALE_OWNER_RECOVERY",
            lease_owner_token="evidence-owner-old",
        )
        is None
    )
    updated = await repository.mark_evidence_persistence_unknown(
        integration_db_session,
        current.id,
        "EXTERNAL_HTTP_EVIDENCE_PERSISTENCE_FAILED outcome=ACCEPTED",
        lease_owner_token="evidence-owner-current",
    )
    await integration_db_session.flush()

    assert updated is current
    assert enum_value(current.status) == SystemOutboxStatus.UNKNOWN.value
    assert current.lease_expires_at is None
    assert (already_fenced.last_error, already_fenced.finished_at, already_fenced.attempt_count) == fenced_evidence
    assert enum_value(expired.status) == SystemOutboxStatus.DISPATCHING.value
    assert enum_value(different_owner.status) == SystemOutboxStatus.DISPATCHING.value


@pytest.mark.asyncio
async def test_callback_closes_dispatching_lease_shape_on_postgresql(integration_db_session: AsyncSession) -> None:
    owner = f"callback-owner:{uuid4().hex}"
    outbox = _external_http_outbox(
        dispatch_key=f"callback-lease-shape:{uuid4().hex}",
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token=owner,
        lease_expires_at=timezone.now_for_db() + timedelta(minutes=5),
    )
    integration_db_session.add(outbox)
    await integration_db_session.flush()

    closed = await SystemOutboxRepository().finish_sent_external_by_dispatch_key(
        integration_db_session,
        outbox.dispatch_key,
    )
    await integration_db_session.flush()

    assert closed is outbox
    assert enum_value(outbox.status) == SystemOutboxStatus.SENT.value
    assert outbox.lease_expires_at is None
    assert outbox.lease_owner_token == owner


@pytest.mark.asyncio
async def test_sandbox_transition_closes_dispatching_lease_shape_on_postgresql(
    integration_db_session: AsyncSession,
) -> None:
    owner = f"sandbox-callback-owner:{uuid4().hex}"
    outbox = _external_http_outbox(
        dispatch_key=f"sandbox-callback-lease-shape:{uuid4().hex}",
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token=owner,
        lease_expires_at=timezone.now_for_db() + timedelta(minutes=5),
    )
    integration_db_session.add(outbox)
    await integration_db_session.flush()

    _transition_sandbox_outbox_to_sent(outbox)
    await integration_db_session.flush()

    assert enum_value(outbox.status) == SystemOutboxStatus.SENT.value
    assert outbox.lease_expires_at is None
    assert outbox.lease_owner_token == owner


@pytest.mark.asyncio
async def test_workline_cancel_closes_dispatching_lease_shape_on_postgresql(
    integration_db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex
    workline = WorkLine(line_code=f"T8E-WORKLINE-{suffix}", line_name="T8e lease shape", line_type=LineType.AUTO)
    integration_db_session.add(workline)
    await integration_db_session.flush()
    owner = f"workline-cancel-owner:{suffix}"
    outbox = _external_http_outbox(
        dispatch_key=f"workline-cancel-lease-shape:{suffix}",
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token=owner,
        lease_expires_at=timezone.now_for_db() + timedelta(minutes=5),
        workline_id=workline.id,
    )
    integration_db_session.add(outbox)
    await integration_db_session.flush()

    cancelled = await SystemOutboxRepository().cancel_active_by_workline(
        integration_db_session,
        workline.id,
        incident_id=991,
    )
    await integration_db_session.flush()

    assert cancelled == 1
    assert enum_value(outbox.status) == SystemOutboxStatus.CANCELLED.value
    assert outbox.lease_expires_at is None
    assert outbox.lease_owner_token == owner


@pytest.mark.asyncio
async def test_session_cancel_closes_dispatching_lease_shape_on_postgresql(
    integration_db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex
    workline = WorkLine(line_code=f"T8E-SESSION-{suffix}", line_name="T8e session lease shape", line_type=LineType.AUTO)
    integration_db_session.add(workline)
    await integration_db_session.flush()
    session = WorklineSession(
        session_code=f"T8E-SESSION-{suffix}",
        workline_id=workline.id,
        plugin_key="t8e-lease-shape",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
    )
    integration_db_session.add(session)
    await integration_db_session.flush()
    owner = f"session-cancel-owner:{suffix}"
    outbox = _external_http_outbox(
        dispatch_key=f"session-cancel-lease-shape:{suffix}",
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token=owner,
        lease_expires_at=timezone.now_for_db() + timedelta(minutes=5),
        workline_id=workline.id,
        session_id=session.id,
    )
    integration_db_session.add(outbox)
    await integration_db_session.flush()

    cancelled = await SystemOutboxRepository().cancel_active_by_session(
        integration_db_session,
        session_id=session.id,
        reason="MANUAL_CANCEL_REQUESTED",
    )
    await integration_db_session.flush()

    assert cancelled == 1
    assert enum_value(outbox.status) == SystemOutboxStatus.CANCELLED.value
    assert outbox.lease_expires_at is None
    assert outbox.lease_owner_token == owner


async def _seed_bound_expired_lease(
    db: AsyncSession,
    *,
    suffix: str,
) -> tuple[SystemOutbox, WorklineDispatchAttempt, RuntimeIntentLog]:
    now = timezone.now_for_db()
    dispatch_key = f"bound-expired-lease:{suffix}"
    execution_session = ExecutionSession(workline_id=991, manifest_version="v1", state="RUNNING")
    db.add(execution_session)
    await db.flush()
    correlation = ExecutionCorrelation(
        correlation_id=f"bound-expired-correlation:{suffix}",
        execution_session_id=execution_session.id,
        trace_id=f"bound-expired-trace:{suffix}",
    )
    db.add(correlation)
    await db.flush()
    intent = RuntimeIntentLog(
        execution_session_id=execution_session.id,
        correlation_id=correlation.correlation_id,
        provider_code="WMS",
        operation_kind="system_capability_effect",
        target_domain="wms_integration",
        target_action="lease_loss_test",
        idempotency_key=f"bound-expired-idempotency:{suffix}",
        request_hash="a" * 64,
        dispatch_key=dispatch_key,
    )
    owner = f"bound-expired-owner:{suffix}"
    outbox = _external_http_outbox(
        dispatch_key=dispatch_key,
        status=SystemOutboxStatus.DISPATCHING,
        lease_owner_token=owner,
        lease_expires_at=now - timedelta(seconds=1),
    )
    db.add_all([intent, outbox])
    await db.flush()
    attempt = WorklineDispatchAttempt(
        outbox_id=outbox.id,
        dispatch_key=dispatch_key,
        attempt_no=1,
        lease_token=owner,
        lease_expires_at=outbox.lease_expires_at,
        status=DispatchAttemptStatus.DISPATCHING,
        started_at=now - timedelta(minutes=1),
    )
    db.add(attempt)
    await db.flush()
    return outbox, attempt, intent


@pytest.mark.asyncio
async def test_expired_bound_http_lease_closes_attempt_and_opens_reconciliation_atomically(
    integration_db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex
    outbox, attempt, intent = await _seed_bound_expired_lease(integration_db_session, suffix=suffix)
    scheduler = FairDispatchScheduler(
        repository=SystemOutboxRepository(),
        policy_registry=DispatchPolicyRegistry(),
        worker_identity=f"lease-loss-worker:{suffix}",
    )

    batch = await scheduler.claim(
        integration_db_session,
        limit=1,
        now=timezone.now_for_db(),
        operation_domains=("T8E_INTEGRATION",),
    )
    await integration_db_session.flush()
    case = await integration_db_session.scalar(
        select(ReconciliationCase).where(ReconciliationCase.dispatch_key == outbox.dispatch_key)
    )

    assert batch.claims == ()
    assert batch.metrics.lease_loss_count == 1
    assert enum_value(outbox.status) == SystemOutboxStatus.UNKNOWN.value
    assert enum_value(attempt.status) == DispatchAttemptStatus.UNKNOWN.value
    assert attempt.transport_outcome == "AMBIGUOUS"
    assert attempt.safe_to_retry is False
    assert enum_value(intent.effect_status) == RuntimeIntentStatus.RECONCILING.value
    assert [item["event_type"] for item in intent.outcome_history_json] == [
        "TRANSPORT_AMBIGUOUS",
        "RECONCILIATION_OPENED",
    ]
    assert case is not None
    assert enum_value(case.status) == ReconciliationCaseStatus.OPEN.value
    assert case.reason_code == "STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED"


@pytest.mark.asyncio
async def test_expired_http_lease_bridge_failure_rolls_back_the_whole_closure_savepoint(
    integration_db_session: AsyncSession,
) -> None:
    from src.app.runtime.orchestration.services.inbox.external_http_lease_loss_service import (
        ExternalHttpLeaseLossService,
    )

    class _FailingBridge:
        async def record_result(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("bridge unavailable")

    suffix = uuid4().hex
    outbox, attempt, _intent = await _seed_bound_expired_lease(integration_db_session, suffix=suffix)
    service = ExternalHttpLeaseLossService(effect_transport_bridge=_FailingBridge())

    with pytest.raises(RuntimeError, match="bridge unavailable"):
        await service.fence_expired_leases(
            integration_db_session,
            now=timezone.now_for_db(),
            operation_domains=("T8E_INTEGRATION",),
        )

    await integration_db_session.refresh(outbox)
    await integration_db_session.refresh(attempt)
    assert enum_value(outbox.status) == SystemOutboxStatus.DISPATCHING.value
    assert outbox.lease_expires_at is not None
    assert enum_value(attempt.status) == DispatchAttemptStatus.DISPATCHING.value
    assert attempt.finalized_at is None
