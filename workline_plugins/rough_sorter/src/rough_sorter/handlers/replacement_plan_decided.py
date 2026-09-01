"""WMS 换架计划触发两个独立 RACK_MOVE 或安全等待。"""

from __future__ import annotations

from wes_plugin_sdk import (
    CreateTransportTask,
    DeferExecution,
    PauseForReconciliation,
    TransportLeg,
    TransportRcsTemplateId,
    TransportTaskType,
    Wait,
    handler,
)

from rough_sorter.facts import (
    PlacementCommandStatus,
    PlacementConfirmationStatus,
    PlacementResponseResult,
    ReplacementPlanDecidedFact,
    ReplacementResult,
)
from rough_sorter.handlers._guards import require_epoch, require_execution


@handler(
    fact_type=ReplacementPlanDecidedFact,
    name="replacement-plan-decided",
    supported_versions=("1.0",),
)
class ReplacementPlanDecidedHandler:
    def __call__(
        self,
        fact: ReplacementPlanDecidedFact,
    ) -> tuple[CreateTransportTask | DeferExecution | PauseForReconciliation | Wait, ...]:
        snapshot = fact.runtime_snapshot
        execution = require_execution(
            snapshot.execution,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        _ = require_epoch(snapshot.epoch, line_run_epoch_id=execution.line_run_epoch_id)
        if fact.result is ReplacementResult.WAIT:
            return (
                Wait(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "WMS_REPLACEMENT_WAIT",
                ),
            )
        if fact.result is ReplacementResult.RECONCILING:
            return (
                PauseForReconciliation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "WMS_REPLACEMENT_RECONCILING",
                    affected_resource_ids=(fact.current_rack_id,),
                ),
            )
        release_snapshot = fact.release_snapshot
        if release_snapshot is None:
            raise ValueError("READY replacement requires release snapshot")
        if any(
            item.command_status
            in {
                PlacementCommandStatus.FAILED,
                PlacementCommandStatus.TIMED_OUT,
                PlacementCommandStatus.RECONCILING,
            }
            or item.confirmation_status is PlacementConfirmationStatus.RECONCILING
            for item in release_snapshot.placements
        ):
            return (
                PauseForReconciliation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code="RACK_RELEASE_GATE_CONFLICT",
                    affected_resource_ids=(fact.current_rack_id,),
                ),
            )
        if any(
            item.command_status is not PlacementCommandStatus.SUCCEEDED
            or item.command_result_evidence_id is None
            or item.confirmation_status is not PlacementConfirmationStatus.COMPLETED
            or item.response_result not in {PlacementResponseResult.RECORDED, PlacementResponseResult.DUPLICATE}
            or item.response_evidence_id is None
            for item in release_snapshot.placements
        ):
            return (
                DeferExecution(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code="RACK_RELEASE_GATE_NOT_CLOSED",
                ),
            )
        old_plan = fact.old_loaded_rack
        new_plan = fact.new_empty_rack
        if old_plan is None or new_plan is None:
            raise ValueError("READY replacement requires both rack plans")
        replacement_id = fact.rack_replacement_id or ""
        return (
            CreateTransportTask(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                task_type=TransportTaskType.RACK_MOVE,
                rack_replacement_id=replacement_id,
                leg=TransportLeg.OLD_OUT,
                current_rack_id=fact.current_rack_id,
                rack_id=old_plan.rack_id,
                source=old_plan.source,
                target=old_plan.target,
                target_face=old_plan.target_face,
                rcs_template_id=TransportRcsTemplateId.CTU03,
            ),
            CreateTransportTask(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                task_type=TransportTaskType.RACK_MOVE,
                rack_replacement_id=replacement_id,
                leg=TransportLeg.NEW_IN,
                current_rack_id=fact.current_rack_id,
                rack_id=new_plan.rack_id,
                source=new_plan.source,
                target=new_plan.target,
                target_face=new_plan.target_face,
                rcs_template_id=TransportRcsTemplateId.CTU01,
            ),
        )


__all__ = ["ReplacementPlanDecidedHandler"]
