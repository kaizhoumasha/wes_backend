"""WorkLine 插件配置校验测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from src.app.device.models import Device
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.sys.models.outbox import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.workline.models import InboxKind, InboxStatus, LineType, SourceSystem, WorkLine, WorkLineRunMode
from src.app.workline.models.inbox import WorklineInbox
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


def test_workline_service_lists_rough_sorter_plugin_option() -> None:
    """粗分机插件注册后，插件下拉选项应暴露新合同版本。"""

    service = WorkLineService()

    options = service.list_plugin_options()

    assert [option.plugin_key for option in options] == ["rough_sorter"]
    assert options[0].default_contract_version == "rough_sorter.v1"


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
                capabilities_json={},
            )
        )
    await db_session.commit()
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
                capabilities_json={},
            )
        )
    await db_session.commit()
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
