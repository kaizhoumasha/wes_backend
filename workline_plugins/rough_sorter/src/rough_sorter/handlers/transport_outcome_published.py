"""已发布换架腿结果触发独立恢复或隔离。"""

from __future__ import annotations

from wes_plugin_sdk import (
    CreateWmsConfirmation,
    PauseForReconciliation,
    handler,
)

from rough_sorter.facts import TransportOutcome, TransportOutcomePublishedFact
from rough_sorter.handlers._guards import require_epoch, require_execution

TARGET_OPERATION = "inbound.material.target_decide@v1"


@handler(
    fact_type=TransportOutcomePublishedFact,
    name="transport-outcome-published",
    supported_versions=("1.0",),
)
class TransportOutcomePublishedHandler:
    def __call__(
        self,
        fact: TransportOutcomePublishedFact,
    ) -> tuple[CreateWmsConfirmation | PauseForReconciliation]:
        snapshot = fact.runtime_snapshot
        execution = require_execution(
            snapshot.execution,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
            allow_reconciling=True,
        )
        require_epoch(snapshot.epoch, line_run_epoch_id=execution.line_run_epoch_id)
        if fact.outcome is not TransportOutcome.SUCCEEDED:
            return (
                PauseForReconciliation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "NEW_RACK_MOVE_RECONCILING",
                    affected_resource_ids=(fact.rack_id,),
                ),
            )
        actual_rack_id = fact.actual_rack_id
        final_position = fact.final_position
        arrival_face = fact.arrival_face
        if actual_rack_id is None or final_position is None or arrival_face is None:
            raise ValueError("successful transport outcome requires complete actual arrival")
        if (
            actual_rack_id != fact.rack_id
            or final_position != fact.expected_target
            or arrival_face is not fact.expected_face
        ):
            affected_resource_ids = (fact.rack_id,)
            if actual_rack_id != fact.rack_id:
                affected_resource_ids = (*affected_resource_ids, actual_rack_id)
            return (
                PauseForReconciliation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code="NEW_RACK_ARRIVAL_MISMATCH",
                    affected_resource_ids=affected_resource_ids,
                ),
            )
        source_position = fact.source_position
        if source_position is None:
            raise ValueError("NEW_IN success requires material source_position")
        return (
            CreateWmsConfirmation(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                operation=TARGET_OPERATION,
                operation_id=fact.request_operation_id or "",
                evidence_refs=(fact.evidence_id,),
                snapshot_refs=(
                    f"execution:{fact.material_execution_id}",
                    f"transport:{fact.transport_task_id}",
                    f"wms-admission:{fact.inbound_admission_id}",
                    f"position:{source_position.location_id}",
                    f"rack:{fact.rack_id}",
                ),
            ),
        )


__all__ = ["TransportOutcomePublishedHandler"]
