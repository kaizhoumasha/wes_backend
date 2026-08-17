"""WMS 目标决定触发出料、换架请求、等待或对账。"""

from __future__ import annotations

from dataclasses import dataclass

from wes_plugin_sdk import (
    CreateDeviceCommand,
    CreateWmsConfirmation,
    EpochConfigurationSnapshotReader,
    ExecutionSnapshotReader,
    PauseForReconciliation,
    PositionResourceSnapshotReader,
    Wait,
    handler,
)

from rough_sorter.facts import TargetDecidedFact, TargetResult
from rough_sorter.handlers._guards import require_epoch, require_execution, require_source, require_target

REPLACEMENT_PLAN_OPERATION = "inbound.source_rack.replacement_plan_decide@v1"


@handler(
    fact_type=TargetDecidedFact,
    name="target-decided",
    supported_versions=("1.0",),
)
@dataclass(frozen=True, slots=True)
class TargetDecidedHandler:
    executions: ExecutionSnapshotReader
    positions: PositionResourceSnapshotReader
    epochs: EpochConfigurationSnapshotReader

    def __call__(
        self,
        fact: TargetDecidedFact,
    ) -> tuple[CreateDeviceCommand | CreateWmsConfirmation | PauseForReconciliation | Wait]:
        execution = require_execution(
            self.executions,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        require_epoch(self.epochs, line_run_epoch_id=execution.line_run_epoch_id)
        require_source(self.positions, fact.source_position, material_trace_id=fact.material_trace_id)
        if fact.result is TargetResult.WAIT:
            return (self._wait(fact, fact.reason_code or "WMS_TARGET_WAIT"),)
        if fact.result is TargetResult.RECONCILING:
            return (
                PauseForReconciliation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "WMS_TARGET_RECONCILING",
                    affected_resource_ids=(fact.source_position.location_id, fact.current_rack_id),
                ),
            )
        if fact.result is TargetResult.NO_AVAILABLE_CELL:
            return (
                CreateWmsConfirmation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    operation=REPLACEMENT_PLAN_OPERATION,
                    operation_id=fact.request_operation_id or "",
                    evidence_refs=(fact.evidence_id,),
                    snapshot_refs=(
                        f"execution:{fact.material_execution_id}",
                        f"rack:{fact.current_rack_id}",
                    ),
                ),
            )
        if not fact.device_ready:
            return (self._wait(fact, "PLACEMENT_DEVICE_NOT_READY"),)
        target = fact.target_position
        if target is None:
            raise ValueError("target action requires target_position")
        require_target(self.positions, target)
        return (
            CreateDeviceCommand(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                device_role="PLACEMENT_DEVICE",
                task_type="PICK_AND_PUT",
                material_trace_id=fact.material_trace_id,
                source=fact.source_position,
                target=target,
            ),
        )

    @staticmethod
    def _wait(fact: TargetDecidedFact, reason_code: str) -> Wait:
        return Wait(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            reason_code=reason_code,
        )


__all__ = ["TargetDecidedHandler"]
