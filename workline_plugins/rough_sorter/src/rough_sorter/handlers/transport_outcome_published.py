"""已发布换架腿结果触发独立恢复或隔离。"""

from __future__ import annotations

from wes_plugin_sdk import (
    InboundWmsIntent,
    PauseForReconciliation,
    handler,
)

from rough_sorter.facts import TransportOutcome, TransportOutcomePublishedFact
from rough_sorter.handlers._guards import require_execution, require_workline
from rough_sorter.wms_requests import target_data


@handler(
    fact_type=TransportOutcomePublishedFact,
    name="transport-outcome-published",
    supported_versions=("1.0",),
)
class TransportOutcomePublishedHandler:
    def __call__(
        self,
        fact: TransportOutcomePublishedFact,
    ) -> tuple[InboundWmsIntent | PauseForReconciliation]:
        snapshot = fact.runtime_snapshot
        execution = require_execution(
            snapshot.execution,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
            allow_reconciling=True,
        )
        _ = require_workline(snapshot.workline, workline_id=execution.workline_id)
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
        exact_target_mismatch = fact.expected_target.kind == "RACK_POSITION" and final_position != fact.expected_target
        if actual_rack_id != fact.rack_id or exact_target_mismatch or arrival_face != fact.expected_face:
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
        return (target_data(fact),)


__all__ = ["TransportOutcomePublishedHandler"]
