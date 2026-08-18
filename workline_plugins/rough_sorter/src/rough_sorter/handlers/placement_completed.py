"""WMS placement 记录结果触发关闭或对账。"""

from __future__ import annotations

from wes_plugin_sdk import CompleteExecution, PauseForReconciliation, handler

from rough_sorter.facts import CompletionKind, CompletionResult, PlacementCompletedFact
from rough_sorter.handlers._guards import require_execution


@handler(
    fact_type=PlacementCompletedFact,
    name="placement-completed",
    supported_versions=("1.0",),
)
class PlacementCompletedHandler:
    def __call__(self, fact: PlacementCompletedFact) -> tuple[CompleteExecution | PauseForReconciliation]:
        require_execution(
            fact.runtime_snapshot.execution,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        if fact.result is CompletionResult.RECONCILING:
            return (
                PauseForReconciliation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "WMS_PLACEMENT_RECONCILING",
                    affected_resource_ids=fact.affected_resource_ids,
                ),
            )
        prefix = "PLACEMENT" if fact.kind is CompletionKind.PLACEMENT else "NG_PLACEMENT"
        return (
            CompleteExecution(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                reason_code=f"{prefix}_{fact.result.value}",
            ),
        )


__all__ = ["PlacementCompletedHandler"]
