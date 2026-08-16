"""粗分机入库与 WMS 之间的严格线上合同。"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

ADMISSION_OPERATION = "inbound.material.admission_decide@v1"
TARGET_OPERATION = "inbound.material.target_decide@v1"
PLACEMENT_OPERATION = "inbound.material.placement_report@v1"
NG_PLACEMENT_OPERATION = "inbound.material.ng_placement_report@v1"
REPLACEMENT_PLAN_OPERATION = "inbound.source_rack.replacement_plan_decide@v1"
RECONCILIATION_OPERATION = "inbound.execution.reconciliation_decided@v1"

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

_UUIDV7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_DECIMAL_MM_PATTERN = r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"


def _nonblank_utf8_identifier(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError("业务身份必须是非空且不含 NUL 的 UTF-8 string")
    try:
        _ = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("业务身份必须是有效 UTF-8 string") from error
    return value


Identifier = Annotated[str, AfterValidator(_nonblank_utf8_identifier)]
ExecutionCode = Annotated[str, StringConstraints(max_length=120), AfterValidator(_nonblank_utf8_identifier)]
MaterialTraceId = Annotated[str, StringConstraints(max_length=160), AfterValidator(_nonblank_utf8_identifier)]
CommandCode = Annotated[str, StringConstraints(max_length=160), AfterValidator(_nonblank_utf8_identifier)]
OperationId = Annotated[str, StringConstraints(pattern=_UUIDV7_PATTERN)]
PositiveMilliseconds = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
PositiveInteger = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
RetryAfterMilliseconds = Annotated[int, Field(strict=True, ge=1, le=60_000)]
MeasurementMillimeters = Annotated[str, StringConstraints(pattern=_DECIMAL_MM_PATTERN)]


def _nonblank_device_text(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError("六合一码字段必须是非空 UTF-8 string")
    try:
        _ = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("六合一码字段必须是有效 UTF-8 string") from error
    return value


DeviceText = Annotated[str, AfterValidator(_nonblank_device_text)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class HandoffPosition(_StrictModel):
    type: Literal["HANDOFF_POSITION"]
    location_code: Identifier


class NgPosition(_StrictModel):
    type: Literal["NG_POSITION"]
    location_code: Identifier


class RackPosition(_StrictModel):
    type: Literal["RACK_POSITION"]
    location_code: Identifier


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
    business_context: Literal["ROUGH_SORT_INBOUND"]

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
    source: RackPosition
    target: RackPosition
    target_face: Literal["A", "B"]


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


class AuthoritativePosition(_StrictModel):
    material_execution_id: ExecutionCode
    material_trace_id: MaterialTraceId
    pkg_id: Identifier | None = None
    position: MaterialPosition | None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_pkg_id(cls, value: Any) -> Any:
        return _reject_explicit_null(value, "pkg_id")


class ReconciliationData(_StrictModel):
    reconciliation_id: Identifier
    affected_execution_ids: Annotated[list[ExecutionCode], Field(min_length=1)]
    authoritative_positions: Annotated[list[AuthoritativePosition], Field(min_length=1)]
    decision: Literal["CONTINUE", "ABORT"]
    reason_code: Identifier

    @model_validator(mode="after")
    def validate_correspondence(self) -> ReconciliationData:
        affected = self.affected_execution_ids
        positioned = [item.material_execution_id for item in self.authoritative_positions]
        if len(set(affected)) != len(affected) or len(set(positioned)) != len(positioned):
            raise ValueError("执行身份数组不得包含重复成员")
        if set(affected) != set(positioned) or len(affected) != len(positioned):
            raise ValueError("权威位置必须与受影响执行一一对应")
        if self.decision == "CONTINUE" and any(item.position is None for item in self.authoritative_positions):
            raise ValueError("CONTINUE 要求全部权威位置非空")
        return self


class ReconciliationEvent(_StrictModel):
    operation_id: OperationId
    operation: Literal["inbound.execution.reconciliation_decided@v1"]
    timestamp: PositiveMilliseconds
    data: ReconciliationData


def parse_outbound_request(value: object) -> OutboundRequest:
    if not isinstance(value, dict):
        raise TypeError("粗分 WMS request 必须是 JSON object")
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
    raise ValueError("不支持的粗分 WMS operation")


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
    raise ValueError("不支持的粗分 WMS operation")


def parse_reconciliation_event(value: object) -> ReconciliationEvent:
    return ReconciliationEvent.model_validate(value)


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
    "RECONCILIATION_OPERATION",
    "REPLACEMENT_PLAN_OPERATION",
    "TARGET_OPERATION",
    "OutboundRequest",
    "OutboundResponse",
    "ReconciliationEvent",
    "parse_outbound_request",
    "parse_outbound_response",
    "parse_reconciliation_event",
]
