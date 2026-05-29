from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus
from src.app.workline.models.inbox import InboxKind, SourceSystem
from src.app.workline.models.session import SessionStatus
from src.app.workline.models.workline import WorkLineRunMode


class _InboxRepoStub:
    def __init__(self, original: object | None = None) -> None:
        self.original = original
        self.created: dict[str, Any] | None = None
        self.get_by_id = AsyncMock(return_value=original)
        self.get_by_idempotency_key = AsyncMock(return_value=None)
        self.create = AsyncMock(side_effect=self._create)
        self.create_idempotent = AsyncMock(side_effect=self._create_idempotent)

    async def _create(self, _db: object, data: dict[str, Any]) -> Any:
        self.created = data
        return SimpleNamespace(id=88, **data)

    async def _create_idempotent(self, _db: object, data: dict[str, Any], *, idempotency_key: str) -> Any:
        self.created = data | {"idempotency_key": idempotency_key}
        return SimpleNamespace(id=88, **self.created)

    def calculate_external_http_idempotency_key(
        self,
        *,
        callback_type: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> str:
        source_event_id = payload["source_event_id"]
        return f"external_http:{callback_type}:{trace_id}:source_event:{source_event_id}"


class _SessionRepoStub:
    def __init__(self, session: object | None = None) -> None:
        self.session = session
        self.get_by_id = AsyncMock(return_value=session)


class _SingleItemRepoStub:
    def __init__(self, item: object | None = None) -> None:
        self.item = item
        self.get_by_id = AsyncMock(return_value=item)
        self.get_for_update = AsyncMock(return_value=item)
        self.get_by_device_code = AsyncMock(return_value=item)
        self.get_by_command_code = AsyncMock(return_value=item)


class _OutboxRepoStub:
    def __init__(self, outbox: object | None = None) -> None:
        self.outbox = outbox
        self.get_by_dispatch_key = AsyncMock(return_value=outbox)
        self.get_sandbox_pending_messages = AsyncMock(return_value=[outbox] if outbox is not None else [])
        self.release_blocked_by_device = AsyncMock(return_value=0)


class _RuntimeHoldRepoStub:
    def __init__(self, hold: object | None = None) -> None:
        self.hold = hold
        self.find_latest_for_projection = AsyncMock(return_value=hold)


@pytest.mark.asyncio
async def test_submit_sandbox_event_locks_workline_before_creating_inbox() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    workline = SimpleNamespace(id=42, run_mode=WorkLineRunMode.SIMULATION, is_active=True, plugin_key="rough_sorter")
    workline_repo = _SingleItemRepoStub(workline)
    inbox_repo = _InboxRepoStub()
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        workline_repo=cast("Any", workline_repo),
    )

    db = object()
    inbox = await service.submit_sandbox_event(
        db,
        workline_id=42,
        device_id=7,
        event_type="BARCODE_SCANNED",
        auto_commit=False,
    )

    assert inbox is not None
    workline_repo.get_for_update.assert_awaited_once_with(db, 42)
    workline_repo.get_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_sandbox_event_rejects_inactive_simulation_workline() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    workline = SimpleNamespace(id=42, run_mode=WorkLineRunMode.SIMULATION, is_active=False)
    inbox_repo = _InboxRepoStub()
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with pytest.raises(ValueError, match="未启用"):
        await service.submit_sandbox_event(
            object(),
            workline_id=42,
            device_id=7,
            event_type="BARCODE_SCANNED",
            auto_commit=False,
        )

    inbox_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_clones_original_inbox_for_runtime_processing_and_does_not_mutate_original() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    original_payload = {"message_type": "DEVICE_EVENT", "device_code": "ARM01"}
    original = SimpleNamespace(
        id=10,
        kind=InboxKind.DEVICE_EVENT,
        payload_json=original_payload,
        trace_id="trace-001",
        event_id="event-original",
        causation_id=None,
        workline_id=1,
        device_id=None,
        command_id=None,
        session_id=2,
        source_message_id="req-001",
    )
    inbox_repo = _InboxRepoStub(original)
    session = SimpleNamespace(id=2, reconciliation_state=None)
    workline_repo = _SingleItemRepoStub(SimpleNamespace(id=1, is_active=True))
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
        workline_repo=cast("Any", workline_repo),
    )

    db = object()
    replay = await service.replay_inbox(db, inbox_id=10, reason="重新诊断", operator_id="ops-1", auto_commit=False)

    assert replay.id == 88
    workline_repo.get_for_update.assert_awaited_once_with(db, 1)
    assert inbox_repo.created is not None
    assert inbox_repo.created["kind"] == InboxKind.DEVICE_EVENT
    assert inbox_repo.created["trace_id"] == "trace-001"
    assert inbox_repo.created["event_id"].startswith("replay:event-original:")
    assert inbox_repo.created["causation_id"] == "event-original"
    assert inbox_repo.created["payload_json"]["replay_of_event_id"] == "event-original"
    assert inbox_repo.created["payload_json"]["message_type"] == original_payload["message_type"]
    assert original.payload_json == original_payload


@pytest.mark.asyncio
async def test_replay_inbox_rejects_inactive_workline() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    original = SimpleNamespace(
        id=10,
        kind=InboxKind.DEVICE_EVENT,
        payload_json={"message_type": "DEVICE_EVENT"},
        trace_id="trace-001",
        event_id="event-original",
        causation_id=None,
        workline_id=1,
        device_id=None,
        command_id=None,
        session_id=None,
        source_message_id="req-001",
    )
    inbox_repo = _InboxRepoStub(original)
    workline_repo = _SingleItemRepoStub(SimpleNamespace(id=1, is_active=False))
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        workline_repo=cast("Any", workline_repo),
    )

    db = object()
    with pytest.raises(ValueError, match="未启用"):
        await service.replay_inbox(db, inbox_id=10, reason="重新诊断", operator_id="ops-1", auto_commit=False)

    workline_repo.get_for_update.assert_awaited_once_with(db, 1)
    inbox_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_operation_requires_open_session_state() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    session = SimpleNamespace(id=20, status=SessionStatus.COMPLETED, workline_id=1, trace_id="trace-closed")
    service = WorklineOperationService(
        inbox_repo=cast("Any", _InboxRepoStub()),
        session_repo=cast("Any", _SessionRepoStub(session)),
    )

    with pytest.raises(ValueError, match="当前会话状态不允许人工操作"):
        await service.create_manual_operation(
            object(),
            session_id=20,
            operation="HOLD",
            operator_id="ops-1",
            reason="需要检查",
            auto_commit=False,
        )


@pytest.mark.asyncio
async def test_manual_operation_rejects_inactive_workline() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    session = SimpleNamespace(
        id=20,
        status=SessionStatus.RUNNING,
        workline_id=1,
        trace_id="trace-open",
        reconciliation_state=None,
    )
    inbox_repo = _InboxRepoStub()
    workline_repo = _SingleItemRepoStub(SimpleNamespace(id=1, is_active=False))
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
        workline_repo=cast("Any", workline_repo),
    )

    db = object()
    with pytest.raises(ValueError, match="未启用"):
        await service.create_manual_operation(
            db,
            session_id=20,
            operation="HOLD",
            operator_id="ops-1",
            reason="需要检查",
            auto_commit=False,
        )

    workline_repo.get_for_update.assert_awaited_once_with(db, 1)
    inbox_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_sandbox_external_callback_creates_external_http_inbox_for_pending_outbox() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=28,
        dispatch_key="external:test_workline_plugin:trace-001:RACK_OPERATION",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        status=SystemOutboxStatus.NEW,
        sent_at=None,
        next_retry_at="retry-later",
        last_error="previous dispatch wait",
        session_id=530,
        workline_id=45,
        payload_json={"resume_callback_type": "WMS_RACK_ARRIVED", "trace_id": "trace-001"},
    )
    session = SimpleNamespace(
        id=530,
        status=SessionStatus.WAITING_EXTERNAL,
        current_wait_type="EXTERNAL_HTTP",
        workline_id=45,
        trace_id="trace-001",
    )
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    callback_payload = {
        "active_bin_rack": {
            "rack_id": "RACK-001",
            "rack_code": "RACK-001",
            "cells": [
                {
                    "rack_id": "RACK-001",
                    "rack_slot_code": "A",
                    "rack_slot_location_code": "RACK-001-1A-0",
                    "bin_id": "BIN-001",
                    "bin_orientation_code": "BIN-001-A",
                    "bin_type": "6格箱",
                    "bin_cell_location": "BIN-001-1",
                    "status": "EMPTY",
                }
            ],
        }
    }
    inbox_repo = _InboxRepoStub()
    rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock(return_value=None))
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        outbox_repo=cast("Any", _OutboxRepoStub(outbox)),
        session_repo=cast("Any", _SessionRepoStub(session)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
        rack_task_lifecycle_service=cast("Any", rack_task_service),
    )

    inbox = await service.submit_sandbox_external_callback(
        object(),
        dispatch_key="external:test_workline_plugin:trace-001:RACK_OPERATION",
        payload=callback_payload,
        source_event_id="wms-event-001",
        request_id="rack-request-001",
        auto_commit=False,
    )

    assert inbox.id == 88
    assert inbox_repo.created is not None
    assert inbox_repo.created["kind"] == InboxKind.EXTERNAL_HTTP
    assert inbox_repo.created["source_system"] == SourceSystem.MANUAL
    assert inbox_repo.created["session_id"] == 530
    assert inbox_repo.created["workline_id"] == 45
    assert inbox_repo.created["trace_id"] == "trace-001"
    assert inbox_repo.created["event_id"] == "wms-event-001"
    assert inbox_repo.created["source_message_id"] == "rack-request-001"
    created_payload = inbox_repo.created["payload_json"]
    assert created_payload["message_type"] == "EXTERNAL_HTTP"
    assert created_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert created_payload["trace_id"] == "trace-001"
    assert created_payload["dispatch_key"] == "external:test_workline_plugin:trace-001:RACK_OPERATION"
    assert created_payload["source_system"] == "WMS"
    assert created_payload["source_event_id"] == "wms-event-001"
    assert created_payload["source_version"] == "1"
    assert created_payload["request_id"] == "rack-request-001"
    assert created_payload["signature"] == "sandbox"
    assert created_payload["sandbox_mode"] is True
    assert created_payload["active_bin_rack"] == callback_payload["active_bin_rack"]
    assert "message_type" not in callback_payload
    assert outbox.status == SystemOutboxStatus.SENT
    assert outbox.sent_at is not None
    assert outbox.next_retry_at is None
    assert outbox.last_error is None
    rack_task_service.record_callback_from_external_http.assert_awaited_once()
    callback_kwargs = rack_task_service.record_callback_from_external_http.await_args.kwargs
    assert callback_kwargs["payload_json"]["dispatch_key"] == "external:test_workline_plugin:trace-001:RACK_OPERATION"
    assert callback_kwargs["payload_json"]["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_kwargs["payload_json"]["active_bin_rack"] == callback_payload["active_bin_rack"]
    assert callback_kwargs["trace_id"] == "trace-001"


@pytest.mark.asyncio
async def test_sandbox_external_callback_accepts_rack_operation_wait() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=282,
        dispatch_key="rack-operation:external:test_workline_plugin:trace-001:RACK_OPERATION:2:ALLOCATE_AND_MOVE_RACK",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        status=SystemOutboxStatus.SENT,
        sent_at=None,
        next_retry_at=None,
        last_error=None,
        session_id=530,
        workline_id=45,
        payload_json={
            "operation_key": "external:test_workline_plugin:trace-001:RACK_OPERATION",
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "trace_id": "trace-001",
            "dispatch_key": "rack-operation:external:test_workline_plugin:trace-001:RACK_OPERATION:2:ALLOCATE_AND_MOVE_RACK",
        },
    )
    session = SimpleNamespace(
        id=530,
        status=SessionStatus.WAITING_EXTERNAL,
        current_wait_type="RACK_OPERATION",
        workline_id=45,
        trace_id="trace-001",
        context_json={"rack_operation": {"resume_callback_type": "WMS_RACK_ARRIVED"}},
    )
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    inbox_repo = _InboxRepoStub()
    rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock(return_value=None))
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        outbox_repo=cast("Any", _OutboxRepoStub(outbox)),
        session_repo=cast("Any", _SessionRepoStub(session)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
        rack_task_lifecycle_service=cast("Any", rack_task_service),
    )

    inbox = await service.submit_sandbox_external_callback(
        object(),
        dispatch_key=outbox.dispatch_key,
        auto_commit=False,
    )

    assert inbox.id == 88
    assert inbox_repo.created is not None
    assert inbox_repo.created["kind"] == InboxKind.EXTERNAL_HTTP
    assert inbox_repo.created["session_id"] == 530
    assert inbox_repo.created["payload_json"]["callback_type"] == "WMS_RACK_ARRIVED"
    rack_task_service.record_callback_from_external_http.assert_awaited_once()


@pytest.mark.asyncio
async def test_sandbox_external_callback_rejects_device_outbox() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        status=SystemOutboxStatus.SENT,
        session_id=530,
        workline_id=45,
        payload_json={"command_code": "CMD-001"},
    )
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    service = WorklineOperationService(
        outbox_repo=cast("Any", _OutboxRepoStub(outbox)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with pytest.raises(ValueError, match="仅允许 EXTERNAL_HTTP"):
        await service.submit_sandbox_external_callback(
            object(),
            dispatch_key="device-command:CMD-001",
            auto_commit=False,
        )


@pytest.mark.asyncio
async def test_sandbox_external_callback_requires_waiting_external_session() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=28,
        dispatch_key="external:test_workline_plugin:trace-001:RACK_OPERATION",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        status=SystemOutboxStatus.SENT,
        session_id=530,
        workline_id=45,
        payload_json={"resume_callback_type": "WMS_RACK_ARRIVED", "trace_id": "trace-001"},
    )
    session = SimpleNamespace(
        id=530,
        status=SessionStatus.RUNNING,
        current_wait_type=None,
        workline_id=45,
        trace_id="trace-001",
    )
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    service = WorklineOperationService(
        outbox_repo=cast("Any", _OutboxRepoStub(outbox)),
        session_repo=cast("Any", _SessionRepoStub(session)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with pytest.raises(ValueError, match="当前会话状态不允许模拟外部回调"):
        await service.submit_sandbox_external_callback(
            object(),
            dispatch_key="external:test_workline_plugin:trace-001:RACK_OPERATION",
            auto_commit=False,
        )


@pytest.mark.asyncio
async def test_sandbox_ack_rejects_outbox_when_session_is_not_waiting_for_device_result() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        status=SystemOutboxStatus.SENT,
        sent_at=None,
        next_retry_at=None,
        last_error=None,
        session_id=530,
        workline_id=45,
        payload_json={"command_code": "CMD-001"},
    )
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        session_id_int=530,
    )
    session = SimpleNamespace(id=530, status=SessionStatus.FAILED, awaiting_command_id=9)
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    outbox_repo = _OutboxRepoStub(outbox)
    service = WorklineOperationService(
        outbox_repo=cast("Any", outbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with pytest.raises(ValueError, match="当前会话状态不允许模拟 ACK"):
        await service.submit_sandbox_ack(
            object(),
            dispatch_key="device-command:CMD-001",
            auto_commit=False,
        )


@pytest.mark.asyncio
async def test_sandbox_ack_requires_current_awaiting_command() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        status=SystemOutboxStatus.SENT,
        sent_at=None,
        next_retry_at=None,
        last_error=None,
        session_id=530,
        workline_id=45,
        payload_json={"command_code": "CMD-001"},
    )
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        session_id_int=530,
    )
    session = SimpleNamespace(id=530, status=SessionStatus.WAITING_DEVICE_RESULT, awaiting_command_id=10)
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    outbox_repo = _OutboxRepoStub(outbox)
    service = WorklineOperationService(
        outbox_repo=cast("Any", outbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with pytest.raises(ValueError, match="当前会话等待的 Command 不匹配"):
        await service.submit_sandbox_ack(
            object(),
            dispatch_key="device-command:CMD-001",
            auto_commit=False,
        )

    outbox_repo.get_by_dispatch_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_sandbox_ack_rejects_inactive_simulation_workline() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        status=SystemOutboxStatus.SENT,
        sent_at=None,
        next_retry_at=None,
        last_error=None,
        session_id=530,
        workline_id=45,
        payload_json={"command_code": "CMD-001"},
    )
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=False)
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        session_id_int=530,
        sent_at=None,
        ack_received_at=None,
        ack_code=None,
        ack_message=None,
    )
    session = SimpleNamespace(id=530, status=SessionStatus.WAITING_DEVICE_RESULT, awaiting_command_id=9)
    service = WorklineOperationService(
        outbox_repo=cast("Any", _OutboxRepoStub(outbox)),
        session_repo=cast("Any", _SessionRepoStub(session)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with (
        patch(
            "src.app.workline.services.runtime_reconciliation_service."
            "workline_runtime_reconciliation_service.activate_execution_deadline_after_ack",
            new=AsyncMock(return_value=session),
        ),
        pytest.raises(ValueError, match="未启用"),
    ):
        await service.submit_sandbox_ack(
            object(),
            dispatch_key="device-command:CMD-001",
            auto_commit=False,
        )


@pytest.mark.asyncio
async def test_sandbox_ack_marks_command_ack_and_keeps_outbox_sent() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        status=SystemOutboxStatus.SENT,
        sent_at=None,
        next_retry_at=None,
        last_error=None,
        session_id=530,
        workline_id=45,
        payload_json={"command_code": "CMD-001"},
    )
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        session_id_int=530,
        sent_at=None,
        ack_received_at=None,
        ack_code=None,
        ack_message=None,
    )
    session = SimpleNamespace(id=530, status=SessionStatus.WAITING_DEVICE_RESULT, awaiting_command_id=9)
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    outbox_repo = _OutboxRepoStub(outbox)
    service = WorklineOperationService(
        outbox_repo=cast("Any", outbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with patch(
        "src.app.workline.services.runtime_reconciliation_service."
        "workline_runtime_reconciliation_service.activate_execution_deadline_after_ack",
        new=AsyncMock(return_value=session),
    ) as activate_deadline:
        returned = await service.submit_sandbox_ack(
            object(),
            dispatch_key="device-command:CMD-001",
            auto_commit=False,
        )

    assert returned is outbox
    assert outbox.status == SystemOutboxStatus.SENT
    assert outbox.sent_at is command.ack_received_at
    assert outbox.next_retry_at is None
    assert outbox.last_error is None
    assert command.status == CommandStatus.ACK_RECEIVED
    assert command.sent_at is command.ack_received_at
    assert command.ack_code == 200
    assert command.ack_message == "SANDBOX_ACK"
    activate_deadline.assert_awaited_once()


@pytest.mark.asyncio
async def test_sandbox_ack_accepts_new_outbox_and_marks_it_sent() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        status=SystemOutboxStatus.NEW,
        sent_at=None,
        next_retry_at="retry-later",
        last_error="previous dispatch wait",
        session_id=530,
        workline_id=45,
        payload_json={"command_code": "CMD-001"},
    )
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        session_id_int=530,
        sent_at=None,
        ack_received_at=None,
        ack_code=None,
        ack_message=None,
    )
    session = SimpleNamespace(id=530, status=SessionStatus.WAITING_DEVICE_RESULT, awaiting_command_id=9)
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    service = WorklineOperationService(
        outbox_repo=cast("Any", _OutboxRepoStub(outbox)),
        session_repo=cast("Any", _SessionRepoStub(session)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with patch(
        "src.app.workline.services.runtime_reconciliation_service."
        "workline_runtime_reconciliation_service.activate_execution_deadline_after_ack",
        new=AsyncMock(return_value=session),
    ):
        await service.submit_sandbox_ack(
            object(),
            dispatch_key="device-command:CMD-001",
            auto_commit=False,
        )

    assert outbox.status == SystemOutboxStatus.SENT
    assert outbox.sent_at is command.ack_received_at
    assert outbox.next_retry_at is None
    assert outbox.last_error is None
    assert command.status == CommandStatus.ACK_RECEIVED


@pytest.mark.asyncio
async def test_sandbox_ack_rejects_duplicate_ack_without_resetting_deadline() -> None:
    from datetime import datetime

    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    ack_received_at = datetime(2026, 5, 8, 9, 0, 0)
    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        status=SystemOutboxStatus.SENT,
        session_id=530,
        workline_id=45,
        payload_json={"command_code": "CMD-001"},
    )
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        session_id_int=530,
        status=CommandStatus.ACK_RECEIVED,
        sent_at=ack_received_at,
        ack_received_at=ack_received_at,
        ack_code=200,
        ack_message="SANDBOX_ACK",
    )
    session = SimpleNamespace(id=530, status=SessionStatus.WAITING_DEVICE_RESULT, awaiting_command_id=9)
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    service = WorklineOperationService(
        outbox_repo=cast("Any", _OutboxRepoStub(outbox)),
        session_repo=cast("Any", _SessionRepoStub(session)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with patch(
        "src.app.workline.services.runtime_reconciliation_service."
        "workline_runtime_reconciliation_service.activate_execution_deadline_after_ack",
        new=AsyncMock(),
    ) as activate_deadline:
        with pytest.raises(ValueError, match="Command 已 ACK"):
            await service.submit_sandbox_ack(
                object(),
                dispatch_key="device-command:CMD-001",
                auto_commit=False,
            )

    assert command.ack_received_at == ack_received_at
    activate_deadline.assert_not_awaited()


@pytest.mark.asyncio
async def test_sandbox_pending_projects_ack_received_command_as_acked() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type="DEVICE",
        target_code="ARM01",
        status=SystemOutboxStatus.SENT,
        session_id=530,
        workline_id=45,
        payload_json={"command_code": "CMD-001"},
    )
    command = SimpleNamespace(status=CommandStatus.ACK_RECEIVED)
    outbox_repo = _OutboxRepoStub(outbox)
    service = WorklineOperationService(
        outbox_repo=cast("Any", outbox_repo),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
    )
    db = object()

    result = await service.get_sandbox_pending(db, workline_id=45)

    assert len(result) == 1
    assert result[0].status == "ACKED"
    assert outbox.status == SystemOutboxStatus.SENT
    outbox_repo.get_sandbox_pending_messages.assert_awaited_once_with(
        db,
        limit=50,
        workline_id=45,
        device_id=None,
    )


@pytest.mark.asyncio
async def test_sandbox_pending_projects_waiting_device_command_as_sent_for_ack() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type="DEVICE",
        target_code="ARM01",
        status=SystemOutboxStatus.NEW,
        session_id=530,
        workline_id=45,
        payload_json={"command_code": "CMD-001"},
    )
    command = SimpleNamespace(status=CommandStatus.SENT)
    service = WorklineOperationService(
        outbox_repo=cast("Any", _OutboxRepoStub(outbox)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
    )

    result = await service.get_sandbox_pending(object(), workline_id=45)

    assert result[0].status == "SENT"
    assert outbox.status == SystemOutboxStatus.NEW


@pytest.mark.asyncio
async def test_sandbox_pending_keeps_history_but_only_current_command_actionable() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    old_acked = SimpleNamespace(
        id=856,
        dispatch_key="device-command:CMD-OLD-ACKED",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type="DEVICE",
        target_code="ARM03",
        status=SystemOutboxStatus.SENT,
        session_id=550,
        workline_id=45,
        payload_json={"command_code": "CMD-OLD-ACKED"},
        last_error=None,
    )
    old_completed = SimpleNamespace(
        id=857,
        dispatch_key="device-command:CMD-OLD-COMPLETED",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type="DEVICE",
        target_code="PIPELINE02",
        status=SystemOutboxStatus.SENT,
        session_id=550,
        workline_id=45,
        payload_json={"command_code": "CMD-OLD-COMPLETED"},
        last_error=None,
    )
    current = SimpleNamespace(
        id=860,
        dispatch_key="device-command:CMD-CURRENT",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type="DEVICE",
        target_code="ARM04",
        status=SystemOutboxStatus.SENT,
        session_id=550,
        workline_id=45,
        payload_json={"command_code": "CMD-CURRENT"},
        last_error=None,
    )
    commands = {
        "CMD-OLD-ACKED": SimpleNamespace(id=875, status=CommandStatus.ACK_RECEIVED),
        "CMD-OLD-COMPLETED": SimpleNamespace(id=876, status=CommandStatus.COMPLETED),
        "CMD-CURRENT": SimpleNamespace(id=879, status=CommandStatus.PENDING),
    }
    command_repo = SimpleNamespace(
        get_by_command_code=AsyncMock(side_effect=lambda _db, command_code: commands[command_code])
    )
    session = SimpleNamespace(
        id=550,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        awaiting_command_id=879,
    )
    outbox_repo = _OutboxRepoStub()
    outbox_repo.get_sandbox_pending_messages = AsyncMock(return_value=[old_acked, old_completed, current])
    service = WorklineOperationService(
        outbox_repo=cast("Any", outbox_repo),
        command_repo=cast("Any", command_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
    )

    result = await service.get_sandbox_pending(object(), workline_id=45)

    assert [item.id for item in result] == [856, 857, 860]
    assert [item.status for item in result] == ["ACKED", "COMPLETED", "SENT"]
    assert [item.is_current_action for item in result] == [False, False, True]
    assert [item.dispatch_key for item in result] == [
        "device-command:CMD-OLD-ACKED",
        "device-command:CMD-OLD-COMPLETED",
        "device-command:CMD-CURRENT",
    ]
    assert [item.is_actionable for item in result] == [False, False, True]
    assert [item.history_group_key for item in result] == [
        "session:550",
        "session:550",
        "session:550",
    ]


@pytest.mark.asyncio
async def test_sandbox_pending_marks_completed_external_http_as_history_when_session_moves_on() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=282,
        dispatch_key="rack-operation:external:test_workline_plugin:trace-001:RACK_OPERATION:2:ALLOCATE_AND_MOVE_RACK",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        target_type="HTTP_ENDPOINT",
        target_code="http://host.docker.internal:8010/api/rack-exchange",
        status=SystemOutboxStatus.SENT,
        session_id=550,
        workline_id=45,
        payload_json={
            "operation_key": "external:test_workline_plugin:trace-001:RACK_OPERATION",
            "task_type": "ALLOCATE_AND_MOVE_RACK",
        },
        last_error=None,
    )
    session = SimpleNamespace(
        id=550,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        context_json={
            "rack_operation": {"status": "ARRIVED"},
            "waiting_rack_operation_key": None,
        },
    )
    service = WorklineOperationService(session_repo=cast("Any", _SessionRepoStub(session)))

    result = await service._project_sandbox_pending_outbox(object(), outbox)

    assert result.status == "SENT"
    assert result.is_current_action is False
    assert result.is_actionable is False
    assert result.history_group_key == "session:550"


@pytest.mark.asyncio
async def test_sandbox_failed_projection_exposes_hold_entry_without_action_button() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=901,
        dispatch_key="device-command:CMD-FAILED-HOLD",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type="DEVICE",
        target_code="ARM03",
        status=SystemOutboxStatus.FAILED,
        session_id=770,
        workline_id=45,
        payload_json={"command_code": "CMD-FAILED-HOLD"},
        last_error="COMMAND_ACK_EXHAUSTED",
        blocked_by_runtime_hold_id=None,
    )
    command = SimpleNamespace(
        id=990,
        command_code="CMD-FAILED-HOLD",
        status=CommandStatus.FAILED,
        error_detail={"code": "DEVICE_BUSY", "message": "设备正在执行其他任务"},
    )
    hold = SimpleNamespace(id=8801, source_reason="COMMAND_ACK_EXHAUSTED", status="OPEN")
    service = WorklineOperationService(
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        runtime_hold_repo=cast("Any", _RuntimeHoldRepoStub(hold)),
    )

    result = await service._project_sandbox_pending_outbox(object(), outbox)

    assert result.status == "FAILED"
    assert result.command_status == "FAILED"
    assert result.is_actionable is False
    assert result.runtime_hold_id == 8801
    assert result.failure_summary == {
        "code": "COMMAND_ACK_EXHAUSTED",
        "message": "设备正在执行其他任务",
        "runtime_hold_id": 8801,
    }
    assert result.history_group_key == "session:770"


@pytest.mark.asyncio
async def test_sandbox_completed_history_is_grouped_and_links_runtime_hold() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox_repo = _OutboxRepoStub()
    outbox_repo.get_sandbox_completed_messages = AsyncMock(
        return_value=[
            {
                "history_group_key": "session:770",
                "session": {"id": 770, "failure_code": "DEVICE_BUSY", "failure_message": "设备忙"},
                "outbox_items": [
                    {
                        "id": 901,
                        "session_id": 770,
                        "workline_id": 45,
                        "dispatch_key": "device-command:CMD-FAILED-HOLD",
                        "status": "FAILED",
                        "last_error": "COMMAND_ACK_EXHAUSTED",
                        "payload_json": {"command_code": "CMD-FAILED-HOLD"},
                        "runtime_hold_id": None,
                        "failure_summary": {
                            "code": "DEVICE_BUSY",
                            "message": "设备忙",
                            "runtime_hold_id": None,
                        },
                    }
                ],
            }
        ]
    )
    command = SimpleNamespace(
        id=990,
        device_id=7,
        command_code="CMD-FAILED-HOLD",
        status=CommandStatus.FAILED,
        error_detail={"message": "设备正在执行其他任务"},
    )
    hold = SimpleNamespace(id=8801, source_reason="COMMAND_ACK_EXHAUSTED", status="OPEN")
    service = WorklineOperationService(
        outbox_repo=cast("Any", outbox_repo),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        runtime_hold_repo=cast("Any", _RuntimeHoldRepoStub(hold)),
    )

    groups = await service.get_sandbox_completed(object(), workline_id=45)

    item = groups[0]["outbox_items"][0]
    assert groups[0]["history_group_key"] == "session:770"
    assert item["is_actionable"] is False
    assert item["runtime_hold_id"] == 8801
    assert item["history_group_key"] == "session:770"
    assert item["failure_summary"] == {
        "code": "COMMAND_ACK_EXHAUSTED",
        "message": "设备正在执行其他任务",
        "runtime_hold_id": 8801,
    }


@pytest.mark.asyncio
async def test_sandbox_completed_history_uses_command_status_for_successful_items() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox_repo = _OutboxRepoStub()
    outbox_repo.get_sandbox_completed_messages = AsyncMock(
        return_value=[
            {
                "history_group_key": "session:770",
                "session": {"id": 770, "failure_code": "DEVICE_BUSY", "failure_message": "设备忙"},
                "outbox_items": [
                    {
                        "id": 900,
                        "session_id": 770,
                        "workline_id": 45,
                        "dispatch_key": "device-command:CMD-SUCCESS",
                        "status": "SENT",
                        "last_error": None,
                        "payload_json": {"command_code": "CMD-SUCCESS"},
                        "runtime_hold_id": None,
                        "failure_summary": {
                            "code": "DEVICE_BUSY",
                            "message": "设备忙",
                            "runtime_hold_id": None,
                        },
                    }
                ],
            }
        ]
    )
    command = SimpleNamespace(
        id=990,
        device_id=7,
        command_code="CMD-SUCCESS",
        status=CommandStatus.COMPLETED,
        error_detail=None,
    )
    hold = SimpleNamespace(id=8801, source_reason="COMMAND_ACK_EXHAUSTED", status="OPEN")
    service = WorklineOperationService(
        outbox_repo=cast("Any", outbox_repo),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        runtime_hold_repo=cast("Any", _RuntimeHoldRepoStub(hold)),
    )

    groups = await service.get_sandbox_completed(object(), workline_id=45)

    item = groups[0]["outbox_items"][0]
    assert item["status"] == "COMPLETED"
    assert item["command_status"] == "COMPLETED"
    assert item["runtime_hold_id"] is None
    assert item["failure_summary"] is None


@pytest.mark.asyncio
async def test_sandbox_result_inbox_contains_command_contract_fields_for_runtime_processing() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    input_payload = {"item_id": "ITEM-001"}
    db = object()
    device = SimpleNamespace(id=7, device_code="ARM01")
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        task_type="PICK_AND_PUT",
        workline_id=45,
        session_id=530,
        session_id_int=530,
        device_id=7,
        trace_id="trace-001",
    )
    session = SimpleNamespace(id=530, status=SessionStatus.WAITING_DEVICE_RESULT, awaiting_command_id=9)
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    inbox_repo = _InboxRepoStub()
    outbox_repo = _OutboxRepoStub()
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        outbox_repo=cast("Any", outbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
        device_repo=cast("Any", _SingleItemRepoStub(device)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    mock_device_service = SimpleNamespace(
        mark_command_finished=AsyncMock(return_value=SimpleNamespace(device_status="IDLE", current_command_id=None))
    )
    with patch("src.app.device.services.device_service", mock_device_service):
        inbox = await service.submit_sandbox_result(
            db,
            command_code="CMD-001",
            device_code="ARM01",
            result="SUCCESS",
            payload=input_payload,
            auto_commit=False,
        )

    assert inbox.id == 88
    assert command.status == CommandStatus.COMPLETED
    assert command.result == "SUCCESS"
    assert command.result_data == {"item_id": "ITEM-001"}
    mock_device_service.mark_command_finished.assert_awaited_once()
    outbox_repo.release_blocked_by_device.assert_awaited_once_with(db, device_id=7)
    assert inbox_repo.created is not None
    result_payload = inbox_repo.created["payload_json"]
    assert result_payload["command_code"] == "CMD-001"
    assert result_payload["device_code"] == "ARM01"
    assert result_payload["command_type"] == "PICK_AND_PUT"
    assert result_payload["task_type"] == "PICK_AND_PUT"
    assert result_payload["result"] == "SUCCESS"
    assert result_payload["sandbox_mode"] is True
    assert result_payload["data"] == {"item_id": "ITEM-001"}
    assert "item_id" not in result_payload
    assert inbox_repo.created["session_id"] == 530
    assert input_payload == {"item_id": "ITEM-001"}


@pytest.mark.asyncio
async def test_sandbox_result_rejects_command_when_session_is_waiting_for_another_command() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    device = SimpleNamespace(id=7, device_code="ARM01")
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        task_type="PICK_AND_PUT",
        workline_id=45,
        session_id=530,
        session_id_int=530,
        device_id=7,
        trace_id="trace-001",
    )
    session = SimpleNamespace(id=530, status=SessionStatus.WAITING_DEVICE_RESULT, awaiting_command_id=10)
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    inbox_repo = _InboxRepoStub()
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
        device_repo=cast("Any", _SingleItemRepoStub(device)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with pytest.raises(ValueError, match="当前会话等待的 Command 不匹配"):
        await service.submit_sandbox_result(
            object(),
            command_code="CMD-001",
            device_code="ARM01",
            result="SUCCESS",
            payload={"item_id": "ITEM-001"},
            auto_commit=False,
        )

    assert inbox_repo.created is None


@pytest.mark.asyncio
async def test_sandbox_result_rejects_command_when_session_is_not_waiting_for_device_result() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    device = SimpleNamespace(id=7, device_code="ARM01")
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        task_type="PICK_AND_PUT",
        workline_id=45,
        session_id=530,
        session_id_int=530,
        device_id=7,
        trace_id="trace-001",
    )
    session = SimpleNamespace(id=530, status=SessionStatus.FAILED, awaiting_command_id=9)
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)
    inbox_repo = _InboxRepoStub()
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
        device_repo=cast("Any", _SingleItemRepoStub(device)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    with pytest.raises(ValueError, match="当前会话状态不允许提交 Result"):
        await service.submit_sandbox_result(
            object(),
            command_code="CMD-001",
            device_code="ARM01",
            result="SUCCESS",
            payload={"item_id": "ITEM-001"},
            auto_commit=False,
        )

    assert inbox_repo.created is None
