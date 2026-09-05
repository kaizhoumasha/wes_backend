"""WES 与 WMS 之间当前启用 operation 的严格线上合同。"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from pydantic import (
    AfterValidator,
    Field,
    StringConstraints,
    model_validator,
)
from wes_plugin_sdk.validation import validate_persistable_text

from src.app.wms_adapter.wire_common import (
    OperationId,
    PositiveInteger,
    PositiveMilliseconds,
)
from src.app.wms_adapter.wire_common import (
    StrictWireModel as _StrictModel,
)

ADMISSION_OPERATION = "inbound.material.admission_decide@v1"
TARGET_OPERATION = "inbound.material.target_decide@v1"
PLACEMENT_OPERATION = "inbound.material.placement_report@v1"
NG_PLACEMENT_OPERATION = "inbound.material.ng_placement_report@v1"
REPLACEMENT_PLAN_OPERATION = "inbound.source_rack.replacement_plan_decide@v1"
RECOVERY_OPERATION = "inbound.execution.recovery_decided@v1"

OUTBOUND_OPERATIONS = frozenset(
    {
        ADMISSION_OPERATION,
        TARGET_OPERATION,
        PLACEMENT_OPERATION,
        NG_PLACEMENT_OPERATION,
        REPLACEMENT_PLAN_OPERATION,
    }
)
DECISION_OPERATIONS = frozenset({ADMISSION_OPERATION, TARGET_OPERATION, REPLACEMENT_PLAN_OPERATION})
FACT_OPERATIONS = frozenset({PLACEMENT_OPERATION, NG_PLACEMENT_OPERATION})
DECISION_PATH = "/api/v1/wes/decisions"
FACT_PATH = "/api/v1/wes/facts"
MAX_INBOUND_BODY_BYTES = 256 * 1024

_DECIMAL_MM_PATTERN = r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"


def _nonblank_utf8_identifier(value: str) -> str:
    return validate_persistable_text(value, "业务身份")


Identifier = Annotated[str, AfterValidator(_nonblank_utf8_identifier)]
ExecutionCode = Annotated[str, StringConstraints(max_length=120), AfterValidator(_nonblank_utf8_identifier)]
MaterialTraceId = Annotated[str, StringConstraints(max_length=160), AfterValidator(_nonblank_utf8_identifier)]
CommandCode = Annotated[str, StringConstraints(max_length=160), AfterValidator(_nonblank_utf8_identifier)]
RetryAfterMilliseconds = Annotated[int, Field(strict=True, ge=1, le=60_000)]
MeasurementMillimeters = Annotated[str, StringConstraints(pattern=_DECIMAL_MM_PATTERN)]


def _nonblank_device_text(value: str) -> str:
    return validate_persistable_text(value, "六合一码字段")


DeviceText = Annotated[str, AfterValidator(_nonblank_device_text)]


class HandoffPosition(_StrictModel):
    type: Literal["HANDOFF_POSITION"]
    location_code: Identifier


class NgPosition(_StrictModel):
    type: Literal["NG_POSITION"]
    location_code: Identifier


class RackPosition(_StrictModel):
    type: Literal["RACK_POSITION"]
    location_code: Identifier


class RackMoveRackReference(_StrictModel):
    kind: Literal["RACK"]
    location_code: Identifier


class RackMoveZonePosition(_StrictModel):
    kind: Literal["ZONE"]
    location_code: Identifier


class RackMoveRackPosition(_StrictModel):
    kind: Literal["RACK_POSITION"]
    location_code: Identifier


type RackMovePosition = Annotated[
    RackMoveRackReference | RackMoveZonePosition | RackMoveRackPosition,
    Field(discriminator="kind"),
]


class OneLayerBinCell(_StrictModel):
    type: Literal["ONE_LAYER_BIN_CELL"]
    rack_id: Identifier
    rack_slot_code: Identifier
    bin_id: Identifier
    bin_cell_id: Identifier


type MaterialPosition = Annotated[
    HandoffPosition | NgPosition | OneLayerBinCell,
    Field(discriminator="type"),
]


class SixInOne(_StrictModel):
    LotCode: DeviceText
    DateCode: DeviceText
    Qty: DeviceText
    ProductNo: DeviceText
    MfrPN: DeviceText
    PONumber: DeviceText


class Measurements(_StrictModel):
    diameter_mm: MeasurementMillimeters
    thickness_mm: MeasurementMillimeters


class AdmissionRequestData(_StrictModel):
    material_execution_id: ExecutionCode
    material_trace_id: MaterialTraceId
    six_in_one: SixInOne
    measurements: Measurements
    shape_result: Literal["PASS", "FAIL"]
    line_run_epoch_id: Identifier
    workline_code: Identifier
    source_position: HandoffPosition


class TargetRequestData(_StrictModel):
    material_execution_id: ExecutionCode
    material_trace_id: MaterialTraceId
    pkg_id: Identifier
    inbound_admission_id: Identifier
    source_position: HandoffPosition
    current_rack_id: Identifier


class PlacementRequestData(_StrictModel):
    material_execution_id: ExecutionCode
    material_trace_id: MaterialTraceId
    pkg_id: Identifier
    inbound_admission_id: Identifier
    target_assignment_id: Identifier
    target_position: OneLayerBinCell
    placement_sequence: PositiveInteger
    command_code: CommandCode
    placed_at: PositiveMilliseconds


class NgPlacementRequestData(_StrictModel):
    material_execution_id: ExecutionCode
    material_trace_id: MaterialTraceId
    pkg_id: Identifier | None = None
    ng_evidence_id: Identifier
    ng_position: NgPosition
    reason_code: Identifier
    business_context: Identifier

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_pkg_id(cls, value: Any) -> Any:
        return _reject_explicit_null(value, "pkg_id")


class ReplacementPlanRequestData(_StrictModel):
    material_execution_id: ExecutionCode
    material_trace_id: MaterialTraceId
    current_rack_id: Identifier


class AdmissionRequest(_StrictModel):
    operation_id: OperationId
    operation: Literal["inbound.material.admission_decide@v1"]
    timestamp: PositiveMilliseconds
    data: AdmissionRequestData


class TargetRequest(_StrictModel):
    operation_id: OperationId
    operation: Literal["inbound.material.target_decide@v1"]
    timestamp: PositiveMilliseconds
    data: TargetRequestData


class PlacementRequest(_StrictModel):
    operation_id: OperationId
    operation: Literal["inbound.material.placement_report@v1"]
    timestamp: PositiveMilliseconds
    data: PlacementRequestData


class NgPlacementRequest(_StrictModel):
    operation_id: OperationId
    operation: Literal["inbound.material.ng_placement_report@v1"]
    timestamp: PositiveMilliseconds
    data: NgPlacementRequestData


class ReplacementPlanRequest(_StrictModel):
    operation_id: OperationId
    operation: Literal["inbound.source_rack.replacement_plan_decide@v1"]
    timestamp: PositiveMilliseconds
    data: ReplacementPlanRequestData


type OutboundRequest = AdmissionRequest | TargetRequest | PlacementRequest | NgPlacementRequest | ReplacementPlanRequest


class AdmissionAccepted(_StrictModel):
    result: Literal["ACCEPT"]
    pkg_id: Identifier
    inbound_admission_id: Identifier


class Rejected(_StrictModel):
    result: Literal["REJECT"]
    reason_code: Identifier
    ng_destination: NgPosition


class Wait(_StrictModel):
    result: Literal["WAIT"]
    reason_code: Identifier
    retry_after_ms: RetryAfterMilliseconds


type AdmissionDecision = Annotated[AdmissionAccepted | Rejected | Wait, Field(discriminator="result")]


class TargetAssigned(_StrictModel):
    result: Literal["ASSIGNED"]
    target_assignment_id: Identifier
    target_position: OneLayerBinCell
    placement_sequence: PositiveInteger
    expected_height_mm: MeasurementMillimeters


class NoAvailableCell(_StrictModel):
    result: Literal["NO_AVAILABLE_CELL"]
    reason_code: Identifier


type TargetDecision = Annotated[TargetAssigned | NoAvailableCell | Rejected | Wait, Field(discriminator="result")]


class RackMovePlan(_StrictModel):
    rack_id: Identifier
    source: RackMovePosition
    target: RackMovePosition
    target_face: Annotated[str, Field(min_length=1, pattern=r"^[^\x00]+$")]

    @model_validator(mode="after")
    def validate_rack_reference_identity(self) -> RackMovePlan:
        for position in (self.source, self.target):
            if isinstance(position, RackMoveRackReference) and position.location_code != self.rack_id:
                raise ValueError("RACK location_code 必须等于 rack_id")
        return self


class ReplacementReady(_StrictModel):
    result: Literal["READY"]
    rack_replacement_id: Identifier
    old_loaded_rack: RackMovePlan
    new_empty_rack: RackMovePlan


type ReplacementDecision = Annotated[ReplacementReady | Wait, Field(discriminator="result")]


class EmptyData(_StrictModel):
    pass


class AdmissionDecisionResponse(_StrictModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: PositiveMilliseconds
    data: AdmissionDecision


class TargetDecisionResponse(_StrictModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: PositiveMilliseconds
    data: TargetDecision


class ReplacementDecisionResponse(_StrictModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: PositiveMilliseconds
    data: ReplacementDecision


class FactResponse(_StrictModel):
    operation_id: OperationId
    code: Literal["RECORDED", "DUPLICATE"]
    timestamp: PositiveMilliseconds
    data: EmptyData


class RejectedResponseData(_StrictModel):
    reason_code: Literal["INVALID_ENVELOPE", "UNSUPPORTED_OPERATION", "INVALID_DATA"]


class ConflictResponseData(_StrictModel):
    reason_code: Literal["IDEMPOTENCY_CONFLICT", "STATE_CONFLICT", "REFERENCE_CONFLICT", "POSITION_CONFLICT"]


class BusyResponseData(_StrictModel):
    retry_after_ms: RetryAfterMilliseconds


class RejectedResponse(_StrictModel):
    operation_id: OperationId
    code: Literal["REJECTED"]
    timestamp: PositiveMilliseconds
    data: RejectedResponseData


class ConflictResponse(_StrictModel):
    operation_id: OperationId
    code: Literal["CONFLICT"]
    timestamp: PositiveMilliseconds
    data: ConflictResponseData


class BusyResponse(_StrictModel):
    operation_id: OperationId
    code: Literal["BUSY"]
    timestamp: PositiveMilliseconds
    data: BusyResponseData


class UnavailableResponse(_StrictModel):
    operation_id: OperationId
    code: Literal["UNAVAILABLE"]
    timestamp: PositiveMilliseconds
    data: EmptyData


type OutboundResponse = (
    AdmissionDecisionResponse
    | TargetDecisionResponse
    | ReplacementDecisionResponse
    | FactResponse
    | RejectedResponse
    | ConflictResponse
    | BusyResponse
    | UnavailableResponse
)


class RecoveryData(_StrictModel):
    recovery_id: Identifier
    material_execution_id: ExecutionCode
    material_trace_id: MaterialTraceId
    reconciling_evidence_id: Identifier
    decision: Literal["CONTINUE", "ABORT"]
    authoritative_position: MaterialPosition | None
    reason_code: Identifier

    @model_validator(mode="after")
    def validate_position(self) -> RecoveryData:
        if self.decision == "CONTINUE" and self.authoritative_position is None:
            raise ValueError("CONTINUE 要求 authoritative_position 非空")
        return self


class RecoveryEvent(_StrictModel):
    operation_id: OperationId
    operation: Literal["inbound.execution.recovery_decided@v1"]
    timestamp: PositiveMilliseconds
    data: RecoveryData


def parse_outbound_request(value: object) -> OutboundRequest:
    if not isinstance(value, dict):
        raise TypeError("WMS operation request 必须是 JSON object")
    envelope = cast("dict[str, Any]", value)
    operation = envelope.get("operation")
    if operation == ADMISSION_OPERATION:
        return AdmissionRequest.model_validate(envelope)
    if operation == TARGET_OPERATION:
        return TargetRequest.model_validate(envelope)
    if operation == PLACEMENT_OPERATION:
        return PlacementRequest.model_validate(envelope)
    if operation == NG_PLACEMENT_OPERATION:
        return NgPlacementRequest.model_validate(envelope)
    if operation == REPLACEMENT_PLAN_OPERATION:
        return ReplacementPlanRequest.model_validate(envelope)
    raise ValueError("不支持的 WMS operation")


def parse_outbound_response(operation: str, http_status: int, value: object) -> OutboundResponse:
    if isinstance(value, dict):
        envelope = cast("dict[str, Any]", value)
        code = envelope.get("code")
        if (http_status, code) == (422, "REJECTED"):
            return RejectedResponse.model_validate(envelope)
        if (http_status, code) == (409, "CONFLICT"):
            return ConflictResponse.model_validate(envelope)
        if (http_status, code) == (429, "BUSY"):
            return BusyResponse.model_validate(envelope)
        if (http_status, code) == (503, "UNAVAILABLE"):
            return UnavailableResponse.model_validate(envelope)
    if http_status != 200:
        raise ValueError("HTTP status 与 WMS 响应 code 不匹配")
    if operation == ADMISSION_OPERATION:
        return AdmissionDecisionResponse.model_validate(value)
    if operation == TARGET_OPERATION:
        return TargetDecisionResponse.model_validate(value)
    if operation == REPLACEMENT_PLAN_OPERATION:
        return ReplacementDecisionResponse.model_validate(value)
    if operation in FACT_OPERATIONS:
        return FactResponse.model_validate(value)
    raise ValueError("不支持的 WMS operation")


def parse_recovery_event(value: object) -> RecoveryEvent:
    return RecoveryEvent.model_validate(value)


def _reject_explicit_null(value: Any, field_name: str) -> Any:
    if isinstance(value, dict) and field_name in value and value[field_name] is None:
        raise ValueError(f"{field_name} 有值时必须是非空 string，否则应省略")
    return value


__all__ = [
    "ADMISSION_OPERATION",
    "DECISION_OPERATIONS",
    "DECISION_PATH",
    "FACT_OPERATIONS",
    "FACT_PATH",
    "MAX_INBOUND_BODY_BYTES",
    "NG_PLACEMENT_OPERATION",
    "OUTBOUND_OPERATIONS",
    "PLACEMENT_OPERATION",
    "RECOVERY_OPERATION",
    "REPLACEMENT_PLAN_OPERATION",
    "TARGET_OPERATION",
    "OutboundRequest",
    "OutboundResponse",
    "RecoveryEvent",
    "parse_outbound_request",
    "parse_outbound_response",
    "parse_recovery_event",
]
