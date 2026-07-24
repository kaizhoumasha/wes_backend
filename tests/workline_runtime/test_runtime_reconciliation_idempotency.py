"""Runtime reconciliation 热路径幂等登记测试。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.app.reconciliation.manager import ReconciliationManager, ReconciliationRegistrationResult
from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
from src.app.sys.models import SystemOutboxStatus
from src.utils.timezone import timezone


class _ReconciliationDb:
    def __init__(self, command: Any | None = None) -> None:
        self.command = command
        self.flush = AsyncMock()

    async def get(self, _model: Any, _pk: int) -> Any | None:
        return self.command


class _RecordingReconciliationManager:
    def __init__(self, claim_result: ClaimResult = ClaimResult.NEW) -> None:
        self.claim_result = claim_result
        self.calls: list[dict[str, Any]] = []
        self._manager = ReconciliationManager()

    async def register_conflict_idempotent(self, db: Any, conflict: Any, **kwargs: Any) -> Any:
        self.calls.append({"db": db, "conflict": conflict, **kwargs})
        return ReconciliationRegistrationResult(
            decision=self._manager.register_conflict(conflict),
            claim_result=self.claim_result,
        )


def _build_dispatch_ack_objects() -> tuple[Any, Any, Any, Any]:
    from src.app.device.models.command import CommandStatus
    from src.app.runtime.orchestration.models.session import SessionStatus
    from src.app.sys.models import SystemOutboxStatus

    now = timezone.now_for_db()
    command = SimpleNamespace(
        id=881,
        command_code="CMD-ACK-EXHAUSTED",
        workline_id=45,
        device_id=7,
        correlation_id="corr-runtime-reconciliation-dispatch",
        status=CommandStatus.SENT,
        completed_at=None,
        error_detail=None,
    )
    outbox = SimpleNamespace(
        id=862,
        session_id=553,
        workline_id=45,
        target_code="CONVEYOR01",
        dispatch_key="device-command:CMD-ACK-EXHAUSTED",
        status=SystemOutboxStatus.SENT,
        last_error=None,
        next_retry_at=now,
        finished_at=None,
        blocked_by_runtime_hold_id=None,
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
        payload_json={"command_code": "CMD-ACK-EXHAUSTED"},
    )
    session = SimpleNamespace(
        id=553,
        workline_id=45,
        trace_id="trace-dispatch-reconciliation",
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=300,
        waiting_since=now,
        deadline_at=None,
        awaiting_device_command_code=command.command_code,
        reconciliation_state=None,
        context_json={},
    )
    workline = SimpleNamespace(id=45)
    return command, outbox, session, workline


def _build_timer_timeout_objects() -> tuple[Any, Any, Any, Any]:
    from src.app.device.models.command import CommandStatus
    from src.app.runtime.orchestration.models.session import SessionStatus

    now = timezone.now_for_db()
    deadline_at = now - timedelta(seconds=1)
    ack_received_at = now - timedelta(seconds=30)
    command = SimpleNamespace(
        id=991,
        command_code="CMD-TIMER-TIMEOUT",
        workline_id=45,
        device_id=7,
        correlation_id="corr-runtime-reconciliation-timer",
        status=CommandStatus.ACK_RECEIVED,
        ack_received_at=ack_received_at,
    )
    inbox = SimpleNamespace(
        id=901,
        session_id=553,
        workline_id=45,
        payload_json={
            "session_id": 553,
            "workline_id": 45,
            "deadline_at": deadline_at.isoformat(),
            "command_code": command.command_code,
            "awaiting_device_command_code": command.command_code,
            "ack_received_at": ack_received_at.isoformat(),
        },
    )
    session = SimpleNamespace(
        id=553,
        workline_id=45,
        trace_id="trace-timer-reconciliation",
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=300,
        waiting_since=now - timedelta(seconds=400),
        deadline_at=deadline_at,
        awaiting_device_command_code=command.command_code,
        reconciliation_state=None,
        context_json={},
        ended_at=None,
    )
    workline = SimpleNamespace(id=45)
    return command, inbox, session, workline


def _build_service(*, session: Any, workline: Any, reconciliation_manager: Any) -> Any:
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        WorklineRuntimeReconciliationService,
    )

    session_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=session))
    workline_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=workline))
    device_service = SimpleNamespace(
        mark_dispatch_ack_exhausted=AsyncMock(return_value=None),
        mark_callback_deadline_expired=AsyncMock(return_value=None),
    )
    runtime_hold_creation_service = SimpleNamespace(
        create_for_dispatch_ack_exhausted=AsyncMock(return_value=SimpleNamespace(id=9904)),
        create_for_callback_deadline_expired=AsyncMock(return_value=SimpleNamespace(id=9905)),
    )
    workline_status_projection_service = SimpleNamespace(project_reconciling=AsyncMock(return_value=True))
    system_outbox_cancellation_service = SimpleNamespace(cancel_active_by_session=AsyncMock(return_value=0))
    rack_task_repository = SimpleNamespace(cancel_active_by_material_session=AsyncMock(return_value=0))
    return WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        system_outbox_cancellation_service=system_outbox_cancellation_service,
        device_service=device_service,
        runtime_hold_creation_service=runtime_hold_creation_service,
        rack_task_repository=rack_task_repository,
        reconciliation_manager=reconciliation_manager,
        workline_status_projection_service=workline_status_projection_service,
    )


@pytest.mark.asyncio
async def test_dispatch_ack_exhausted_registers_reconciliation_idempotency_before_hold() -> None:
    """dispatch ACK exhausted 进入隔离时必须登记 reconciliation 幂等 claim。"""

    command, outbox, session, workline = _build_dispatch_ack_objects()
    manager = _RecordingReconciliationManager()
    service = _build_service(session=session, workline=workline, reconciliation_manager=manager)
    db = _ReconciliationDb(command=command)

    with (
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.add_timeline_with_sequence",
            new=AsyncMock(),
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
    ):
        _ = await service.handle_dispatch_ack_exhausted(
            db,
            outbox=outbox,
            command=command,
            error_message="COMMAND_ACK_TIMEOUT",
        )

    assert len(manager.calls) == 1
    call = manager.calls[0]
    assert call["provider_code"] == "WES"
    assert call["idempotency_key"] == "runtime-reconciliation:COMMAND_ACK_EXHAUSTED:outbox:862"
    assert call["execution_correlation_id"] == "corr-runtime-reconciliation-dispatch"
    assert call["business_owner_key"] == "runtime:ExecutionSession:553"
    assert len(call["request_hash"]) == 64
    assert call["conflict"].owner_domain == "runtime"
    assert call["conflict"].owner_kind == "ExecutionSession"
    assert call["conflict"].owner_id == "553"
    assert call["conflict"].conflict_kind == "COMMAND_ACK_EXHAUSTED"
    assert call["conflict"].evidence_refs == ["outbox:862", "command:881"]
    assert outbox.status is SystemOutboxStatus.SENT

    audit = session.context_json["runtime_reconciliation_registration"]
    assert audit["claim_result"] == "NEW"
    assert audit["idempotency_key"] == "runtime-reconciliation:COMMAND_ACK_EXHAUSTED:outbox:862"
    assert audit["operation_kind"] == "reconciliation"
    assert audit["decision"]["allowed_next_effect_scope"]["owner_id"] == "553"


@pytest.mark.asyncio
async def test_timer_timeout_registers_reconciliation_idempotency_with_command_correlation() -> None:
    """TIMER_TIMEOUT 进入 callback deadline 对账时必须复用命令 correlation claim。"""

    command, inbox, session, workline = _build_timer_timeout_objects()
    manager = _RecordingReconciliationManager(claim_result=ClaimResult.MATCH)
    service = _build_service(session=session, workline=workline, reconciliation_manager=manager)
    db = _ReconciliationDb(command=command)

    class _CommandRepo:
        async def get_by_command_code(self, _db: Any, _command_code: str) -> Any:
            return command

    with (
        patch(
            "src.app.device.repositories.command_repository.DeviceCommandRepository",
            _CommandRepo,
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.add_timeline_with_sequence",
            new=AsyncMock(),
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
    ):
        result = await service.handle_timer_timeout(
            db,
            session_id=553,
            inbox_id=901,
            payload={"event_type": "TIMER_TIMEOUT", "data": inbox.payload_json},
            source_inbox_id=77,
            correlation_id="corr-runtime-reconciliation-timer",
            trace_id="trace-timer-reconciliation",
        )

    assert result.disposition == "RECONCILED"
    assert result.session is session
    assert len(manager.calls) == 1
    call = manager.calls[0]
    assert call["idempotency_key"] == "runtime-reconciliation:CALLBACK_DEADLINE_EXPIRED:inbox:901"
    assert call["execution_correlation_id"] == "corr-runtime-reconciliation-timer"
    assert call["conflict"].conflict_kind == "CALLBACK_DEADLINE_EXPIRED"
    assert call["conflict"].evidence_refs == ["inbox:901", "command:991"]

    audit = session.context_json["runtime_reconciliation_registration"]
    assert audit["claim_result"] == "MATCH"
    assert audit["correlation_id"] == "corr-runtime-reconciliation-timer"
    assert audit["decision"]["runtime_hold_required"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session", "expected_disposition"),
    [
        (None, "SESSION_MISSING"),
        (SimpleNamespace(id=553, status="COMPLETED"), "SESSION_NOT_WAITING"),
    ],
)
async def test_timer_timeout_returns_structured_ignored_result_without_terminal_write(
    session: Any,
    expected_disposition: str,
) -> None:
    """Session 不存在或已终态时仅返回结构化结果，由 processor 负责终态。"""

    manager = _RecordingReconciliationManager()
    service = _build_service(session=session, workline=None, reconciliation_manager=manager)
    result = await service.handle_timer_timeout(
        _ReconciliationDb(),
        session_id=553,
        inbox_id=901,
        payload={"event_type": "TIMER_TIMEOUT", "data": {"deadline_at": "2026-07-11T08:00:00"}},
        source_inbox_id=77,
    )

    assert result.disposition == expected_disposition
    assert result.session is session
    assert manager.calls == []


@pytest.mark.asyncio
async def test_timer_timeout_rejects_non_ack_command_evidence_without_terminal_write() -> None:
    """命令未 ACK 的 timeout evidence 不进入对账，终态仍由 processor fenced 写回。"""

    command, inbox, session, workline = _build_timer_timeout_objects()
    command.status = "SENT"
    manager = _RecordingReconciliationManager()
    service = _build_service(session=session, workline=workline, reconciliation_manager=manager)

    class _CommandRepo:
        async def get_by_command_code(self, _db: Any, _command_code: str) -> Any:
            return command

    with (
        patch("src.app.device.repositories.command_repository.DeviceCommandRepository", _CommandRepo),
    ):
        result = await service.handle_timer_timeout(
            _ReconciliationDb(command=command),
            session_id=553,
            inbox_id=901,
            payload={"event_type": "TIMER_TIMEOUT", "data": inbox.payload_json},
            source_inbox_id=77,
        )

    assert result.disposition == "EVIDENCE_STALE"
    assert result.session is session
    assert manager.calls == []
