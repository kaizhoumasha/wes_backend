"""完整扫码测量证据触发业务准入请求。"""

from __future__ import annotations

from wes_plugin_sdk import (
    InboundWmsIntent,
    handler,
)

from rough_sorter.facts import MaterialEvidenceReadyFact
from rough_sorter.handlers._guards import require_execution, require_workline
from rough_sorter.wms_requests import admission_data


@handler(
    fact_type=MaterialEvidenceReadyFact,
    name="material-evidence-ready",
    supported_versions=("1.0",),
)
class MaterialEvidenceReadyHandler:
    def __call__(self, fact: MaterialEvidenceReadyFact) -> tuple[InboundWmsIntent]:
        snapshot = fact.runtime_snapshot
        execution = require_execution(
            snapshot.execution,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        if execution.workline_id != fact.workline_id:
            raise ValueError("execution WorkLine does not match scan Fact")
        _ = require_workline(snapshot.workline, workline_id=fact.workline_id, workline_code=fact.workline_code)
        return (admission_data(fact),)


__all__ = ["MaterialEvidenceReadyHandler"]
