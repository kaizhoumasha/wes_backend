from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from src.app.device.models.command import CommandStatus
from src.app.device.models.device import DeviceStatus, DeviceUpdate
from src.app.device.services.device_service import DeviceService
from src.app.sys.services.event_stream_service import DEVICE_STATUS_CHANGED_EVENT, publish_deferred_sse_events
from src.core.exceptions import BusinessException
from src.utils.timezone import timezone


class FakeDeviceRepo:
    def __init__(self, *devices: SimpleNamespace) -> None:
        self.devices = {device.id: device for device in devices}
        self.devices_by_code = {device.device_code: device for device in devices}
        self.update_calls: list[tuple[int, dict[str, Any]]] = []

    async def get_by_id(self, _db: object, device_id: int) -> SimpleNamespace | None:
        return self.devices.get(device_id)

    async def get_by_device_code(self, _db: object, device_code: str) -> SimpleNamespace | None:
        return self.devices_by_code.get(device_code)

    async def update(self, _db: object, device_id: int, data: dict[str, Any]) -> SimpleNamespace | None:
        self.update_calls.append((device_id, dict(data)))
        device = self.devices.get(device_id)
        if device is None:
            return None
        for key, value in data.items():
            setattr(device, key, value)
        return device

    async def get_heartbeat_stale_devices(
        self,
        _db: object,
        *,
        cutoff: object,
        limit: int,
    ) -> list[SimpleNamespace]:
        _ = cutoff
        return list(self.devices.values())[:limit]

    async def get_non_maintenance_by_workline_for_update(
        self,
        _db: object,
        workline_id: int,
    ) -> list[SimpleNamespace]:
        return [
            device
            for device in self.devices.values()
            if getattr(device, "work_line_id", None) == workline_id
            and getattr(device, "device_status", None) in {DeviceStatus.IDLE, DeviceStatus.RUNNING}
            and not bool(getattr(device, "maintenance_mode", False))
        ]

    async def get_safety_error_by_workline_for_update(
        self,
        _db: object,
        workline_id: int,
    ) -> list[SimpleNamespace]:
        return [
            device
            for device in self.devices.values()
            if getattr(device, "work_line_id", None) == workline_id
            and getattr(device, "device_status", None) == DeviceStatus.ERROR
            and getattr(device, "error_code", None) == "WORKLINE_ESTOPPED"
        ]


class FakeCommandRepo:
    def __init__(self, active_commands: list[SimpleNamespace] | None = None) -> None:
        self.active_commands = active_commands or []
        self.active_queries: list[tuple[int, int | None, int]] = []

    async def get_active_commands_for_device(
        self,
        _db: object,
        device_id: int,
        *,
        exclude_command_id: int | None = None,
        limit: int = 1,
    ) -> list[SimpleNamespace]:
        self.active_queries.append((device_id, exclude_command_id, limit))
        active_statuses = {CommandStatus.SENT, CommandStatus.ACK_RECEIVED}
        active_commands = [
            command for command in self.active_commands if getattr(command, "status", None) in active_statuses
        ]
        return active_commands[:limit]


def _service(device_repo: FakeDeviceRepo, command_repo: FakeCommandRepo | None = None) -> DeviceService:
    service = DeviceService()
    service.repo = device_repo  # type: ignore[assignment]
    service.command_repo = command_repo or FakeCommandRepo()  # type: ignore[attr-defined]
    return service


def test_device_update_rejects_runtime_projection_fields() -> None:
    with pytest.raises(ValidationError):
        DeviceUpdate.model_validate(
            {
                "version": 1,
                "device_status": "ERROR",
                "current_command_id": 1001,
                "error_code": "MANUAL",
                "maintenance_mode": True,
            }
        )


def test_runtime_policy_rejects_illegal_running_without_command() -> None:
    from src.app.device.services.runtime_state_policy import DeviceRuntimeStatePolicy

    with pytest.raises(BusinessException, match="RUNNING"):
        DeviceRuntimeStatePolicy.validate(
            {
                "device_status": DeviceStatus.RUNNING,
                "current_command_id": None,
                "error_code": None,
                "maintenance_mode": False,
            },
            reason="unit-test",
        )


def test_runtime_policy_projects_maintenance_state() -> None:
    from src.app.device.services.runtime_state_policy import DeviceRuntimeStatePolicy

    assert DeviceRuntimeStatePolicy.maintenance("planned").data == {
        "device_status": DeviceStatus.MAINTENANCE,
        "maintenance_mode": True,
        "current_command_id": None,
        "error_code": "planned",
    }


@pytest.mark.asyncio
async def test_mark_command_dispatched_sets_running_for_single_task_device() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.IDLE,
        current_command_id=None,
        error_code="OLD",
        max_concurrent_tasks=1,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock())

    updated = await service.mark_command_dispatched(cast("Any", db), device_id=7, command_id=1001, auto_commit=False)

    assert updated is device
    assert repo.update_calls == [
        (
            7,
            {
                "device_status": DeviceStatus.RUNNING,
                "current_command_id": 1001,
                "error_code": None,
            },
        )
    ]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_command_dispatched_rejects_invalid_command_id() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.IDLE,
        current_command_id=None,
        error_code=None,
        maintenance_mode=False,
        max_concurrent_tasks=1,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock())

    with pytest.raises(BusinessException, match="RUNNING"):
        await service.mark_command_dispatched(cast("Any", db), device_id=7, command_id=0, auto_commit=False)

    assert repo.update_calls == []


@pytest.mark.asyncio
async def test_mark_command_dispatched_defers_device_status_sse_event() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        work_line_id=3,
        device_status=DeviceStatus.IDLE,
        current_command_id=None,
        error_code=None,
        max_concurrent_tasks=1,
        version=5,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock(), info={})

    updated = await service.mark_command_dispatched(cast("Any", db), device_id=7, command_id=1001, auto_commit=False)

    assert updated is device
    assert repo.update_calls == [
        (
            7,
            {
                "device_status": DeviceStatus.RUNNING,
                "current_command_id": 1001,
                "version": 5,
            },
        )
    ]

    with patch(
        "src.app.sys.services.event_stream_service.event_stream_service.publish",
        new=AsyncMock(return_value=True),
    ) as publish:
        await publish_deferred_sse_events(cast("Any", db))

    publish.assert_awaited_once()
    event_type, payload = publish.await_args.args
    assert event_type == DEVICE_STATUS_CHANGED_EVENT
    assert payload["device_id"] == 7
    assert payload["device_code"] == "ARM01"
    assert payload["work_line_id"] == 3
    assert payload["status"] == DeviceStatus.RUNNING.value
    assert payload["previous_status"] == DeviceStatus.IDLE.value
    assert payload["current_command_id"] == 1001
    assert payload["version"] == 5
    assert payload["changed_fields"] == ["current_command_id", "device_status"]


@pytest.mark.asyncio
async def test_mark_command_dispatched_does_not_emit_sse_when_state_is_unchanged() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        work_line_id=3,
        device_status=DeviceStatus.RUNNING,
        current_command_id=1001,
        error_code=None,
        max_concurrent_tasks=1,
        version=5,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock(), info={})

    updated = await service.mark_command_dispatched(cast("Any", db), device_id=7, command_id=1001, auto_commit=False)

    assert updated is device
    assert repo.update_calls == []

    with patch(
        "src.app.sys.services.event_stream_service.event_stream_service.publish",
        new=AsyncMock(return_value=True),
    ) as publish:
        await publish_deferred_sse_events(cast("Any", db))

    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_command_finished_keeps_running_when_another_command_is_in_hardware() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.RUNNING,
        current_command_id=1001,
        error_code=None,
        max_concurrent_tasks=1,
    )
    repo = FakeDeviceRepo(device)
    command_repo = FakeCommandRepo([SimpleNamespace(id=1002, status=CommandStatus.ACK_RECEIVED)])
    service = _service(repo, command_repo)
    db = SimpleNamespace(commit=AsyncMock())

    updated = await service.mark_command_finished(
        cast("Any", db),
        device_id=7,
        command_id=1001,
        success=True,
        auto_commit=False,
    )

    assert updated is device
    assert repo.update_calls == [(7, {"current_command_id": 1002})]
    assert device.device_status == DeviceStatus.RUNNING
    assert device.error_code is None
    assert command_repo.active_queries == [(7, 1001, 1)]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_command_finished_releases_device_when_only_pending_commands_are_queued() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.RUNNING,
        current_command_id=1001,
        error_code=None,
        max_concurrent_tasks=1,
    )
    repo = FakeDeviceRepo(device)
    command_repo = FakeCommandRepo([SimpleNamespace(id=1002, status=CommandStatus.PENDING)])
    service = _service(repo, command_repo)
    db = SimpleNamespace(commit=AsyncMock())

    updated = await service.mark_command_finished(
        cast("Any", db),
        device_id=7,
        command_id=1001,
        success=True,
        auto_commit=False,
    )

    assert updated is device
    assert repo.update_calls == [
        (
            7,
            {
                "device_status": DeviceStatus.IDLE,
                "current_command_id": None,
            },
        )
    ]
    assert device.error_code is None
    assert command_repo.active_queries == [(7, 1001, 1)]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_command_finished_clears_device_when_no_active_commands_remain() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.RUNNING,
        current_command_id=1001,
        error_code=None,
        max_concurrent_tasks=1,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo, FakeCommandRepo([]))
    db = SimpleNamespace(commit=AsyncMock())

    updated = await service.mark_command_finished(
        cast("Any", db),
        device_id=7,
        command_id=1001,
        success=True,
        auto_commit=False,
    )

    assert updated is device
    assert repo.update_calls == [
        (
            7,
            {
                "device_status": DeviceStatus.IDLE,
                "current_command_id": None,
            },
        )
    ]
    assert device.error_code is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_command_finished_sets_error_on_failed_result() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.RUNNING,
        current_command_id=1001,
        error_code=None,
        max_concurrent_tasks=1,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock())

    updated = await service.mark_command_finished(
        cast("Any", db),
        device_id=7,
        command_id=1001,
        success=False,
        error_code="PICK_FAILED",
        auto_commit=False,
    )

    assert updated is device
    assert repo.update_calls == [
        (
            7,
            {
                "device_status": DeviceStatus.ERROR,
                "current_command_id": None,
                "error_code": "PICK_FAILED",
            },
        )
    ]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_command_finished_failed_result_requires_error_code_projection() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.RUNNING,
        current_command_id=1001,
        error_code=None,
        maintenance_mode=False,
        max_concurrent_tasks=1,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock())

    updated = await service.mark_command_finished(
        cast("Any", db),
        device_id=7,
        command_id=1001,
        success=False,
        error_code="",
        auto_commit=False,
    )

    assert updated is device
    assert repo.update_calls == [
        (
            7,
            {
                "device_status": DeviceStatus.ERROR,
                "current_command_id": None,
                "error_code": "COMMAND_FAILED",
            },
        )
    ]


@pytest.mark.asyncio
async def test_mark_command_finished_does_not_exit_maintenance_on_late_success() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.MAINTENANCE,
        maintenance_mode=True,
        current_command_id=1001,
        error_code="MAINTENANCE",
        max_concurrent_tasks=1,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo, FakeCommandRepo([]))
    db = SimpleNamespace(commit=AsyncMock())

    updated = await service.mark_command_finished(
        cast("Any", db),
        device_id=7,
        command_id=1001,
        success=True,
        auto_commit=False,
    )

    assert updated is device
    assert repo.update_calls == [(7, {"current_command_id": None})]
    assert device.device_status == DeviceStatus.MAINTENANCE
    assert device.maintenance_mode is True
    assert device.error_code == "MAINTENANCE"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_command_finished_keeps_maintenance_on_late_failure() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.MAINTENANCE,
        maintenance_mode=True,
        current_command_id=1001,
        error_code="MAINTENANCE",
        max_concurrent_tasks=1,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo, FakeCommandRepo([]))
    db = SimpleNamespace(commit=AsyncMock())

    updated = await service.mark_command_finished(
        cast("Any", db),
        device_id=7,
        command_id=1001,
        success=False,
        error_code="PICK_FAILED",
        auto_commit=False,
    )

    assert updated is device
    assert repo.update_calls == [
        (
            7,
            {
                "current_command_id": None,
                "error_code": "PICK_FAILED",
            },
        )
    ]
    assert device.device_status == DeviceStatus.MAINTENANCE
    assert device.maintenance_mode is True
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_heartbeat_updates_last_seen_without_clearing_error() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.ERROR,
        current_command_id=None,
        error_code="PICK_FAILED",
        last_heartbeat_at=None,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock())

    updated = await service.record_heartbeat(cast("Any", db), device_code="ARM01", auto_commit=False)

    assert updated is device
    assert repo.update_calls[0][0] == 7
    assert repo.update_calls[0][1]["last_heartbeat_at"] is not None
    assert device.device_status == DeviceStatus.ERROR
    assert device.error_code == "PICK_FAILED"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_stale_heartbeats_offline_only_updates_stale_running_or_idle_devices() -> None:
    stale_device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        device_status=DeviceStatus.RUNNING,
        current_command_id=1001,
        error_code=None,
        last_heartbeat_at=timezone.now_for_db() - timedelta(minutes=10),
    )
    repo = FakeDeviceRepo(stale_device)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock())

    count = await service.mark_stale_heartbeats_offline(
        cast("Any", db),
        threshold_seconds=120,
        limit=50,
        auto_commit=False,
    )

    assert count == 1
    assert repo.update_calls == [
        (
            7,
            {
                "device_status": DeviceStatus.OFFLINE,
                "current_command_id": None,
                "error_code": "HEARTBEAT_TIMEOUT",
            },
        )
    ]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_enter_exit_maintenance_and_clear_fault_use_runtime_projection() -> None:
    device = SimpleNamespace(
        id=7,
        device_code="ARM01",
        work_line_id=3,
        device_status=DeviceStatus.ERROR,
        maintenance_mode=False,
        current_command_id=None,
        error_code="PICK_FAILED",
        version=5,
    )
    repo = FakeDeviceRepo(device)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock(), info={})

    await service.enter_maintenance(cast("Any", db), device_id=7, reason="PLANNED", auto_commit=False)
    await service.exit_maintenance(cast("Any", db), device_id=7, auto_commit=False)
    await service.clear_fault(cast("Any", db), device_id=7, auto_commit=False)

    assert repo.update_calls == [
        (
            7,
            {
                "device_status": DeviceStatus.MAINTENANCE,
                "maintenance_mode": True,
                "error_code": "PLANNED",
                "version": 5,
            },
        ),
        (
            7,
            {
                "device_status": DeviceStatus.IDLE,
                "maintenance_mode": False,
                "error_code": None,
                "version": 5,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_workline_safety_projection_skips_maintenance_and_clears_only_estop_error() -> None:
    running = SimpleNamespace(
        id=1,
        device_code="RUN",
        work_line_id=3,
        device_status=DeviceStatus.RUNNING,
        maintenance_mode=False,
        current_command_id=1001,
        error_code=None,
    )
    maintenance = SimpleNamespace(
        id=2,
        device_code="MAINT",
        work_line_id=3,
        device_status=DeviceStatus.MAINTENANCE,
        maintenance_mode=True,
        current_command_id=None,
        error_code="MAINTENANCE",
    )
    manual_error = SimpleNamespace(
        id=3,
        device_code="MANUAL",
        work_line_id=3,
        device_status=DeviceStatus.ERROR,
        maintenance_mode=False,
        current_command_id=None,
        error_code="MANUAL_FAULT",
    )
    repo = FakeDeviceRepo(running, maintenance, manual_error)
    service = _service(repo)
    db = SimpleNamespace(commit=AsyncMock())

    marked = await service.mark_workline_safety_error(cast("Any", db), workline_id=3, auto_commit=False)
    cleared = await service.clear_workline_safety_error(cast("Any", db), workline_id=3, auto_commit=False)

    assert marked == 1
    assert cleared == 1
    assert maintenance.device_status == DeviceStatus.MAINTENANCE
    assert maintenance.error_code == "MAINTENANCE"
    assert manual_error.device_status == DeviceStatus.ERROR
    assert manual_error.error_code == "MANUAL_FAULT"


def test_plain_update_rejects_runtime_fields() -> None:
    service = DeviceService()

    with pytest.raises(BusinessException, match="专用操作"):
        service._reject_runtime_update({"maintenance_mode": True})
