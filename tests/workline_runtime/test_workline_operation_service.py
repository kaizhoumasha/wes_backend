from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from src.app.workline.models.inbox import InboxKind
from src.app.workline.models.outbox import DispatchType, OutboxStatus
from src.app.workline.models.session import SessionStatus
from src.app.workline.models.workline import WorkLineRunMode


class _InboxRepoStub:
    def __init__(self, original: object | None = None) -> None:
        self.original = original
        self.created: dict[str, Any] | None = None
        self.get_by_id = AsyncMock(return_value=original)
        self.create = AsyncMock(side_effect=self._create)

    async def _create(self, _db: object, data: dict[str, Any]) -> Any:
        self.created = data
        return SimpleNamespace(id=88, **data)


class _SessionRepoStub:
    def __init__(self, session: object | None = None) -> None:
        self.session = session
        self.get_by_id = AsyncMock(return_value=session)


class _SingleItemRepoStub:
    def __init__(self, item: object | None = None) -> None:
        self.item = item
        self.get_by_id = AsyncMock(return_value=item)
        self.get_by_device_code = AsyncMock(return_value=item)
        self.get_by_command_code = AsyncMock(return_value=item)


class _OutboxRepoStub:
    def __init__(self, outbox: object | None = None) -> None:
        self.outbox = outbox
        self.get_by_dispatch_key = AsyncMock(return_value=outbox)
        self.get_sandbox_pending_messages = AsyncMock(return_value=[outbox] if outbox is not None else [])


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
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
    )

    replay = await service.replay_inbox(
        object(), inbox_id=10, reason="重新诊断", operator_id="ops-1", auto_commit=False
    )

    assert replay.id == 88
    assert inbox_repo.created is not None
    assert inbox_repo.created["kind"] == InboxKind.DEVICE_EVENT
    assert inbox_repo.created["trace_id"] == "trace-001"
    assert inbox_repo.created["event_id"].startswith("replay:event-original:")
    assert inbox_repo.created["causation_id"] == "event-original"
    assert inbox_repo.created["payload_json"]["replay_of_event_id"] == "event-original"
    assert inbox_repo.created["payload_json"]["message_type"] == original_payload["message_type"]
    assert original.payload_json == original_payload


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
async def test_sandbox_ack_rejects_outbox_when_session_is_not_waiting_for_device_result() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=DispatchType.DEVICE_COMMAND,
        status=OutboxStatus.SENT,
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
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION)
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
        dispatch_type=DispatchType.DEVICE_COMMAND,
        status=OutboxStatus.SENT,
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
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION)
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
async def test_sandbox_ack_marks_command_ack_and_keeps_outbox_sent() -> None:
    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=DispatchType.DEVICE_COMMAND,
        status=OutboxStatus.SENT,
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
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION)
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
    assert outbox.status == OutboxStatus.SENT
    assert command.status == CommandStatus.ACK_RECEIVED
    assert command.sent_at is command.ack_received_at
    assert command.ack_code == 200
    assert command.ack_message == "SANDBOX_ACK"
    activate_deadline.assert_awaited_once()


@pytest.mark.asyncio
async def test_sandbox_ack_rejects_duplicate_ack_without_resetting_deadline() -> None:
    from datetime import datetime

    from src.app.device.models.command import CommandStatus
    from src.app.workline.services.operation_service import WorklineOperationService

    ack_received_at = datetime(2026, 5, 8, 9, 0, 0)
    outbox = SimpleNamespace(
        id=34,
        dispatch_key="device-command:CMD-001",
        dispatch_type=DispatchType.DEVICE_COMMAND,
        status=OutboxStatus.SENT,
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
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION)
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
        dispatch_type=DispatchType.DEVICE_COMMAND,
        target_type="DEVICE",
        target_code="ARM01",
        status=OutboxStatus.SENT,
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
    assert outbox.status == OutboxStatus.SENT
    outbox_repo.get_sandbox_pending_messages.assert_awaited_once_with(
        db,
        limit=50,
        workline_id=45,
        device_id=None,
    )


@pytest.mark.asyncio
async def test_sandbox_result_inbox_contains_command_contract_fields_for_runtime_processing() -> None:
    from src.app.workline.services.operation_service import WorklineOperationService

    input_payload = {"PkgID": "PKG-001"}
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
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION)
    inbox_repo = _InboxRepoStub()
    service = WorklineOperationService(
        inbox_repo=cast("Any", inbox_repo),
        session_repo=cast("Any", _SessionRepoStub(session)),
        device_repo=cast("Any", _SingleItemRepoStub(device)),
        command_repo=cast("Any", _SingleItemRepoStub(command)),
        workline_repo=cast("Any", _SingleItemRepoStub(workline)),
    )

    inbox = await service.submit_sandbox_result(
        object(),
        command_code="CMD-001",
        device_code="ARM01",
        result="SUCCESS",
        payload=input_payload,
        auto_commit=False,
    )

    assert inbox.id == 88
    assert inbox_repo.created is not None
    result_payload = inbox_repo.created["payload_json"]
    assert result_payload["command_code"] == "CMD-001"
    assert result_payload["device_code"] == "ARM01"
    assert result_payload["command_type"] == "PICK_AND_PUT"
    assert result_payload["task_type"] == "PICK_AND_PUT"
    assert result_payload["result"] == "SUCCESS"
    assert result_payload["sandbox_mode"] is True
    assert result_payload["data"] == {"PkgID": "PKG-001"}
    assert "PkgID" not in result_payload
    assert inbox_repo.created["session_id"] == 530
    assert input_payload == {"PkgID": "PKG-001"}


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
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION)
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
            payload={"PkgID": "PKG-001"},
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
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION)
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
            payload={"PkgID": "PKG-001"},
            auto_commit=False,
        )

    assert inbox_repo.created is None
