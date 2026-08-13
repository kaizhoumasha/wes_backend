"""DeviceCommand 应用端口的创建边界。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.app.device.contracts import DeviceCommandRequest
from src.app.device.models.command import DeviceCommand  # noqa: TC001
from src.app.device.services.device_command_service import (
    DeviceCommandCapacityError,
    DeviceCommandDeadlineError,
    DeviceCommandIdentityConflictError,
    DeviceCommandService,
    DeviceNotFoundError,
)
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding


class FakeBegin(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSessionFactory:
    def begin(self) -> FakeBegin:
        return FakeBegin()


class FakeCommandRepository:
    def __init__(self) -> None:
        self.unclosed: dict[str, DeviceCommand] = {}
        self.created: list[DeviceCommand] = []

    async def get_unclosed_for_device_for_update(self, _db: object, device_code: str) -> DeviceCommand | None:
        return self.unclosed.get(device_code)

    async def get_by_execution_ref_for_update(
        self,
        _db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
        execution_ref_type: str,
        execution_ref_id: str,
    ) -> DeviceCommand | None:
        return next(
            (
                command
                for command in self.created
                if command.line_run_epoch_id == line_run_epoch_id
                and command.device_code == device_code
                and command.execution_ref_type == execution_ref_type
                and command.execution_ref_id == execution_ref_id
            ),
            None,
        )

    async def add(self, _db: object, command: DeviceCommand) -> DeviceCommand:
        command.id = len(self.created) + 1
        self.created.append(command)
        self.unclosed[command.device_code] = command
        return command


class FakeEpochRepository:
    def __init__(self, bindings: dict[tuple[int, str], LineRunEpochDeviceBinding]) -> None:
        self.bindings = bindings

    async def get_binding_for_command_creation(
        self,
        _db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None:
        return self.bindings.get((line_run_epoch_id, device_code))


def _binding(device_code: str = "ARM-01") -> LineRunEpochDeviceBinding:
    return LineRunEpochDeviceBinding(
        id=21,
        line_run_epoch_id=11,
        device_id=7,
        device_code=device_code,
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )


def _request(device_code: str = "ARM-01") -> DeviceCommandRequest:
    return DeviceCommandRequest(
        device_code=device_code,
        line_run_epoch_id=11,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id="EXEC-001",
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        params={"source_location": "STATION-A"},
        deadline_at=datetime(2026, 8, 13, 0, 0, 30),
        trace_id="TRACE-001",
    )


def _service(*bindings: LineRunEpochDeviceBinding) -> tuple[DeviceCommandService, FakeCommandRepository]:
    command_repository = FakeCommandRepository()
    epoch_repository = FakeEpochRepository(
        {(binding.line_run_epoch_id, binding.device_code): binding for binding in bindings}
    )
    service = DeviceCommandService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        command_repository=command_repository,  # type: ignore[arg-type]
        epoch_repository=epoch_repository,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13),
    )
    return service, command_repository


@pytest.mark.asyncio
async def test_unbound_device_fails_before_creating_command() -> None:
    service, repository = _service()

    with pytest.raises(DeviceNotFoundError):
        await service.create_command(_request())

    assert repository.created == []


@pytest.mark.asyncio
async def test_same_device_rejects_second_unclosed_command() -> None:
    service, _ = _service(_binding())

    await service.create_command(_request())

    with pytest.raises(DeviceCommandCapacityError):
        await service.create_command(replace(_request(), execution_ref_id="EXEC-002"))


@pytest.mark.asyncio
async def test_same_execution_identity_and_payload_returns_original_handle() -> None:
    service, repository = _service(_binding())

    first = await service.create_command(_request())
    duplicate = await service.create_command(replace(_request(), trace_id="TRACE-RETRY"))

    assert duplicate == first
    assert len(repository.created) == 1


@pytest.mark.asyncio
async def test_same_execution_identity_with_different_payload_is_conflict() -> None:
    service, repository = _service(_binding())
    await service.create_command(_request())

    with pytest.raises(DeviceCommandIdentityConflictError):
        await service.create_command(replace(_request(), params={"source_location": "STATION-B"}))

    assert len(repository.created) == 1


@pytest.mark.asyncio
async def test_different_devices_create_independent_commands() -> None:
    service, repository = _service(_binding("ARM-01"), _binding("ARM-02"))

    first = await service.create_command(_request("ARM-01"))
    second = await service.create_command(_request("ARM-02"))

    assert first.command_code != second.command_code
    assert {command.device_code for command in repository.created} == {"ARM-01", "ARM-02"}
    assert all(len(command.payload_digest) == 64 for command in repository.created)


@pytest.mark.asyncio
async def test_deadline_cannot_exceed_frozen_binding_timeout() -> None:
    service, repository = _service(_binding())

    with pytest.raises(DeviceCommandDeadlineError, match="deadline"):
        await service.create_command(replace(_request(), deadline_at=datetime(2026, 8, 13, 0, 0, 31)))

    assert repository.created == []


@pytest.mark.asyncio
async def test_aware_deadline_is_rejected_as_database_contract_violation() -> None:
    service, repository = _service(_binding())
    with pytest.raises(DeviceCommandDeadlineError, match="naive UTC"):
        await service.create_command(replace(_request(), deadline_at=datetime(2026, 8, 13, tzinfo=UTC)))
    assert repository.created == []
