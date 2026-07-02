"""WorkLine Service 导出"""

from __future__ import annotations

from typing import Any

# Phase 2 burn-down 阶段 6:workline 域退化为纯配置域,运行态 service shim 已
# 物理删除。保留 file 是配置域 service(diagnostic_service / safety_service /
# workline_service / write_back_service)。device_command_gateway 在 C3 迁出至
# runtime/orchestration/services/。其余运行态 service 迁入 runtime/orchestration/
# services 与 runtime/capabilities/phase4/ 后已物理删除。
#
# 阶段 6 C5:__all__ / _LAZY_SHIM_MAP 收敛到当前 6 个真实 module export +
# 3 个未初始化 service 属性的 fallback tombstone(inbox_service /
# workline_bin_cell_reservation_service / WorklineInboxService)。
# 这 3 个符号历史上由 workline.services 域暴露,底层 module 已物理删除
# (迁入 runtime/orchestration/services/ 与 repositories/)。3 处 caller 仍按
# `from src.app.workline.services import ...` 旧路径访问:
#   - runtime_intent_effects.py:1545/1627 — `self._inbox_service` /
#     `self._bin_cell_reservation_service` 属性未注入时的 fallback import
#     (属性注入后不触发,路径是活的防御性兜底,非死代码)
#   - callback_orchestration_service.py:35 — TYPE_CHECKING 块内 type hint
#     (运行时不触发,静态类型检查用)
# PEP 562 __getattr__ 命中 _LAZY_SHIM_MAP 后 import 旧路径,因 module 已删除
# 抛 ModuleNotFoundError,让调用方明确感知"属性不可用"。其他 dead entries
# 已物理删除。
from .diagnostic_service import WorklineDiagnosticService, workline_diagnostic_service
from .manifest_validator import WorkLineManifestActivationValidator, workline_manifest_activation_validator
from .plane_service import (
    PlaneReadSecurityPolicy,
    WorkLinePlaneService,
    plane_read_security_policy,
    workline_plane_service,
)
from .safety_service import WorkLineSafetyBlocked, WorkLineSafetyService, workline_safety_service
from .workline_service import WorkLineService, workline_service
from .write_back_service import OrchestratorWriteBackService, orchestrator_write_back_service

__all__ = [
    "OrchestratorWriteBackService",
    "PlaneReadSecurityPolicy",
    "WorkLineManifestActivationValidator",
    "WorkLinePlaneService",
    "WorkLineSafetyBlocked",
    "WorkLineSafetyService",
    "WorkLineService",
    "WorklineDiagnosticService",
    "WorklineInboxService",
    "inbox_service",
    "orchestrator_write_back_service",
    "plane_read_security_policy",
    "workline_bin_cell_reservation_service",
    "workline_diagnostic_service",
    "workline_manifest_activation_validator",
    "workline_plane_service",
    "workline_safety_service",
    "workline_service",
]


# 阶段 6 C5:3 个未初始化 service 属性的 fallback tombstone。caller 命中时
# 通过 importlib.import_module 触发 ModuleNotFoundError(旧 module 已物理
# 删除),与 Python 默认 attribute lookup 抛 AttributeError 不同但对调用方
# 语义一致(都是不可用)。不在表中的属性按 PEP 562 默认行为抛 AttributeError。
_LAZY_SHIM_MAP = {
    "inbox_service": "inbox_service",
    "workline_bin_cell_reservation_service": "bin_cell_reservation_service",
    "WorklineInboxService": "inbox_service",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_SHIM_MAP.get(name)
    if module_name is None:
        raise AttributeError(name)
    import importlib

    module = importlib.import_module(f"src.app.workline.services.{module_name}")
    value = getattr(module, name)
    globals()[name] = value
    return value
