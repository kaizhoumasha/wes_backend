import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock

import pytest

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.app.device.models.command import (
    CommandCallbackResult,
    CommandRequest,
    CommandResult,
    CommandStatus,
    TaskType,
)
from src.app.device.services.device_command_service import (
    DeviceCommandService,
)


class FakeCommand:
    def __init__(
        self,
        *,
        id: int = 1,
        command_code: str = "CMD-TEST-001",
        device_id: int = 100,
        task_type: str = "MOVE_FORWARD",
        priority: int = 5,
        timeout_ms: int = 30000,
        params: dict[str, Any] | None = None,
        status: CommandStatus = CommandStatus.PENDING,
        retry_count: int = 0,
    ) -> None:
        self.id = id
        self.command_code = command_code
        self.device_id = device_id
        self.task_type = task_type
        self.priority = priority
        self.timeout_ms = timeout_ms
        self.params = params or {}
        self.status = status
        self.retry_count = retry_count

    def can_retry(self) -> bool:
        return self.status in [CommandStatus.FAILED, CommandStatus.TIMEOUT] and self.retry_count < 3

    def get_duration_ms(self) -> int:
        return 0


class FakeRepo:
    def __init__(self, command: FakeCommand) -> None:
        self.command = command
        self.update_calls: list[tuple[int, dict[str, Any]]] = []
        self.create_calls: list[dict[str, Any]] = []

    async def get_by_command_code(self, _db: object, command_code: str) -> FakeCommand | None:
        if self.command.command_code == command_code:
            return self.command
        return None

    async def create(self, _db: object, data: dict[str, Any]) -> FakeCommand:
        self.create_calls.append(dict(data))
        for key, value in data.items():
            setattr(self.command, key, value)
        return self.command

    async def update(self, _db: object, id: int, data: dict[str, Any]) -> FakeCommand | None:
        self.update_calls.append((id, dict(data)))
        for key, value in data.items():
            setattr(self.command, key, value)
        return self.command


class NullUpdateRepo(FakeRepo):
    async def update(self, _db: object, id: int, data: dict[str, Any]) -> FakeCommand | None:
        self.update_calls.append((id, dict(data)))
        return None


class FakeAckResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"code": 200, "message": "Accepted", "trace_id": "TRACE-ACK"}


class CapturingAsyncClient:
    requests: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "CapturingAsyncClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> FakeAckResponse:
        self.requests.append({"url": url, "json": json})
        return FakeAckResponse()


def test_command_request_accepts_plugin_defined_task_type() -> None:
    request = CommandRequest(device_id=1, task_type="WEIGH_TOTE")

    assert request.task_type == "WEIGH_TOTE"


def test_command_request_normalizes_legacy_task_type_enum() -> None:
    request = CommandRequest(device_id=1, task_type=TaskType.PICK_AND_PUT)

    assert request.task_type == "PICK_AND_PUT"


@pytest.mark.asyncio
async def test_create_command_persists_plugin_defined_task_type() -> None:
    command = FakeCommand()
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]

    db = SimpleNamespace(commit=AsyncMock())
    request = CommandRequest(device_id=1, task_type="WEIGH_TOTE", params={"tote_id": "TOTE-001"})

    created = await service.create_command(cast("Any", db), request)

    assert created is command
    assert repo.create_calls
    command_data = repo.create_calls[0]
    assert command_data["task_type"] == "WEIGH_TOTE"
    assert "-WEIGH_TOTE-" in command_data["command_code"]


@pytest.mark.asyncio
async def test_cancel_command_updates_status_without_optimistic_lock() -> None:
    command = FakeCommand(status=CommandStatus.PENDING)
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]

    db = SimpleNamespace(commit=AsyncMock())
    _ = await service.cancel_command(cast("Any", db), command.command_code)

    assert repo.update_calls
    update_data = repo.update_calls[0][1]
    assert update_data == {"status": CommandStatus.CANCELLED}


@pytest.mark.asyncio
async def test_retry_command_updates_state_without_optimistic_lock() -> None:
    command = FakeCommand(status=CommandStatus.FAILED, retry_count=1)
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]

    db = SimpleNamespace(commit=AsyncMock())
    _ = await service.retry_command(cast("Any", db), command.command_code)

    assert repo.update_calls
    update_data = repo.update_calls[0][1]
    assert update_data["status"] == CommandStatus.PENDING
    assert update_data["retry_count"] == 2
    assert "version" not in update_data


@pytest.mark.asyncio
async def test_error_detail_dict_is_kept_as_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    command = FakeCommand(status=CommandStatus.ACK_RECEIVED)
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )

    record_late_callback = AsyncMock(return_value=False)
    monkeypatch.setattr(
        workline_runtime_reconciliation_service,
        "record_late_callback_if_pending",
        record_late_callback,
    )
    db = SimpleNamespace(commit=AsyncMock())

    callback = CommandCallbackResult(
        command_code=command.command_code,
        device_code="ROBOT-ARM-01",
        result=CommandResult.FAILED,
        finish_time=1700000000000,
        error_detail={"code": "E-TIMEOUT", "msg": "timeout"},
    )

    _ = await service.handle_callback_result(cast("Any", db), callback)

    assert repo.update_calls
    update_data = repo.update_calls[0][1]
    assert isinstance(update_data["error_detail"], dict)
    assert update_data["error_detail"]["code"] == "E-TIMEOUT"
    record_late_callback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_error_detail_string_is_normalized_to_json_object() -> None:
    command = FakeCommand(status=CommandStatus.SENT)
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]

    db = SimpleNamespace(commit=AsyncMock())
    await service._update_command_status(
        cast("Any", db),
        cast("Any", command),
        CommandStatus.FAILED,
        error_detail="network timeout",
    )

    assert repo.update_calls
    update_data = repo.update_calls[0][1]
    assert update_data["error_detail"] == {"message": "network timeout"}


@pytest.mark.asyncio
async def test_send_command_body_contains_top_level_device_code_and_uses_device_callback_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    device_repository_module = importlib.import_module("src.app.device.repositories.device_repository")

    CapturingAsyncClient.requests.clear()
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    device = SimpleNamespace(
        id=100,
        device_code="RS-CONVEYOR-01",
        host="mock_ecs",
        port=8010,
        protocol="HTTP",
        callback_path="/api/v1/device/command",
    )
    monkeypatch.setattr(device_repository_module.device_repository, "get_by_id", AsyncMock(return_value=device))
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )

    monkeypatch.setattr(
        workline_runtime_reconciliation_service,
        "activate_execution_deadline_after_ack",
        AsyncMock(return_value=None),
    )

    command = FakeCommand(
        device_id=100,
        command_code="CMD-ECS-SVC",
        task_type="MOVE_FORWARD",
        params={"slot": "A01"},
    )
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]

    db = SimpleNamespace(commit=AsyncMock())
    ack = await service.send_command(cast("Any", db), command.command_code)

    assert ack.code == 200
    assert CapturingAsyncClient.requests == [
        {
            "url": "http://mock_ecs:8010/api/v1/device/command",
            "json": {
                "device_code": "RS-CONVEYOR-01",
                "command_code": "CMD-ECS-SVC",
                "task_type": "MOVE_FORWARD",
                "priority": 5,
                "timeout": 30000,
                "params": {"slot": "A01"},
                "timestamp": CapturingAsyncClient.requests[0]["json"]["timestamp"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_get_device_url_does_not_use_removed_single_device_mock_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_repository_module = importlib.import_module("src.app.device.repositories.device_repository")

    service = DeviceCommandService()
    device = SimpleNamespace(
        id=100,
        device_code="ROBOT-ARM-01",
        host=None,
        port=None,
        protocol="HTTP",
        callback_path=None,
    )
    monkeypatch.setattr(device_repository_module.device_repository, "get_by_id", AsyncMock(return_value=device))

    endpoint = await service._get_device_url(cast("Any", SimpleNamespace()), 100)

    assert endpoint == "http://ROBOT-ARM-01:8080"
