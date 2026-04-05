"""WorkLine 插件配置校验测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from src.app.device.models.device import Device
from src.app.workline.models import LineType, WorkLine
from src.app.workline.services.workline_service import WorkLineService
from src.core.exceptions import BadRequestException
from src.workline_plugins.simplified_smt_plugin import SimplifiedSmtPlugin


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
        plugin_key="simplified_smt",
    )

    assert workline.plugin_class is SimplifiedSmtPlugin
    # 简化插件使用 @step 装饰器，无独立状态机类
    assert workline.state_machine_class is None


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
            {"plugin_key": "simplified_smt", "version": workline.version},
        )

    assert result is not None
    assert result.plugin_key == "simplified_smt"
    assert result.plugin_class is SimplifiedSmtPlugin


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
                "plugin_key": "simplified_smt",
            },
        )

    assert result is not None
    assert result.plugin_key == "simplified_smt"


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
