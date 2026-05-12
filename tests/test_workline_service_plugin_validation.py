"""WorkLine 插件配置校验测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from src.app.device.models.device import Device
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.services.workline_service import WorkLineService
from src.core.exceptions import BadRequestException
from src.workline_plugins.smt_classifier import SmtClassifierContext, SmtClassifierPlugin


def make_workline() -> WorkLine:
    """创建测试作业线。"""

    return WorkLine(
        line_code="WL-SMT-001",
        line_name="粗分机#1",
        line_type=LineType.AUTO,
    )


def make_device(
    *,
    work_line_id: int,
    device_code: str,
    device_name: str,
    device_role: str,
) -> Device:
    """创建测试设备。"""

    return Device(
        device_code=device_code,
        device_name=device_name,
        work_line_id=work_line_id,
        device_role=device_role,
    )


def test_workline_model_resolves_runtime_plugin_classes() -> None:
    """WorkLine 应能按 plugin_key 解析运行时插件类。"""

    workline = WorkLine(
        line_code="WL-SMT-002",
        line_name="粗分机#2",
        line_type=LineType.AUTO,
        plugin_key="smt_classifier",
    )

    assert workline.plugin_class is SmtClassifierPlugin
    assert not hasattr(workline, "state" + "_machine_class")
    assert workline.plugin_definition is not None
    assert workline.plugin_definition.manifest.plugin_key == "smt_classifier"
    assert workline.plugin_definition.manifest.contract_version == "1.0"
    assert workline.plugin_definition.manifest.context_model is SmtClassifierContext


def test_workline_service_lists_plugin_options_from_registry() -> None:
    """作业线插件下拉选项应来自插件注册表。"""

    service = WorkLineService()

    options = service.list_plugin_options()

    assert options
    smt_option = next(option for option in options if option.plugin_key == "smt_classifier")
    assert smt_option.label == "smt_classifier"
    assert smt_option.default_contract_version == "1.0"
    assert smt_option.contract_versions == ["1.0"]


def test_workline_run_mode_defaults_to_auto() -> None:
    """WorkLine 默认运行模式应是 AUTO。"""

    workline = make_workline()

    assert workline.run_mode == WorkLineRunMode.AUTO
    assert workline.resolved_runtime_config["run_mode"] == WorkLineRunMode.AUTO.value


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
async def test_workline_service_accepts_simplified_plugin(db_session) -> None:
    """设备角色满足插件要求时，应允许绑定插件。"""

    workline = make_workline()
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    db_session.add_all(
        [
            make_device(
                work_line_id=workline.id,  # type: ignore[arg-type]
                device_code="ARM01",
                device_name="进料机械臂",
                device_role="INPUT_ARM",
            ),
            make_device(
                work_line_id=workline.id,  # type: ignore[arg-type]
                device_code="ARM02",
                device_name="出料机械臂",
                device_role="OUTPUT_ARM",
            ),
            make_device(
                work_line_id=workline.id,  # type: ignore[arg-type]
                device_code="PIPELINE01",
                device_name="流水线",
                device_role="CONVEYOR",
            ),
        ]
    )
    await db_session.commit()

    service = WorkLineService()
    with patch(
        "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
        AsyncMock(return_value=None),
    ):
        result = await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"plugin_key": "smt_classifier", "version": workline.version},
        )

    assert result is not None
    assert result.plugin_key == "smt_classifier"
    assert result.plugin_class is SmtClassifierPlugin


@pytest.mark.asyncio
async def test_workline_service_rejects_plugin_when_required_device_role_missing(db_session) -> None:
    """插件 manifest 要求的设备角色缺失时，应拒绝绑定。"""

    workline = make_workline()
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)

    db_session.add_all(
        [
            make_device(
                work_line_id=workline.id,  # type: ignore[arg-type]
                device_code="ARM01",
                device_name="进料机械臂",
                device_role="INPUT_ARM",
            ),
            make_device(
                work_line_id=workline.id,  # type: ignore[arg-type]
                device_code="PIPELINE01",
                device_name="流水线",
                device_role="CONVEYOR",
            ),
        ]
    )
    await db_session.commit()

    service = WorkLineService()
    with (
        patch(
            "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
            AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException, match="角色 OUTPUT_ARM 至少 1 个设备"),
    ):
        _ = await service.update(
            db_session,
            workline.id,  # type: ignore[arg-type]
            {"plugin_key": "smt_classifier", "version": workline.version},
        )


@pytest.mark.asyncio
async def test_workline_service_create_allows_plugin_before_devices_are_bound(db_session) -> None:
    """创建工作线时允许先保存 plugin_key，拓扑校验留到后续更新。"""

    service = WorkLineService()
    with patch(
        "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
        AsyncMock(return_value=None),
    ):
        result = await service.create(
            db_session,
            {
                "line_code": "WL-SMT-003",
                "line_name": "粗分机#3",
                "line_type": LineType.AUTO,
                "plugin_key": "smt_classifier",
            },
        )

    assert result is not None
    assert result.plugin_key == "smt_classifier"
    assert result.contract_version == "1.0"


@pytest.mark.asyncio
async def test_workline_service_rejects_manual_contract_version_mismatch(db_session) -> None:
    """契约版本是插件 manifest 注解，不允许手工写入不匹配值。"""

    service = WorkLineService()
    with (
        patch(
            "src.app.sys.services.audit_service.audit_log_service.create_operation_log",
            AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException, match=r"契约版本必须为 1\.0"),
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
