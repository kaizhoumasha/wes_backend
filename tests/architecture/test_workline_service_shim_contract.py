"""WorkLine service shim 契约测试。"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


def test_runtime_reconciliation_shim_aliases_impl_module():
    """WorkLine 配置域收口:runtime_reconciliation_service shim 已物理删除,impl 仍可直连。"""
    impl_module = importlib.import_module(
        "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl"
    )
    marker = object()

    assert impl_module.workline_runtime_reconciliation_service is not None

    with patch(
        "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl.workline_diagnostic_service",
        new=marker,
    ):
        assert impl_module.workline_diagnostic_service is marker


def test_runtime_reconciliation_facade_removed_after_facade_retirement():
    """RuntimeReconciliationFacade 收口:`RuntimeReconciliationFacade` 必须物理删除。"""
    # 原 facade 模块在 runtime/orchestration/services/ 下,模块移除后
    # importlib.import_module 必须抛 ModuleNotFoundError。
    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("src.app.runtime.orchestration.services.runtime_reconciliation_service")

    # Facade 类与单例符号必须不再暴露在 runtime/orchestration.services 顶层。
    services_module = importlib.import_module("src.app.runtime.orchestration.services")
    assert not hasattr(services_module, "RuntimeReconciliationFacade")
    assert not hasattr(services_module, "runtime_reconciliation_facade")
    assert "RuntimeReconciliationFacade" not in services_module.__all__
    assert "runtime_reconciliation_facade" not in services_module.__all__


# WorkLine 配置域收口:workline 域退化为纯配置域(WorkLine CRUD + manifest + plane scene +
# diagnostic_service keep-contract + rack_position_service 配置能力 + domain/ +
# plugins/)。任何运行态 service / model / repository / v1 router 文件必须物理删除。
_WORKLINE_RUNTIME_REMOVED_SERVICES = (
    "runtime_hold_creation_service",
    "runtime_hold_query_service",
    "runtime_hold_release_service",
    "runtime_query_service",
    "inbox_batch_processor",
    "inbox_service",
    "dispatch_attempt_service",
    "object_transition_event_service",
    "operation_service",
    "outbox_dispatch_service",
    "smt_inbound_handoff_service",
    "timeline_sequence_service",
    "trace_query_service",
    "trace_resource_view_builder",
    "trace_response_builder",
)

_WORKLINE_RUNTIME_REMOVED_REPOSITORIES = (
    "inbox_repository",
    "dispatch_attempt_repository",
    "object_transition_event_repository",
    "runtime_hold_repository",
    "session_repository",
    "smt_inbound_handoff_repository",
    "bin_cell_reservation_repository",
    "material_unit_repository",
    "rack_position_repository",
    "diagnostic_repository",
)

_WORKLINE_RUNTIME_REMOVED_MODELS = (
    "runtime",
    "runtime_hold",
    "runtime_hold_api",
    "inbox",
    "dispatch_attempt",
    "object_transition_event",
    "operation",
    "session",
    "timeline",
    "rack_position",
    "diagnostic",
    "smt_inbound_handoff",
    "bin_cell_reservation",
    "material_unit",
)
# `safety.py` 不在删除列表:WorklineSafetyIncident 表是 safety_service 配置域
# 审计表,safety_service 仍保留在 workline 域。WorkLine runtime status enum
# 已迁入 runtime/orchestration 的原生投影模型。

_WORKLINE_CONFIG_KEPT_MODELS = ("safety",)

_WORKLINE_CONFIG_KEPT_REPOSITORIES = ("safety_incident_repository",)

_WORKLINE_RUNTIME_REMOVED_V1_ROUTERS = (
    "runtime",
    "runtime_hold",
    "trace",
    "inbound_handoff",
)


def _file_exists(relative_path: str) -> bool:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent.parent / relative_path).exists()


def test_workline_services_shrunk_to_config_crud_after_runtime_split():
    """WorkLine 配置域收口:workline/services/ 下运行态 service 必须物理删除。"""
    for name in _WORKLINE_RUNTIME_REMOVED_SERVICES:
        assert not _file_exists(f"src/app/workline/services/{name}.py"), (
            f"WorkLine 配置域收口:workline 运行态 service 必须物理删除,遗留: {name}.py"
        )


def test_workline_repositories_shrunk_to_workline_only_after_runtime_split():
    """WorkLine 配置域收口:workline/repositories/ 下运行态 repository 必须物理删除。"""
    for name in _WORKLINE_RUNTIME_REMOVED_REPOSITORIES:
        assert not _file_exists(f"src/app/workline/repositories/{name}.py"), (
            f"WorkLine 配置域收口:workline 运行态 repository 必须物理删除,遗留: {name}.py"
        )


def test_workline_kept_models_preserved_after_runtime_split():
    """WorkLine 配置域收口:safety.py 必须保留(承载 WorkLine 安全事件审计模型)。"""
    assert _file_exists("src/app/workline/models/safety.py"), (
        "WorkLine 配置域收口:safety.py 必须保留 — safety_service 配置域审计表仍依赖"
    )


def test_workline_kept_repositories_preserved_after_runtime_split():
    """WorkLine 配置域收口:safety_incident_repository 必须保留(支撑 safety_service 配置域)。"""
    assert _file_exists("src/app/workline/repositories/safety_incident_repository.py"), (
        "WorkLine 配置域收口:safety_incident_repository 必须保留 — safety_service 配置域审计表仍依赖"
    )


def test_workline_models_shrunk_to_workline_only_after_runtime_split():
    """WorkLine 配置域收口:workline/models/ 下运行态 model 文件必须物理删除。

    safety.py 例外保留,见 `_WORKLINE_CONFIG_KEPT_MODELS`。
    """
    for name in _WORKLINE_RUNTIME_REMOVED_MODELS:
        assert not _file_exists(f"src/app/workline/models/{name}.py"), (
            f"WorkLine 配置域收口:workline 运行态 model 必须物理删除,遗留: {name}.py"
        )


def test_workline_v1_routers_shrunk_after_runtime_split():
    """WorkLine 配置域收口:workline/v1/ 下运行时 router 必须物理删除。"""
    for name in _WORKLINE_RUNTIME_REMOVED_V1_ROUTERS:
        assert not _file_exists(f"src/app/workline/v1/{name}.py"), (
            f"WorkLine 配置域收口:workline 运行态 v1 router 必须物理删除,遗留: {name}.py"
        )


def test_workline_runtime_reconciliation_shim_alias_removed_after_runtime_split():
    """WorkLine 配置域收口:runtime_reconciliation_service.py shim 必须物理删除。"""
    assert not _file_exists("src/app/workline/services/runtime_reconciliation_service.py"), (
        "WorkLine 配置域收口:workline runtime_reconciliation_service shim 必须物理删除"
    )


def test_device_command_gateway_module_moved_to_runtime_service_boundary():
    """WorkLine device gateway 收口。

    device_command_gateway 必须从 workline 域迁入 runtime/orchestration。
    """
    import importlib

    runtime_module = importlib.import_module("src.app.runtime.orchestration.services.device_command_gateway")
    assert hasattr(runtime_module, "DeviceCommandGateway"), (
        "WorkLine device gateway 收口:runtime/orchestration/services/device_command_gateway 必须暴露 DeviceCommandGateway 类"
    )
    assert hasattr(runtime_module, "device_command_gateway"), (
        "WorkLine device gateway 收口:runtime/orchestration/services/device_command_gateway 必须暴露单例符号"
    )
    assert not _file_exists("src/app/workline/services/device_command_gateway.py"), (
        "WorkLine device gateway 收口:workline/services/device_command_gateway.py 必须物理删除"
    )


def test_workline_services_module_does_not_export_device_command_gateway_after_runtime_split():
    """WorkLine device gateway 收口:workline.services 顶层不再导出 device_command_gateway 符号。"""
    import importlib

    workline_services = importlib.import_module("src.app.workline.services")
    assert not hasattr(workline_services, "device_command_gateway"), (
        "WorkLine device gateway 收口:workline.services 必须不再暴露 device_command_gateway 符号"
    )
    assert not hasattr(workline_services, "DeviceCommandGateway"), (
        "WorkLine device gateway 收口:workline.services 必须不再暴露 DeviceCommandGateway 类"
    )


def test_workline_service_config_only_after_runtime_split():
    """WorkLine device gateway 收口:workline_service 配置域保留(无运行态方法)。

    F-4 行为验证:不再用 hasattr 存在性守卫,改为验证方法为 async callable
    + 签名契约(db 入参 + 返回类型注解),确保配置域方法形态稳定。
    """
    import asyncio
    import importlib
    import inspect
    import typing

    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    workline_service_singleton = workline_service_module.workline_service

    # 配置域方法形态契约:name → 期望返回类型(单类型)或 union 成员集合
    from src.app.workline.models.workline import WorkLine, WorkLineConfigurationStatus

    expected: dict[str, object] = {
        "create": (WorkLine, type(None)),
        "update": (WorkLine, type(None)),
        "delete": (bool, type(None)),
        "activate": (WorkLine, type(None)),
        "deactivate": (WorkLine, type(None)),
        "configuration_status": WorkLineConfigurationStatus,
    }
    for name, expected_return in expected.items():
        method = getattr(workline_service_singleton, name, None)
        assert method is not None, f"WorkLine device gateway 收口:WorkLineService 必须保留 {name} 配置域方法"
        assert asyncio.iscoroutinefunction(method), (
            f"WorkLine device gateway 收口:WorkLineService.{name} 必须是 async callable(配置域 CRUD 契约)"
        )
        sig = inspect.signature(method)
        assert "db" in sig.parameters, (
            f"WorkLine device gateway 收口:WorkLineService.{name} 签名必须保留 db: AsyncSession 入参"
        )
        return_annotation = sig.return_annotation
        assert return_annotation is not inspect.Parameter.empty, (
            f"WorkLine device gateway 收口:WorkLineService.{name} 必须声明返回类型注解"
        )
        if isinstance(expected_return, tuple):
            union_args = set(typing.get_args(return_annotation))
            assert set(expected_return).issubset(union_args), (
                f"WorkLine device gateway 收口:WorkLineService.{name} 返回注解 {return_annotation} "
                f"必须包含 {expected_return},实际 union args {union_args}"
            )
        else:
            assert return_annotation is expected_return, (
                f"WorkLine device gateway 收口:WorkLineService.{name} 返回注解必须为 {expected_return},实际 {return_annotation}"
            )


# WorkLine service facade 收口:workline.services.__init__ 仅保留当前 19 个真实
# module export。已物理删除的运行态 service 不得继续作为包级导出或延迟加载入口。
#
# 来源:workline 配置域 facade 的实际模块导出,按 service 模块分组维护。
_WORKLINE_SERVICE_REAL_EXPORTS = frozenset(
    {
        # diagnostic_service
        "WorklineDiagnosticService",
        "workline_diagnostic_service",
        # manifest_validator
        "WorkLineManifestActivationValidator",
        "workline_manifest_activation_validator",
        # plane_service
        "WorkLinePlaneService",
        "workline_plane_service",
        # safety_service
        "WorkLineSafetyBlocked",
        "WorkLineSafetyService",
        "workline_safety_service",
        # workline_service
        "WorkLineService",
        "workline_service",
        # migration_inventory_service
        "WorklineMigrationInventoryService",
        "WorklineMigrationInventoryInvariantError",
        "WorklineMigrationInventoryLimitExceeded",
        "workline_migration_inventory_service",
        # migration_matrix_service
        "WorklineMigrationMatrixService",
        "WorklineMigrationMatrixInvariantError",
        "WorklineMigrationMatrixPreflightError",
        "workline_migration_matrix_service",
    }
)


def test_workline_services_init_exports_only_real_modules():
    """WorkLine service facade 收口。

    `workline.services.__init__` 的 `__all__` 必须只包含实际 module export,
    不允许残留已删除运行态 service。
    """
    import importlib

    workline_services = importlib.import_module("src.app.workline.services")

    assert set(workline_services.__all__) == _WORKLINE_SERVICE_REAL_EXPORTS, (
        "WorkLine service facade 收口:__all__ 残留 dead entries。\n"
        f"  期望: {sorted(_WORKLINE_SERVICE_REAL_EXPORTS)}\n"
        f"  实际: {sorted(workline_services.__all__)}"
    )


def test_workline_services_init_has_no_module_level_lazy_loader():
    """已删除的运行态 service 不得通过包级延迟加载器继续暴露。"""
    import importlib

    workline_services = importlib.import_module("src.app.workline.services")

    assert "_LAZY_SHIM_MAP" not in vars(workline_services)
    assert "__getattr__" not in vars(workline_services)


def test_workline_runtime_has_no_legacy_bin_cell_service_name():
    """格位预占服务迁入 runtime 后不得保留带 workline 前缀的旧单例名。"""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    legacy_name = "workline_bin_cell_" + "reservation_service"
    residual_paths = [
        path.relative_to(repo_root)
        for root in ("src", "tests")
        for path in (repo_root / root).rglob("*.py")
        if legacy_name in path.read_text(encoding="utf-8")
    ]

    assert residual_paths == [], f"格位预占服务旧单例名仍有残留: {residual_paths}"


def test_workline_services_init_keeps_real_exports_accessible():
    """真实 module export 仍可直接访问,未知属性使用 Python 默认查找语义。"""
    import importlib

    workline_services = importlib.import_module("src.app.workline.services")

    diagnostic_class = workline_services.WorklineDiagnosticService
    assert diagnostic_class is not None

    with pytest.raises(AttributeError):
        workline_services.inbox_service  # noqa: B018

    with pytest.raises(AttributeError):
        workline_services.never_existed_attribute  # noqa: B018
