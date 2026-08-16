"""插件可返回的封闭、不可变执行决策。"""

from dataclasses import dataclass
from enum import StrEnum


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _required_refs(values: tuple[str, ...], field_name: str) -> None:
    if type(values) is not tuple:
        raise TypeError(f"{field_name} must be a tuple")
    if not values:
        raise ValueError(f"{field_name} must not be empty")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    for value in values:
        _required(value, field_name)


class TransportTaskType(StrEnum):
    RACK_MOVE = "RACK_MOVE"


class TransportLeg(StrEnum):
    OLD_OUT = "OLD_OUT"
    NEW_IN = "NEW_IN"


class RackFace(StrEnum):
    A = "A"
    B = "B"


@dataclass(frozen=True, slots=True)
class DevicePosition:
    location_id: str
    location_type: str
    material_trace_id: str
    rack_id: str | None = None
    rack_slot_code: str | None = None
    bin_id: str | None = None
    bin_cell_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.location_id, "location_id")
        _required(self.location_type, "location_type")
        _required(self.material_trace_id, "material_trace_id")
        for field_name in ("rack_id", "rack_slot_code", "bin_id", "bin_cell_id"):
            value = getattr(self, field_name)
            if value is not None:
                _required(value, field_name)


@dataclass(frozen=True, slots=True)
class TransportRackPosition:
    location_code: str

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code")


@dataclass(frozen=True, slots=True)
class Wait:
    material_execution_id: str
    fact_id: str
    reason_code: str

    def __post_init__(self) -> None:
        _required(self.material_execution_id, "material_execution_id")
        _required(self.fact_id, "fact_id")
        _required(self.reason_code, "reason_code")


@dataclass(frozen=True, slots=True)
class CreateDeviceCommand:
    material_execution_id: str
    fact_id: str
    device_role: str
    task_type: str
    material_trace_id: str
    source: DevicePosition
    target: DevicePosition

    def __post_init__(self) -> None:
        _required(self.material_execution_id, "material_execution_id")
        _required(self.fact_id, "fact_id")
        _required(self.device_role, "device_role")
        _required(self.task_type, "task_type")
        _required(self.material_trace_id, "material_trace_id")
        if type(self.source) is not DevicePosition or type(self.target) is not DevicePosition:
            raise TypeError("source and target must be DevicePosition values")
        if (
            self.source.material_trace_id != self.material_trace_id
            or self.target.material_trace_id != self.material_trace_id
        ):
            raise ValueError("source and target must reference material_trace_id")
        if self.source == self.target:
            raise ValueError("source and target must differ")


@dataclass(frozen=True, slots=True)
class CreateWmsConfirmation:
    material_execution_id: str
    fact_id: str
    operation: str
    operation_id: str
    evidence_refs: tuple[str, ...]
    snapshot_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(self.material_execution_id, "material_execution_id")
        _required(self.fact_id, "fact_id")
        _required(self.operation, "operation")
        _required(self.operation_id, "operation_id")
        _required_refs(self.evidence_refs, "evidence_refs")
        _required_refs(self.snapshot_refs, "snapshot_refs")


@dataclass(frozen=True, slots=True)
class CreateTransportTask:
    material_execution_id: str
    fact_id: str
    task_type: TransportTaskType
    rack_replacement_id: str
    leg: TransportLeg
    rack_id: str
    source: TransportRackPosition
    target: TransportRackPosition
    target_face: RackFace

    def __post_init__(self) -> None:
        _required(self.material_execution_id, "material_execution_id")
        _required(self.fact_id, "fact_id")
        _required(self.rack_replacement_id, "rack_replacement_id")
        _required(self.rack_id, "rack_id")
        if type(self.task_type) is not TransportTaskType or self.task_type is not TransportTaskType.RACK_MOVE:
            raise ValueError("task_type must be RACK_MOVE")
        if type(self.leg) is not TransportLeg:
            raise ValueError("leg must be a TransportLeg")
        if type(self.source) is not TransportRackPosition or type(self.target) is not TransportRackPosition:
            raise TypeError("source and target must be TransportRackPosition values")
        if type(self.target_face) is not RackFace:
            raise ValueError("target_face must be a RackFace")
        if self.source == self.target:
            raise ValueError("source and target must differ")

    @property
    def business_identity(self) -> tuple[str, TransportLeg]:
        return self.rack_replacement_id, self.leg


@dataclass(frozen=True, slots=True)
class PauseForReconciliation:
    material_execution_id: str
    fact_id: str
    reason_code: str
    affected_resource_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(self.material_execution_id, "material_execution_id")
        _required(self.fact_id, "fact_id")
        _required(self.reason_code, "reason_code")
        _required_refs(self.affected_resource_ids, "affected_resource_ids")


@dataclass(frozen=True, slots=True)
class CompleteExecution:
    material_execution_id: str
    fact_id: str
    reason_code: str

    def __post_init__(self) -> None:
        _required(self.material_execution_id, "material_execution_id")
        _required(self.fact_id, "fact_id")
        _required(self.reason_code, "reason_code")


Decision = (
    Wait
    | CreateDeviceCommand
    | CreateWmsConfirmation
    | CreateTransportTask
    | PauseForReconciliation
    | CompleteExecution
)
