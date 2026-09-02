"""恢复裁决触发终止或一个经过因果验证的类型化续作。"""

from __future__ import annotations

from wes_plugin_sdk import (
    CompleteExecution,
    CreateDeviceCommand,
    CreateWmsConfirmation,
    DeferExecution,
    handler,
)

from rough_sorter.facts import (
    RecoveryDecidedFact,
    RecoveryDecision,
    RecoveryDeferContinuation,
    RecoveryDeviceContinuation,
    RecoveryWmsContinuation,
)
from rough_sorter.handlers._guards import require_epoch, require_execution

_POSITION_WMS_OPERATIONS = {
    "MEASUREMENT_POSITION": {"inbound.material.admission_decide@v1"},
    "PIPELINE_OUTLET": {
        "inbound.material.target_decide@v1",
        "inbound.source_rack.replacement_plan_decide@v1",
    },
    "RACK_CELL": {"inbound.material.placement_report@v1"},
    "NG_POSITION": {"inbound.material.ng_placement_report@v1"},
}

_DEVICE_RECOVERY_TOPOLOGY = {
    ("MEASUREMENT_POSITION", "PIPELINE_INLET"): ("MEASUREMENT_DEVICE", "PICK_AND_PUT"),
    ("MEASUREMENT_POSITION", "NG_POSITION"): ("MEASUREMENT_DEVICE", "PICK_AND_PUT"),
    ("PIPELINE_INLET", "PIPELINE_OUTLET"): ("TRANSFER_DEVICE", "MOVE_FORWARD"),
    ("PIPELINE_OUTLET", "RACK_CELL"): ("PLACEMENT_DEVICE", "PICK_AND_PUT"),
    ("PIPELINE_OUTLET", "NG_POSITION"): ("PLACEMENT_DEVICE", "PICK_AND_PUT"),
}


@handler(
    fact_type=RecoveryDecidedFact,
    name="recovery-decided",
    supported_versions=("1.0",),
)
class RecoveryDecidedHandler:
    def __call__(
        self,
        fact: RecoveryDecidedFact,
    ) -> tuple[CompleteExecution | CreateDeviceCommand | CreateWmsConfirmation | DeferExecution]:
        snapshot = fact.runtime_snapshot
        execution = require_execution(
            snapshot.execution,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
            allow_reconciling=True,
        )
        _ = require_epoch(snapshot.epoch, line_run_epoch_id=execution.line_run_epoch_id)
        if fact.decision is RecoveryDecision.ABORT:
            return (
                CompleteExecution(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=f"RECOVERY_ABORT:{fact.reason_code}",
                ),
            )
        position = fact.authoritative_position
        if position is None:
            raise ValueError("CONTINUE requires authoritative_position")
        continuation = fact.continuation
        if type(continuation) is RecoveryWmsContinuation:
            return (self._continue_wms(fact, continuation),)
        if type(continuation) is RecoveryDeviceContinuation:
            return (self._continue_device(fact, continuation),)
        if type(continuation) is RecoveryDeferContinuation:
            return (
                DeferExecution(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=continuation.reason_code,
                ),
            )
        raise TypeError("CONTINUE requires a typed continuation")

    @staticmethod
    def _continue_wms(fact: RecoveryDecidedFact, continuation: RecoveryWmsContinuation) -> CreateWmsConfirmation:
        position = fact.authoritative_position
        if position is None or continuation.operation not in _POSITION_WMS_OPERATIONS.get(
            position.location_type, set()
        ):
            raise ValueError("WMS continuation does not match authoritative position")
        return CreateWmsConfirmation(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            operation=continuation.operation,
            operation_id=continuation.operation_id,
            request_data=continuation.request_data,
        )

    def _continue_device(
        self,
        fact: RecoveryDecidedFact,
        continuation: RecoveryDeviceContinuation,
    ) -> CreateDeviceCommand | DeferExecution:
        if continuation.source != fact.authoritative_position:
            raise ValueError("device continuation source must equal authoritative position")
        expected = _DEVICE_RECOVERY_TOPOLOGY.get((continuation.source.location_type, continuation.target.location_type))
        if expected != (continuation.device_role, continuation.task_type):
            raise ValueError("device continuation does not match approved topology")
        if not continuation.device_ready:
            return DeferExecution(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                reason_code=f"{continuation.device_role}_NOT_READY",
            )
        return CreateDeviceCommand(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            device_role=continuation.device_role,
            task_type=continuation.task_type,
            material_trace_id=fact.material_trace_id,
            source=continuation.source,
            target=continuation.target,
        )


__all__ = ["RecoveryDecidedHandler"]
