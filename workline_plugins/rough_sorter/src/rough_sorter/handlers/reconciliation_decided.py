"""人工对账结论触发终止或一个类型化恢复动作。"""

from __future__ import annotations

from dataclasses import dataclass

from wes_plugin_sdk import (
    CompleteExecution,
    CreateDeviceCommand,
    CreateWmsConfirmation,
    EpochConfigurationSnapshotReader,
    ExecutionSnapshotReader,
    PositionResourceSnapshotReader,
    Wait,
    handler,
)

from rough_sorter.facts import (
    ReconciliationDecidedFact,
    ReconciliationDecision,
    ResumeDeviceAction,
    ResumeWait,
    ResumeWmsAction,
)
from rough_sorter.handlers._guards import require_epoch, require_execution, require_source, require_target

_POSITION_WMS_OPERATIONS = {
    "MEASUREMENT_POSITION": {"inbound.material.admission_decide@v1"},
    "PIPELINE_OUTLET": {
        "inbound.material.target_decide@v1",
        "inbound.source_rack.replacement_plan_decide@v1",
    },
    "RACK_CELL": {"inbound.material.placement_report@v1"},
    "NG_POSITION": {"inbound.material.ng_placement_report@v1"},
}

_DEVICE_RESUME_TOPOLOGY = {
    ("MEASUREMENT_POSITION", "PIPELINE_INLET"): ("MEASUREMENT_DEVICE", "PICK_AND_PUT"),
    ("MEASUREMENT_POSITION", "NG_POSITION"): ("MEASUREMENT_DEVICE", "PICK_AND_PUT"),
    ("PIPELINE_INLET", "PIPELINE_OUTLET"): ("TRANSFER_DEVICE", "MOVE_FORWARD"),
    ("PIPELINE_OUTLET", "RACK_CELL"): ("PLACEMENT_DEVICE", "PICK_AND_PUT"),
    ("PIPELINE_OUTLET", "NG_POSITION"): ("PLACEMENT_DEVICE", "PICK_AND_PUT"),
}


@handler(
    fact_type=ReconciliationDecidedFact,
    name="reconciliation-decided",
    supported_versions=("1.0",),
)
@dataclass(frozen=True, slots=True)
class ReconciliationDecidedHandler:
    executions: ExecutionSnapshotReader
    positions: PositionResourceSnapshotReader
    epochs: EpochConfigurationSnapshotReader

    def __call__(
        self,
        fact: ReconciliationDecidedFact,
    ) -> tuple[CompleteExecution | CreateDeviceCommand | CreateWmsConfirmation | Wait]:
        execution = require_execution(
            self.executions,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
            allow_reconciling=True,
        )
        require_epoch(self.epochs, line_run_epoch_id=execution.line_run_epoch_id)
        if fact.decision is ReconciliationDecision.ABORT:
            return (
                CompleteExecution(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=f"RECONCILIATION_ABORT:{fact.reason_code}",
                ),
            )
        position = fact.authoritative_position
        if position is None:
            raise ValueError("CONTINUE requires authoritative_position")
        require_source(self.positions, position, material_trace_id=fact.material_trace_id)
        action = fact.resume_action
        if type(action) is ResumeWmsAction:
            return (self._resume_wms(fact, action),)
        if type(action) is ResumeDeviceAction:
            return (self._resume_device(fact, action),)
        if type(action) is ResumeWait:
            return (
                Wait(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=action.reason_code,
                ),
            )
        raise TypeError("CONTINUE requires a typed resume_action")

    @staticmethod
    def _resume_wms(fact: ReconciliationDecidedFact, action: ResumeWmsAction) -> CreateWmsConfirmation:
        position = fact.authoritative_position
        if position is None or action.operation not in _POSITION_WMS_OPERATIONS.get(position.location_type, set()):
            raise ValueError("WMS resume operation does not match authoritative position")
        if fact.evidence_id not in action.evidence_refs:
            raise ValueError("WMS resume must reference reconciliation evidence")
        return CreateWmsConfirmation(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            operation=action.operation,
            operation_id=action.operation_id,
            evidence_refs=action.evidence_refs,
            snapshot_refs=action.snapshot_refs,
        )

    def _resume_device(self, fact: ReconciliationDecidedFact, action: ResumeDeviceAction) -> CreateDeviceCommand | Wait:
        if action.source != fact.authoritative_position:
            raise ValueError("device resume source must equal authoritative position")
        expected = _DEVICE_RESUME_TOPOLOGY.get((action.source.location_type, action.target.location_type))
        if expected != (action.device_role, action.task_type):
            raise ValueError("device resume action does not match approved topology")
        if not action.device_ready:
            return Wait(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                reason_code=f"{action.device_role}_NOT_READY",
            )
        require_target(self.positions, action.target)
        return CreateDeviceCommand(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            device_role=action.device_role,
            task_type=action.task_type,
            material_trace_id=fact.material_trace_id,
            source=action.source,
            target=action.target,
        )


__all__ = ["ReconciliationDecidedHandler"]
