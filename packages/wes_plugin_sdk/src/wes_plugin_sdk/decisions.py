"""插件可返回的封闭、不可变执行决策。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from .validation import freeze_json_object, validate_opaque_face, validate_persistable_text
from .validation import validate_required_refs as _required_refs
from .validation import validate_required_text as _required


class TransportTaskType(StrEnum):
    RACK_MOVE = "RACK_MOVE"


class TransportRcsTemplateId(StrEnum):
    CTU01 = "CTU01"
    CTU02 = "CTU02"
    CTU03 = "CTU03"
    F01 = "F01"


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
    kind: Literal["RACK_POSITION"] = "RACK_POSITION"

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code")
        if self.kind != "RACK_POSITION":
            raise ValueError("kind must be RACK_POSITION")


@dataclass(frozen=True, slots=True)
class TransportRackReference:
    location_code: str
    kind: Literal["RACK"] = "RACK"

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code")
        if self.kind != "RACK":
            raise ValueError("kind must be RACK")


@dataclass(frozen=True, slots=True)
class TransportZonePosition:
    location_code: str
    kind: Literal["ZONE"] = "ZONE"

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code")
        if self.kind != "ZONE":
            raise ValueError("kind must be ZONE")


type TransportRackMovePosition = TransportRackReference | TransportZonePosition | TransportRackPosition


def _validate_reasoned_execution_decision(material_execution_id: str, fact_id: str, reason_code: str) -> None:
    _required(material_execution_id, "material_execution_id")
    _required(fact_id, "fact_id")
    _required(reason_code, "reason_code")


@dataclass(frozen=True, slots=True)
class Wait:
    material_execution_id: str
    fact_id: str
    reason_code: str

    def __post_init__(self) -> None:
        _validate_reasoned_execution_decision(self.material_execution_id, self.fact_id, self.reason_code)


@dataclass(frozen=True, slots=True)
class DeferExecution:
    material_execution_id: str
    fact_id: str
    reason_code: str

    def __post_init__(self) -> None:
        _validate_reasoned_execution_decision(self.material_execution_id, self.fact_id, self.reason_code)


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
    request_data: dict[str, object]

    def __post_init__(self) -> None:
        _required(self.material_execution_id, "material_execution_id")
        _required(self.fact_id, "fact_id")
        _required(self.operation, "operation")
        _required(self.operation_id, "operation_id")
        object.__setattr__(self, "request_data", freeze_json_object(self.request_data, "request_data"))


@dataclass(frozen=True, slots=True)
class CreateTransportTask:
    material_execution_id: str
    fact_id: str
    task_type: TransportTaskType
    correlation_id: str
    step: str
    resource_fence_id: str
    rack_id: str
    source: TransportRackMovePosition
    target: TransportRackMovePosition
    target_face: str
    rcs_template_id: TransportRcsTemplateId

    def __post_init__(self) -> None:
        _required(self.material_execution_id, "material_execution_id")
        _required(self.fact_id, "fact_id")
        validate_persistable_text(self.correlation_id, "correlation_id", max_length=160)
        validate_persistable_text(self.step, "step", max_length=80)
        validate_persistable_text(self.resource_fence_id, "resource_fence_id", max_length=160)
        _required(self.rack_id, "rack_id")
        if type(self.task_type) is not TransportTaskType or self.task_type is not TransportTaskType.RACK_MOVE:
            raise ValueError("task_type must be RACK_MOVE")
        position_types = {TransportRackReference, TransportZonePosition, TransportRackPosition}
        if type(self.source) not in position_types or type(self.target) not in position_types:
            raise TypeError("source and target must be Transport rack move positions")
        validate_opaque_face(self.target_face, "target_face")
        if type(self.rcs_template_id) is not TransportRcsTemplateId:
            raise ValueError("rcs_template_id must be a TransportRcsTemplateId")
        if self.source == self.target:
            raise ValueError("source and target must differ")
        for position in (self.source, self.target):
            if type(position) is TransportRackReference and position.location_code != self.rack_id:
                raise ValueError("RACK location_code must match rack_id")
        allowed_edges = {
            TransportRcsTemplateId.CTU01: {
                (TransportZonePosition, TransportRackPosition),
                (TransportRackReference, TransportRackPosition),
                (TransportRackPosition, TransportRackPosition),
            },
            TransportRcsTemplateId.CTU03: {
                (TransportRackPosition, TransportRackReference),
                (TransportRackPosition, TransportZonePosition),
                (TransportRackPosition, TransportRackPosition),
            },
            TransportRcsTemplateId.F01: {(TransportRackPosition, TransportRackPosition)},
        }
        if (type(self.source), type(self.target)) not in allowed_edges.get(self.rcs_template_id, set()):
            raise ValueError("source, target, and rcs_template_id are not an approved edge")

    @property
    def correlation_identity(self) -> tuple[str, str]:
        return self.correlation_id, self.step


@dataclass(frozen=True, slots=True)
class PauseForReconciliation:
    material_execution_id: str
    fact_id: str
    reason_code: str
    affected_resource_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_reasoned_execution_decision(self.material_execution_id, self.fact_id, self.reason_code)
        _required_refs(self.affected_resource_ids, "affected_resource_ids")


@dataclass(frozen=True, slots=True)
class CompleteExecution:
    material_execution_id: str
    fact_id: str
    reason_code: str

    def __post_init__(self) -> None:
        _validate_reasoned_execution_decision(self.material_execution_id, self.fact_id, self.reason_code)


Decision = (
    Wait
    | DeferExecution
    | CreateDeviceCommand
    | CreateWmsConfirmation
    | CreateTransportTask
    | PauseForReconciliation
    | CompleteExecution
)
