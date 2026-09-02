"""WMS 准入决定触发入料、NG、等待或对账。"""

from __future__ import annotations

from wes_plugin_sdk import (
    CreateDeviceCommand,
    DeferExecution,
    PauseForReconciliation,
    Wait,
    handler,
)

from rough_sorter.facts import AdmissionDecidedFact, AdmissionResult
from rough_sorter.handlers._guards import require_epoch, require_execution


@handler(
    fact_type=AdmissionDecidedFact,
    name="admission-decided",
    supported_versions=("1.0",),
)
class AdmissionDecidedHandler:
    def __call__(
        self,
        fact: AdmissionDecidedFact,
    ) -> tuple[CreateDeviceCommand | DeferExecution | PauseForReconciliation | Wait]:
        snapshot = fact.runtime_snapshot
        execution = require_execution(
            snapshot.execution,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        require_epoch(snapshot.epoch, line_run_epoch_id=execution.line_run_epoch_id)
        if fact.result is AdmissionResult.WAIT:
            return (
                Wait(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "WMS_ADMISSION_WAIT",
                ),
            )
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
            return (
                DeferExecution(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code="MEASUREMENT_DEVICE_NOT_READY",
                ),
            )

        target = fact.next_position
        if target is None:
            raise ValueError("admission action requires next_position")
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


__all__ = ["AdmissionDecidedHandler"]
