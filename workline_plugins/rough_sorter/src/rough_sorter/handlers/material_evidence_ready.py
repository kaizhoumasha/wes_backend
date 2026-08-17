"""完整扫码测量证据触发业务准入请求。"""

from __future__ import annotations

from dataclasses import dataclass

from wes_plugin_sdk import (
    CreateWmsConfirmation,
    EpochConfigurationSnapshotReader,
    ExecutionSnapshotReader,
    PositionResourceSnapshotReader,
    handler,
)

from rough_sorter.facts import MaterialEvidenceReadyFact
from rough_sorter.handlers._guards import require_epoch, require_execution, require_source

ADMISSION_OPERATION = "inbound.material.admission_decide@v1"


@handler(
    fact_type=MaterialEvidenceReadyFact,
    name="material-evidence-ready",
    supported_versions=("1.0",),
)
@dataclass(frozen=True, slots=True)
class MaterialEvidenceReadyHandler:
    executions: ExecutionSnapshotReader
    positions: PositionResourceSnapshotReader
    epochs: EpochConfigurationSnapshotReader

    def __call__(self, fact: MaterialEvidenceReadyFact) -> tuple[CreateWmsConfirmation]:
        execution = require_execution(
            self.executions,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        if execution.line_run_epoch_id != fact.line_run_epoch_id:
            raise ValueError("execution Epoch does not match scan Fact")
        require_epoch(self.epochs, line_run_epoch_id=fact.line_run_epoch_id, workline_code=fact.workline_code)
        require_source(self.positions, fact.source_position, material_trace_id=fact.material_trace_id)
        return (
            CreateWmsConfirmation(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                operation=ADMISSION_OPERATION,
                operation_id=fact.request_operation_id,
                evidence_refs=(fact.evidence_id,),
                snapshot_refs=(
                    f"execution:{fact.material_execution_id}",
                    f"epoch:{fact.line_run_epoch_id}",
                ),
            ),
        )


__all__ = ["MaterialEvidenceReadyHandler"]
