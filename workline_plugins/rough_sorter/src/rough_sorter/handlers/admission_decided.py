"""WMS 准入决定触发入料、NG、等待或对账。"""

from __future__ import annotations

from dataclasses import dataclass

from wes_plugin_sdk import (
    CreateDeviceCommand,
    EpochConfigurationSnapshotReader,
    ExecutionSnapshotReader,
    PauseForReconciliation,
    PositionResourceSnapshotReader,
    Wait,
    handler,
)

from rough_sorter.facts import AdmissionDecidedFact, AdmissionResult
from rough_sorter.handlers._guards import require_epoch, require_execution, require_source, require_target


@handler(
    fact_type=AdmissionDecidedFact,
    name="admission-decided",
    supported_versions=("1.0",),
)
@dataclass(frozen=True, slots=True)
class AdmissionDecidedHandler:
    executions: ExecutionSnapshotReader
    positions: PositionResourceSnapshotReader
    epochs: EpochConfigurationSnapshotReader

    def __call__(
        self,
        fact: AdmissionDecidedFact,
    ) -> tuple[CreateDeviceCommand | PauseForReconciliation | Wait]:
        execution = require_execution(
            self.executions,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        require_epoch(self.epochs, line_run_epoch_id=execution.line_run_epoch_id)
        require_source(self.positions, fact.source_position, material_trace_id=fact.material_trace_id)

        if fact.result is AdmissionResult.WAIT:
            return (self._wait(fact, fact.reason_code or "WMS_ADMISSION_WAIT"),)
        if fact.result is AdmissionResult.RECONCILING:
            return (
                PauseForReconciliation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "WMS_ADMISSION_RECONCILING",
                    affected_resource_ids=(fact.source_position.location_id,),
                ),
            )
        if not fact.device_ready:
            return (self._wait(fact, "MEASUREMENT_DEVICE_NOT_READY"),)

        target = fact.next_position
        if target is None:
            raise ValueError("admission action requires next_position")
        require_target(self.positions, target)
        return (
            CreateDeviceCommand(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                device_role="MEASUREMENT_DEVICE",
                task_type="PICK_AND_PUT",
                material_trace_id=fact.material_trace_id,
                source=fact.source_position,
                target=target,
            ),
        )

    @staticmethod
    def _wait(fact: AdmissionDecidedFact, reason_code: str) -> Wait:
        return Wait(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            reason_code=reason_code,
        )


__all__ = ["AdmissionDecidedHandler"]
