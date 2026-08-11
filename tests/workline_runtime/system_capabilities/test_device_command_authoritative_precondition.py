"""设备命令副作用的权威前置事实与锁顺序。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.device.models.command import CommandStatus
from src.app.device.repositories.command_repository import DeviceCommandRepository
from src.app.device.repositories.device_repository import DeviceRepository
from src.app.device.services.device_command_service import (
    DeviceCommandService,
    StaleDeviceCommandPrecondition,
)
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _StatementDb:
    def __init__(self, value: object) -> None:
        self.value = value
        self.statement: object | None = None

    async def execute(self, statement: object) -> _ScalarResult:
        self.statement = statement
        return _ScalarResult(self.value)


class _RecordingDeviceRepository(DeviceRepository):
    def __init__(self, device: object, events: list[str]) -> None:
        super().__init__()
        self.device = device
        self.events = events

    async def get_runtime_effect_target_for_update(
        self,
        _db: object,
        *,
        target_device_id: int | None,
        target_device_code: str | None,
        expected_workline_id: int,
    ) -> object | None:
        self.events.append("lock")
        assert expected_workline_id == 3
        return self.device


class _RecordingCommandRepository(DeviceCommandRepository):
    def __init__(self, events: list[str], *, unfinished_commands: list[object] | None = None) -> None:
        super().__init__()
        self.events = events
        self.unfinished_commands = unfinished_commands or []
        self.effects: list[tuple[object, object, object]] = []

    async def get_unfinished_commands_for_device(
        self,
        _db: object,
        device_id: int,
        *,
        limit: int = 1,
    ) -> list[object]:
        self.events.append("check-unfinished")
        assert device_id == 71
        return self.unfinished_commands[:limit]

    async def add_runtime_effect(
        self,
        _db: object,
        command: object,
        intent_log: object,
        outbox: object,
    ) -> None:
        self.events.append("write")
        self.effects.append((command, intent_log, outbox))


def _locked_device(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": 71,
        "device_code": "ROBOT-71",
        "version": 2,
        "device_status": "IDLE",
        "maintenance_mode": False,
        "current_command_id": None,
        "is_active": True,
        "work_line_id": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _service(
    device: object,
    events: list[str],
    *,
    unfinished_commands: list[object] | None = None,
) -> tuple[DeviceCommandService, _RecordingCommandRepository]:
    command_repository = _RecordingCommandRepository(events, unfinished_commands=unfinished_commands)
    service = DeviceCommandService(
        repository=command_repository,
        device_repository=_RecordingDeviceRepository(device, events),
    )
    return service, command_repository


async def _prepare(
    service: DeviceCommandService,
    *,
    target_device_id: int | None = 71,
    target_device_code: str | None = None,
    expected_workline_id: int = 3,
) -> tuple[object, object]:
    return await service.prepare_runtime_effect(
        SimpleNamespace(),
        request=SimpleNamespace(
            command_code="CMD-71-AUTHORITY",
            action="PICK_AND_PUT",
            priority=5,
            timeout_ms=30000,
            payload={},
            result_policy="COMMAND_RESULT",
        ),
        target_device_id=target_device_id,
        target_device_code=target_device_code,
        expected_workline_id=expected_workline_id,
        expected_fact_version="device:v2",
        expected_available=True,
        session=SimpleNamespace(id=10, workline_id=3, plugin_key="demo", contract_version="v1"),
        workline=SimpleNamespace(id=3, plugin_key="demo"),
        idempotency_key="device:71:dispatch",
        execution_correlation_id="corr-device-71-dispatch",
        trace_id="trace-1",
        intent_log=SimpleNamespace(
            effect_status=RuntimeIntentStatus.PROPOSED,
            dispatch_key="device-command:CMD-71-AUTHORITY",
        ),
    )


@pytest.mark.asyncio
async def test_device_repository_runtime_target_query_requests_row_lock() -> None:
    target = _locked_device()
    db = _StatementDb(target)

    result = await DeviceRepository().get_runtime_effect_target_for_update(
        db,  # type: ignore[arg-type]
        target_device_id=71,
        target_device_code=None,
        expected_workline_id=3,
    )

    assert result is target
    assert getattr(db.statement, "_for_update_arg", None) is not None
    assert "work_line_id" in str(db.statement)


@pytest.mark.asyncio
async def test_unfinished_command_query_includes_pending_and_hardware_active_statuses() -> None:
    class _Scalars:
        @staticmethod
        def all() -> list[object]:
            return []

    class _Result:
        @staticmethod
        def scalars() -> _Scalars:
            return _Scalars()

    class _Db:
        def __init__(self) -> None:
            self.statement: object | None = None

        async def execute(self, statement: object) -> _Result:
            self.statement = statement
            return _Result()

    db = _Db()
    result = await DeviceCommandRepository().get_unfinished_commands_for_device(
        db,  # type: ignore[arg-type]
        71,
    )

    assert result == []
    assert db.statement is not None
    params = db.statement.compile().params  # type: ignore[union-attr]
    status_values = next(value for key, value in params.items() if key.startswith("status_"))
    assert status_values == [CommandStatus.PENDING, CommandStatus.SENT, CommandStatus.ACK_RECEIVED]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authoritative_device",
    [
        _locked_device(version=3),
        _locked_device(device_status="RUNNING", current_command_id=99),
        _locked_device(maintenance_mode=True),
        _locked_device(is_active=False),
    ],
)
async def test_locked_authoritative_mismatch_writes_nothing(authoritative_device: object) -> None:
    events: list[str] = []
    service, command_repository = _service(authoritative_device, events)

    with pytest.raises(StaleDeviceCommandPrecondition):
        await _prepare(service)

    assert events == ["lock"]
    assert command_repository.effects == []


@pytest.mark.asyncio
async def test_locked_authoritative_match_writes_command_and_outbox_once_after_lock() -> None:
    events: list[str] = []
    service, command_repository = _service(_locked_device(), events)

    command, outbox = await _prepare(service)

    assert events == ["lock", "check-unfinished", "write"]
    [(written_command, intent_log, written_outbox)] = command_repository.effects
    assert written_command is command
    assert written_outbox is outbox
    assert intent_log.dispatch_key == outbox.dispatch_key
    assert command.device_id == 71
    assert command.plugin_key is None
    assert command.contract_version is None
    assert outbox.target_code == "ROBOT-71"


@pytest.mark.asyncio
async def test_locked_device_with_unfinished_command_rejects_before_write() -> None:
    events: list[str] = []
    service, command_repository = _service(
        _locked_device(),
        events,
        unfinished_commands=[SimpleNamespace(id=99, status="PENDING")],
    )

    with pytest.raises(StaleDeviceCommandPrecondition, match="unfinished command"):
        await _prepare(service)

    assert events == ["lock", "check-unfinished"]
    assert command_repository.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_device_id", "target_device_code"),
    [(71, None), (None, "ROBOT-71")],
)
async def test_cross_workline_target_identity_fails_closed_before_write(
    target_device_id: int | None,
    target_device_code: str | None,
) -> None:
    events: list[str] = []
    service, command_repository = _service(_locked_device(work_line_id=4), events)

    with pytest.raises(StaleDeviceCommandPrecondition):
        await _prepare(
            service,
            target_device_id=target_device_id,
            target_device_code=target_device_code,
        )

    assert events == ["lock"]
    assert command_repository.effects == []
