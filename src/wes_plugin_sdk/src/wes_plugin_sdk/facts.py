"""已验证事实的不可变触发引用。"""

from dataclasses import dataclass
from enum import StrEnum

from .decisions import DevicePosition
from .validation import validate_required_text as _required
from .wms_types import (
    AdmissionOutcome,
    NgPlacementOutcome,
    PickingTaskPrepareOutcome,
    PlacementOutcome,
    ReplacementPlanOutcome,
    TargetOutcome,
    WmsOperationOutcome,
)


@dataclass(frozen=True, slots=True)
class FactReference:
    fact_id: str
    evidence_id: str
    fact_version: str
    material_execution_id: str

    def __post_init__(self) -> None:
        for field_name in ("fact_id", "evidence_id", "fact_version", "material_execution_id"):
            _ = _required(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class EvidenceReadyFact(FactReference):
    pass


@dataclass(frozen=True, slots=True)
class WmsResultReadyFact(FactReference):
    operation_id: str
    outcome: WmsOperationOutcome

    def __post_init__(self) -> None:
        FactReference.__post_init__(self)
        _ = _required(self.operation_id, "operation_id")
        if type(self.outcome) not in (
            AdmissionOutcome,
            TargetOutcome,
            ReplacementPlanOutcome,
            PlacementOutcome,
            NgPlacementOutcome,
            PickingTaskPrepareOutcome,
        ):
            raise TypeError("outcome must be a typed WMS operation outcome")


@dataclass(frozen=True, slots=True)
class DeviceResultReadyFact(FactReference):
    command_code: str
    device_code: str
    material_trace_id: str

    def __post_init__(self) -> None:
        FactReference.__post_init__(self)
        _ = _required(self.command_code, "command_code")
        _ = _required(self.device_code, "device_code")
        _ = _required(self.material_trace_id, "material_trace_id")


@dataclass(frozen=True, slots=True)
class TransportResultReadyFact(FactReference):
    transport_task_id: str

    def __post_init__(self) -> None:
        FactReference.__post_init__(self)
        _ = _required(self.transport_task_id, "transport_task_id")


class RecoveryDecision(StrEnum):
    CONTINUE = "CONTINUE"
    ABORT = "ABORT"


@dataclass(frozen=True, slots=True)
class RecoveryDecidedFact(FactReference):
    recovery_id: str
    decision: RecoveryDecision
    authoritative_position: DevicePosition | None
    reason_code: str

    def __post_init__(self) -> None:
        FactReference.__post_init__(self)
        _ = _required(self.recovery_id, "recovery_id")
        _ = _required(self.reason_code, "reason_code")
        if type(self.decision) is not RecoveryDecision:
            raise TypeError("decision must be a RecoveryDecision")
        if self.authoritative_position is not None and type(self.authoritative_position) is not DevicePosition:
            raise TypeError("authoritative_position must be a DevicePosition")
        if self.decision is RecoveryDecision.CONTINUE and self.authoritative_position is None:
            raise ValueError("CONTINUE requires authoritative_position")


Fact = FactReference
