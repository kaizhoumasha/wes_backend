"""WorkLine 插件配置校验测试。"""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from src.app.device.models import Device
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.resource.models import RackKind
from src.app.sys.models.outbox import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.workline.models import InboxKind, InboxStatus, LineType, SourceSystem, WorkLine, WorkLineRunMode
from src.app.workline.models.inbox import WorklineInbox
from src.app.workline.models.rack_position import WorklineRackPosition, WorklineRackPositionRole
from src.app.workline.models.runtime_hold import RuntimeHoldType
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.repositories.runtime_hold_repository import RuntimeHoldRepository
from src.app.workline.services.workline_service import WorkLineService
from src.core.exceptions import BadRequestException, BusinessException
from src.workline_plugin_registry import validate_workline_plugin_assignment
from src.workline_plugins.rough_sorter.contract import (
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
)
from src.workline_plugins.rough_sorter.plugin import POSITION_WORK_SINGLE_LAYER
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_SESSION_COMPLETE_REQUESTED,
    EVENT_WORKING_BIN_SCAN,
    ROLE_SORTING_SCAN_PLATFORM,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    ROLE_SORTING_WORKSTATION,
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.plugin import (
    POSITION_SOURCE_STATION_A,
    POSITION_SOURCE_STATION_B,
    POSITION_TARGET_STATION,
)

workline_service_module = importlib.import_module("src.app.workline.services.workline_service")


def make_workline() -> WorkLine:
    """创建测试作业线。"""

    return WorkLine(
        line_code="WL-SMT-001",
        line_name="粗分机#1",
        line_type=LineType.AUTO,
    )


def make_device(device_id: int, role: str) -> SimpleNamespace:
    """创建测试设备拓扑节点。"""

    return SimpleNamespace(
        id=device_id,
        device_code=f"DEV-{device_id}",
        device_role=role,
        role_index=device_id,
        sort_order=device_id,
        upstream_device_id=None,
        capabilities_json={},
        host="mock-ecs",
        port=8010,
    )


def _carrier_capability(
    *,
    allowed_rack_kinds: tuple[str, ...] = ("SINGLE_LAYER",),
    min_capacity: int = 1,
    max_capacity: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        allowed_rack_kinds=allowed_rack_kinds,
        min_capacity=min_capacity,
        max_capacity=max_capacity,
        allowed_slot_kinds=(),
    )


async def _add_rough_sorter_rack_positions(db_session, workline: WorkLine) -> None:
    workline_id = workline.id
    assert workline_id is not None
    db_session.add(
        WorklineRackPosition(
            workline_id=workline_id,
            workline_code=workline.line_code,
            position_code=POSITION_WORK_SINGLE_LAYER,
            position_name="粗分机单层货架工作位",
            position_role=WorklineRackPositionRole.SMT_CLASSIFIER_SINGLE_RACK_WORK,
            allowed_rack_kind=RackKind.SINGLE_LAYER,
            capacity=1,
            logic_location_code=f"{workline.line_code}:{POSITION_WORK_SINGLE_LAYER}",
            external_location_code=POSITION_WORK_SINGLE_LAYER,
            device_role=ROLE_OUTPUT_ARM,
            priority=100,
            metadata_json={"test_fixture": True},
        )
    )
    await db_session.commit()


async def _add_smt_sorting_inbound_rack_positions(db_session, workline: WorkLine) -> None:
    workline_id = workline.id
    assert workline_id is not None
    specs = (
        (POSITION_SOURCE_STATION_A, RackKind.SINGLE_LAYER, ROLE_SORTING_SOURCE_ARM),
        (POSITION_SOURCE_STATION_B, RackKind.SINGLE_LAYER, ROLE_SORTING_SOURCE_ARM),
        (POSITION_TARGET_STATION, RackKind.FIVE_LAYER, ROLE_SORTING_TARGET_ARM),
    )
    for priority, (position_code, rack_kind, device_role) in enumerate(specs, start=100):
        db_session.add(
            WorklineRackPosition(
                workline_id=workline_id,
                workline_code=workline.line_code,
                position_code=position_code,
                position_name=f"SMT 分拣入库 {position_code}",
                position_role=WorklineRackPositionRole.SMT_SORTER_STATION,
                allowed_rack_kind=rack_kind,
                capacity=1,
                logic_location_code=f"{workline.line_code}:{position_code}",
                external_location_code=position_code,
                device_role=device_role,
                priority=priority,
                metadata_json={"test_fixture": True},
            )
        )
    await db_session.commit()


def test_workline_model_returns_none_for_removed_smt_plugin() -> None:
    """旧 SMT 插件清理后，WorkLine 不再解析该插件类。"""

    workline = WorkLine(
        line_code="WL-SMT-002",
        line_name="粗分机#2",
        line_type=LineType.AUTO,
        plugin_key="smt_classifier",
    )

    assert workline.plugin_class is None
    assert not hasattr(workline, "state" + "_machine_class")
    assert workline.plugin_definition is None


def test_workline_service_lists_selector_only_plugin_options() -> None:
    """插件下拉选项只暴露选择器字段，不承载 manifest 能力事实。"""

    service = WorkLineService()

    options = service.list_plugin_options()
    options_by_key = {option.plugin_key: option for option in options}

    assert [option.plugin_key for option in options] == [SMT_SORTING_INBOUND_PLUGIN_KEY, "rough_sorter"]
    assert all(
        set(option.model_dump()) == {"plugin_key", "label", "contract_versions", "default_contract_version"}
        for option in options
    )
    assert options_by_key["rough_sorter"].default_contract_version == "rough_sorter.v1"
    assert options_by_key[SMT_SORTING_INBOUND_PLUGIN_KEY].default_contract_version == (
        SMT_SORTING_INBOUND_CONTRACT_VERSION
    )


def test_workline_service_returns_new_plugin_manifest_summary() -> None:
    """单插件 manifest 摘要应暴露新纯数据合同字段。"""

    service = WorkLineService()

    summary = service.get_plugin_manifest_summary(SMT_SORTING_INBOUND_PLUGIN_KEY)

    assert summary is not None
    assert summary.plugin_key == SMT_SORTING_INBOUND_PLUGIN_KEY
    assert summary.contract_version == SMT_SORTING_INBOUND_CONTRACT_VERSION
    device_roles = {req.role for req in summary.devices}
    rack_position_codes = {rack_position.code for rack_position in summary.rack_positions}
    business_demand_types = {boundary.business_demand_type for boundary in summary.resource_boundaries}
    assert device_roles == {
        ROLE_SORTING_SOURCE_ARM,
        ROLE_SORTING_SCAN_PLATFORM,
        ROLE_SORTING_TARGET_ARM,
        ROLE_SORTING_WORKSTATION,
    }
    assert "SORTING_NG_STATION" not in device_roles
    assert rack_position_codes == {"SOURCE_STATION_A", "SOURCE_STATION_B", "TARGET_STATION"}
    assert "NG_STATION" not in rack_position_codes
    assert "WORKSTATION" not in rack_position_codes
    assert business_demand_types == {"SORTING_INBOUND_SOURCE", "SORTING_INBOUND_TARGET"}
    assert "SORTING_INBOUND_NG" not in business_demand_types
    assert "SORTING_INBOUND_WORK" not in business_demand_types
    events_by_name = {event.event: event for event in summary.events}
    commands_by_name = {command.command: command for command in summary.commands}
    assert events_by_name[EVENT_WORKING_BIN_SCAN].source_device_roles == [ROLE_SORTING_SCAN_PLATFORM]
    assert events_by_name[EVENT_SESSION_COMPLETE_REQUESTED].source_device_roles == [ROLE_SORTING_WORKSTATION]
    assert commands_by_name[COMMAND_SOURCE_PICK].target_device_role == ROLE_SORTING_SOURCE_ARM
    assert commands_by_name[COMMAND_TARGET_PLACE].target_device_role == ROLE_SORTING_TARGET_ARM
    assert commands_by_name[COMMAND_NG_PLACE].target_device_role == ROLE_SORTING_TARGET_ARM
    assert commands_by_name[COMMAND_NG_PLACE].rack_position_args == []
    target_place_target = commands_by_name[COMMAND_TARGET_PLACE].rack_position_args[0]
    assert target_place_target.rack_position_ref == "TARGET_STATION"
    assert target_place_target.source is None


def test_plugin_options_do_not_expose_manifest_capabilities() -> None:
    """插件选择器不再复制 manifest 的设备、事件和命令能力字段。"""

    option = WorkLineService().list_plugin_options()[0]

    assert set(option.model_dump()) == {
        "plugin_key",
        "label",
        "contract_versions",
        "default_contract_version",
    }


def test_manifest_summary_exposes_devices_rack_positions_topology_events_commands_resource_boundaries() -> None:
    """manifest summary 使用新字段名完整暴露纯数据合同。"""

    summary = WorkLineService().get_plugin_manifest_summary("rough_sorter")

    assert summary is not None
    assert set(summary.model_dump()) == {
        "plugin_key",
        "contract_version",
        "devices",
        "rack_positions",
        "topology",
        "events",
        "commands",
        "resource_boundaries",
    }
    assert summary.devices
    assert summary.rack_positions
    assert summary.topology.flow_edges
    assert summary.events
    assert summary.commands
    assert summary.resource_boundaries
    assert summary.rack_positions[0].carrier_capability.allowed_rack_kinds


def test_smt_sorting_inbound_manifest_summary_does_not_expose_ng_arm_role() -> None:
    """SMT 分拣入库 NG 放置复用目标机械臂，不暴露独立 NG_ARM 角色。"""

    summary = WorkLineService().get_plugin_manifest_summary(SMT_SORTING_INBOUND_PLUGIN_KEY)

    assert summary is not None
    command_roles = {command.target_device_role for command in summary.commands}
    required_roles = {requirement.role for requirement in summary.devices}
    commands_by_name = {command.command: command for command in summary.commands}
    assert commands_by_name[COMMAND_NG_PLACE].target_device_role == ROLE_SORTING_TARGET_ARM
    assert command_roles == {ROLE_SORTING_SOURCE_ARM, ROLE_SORTING_TARGET_ARM}
    assert "NG_ARM" not in command_roles
    assert "NG_ARM" not in required_roles


def test_configuration_checks_use_event_and_command_bindings(monkeypatch) -> None:
    """配置预检应读取 manifest.events / manifest.commands，而不是旧角色映射。"""

    manifest = SimpleNamespace(
        plugin_key="binding_plugin",
        contract_version="binding.v1",
        devices=(
            SimpleNamespace(role="SCANNER", min_count=1, max_count=1, hardware_capabilities=frozenset()),
            SimpleNamespace(role="ARM", min_count=1, max_count=1, hardware_capabilities=frozenset()),
        ),
        rack_positions=(),
        topology=SimpleNamespace(flow_edges=()),
        events=(
            SimpleNamespace(
                event="NEW_EVENT",
                source_device_roles=("SCANNER",),
                category="ENTRY_DEVICE",
                payload_schema_ref=None,
            ),
        ),
        commands=(
            SimpleNamespace(
                command="NEW_COMMAND",
                target_device_role="ARM",
                rack_position_args=(),
                payload_schema_ref=None,
                result_bindings=(),
            ),
        ),
        resource_boundaries=(),
    )
    definition = SimpleNamespace(plugin_key="binding_plugin", manifest=manifest)
    monkeypatch.setattr(workline_service_module, "get_workline_plugin_definition", lambda plugin_key: definition)

    scanner = make_device(1, "SCANNER")
    scanner.capabilities_json = {"supports_event_types": ["NEW_EVENT"]}
    arm = make_device(2, "ARM")
    arm.capabilities_json = {"supports_command_types": ["NEW_COMMAND"]}
    workline = WorkLine(
        line_code="WL-BINDING",
        line_name="事件命令绑定检查",
        line_type=LineType.AUTO,
        plugin_key="binding_plugin",
        contract_version="binding.v1",
    )

    checks = WorkLineService()._build_configuration_checks(workline, [scanner, arm])
    event_checks = [check for check in checks if check.code == "EVENT_SOURCE_CAPABILITY"]
    command_checks = [check for check in checks if check.code == "COMMAND_TARGET_CAPABILITY"]

    assert [check.context["event_type"] for check in event_checks] == ["NEW_EVENT"]
    assert [check.context["command_type"] for check in command_checks] == ["NEW_COMMAND"]
    assert all(check.status == "PASS" for check in [*event_checks, *command_checks])


def test_configuration_checks_only_require_entry_device_event_capability(monkeypatch) -> None:
    """只有入口设备事件需要设备声明 supports_event_types。"""

    manifest = SimpleNamespace(
        plugin_key="entry_event_plugin",
        contract_version="entry-event.v1",
        devices=(
            SimpleNamespace(role="SCANNER", min_count=1, max_count=1, hardware_capabilities=frozenset()),
            SimpleNamespace(role="ARM", min_count=1, max_count=1, hardware_capabilities=frozenset()),
        ),
        rack_positions=(),
        topology=SimpleNamespace(flow_edges=()),
        events=(
            SimpleNamespace(
                event="ENTRY_SCAN",
                source_device_roles=("SCANNER",),
                category="ENTRY_DEVICE",
                payload_schema_ref=None,
            ),
            SimpleNamespace(
                event="INTERNAL_RETRY",
                source_device_roles=("ARM",),
                category="INTERNAL",
                payload_schema_ref=None,
            ),
        ),
        commands=(),
        resource_boundaries=(),
    )
    definition = SimpleNamespace(plugin_key="entry_event_plugin", manifest=manifest)
    monkeypatch.setattr(workline_service_module, "get_workline_plugin_definition", lambda plugin_key: definition)

    scanner = make_device(1, "SCANNER")
    scanner.capabilities_json = {"supports_event_types": ["ENTRY_SCAN"]}
    arm = make_device(2, "ARM")
    arm.capabilities_json = {"supports_event_types": ["ARM_READY"]}
    workline = WorkLine(
        line_code="WL-ENTRY-EVENT",
        line_name="入口事件能力检查",
        line_type=LineType.AUTO,
        plugin_key="entry_event_plugin",
        contract_version="entry-event.v1",
    )

    checks = WorkLineService()._build_configuration_checks(workline, [scanner, arm])
    event_checks = [check for check in checks if check.code == "EVENT_SOURCE_CAPABILITY"]

    assert [check.context["event_type"] for check in event_checks] == ["ENTRY_SCAN"]
    assert all(check.status == "PASS" for check in event_checks)


def test_configuration_checks_validate_rack_position_carrier_capability_against_workline_config(monkeypatch) -> None:
    """配置预检应比较 manifest rack position 承载约束和工作线货架位置配置。"""

    manifest = SimpleNamespace(
        plugin_key="carrier_plugin",
        contract_version="carrier.v1",
        devices=(),
        rack_positions=(
            SimpleNamespace(
                code="WORK_POSITION",
                role="WORK",
                station_code="WORK_STATION",
                carrier_capability=_carrier_capability(
                    allowed_rack_kinds=("FIVE_LAYER",),
                    min_capacity=2,
                    max_capacity=4,
                ),
            ),
        ),
        topology=SimpleNamespace(flow_edges=()),
        events=(),
        commands=(),
        resource_boundaries=(),
    )
    definition = SimpleNamespace(plugin_key="carrier_plugin", manifest=manifest)
    monkeypatch.setattr(workline_service_module, "get_workline_plugin_definition", lambda plugin_key: definition)
    workline = WorkLine(
        line_code="WL-CARRIER",
        line_name="承载能力检查",
        line_type=LineType.AUTO,
        plugin_key="carrier_plugin",
        contract_version="carrier.v1",
    )
    rack_position = SimpleNamespace(
        position_code="WORK_POSITION",
        allowed_rack_kind=RackKind.SINGLE_LAYER,
        capacity=1,
        enabled=True,
    )

    checks = WorkLineService()._build_configuration_checks(workline, [], [rack_position])
    carrier_checks = [check for check in checks if check.code == "RACK_POSITION_CARRIER_CAPABILITY"]

    assert len(carrier_checks) == 1
    assert carrier_checks[0].status == "FAIL"
    assert carrier_checks[0].severity == "BLOCKER"
    assert carrier_checks[0].context["rack_position_code"] == "WORK_POSITION"
    assert carrier_checks[0].context["allowed_rack_kind"] == "SINGLE_LAYER"
    assert carrier_checks[0].context["allowed_rack_kinds"] == ["FIVE_LAYER"]
    assert carrier_checks[0].context["capacity"] == 1
    assert carrier_checks[0].context["min_capacity"] == 2
    assert carrier_checks[0].context["max_capacity"] == 4


def test_configuration_checks_block_when_manifest_rack_position_config_missing(monkeypatch) -> None:
    """配置预检应阻断 manifest 声明但工作线未配置的货架停靠位。"""

    manifest = SimpleNamespace(
        plugin_key="carrier_plugin",
        contract_version="carrier.v1",
        devices=(),
        rack_positions=(
            SimpleNamespace(
                code="MISSING_POSITION",
                role="WORK",
                station_code="WORK_STATION",
                carrier_capability=_carrier_capability(
                    allowed_rack_kinds=("FIVE_LAYER",),
                    min_capacity=2,
                    max_capacity=4,
                ),
            ),
        ),
        topology=SimpleNamespace(flow_edges=()),
        events=(),
        commands=(),
        resource_boundaries=(),
    )
    definition = SimpleNamespace(plugin_key="carrier_plugin", manifest=manifest)
    monkeypatch.setattr(workline_service_module, "get_workline_plugin_definition", lambda plugin_key: definition)
    workline = WorkLine(
        line_code="WL-CARRIER",
        line_name="承载能力检查",
        line_type=LineType.AUTO,
        plugin_key="carrier_plugin",
        contract_version="carrier.v1",
    )

    checks = WorkLineService()._build_configuration_checks(workline, [], [])
    carrier_checks = [check for check in checks if check.code == "RACK_POSITION_CARRIER_CAPABILITY"]

    assert len(carrier_checks) == 1
    assert carrier_checks[0].status == "FAIL"
    assert carrier_checks[0].severity == "BLOCKER"
    assert carrier_checks[0].context["rack_position_code"] == "MISSING_POSITION"
    assert carrier_checks[0].context["missing_rack_position_config"] is True
    assert carrier_checks[0].context["allowed_rack_kinds"] == ["FIVE_LAYER"]
    assert carrier_checks[0].context["min_capacity"] == 2
    assert carrier_checks[0].context["max_capacity"] == 4


def test_workline_service_returns_none_for_unknown_plugin_manifest_summary() -> None:
    """未知插件 manifest 摘要应返回 None，交给 API 层转统一 404。"""

    service = WorkLineService()

    assert service.get_plugin_manifest_summary("unknown_plugin") is None


def test_workline_service_does_not_return_default_manifest_for_different_contract_version() -> None:
    """请求指定版本时，不允许把同插件默认版本误当作匹配 manifest。"""

    service = WorkLineService()

    assert service.get_plugin_manifest_summary("rough_sorter", contract_version="missing.v2") is None


def test_workline_service_status_path_defaults_to_standard_endpoint_not_callback_path() -> None:
    """START/status 预检不能把命令 callback_path 当成设备 status endpoint。"""

    device = SimpleNamespace(
        capabilities_json={},
        callback_path="/api/v1/device/command",
    )

    assert WorkLineService._resolve_device_status_path(device) == "/api/v1/device/status"


def test_rough_sorter_plugin_assignment_accepts_required_roles() -> None:
    """粗分机插件绑定必须具备全部关键设备角色。"""

    validate_workline_plugin_assignment(
        "rough_sorter",
        make_workline(),
        [
            make_device(1, ROLE_INPUT_ARM),
            make_device(2, ROLE_CONVEYOR),
            make_device(3, ROLE_OUTPUT_ARM),
        ],
    )


def test_smt_sorting_inbound_plugin_assignment_accepts_required_roles() -> None:
    """SMT 分拣入库插件绑定必须具备 P0 全部设备角色。"""

    validate_workline_plugin_assignment(
        SMT_SORTING_INBOUND_PLUGIN_KEY,
        make_workline(),
        [
            make_device(1, ROLE_SORTING_SOURCE_ARM),
            make_device(2, ROLE_SORTING_TARGET_ARM),
            make_device(4, ROLE_SORTING_SCAN_PLATFORM),
            make_device(6, ROLE_SORTING_WORKSTATION),
        ],
    )


@pytest.mark.asyncio
async def test_smt_sorting_inbound_configuration_status_does_not_require_event_capability_for_command_results(
    db_session,
) -> None:
    """SMT 命令结果经 callback/result 分发，不应作为设备事件能力 blocker。"""

    workline = WorkLine(
        line_code="WL-SMT-COMMAND-RESULT-EVENTS",
        line_name="SMT 命令结果能力校验",
        line_type=LineType.AUTO,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    device_specs = (
        (
            ROLE_SORTING_SOURCE_ARM,
            {"supports_command_types": [COMMAND_SOURCE_PICK], "supports_event_types": ["ARM_READY"]},
        ),
        (
            ROLE_SORTING_TARGET_ARM,
            {"supports_command_types": [COMMAND_TARGET_PLACE, COMMAND_NG_PLACE], "supports_event_types": ["ARM_READY"]},
        ),
        (ROLE_SORTING_SCAN_PLATFORM, {"supports_event_types": [EVENT_WORKING_BIN_SCAN]}),
        (ROLE_SORTING_WORKSTATION, {"supports_event_types": [EVENT_SESSION_COMPLETE_REQUESTED]}),
    )
    for index, (role, capabilities_json) in enumerate(device_specs, start=1):
        db_session.add(
            Device(
                device_code=f"SMT-CMD-RESULT-DEV-{index}",
                device_name=f"SMT 命令结果设备{index}",
                work_line_id=workline.id,
                device_role=role,
                role_index=1,
                capabilities_json={**capabilities_json, "status_path": "/api/v1/device/status"},
                host="mock-ecs",
                port=8010,
            )
        )
    await db_session.commit()
    await _add_smt_sorting_inbound_rack_positions(db_session, workline)

    status = await WorkLineService().configuration_status(db_session, workline.id)  # type: ignore[arg-type]

    failed_event_checks = [
        check for check in status.checks if check.code == "EVENT_SOURCE_CAPABILITY" and check.status == "FAIL"
    ]
    assert status.can_activate is True
    assert failed_event_checks == []


def test_smt_sorting_inbound_plugin_assignment_rejects_missing_required_role() -> None:
    """缺少扫码平台时，SMT 分拣入库插件绑定应失败。"""

    with pytest.raises(BadRequestException, match=ROLE_SORTING_SCAN_PLATFORM):
        validate_workline_plugin_assignment(
            SMT_SORTING_INBOUND_PLUGIN_KEY,
            make_workline(),
            [
                make_device(1, ROLE_SORTING_SOURCE_ARM),
                make_device(2, ROLE_SORTING_TARGET_ARM),
                make_device(6, ROLE_SORTING_WORKSTATION),
            ],
        )


def test_rough_sorter_plugin_assignment_rejects_missing_required_role() -> None:
    """缺少出料机械臂角色时，WorkLine 绑定应失败。"""

    with pytest.raises(BadRequestException, match=ROLE_OUTPUT_ARM):
        validate_workline_plugin_assignment(
            "rough_sorter",
            make_workline(),
            [
                make_device(1, ROLE_INPUT_ARM),
                make_device(2, ROLE_CONVEYOR),
            ],
        )


def test_workline_run_mode_defaults_to_auto() -> None:
    """WorkLine 默认运行模式应是 AUTO。"""

    workline = make_workline()

    assert workline.run_mode == WorkLineRunMode.AUTO
    assert workline.resolved_runtime_config["run_mode"] == WorkLineRunMode.AUTO.value
    assert workline.is_active is False


def test_workline_create_update_schema_rejects_is_active() -> None:
    """普通 CRUD schema 不暴露 is_active，状态只能通过专用动作切换。"""

    from src.app.workline.models import WorkLineCreate, WorkLineUpdate

    with pytest.raises(ValidationError, match="is_active"):
        WorkLineCreate.model_validate(
            {
                "line_code": "WL-DRAFT",
                "line_name": "草稿线",
                "line_type": LineType.AUTO,
                "is_active": True,
            }
        )

    with pytest.raises(ValidationError, match="is_active"):
        WorkLineUpdate.model_validate({"version": 1, "is_active": True})


def test_workline_service_rejects_simulation_in_prod() -> None:
    """生产环境不允许开启 SIMULATION 沙箱模式。"""

    service = WorkLineService()

    with (
        patch("src.app.workline.services.workline_service.settings.APP_ENV", "prod"),
        pytest.raises(BadRequestException, match="SIMULATION 运行模式只能在 dev/test 环境启用"),
    ):
        service._validate_run_mode({"run_mode": WorkLineRunMode.SIMULATION})


def test_workline_service_allows_simulation_in_dev_and_test() -> None:
    """开发/测试环境允许开启 SIMULATION 沙箱模式。"""

    service = WorkLineService()

    with patch("src.app.workline.services.workline_service.settings.APP_ENV", "dev"):
        service._validate_run_mode({"run_mode": WorkLineRunMode.SIMULATION})

    with patch("src.app.workline.services.workline_service.settings.APP_ENV", "test"):
        service._validate_run_mode({"run_mode": WorkLineRunMode.SIMULATION})


@pytest.mark.asyncio
async def test_workline_service_rejects_removed_smt_classifier_plugin(db_session) -> None:
    """旧 SMT 插件已完全清理，重写注册前应拒绝继续绑定。"""

    workline = make_workline()
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with (
        patch(
            "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
            AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException, match="不支持的工作线插件"),
    ):
        _ = await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"plugin_key": "smt_classifier", "version": workline.version},
        )


@pytest.mark.asyncio
async def test_workline_service_rejects_removed_full_box_exchange_plugin(db_session) -> None:
    """旧满箱交换插件已被货架任务模型替代，应拒绝继续绑定。"""

    workline = make_workline()
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with (
        patch(
            "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
            AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException, match="不支持的工作线插件"),
    ):
        _ = await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"plugin_key": "smt_full_box_exchange", "version": workline.version},
        )


@pytest.mark.asyncio
async def test_workline_service_create_rejects_removed_smt_classifier_plugin(db_session) -> None:
    """创建工作线时也应拒绝已清理的 SMT 插件。"""

    service = WorkLineService()
    with (
        patch(
            "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
            AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException, match="不支持的工作线插件"),
    ):
        _ = await service.create(
            db_session,
            {
                "line_code": "WL-SMT-003",
                "line_name": "粗分机#3",
                "line_type": LineType.AUTO,
                "plugin_key": "smt_classifier",
            },
        )


@pytest.mark.asyncio
async def test_workline_service_rejects_contract_version_for_removed_smt_classifier(db_session) -> None:
    """旧 SMT 插件未注册时，带 contract_version 的创建仍按不支持插件拒绝。"""

    service = WorkLineService()
    with (
        patch(
            "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
            AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException, match="不支持的工作线插件"),
    ):
        _ = await service.create(
            db_session,
            {
                "line_code": "WL-SMT-004",
                "line_name": "粗分机#4",
                "line_type": LineType.AUTO,
                "plugin_key": "smt_classifier",
                "contract_version": "manual-override",
            },
        )


@pytest.mark.asyncio
async def test_workline_service_create_rejects_unknown_plugin_key(db_session) -> None:
    """创建工作线时仍应拒绝未知插件标识。"""

    service = WorkLineService()
    with (
        patch(
            "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
            AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException, match="不支持的工作线插件"),
    ):
        _ = await service.create(
            db_session,
            {
                "line_code": "WL-SMT-004",
                "line_name": "粗分机#4",
                "line_type": LineType.AUTO,
                "plugin_key": "unknown_plugin",
            },
        )


@pytest.mark.asyncio
async def test_workline_service_create_keeps_rough_sorter_as_inactive_draft_without_topology(db_session) -> None:
    """创建 rough_sorter 草稿不做拓扑强校验，且默认保持未启用。"""

    service = WorkLineService()
    with patch(
        "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
        AsyncMock(return_value=None),
    ):
        workline = await service.create(
            db_session,
            {
                "line_code": "WL-ROUGH-DRAFT",
                "line_name": "粗分机草稿",
                "line_type": LineType.AUTO,
                "plugin_key": "rough_sorter",
            },
        )

    assert workline is not None
    assert workline.is_active is False
    assert workline.contract_version == "rough_sorter.v1"


@pytest.mark.asyncio
async def test_workline_service_update_allows_rough_sorter_draft_without_topology(db_session) -> None:
    """普通保存只校验基础配置，不因设备拓扑未完整而拒绝。"""

    workline = make_workline()
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with patch(
        "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
        AsyncMock(return_value=None),
    ):
        updated = await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"plugin_key": "rough_sorter", "version": workline.version},
        )

    assert updated is not None
    assert updated.plugin_key == "rough_sorter"
    assert updated.contract_version == "rough_sorter.v1"


@pytest.mark.asyncio
async def test_workline_service_plain_update_rejects_is_active(db_session) -> None:
    """普通 CRUD 不允许直接写 is_active。"""

    workline = make_workline()
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException, match="启用状态只能通过专用操作修改"):
        await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"is_active": True, "version": workline.version},
        )


@pytest.mark.asyncio
async def test_workline_service_rejects_plugin_config_update_when_active(db_session) -> None:
    """启用态 WorkLine 的插件合同配置不能通过普通 CRUD 热切换。"""

    workline = WorkLine(
        line_code="WL-ROUGH-ACTIVE-CONFIG",
        line_name="粗分机启用配置",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException, match="已启用作业线"):
        await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"run_mode": WorkLineRunMode.MANUAL, "version": workline.version},
        )


@pytest.mark.asyncio
async def test_workline_service_rejects_line_code_update_when_active(db_session) -> None:
    """启用态 WorkLine 的运行身份编码不能通过普通 CRUD 热切换。"""

    workline = WorkLine(
        line_code="WL-ROUGH-ACTIVE-CODE",
        line_name="粗分机启用编码",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with (
        patch(
            "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
            AsyncMock(return_value=None),
        ),
        pytest.raises(BusinessException, match="已启用作业线"),
    ):
        await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"line_code": "WL-ROUGH-ACTIVE-CODE-NEW", "version": workline.version},
        )


@pytest.mark.asyncio
async def test_workline_service_active_line_name_update_keeps_existing_contract_version(db_session) -> None:
    """启用态 WorkLine 修改非运行配置字段时，不能被 manifest 默认值顺手热切换合同版本。"""

    workline = WorkLine(
        line_code="WL-ROUGH-ACTIVE-RENAME",
        line_name="粗分机启用重命名",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with (
        patch(
            "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
            AsyncMock(return_value=None),
        ),
        patch("src.app.workline.services.workline_service.get_plugin_contract_version", return_value="rough_sorter.v2"),
    ):
        updated = await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"line_name": "粗分机启用重命名后", "version": workline.version},
        )

    assert updated is not None
    assert updated.line_name == "粗分机启用重命名后"
    assert updated.contract_version == "rough_sorter.v1"


@pytest.mark.asyncio
async def test_workline_service_locks_workline_before_plugin_config_update_guard(db_session) -> None:
    """插件合同配置更新必须和启用流程竞争同一把 WorkLine 锁。"""

    workline = WorkLine(
        line_code="WL-ROUGH-CONFIG-LOCK",
        line_name="粗分机配置锁",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    service.repo.get_for_update = AsyncMock(wraps=service.repo.get_for_update)  # type: ignore[method-assign]

    with pytest.raises(BusinessException, match="已启用作业线"):
        await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"run_mode": WorkLineRunMode.MANUAL, "version": workline.version},
        )

    service.repo.get_for_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_configuration_status_reports_missing_plugin_and_role_blockers(db_session) -> None:
    """配置状态返回结构化检查项，供前端展示 blocker。"""

    workline = make_workline()
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    status = await service.configuration_status(db_session, workline.id)  # type: ignore[arg-type]

    assert status.can_activate is False
    assert status.checks[0].code == "PLUGIN_CONFIGURED"
    assert status.checks[0].status == "FAIL"
    assert status.checks[0].severity == "BLOCKER"

    workline.plugin_key = "rough_sorter"
    workline.contract_version = "rough_sorter.v1"
    await db_session.commit()

    status = await service.configuration_status(db_session, workline.id)  # type: ignore[arg-type]
    role_checks = {check.context["role"]: check for check in status.checks if check.code == "ROLE_REQUIREMENT"}
    assert status.can_activate is False
    assert role_checks[ROLE_INPUT_ARM].status == "FAIL"
    assert role_checks[ROLE_CONVEYOR].status == "FAIL"
    assert role_checks[ROLE_OUTPUT_ARM].status == "FAIL"


@pytest.mark.asyncio
async def test_configuration_status_reports_incomplete_command_target_communication(db_session) -> None:
    """命令目标设备缺少 host/port 时，配置状态应直接命名 affected device。"""

    workline = WorkLine(
        line_code="WL-ROUGH-COMM-CHECK",
        line_name="粗分机通信检查",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    for index, role in enumerate((ROLE_INPUT_ARM, ROLE_CONVEYOR, ROLE_OUTPUT_ARM), start=1):
        db_session.add(
            Device(
                device_code=f"ROUGH-COMM-DEV-{index}",
                device_name=f"粗分机通信设备{index}",
                work_line_id=workline.id,
                device_role=role,
                role_index=1,
                capabilities_json={} if role == ROLE_CONVEYOR else {"status_path": "/api/v1/device/status"},
                host=None if role == ROLE_CONVEYOR else "mock-ecs",
                port=None if role == ROLE_CONVEYOR else 8010,
            )
        )
    await db_session.commit()
    await _add_rough_sorter_rack_positions(db_session, workline)

    service = WorkLineService()
    status = await service.configuration_status(db_session, workline.id)  # type: ignore[arg-type]

    communication_checks = [
        check for check in status.checks if check.code == "COMMAND_TARGET_COMMUNICATION" and check.status == "FAIL"
    ]
    assert status.can_activate is False
    assert communication_checks
    assert communication_checks[0].severity == "BLOCKER"
    assert communication_checks[0].context["device_code"] == "ROUGH-COMM-DEV-2"
    assert set(communication_checks[0].context["missing_fields"]) == {"host", "port"}
    assert communication_checks[0].context["status_path"] == "/api/v1/device/status"


@pytest.mark.asyncio
async def test_configuration_status_reports_invalid_command_target_capabilities_as_blocker(db_session) -> None:
    """命令目标设备能力配置无效时，配置状态应返回 blocker 而不是抛运行时异常。"""

    workline = WorkLine(
        line_code="WL-ROUGH-CAPABILITY-BAD",
        line_name="粗分机能力配置错误",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    devices: list[Device] = []
    for index, role in enumerate((ROLE_INPUT_ARM, ROLE_CONVEYOR, ROLE_OUTPUT_ARM), start=1):
        device = Device(
            device_code=f"ROUGH-CAP-BAD-DEV-{index}",
            device_name=f"粗分机能力配置设备{index}",
            work_line_id=workline.id,
            device_role=role,
            role_index=1,
            capabilities_json={},
            host="mock-ecs",
            port=8010,
        )
        devices.append(device)
        db_session.add(device)
    await db_session.commit()
    await db_session.execute(
        update(Device).where(Device.id == devices[0].id).values(capabilities_json="not-a-dict")  # type: ignore[arg-type]
    )
    await db_session.commit()

    service = WorkLineService()
    status = await service.configuration_status(db_session, workline.id)  # type: ignore[arg-type]

    capability_checks = [
        check for check in status.checks if check.code == "COMMAND_TARGET_CAPABILITY_CONFIG" and check.status == "FAIL"
    ]
    assert status.can_activate is False
    assert capability_checks
    assert capability_checks[0].severity == "BLOCKER"
    assert capability_checks[0].context["device_code"] == "ROUGH-CAP-BAD-DEV-1"
    assert capability_checks[0].context["capabilities_error"] == "Input should be a valid dictionary"


@pytest.mark.asyncio
async def test_activate_simulation_does_not_block_on_command_target_communication(db_session) -> None:
    """SIMULATION 沙箱模式缺少真实 ECS 通信配置时，不应被通信检查 blocker 阻断。"""

    workline = WorkLine(
        line_code="WL-ROUGH-SIM-COMM-CHECK",
        line_name="粗分机 SIM 通信检查",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        run_mode=WorkLineRunMode.SIMULATION,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    for index, role in enumerate((ROLE_INPUT_ARM, ROLE_CONVEYOR, ROLE_OUTPUT_ARM), start=1):
        db_session.add(
            Device(
                device_code=f"ROUGH-SIM-COMM-DEV-{index}",
                device_name=f"粗分机 SIM 通信设备{index}",
                work_line_id=workline.id,
                device_role=role,
                role_index=1,
                capabilities_json={},
                host=None,
                port=None,
            )
        )
    await db_session.commit()
    await _add_rough_sorter_rack_positions(db_session, workline)

    service = WorkLineService()
    status = await service.configuration_status(db_session, workline.id)  # type: ignore[arg-type]

    communication_checks = [
        check for check in status.checks if check.code == "COMMAND_TARGET_COMMUNICATION" and check.status == "WARN"
    ]
    assert status.can_activate is True
    assert communication_checks
    assert communication_checks[0].severity == "WARNING"
    assert communication_checks[0].context["device_code"] == "ROUGH-SIM-COMM-DEV-1"

    with patch("src.app.workline.services.workline_service.settings.APP_ENV", "dev"):
        activated = await service.activate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    assert activated is not None
    assert activated.is_active is True


@pytest.mark.asyncio
async def test_activate_succeeds_only_after_configuration_checks_pass(db_session) -> None:
    """activate 复用配置预检，角色完整后才允许启用。"""

    workline = WorkLine(
        line_code="WL-ROUGH-ACTIVATE",
        line_name="粗分机启用",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException, match="配置预检未通过"):
        await service.activate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    for index, role in enumerate((ROLE_INPUT_ARM, ROLE_CONVEYOR, ROLE_OUTPUT_ARM), start=1):
        db_session.add(
            Device(
                device_code=f"ROUGH-DEV-{index}",
                device_name=f"粗分机设备{index}",
                work_line_id=workline.id,
                device_role=role,
                role_index=1,
                capabilities_json={"status_path": "/api/v1/device/status"},
                host="mock-ecs",
                port=8010,
            )
        )
    await db_session.commit()
    await _add_rough_sorter_rack_positions(db_session, workline)
    await db_session.refresh(workline)

    activated = await service.activate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    assert activated is not None
    assert activated.is_active is True


@pytest.mark.asyncio
async def test_activate_locks_workline_before_configuration_checks(db_session) -> None:
    """启用流程必须先锁定 WorkLine，避免拓扑预检和启用写入之间并发修改。"""

    workline = WorkLine(
        line_code="WL-ROUGH-ACTIVATE-LOCK",
        line_name="粗分机启用锁",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    for index, role in enumerate((ROLE_INPUT_ARM, ROLE_CONVEYOR, ROLE_OUTPUT_ARM), start=1):
        db_session.add(
            Device(
                device_code=f"ROUGH-LOCK-DEV-{index}",
                device_name=f"粗分机锁设备{index}",
                work_line_id=workline.id,
                device_role=role,
                role_index=1,
                capabilities_json={"status_path": "/api/v1/device/status"},
                host="mock-ecs",
                port=8010,
            )
        )
    await db_session.commit()
    await _add_rough_sorter_rack_positions(db_session, workline)
    await db_session.refresh(workline)

    service = WorkLineService()
    service.repo.get_for_update = AsyncMock(wraps=service.repo.get_for_update)  # type: ignore[method-assign]

    await service.activate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    service.repo.get_for_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_deactivate_blocks_when_workline_has_unfinished_sessions(db_session) -> None:
    """停用前只返回未完成负载摘要，不全量加载运行对象。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DEACTIVATE",
        line_name="粗分机停用",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)
    db_session.add(
        WorklineSession(
            session_code="S-UNFINISHED",
            workline_id=workline.id,  # type: ignore[arg-type]
            plugin_key="rough_sorter",
            status=SessionStatus.RUNNING,
        )
    )
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException) as exc_info:
        await service.deactivate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    assert "存在未完成运行负载" in exc_info.value.message
    assert exc_info.value.detail is not None
    assert exc_info.value.detail["workload"]["count"] == 1


@pytest.mark.asyncio
async def test_deactivate_blocks_when_workline_has_active_command(db_session) -> None:
    """停用前必须阻止仍在派发或执行中的设备指令。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DEACTIVATE-CMD",
        line_name="粗分机停用指令",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    device = Device(
        device_code="ROUGH-CMD-DEV",
        device_name="粗分机指令设备",
        work_line_id=None,
        device_role=ROLE_INPUT_ARM,
        capabilities_json={},
    )
    db_session.add_all([workline, device])
    await db_session.commit()
    await db_session.refresh(workline)
    await db_session.refresh(device)

    db_session.add(
        DeviceCommand(
            command_code="CMD-ACTIVE-DEACTIVATE",
            device_id=device.id,  # type: ignore[arg-type]
            workline_id=workline.id,  # type: ignore[arg-type]
            task_type="PICK",
            status=CommandStatus.SENT,
        )
    )
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException) as exc_info:
        await service.deactivate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    assert "存在未完成运行负载" in exc_info.value.message
    assert exc_info.value.detail is not None
    assert exc_info.value.detail["workload"]["by_type"]["commands"] == 1


@pytest.mark.asyncio
async def test_deactivate_blocks_when_workline_has_active_outbox(db_session) -> None:
    """停用前必须阻止仍未终态的 SystemOutbox。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DEACTIVATE-OUTBOX",
        line_name="粗分机停用发件箱",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    db_session.add(
        SystemOutbox(
            workline_id=workline.id,  # type: ignore[arg-type]
            operation_domain="WORKLINE",
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            dispatch_key="OUTBOX-ACTIVE-DEACTIVATE",
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ROUGH-OUTBOX-DEV",
            payload_json={},
            status=SystemOutboxStatus.NEW,
        )
    )
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException) as exc_info:
        await service.deactivate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    assert "存在未完成运行负载" in exc_info.value.message
    assert exc_info.value.detail is not None
    assert exc_info.value.detail["workload"]["by_type"]["outboxes"] == 1


@pytest.mark.asyncio
async def test_deactivate_allows_sent_outbox_history(db_session) -> None:
    """已派发成功的 SystemOutbox 历史不应永久阻止停用。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DEACTIVATE-SENT-OUTBOX",
        line_name="粗分机停用已派发历史",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    db_session.add(
        SystemOutbox(
            workline_id=workline.id,  # type: ignore[arg-type]
            operation_domain="WORKLINE",
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            dispatch_key="OUTBOX-SENT-HISTORY-DEACTIVATE",
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ROUGH-SENT-HISTORY-DEV",
            payload_json={},
            status=SystemOutboxStatus.SENT,
        )
    )
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    deactivated = await service.deactivate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    assert deactivated is not None
    assert deactivated.is_active is False


@pytest.mark.asyncio
async def test_deactivate_blocks_when_workline_has_active_inbox(db_session) -> None:
    """停用前必须阻止仍待处理或可重试的 WorklineInbox。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DEACTIVATE-INBOX",
        line_name="粗分机停用收件箱",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    db_session.add(
        WorklineInbox(
            kind=InboxKind.DEVICE_EVENT,
            source_system=SourceSystem.DEVICE,
            source_message_id="INBOX-ACTIVE-DEACTIVATE",
            workline_id=workline.id,  # type: ignore[arg-type]
            payload_json={},
            status=InboxStatus.RETRY,
        )
    )
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException) as exc_info:
        await service.deactivate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    assert "存在未完成运行负载" in exc_info.value.message
    assert exc_info.value.detail is not None
    assert exc_info.value.detail["workload"]["by_type"]["inboxes"] == 1


@pytest.mark.asyncio
async def test_deactivate_blocks_when_workline_has_active_runtime_hold(db_session) -> None:
    """停用前必须阻止不一定关联 session 的 active blocking RuntimeHold。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DEACTIVATE-HOLD",
        line_name="粗分机停用 Hold",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    await RuntimeHoldRepository().create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.SAFETY_ESTOP,
        workline_id=workline.id,  # type: ignore[arg-type]
        source_kind="SAFETY_ESTOP",
        source_reason="ESTOP_PRESSED",
        source_idempotency_key="safety-estop:deactivate-hold",
    )
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException) as exc_info:
        await service.deactivate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    assert "存在未完成运行负载" in exc_info.value.message
    assert exc_info.value.detail is not None
    assert exc_info.value.detail["workload"]["by_type"]["runtime_holds"] == 1


@pytest.mark.asyncio
async def test_deactivate_locks_workline_before_workload_checks(db_session) -> None:
    """停用流程必须先锁定 WorkLine，避免负载检查和停用写入之间并发新增运行负载。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DEACTIVATE-LOCK",
        line_name="粗分机停用锁",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    service.repo.get_for_update = AsyncMock(wraps=service.repo.get_for_update)  # type: ignore[method-assign]

    await service.deactivate(db_session, workline.id, version=workline.version)  # type: ignore[arg-type]

    service.repo.get_for_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_rejects_active_workline(db_session) -> None:
    """删除 WorkLine 不能绕过启用态停用保护。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DELETE-ACTIVE",
        line_name="粗分机删除启用线",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=True,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException, match="停用"):
        await service.delete(db_session, workline.id)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_delete_rejects_workline_with_unfinished_workload(db_session) -> None:
    """删除 WorkLine 不能绕过未完成运行负载检查。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DELETE-WORKLOAD",
        line_name="粗分机删除负载线",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=False,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    db_session.add(
        SystemOutbox(
            workline_id=workline.id,  # type: ignore[arg-type]
            operation_domain="WORKLINE",
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            dispatch_key="OUTBOX-DELETE-WORKLOAD",
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ROUGH-DELETE-WORKLOAD-DEV",
            payload_json={},
            status=SystemOutboxStatus.NEW,
        )
    )
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException, match="未完成运行负载"):
        await service.delete(db_session, workline.id)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_delete_rejects_workline_with_active_runtime_hold(db_session) -> None:
    """删除 WorkLine 不能绕过 active blocking RuntimeHold。"""

    workline = WorkLine(
        line_code="WL-ROUGH-DELETE-HOLD",
        line_name="粗分机删除 Hold",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v1",
        is_active=False,
    )
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    await RuntimeHoldRepository().create_open_hold(
        db_session,
        hold_type=RuntimeHoldType.SAFETY_ESTOP,
        workline_id=workline.id,  # type: ignore[arg-type]
        source_kind="SAFETY_ESTOP",
        source_reason="ESTOP_PRESSED",
        source_idempotency_key="safety-estop:delete-hold",
    )
    await db_session.commit()
    await db_session.refresh(workline)

    service = WorkLineService()
    with pytest.raises(BusinessException) as exc_info:
        await service.delete(db_session, workline.id)  # type: ignore[arg-type]

    assert "未完成运行负载" in exc_info.value.message
    assert exc_info.value.detail is not None
    assert exc_info.value.detail["workload"]["by_type"]["runtime_holds"] == 1


def test_rough_sorter_rejects_duplicate_unique_roles() -> None:
    """粗分机三个关键角色都是唯一角色。"""

    with pytest.raises(BadRequestException, match="最多 1 个设备"):
        validate_workline_plugin_assignment(
            "rough_sorter",
            make_workline(),
            [
                make_device(1, ROLE_INPUT_ARM),
                make_device(2, ROLE_INPUT_ARM),
                make_device(3, ROLE_CONVEYOR),
                make_device(4, ROLE_OUTPUT_ARM),
            ],
        )
