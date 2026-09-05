"""粗分插件的显式、无扫描 handler 组合。"""

from __future__ import annotations

from rough_sorter.handlers import (
    AdmissionDecidedHandler,
    DevicePositionConfirmedHandler,
    MaterialEvidenceReadyHandler,
    PlacementCompletedHandler,
    RecoveryDecidedHandler,
    ReplacementPlanDecidedHandler,
    TargetDecidedHandler,
    TransportOutcomePublishedHandler,
)

PLUGIN_KEY = "rough_sorter"
PLUGIN_VERSION = "1.0.0"
# WES 逻辑位置参数。由 Epoch/工作线限定作用域, ECS 负责映射物理点位。
POSITION_ROLES = ("MEASUREMENT_POSITION", "PIPELINE_INLET", "PIPELINE_OUTLET", "NG_POSITION")

type RoughSorterHandler = (
    MaterialEvidenceReadyHandler
    | AdmissionDecidedHandler
    | DevicePositionConfirmedHandler
    | TargetDecidedHandler
    | PlacementCompletedHandler
    | ReplacementPlanDecidedHandler
    | TransportOutcomePublishedHandler
    | RecoveryDecidedHandler
)


def build_handlers() -> tuple[RoughSorterHandler, ...]:
    """只在部署组合时构造固定 handler. 模块导入不实例化依赖."""

    return (
        MaterialEvidenceReadyHandler(),
        AdmissionDecidedHandler(),
        DevicePositionConfirmedHandler(),
        TargetDecidedHandler(),
        PlacementCompletedHandler(),
        ReplacementPlanDecidedHandler(),
        TransportOutcomePublishedHandler(),
        RecoveryDecidedHandler(),
    )


__all__ = ["PLUGIN_KEY", "PLUGIN_VERSION", "RoughSorterHandler", "build_handlers"]
