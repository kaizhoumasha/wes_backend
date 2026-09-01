"""完整扫码测量证据触发业务准入请求。"""

from __future__ import annotations

from wes_plugin_sdk import (
    CreateWmsConfirmation,
    handler,
)

from rough_sorter.facts import MaterialEvidenceReadyFact
from rough_sorter.handlers._guards import require_epoch, require_execution
from rough_sorter.wms_requests import admission_data

ADMISSION_OPERATION = "inbound.material.admission_decide@v1"


@handler(
    fact_type=MaterialEvidenceReadyFact,
    name="material-evidence-ready",
    supported_versions=("1.0",),
)
class MaterialEvidenceReadyHandler:
    def __call__(self, fact: MaterialEvidenceReadyFact) -> tuple[CreateWmsConfirmation]:
        snapshot = fact.runtime_snapshot
        execution = require_execution(
            snapshot.execution,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        if execution.line_run_epoch_id != fact.line_run_epoch_id:
            raise ValueError("execution Epoch does not match scan Fact")
        require_epoch(snapshot.epoch, line_run_epoch_id=fact.line_run_epoch_id, workline_code=fact.workline_code)
        return (
            CreateWmsConfirmation(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                operation=ADMISSION_OPERATION,
                operation_id=fact.request_operation_id,
                request_data=admission_data(fact),
            ),
        )


__all__ = ["MaterialEvidenceReadyHandler"]
