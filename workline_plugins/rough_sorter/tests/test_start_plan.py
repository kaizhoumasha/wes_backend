"""粗分机业务配置到通用 Epoch 激活计划的唯一翻译。"""

from __future__ import annotations

import socket
from copy import deepcopy
from types import SimpleNamespace

import pytest
from src.app.workline.services.workline_start_service import WorkLineStartConfigurationError

from rough_sorter.application.start_plan import RoughSorterStartPlanBuilder


def _rough_sorter_configuration() -> dict[str, object]:
    contract = {
        "ecs_version": "ecs-1",
        "gateway_version": "gateway-1",
        "device_model": "model-1",
        "firmware_version": "firmware-1",
        "status_max_age_ms": 600_000,
        "command_timeout_ms": 30_000,
        "time_source": "plc",
        "allowed_clock_skew_ms": 1_000,
        "callback_retry_window_ms": 60_000,
        "evidence_retention_days": 30,
    }
    return {
        "device_contracts": {
            "MEASUREMENT_DEVICE": deepcopy(contract),
            "TRANSFER_DEVICE": deepcopy(contract),
            "PLACEMENT_DEVICE": deepcopy(contract),
        },
        "position_bindings": {
            "MEASUREMENT_POSITION": "measurement-1",
            "PIPELINE_INLET": "inlet-1",
            "PIPELINE_OUTLET": "outlet-1",
            "NG_POSITION": "ng-1",
        },
    }


def _device(device_id: int, role: str, endpoint: str | None, *, active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=device_id,
        device_code=f"DEVICE-{device_id}",
        device_role=role,
        endpoint_base_url=endpoint,
        is_active=active,
    )


def _required_devices(endpoint: str = "http://ecs-a:8080", *, id_offset: int = 0) -> list[SimpleNamespace]:
    return [
        _device(id_offset + 1, "MEASUREMENT_DEVICE", endpoint),
        _device(id_offset + 2, "TRANSFER_DEVICE", endpoint),
        _device(id_offset + 3, "PLACEMENT_DEVICE", endpoint),
    ]


class _DeviceRepository:
    def __init__(self, devices_by_workline: dict[int, list[SimpleNamespace]]) -> None:
        self.devices_by_workline = devices_by_workline
        self.calls: list[int] = []

    async def get_by_work_line_id_for_update(self, _db: object, workline_id: int) -> list[SimpleNamespace]:
        self.calls.append(workline_id)
        return self.devices_by_workline[workline_id]


@pytest.mark.asyncio
async def test_builder_reads_devices_once_without_network_and_returns_complete_foundation_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = _required_devices()
    devices.append(_device(4, "REPORT_ONLY", None))
    repository = _DeviceRepository({10: devices})
    builder = RoughSorterStartPlanBuilder(device_repository=repository)
    workline = SimpleNamespace(id=10, config={"rough_sorter": _rough_sorter_configuration(), "sibling": {"kept": True}})

    def fail_on_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("builder 不得发起网络调用")

    monkeypatch.setattr(socket, "socket", fail_on_network)

    plan = await builder.build(object(), workline)

    assert repository.calls == [10]
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
    builder = RoughSorterStartPlanBuilder(device_repository=repository)
    first_configuration = _rough_sorter_configuration()
    second_configuration = _rough_sorter_configuration()
    second_contracts = second_configuration["device_contracts"]
    second_positions = second_configuration["position_bindings"]
    assert isinstance(second_contracts, dict)
    assert isinstance(second_positions, dict)
    second_measurement = second_contracts["MEASUREMENT_DEVICE"]
    assert isinstance(second_measurement, dict)
    second_measurement["firmware_version"] = "firmware-2"
    second_positions["NG_POSITION"] = "ng-2"

    first_plan = await builder.build(object(), SimpleNamespace(id=10, config={"rough_sorter": first_configuration}))
    second_plan = await builder.build(object(), SimpleNamespace(id=20, config={"rough_sorter": second_configuration}))
    first_configuration.clear()
    second_measurement["firmware_version"] = "changed-after-build"
    second_positions["NG_POSITION"] = "changed-after-build"

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
    assert (
        first_plan.configuration_snapshot["device_contracts"]["MEASUREMENT_DEVICE"]["firmware_version"] == "firmware-1"
    )
    assert first_plan.configuration_snapshot["position_bindings"]["NG_POSITION"] == "ng-1"
    assert (
        second_plan.configuration_snapshot["device_contracts"]["MEASUREMENT_DEVICE"]["firmware_version"] == "firmware-2"
    )
    assert second_plan.configuration_snapshot["position_bindings"]["NG_POSITION"] == "ng-2"
    assert repository.calls == [10, 20]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "duplicate", "inactive", "endpoint_missing"])
async def test_builder_fails_closed_for_invalid_required_device_topology(case: str) -> None:
    devices = _required_devices()
    match case:
        case "missing":
            devices.pop()
        case "duplicate":
            devices.append(_device(4, "TRANSFER_DEVICE", "http://ecs-a:8080"))
        case "inactive":
            devices[0].is_active = False
        case "endpoint_missing":
            devices[0].endpoint_base_url = None
    builder = RoughSorterStartPlanBuilder(device_repository=_DeviceRepository({10: devices}))

    with pytest.raises(WorkLineStartConfigurationError):
        await builder.build(object(), SimpleNamespace(id=10, config={"rough_sorter": _rough_sorter_configuration()}))


@pytest.mark.asyncio
async def test_builder_maps_invalid_business_configuration_to_start_configuration_error() -> None:
    builder = RoughSorterStartPlanBuilder(device_repository=_DeviceRepository({10: _required_devices()}))

    with pytest.raises(WorkLineStartConfigurationError):
        await builder.build(object(), SimpleNamespace(id=10, config={"rough_sorter": {}}))
