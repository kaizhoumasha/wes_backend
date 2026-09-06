"""粗分机业务配置到通用 Epoch 激活计划的唯一翻译。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from src.app.device.contracts import (
    EcsDeviceInfo,
    EcsDeviceMode,
    EcsDeviceRuntimeState,
    EcsDeviceState,
    EcsDeviceStatus,
)
from src.app.workline.services.workline_start_service import WorkLineStartConfigurationError

from rough_sorter.application.start_plan import RoughSorterStartPlanBuilder


def _rough_sorter_configuration() -> dict[str, object]:
    return {
        "device_bindings": {
            "MEASUREMENT_DEVICE": "DEVICE-1",
            "TRANSFER_DEVICE": "DEVICE-2",
            "PLACEMENT_DEVICE": "DEVICE-3",
        }
    }


def _device(device_id: int, endpoint: str | None, *, active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=device_id,
        device_code=f"DEVICE-{device_id}",
        is_deleted=False,
        endpoint_base_url=endpoint,
        is_active=active,
    )


def _required_devices(endpoint: str = "http://ecs-a:8080", *, id_offset: int = 0) -> list[SimpleNamespace]:
    return [
        _device(id_offset + 1, endpoint),
        _device(id_offset + 2, endpoint),
        _device(id_offset + 3, endpoint),
    ]


class _DeviceRepository:
    def __init__(self, devices_by_workline: dict[int, list[SimpleNamespace]]) -> None:
        self.devices_by_workline = devices_by_workline
        self.calls: list[int] = []

    async def get_by_work_line_id_for_update(self, _db: object, workline_id: int) -> list[SimpleNamespace]:
        self.calls.append(workline_id)
        return self.devices_by_workline[workline_id]


def _status(  # noqa: PLR0913 - 实时状态反例按独立字段构造
    device_code: str,
    command: str,
    *,
    online: bool = True,
    updated_at: int = 999_000,
    commands: tuple[str, ...] | None = None,
    events: tuple[str, ...] | None = ("SCAN_COMPLETED",),
) -> EcsDeviceStatus:
    return EcsDeviceStatus(
        device=EcsDeviceInfo(
            device_code=device_code,
            device_name=device_code,
            device_type="TEST_DEVICE",
            role="TEST_ROLE",
            supported_commands=commands if commands is not None else (command,),
            supported_events=events,
        ),
        state=EcsDeviceRuntimeState(
            device_code=device_code,
            mode=EcsDeviceMode.AUTO,
            status=EcsDeviceState.IDLE,
            is_online=online,
            current_command_code=None,
            scenario=None,
            updated_at=updated_at,
        ),
    )


def _statuses(devices: list[SimpleNamespace]) -> tuple[EcsDeviceStatus, ...]:
    return tuple(
        _status(
            device.device_code,
            "MOVE_FORWARD" if device.device_code in {"DEVICE-2", "DEVICE-102"} else "PICK_AND_PUT",
            events=("SCAN_COMPLETED",) if device.device_code in {"DEVICE-1", "DEVICE-101"} else (),
        )
        for device in devices
    )


class _Adapter:
    def __init__(self, statuses: tuple[EcsDeviceStatus, ...] | Exception) -> None:
        self.statuses = statuses
        self.calls = 0

    async def fetch_statuses(self) -> tuple[EcsDeviceStatus, ...]:
        self.calls += 1
        if isinstance(self.statuses, Exception):
            raise self.statuses
        return self.statuses


class _AdapterProvider:
    def __init__(self, adapters: dict[str, _Adapter]) -> None:
        self.adapters = adapters
        self.calls: list[str] = []

    async def get_adapter(self, endpoint: str) -> _Adapter:
        self.calls.append(endpoint)
        return self.adapters[endpoint]


def _builder(
    repository: _DeviceRepository,
    devices_by_endpoint: dict[str, list[SimpleNamespace]],
) -> tuple[RoughSorterStartPlanBuilder, _AdapterProvider]:
    provider = _AdapterProvider(
        {endpoint: _Adapter(_statuses(devices)) for endpoint, devices in devices_by_endpoint.items()}
    )
    return (
        RoughSorterStartPlanBuilder(
            device_repository=repository,
            adapter_provider=provider,
            clock=lambda: datetime.fromtimestamp(1_000, UTC),
        ),
        provider,
    )


@pytest.mark.asyncio
async def test_builder_reads_devices_once_and_each_ecs_endpoint_once() -> None:
    devices = _required_devices()
    devices.append(_device(4, None))
    repository = _DeviceRepository({10: devices})
    builder, provider = _builder(repository, {"http://ecs-a:8080": devices[:3]})
    workline = SimpleNamespace(id=10, config=_rough_sorter_configuration())

    plan = await builder.build(object(), workline)

    assert repository.calls == [10]
    assert provider.calls == ["http://ecs-a:8080"]
    assert provider.adapters["http://ecs-a:8080"].calls == 1
    assert (plan.plugin_key, plan.plugin_version, plan.flow_mode) == (
        "rough_sorter",
        "1.0.0",
        "ROUGH_SORT_INBOUND",
    )
    assert plan.configuration_snapshot == _rough_sorter_configuration()
    assert [(item.device_role, item.endpoint_base_url) for item in plan.device_bindings] == [
        ("MEASUREMENT_DEVICE", "http://ecs-a:8080"),
        ("TRANSFER_DEVICE", "http://ecs-a:8080"),
        ("PLACEMENT_DEVICE", "http://ecs-a:8080"),
    ]
    assert [(item.position_role, item.location_type) for item in plan.position_bindings] == [
        ("MEASUREMENT_POSITION", "MEASUREMENT_POSITION"),
        ("PIPELINE_INLET", "PIPELINE_INLET"),
        ("PIPELINE_OUTLET", "PIPELINE_OUTLET"),
        ("NG_POSITION", "NG_POSITION"),
    ]


@pytest.mark.asyncio
async def test_builder_keeps_workline_device_and_endpoint_state_isolated_across_calls() -> None:
    first = _required_devices("http://shared-ecs:8080")
    second = _required_devices("http://other-ecs:8081", id_offset=100)
    second[2].endpoint_base_url = "http://shared-ecs:8080"
    repository = _DeviceRepository({10: first, 20: second})
    builder, provider = _builder(
        repository,
        {
            "http://shared-ecs:8080": [*first, second[2]],
            "http://other-ecs:8081": second[:2],
        },
    )
    first_configuration = _rough_sorter_configuration()
    second_configuration = _rough_sorter_configuration()
    second_configuration["device_bindings"] = {
        "MEASUREMENT_DEVICE": "DEVICE-101",
        "TRANSFER_DEVICE": "DEVICE-102",
        "PLACEMENT_DEVICE": "DEVICE-103",
    }
    first_plan = await builder.build(object(), SimpleNamespace(id=10, config=first_configuration))
    second_plan = await builder.build(object(), SimpleNamespace(id=20, config=second_configuration))
    first_configuration.clear()
    second_configuration.clear()

    assert [(item.device_id, item.device_code, item.endpoint_base_url) for item in first_plan.device_bindings] == [
        (1, "DEVICE-1", "http://shared-ecs:8080"),
        (2, "DEVICE-2", "http://shared-ecs:8080"),
        (3, "DEVICE-3", "http://shared-ecs:8080"),
    ]
    assert [(item.device_id, item.device_code, item.endpoint_base_url) for item in second_plan.device_bindings] == [
        (101, "DEVICE-101", "http://other-ecs:8081"),
        (102, "DEVICE-102", "http://other-ecs:8081"),
        (103, "DEVICE-103", "http://shared-ecs:8080"),
    ]
    assert first_plan.configuration_snapshot == _rough_sorter_configuration()
    assert second_plan.configuration_snapshot["device_bindings"]["MEASUREMENT_DEVICE"] == "DEVICE-101"
    assert {binding.location_id for binding in first_plan.position_bindings} == {
        "MEASUREMENT_POSITION",
        "PIPELINE_INLET",
        "PIPELINE_OUTLET",
        "NG_POSITION",
    }
    assert first_plan.position_bindings == second_plan.position_bindings
    assert all(
        binding.status_max_age_ms == 10_000 and binding.command_timeout_ms == 30_000
        for binding in first_plan.device_bindings
    )
    assert repository.calls == [10, 20]
    assert provider.calls == [
        "http://shared-ecs:8080",
        "http://other-ecs:8081",
        "http://shared-ecs:8080",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "duplicate", "inactive", "endpoint_missing", "deleted", "unpersisted"])
async def test_builder_fails_closed_for_invalid_required_device_topology(case: str) -> None:
    devices = _required_devices()
    configuration = _rough_sorter_configuration()
    match case:
        case "missing":
            devices.pop()
        case "duplicate":
            configuration["device_bindings"]["TRANSFER_DEVICE"] = "DEVICE-1"
        case "deleted":
            devices[0].is_deleted = True
        case "unpersisted":
            devices[0].id = None
        case "inactive":
            devices[0].is_active = False
        case "endpoint_missing":
            devices[0].endpoint_base_url = None
    repository = _DeviceRepository({10: devices})
    builder, _provider = _builder(repository, {"http://ecs-a:8080": devices})

    with pytest.raises(WorkLineStartConfigurationError):
        await builder.build(object(), SimpleNamespace(id=10, config=configuration))


@pytest.mark.asyncio
async def test_builder_maps_invalid_business_configuration_to_start_configuration_error() -> None:
    devices = _required_devices()
    repository = _DeviceRepository({10: devices})
    builder, _provider = _builder(repository, {"http://ecs-a:8080": devices})

    with pytest.raises(WorkLineStartConfigurationError):
        await builder.build(object(), SimpleNamespace(id=10, config={"rough_sorter": {}}))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unavailable", "missing", "stale", "offline", "capability"])
async def test_builder_fails_closed_when_ecs_fact_is_not_startable(case: str) -> None:
    devices = _required_devices()
    statuses = list(_statuses(devices))
    if case == "missing":
        statuses.pop()
    elif case == "stale":
        statuses[0] = _status("DEVICE-1", "PICK_AND_PUT", updated_at=1)
    elif case == "offline":
        statuses[0] = _status("DEVICE-1", "PICK_AND_PUT", online=False)
    elif case == "capability":
        statuses[1] = _status("DEVICE-2", "MOVE_FORWARD", commands=("OTHER_COMMAND",))
    adapter = _Adapter(RuntimeError("unavailable") if case == "unavailable" else tuple(statuses))
    provider = _AdapterProvider({"http://ecs-a:8080": adapter})
    builder = RoughSorterStartPlanBuilder(
        device_repository=_DeviceRepository({10: devices}),
        adapter_provider=provider,
        clock=lambda: datetime.fromtimestamp(1_000, UTC),
    )

    with pytest.raises(WorkLineStartConfigurationError, match="http://ecs-a:8080"):
        await builder.build(object(), SimpleNamespace(id=10, config=_rough_sorter_configuration()))

    assert adapter.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("age_ms", [-1, 0, 10_000, 10_001])
async def test_start_measures_freshness_after_endpoint_response(age_ms: int) -> None:
    devices = _required_devices()
    now = [1_000]

    class AdvancingAdapter:
        async def fetch_statuses(self):
            now[0] = 1_002
            return tuple(
                _status(
                    device.device_code,
                    "MOVE_FORWARD" if device.id == 2 else "PICK_AND_PUT",
                    updated_at=1_002_000 - age_ms,
                    events=("SCAN_COMPLETED",) if device.id == 1 else (),
                )
                for device in devices
            )

    builder = RoughSorterStartPlanBuilder(
        device_repository=_DeviceRepository({10: devices}),
        adapter_provider=_AdapterProvider({"http://ecs-a:8080": AdvancingAdapter()}),
        clock=lambda: datetime.fromtimestamp(now[0], UTC),
    )
    if 0 <= age_ms <= 10_000:
        await builder.build(object(), SimpleNamespace(id=10, config=_rough_sorter_configuration()))
    else:
        with pytest.raises(WorkLineStartConfigurationError):
            await builder.build(object(), SimpleNamespace(id=10, config=_rough_sorter_configuration()))


@pytest.mark.asyncio
@pytest.mark.parametrize("device_index", [0, 1, 2])
@pytest.mark.parametrize("events", [None, (), ("OTHER_EVENT",), ("SCAN_COMPLETED",)])
async def test_only_measurement_role_requires_scan_completed(device_index: int, events: tuple[str, ...] | None) -> None:
    devices = _required_devices()
    statuses = list(_statuses(devices))
    statuses[device_index] = _status(
        devices[device_index].device_code,
        "MOVE_FORWARD" if device_index == 1 else "PICK_AND_PUT",
        events=events,
    )
    adapter = _Adapter(tuple(statuses))
    builder = RoughSorterStartPlanBuilder(
        device_repository=_DeviceRepository({10: devices}),
        adapter_provider=_AdapterProvider({"http://ecs-a:8080": adapter}),
        clock=lambda: datetime.fromtimestamp(1_000, UTC),
    )
    workline = SimpleNamespace(id=10, config=_rough_sorter_configuration())
    if device_index == 0 and "SCAN_COMPLETED" not in (events or ()):
        with pytest.raises(WorkLineStartConfigurationError, match="SCAN_COMPLETED"):
            await builder.build(object(), workline)
    else:
        plan = await builder.build(object(), workline)
        assert len(plan.device_bindings) == 3
    assert adapter.calls == 1
