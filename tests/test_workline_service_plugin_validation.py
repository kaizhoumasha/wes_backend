"""WorkLine 插件配置校验测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.services.workline_service import WorkLineService
from src.core.exceptions import BadRequestException


def make_workline() -> WorkLine:
    """创建测试作业线。"""

    return WorkLine(
        line_code="WL-SMT-001",
        line_name="粗分机#1",
        line_type=LineType.AUTO,
    )


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


def test_workline_service_lists_no_plugin_options_after_smt_cleanup() -> None:
    """旧 SMT 插件清理后，插件下拉选项为空，等待新插件重写注册。"""

    service = WorkLineService()

    options = service.list_plugin_options()

    assert options == []


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
