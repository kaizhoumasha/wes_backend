"""无 I/O 的 WMS 业务意图与封闭结果；不承载 wire 信封。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .validation import validate_opaque_face
from .validation import validate_required_text as _required

if TYPE_CHECKING:
    from .decisions import DevicePosition, TransportRackMovePosition, TransportRackPosition


def _positive(value: int, name: str, maximum: int | None = None) -> None:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be a positive bounded integer")


def _millimeters(value: str, name: str) -> None:
    # 字符扫描有界于输入长度；不展开 exponent 或创建巨大整数。
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is None:
        raise ValueError(f"{name} must be a decimal millimeter string")


def _position(value: DevicePosition, kind: str, trace: str | None = None) -> None:
    from .decisions import DevicePosition

    if type(value) is not DevicePosition:
        raise TypeError("position must be a DevicePosition")
    if value.location_type != kind:
        raise ValueError(f"position must be {kind}")
    if trace is not None and value.material_trace_id != trace:
        raise ValueError("position material_trace_id must match intent")
    if kind == "RACK_CELL":
        for name in ("rack_id", "rack_slot_code", "bin_code", "bin_cell_id"):
            _required(getattr(value, name), name)


@dataclass(frozen=True, slots=True)
class SixInOne:
    LotCode: str
    DateCode: str
    Qty: str
    ProductNo: str
    MfrPN: str
    PONumber: str

    def __post_init__(self) -> None:
        for name in ("LotCode", "DateCode", "Qty", "ProductNo", "MfrPN", "PONumber"):
            _required(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class Measurements:
    diameter_mm: str
    thickness_mm: str

    def __post_init__(self) -> None:
        _millimeters(self.diameter_mm, "diameter_mm")
        _millimeters(self.thickness_mm, "thickness_mm")


@dataclass(frozen=True, slots=True, kw_only=True)
class _MaterialIntent:
    material_execution_id: str
    fact_id: str
    operation_id: str
    material_trace_id: str

    def __post_init__(self) -> None:
        for name in ("material_execution_id", "fact_id", "operation_id", "material_trace_id"):
            _required(getattr(self, name), name)


@dataclass(frozen=True, slots=True, kw_only=True)
class AdmissionIntent(_MaterialIntent):
    six_in_one: SixInOne
    measurements: Measurements
    shape_result: Literal["PASS", "FAIL"]
    workline_code: str
    source_position: DevicePosition

    def __post_init__(self) -> None:
        _MaterialIntent.__post_init__(self)
        if type(self.six_in_one) is not SixInOne or type(self.measurements) is not Measurements:
            raise TypeError("admission requires typed SixInOne and Measurements")
        if self.shape_result not in ("PASS", "FAIL"):
            raise ValueError("shape_result must be PASS or FAIL")
        _required(self.workline_code, "workline_code")
        _position(self.source_position, "MEASUREMENT_POSITION", self.material_trace_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetIntent(_MaterialIntent):
    pkg_id: str
    inbound_admission_id: str
    source_position: DevicePosition
    current_rack_id: str

    def __post_init__(self) -> None:
        _MaterialIntent.__post_init__(self)
        for name in ("pkg_id", "inbound_admission_id", "current_rack_id"):
            _required(getattr(self, name), name)
        _position(self.source_position, "PIPELINE_OUTLET", self.material_trace_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlacementIntent(_MaterialIntent):
    pkg_id: str
    inbound_admission_id: str
    target_assignment_id: str
    target_position: DevicePosition
    placement_sequence: int
    command_code: str
    placed_at: int

    def __post_init__(self) -> None:
        _MaterialIntent.__post_init__(self)
        for name in ("pkg_id", "inbound_admission_id", "target_assignment_id", "command_code"):
            _required(getattr(self, name), name)
        _position(self.target_position, "RACK_CELL", self.material_trace_id)
        _positive(self.placement_sequence, "placement_sequence")
        _positive(self.placed_at, "placed_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class NgPlacementIntent(_MaterialIntent):
    ng_evidence_id: str
    ng_position: DevicePosition
    reason_code: str
    business_context: str
    pkg_id: str | None = None

    def __post_init__(self) -> None:
        _MaterialIntent.__post_init__(self)
        for name in ("ng_evidence_id", "reason_code", "business_context"):
            _required(getattr(self, name), name)
        if self.pkg_id is not None:
            _required(self.pkg_id, "pkg_id")
        _position(self.ng_position, "NG_POSITION", self.material_trace_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplacementPlanIntent(_MaterialIntent):
    current_rack_id: str

    def __post_init__(self) -> None:
        _MaterialIntent.__post_init__(self)
        _required(self.current_rack_id, "current_rack_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class PickingTaskPrepareIntent:
    operation_id: str
    task_id: str
    work_line_code: str

    def __post_init__(self) -> None:
        for name in ("operation_id", "task_id", "work_line_code"):
            _required(getattr(self, name), name)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReturnRackArrivalReportIntent:
    operation_id: str
    task_id: str
    transport_task_id: str
    outcome_revision: int
    rack_id: str
    final_position: TransportRackPosition
    arrival_face: str

    def __post_init__(self) -> None:
        from .decisions import TransportRackPosition

        _required(self.operation_id, "operation_id")
        for name in ("task_id", "rack_id", "transport_task_id"):
            value = _required(getattr(self, name), name)
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", value) is None:
                raise ValueError(f"{name} must be a business identifier")
        if len(self.transport_task_id) > 80:
            raise ValueError("transport_task_id must not exceed 80 characters")
        _positive(self.outcome_revision, "outcome_revision", 2**63 - 1)
        if type(self.final_position) is not TransportRackPosition:
            raise TypeError("arrival requires TransportRackPosition")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", self.final_position.location_code) is None:
            raise ValueError("final_position location_code must be a business identifier")
        validate_opaque_face(self.arrival_face, "arrival_face")


@dataclass(frozen=True, slots=True, kw_only=True)
class BinInboundBatchIntent:
    operation_id: str
    task_id: str
    rack_id: str
    rack_face: str
    max_bin_count: int

    def __post_init__(self) -> None:
        _required(self.operation_id, "operation_id")
        for name in ("task_id", "rack_id"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(getattr(self, name), name)) is None:
                raise ValueError(f"{name} must be a business identifier")
        validate_opaque_face(self.rack_face, "rack_face")
        _positive(self.max_bin_count, "max_bin_count", 4)


@dataclass(frozen=True, slots=True)
class TransportRackBinSlot:
    rack_id: str
    rack_face: str
    slot_id: str

    def __post_init__(self) -> None:
        for name in ("rack_id", "slot_id"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(getattr(self, name), name)) is None:
                raise ValueError(f"{name} must be a business identifier")
        validate_opaque_face(self.rack_face, "rack_face")


@dataclass(frozen=True, slots=True)
class BinInboundBatchMember:
    bin_code: str
    source_locator: TransportRackBinSlot

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(self.bin_code, "bin_code")) is None:
            raise ValueError("bin_code must be a business identifier")
        if type(self.source_locator) is not TransportRackBinSlot:
            raise TypeError("source_locator requires TransportRackBinSlot")


@dataclass(frozen=True, slots=True)
class BinInboundBatchReady:
    bins: tuple[BinInboundBatchMember, ...]

    def __post_init__(self) -> None:
        if type(self.bins) is not tuple or any(type(member) is not BinInboundBatchMember for member in self.bins):
            raise TypeError("bins must be an immutable tuple of BinInboundBatchMember")
        if not 1 <= len(self.bins) <= 4 or len({member.bin_code for member in self.bins}) != len(self.bins):
            raise ValueError("bins must contain 1..4 unique members")
        if len({member.source_locator for member in self.bins}) != len(self.bins):
            raise ValueError("bins must use distinct source slots")


@dataclass(frozen=True, slots=True)
class BinBatchNoBatch:
    retry_after_ms: int

    def __post_init__(self) -> None:
        _positive(self.retry_after_ms, "retry_after_ms", 60000)


@dataclass(frozen=True, slots=True)
class BinInboundBatchRackFaceDone:
    pass


@dataclass(frozen=True, slots=True)
class BinReturnCandidate:
    sequence_no: int
    bin_code: str
    source_location_code: str

    def __post_init__(self) -> None:
        _positive(self.sequence_no, "sequence_no", 4)
        for name in ("bin_code", "source_location_code"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(getattr(self, name), name)) is None:
                raise ValueError(f"{name} must be a business identifier")


@dataclass(frozen=True, slots=True, kw_only=True)
class BinReturnBatchIntent:
    operation_id: str
    workline_code: str
    rack_id: str
    rack_face: str
    return_candidates: tuple[BinReturnCandidate, ...]

    def __post_init__(self) -> None:
        _required(self.operation_id, "operation_id")
        for name in ("workline_code", "rack_id"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(getattr(self, name), name)) is None:
                raise ValueError(f"{name} must be a business identifier")
        validate_opaque_face(self.rack_face, "rack_face")
        candidates = self.return_candidates
        if type(candidates) is not tuple or any(type(c) is not BinReturnCandidate for c in candidates):
            raise TypeError("return_candidates must be an immutable tuple of BinReturnCandidate")
        if not 1 <= len(candidates) <= 4 or [c.sequence_no for c in candidates] != list(range(1, len(candidates) + 1)):
            raise ValueError("return_candidates require a bounded FIFO prefix")
        if len({c.bin_code for c in candidates}) != len(candidates):
            raise ValueError("duplicate candidate Bin")


@dataclass(frozen=True, slots=True)
class BinReturnMove:
    sequence_no: int
    bin_code: str
    target: TransportRackBinSlot

    def __post_init__(self) -> None:
        _positive(self.sequence_no, "sequence_no", 4)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(self.bin_code, "bin_code")) is None:
            raise ValueError("bin_code must be a business identifier")
        if type(self.target) is not TransportRackBinSlot:
            raise TypeError("target requires TransportRackBinSlot")


@dataclass(frozen=True, slots=True)
class BinReturnBatchReady:
    moves: tuple[BinReturnMove, ...]

    def __post_init__(self) -> None:
        if type(self.moves) is not tuple or any(type(m) is not BinReturnMove for m in self.moves):
            raise TypeError("moves must be an immutable tuple of BinReturnMove")
        if not 1 <= len(self.moves) <= 4 or [m.sequence_no for m in self.moves] != list(range(1, len(self.moves) + 1)):
            raise ValueError("moves require a bounded FIFO prefix")
        if len({m.bin_code for m in self.moves}) != len(self.moves) or len({m.target for m in self.moves}) != len(
            self.moves
        ):
            raise ValueError("duplicate Bin or target")


@dataclass(frozen=True, slots=True, kw_only=True)
class BinWorkPlanIntent:
    operation_id: str
    task_id: str
    bin_code: str
    scanned_at: int

    def __post_init__(self) -> None:
        _required(self.operation_id, "operation_id")
        for name in ("task_id", "bin_code"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(getattr(self, name), name)) is None:
                raise ValueError(f"{name} must be a business identifier")
        if type(self.scanned_at) is not int or not 0 <= self.scanned_at <= 2**63 - 1:
            raise ValueError("scanned_at must be nonnegative int64 milliseconds")


@dataclass(frozen=True, slots=True)
class BinWorkPlanReady:
    cell_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.cell_ids) is not tuple:
            raise TypeError("cell_ids must be an immutable tuple")
        for cell_id in self.cell_ids:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(cell_id, "cell_id")) is None:
                raise ValueError("cell_id must be a business identifier")
        if not self.cell_ids or len(set(self.cell_ids)) != len(self.cell_ids):
            raise ValueError("cell_ids must be nonempty and unique")


@dataclass(frozen=True, slots=True)
class BinWorkPlanNoWork:
    pass


@dataclass(frozen=True, slots=True)
class BinWorkPlanWait:
    retry_after_ms: int

    def __post_init__(self) -> None:
        _positive(self.retry_after_ms, "retry_after_ms", 60000)


@dataclass(frozen=True, slots=True, kw_only=True)
class RackDepartureIntent:
    operation_id: str
    task_id: str
    rack_id: str
    current_location: TransportRackPosition
    current_face: str

    def __post_init__(self) -> None:
        from .decisions import TransportRackPosition

        _ = _required(self.operation_id, "operation_id")
        for name in ("task_id", "rack_id"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(getattr(self, name), name)) is None:
                raise ValueError(f"{name} must be a business identifier")
        if type(self.current_location) is not TransportRackPosition:
            raise TypeError("departure requires TransportRackPosition")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", self.current_location.location_code) is None:
            raise ValueError("current_location must use a business identifier")
        validate_opaque_face(self.current_face, "current_face")


@dataclass(frozen=True, slots=True)
class RackDepartureReady:
    rack_destination: TransportRackPosition

    def __post_init__(self) -> None:
        from .decisions import TransportRackPosition

        if type(self.rack_destination) is not TransportRackPosition:
            raise TypeError("departure destination requires TransportRackPosition")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", self.rack_destination.location_code) is None:
            raise ValueError("rack_destination must use a business identifier")


@dataclass(frozen=True, slots=True)
class RackDepartureWait:
    retry_after_ms: int

    def __post_init__(self) -> None:
        _positive(self.retry_after_ms, "retry_after_ms", 60000)


@dataclass(frozen=True, slots=True)
class PickingRackSlot:
    rack_id: str
    rack_face: str
    slot_id: str

    def __post_init__(self) -> None:
        for name in ("rack_id", "slot_id"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(getattr(self, name), name)) is None:
                raise ValueError(f"{name} must be a business identifier")
        validate_opaque_face(self.rack_face, "rack_face")


@dataclass(frozen=True, slots=True)
class PickingBinCell:
    rack_id: str
    rack_face: str
    bin_code: str
    cell_id: str

    def __post_init__(self) -> None:
        for name in ("rack_id", "bin_code", "cell_id"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(getattr(self, name), name)) is None:
                raise ValueError(f"{name} must be a business identifier")
        validate_opaque_face(self.rack_face, "rack_face")


@dataclass(frozen=True, slots=True)
class PickingSixInOne:
    HHPN: str
    MfrPN: str
    Qty: str
    DateCode: str
    LotCode: str
    PkgID: str

    def __post_init__(self) -> None:
        for name in ("HHPN", "MfrPN", "Qty", "DateCode", "LotCode", "PkgID"):
            value = getattr(self, name)
            if type(value) is not str or not 1 <= len(value) <= 256:
                raise ValueError(f"{name} must be original scan text of 1..256 characters")


@dataclass(frozen=True, slots=True, kw_only=True)
class PickingMaterialIntent:
    operation_id: str
    task_id: str
    source_locator: PickingRackSlot | PickingBinCell
    six_in_one: PickingSixInOne
    scanned_at: int

    def __post_init__(self) -> None:
        _ = _required(self.operation_id, "operation_id")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(self.task_id, "task_id")) is None:
            raise ValueError("task_id must be a business identifier")
        if type(self.source_locator) not in (PickingRackSlot, PickingBinCell):
            raise TypeError("source_locator requires PickingRackSlot or PickingBinCell")
        if type(self.six_in_one) is not PickingSixInOne:
            raise TypeError("six_in_one requires PickingSixInOne")
        if type(self.scanned_at) is not int or not 0 <= self.scanned_at <= 2**63 - 1:
            raise ValueError("scanned_at must be nonnegative int64 milliseconds")


@dataclass(frozen=True, slots=True)
class PickingTargetRotate:
    pass


@dataclass(frozen=True, slots=True)
class PickingTargetReplace:
    rack_destination: TransportRackPosition

    def __post_init__(self) -> None:
        from .decisions import TransportRackPosition

        if type(self.rack_destination) is not TransportRackPosition:
            raise TypeError("rack_destination requires TransportRackPosition")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", self.rack_destination.location_code) is None:
            raise ValueError("rack_destination must use a business identifier")


@dataclass(frozen=True, slots=True)
class PickingMaterialAccept:
    target_locator: PickingRackSlot
    next_source_action: Literal["CONTINUE", "SOURCE_DONE"]
    target_preparation: PickingTargetRotate | PickingTargetReplace | None = None

    def __post_init__(self) -> None:
        if type(self.target_locator) is not PickingRackSlot:
            raise TypeError("target_locator requires PickingRackSlot")
        if self.next_source_action not in ("CONTINUE", "SOURCE_DONE"):
            raise ValueError("unsupported next_source_action")
        if self.target_preparation is not None and type(self.target_preparation) not in (
            PickingTargetRotate,
            PickingTargetReplace,
        ):
            raise TypeError("target_preparation requires ROTATE or REPLACE")


@dataclass(frozen=True, slots=True)
class PickingMaterialReject:
    business_exception_code: Literal["MATERIAL_REJECTED", "SOURCE_CELL_MISMATCH"]
    ng_zone_code: str
    source_disposition: Literal["CONTINUE", "CLOSE"]

    def __post_init__(self) -> None:
        if self.business_exception_code not in ("MATERIAL_REJECTED", "SOURCE_CELL_MISMATCH"):
            raise ValueError("unsupported business_exception_code")
        if self.source_disposition not in ("CONTINUE", "CLOSE"):
            raise ValueError("unsupported source_disposition")
        if self.business_exception_code == "SOURCE_CELL_MISMATCH" and self.source_disposition != "CLOSE":
            raise ValueError("SOURCE_CELL_MISMATCH requires CLOSE")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(self.ng_zone_code, "ng_zone_code")) is None:
            raise ValueError("ng_zone_code must be a business identifier")


@dataclass(frozen=True, slots=True)
class PickingMaterialWait:
    retry_after_ms: int

    def __post_init__(self) -> None:
        _positive(self.retry_after_ms, "retry_after_ms", 60000)


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceEmptyIntent:
    operation_id: str
    task_id: str
    source_locator: PickingRackSlot | PickingBinCell
    observed_at: int

    def __post_init__(self) -> None:
        _ = _required(self.operation_id, "operation_id")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", _required(self.task_id, "task_id")) is None:
            raise ValueError("task_id must be a business identifier")
        if type(self.source_locator) not in (PickingRackSlot, PickingBinCell):
            raise TypeError("source_locator requires PickingRackSlot or PickingBinCell")
        if type(self.observed_at) is not int or not 0 <= self.observed_at <= 2**63 - 1:
            raise ValueError("observed_at must be nonnegative int64 milliseconds")


@dataclass(frozen=True, slots=True)
class SourceEmptyRetry:
    pass


@dataclass(frozen=True, slots=True)
class SourceEmptyDone:
    pass


@dataclass(frozen=True, slots=True)
class SourceEmptyWait:
    retry_after_ms: int

    def __post_init__(self) -> None:
        _positive(self.retry_after_ms, "retry_after_ms", 60000)


InboundWmsIntent = AdmissionIntent | TargetIntent | PlacementIntent | NgPlacementIntent | ReplacementPlanIntent
WmsOperationIntent = (
    InboundWmsIntent
    | PickingTaskPrepareIntent
    | ReturnRackArrivalReportIntent
    | BinInboundBatchIntent
    | BinReturnBatchIntent
    | BinWorkPlanIntent
    | RackDepartureIntent
    | PickingMaterialIntent
    | SourceEmptyIntent
)


@dataclass(frozen=True, slots=True)
class AdmissionAccepted:
    pkg_id: str
    inbound_admission_id: str

    def __post_init__(self) -> None:
        _required(self.pkg_id, "pkg_id")
        _required(self.inbound_admission_id, "inbound_admission_id")


@dataclass(frozen=True, slots=True)
class MaterialRejected:
    reason_code: str
    ng_destination: DevicePosition

    def __post_init__(self) -> None:
        _required(self.reason_code, "reason_code")
        _position(self.ng_destination, "NG_POSITION")


@dataclass(frozen=True, slots=True)
class OperationWait:
    reason_code: str
    retry_after_ms: int

    def __post_init__(self) -> None:
        _required(self.reason_code, "reason_code")
        _positive(self.retry_after_ms, "retry_after_ms", 60000)


@dataclass(frozen=True, slots=True)
class TargetAssigned:
    target_assignment_id: str
    target_position: DevicePosition
    placement_sequence: int
    expected_height_mm: str

    def __post_init__(self) -> None:
        _required(self.target_assignment_id, "target_assignment_id")
        _position(self.target_position, "RACK_CELL")
        _positive(self.placement_sequence, "placement_sequence")
        _millimeters(self.expected_height_mm, "expected_height_mm")


@dataclass(frozen=True, slots=True)
class NoAvailableCell:
    reason_code: str

    def __post_init__(self) -> None:
        _required(self.reason_code, "reason_code")


@dataclass(frozen=True, slots=True)
class RackMovePlan:
    rack_id: str
    source: TransportRackMovePosition
    target: TransportRackMovePosition
    target_face: str

    def __post_init__(self) -> None:
        from .decisions import TransportRackPosition, TransportRackReference, TransportZonePosition

        _required(self.rack_id, "rack_id")
        validate_opaque_face(self.target_face, "target_face")
        for position in (self.source, self.target):
            if type(position) not in (TransportRackPosition, TransportRackReference, TransportZonePosition):
                raise TypeError("rack move requires typed Transport positions")
            if type(position) is TransportRackReference and position.location_code != self.rack_id:
                raise ValueError("RACK location_code must match rack_id")


@dataclass(frozen=True, slots=True)
class ReplacementReady:
    rack_replacement_id: str
    old_loaded_rack: RackMovePlan
    new_empty_rack: RackMovePlan

    def __post_init__(self) -> None:
        _required(self.rack_replacement_id, "rack_replacement_id")
        if type(self.old_loaded_rack) is not RackMovePlan or type(self.new_empty_rack) is not RackMovePlan:
            raise TypeError("replacement requires typed RackMovePlan values")


@dataclass(frozen=True, slots=True)
class FactRecorded:
    duplicate: bool = False

    def __post_init__(self) -> None:
        if type(self.duplicate) is not bool:
            raise TypeError("duplicate must be bool")


@dataclass(frozen=True, slots=True)
class PrepareAccepted:
    pass


@dataclass(frozen=True, slots=True)
class OperationRejected:
    reason_code: Literal["INVALID_ENVELOPE", "UNSUPPORTED_OPERATION", "INVALID_DATA"]
    field_path: str | None = None

    def __post_init__(self) -> None:
        if self.reason_code not in ("INVALID_ENVELOPE", "UNSUPPORTED_OPERATION", "INVALID_DATA"):
            raise ValueError("unapproved rejection reason")
        if self.field_path is not None:
            _required(self.field_path, "field_path")
            if self.reason_code != "INVALID_DATA" or not self.field_path.startswith("/"):
                raise ValueError("field_path requires INVALID_DATA and JSON Pointer")


@dataclass(frozen=True, slots=True)
class OperationConflict:
    reason_code: Literal[
        "IDEMPOTENCY_CONFLICT", "STATE_CONFLICT", "REFERENCE_CONFLICT", "POSITION_CONFLICT", "REVISION_CONFLICT"
    ]

    def __post_init__(self) -> None:
        if self.reason_code not in (
            "IDEMPOTENCY_CONFLICT",
            "STATE_CONFLICT",
            "REFERENCE_CONFLICT",
            "POSITION_CONFLICT",
            "REVISION_CONFLICT",
        ):
            raise ValueError("unapproved conflict reason")


@dataclass(frozen=True, slots=True)
class OperationBusy:
    retry_after_ms: int

    def __post_init__(self) -> None:
        _positive(self.retry_after_ms, "retry_after_ms", 60000)


@dataclass(frozen=True, slots=True)
class OperationUnavailable:
    pass


type _InboundError = OperationRejected | OperationConflict | OperationBusy | OperationUnavailable


def _inbound_result(result: object, allowed: tuple[type, ...]) -> None:
    if type(result) not in (*allowed, OperationRejected, OperationConflict, OperationBusy, OperationUnavailable):
        raise TypeError("outcome requires an approved typed result")
    if type(result) is OperationConflict and result.reason_code == "REVISION_CONFLICT":
        raise ValueError("REVISION_CONFLICT is only approved for prepare")
    if type(result) is OperationRejected and result.field_path is not None:
        raise ValueError("field_path is only approved for prepare")


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    result: AdmissionAccepted | MaterialRejected | OperationWait | _InboundError

    def __post_init__(self) -> None:
        _inbound_result(self.result, (AdmissionAccepted, MaterialRejected, OperationWait))


@dataclass(frozen=True, slots=True)
class TargetOutcome:
    result: TargetAssigned | NoAvailableCell | MaterialRejected | OperationWait | _InboundError

    def __post_init__(self) -> None:
        _inbound_result(self.result, (TargetAssigned, NoAvailableCell, MaterialRejected, OperationWait))


@dataclass(frozen=True, slots=True)
class ReplacementPlanOutcome:
    result: ReplacementReady | OperationWait | _InboundError

    def __post_init__(self) -> None:
        _inbound_result(self.result, (ReplacementReady, OperationWait))


@dataclass(frozen=True, slots=True)
class PlacementOutcome:
    result: FactRecorded | _InboundError

    def __post_init__(self) -> None:
        _inbound_result(self.result, (FactRecorded,))


@dataclass(frozen=True, slots=True)
class NgPlacementOutcome:
    result: FactRecorded | _InboundError

    def __post_init__(self) -> None:
        _inbound_result(self.result, (FactRecorded,))


@dataclass(frozen=True, slots=True)
class PickingTaskPrepareOutcome:
    result: PrepareAccepted | OperationRejected | OperationConflict | OperationUnavailable

    def __post_init__(self) -> None:
        if type(self.result) not in (PrepareAccepted, OperationRejected, OperationConflict, OperationUnavailable):
            raise TypeError("prepare outcome requires an approved typed result")
        if type(self.result) is OperationConflict and self.result.reason_code == "POSITION_CONFLICT":
            raise ValueError("POSITION_CONFLICT is not approved for prepare")


def _picking_rejection_pointer(result: object) -> None:
    if (
        type(result) is OperationRejected
        and result.field_path is not None
        and (len(result.field_path) > 256 or re.fullmatch(r"/(?:[^~]|~[01])*", result.field_path) is None)
    ):
        raise ValueError("field_path must be a bounded JSON Pointer")


@dataclass(frozen=True, slots=True)
class ReturnRackArrivalReportOutcome:
    result: FactRecorded | OperationRejected | OperationConflict | OperationUnavailable

    def __post_init__(self) -> None:
        if type(self.result) not in (FactRecorded, OperationRejected, OperationConflict, OperationUnavailable):
            raise TypeError("arrival outcome requires an approved typed result")
        if type(self.result) is OperationConflict and self.result.reason_code == "POSITION_CONFLICT":
            raise ValueError("POSITION_CONFLICT is not approved for arrival report")
        _picking_rejection_pointer(self.result)


@dataclass(frozen=True, slots=True)
class BinInboundBatchOutcome:
    result: (
        BinInboundBatchReady
        | BinBatchNoBatch
        | BinInboundBatchRackFaceDone
        | OperationRejected
        | OperationConflict
        | OperationUnavailable
    )

    def __post_init__(self) -> None:
        if type(self.result) not in (
            BinInboundBatchReady,
            BinBatchNoBatch,
            BinInboundBatchRackFaceDone,
            OperationRejected,
            OperationConflict,
            OperationUnavailable,
        ):
            raise TypeError("inbound batch outcome requires an approved typed result")
        if type(self.result) is OperationConflict and self.result.reason_code == "POSITION_CONFLICT":
            raise ValueError("POSITION_CONFLICT is not approved for inbound batch")
        _picking_rejection_pointer(self.result)


@dataclass(frozen=True, slots=True)
class BinReturnBatchOutcome:
    result: BinReturnBatchReady | BinBatchNoBatch | OperationRejected | OperationConflict | OperationUnavailable

    def __post_init__(self) -> None:
        if type(self.result) not in (
            BinReturnBatchReady,
            BinBatchNoBatch,
            OperationRejected,
            OperationConflict,
            OperationUnavailable,
        ):
            raise TypeError("return batch requires an approved result")
        if type(self.result) is OperationConflict and self.result.reason_code == "POSITION_CONFLICT":
            raise ValueError("POSITION_CONFLICT is not approved for return batch")
        _picking_rejection_pointer(self.result)


@dataclass(frozen=True, slots=True)
class BinWorkPlanOutcome:
    result: (
        BinWorkPlanReady
        | BinWorkPlanNoWork
        | BinWorkPlanWait
        | OperationRejected
        | OperationConflict
        | OperationUnavailable
    )

    def __post_init__(self) -> None:
        if type(self.result) not in (
            BinWorkPlanReady,
            BinWorkPlanNoWork,
            BinWorkPlanWait,
            OperationRejected,
            OperationConflict,
            OperationUnavailable,
        ):
            raise TypeError("work plan requires an approved result")
        if type(self.result) is OperationConflict and self.result.reason_code == "POSITION_CONFLICT":
            raise ValueError("POSITION_CONFLICT is not approved for work plan")
        _picking_rejection_pointer(self.result)


@dataclass(frozen=True, slots=True)
class RackDepartureOutcome:
    result: RackDepartureReady | RackDepartureWait | OperationRejected | OperationConflict | OperationUnavailable

    def __post_init__(self) -> None:
        if type(self.result) not in (
            RackDepartureReady,
            RackDepartureWait,
            OperationRejected,
            OperationConflict,
            OperationUnavailable,
        ):
            raise TypeError("departure requires an approved result")
        if type(self.result) is OperationConflict and self.result.reason_code == "POSITION_CONFLICT":
            raise ValueError("POSITION_CONFLICT is not approved for departure")
        _picking_rejection_pointer(self.result)


@dataclass(frozen=True, slots=True)
class PickingMaterialOutcome:
    result: (
        PickingMaterialAccept
        | PickingMaterialReject
        | PickingMaterialWait
        | OperationRejected
        | OperationConflict
        | OperationUnavailable
    )

    def __post_init__(self) -> None:
        if type(self.result) not in (
            PickingMaterialAccept,
            PickingMaterialReject,
            PickingMaterialWait,
            OperationRejected,
            OperationConflict,
            OperationUnavailable,
        ):
            raise TypeError("material decide requires an approved result")
        if type(self.result) is OperationConflict and self.result.reason_code == "POSITION_CONFLICT":
            raise ValueError("POSITION_CONFLICT is not approved for material decide")
        _picking_rejection_pointer(self.result)


@dataclass(frozen=True, slots=True)
class SourceEmptyOutcome:
    result: (
        SourceEmptyRetry
        | SourceEmptyDone
        | SourceEmptyWait
        | OperationRejected
        | OperationConflict
        | OperationUnavailable
    )

    def __post_init__(self) -> None:
        if type(self.result) not in (
            SourceEmptyRetry,
            SourceEmptyDone,
            SourceEmptyWait,
            OperationRejected,
            OperationConflict,
            OperationUnavailable,
        ):
            raise TypeError("source empty requires an approved result")
        if type(self.result) is OperationConflict and self.result.reason_code == "POSITION_CONFLICT":
            raise ValueError("POSITION_CONFLICT is not approved for source empty")
        _picking_rejection_pointer(self.result)


WmsOperationOutcome = (
    AdmissionOutcome
    | TargetOutcome
    | ReplacementPlanOutcome
    | PlacementOutcome
    | NgPlacementOutcome
    | PickingTaskPrepareOutcome
    | ReturnRackArrivalReportOutcome
    | BinInboundBatchOutcome
    | BinReturnBatchOutcome
    | BinWorkPlanOutcome
    | RackDepartureOutcome
    | PickingMaterialOutcome
    | SourceEmptyOutcome
)
