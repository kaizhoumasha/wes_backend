"""粗分插件的显式、无扫描 handler 组合。"""

from __future__ import annotations

from wes_plugin_sdk import EpochConfigurationSnapshotReader, ExecutionSnapshotReader, PositionResourceSnapshotReader

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


def build_handlers(
    *,
    executions: ExecutionSnapshotReader,
    positions: PositionResourceSnapshotReader,
    epochs: EpochConfigurationSnapshotReader,
) -> tuple[RoughSorterHandler, ...]:
    """只在部署组合时构造固定 handler. 模块导入不实例化依赖."""

    return (
        MaterialEvidenceReadyHandler(executions, positions, epochs),
        AdmissionDecidedHandler(executions, positions, epochs),
        DevicePositionConfirmedHandler(executions, positions, epochs),
        TargetDecidedHandler(executions, positions, epochs),
        PlacementCompletedHandler(executions),
        ReplacementPlanDecidedHandler(executions, epochs),
        TransportOutcomePublishedHandler(executions, positions, epochs),
        RecoveryDecidedHandler(executions, positions, epochs),
    )


__all__ = ["PLUGIN_KEY", "PLUGIN_VERSION", "RoughSorterHandler", "build_handlers"]
