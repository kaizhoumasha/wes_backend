"""WorkLine Service 导出"""

from __future__ import annotations

from typing import Any

# Phase 2 burn-down 阶段 6:workline 域退化为纯配置域,运行态 service shim 已
# 物理删除。保留 file 是配置域 service(diagnostic_service / safety_service /
# workline_service / write_back_service)。device_command_gateway 在 C3 迁出至
# runtime/orchestration/services/。其余运行态 service 迁入 runtime/orchestration/
# services 与 runtime/capabilities/phase4/ 后已物理删除。
#
# 阶段 6 C5:__all__ / _LAZY_SHIM_MAP 收敛到当前 4 个真实 module export +
# 3 个 live caller 死引用 tombstone(inbox_service / workline_bin_cell_reservation_service
# / WorklineInboxService,源在 runtime_intent_effects.py:1545/1627 与
# callback_orchestration_service.py:35 — 这些 caller 是死代码,未触发,
# 保留作为 lazy shim 兜底的最后一道闸)。其他 dead entries 已物理删除。
from .diagnostic_service import WorklineDiagnosticService, workline_diagnostic_service
from .safety_service import WorkLineSafetyBlocked, WorkLineSafetyService, workline_safety_service
from .workline_service import WorkLineService, workline_service
from .write_back_service import OrchestratorWriteBackService, orchestrator_write_back_service

__all__ = [
    "OrchestratorWriteBackService",
    "WorkLineSafetyBlocked",
    "WorkLineSafetyService",
    "WorkLineService",
    "WorklineDiagnosticService",
    "WorklineInboxService",
    "inbox_service",
    "orchestrator_write_back_service",
    "workline_bin_cell_reservation_service",
    "workline_diagnostic_service",
    "workline_safety_service",
    "workline_service",
]


# 阶段 6 C5:3 个 live caller 死引用 tombstone,attribute access 命中时通过
# importlib.import_module 触发 ModuleNotFoundError — 与 Python 默认
# attribute lookup 抛 AttributeError 不同但对调用方语义一致(都是不可用)。
# 不在表中的属性按 PEP 562 默认行为抛 AttributeError。
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
