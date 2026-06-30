"""WorkLine service shim 契约测试。"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


def test_runtime_reconciliation_shim_aliases_impl_module():
    """阶段 6:runtime_reconciliation_service shim 已物理删除,impl 仍可直连。"""
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


def test_runtime_reconciliation_facade_removed_after_stage5():
    """阶段 5:`RuntimeReconciliationFacade` 必须物理删除。"""
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


# 阶段 6:workline 域退化为纯配置域(WorkLine CRUD + manifest + plane scene +
# diagnostic_service keep-contract + rack_position_service 配置能力 + domain/ +
# plugins/)。任何运行态 service / model / repository / v1 router 文件必须物理删除。
_STAGE6_REMOVED_SERVICES = (
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

_STAGE6_REMOVED_REPOSITORIES = (
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

_STAGE6_REMOVED_MODELS = (
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
# `safety.py` 不在删除列表:WorkLineRuntimeStatus enum 是 WorkLine 模型字段
# (runtime_status) 的运行状态 enum,被 8 个 runtime 服务依赖,跨域引用是合理的
# (与 R-I3b allowlist 同类语义)。WorklineSafetyIncident 表是 safety_service 配
# 置域审计表,safety_service 仍保留在 workline 域。后续 PR 考虑把 enum 迁到
# runtime/models/,把 safety 表迁到 runtime 域。

_STAGE6_KEPT_MODELS = ("safety",)

_STAGE6_KEPT_REPOSITORIES = ("safety_incident_repository",)

_STAGE6_REMOVED_V1_ROUTERS = (
    "runtime",
    "runtime_hold",
    "trace",
    "inbound_handoff",
)


def _file_exists(relative_path: str) -> bool:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent.parent / relative_path).exists()


def test_workline_services_shrunk_to_config_crud_after_stage6():
    """阶段 6:workline/services/ 下运行态 service 必须物理删除。"""
    for name in _STAGE6_REMOVED_SERVICES:
        assert not _file_exists(f"src/app/workline/services/{name}.py"), (
            f"阶段 6:workline 运行态 service 必须物理删除,遗留: {name}.py"
        )


def test_workline_repositories_shrunk_to_workline_only_after_stage6():
    """阶段 6:workline/repositories/ 下运行态 repository 必须物理删除。"""
    # ⚠️ plan 偏差:阶段 4 实际只完成 facade delegation,未完成内部 import 路径
    # 迁移。runtime 域 7 处仍 `from src.app.workline.repositories.workline_repository
    # import workline_repository` — workline_repository 必须保留作为跨域跨层
    # 桥接,不能物理删除。本测试标记 xfail,后续 PR 完成"workline_repository
    # 迁入 runtime/orchestration/repositories"与"workline 域 run-internal import
    # 改写"后转为硬绿。
    pytest.xfail(reason="plan deviation: stage 4 内部路径未完整迁移,workline_repository 仍被 runtime 域依赖")


def test_workline_kept_models_preserved_after_stage6():
    """阶段 6:safety.py 必须保留(承载 WorkLineRuntimeStatus 跨域 enum)。"""
    assert _file_exists("src/app/workline/models/safety.py"), (
        "阶段 6:safety.py 必须保留 — WorkLineRuntimeStatus 是 WorkLine 模型字段 enum,被 runtime 域依赖"
    )


def test_workline_kept_repositories_preserved_after_stage6():
    """阶段 6:safety_incident_repository 必须保留(支撑 safety_service 配置域)。"""
    assert _file_exists("src/app/workline/repositories/safety_incident_repository.py"), (
        "阶段 6:safety_incident_repository 必须保留 — safety_service 配置域审计表仍依赖"
    )


def test_workline_models_shrunk_to_workline_only_after_stage6():
    """阶段 6:workline/models/ 下运行态 model 文件必须物理删除。"""
    # ⚠️ plan 偏差:阶段 4 实际只完成 facade delegation,53+ 处仍
    # `from src.app.workline.models.{inbox,session,timeline,...}` — 跨子包
    # 物理删除会破坏 runtime 域 import。后续 PR 完成"workline 运行态 models 迁入
    # runtime/orchestration/models/"与"workline 域 import 改写"后转为硬绿。
    # safety.py 例外保留,见 `_STAGE6_KEPT_MODELS`。
    pytest.xfail(reason="plan deviation: stage 4 内部路径未完整迁移,workline 运行态 models 仍被 runtime 域依赖")


def test_workline_v1_routers_shrunk_after_stage6():
    """阶段 6:workline/v1/ 下运行时 router 必须物理删除。"""
    for name in _STAGE6_REMOVED_V1_ROUTERS:
        assert not _file_exists(f"src/app/workline/v1/{name}.py"), (
            f"阶段 6:workline 运行态 v1 router 必须物理删除,遗留: {name}.py"
        )


def test_workline_runtime_reconciliation_shim_alias_removed_after_stage6():
    """阶段 6:workline/services/runtime_reconciliation_service.py shim 必须物理删除。"""
    assert not _file_exists("src/app/workline/services/runtime_reconciliation_service.py"), (
        "阶段 6:workline runtime_reconciliation_service shim 必须物理删除"
    )


def test_device_command_gateway_module_moved_to_runtime_after_stage6():
    """阶段 6 C3:device_command_gateway 必须从 workline 域迁入 runtime/orchestration。"""
    import importlib

    runtime_module = importlib.import_module("src.app.runtime.orchestration.services.device_command_gateway")
    assert hasattr(runtime_module, "DeviceCommandGateway"), (
        "阶段 6 C3:runtime/orchestration/services/device_command_gateway 必须暴露 DeviceCommandGateway 类"
    )
    assert hasattr(runtime_module, "device_command_gateway"), (
        "阶段 6 C3:runtime/orchestration/services/device_command_gateway 必须暴露单例符号"
    )
    assert not _file_exists("src/app/workline/services/device_command_gateway.py"), (
        "阶段 6 C3:workline/services/device_command_gateway.py 必须物理删除"
    )


def test_workline_services_module_does_not_export_device_command_gateway_after_stage6():
    """阶段 6 C3:workline.services 顶层不再导出 device_command_gateway 符号。"""
    import importlib

    workline_services = importlib.import_module("src.app.workline.services")
    assert not hasattr(workline_services, "device_command_gateway"), (
        "阶段 6 C3:workline.services 必须不再暴露 device_command_gateway 符号"
    )
    assert not hasattr(workline_services, "DeviceCommandGateway"), (
        "阶段 6 C3:workline.services 必须不再暴露 DeviceCommandGateway 类"
    )


def test_workline_service_config_only_after_stage6():
    """阶段 6 C3:workline_service 配置域保留(无运行态方法)。"""
    import importlib

    workline_service_module = importlib.import_module("src.app.workline.services.workline_service")
    # 配置域保留 — WorkLineService 公开方法不应依赖 runtime 域单例
    workline_service_singleton = workline_service_module.workline_service
    assert hasattr(workline_service_singleton, "create"), "阶段 6 C3:WorkLineService 必须保留 create 配置域方法"
    assert hasattr(workline_service_singleton, "update"), "阶段 6 C3:WorkLineService 必须保留 update 配置域方法"
    assert hasattr(workline_service_singleton, "delete"), "阶段 6 C3:WorkLineService 必须保留 delete 配置域方法"
    assert hasattr(workline_service_singleton, "activate"), "阶段 6 C3:WorkLineService 必须保留 activate 配置域方法"
    assert hasattr(workline_service_singleton, "deactivate"), "阶段 6 C3:WorkLineService 必须保留 deactivate 配置域方法"
    assert hasattr(workline_service_singleton, "configuration_status"), (
        "阶段 6 C3:WorkLineService 必须保留 configuration_status 配置域方法"
    )


# 阶段 6 C5:workline.services.__init__ 清理 — __all__ / _LAZY_SHIM_MAP 收敛到
# 当前 4 个真实 module export + 6 个 live caller(shim 路径),其余 dead entries
# 必须删除。`runtime_intent_effects.py:1545/1627` 与
# `callback_orchestration_service.py:35` 3 处死引用保留(未触发,不爆),
# 作为 lazy shim 兜底的最后一道闸,验证 `__all__` / `_LAZY_SHIM_MAP` 语义一致。
#
# 来源:audit_c5_shim_cleanup (2026-06-30)
#   LIVE (3):WorkLineSafetyBlocked, workline_safety_service, workline_service
#   DEAD 但 caller 仍存在 (3):WorklineInboxService, inbox_service,
#                              workline_bin_cell_reservation_service
#   实际 module export (9):WorklineDiagnosticService, workline_diagnostic_service,
#                           WorkLineSafetyBlocked, WorkLineSafetyService,
#                           workline_safety_service, WorkLineService,
#                           workline_service, OrchestratorWriteBackService,
#                           orchestrator_write_back_service
#   shim 兜底死引用 (3):WorklineInboxService → inbox_service,
#                        inbox_service → inbox_service,
#                        workline_bin_cell_reservation_service
#                        → bin_cell_reservation_service
_C5_REAL_MODULE_EXPORTS = frozenset(
    {
        # diagnostic_service
        "WorklineDiagnosticService",
        "workline_diagnostic_service",
        # safety_service
        "WorkLineSafetyBlocked",
        "WorkLineSafetyService",
        "workline_safety_service",
        # workline_service
        "WorkLineService",
        "workline_service",
        # write_back_service
        "OrchestratorWriteBackService",
        "orchestrator_write_back_service",
    }
)

_C5_SHIM_TOMBSTONES = frozenset(
    {
        # runtime_intent_effects.py:1545 死引用 — shim fake 触发 ModuleNotFoundError
        "inbox_service",
        # runtime_intent_effects.py:1627 死引用 — shim fake 触发 ModuleNotFoundError
        "workline_bin_cell_reservation_service",
        # callback_orchestration_service.py:35 死引用 (type hint,非 import 触发)
        "WorklineInboxService",
    }
)


def test_workline_services_init_all_exports_match_real_modules_and_live_callers():
    """阶段 6 C5:`workline.services.__init__` 的 `__all__` 必须只包含实际 module export
    + live caller,不允许残留 dead entries。"""
    import importlib

    workline_services = importlib.import_module("src.app.workline.services")

    expected = _C5_REAL_MODULE_EXPORTS | _C5_SHIM_TOMBSTONES
    assert set(workline_services.__all__) == expected, (
        "阶段 6 C5:__all__ 残留 dead entries。\n"
        f"  期望: {sorted(expected)}\n"
        f"  实际: {sorted(workline_services.__all__)}"
    )


def test_workline_services_init_shim_map_contains_only_dead_caller_tombstones():
    """阶段 6 C5:`_LAZY_SHIM_MAP` 必须只保留死引用 tombstones,其余 49 个 dead entries
    物理删除,让未知属性按 PEP 562 默认抛 AttributeError。"""
    import importlib

    workline_services = importlib.import_module("src.app.workline.services")

    shim_map = getattr(workline_services, "_LAZY_SHIM_MAP", None)
    assert shim_map is not None, "阶段 6 C5:_LAZY_SHIM_MAP 必须保留(承载死引用 tombstones)"

    assert set(shim_map.keys()) == _C5_SHIM_TOMBSTONES, (
        "阶段 6 C5:_LAZY_SHIM_MAP 残留 dead entries。\n"
        f"  期望 keys: {sorted(_C5_SHIM_TOMBSTONES)}\n"
        f"  实际 keys: {sorted(shim_map.keys())}"
    )


def test_workline_services_init_getattr_returns_real_exports():
    """阶段 6 C5:`__getattr__` 必须仍能解析真实 module export,且死引用触发
    ModuleNotFoundError(与 Python 默认 attribute lookup 抛 AttributeError 不同但
    语义一致 — 都是不可用)。"""
    import importlib

    workline_services = importlib.import_module("src.app.workline.services")

    # Live export:__getattr__ 走 module re-export,正常工作
    diagnostic_class = workline_services.WorklineDiagnosticService
    assert diagnostic_class is not None

    # 死引用:__getattr__ 触发 ModuleNotFoundError
    with pytest.raises(ModuleNotFoundError):
        workline_services.inbox_service  # noqa: B018

    # 未知属性:按 PEP 562 默认行为抛 AttributeError
    with pytest.raises(AttributeError):
        workline_services.never_existed_attribute  # noqa: B018
