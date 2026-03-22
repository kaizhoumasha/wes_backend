import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.app.device.models.command import (  # noqa: E402
    CommandCallbackResult,
    CommandResult,
    CommandStatus,
)
from src.app.device.services.device_command_service import (  # noqa: E402
    DeviceCommandService,
)


class FakeCommand:
    def __init__(
        self,
        *,
        id: int = 1,
        command_code: str = "CMD-TEST-001",
        status: CommandStatus = CommandStatus.PENDING,
        retry_count: int = 0,
        version: int = 0,
    ) -> None:
        self.id = id
        self.command_code = command_code
        self.status = status
        self.retry_count = retry_count
        self.version = version

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
            if key == "version":
                continue
            setattr(self.command, key, value)
        return self.command


class NullUpdateRepo(FakeRepo):
    async def update(self, _db: object, id: int, data: dict[str, Any]) -> FakeCommand | None:
        self.update_calls.append((id, dict(data)))
        return None


@pytest.mark.asyncio
async def test_cancel_command_includes_version_for_optimistic_lock() -> None:
    command = FakeCommand(status=CommandStatus.PENDING, version=7)
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]

    _ = await service.cancel_command(cast("Any", SimpleNamespace()), command.command_code)

    assert repo.update_calls
    update_data = repo.update_calls[0][1]
    assert update_data["status"] == CommandStatus.CANCELLED
    assert update_data["version"] == 7


@pytest.mark.asyncio
async def test_retry_command_includes_version_for_optimistic_lock() -> None:
    command = FakeCommand(status=CommandStatus.FAILED, retry_count=1, version=9)
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]

    _ = await service.retry_command(cast("Any", SimpleNamespace()), command.command_code)

    assert repo.update_calls
    update_data = repo.update_calls[0][1]
    assert update_data["status"] == CommandStatus.PENDING
    assert update_data["retry_count"] == 2
    assert update_data["version"] == 9


@pytest.mark.asyncio
async def test_error_detail_dict_is_kept_as_json_object() -> None:
    command = FakeCommand(status=CommandStatus.ACK_RECEIVED, version=3)
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]

    callback = CommandCallbackResult(
        command_code=command.command_code,
        device_code="ROBOT-ARM-01",
        result=CommandResult.FAILED,
        finish_time=1700000000000,
        error_detail={"code": "E-TIMEOUT", "msg": "timeout"},
    )

    _ = await service.handle_callback_result(cast("Any", SimpleNamespace()), callback)

    assert repo.update_calls
    update_data = repo.update_calls[0][1]
    assert isinstance(update_data["error_detail"], dict)
    assert update_data["error_detail"]["code"] == "E-TIMEOUT"


@pytest.mark.asyncio
async def test_error_detail_string_is_normalized_to_json_object() -> None:
    command = FakeCommand(status=CommandStatus.SENT, version=5)
    repo = FakeRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]

    await service._update_command_status(
        cast("Any", SimpleNamespace()),
        cast("Any", command),
        CommandStatus.FAILED,
        error_detail="network timeout",
    )

    assert repo.update_calls
    update_data = repo.update_calls[0][1]
    assert update_data["error_detail"] == {"message": "network timeout"}
