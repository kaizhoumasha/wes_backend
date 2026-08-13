"""WorkLine Service 按需导出，避免包导入拉起运行时闭包。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "LineRunEpochService": ("line_run_epoch_service", "LineRunEpochService"),
    "line_run_epoch_service": ("line_run_epoch_service", "line_run_epoch_service"),
    "WorkLinePlaneService": ("plane_service", "WorkLinePlaneService"),
    "workline_plane_service": ("plane_service", "workline_plane_service"),
    "WorkLineSafetyBlocked": ("safety_service", "WorkLineSafetyBlocked"),
    "WorkLineSafetyService": ("safety_service", "WorkLineSafetyService"),
    "workline_safety_service": ("safety_service", "workline_safety_service"),
    "WorkLineService": ("workline_service", "WorkLineService"),
    "workline_service": ("workline_service", "workline_service"),
    "WorklineDiagnosticService": ("diagnostic_service", "WorklineDiagnosticService"),
    "workline_diagnostic_service": ("diagnostic_service", "workline_diagnostic_service"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name == "write_back_service":
        return import_module(f"{__name__}.write_back_service")
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(f"{__name__}.{target[0]}")
    value = getattr(module, target[1])
    globals()[name] = value
    return value
