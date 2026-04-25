from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from src.app.device.models.command import CommandStatus
from src.app.device.models.device import DeviceStatus
from src.app.device.services.device_service import DeviceService
from src.app.sys.services.event_stream_service import DEVICE_STATUS_CHANGED_EVENT, publish_deferred_sse_events
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


def test_normalize_runtime_update_forces_maintenance_to_release_current_command() -> None:
    service = DeviceService()

    normalized = service._normalize_runtime_update(
        {
            "maintenance_mode": True,
            "current_command_id": 1001,
            "error_code": "",
        },
        current=None,
    )

    assert normalized["device_status"] == DeviceStatus.MAINTENANCE
    assert normalized["maintenance_mode"] is True
    assert normalized["current_command_id"] is None
    assert normalized["error_code"] == "MAINTENANCE"


def test_normalize_runtime_update_status_maintenance_releases_current_command() -> None:
    service = DeviceService()

    normalized = service._normalize_runtime_update(
        {
            "device_status": DeviceStatus.MAINTENANCE,
            "current_command_id": 1001,
        },
        current=None,
    )

    assert normalized["maintenance_mode"] is True
    assert normalized["current_command_id"] is None
