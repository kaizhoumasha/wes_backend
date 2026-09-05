"""WMS 目标决定触发出料、换架请求、等待或对账。"""

from __future__ import annotations

from wes_plugin_sdk import (
    CreateDeviceCommand,
    CreateWmsConfirmation,
    DeferExecution,
    DevicePosition,
    PauseForReconciliation,
    Wait,
    handler,
)

from rough_sorter.facts import TargetDecidedFact, TargetResult
from rough_sorter.handlers._guards import require_device_binding, require_epoch, require_execution
from rough_sorter.wms_requests import replacement_plan_data

REPLACEMENT_PLAN_OPERATION = "inbound.source_rack.replacement_plan_decide@v1"


@handler(
    fact_type=TargetDecidedFact,
    name="target-decided",
    supported_versions=("1.0",),
)
class TargetDecidedHandler:
    def __call__(
        self,
        fact: TargetDecidedFact,
    ) -> tuple[CreateDeviceCommand | CreateWmsConfirmation | DeferExecution | PauseForReconciliation | Wait]:
        snapshot = fact.runtime_snapshot
        execution = require_execution(
            snapshot.execution,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        epoch = require_epoch(snapshot.epoch, line_run_epoch_id=execution.line_run_epoch_id)
        if fact.result is TargetResult.WAIT:
            return (
                Wait(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "WMS_TARGET_WAIT",
                ),
            )
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
                    request_data=replacement_plan_data(fact),
                ),
            )
        target = fact.target_position
        if target is None:
            raise ValueError("target action requires target_position")
        conflict = self._assigned_conflict(fact, target)
        if conflict is not None:
            return (conflict,)
        if not fact.device_ready:
            return (
                DeferExecution(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code="PLACEMENT_DEVICE_NOT_READY",
                ),
            )
        return (
            CreateDeviceCommand(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                device_role="PLACEMENT_DEVICE",
                device_code=require_device_binding(epoch, "PLACEMENT_DEVICE").device_code,
                task_type="PICK_AND_PUT",
                material_trace_id=fact.material_trace_id,
                source=fact.source_position,
                target=target,
            ),
        )

    @staticmethod
    def _assigned_conflict(fact: TargetDecidedFact, target: DevicePosition) -> PauseForReconciliation | None:
        if fact.result is not TargetResult.ASSIGNED:
            return None
        if fact.current_rack_fenced:
            return PauseForReconciliation(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                reason_code="CURRENT_RACK_ALREADY_REPLACED",
                affected_resource_ids=(fact.current_rack_id,),
            )
        target_rack_id = target.rack_id
        if target_rack_id != fact.current_rack_id:
            return PauseForReconciliation(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                reason_code="TARGET_RACK_MISMATCH",
                affected_resource_ids=(fact.current_rack_id, target_rack_id or ""),
            )
        return None


__all__ = ["TargetDecidedHandler"]
