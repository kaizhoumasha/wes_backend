"""WMS 换架计划触发两个独立 RACK_MOVE 或安全等待。"""

from __future__ import annotations

from dataclasses import dataclass

from wes_plugin_sdk import (
    CreateTransportTask,
    EpochConfigurationSnapshotReader,
    ExecutionSnapshotReader,
    PauseForReconciliation,
    TransportLeg,
    TransportTaskType,
    Wait,
    handler,
)

from rough_sorter.facts import ReplacementPlanDecidedFact, ReplacementResult
from rough_sorter.handlers._guards import require_epoch, require_execution


@handler(
    fact_type=ReplacementPlanDecidedFact,
    name="replacement-plan-decided",
    supported_versions=("1.0",),
)
@dataclass(frozen=True, slots=True)
class ReplacementPlanDecidedHandler:
    executions: ExecutionSnapshotReader
    epochs: EpochConfigurationSnapshotReader

    def __call__(
        self,
        fact: ReplacementPlanDecidedFact,
    ) -> tuple[CreateTransportTask | PauseForReconciliation | Wait, ...]:
        execution = require_execution(
            self.executions,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        require_epoch(self.epochs, line_run_epoch_id=execution.line_run_epoch_id)
        if fact.result is ReplacementResult.WAIT:
            return (self._wait(fact, fact.reason_code or "WMS_REPLACEMENT_WAIT"),)
        if fact.result is ReplacementResult.RECONCILING:
            return (
                PauseForReconciliation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "WMS_REPLACEMENT_RECONCILING",
                    affected_resource_ids=(fact.current_rack_id,),
                ),
            )
        if not fact.release_gate_closed:
            return (self._wait(fact, "RACK_RELEASE_GATE_NOT_CLOSED"),)
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
                rack_id=old_plan.rack_id,
                source=old_plan.source,
                target=old_plan.target,
                target_face=old_plan.target_face,
            ),
            CreateTransportTask(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                task_type=TransportTaskType.RACK_MOVE,
                rack_replacement_id=replacement_id,
                leg=TransportLeg.NEW_IN,
                rack_id=new_plan.rack_id,
                source=new_plan.source,
                target=new_plan.target,
                target_face=new_plan.target_face,
            ),
        )

    @staticmethod
    def _wait(fact: ReplacementPlanDecidedFact, reason_code: str) -> Wait:
        return Wait(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            reason_code=reason_code,
        )


__all__ = ["ReplacementPlanDecidedHandler"]
