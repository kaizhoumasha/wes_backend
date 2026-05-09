from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.app.device.models.command import CommandStatus
from src.app.workline.models.outbox import OutboxStatus
from src.app.workline.models.runtime_hold import RuntimeHoldType
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
    runtime_hold_creation_service = SimpleNamespace(
        create_for_callback_deadline_expired=AsyncMock(return_value=SimpleNamespace(id=9901))
    )
    db = _Db(command=command)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        outbox_repository=outbox_repo,
        device_service=device_service,
        runtime_hold_creation_service=runtime_hold_creation_service,
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
    runtime_hold_creation_service.create_for_callback_deadline_expired.assert_awaited_once_with(
        db,
        session=session,
        inbox=inbox,
        command=command,
    )
    mark_processed.assert_awaited_once_with(db, 88, auto_commit=False)
    add_timeline.assert_awaited_once()
    timeline = add_timeline.await_args.args[1]
    assert timeline.payload_json["runtime_hold_id"] == 9901
    record_diagnostic.assert_awaited_once()
    assert record_diagnostic.await_args.kwargs["evidence"]["runtime_hold_id"] == 9901
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
    runtime_hold_creation_service = SimpleNamespace(
        create_for_callback_deadline_expired=AsyncMock(return_value=SimpleNamespace(id=9902))
    )
    db = _Db(command=command)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        outbox_repository=outbox_repo,
        device_service=device_service,
        runtime_hold_creation_service=runtime_hold_creation_service,
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
    runtime_hold_creation_service.create_for_callback_deadline_expired.assert_awaited_once_with(
        db,
        session=session,
        inbox=inbox,
        command=command,
    )
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
    runtime_hold_creation_service = SimpleNamespace(
        create_for_callback_deadline_expired=AsyncMock(return_value=SimpleNamespace(id=9903))
    )
    db = _Db(command=None)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        outbox_repository=outbox_repo,
        device_service=device_service,
        runtime_hold_creation_service=runtime_hold_creation_service,
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
    runtime_hold_creation_service.create_for_callback_deadline_expired.assert_awaited_once_with(
        db,
        session=session,
        inbox=inbox,
        command=None,
    )
    add_timeline.assert_awaited_once()
    record_diagnostic.assert_awaited_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_ack_exhausted_marks_sent_outbox_and_command_failed() -> None:
    command = SimpleNamespace(
        id=881,
        command_code="CMD-20260509-MOVE_FORWARD-AB5F1A76",
        device_id=7,
        status=CommandStatus.SENT,
        completed_at=None,
        error_detail=None,
    )
    outbox = SimpleNamespace(
        id=862,
        session_id=553,
        workline_id=45,
        target_code="CONVEYOR01",
        status=OutboxStatus.SENT,
        last_error=None,
        next_retry_at=timezone.now_for_db() + timedelta(seconds=30),
        finished_at=None,
    )
    session = SimpleNamespace(
        id=553,
        workline_id=45,
        trace_id="sandbox:trace-ack-timeout",
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_token="CMD-20260509-MOVE_FORWARD-AB5F1A76",
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=300,
        waiting_since=timezone.now_for_db() - timedelta(seconds=400),
        deadline_at=None,
        awaiting_command_id=881,
        reconciliation_state=None,
    )
    workline = SimpleNamespace(runtime_status=WorkLineRuntimeStatus.READY, stopped_at=None, stopped_reason=None)
    session_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=session))
    workline_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=workline))
    device_service = SimpleNamespace(mark_dispatch_ack_exhausted=AsyncMock(return_value=None))
    runtime_hold_creation_service = SimpleNamespace(
        create_for_dispatch_ack_exhausted=AsyncMock(return_value=SimpleNamespace(id=9904))
    )
    db = _Db(command=command)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        device_service=device_service,
        runtime_hold_creation_service=runtime_hold_creation_service,
    )

    with (
        patch(
            "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
            new=AsyncMock(),
        ) as add_timeline,
        patch(
            "src.app.workline.services.runtime_reconciliation_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ) as record_diagnostic,
    ):
        updated = await service.handle_dispatch_ack_exhausted(
            db,
            outbox=outbox,
            command=command,
            error_message="COMMAND_ACK_TIMEOUT",
        )

    assert updated is session
    assert outbox.status == OutboxStatus.FAILED
    assert outbox.last_error == "COMMAND_ACK_TIMEOUT"
    assert outbox.next_retry_at is None
    assert outbox.finished_at is not None
    assert command.status == CommandStatus.FAILED
    assert command.completed_at is not None
    assert command.error_detail == {
        "error_code": RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value,
        "error_message": "COMMAND_ACK_TIMEOUT",
        "outbox_id": 862,
    }
    assert session.status == SessionStatus.MANUAL_HOLD
    assert session.reconciliation_state == RuntimeReconciliationState.PENDING
    assert session.reconciliation_reason == RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED
    assert session.reconciliation_source_kind == RuntimeReconciliationSourceKind.DISPATCH_ACK_EXHAUSTED
    assert session.reconciliation_source_outbox_id == 862
    assert session.reconciliation_command_id == 881
    assert session.reconciliation_device_id == 7
    assert session.reconciliation_wait_token == "CMD-20260509-MOVE_FORWARD-AB5F1A76"
    assert session.current_wait_type is None
    assert session.current_wait_token is None
    assert session.awaiting_command_id is None
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert workline.stopped_reason == RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value
    device_service.mark_dispatch_ack_exhausted.assert_awaited_once_with(db, device_id=7, auto_commit=False)
    runtime_hold_creation_service.create_for_dispatch_ack_exhausted.assert_awaited_once_with(
        db,
        session=session,
        outbox=outbox,
        command=command,
        source_reason=RuntimeReconciliationReason.COMMAND_ACK_EXHAUSTED.value,
    )
    add_timeline.assert_awaited_once()
    timeline = add_timeline.await_args.args[1]
    assert timeline.payload_json["runtime_hold_id"] == 9904
    record_diagnostic.assert_awaited_once()
    assert record_diagnostic.await_args.kwargs["evidence"]["runtime_hold_id"] == 9904
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_ack_exhausted_preserves_outbox_dispatch_failed_source_reason() -> None:
    command = SimpleNamespace(
        id=882,
        command_code="CMD-OUTBOX-DISPATCH-FAILED",
        device_id=7,
        status=CommandStatus.PENDING,
        completed_at=None,
        error_detail=None,
    )
    outbox = SimpleNamespace(
        id=863,
        session_id=554,
        workline_id=45,
        status=OutboxStatus.NEW,
        last_error=None,
        next_retry_at=timezone.now_for_db() + timedelta(seconds=30),
        finished_at=None,
    )
    session = SimpleNamespace(
        id=554,
        workline_id=45,
        trace_id="sandbox:trace-outbox-failed",
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_token="CMD-OUTBOX-DISPATCH-FAILED",
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=300,
        waiting_since=timezone.now_for_db() - timedelta(seconds=400),
        deadline_at=None,
        awaiting_command_id=882,
        reconciliation_state=None,
    )
    workline = SimpleNamespace(runtime_status=WorkLineRuntimeStatus.READY, stopped_at=None, stopped_reason=None)
    session_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=session))
    workline_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=workline))
    device_service = SimpleNamespace(mark_dispatch_ack_exhausted=AsyncMock(return_value=None))
    runtime_hold_creation_service = SimpleNamespace(
        create_for_dispatch_ack_exhausted=AsyncMock(return_value=SimpleNamespace(id=9905))
    )
    db = _Db(command=command)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        device_service=device_service,
        runtime_hold_creation_service=runtime_hold_creation_service,
    )

    with (
        patch(
            "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
            new=AsyncMock(),
        ),
        patch(
            "src.app.workline.services.runtime_reconciliation_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
    ):
        await service.handle_dispatch_ack_exhausted(
            db,
            outbox=outbox,
            command=command,
            error_message=RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED.value,
        )

    runtime_hold_creation_service.create_for_dispatch_ack_exhausted.assert_awaited_once_with(
        db,
        session=session,
        outbox=outbox,
        command=command,
        source_reason=RuntimeReconciliationReason.OUTBOX_DISPATCH_FAILED.value,
    )


@pytest.mark.asyncio
async def test_park_outbox_for_reconciliation_uses_runtime_hold_owner() -> None:
    owner = SimpleNamespace(id=530, reconciliation_device_id=7)
    hold = SimpleNamespace(id=9901, session_id=530, hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION)
    outbox = SimpleNamespace(id=88, workline_id=45)
    session_repo = SimpleNamespace(get_pending_reconciliation_owner_for_workline=AsyncMock(return_value=owner))
    runtime_hold_repo = SimpleNamespace(get_active_blocking_by_workline=AsyncMock(return_value=[hold]))
    outbox_repo = SimpleNamespace(
        block_by_runtime_hold=AsyncMock(return_value=outbox),
        mark_as_blocked_by_workline_state=AsyncMock(return_value=None),
    )
    db = _Db()
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        runtime_hold_repository=runtime_hold_repo,
        outbox_repository=outbox_repo,
    )

    updated = await service.park_outbox_for_reconciliation(
        db,
        outbox=outbox,
        reason="CALLBACK_DEADLINE_EXPIRED",
    )

    assert updated is outbox
    outbox_repo.block_by_runtime_hold.assert_awaited_once_with(
        db,
        88,
        runtime_hold_id=9901,
        owner_session_id=530,
        reason="CALLBACK_DEADLINE_EXPIRED",
        blocked_device_id=7,
        blocked_workline_id=45,
    )
    outbox_repo.mark_as_blocked_by_workline_state.assert_not_awaited()


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
    hold = SimpleNamespace(id=9901, session_id=530, hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION, version=0)
    session_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=session))
    runtime_hold_repo = SimpleNamespace(get_active_blocking_by_workline=AsyncMock(return_value=[hold]))
    release_service = SimpleNamespace(
        build_latest_evidence_hash=lambda _hold, *, session=None: "hash:latest",
        resolve_hold=AsyncMock(
            return_value={
                "released_outbox_count": 2,
                "remaining_active_blocking_holds": 0,
            }
        ),
    )
    db = _Db()
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        runtime_hold_repository=runtime_hold_repo,
        runtime_hold_release_service=release_service,
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
    release_service.resolve_hold.assert_awaited_once()
    release_request = release_service.resolve_hold.await_args.args[2]
    assert release_request.material_disposition == "CONTINUE"
    assert release_request.latest_evidence_hash == "hash:latest"
    add_timeline.assert_awaited_once()
    timeline = add_timeline.await_args.args[1]
    assert timeline.payload_json["runtime_hold_id"] == 9901


@pytest.mark.asyncio
async def test_runtime_reconciliation_resolve_normalizes_aware_confirmed_at_in_timeline() -> None:
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
    hold = SimpleNamespace(id=9902, session_id=530, hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION, version=0)
    session_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=session))
    runtime_hold_repo = SimpleNamespace(get_active_blocking_by_workline=AsyncMock(return_value=[hold]))
    release_service = SimpleNamespace(
        build_latest_evidence_hash=lambda _hold, *, session=None: "hash:latest",
        resolve_hold=AsyncMock(
            return_value={
                "released_outbox_count": 0,
                "remaining_active_blocking_holds": 0,
            }
        ),
    )
    db = _Db(command=command)
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        runtime_hold_repository=runtime_hold_repo,
        runtime_hold_release_service=release_service,
    )

    with patch(
        "src.app.workline.services.runtime_reconciliation_service.add_timeline_with_sequence",
        new=AsyncMock(),
    ) as add_timeline:
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

    release_service.resolve_hold.assert_awaited_once()
    timeline = add_timeline.await_args.args[1]
    assert timeline.payload_json["confirmed_at"] == expected_confirmed_at.isoformat()
