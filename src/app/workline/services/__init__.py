"""WorkLine Service 按需导出，避免包导入拉起运行时闭包。

实际导出符号以本包内 `__all__` 为准；操作 API 直接 import 真实模块，不依赖
此处懒加载入口（例如 `WorkLineStartService` 由 `operation.py` 通过
`src.app.workline.services.workline_start_service` 绝对路径引用）。
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .diagnosis_verdict_builder_service import DiagnosisVerdictBuilder as WorklineDiagnosticService
    from .diagnosis_verdict_builder_service import diagnosis_verdict_builder as workline_diagnostic_service
    from .plane_service import WorkLinePlaneService as WorkLinePlaneService
    from .plane_service import workline_plane_service as workline_plane_service
    from .workline_configuration_service import WorkLineConfigurationService as WorkLineConfigurationService
    from .workline_position_service import WorkLinePositionService as WorkLinePositionService
    from .workline_position_service import workline_position_service as workline_position_service
    from .workline_service import WorkLineService as WorkLineService
    from .workline_service import workline_service as workline_service


_EXPORTS = {
    "WorkLinePlaneService": ("plane_service", "WorkLinePlaneService"),
    "workline_plane_service": ("plane_service", "workline_plane_service"),
    "WorkLineConfigurationService": ("workline_configuration_service", "WorkLineConfigurationService"),
    "WorkLinePositionService": ("workline_position_service", "WorkLinePositionService"),
    "workline_position_service": ("workline_position_service", "workline_position_service"),
    "WorkLineService": ("workline_service", "WorkLineService"),
    "workline_service": ("workline_service", "workline_service"),
    "WorklineDiagnosticService": ("diagnosis_verdict_builder_service", "DiagnosisVerdictBuilder"),
    "workline_diagnostic_service": ("diagnosis_verdict_builder_service", "diagnosis_verdict_builder"),
}


__all__ = [
    "WorkLineConfigurationService",
    "WorkLinePlaneService",
    "WorkLinePositionService",
    "WorkLineService",
    "WorklineDiagnosticService",
    "workline_diagnostic_service",
    "workline_plane_service",
    "workline_position_service",
    "workline_service",
]


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
