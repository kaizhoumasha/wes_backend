from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.app.device.models.command import CommandStatus
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import (
    RuntimeReconciliationReason,
    RuntimeReconciliationSourceKind,
    RuntimeReconciliationState,
    SessionStatus,
)
from src.app.workline.services.runtime_reconciliation_service import WorklineRuntimeReconciliationService
from src.utils.timezone import timezone


class _Db:
    def __init__(self, command: object | None = None) -> None:
        self.command = command
        self.flush = AsyncMock()

    async def get(self, _model: object, _pk: int) -> object | None:
        return self.command


@pytest.mark.asyncio
async def test_activate_execution_deadline_after_ack_uses_wait_timeout_seconds() -> None:
    ack_received_at = datetime(2026, 5, 8, 8, 0, 0)
    session = SimpleNamespace(
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=180,
        deadline_at=None,
    )
    session_repo = SimpleNamespace(get_open_session_by_awaiting_command_id=AsyncMock(return_value=session))
    db = _Db()
    service = WorklineRuntimeReconciliationService(session_repository=session_repo)

    updated = await service.activate_execution_deadline_after_ack(
        db,
        command_id=9,
        ack_received_at=ack_received_at,
    )

    assert updated is session
    assert session.deadline_at == ack_received_at + timedelta(seconds=180)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_timer_timeout_enters_runtime_reconciliation_and_clears_wait() -> None:
    deadline_at = timezone.now_for_db() - timedelta(seconds=10)
    ack_received_at = deadline_at - timedelta(seconds=180)
    command = SimpleNamespace(
        id=9,
        device_id=7,
        status=CommandStatus.ACK_RECEIVED,
        ack_received_at=ack_received_at,
    )
    session = SimpleNamespace(
        id=530,
        workline_id=45,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_token="CMD-001",
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=180,
        waiting_since=ack_received_at,
        deadline_at=deadline_at,
        awaiting_command_id=9,
        reconciliation_state=None,
    )
    inbox = SimpleNamespace(
        id=88,
        session_id=530,
        payload_json={
            "deadline_at": deadline_at.isoformat(),
            "wait_token": "CMD-001",
            "awaiting_command_id": 9,
        },
    )
    workline = SimpleNamespace(runtime_status=WorkLineRuntimeStatus.READY, stopped_at=None, stopped_reason=None)
    session_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=session))
    workline_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=workline))
    outbox_repo = SimpleNamespace(cancel_active_by_session=AsyncMock(return_value=1))
    device_service = SimpleNamespace(mark_callback_deadline_expired=AsyncMock(return_value=None))
    db = _Db(command=command)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        outbox_repository=outbox_repo,
        device_service=device_service,
    )

    with (
        patch(
            "src.app.workline.services.runtime_reconciliation_service.inbox_service.mark_as_processed",
            new=AsyncMock(),
        ) as mark_processed,
        patch(
            "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
            new=AsyncMock(),
        ) as add_timeline,
        patch(
            "src.app.workline.services.runtime_reconciliation_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ) as record_diagnostic,
    ):
        updated = await service.handle_timer_timeout(db, inbox=inbox)

    assert updated is session
    assert session.status == SessionStatus.MANUAL_HOLD
    assert session.reconciliation_state == RuntimeReconciliationState.PENDING
    assert session.reconciliation_reason == RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED
    assert session.reconciliation_source_kind == RuntimeReconciliationSourceKind.TIMER_TIMEOUT
    assert session.reconciliation_source_inbox_id == 88
    assert session.reconciliation_command_id == 9
    assert session.reconciliation_device_id == 7
    assert session.reconciliation_wait_token == "CMD-001"
    assert session.reconciliation_ack_received_at == ack_received_at
    assert session.reconciliation_deadline_at == deadline_at
    assert session.current_wait_type is None
    assert session.current_wait_token is None
    assert session.current_wait_timeout_seconds is None
    assert session.deadline_at is None
    assert session.awaiting_command_id is None
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert workline.stopped_reason == RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value
    device_service.mark_callback_deadline_expired.assert_awaited_once_with(db, device_id=7, auto_commit=False)
    outbox_repo.cancel_active_by_session.assert_awaited_once_with(
        db,
        session_id=530,
        reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
    )
    mark_processed.assert_awaited_once_with(db, 88, auto_commit=False)
    add_timeline.assert_awaited_once()
    record_diagnostic.assert_awaited_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_timer_timeout_uses_payload_claim_when_live_wait_fields_were_cleared() -> None:
    deadline_at = timezone.now_for_db() - timedelta(seconds=10)
    ack_received_at = deadline_at - timedelta(seconds=300)
    command = SimpleNamespace(
        id=9,
        device_id=7,
        status=CommandStatus.ACK_RECEIVED,
        ack_received_at=ack_received_at,
    )
    session = SimpleNamespace(
        id=545,
        workline_id=45,
        trace_id="sandbox:trace-001",
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_token=None,
        current_wait_type=None,
        current_wait_timeout_seconds=300,
        waiting_since=None,
        deadline_at=None,
        awaiting_command_id=None,
        reconciliation_state=None,
    )
    inbox = SimpleNamespace(
        id=85599,
        session_id=545,
        payload_json={
            "deadline_at": deadline_at.isoformat(),
            "wait_token": "CMD-20260508-MEASUREMENT_REEL-0EF06E0F",
            "awaiting_command_id": 9,
            "ack_received_at": ack_received_at.isoformat(),
        },
    )
    workline = SimpleNamespace(runtime_status=WorkLineRuntimeStatus.READY, stopped_at=None, stopped_reason=None)
    session_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=session))
    workline_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=workline))
    outbox_repo = SimpleNamespace(cancel_active_by_session=AsyncMock(return_value=1))
    device_service = SimpleNamespace(mark_callback_deadline_expired=AsyncMock(return_value=None))
    db = _Db(command=command)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        outbox_repository=outbox_repo,
        device_service=device_service,
    )

    with (
        patch(
            "src.app.workline.services.runtime_reconciliation_service.inbox_service.mark_as_processed",
            new=AsyncMock(),
        ) as mark_processed,
        patch(
            "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
            new=AsyncMock(),
        ) as add_timeline,
        patch(
            "src.app.workline.services.runtime_reconciliation_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ) as record_diagnostic,
    ):
        updated = await service.handle_timer_timeout(db, inbox=inbox)

    assert updated is session
    assert session.status == SessionStatus.MANUAL_HOLD
    assert session.reconciliation_state == RuntimeReconciliationState.PENDING
    assert session.reconciliation_source_inbox_id == 85599
    assert session.reconciliation_command_id == 9
    assert session.reconciliation_wait_token == "CMD-20260508-MEASUREMENT_REEL-0EF06E0F"
    assert session.reconciliation_ack_received_at == ack_received_at
    assert session.reconciliation_deadline_at == deadline_at
    assert session.awaiting_command_id is None
    assert session.deadline_at is None
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    outbox_repo.cancel_active_by_session.assert_awaited_once_with(
        db,
        session_id=545,
        reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED.value,
    )
    mark_processed.assert_awaited_once_with(db, 85599, auto_commit=False)
    add_timeline.assert_awaited_once()
    record_diagnostic.assert_awaited_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_wait_timeout_enters_runtime_reconciliation_without_command() -> None:
    deadline_at = timezone.now_for_db() - timedelta(seconds=10)
    session = SimpleNamespace(
        id=546,
        workline_id=45,
        trace_id="external:trace-001",
        status=SessionStatus.WAITING_EXTERNAL,
        current_wait_token="agv:task:001",
        current_wait_type="EXTERNAL_HTTP",
        current_wait_timeout_seconds=300,
        waiting_since=deadline_at - timedelta(seconds=300),
        deadline_at=deadline_at,
        awaiting_command_id=None,
        reconciliation_state=None,
    )
    inbox = SimpleNamespace(
        id=85600,
        session_id=546,
        payload_json={
            "deadline_at": deadline_at.isoformat(),
            "wait_token": "agv:task:001",
            "wait_type": "EXTERNAL_HTTP",
            "awaiting_command_id": None,
        },
    )
    workline = SimpleNamespace(runtime_status=WorkLineRuntimeStatus.READY, stopped_at=None, stopped_reason=None)
    session_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=session))
    workline_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=workline))
    outbox_repo = SimpleNamespace(cancel_active_by_session=AsyncMock(return_value=1))
    device_service = SimpleNamespace(mark_callback_deadline_expired=AsyncMock(return_value=None))
    db = _Db(command=None)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        outbox_repository=outbox_repo,
        device_service=device_service,
    )

    with (
        patch(
            "src.app.workline.services.runtime_reconciliation_service.inbox_service.mark_as_processed",
            new=AsyncMock(),
        ) as mark_processed,
        patch(
            "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
            new=AsyncMock(),
        ) as add_timeline,
        patch(
            "src.app.workline.services.runtime_reconciliation_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ) as record_diagnostic,
    ):
        updated = await service.handle_timer_timeout(db, inbox=inbox)

    assert updated is session
    assert session.status == SessionStatus.MANUAL_HOLD
    assert session.reconciliation_state == RuntimeReconciliationState.PENDING
    assert session.reconciliation_source_inbox_id == 85600
    assert session.reconciliation_command_id is None
    assert session.reconciliation_device_id is None
    assert session.reconciliation_wait_token == "agv:task:001"
    assert session.reconciliation_deadline_at == deadline_at
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    device_service.mark_callback_deadline_expired.assert_not_awaited()
    mark_processed.assert_awaited_once_with(db, 85600, auto_commit=False)
    add_timeline.assert_awaited_once()
    record_diagnostic.assert_awaited_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_callback_evidence_is_idempotent_by_event_id() -> None:
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        device_id=7,
        status=CommandStatus.ACK_RECEIVED,
    )
    original_context: dict[str, object] = {}
    session = SimpleNamespace(
        id=545,
        workline_id=45,
        trace_id="sandbox:trace-001",
        reconciliation_reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED,
        reconciliation_late_evidence_received=False,
        context_json=original_context,
    )
    session_repo = SimpleNamespace(get_pending_reconciliation_by_command_id=AsyncMock(return_value=session))
    db = _Db()
    service = WorklineRuntimeReconciliationService(session_repository=session_repo)
    callback_payload = {
        "event_id": "callback-event-001",
        "command_code": "CMD-001",
        "result": "SUCCESS",
        "finish_time": "2026-05-08T09:00:00",
    }

    with patch(
        "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
        new=AsyncMock(),
    ) as add_timeline:
        first = await service.record_late_callback_if_pending(
            db,
            command=command,
            callback_payload=callback_payload,
        )
        second = await service.record_late_callback_if_pending(
            db,
            command=command,
            callback_payload=dict(callback_payload),
        )

    evidence = session.context_json["runtime_reconciliation_late_callback_evidence"]
    assert first is True
    assert second is True
    assert len(evidence) == 1
    assert evidence[0]["evidence_key"] == "event_id:callback-event-001"
    assert session.context_json is not original_context
    assert session.reconciliation_late_evidence_received is True
    add_timeline.assert_awaited_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_callback_after_dispatch_ack_exhausted_records_evidence() -> None:
    command = SimpleNamespace(
        id=10,
        command_code="CMD-ACK-EXHAUSTED",
        device_id=7,
        status=CommandStatus.FAILED,
    )
    session = SimpleNamespace(
        id=546,
        workline_id=45,
        trace_id="dispatch:trace-001",
        reconciliation_reason=RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED,
        reconciliation_late_evidence_received=False,
        context_json={},
    )
    session_repo = SimpleNamespace(get_pending_reconciliation_by_command_id=AsyncMock(return_value=session))
    db = _Db()
    service = WorklineRuntimeReconciliationService(session_repository=session_repo)
    callback_payload = {
        "event_id": "callback-after-ack-exhausted-001",
        "command_code": "CMD-ACK-EXHAUSTED",
        "result": "SUCCESS",
        "finish_time": "2026-05-08T09:00:00",
    }

    with patch(
        "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
        new=AsyncMock(),
    ) as add_timeline:
        recorded = await service.record_late_callback_if_pending(
            db,
            command=command,
            callback_payload=callback_payload,
        )

    evidence = session.context_json["runtime_reconciliation_late_callback_evidence"]
    assert recorded is True
    assert len(evidence) == 1
    assert evidence[0]["evidence_key"] == "event_id:callback-after-ack-exhausted-001"
    assert session.reconciliation_late_evidence_received is True
    add_timeline.assert_awaited_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_reconciliation_resolve_writes_operator_timeline() -> None:
    from src.app.workline.models.session import RuntimeReconciliationResolution

    session = SimpleNamespace(
        id=530,
        workline_id=45,
        status=SessionStatus.MANUAL_HOLD,
        reconciliation_state=RuntimeReconciliationState.PENDING,
        reconciliation_reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED,
        reconciliation_device_id=7,
        reconciliation_command_id=None,
        context_json={},
    )
    workline = SimpleNamespace(runtime_status=WorkLineRuntimeStatus.RECONCILING, resumed_at=None, stopped_reason=None)
    session_repo = SimpleNamespace(
        get_for_update=AsyncMock(return_value=session),
        count_pending_reconciliations_for_workline=AsyncMock(return_value=0),
    )
    workline_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=workline))
    outbox_repo = SimpleNamespace(release_blocked_by_reconciliation_session=AsyncMock(return_value=2))
    device_service = SimpleNamespace(clear_reconciliation_error=AsyncMock(return_value=None))
    db = _Db()
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        outbox_repository=outbox_repo,
        device_service=device_service,
    )

    with patch(
        "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
        new=AsyncMock(),
    ) as add_timeline:
        result = await service.resolve_runtime_reconciliation(
            db,
            session_id=530,
            resolution=RuntimeReconciliationResolution.COMPLETED,
            checks={
                "device_inspected": True,
                "physical_state_confirmed": True,
                "inventory_or_position_reconciled": True,
                "late_callback_reviewed": True,
            },
            operator_note="现场确认已完成",
            confirmed_at=datetime(2026, 5, 8, 9, 0, 0),
            operator_id=88,
        )

    assert result["released_outbox_count"] == 2
    assert session.context_json["runtime_reconciliation_resolution"]["operator_id"] == 88
    add_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_reconciliation_resolve_normalizes_aware_confirmed_at() -> None:
    from datetime import UTC

    from src.app.workline.models.session import RuntimeReconciliationResolution

    aware_confirmed_at = datetime(2026, 5, 8, 9, 0, 0, tzinfo=UTC)
    expected_confirmed_at = datetime(2026, 5, 8, 9, 0, 0)
    session = SimpleNamespace(
        id=530,
        workline_id=45,
        status=SessionStatus.MANUAL_HOLD,
        reconciliation_state=RuntimeReconciliationState.PENDING,
        reconciliation_reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED,
        reconciliation_device_id=None,
        reconciliation_command_id=9,
        context_json={},
    )
    command = SimpleNamespace(id=9, completed_at=None, status=None, result=None, result_data=None, error_detail=None)
    workline = SimpleNamespace(runtime_status=WorkLineRuntimeStatus.RECONCILING, resumed_at=None, stopped_reason=None)
    session_repo = SimpleNamespace(
        get_for_update=AsyncMock(return_value=session),
        count_pending_reconciliations_for_workline=AsyncMock(return_value=0),
    )
    workline_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=workline))
    outbox_repo = SimpleNamespace(release_blocked_by_reconciliation_session=AsyncMock(return_value=0))
    device_service = SimpleNamespace(clear_reconciliation_error=AsyncMock(return_value=None))
    db = _Db(command=command)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        outbox_repository=outbox_repo,
        device_service=device_service,
    )

    with patch(
        "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
        new=AsyncMock(),
    ):
        await service.resolve_runtime_reconciliation(
            db,
            session_id=530,
            resolution=RuntimeReconciliationResolution.COMPLETED,
            checks={
                "device_inspected": True,
                "physical_state_confirmed": True,
                "inventory_or_position_reconciled": True,
                "late_callback_reviewed": True,
            },
            operator_note="现场确认已完成",
            confirmed_at=aware_confirmed_at,
            operator_id=88,
        )

    assert session.ended_at == expected_confirmed_at
    assert command.completed_at == expected_confirmed_at
    assert (
        session.context_json["runtime_reconciliation_resolution"]["confirmed_at"] == expected_confirmed_at.isoformat()
    )
