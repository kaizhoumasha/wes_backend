"""START 使用 WorkLine version 并在行锁内发布当前插件配置。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wes_plugin_sdk import WorkLineDeviceRole, WorkLinePositionSlot

from src.app.workline.activation import WorkLineActivationPlan, WorkLineDeviceBinding, WorkLinePositionBinding
from src.app.workline.models.workline import LineType, WorkLine, WorkLinePositionInput
from src.app.workline.services.workline_start_service import (
    WorkLineStartConfigurationError,
    WorkLineStartInvalidStateError,
    WorkLineStartNotFoundError,
    WorkLineStartService,
    WorkLineStartVersionConflictError,
)


def setup_start():
    line = WorkLine(
        id=7,
        line_code="WL-7",
        line_name="line",
        line_type=LineType.AUTO,
        version=3,
        plugin_key="example",
        config={"device_bindings": {"INPUT": "DEVICE-9"}, "position_bindings": {"INPUT": "LOCAL-IN"}},
    )
    plan = WorkLineActivationPlan(
        plugin_key="example",
        plugin_version="1.0",
        flow_mode="FLOW",
        device_bindings=(
            WorkLineDeviceBinding(
                workline_id=7,
                device_id=9,
                device_code="DEVICE-9",
                device_role="INPUT",
                endpoint_base_url="http://ecs:8080",
                contract_key="ecs",
                contract_version="1.0",
                status_max_age_ms=1000,
                command_timeout_ms=5000,
            ),
        ),
        position_bindings=(
            WorkLinePositionBinding(position_role="INPUT", location_id="LOC-1", location_type="HANDOFF_POSITION"),
        ),
    )
    builder = AsyncMock()
    builder.build.return_value = plan
    plugin = SimpleNamespace(
        plugin_key="example",
        plugin_version="1.0",
        supports=lambda _: True,
        device_roles=(WorkLineDeviceRole(role_key="INPUT", display_name="设备"),),
        position_slots=(
            WorkLinePositionSlot(
                slot_key="INPUT", display_name="入口", position_type="STATION", location_type="HANDOFF_POSITION"
            ),
        ),
        business_blocker=None,
        start_plan_builder=builder,
    )
    repository = AsyncMock()
    repository.get_for_update.return_value = line
    repository.get_unfinished_workload_summary.return_value = {"by_type": {}, "count": 0, "sample": None}

    async def activate(db, workline):
        workline.is_active = True
        workline.increment_version()
        return workline

    repository.set_active_for_start.side_effect = activate
    safety = AsyncMock()
    safety.get_active_for_workline.return_value = None
    positions = AsyncMock()
    positions.list_for_workline.return_value = [
        WorkLinePositionInput(
            position_code="LOCAL-IN", position_name="入口", position_type="STATION", logic_location_code="LOC-1"
        )
    ]
    return (
        WorkLineStartService(
            position_repository=positions, plugins=(plugin,), workline_repository=repository, safety_repository=safety
        ),
        line,
        repository,
        safety,
        plugin,
    )


@pytest.mark.asyncio
async def test_start_atomically_publishes_contracts_and_advances_version():
    service, line, repository, _, plugin = setup_start()
    db = object()
    result = await service.start(db, workline_id=7, version=3)
    assert result is line and line.is_active and line.version == 4
    assert (line.plugin_version, line.flow_mode) == ("1.0", "FLOW")
    assert line.config == {"device_bindings": {"INPUT": "DEVICE-9"}, "position_bindings": {"INPUT": "LOCAL-IN"}}
    assert line.device_contracts["DEVICE-9"]["endpoint_base_url"] == "http://ecs:8080"
    assert "device_role" not in line.device_contracts["DEVICE-9"]
    assert line.position_bindings == {"INPUT": {"location_id": "LOC-1", "location_type": "HANDOFF_POSITION"}}
    repository.get_for_update.assert_awaited_once_with(db, 7)
    plugin.start_plan_builder.build.assert_awaited_once_with(
        db, line, position_bindings=plugin.start_plan_builder.build.return_value.position_bindings
    )


@pytest.mark.asyncio
async def test_lost_response_replay_cannot_restart_with_old_version():
    service, line, repository, _, plugin = setup_start()
    await service.start(object(), workline_id=7, version=3)
    line.is_active = False
    line.increment_version()
    with pytest.raises(WorkLineStartVersionConflictError):
        await service.start(object(), workline_id=7, version=3)
    assert not line.is_active and line.version == 5
    assert plugin.start_plan_builder.build.await_count == 1
    assert repository.set_active_for_start.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["active", "safety", "workload", "plugin"])
async def test_start_rejects_unfinished_work_before_builder(gate):
    service, line, repository, safety, plugin = setup_start()
    if gate == "active":
        line.is_active = True
    elif gate == "safety":
        safety.get_active_for_workline.return_value = object()
    elif gate == "workload":
        repository.get_unfinished_workload_summary.return_value = {
            "by_type": {"position_projections": True},
            "count": 1,
        }
    else:
        plugin.business_blocker = AsyncMock()
        plugin.business_blocker.get_unfinished_workload_summary.return_value = {"count": 1, "sample": "FIFO"}
    with pytest.raises(WorkLineStartInvalidStateError):
        await service.start(object(), workline_id=7, version=3)
    plugin.start_plan_builder.build.assert_not_awaited()
    repository.set_active_for_start.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_rejects_missing_workline():
    service, _, repository, _, _ = setup_start()
    repository.get_for_update.return_value = None
    with pytest.raises(WorkLineStartNotFoundError):
        await service.start(object(), workline_id=7, version=3)


@pytest.mark.asyncio
async def test_start_rejects_uninstalled_plugin():
    service, line, _, _, _ = setup_start()
    line.plugin_key = "absent"
    with pytest.raises(WorkLineStartConfigurationError):
        await service.start(object(), workline_id=7, version=3)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["1.0", "2.0"])
async def test_worker_requires_current_exact_plugin_version(version):
    service, _, repository, _, _ = setup_start()
    repository.list_active_plugin_identities.return_value = [("example", version)]
    if version == "1.0":
        await service.assert_execution_worker_startable(object())
    else:
        with pytest.raises(WorkLineStartConfigurationError):
            await service.assert_execution_worker_startable(object())


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["foreign_workline", "duplicate_device", "role_drift"])
async def test_start_rejects_inconsistent_device_binding_plan(change):
    from dataclasses import replace

    service, line, repository, _, plugin = setup_start()
    plan = plugin.start_plan_builder.build.return_value
    binding = plan.device_bindings[0]
    if change == "foreign_workline":
        plan = replace(plan, device_bindings=(replace(binding, workline_id=8),))
    elif change == "duplicate_device":
        line.config = {"device_bindings": {"INPUT": "DEVICE-9", "OUTPUT": "DEVICE-9"}}
        plan = replace(plan, device_bindings=(binding, replace(binding, device_role="OUTPUT")))
    else:
        plan = replace(plan, device_bindings=(replace(binding, device_role="OTHER"),))
    plugin.start_plan_builder.build.return_value = plan
    with pytest.raises(WorkLineStartConfigurationError):
        await service.start(object(), workline_id=7, version=3)
    repository.set_active_for_start.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_rejects_unbound_positions_and_builder_cannot_override_site_binding():
    from dataclasses import replace

    service, line, repository, _, plugin = setup_start()
    line.config = {"device_bindings": {"INPUT": "DEVICE-9"}, "position_bindings": {"INPUT": "OTHER-LINE"}}
    with pytest.raises(WorkLineStartConfigurationError, match="缺少本线"):
        await service.start(object(), workline_id=7, version=3)
    plugin.start_plan_builder.build.assert_not_awaited()
    line.config["position_bindings"] = {"INPUT": "LOCAL-IN"}
    plugin.start_plan_builder.build.return_value = replace(
        plugin.start_plan_builder.build.return_value,
        position_bindings=(
            WorkLinePositionBinding(position_role="INPUT", location_id="WRONG", location_type="HANDOFF_POSITION"),
        ),
    )
    with pytest.raises(WorkLineStartConfigurationError, match="装配不一致"):
        await service.start(object(), workline_id=7, version=3)
    repository.set_active_for_start.assert_not_awaited()
    assert not line.is_active and line.device_contracts == {} and line.position_bindings == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "offline",
        "stale",
        "wrong_owner",
        "missing_binding",
        "duplicate_status",
        "missing_provider",
        "missing_admission",
        "missing_status",
        "fetch_error",
        "invalid_endpoint",
    ],
)
async def test_declaration_without_business_start_activates_validated_devices(failure):
    from src.app.device.contracts import EcsDeviceStatus
    from src.app.device.services import device_command_admission
    from src.utils.timezone import timezone

    service, line, repository, _, plugin = setup_start()
    plugin.start_plan_builder = None
    service._devices = AsyncMock()
    service._devices.get_by_work_line_id_for_update.return_value = [
        SimpleNamespace(
            id=9,
            device_code="DEVICE-9",
            work_line_id=7,
            is_active=True,
            is_deleted=False,
            endpoint_base_url="http://ecs:8080",
        )
    ]
    provider = AsyncMock()
    adapter = provider.get_adapter.return_value
    adapter.fetch_statuses.return_value = (
        EcsDeviceStatus.model_validate(
            {
                "device": {
                    "device_code": "DEVICE-9",
                    "device_name": None,
                    "device_type": None,
                    "role": None,
                    "supported_commands": None,
                    "supported_events": None,
                },
                "state": {
                    "device_code": "DEVICE-9",
                    "is_online": True,
                    "mode": "AUTO",
                    "status": "IDLE",
                    "current_command_code": None,
                    "scenario": None,
                    "updated_at": int(timezone.now_utc().timestamp() * 1000),
                },
            }
        ),
    )
    service._adapter_provider = provider
    service._admission = device_command_admission
    if failure == "offline":
        status = adapter.fetch_statuses.return_value[0]
        adapter.fetch_statuses.return_value = (
            status.model_copy(update={"state": status.state.model_copy(update={"is_online": False})}),
        )
    elif failure == "stale":
        status = adapter.fetch_statuses.return_value[0]
        adapter.fetch_statuses.return_value = (
            status.model_copy(update={"state": status.state.model_copy(update={"updated_at": 0})}),
        )
    elif failure == "wrong_owner":
        service._devices.get_by_work_line_id_for_update.return_value[0].work_line_id = 8
    elif failure == "missing_binding":
        line.config = {"device_bindings": {}, "position_bindings": {"INPUT": "LOCAL-IN"}}
    elif failure == "duplicate_status":
        adapter.fetch_statuses.return_value *= 2
    elif failure == "missing_provider":
        service._adapter_provider = None
    elif failure == "missing_admission":
        service._admission = None
    elif failure == "missing_status":
        adapter.fetch_statuses.return_value = ()
    elif failure == "fetch_error":
        adapter.fetch_statuses.side_effect = RuntimeError("ECS unavailable")
    elif failure == "invalid_endpoint":
        service._devices.get_by_work_line_id_for_update.return_value[0].endpoint_base_url = "not-a-url"
    if failure is not None:
        with pytest.raises(WorkLineStartConfigurationError) as error:
            await service.start(object(), workline_id=7, version=3)
        assert not line.is_active and line.plugin_version is None and line.device_contracts == {}
        assert line.version == 3 and line.position_bindings == {}
        repository.set_active_for_start.assert_not_awaited()
        if failure in {"missing_provider", "missing_admission", "invalid_endpoint"}:
            adapter.fetch_statuses.assert_not_awaited()
        if failure == "missing_status":
            assert "ECS 缺少设备 DEVICE-9" in str(error.value)
        elif failure == "fetch_error":
            assert "ECS 实时状态不可启动" in str(error.value)
            assert isinstance(error.value.__cause__, RuntimeError)
        return
    result = await service.start(object(), workline_id=7, version=3)
    assert result.is_active and result.version == 4
    assert result.plugin_version == "1.0" and result.flow_mode is None
    assert result.device_contracts["DEVICE-9"]["contract_key"] == "third_party_integration"
    assert result.position_bindings["INPUT"]["location_id"] == "LOC-1"
