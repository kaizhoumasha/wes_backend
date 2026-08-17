"""可靠设备结果触发下一拓扑动作或最小对账。"""

from __future__ import annotations

from dataclasses import dataclass

from wes_plugin_sdk import (
    CreateDeviceCommand,
    CreateWmsConfirmation,
    DevicePosition,
    EpochConfigurationSnapshotReader,
    ExecutionSnapshotReader,
    PauseForReconciliation,
    PositionResourceSnapshotReader,
    Wait,
    handler,
)

from rough_sorter.facts import DeviceOutcome, DevicePositionConfirmedFact, DeviceStep
from rough_sorter.handlers._guards import require_epoch, require_execution, require_source, require_target

TARGET_OPERATION = "inbound.material.target_decide@v1"
PLACEMENT_OPERATION = "inbound.material.placement_report@v1"
NG_PLACEMENT_OPERATION = "inbound.material.ng_placement_report@v1"


@handler(
    fact_type=DevicePositionConfirmedFact,
    name="device-position-confirmed",
    supported_versions=("1.0",),
)
@dataclass(frozen=True, slots=True)
class DevicePositionConfirmedHandler:
    executions: ExecutionSnapshotReader
    positions: PositionResourceSnapshotReader
    epochs: EpochConfigurationSnapshotReader

    def __call__(
        self,
        fact: DevicePositionConfirmedFact,
    ) -> tuple[CreateDeviceCommand | CreateWmsConfirmation | PauseForReconciliation | Wait]:
        execution = require_execution(
            self.executions,
            material_execution_id=fact.material_execution_id,
            material_trace_id=fact.material_trace_id,
        )
        require_epoch(self.epochs, line_run_epoch_id=execution.line_run_epoch_id)
        if fact.outcome is not DeviceOutcome.SUCCESS:
            return (
                PauseForReconciliation(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code=fact.reason_code or "DEVICE_RESULT_RECONCILING",
                    affected_resource_ids=(
                        fact.device_code,
                        fact.source_position.location_id,
                        fact.target_position.location_id,
                    ),
                ),
            )

        actual_position = fact.actual_position
        if actual_position is None:
            raise ValueError("successful device result requires actual_position")
        require_source(self.positions, actual_position, material_trace_id=fact.material_trace_id)
        if fact.step is DeviceStep.MEASUREMENT_TO_INLET:
            return self._move_to_outlet(fact)
        if fact.step is DeviceStep.TRANSFER_TO_OUTLET:
            return (self._request_target(fact),)
        if fact.step is DeviceStep.PLACEMENT_TO_CELL:
            return (self._report_placement(fact),)
        return (self._report_ng(fact),)

    def _move_to_outlet(self, fact: DevicePositionConfirmedFact) -> tuple[CreateDeviceCommand | Wait]:
        if not fact.next_device_ready:
            return (
                Wait(
                    material_execution_id=fact.material_execution_id,
                    fact_id=fact.fact_id,
                    reason_code="TRANSFER_DEVICE_NOT_READY",
                ),
            )
        target = fact.next_position
        if target is None:
            raise ValueError("MEASUREMENT_TO_INLET requires next_position")
        actual_position = self._confirmed_position(fact)
        require_target(self.positions, target)
        return (
            CreateDeviceCommand(
                material_execution_id=fact.material_execution_id,
                fact_id=fact.fact_id,
                device_role="TRANSFER_DEVICE",
                task_type="MOVE_FORWARD",
                material_trace_id=fact.material_trace_id,
                source=actual_position,
                target=target,
            ),
        )

    @staticmethod
    def _request_target(fact: DevicePositionConfirmedFact) -> CreateWmsConfirmation:
        actual_position = DevicePositionConfirmedHandler._confirmed_position(fact)
        return CreateWmsConfirmation(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            operation=TARGET_OPERATION,
            operation_id=fact.request_operation_id or "",
            evidence_refs=(fact.evidence_id,),
            snapshot_refs=(
                f"execution:{fact.material_execution_id}",
                f"wms-admission:{fact.inbound_admission_id}",
                f"position:{actual_position.location_id}",
                f"rack:{fact.current_rack_id}",
            ),
        )

    @staticmethod
    def _confirmed_position(fact: DevicePositionConfirmedFact) -> DevicePosition:
        position = fact.actual_position
        if position is None:
            raise ValueError("successful device result requires actual_position")
        return position

    @staticmethod
    def _report_placement(fact: DevicePositionConfirmedFact) -> CreateWmsConfirmation:
        return CreateWmsConfirmation(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            operation=PLACEMENT_OPERATION,
            operation_id=fact.request_operation_id or "",
            evidence_refs=(fact.evidence_id,),
            snapshot_refs=(
                f"execution:{fact.material_execution_id}",
                f"command:{fact.command_code}",
                f"target-assignment:{fact.target_assignment_id}",
            ),
        )

    @staticmethod
    def _report_ng(fact: DevicePositionConfirmedFact) -> CreateWmsConfirmation:
        return CreateWmsConfirmation(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            operation=NG_PLACEMENT_OPERATION,
            operation_id=fact.request_operation_id or "",
            evidence_refs=(fact.evidence_id, fact.ng_evidence_id or ""),
            snapshot_refs=(
                f"execution:{fact.material_execution_id}",
                f"command:{fact.command_code}",
            ),
        )


__all__ = ["DevicePositionConfirmedHandler"]
