from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.app.sys.services.event_stream_service import (
    COMMAND_STATUS_CHANGED_EVENT,
    DEVICE_STATUS_CHANGED_EVENT,
    WORKLINE_RUNTIME_CHANGED_EVENT,
)
from src.utils.timezone import timezone

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"


@pytest.mark.asyncio
async def test_device_status_changed_sse_uses_canonical_envelope() -> None:
    from src.app.device.services.device_service import device_service

    db = SimpleNamespace(info={})
    device = SimpleNamespace(
        id=101,
        device_code="ARM-01",
        work_line_id=45,
        device_status="RUNNING",
        current_command_id=505,
        error_code=None,
        maintenance_mode=False,
        version=1,
        last_heartbeat_at=timezone.now(),
    )

    old_state = {"device_status": "IDLE"}
    changed_fields = ["device_status"]

    with patch("src.app.device.services.device_service.defer_sse_event") as mock_defer:
        device_service._defer_device_status_event(db, device=device, old_state=old_state, changed_fields=changed_fields)

    mock_defer.assert_called_once()
    event_type, payload = mock_defer.call_args.args[1], mock_defer.call_args.args[2]

    assert event_type == DEVICE_STATUS_CHANGED_EVENT
    assert payload["domain"] == "workline_runtime"
    assert payload["entity"] == "device"
    assert payload["action"] == "updated"
    assert payload["keys"] == {
        "workline_id": 45,
        "device_id": 101,
    }
    assert payload["device_id"] == 101
    assert payload["device_code"] == "ARM-01"


@pytest.mark.asyncio
async def test_session_updated_sse_uses_canonical_envelope() -> None:
    from src.app.runtime.orchestration.services.inbox.inbox_batch_processor import (
        build_workline_runtime_session_updated_event_payload,
    )
    from src.app.sys.services.event_stream_service import defer_sse_event

    db = SimpleNamespace(info={})
    payload = build_workline_runtime_session_updated_event_payload(workline_id=45, session_id=99)

    defer_sse_event(
        db,
        WORKLINE_RUNTIME_CHANGED_EVENT,
        payload,
    )

    events = db.info.get("_deferred_sse_events_after_commit", [])
    assert len(events) == 1
    event_type, payload = events[0]

    assert event_type == WORKLINE_RUNTIME_CHANGED_EVENT
    assert payload["domain"] == "workline_runtime"
    assert payload["entity"] == "session"
    assert payload["action"] == "updated"
    assert payload["keys"] == {
        "workline_id": 45,
        "session_id": 99,
    }


@pytest.mark.asyncio
async def test_defer_command_status_changed_event_writes_canonical_payload() -> None:
    from src.app.sys.services.event_stream_service import defer_command_status_changed_event

    db = SimpleNamespace(info={})
    command = SimpleNamespace(id=777, command_code="CMD-001", status="ACK_RECEIVED")

    defer_command_status_changed_event(
        db,
        command=command,
        action="acked",
        workline_id=45,
        device_id=101,
        session_id=99,
    )

    events = db.info.get("_deferred_sse_events_after_commit", [])
    assert len(events) == 1
    event_type, payload = events[0]

    assert event_type == COMMAND_STATUS_CHANGED_EVENT
    assert payload["domain"] == "workline_runtime"
    assert payload["entity"] == "command"
    assert payload["action"] == "acked"
    assert payload["keys"] == {
        "workline_id": 45,
        "device_id": 101,
        "command_id": 777,
        "command_code": "CMD-001",
        "session_id": 99,
    }
    assert payload["command_id"] == 777
    assert payload["command_code"] == "CMD-001"
    assert payload["status"] == "ACK_RECEIVED"


@pytest.mark.asyncio
async def test_defer_command_status_changed_event_omits_session_id_when_none() -> None:
    from src.app.sys.services.event_stream_service import defer_command_status_changed_event

    db = SimpleNamespace(info={})
    command = SimpleNamespace(id=10, command_code="CMD-X", status="FAILED")

    defer_command_status_changed_event(
        db,
        command=command,
        action="updated",
        workline_id=1,
        device_id=2,
    )

    events = db.info.get("_deferred_sse_events_after_commit", [])
    assert len(events) == 1
    _, payload = events[0]

    assert "session_id" not in payload["keys"]
    assert payload["keys"] == {
        "workline_id": 1,
        "device_id": 2,
        "command_id": 10,
        "command_code": "CMD-X",
    }
    assert payload["action"] == "updated"


@pytest.mark.asyncio
async def test_defer_command_status_changed_event_unwraps_enum_status() -> None:
    from src.app.sys.services.event_stream_service import defer_command_status_changed_event

    db = SimpleNamespace(info={})
    status_obj = SimpleNamespace(value="COMPLETED")
    command = SimpleNamespace(id=5, command_code="CMD-Z", status=status_obj)

    defer_command_status_changed_event(
        db,
        command=command,
        action="updated",
        workline_id=1,
        device_id=2,
    )

    events = db.info.get("_deferred_sse_events_after_commit", [])
    _, payload = events[0]
    assert payload["status"] == "COMPLETED"


# ===========================================================
# Path matrix: 5 ACK paths must defer command.status.changed via helper
#
# 每条路径都通过驱动真实的 service 方法 / gateway 入口 + patch
# `defer_command_status_changed_event` 间谍来验证：
#   - 间谍被调用一次
#   - 入参 (command, action, workline_id, device_id, session_id) 与契约一致
# 这样即便有人把 helper 调用包进 `if False:` / 死代码也会被捕获。
# ===========================================================

_HELPER_PATCH_TARGET = "src.app.sys.services.event_stream_service.defer_command_status_changed_event"


# ---------- 真实 ECS ACK 路径辅助：复用 gateway 单测的 capturing client ----------


class _AckCapturingAsyncClient:
    """伪 httpx.AsyncClient：先返回 status idle，再返回 ack 200。"""

    requests: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_AckCapturingAsyncClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.requests.append({"method": "GET", "url": url})
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}},
        )

    async def post(self, url: str, *, json: dict[str, Any], **kwargs: Any) -> Any:
        self.requests.append({"method": "POST", "url": url, "json": json})
        return SimpleNamespace(status_code=200, text="")


def _make_ack_dispatchable_device() -> Any:
    return SimpleNamespace(
        id=101,
        device_code="ARM-01",
        host="mock_ecs",
        port=8010,
        protocol="HTTP",
        callback_path="/api/v1/device/command",
        device_status="IDLE",
        current_command_id=None,
        maintenance_mode=False,
        capabilities_json={"supports_command_types": ["MOVE_FORWARD"]},
    )


@pytest.mark.asyncio
async def test_real_ecs_ack_path_defers_command_status_changed_event(monkeypatch) -> None:
    """真实 ECS ACK (HTTP 200) 路径必须经由 helper defer command.status.changed。"""

    import importlib

    from src.app.device.models.command import CommandStatus
    from src.app.runtime.orchestration.services.device_command_gateway import DeviceCommandGateway

    gateway_module = importlib.import_module("src.app.runtime.orchestration.services.device_command_gateway")
    command_repository_module = importlib.import_module("src.app.device.repositories.command_repository")

    _AckCapturingAsyncClient.requests.clear()

    # 真实 Command ORM 对象用 SimpleNamespace 替身，提供完整字段集
    command = SimpleNamespace(
        id=9,
        command_code="CMD-GW-ACK",
        workline_id=45,
        device_id=101,
        correlation_id="session-530",
        status=CommandStatus.SENT,
        sent_at=None,
        ack_received_at=None,
        ack_code=None,
        ack_message=None,
    )

    class _CommandRepoStub:
        async def get_by_command_code(self, _db: Any, _command_code: str) -> Any:
            return command

    monkeypatch.setattr(httpx, "AsyncClient", _AckCapturingAsyncClient)
    monkeypatch.setattr(
        gateway_module,
        "_get_device_for_command_dispatch",
        AsyncMock(return_value=_make_ack_dispatchable_device()),
    )
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", _CommandRepoStub)

    # 阻断旁路调用：device_service.mark_command_dispatched + 对账激活 deadline
    fake_device_service = SimpleNamespace(mark_command_dispatched=AsyncMock(return_value=None))
    monkeypatch.setattr("src.app.device.services.device_service", fake_device_service, raising=True)
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_runtime_reconciliation_service."
        "activate_execution_deadline_after_ack",
        AsyncMock(return_value=None),
        raising=True,
    )

    db = AsyncMock()
    outbox = SimpleNamespace(
        id=1,
        target_code="ARM-01",
        target_type="DEVICE",
        dispatch_key="device-command:CMD-GW-ACK",
        payload_json={"command_code": "CMD-GW-ACK", "task_type": "MOVE_FORWARD"},
        session_id=530,
    )

    gateway = DeviceCommandGateway()
    helper_spy = MagicMock()

    with patch(_HELPER_PATCH_TARGET, new=helper_spy):
        success = await gateway.dispatch(db, outbox)

    assert success is True
    helper_spy.assert_called_once()
    kwargs = helper_spy.call_args.kwargs
    assert kwargs["command"] is command
    assert kwargs["action"] == "acked"
    assert kwargs["workline_id"] == 45
    assert kwargs["device_id"] == 101
    assert kwargs["session_id"] == 530


# ---------- Sandbox 路径：复用 operation_service 测试中的 stub 形态 ----------


class _SandboxOutboxRepoStub:
    def __init__(self, outbox: Any) -> None:
        self.outbox = outbox
        self.get_by_dispatch_key = AsyncMock(return_value=outbox)
        self.release_blocked_by_device = AsyncMock(return_value=0)


class _SandboxSessionRepoStub:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.get_by_id = AsyncMock(return_value=session)
        self.get_open_session_by_awaiting_device_command_code = AsyncMock(
            side_effect=self._get_by_awaiting_device_command_code
        )

    async def _get_by_awaiting_device_command_code(self, _db: Any, command_code: str) -> Any | None:
        if getattr(self.session, "awaiting_device_command_code", None) == command_code:
            return self.session
        return None


class _SandboxSingleRepoStub:
    def __init__(self, item: Any) -> None:
        self.item = item
        self.get_by_id = AsyncMock(return_value=item)
        self.get_for_update = AsyncMock(return_value=item)
        self.get_by_device_code = AsyncMock(return_value=item)
        self.get_by_command_code = AsyncMock(return_value=item)


class _SandboxInboxRepoStub:
    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None
        self.create = AsyncMock(side_effect=self._create)

    async def _create(self, _db: Any, data: dict[str, Any]) -> Any:
        self.created = dict(data)
        return SimpleNamespace(id=88, **self.created)


@pytest.mark.asyncio
async def test_sandbox_ack_path_defers_command_status_changed_event() -> None:
    """sandbox ACK 路径必须经由 helper defer command.status.changed。"""

    from src.app.device.models.command import CommandStatus
    from src.app.runtime.orchestration.models.session import SessionStatus
    from src.app.runtime.orchestration.services.intent.operation_service import WorklineOperationService
    from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus
    from src.app.workline.models.workline import WorkLineRunMode

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
        workline_id=45,
        device_id=101,
        correlation_id="trace-001",
        status=CommandStatus.SENT,
        sent_at=None,
        ack_received_at=None,
        ack_code=None,
        ack_message=None,
    )
    session = SimpleNamespace(
        id=530,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        awaiting_device_command_code="CMD-001",
    )
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)

    service = WorklineOperationService(
        outbox_repo=cast("Any", _SandboxOutboxRepoStub(outbox)),
        session_repo=cast("Any", _SandboxSessionRepoStub(session)),
        command_repo=cast("Any", _SandboxSingleRepoStub(command)),
        workline_repo=cast("Any", _SandboxSingleRepoStub(workline)),
    )

    helper_spy = MagicMock()
    with (
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl."
            "workline_runtime_reconciliation_service.activate_execution_deadline_after_ack",
            new=AsyncMock(return_value=session),
        ),
        patch(_HELPER_PATCH_TARGET, new=helper_spy),
    ):
        _ = await service.submit_sandbox_ack(
            object(),
            dispatch_key="device-command:CMD-001",
            auto_commit=False,
        )

    helper_spy.assert_called_once()
    kwargs = helper_spy.call_args.kwargs
    assert kwargs["command"] is command
    assert kwargs["action"] == "acked"
    assert kwargs["workline_id"] == 45
    assert kwargs["device_id"] == 101
    assert kwargs["session_id"] == 530


@pytest.mark.asyncio
async def test_sandbox_result_path_defers_command_status_changed_event() -> None:
    """sandbox result 路径必须经由 helper defer command.status.changed。"""

    from src.app.runtime.orchestration.models.session import SessionStatus
    from src.app.runtime.orchestration.services.intent.operation_service import WorklineOperationService
    from src.app.workline.models.workline import WorkLineRunMode

    device = SimpleNamespace(id=7, device_code="ARM01")
    command = SimpleNamespace(
        id=9,
        command_code="CMD-001",
        task_type="PICK_AND_PUT",
        params={"business_key": "PKG-001"},
        workline_id=45,
        correlation_id="trace-001",
        device_id=7,
        trace_id="trace-001",
    )
    session = SimpleNamespace(
        id=530,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        awaiting_device_command_code="CMD-001",
    )
    workline = SimpleNamespace(id=45, run_mode=WorkLineRunMode.SIMULATION, is_active=True)

    service = WorklineOperationService(
        inbox_repo=cast("Any", _SandboxInboxRepoStub()),
        outbox_repo=cast("Any", _SandboxOutboxRepoStub(None)),
        session_repo=cast("Any", _SandboxSessionRepoStub(session)),
        device_repo=cast("Any", _SandboxSingleRepoStub(device)),
        command_repo=cast("Any", _SandboxSingleRepoStub(command)),
        workline_repo=cast("Any", _SandboxSingleRepoStub(workline)),
    )

    fake_device_service = SimpleNamespace(
        mark_command_finished=AsyncMock(return_value=SimpleNamespace(device_status="IDLE", current_command_id=None))
    )
    helper_spy = MagicMock()

    with (
        patch("src.app.device.services.device_service", fake_device_service),
        patch(_HELPER_PATCH_TARGET, new=helper_spy),
    ):
        _ = await service.submit_sandbox_result(
            object(),
            command_code="CMD-001",
            device_code="ARM01",
            result="SUCCESS",
            payload={"item_id": "ITEM-001"},
            timestamp=datetime(2026, 5, 29, 8, 0, 1),
            auto_commit=False,
        )

    helper_spy.assert_called_once()
    kwargs = helper_spy.call_args.kwargs
    assert kwargs["command"] is command
    assert kwargs["action"] == "updated"
    assert kwargs["workline_id"] == 45
    assert kwargs["device_id"] == 7
    assert kwargs["session_id"] == 530


# ---------- ACK exhausted: 两条分支都需要 helper ----------


class _ReconciliationDb:
    """复用 reconciliation 单测中的 _Db 形态。"""

    def __init__(self, command: Any) -> None:
        self.command = command
        self.flush = AsyncMock()

    async def get(self, _model: Any, _pk: int) -> Any:
        return self.command


class _SseReconciliationManager:
    async def register_conflict_idempotent(self, _db: Any, conflict: Any, **_kwargs: Any) -> Any:
        from src.app.reconciliation.manager import ReconciliationManager, ReconciliationRegistrationResult
        from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult

        return ReconciliationRegistrationResult(
            decision=ReconciliationManager().register_conflict(conflict),
            claim_result=ClaimResult.NEW,
        )


def _build_ack_exhausted_fixtures(
    *,
    session_status: Any,
    command_status: Any,
    reconciliation_state: Any,
) -> tuple[Any, Any, Any, Any]:
    from src.app.sys.models import SystemOutboxStatus

    command = SimpleNamespace(
        id=881,
        command_code="CMD-ACK-EXHAUSTED",
        workline_id=45,
        device_id=7,
        correlation_id="sandbox-session-553",
        status=command_status,
        completed_at=None,
        error_detail=None,
    )
    outbox = SimpleNamespace(
        id=862,
        session_id=553,
        workline_id=45,
        target_code="CONVEYOR01",
        status=SystemOutboxStatus.SENT,
        last_error=None,
        next_retry_at=timezone.now_for_db() + timedelta(seconds=30),
        finished_at=None,
        blocked_by_runtime_hold_id=None,
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
    )
    session = SimpleNamespace(
        id=553,
        workline_id=45,
        trace_id="sandbox:trace-ack-timeout",
        status=session_status,
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=300,
        waiting_since=timezone.now_for_db() - timedelta(seconds=400),
        deadline_at=None,
        awaiting_device_command_code=command.command_code,
        reconciliation_state=reconciliation_state,
    )
    from src.app.workline.models.safety import WorkLineRuntimeStatus

    workline = SimpleNamespace(runtime_status=WorkLineRuntimeStatus.READY, stopped_at=None, stopped_reason=None)
    return command, outbox, session, workline


def _build_reconciliation_service(*, session: Any, workline: Any) -> Any:
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        WorklineRuntimeReconciliationService,
    )

    session_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=session))
    workline_repo = SimpleNamespace(get_for_update=AsyncMock(return_value=workline))
    device_service = SimpleNamespace(mark_dispatch_ack_exhausted=AsyncMock(return_value=None))
    runtime_hold_creation_service = SimpleNamespace(
        create_for_dispatch_ack_exhausted=AsyncMock(return_value=SimpleNamespace(id=9904))
    )
    return WorklineRuntimeReconciliationService(
        session_repository=session_repo,
        workline_repository=workline_repo,
        device_service=device_service,
        runtime_hold_creation_service=runtime_hold_creation_service,
        reconciliation_manager=_SseReconciliationManager(),
    )


@pytest.mark.asyncio
async def test_ack_exhausted_pending_branch_defers_command_status_changed_event() -> None:
    """ACK exhausted - PENDING 重入分支必须 defer helper。"""

    from src.app.device.models.command import CommandStatus
    from src.app.runtime.orchestration.models.session import RuntimeReconciliationState, SessionStatus

    command, outbox, session, workline = _build_ack_exhausted_fixtures(
        session_status=SessionStatus.MANUAL_HOLD,
        command_status=CommandStatus.PENDING,
        reconciliation_state=RuntimeReconciliationState.PENDING,
    )
    service = _build_reconciliation_service(session=session, workline=workline)
    db = _ReconciliationDb(command=command)

    helper_spy = MagicMock()
    with (
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.add_timeline_with_sequence",
            new=AsyncMock(),
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
        patch(_HELPER_PATCH_TARGET, new=helper_spy),
    ):
        _ = await service.handle_dispatch_ack_exhausted(
            db,
            outbox=outbox,
            command=command,
            error_message="COMMAND_ACK_TIMEOUT",
        )

    helper_spy.assert_called_once()
    kwargs = helper_spy.call_args.kwargs
    assert kwargs["command"] is command
    assert kwargs["action"] == "updated"
    assert kwargs["workline_id"] == 45
    assert kwargs["device_id"] == 7
    assert kwargs["session_id"] == 553


@pytest.mark.asyncio
async def test_ack_exhausted_main_branch_defers_command_status_changed_event() -> None:
    """ACK exhausted - 新建 reconciliation 主分支必须 defer helper。"""

    from src.app.device.models.command import CommandStatus
    from src.app.runtime.orchestration.models.session import SessionStatus

    command, outbox, session, workline = _build_ack_exhausted_fixtures(
        session_status=SessionStatus.WAITING_DEVICE_RESULT,
        command_status=CommandStatus.SENT,
        reconciliation_state=None,
    )
    service = _build_reconciliation_service(session=session, workline=workline)
    db = _ReconciliationDb(command=command)

    helper_spy = MagicMock()
    with (
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.add_timeline_with_sequence",
            new=AsyncMock(),
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
        patch(_HELPER_PATCH_TARGET, new=helper_spy),
    ):
        _ = await service.handle_dispatch_ack_exhausted(
            db,
            outbox=outbox,
            command=command,
            error_message="COMMAND_ACK_TIMEOUT",
        )

    helper_spy.assert_called_once()
    kwargs = helper_spy.call_args.kwargs
    assert kwargs["command"] is command
    assert kwargs["action"] == "updated"
    assert kwargs["workline_id"] == 45
    assert kwargs["device_id"] == 7
    assert kwargs["session_id"] == 553


# ---------- Dispatch failed -> 委托 handle_dispatch_ack_exhausted ----------


@pytest.mark.asyncio
async def test_dispatch_failed_path_delegates_and_defers_command_status_changed_event() -> None:
    """gateway 入口 _mark_device_command_failed_if_dispatch_exhausted 委托给
    handle_dispatch_ack_exhausted；端到端必须经由 helper defer。"""

    from src.app.device.models.command import CommandStatus
    from src.app.runtime.orchestration.models.session import SessionStatus
    from src.app.runtime.orchestration.services.device_command_gateway import (
        _mark_device_command_failed_if_dispatch_exhausted,
    )
    from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus

    command, _, session, workline = _build_ack_exhausted_fixtures(
        session_status=SessionStatus.WAITING_DEVICE_RESULT,
        command_status=CommandStatus.SENT,
        reconciliation_state=None,
    )

    # gateway 路径会从 outbox / failed_outbox 衍生上下文：原 outbox 含 payload + DEVICE_COMMAND；
    # failed_outbox.status 必须为 FAILED 才会触发委托。
    outbox = SimpleNamespace(
        id=862,
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        payload_json={"command_code": "CMD-ACK-EXHAUSTED"},
    )
    failed_outbox = SimpleNamespace(
        id=862,
        status=SystemOutboxStatus.FAILED,
        session_id=553,
        workline_id=45,
        target_code="CONVEYOR01",
        last_error=None,
        next_retry_at=None,
        finished_at=None,
        blocked_by_runtime_hold_id=None,
        blocked_by_reconciliation_session_id=None,
        blocked_device_id=7,
        blocked_workline_id=45,
        blocked_reason="DEVICE_BUSY",
    )

    # 真实 reconciliation service 实例 + stubbed deps，让委托走完整全路径
    reconciliation_service = _build_reconciliation_service(session=session, workline=workline)

    db = _ReconciliationDb(command=command)
    helper_spy = MagicMock()

    class _GatewayCommandRepo:
        async def get_by_command_code(self, _db: Any, _command_code: str) -> Any:
            return command

    with (
        patch(
            "src.app.device.repositories.command_repository.DeviceCommandRepository",
            _GatewayCommandRepo,
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_runtime_reconciliation_service",
            reconciliation_service,
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.add_timeline_with_sequence",
            new=AsyncMock(),
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ),
        patch(_HELPER_PATCH_TARGET, new=helper_spy),
    ):
        await _mark_device_command_failed_if_dispatch_exhausted(
            db,
            outbox=outbox,
            failed_outbox=failed_outbox,
            error_message="OUTBOX_DISPATCH_FAILED",
        )

    helper_spy.assert_called_once()
    kwargs = helper_spy.call_args.kwargs
    assert kwargs["command"] is command
    assert kwargs["action"] == "updated"
    assert kwargs["workline_id"] == 45
    assert kwargs["device_id"] == 7
    assert kwargs["session_id"] == 553


def test_only_helper_emits_command_status_changed_event() -> None:
    """单一入口约束：grep 后端源码确保没有手工拼装 command.status.changed payload。"""

    offending: list[tuple[str, int, str]] = []
    for py_file in _SRC_ROOT.rglob("*.py"):
        # event_stream_service 是 helper 源头，本身允许出现常量
        if py_file.name == "event_stream_service.py":
            continue
        # __init__.py 重新导出常量是允许的
        if py_file.name == "__init__.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # 检测手工拼装：直接调用 defer_sse_event 配合该事件常量 / 字面量
            if "COMMAND_STATUS_CHANGED_EVENT" in stripped and "defer_sse_event" in stripped:
                offending.append((str(py_file.relative_to(_REPO_ROOT)), line_no, stripped))
            if "defer_sse_event(" in stripped and '"command.status.changed"' in stripped:
                offending.append((str(py_file.relative_to(_REPO_ROOT)), line_no, stripped))

    assert offending == [], f"发现绕过 helper 的手工拼装：{offending}"
