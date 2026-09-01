"""Composition root 向私有 deployment 实现传递的 sealed 插件类型集合。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RoughSorterTypes:
    plugin_key: str
    plugin_version: str
    role_contracts: dict[str, str]
    position_roles: tuple[str, ...]
    build_handlers: Any
    AdmissionDecidedFact: Any
    AdmissionResult: Any
    CompletionKind: Any
    CompletionResult: Any
    DeviceOutcome: Any
    DevicePositionConfirmedFact: Any
    DeviceStep: Any
    MaterialEvidenceReadyFact: Any
    PlacementCommandStatus: Any
    PlacementCompletedFact: Any
    PlacementConfirmationStatus: Any
    PlacementReleaseEvidence: Any
    PlacementResponseResult: Any
    RackMoveLegPlan: Any
    RackReleaseSnapshot: Any
    RecoveryDecidedFact: Any
    RecoveryDeferContinuation: Any
    RecoveryDeviceContinuation: Any
    RecoveryWmsContinuation: Any
    ReplacementPlanDecidedFact: Any
    ReplacementResult: Any
    RoughSorterRuntimeSnapshot: Any
    ShapeResult: Any
    TargetDecidedFact: Any
    TargetResult: Any
    TransportOutcome: Any
    TransportOutcomePublishedFact: Any
    TransportLeg: Any
    rack_release_snapshot_ref: Any
    wms_position: Any


__all__ = ["RoughSorterTypes"]
