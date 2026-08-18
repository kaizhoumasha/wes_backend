"""粗分业务 handler 消费的不可变类型化事实。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from wes_plugin_sdk import (
    DevicePosition,
    DeviceResultReadyFact,
    EpochConfigurationSnapshot,
    EvidenceReadyFact,
    ExecutionSnapshot,
    RackFace,
    RecoveryDecision,
    TransportLeg,
    TransportRackPosition,
    TransportResultReadyFact,
    WmsResultReadyFact,
)
from wes_plugin_sdk import (
    RecoveryDecidedFact as BaseRecoveryDecidedFact,
)


class ShapeResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class AdmissionResult(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    WAIT = "WAIT"
    RECONCILING = "RECONCILING"


class DeviceStep(StrEnum):
    MEASUREMENT_TO_INLET = "MEASUREMENT_TO_INLET"
    TRANSFER_TO_OUTLET = "TRANSFER_TO_OUTLET"
    PLACEMENT_TO_CELL = "PLACEMENT_TO_CELL"
    MEASUREMENT_TO_NG = "MEASUREMENT_TO_NG"
    PLACEMENT_TO_NG = "PLACEMENT_TO_NG"


class DeviceOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"


class TargetResult(StrEnum):
    ASSIGNED = "ASSIGNED"
    NO_AVAILABLE_CELL = "NO_AVAILABLE_CELL"
    REJECT = "REJECT"
    WAIT = "WAIT"
    RECONCILING = "RECONCILING"


class CompletionKind(StrEnum):
    PLACEMENT = "PLACEMENT"
    NG_PLACEMENT = "NG_PLACEMENT"


class CompletionResult(StrEnum):
    RECORDED = "RECORDED"
    DUPLICATE = "DUPLICATE"
    RECONCILING = "RECONCILING"


class ReplacementResult(StrEnum):
    READY = "READY"
    WAIT = "WAIT"
    RECONCILING = "RECONCILING"


class PlacementCommandStatus(StrEnum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class PlacementConfirmationStatus(StrEnum):
    ABSENT = "ABSENT"
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    COMPLETED = "COMPLETED"
    RECONCILING = "RECONCILING"


class PlacementResponseResult(StrEnum):
    RECORDED = "RECORDED"
    DUPLICATE = "DUPLICATE"


class TransportOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RoughSorterRuntimeSnapshot:
    execution: ExecutionSnapshot
    epoch: EpochConfigurationSnapshot

    def __post_init__(self) -> None:
        if type(self.execution) is not ExecutionSnapshot or type(self.epoch) is not EpochConfigurationSnapshot:
            raise TypeError("runtime snapshot requires exact SDK snapshot values")
        if self.execution.line_run_epoch_id != self.epoch.line_run_epoch_id:
            raise ValueError("execution and Epoch snapshots do not match")


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _operation_id(value: str, field_name: str) -> None:
    _required(value, field_name)


def _millimeters(value: str, field_name: str) -> None:
    _required(value, field_name)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be decimal millimeters") from exc
    if not parsed.is_finite() or parsed < 0 or value.startswith(("+", "-")) or "e" in value.lower():
        raise ValueError(f"{field_name} must be canonical non-negative decimal millimeters")


def _position(value: DevicePosition, field_name: str, *, location_type: str, material_trace_id: str) -> None:
    if type(value) is not DevicePosition:
        raise TypeError(f"{field_name} must be a DevicePosition")
    _position_identity(value, field_name)
    if value.location_type != location_type:
        raise ValueError(f"{field_name} must use {location_type}")
    if value.material_trace_id != material_trace_id:
        raise ValueError(f"{field_name} must reference material_trace_id")


def _position_identity(value: DevicePosition, field_name: str) -> None:
    rack_identity = (value.rack_id, value.rack_slot_code, value.bin_id, value.bin_cell_id)
    if value.location_type == "RACK_CELL" and not all(rack_identity):
        raise ValueError("RACK_CELL requires complete rack/bin identity")
    if value.location_type != "RACK_CELL" and any(rack_identity):
        raise ValueError(f"{field_name} non-RACK_CELL position must not include rack/bin identity")


def _reject_present_fields(branch: str, fields: tuple[tuple[str, object | None], ...]) -> None:
    present = tuple(field_name for field_name, value in fields if value is not None)
    if present:
        raise ValueError(f"{branch} must not include {', '.join(present)} from another result branch")


def _runtime_snapshot(
    snapshot: RoughSorterRuntimeSnapshot,
    *,
    material_execution_id: str,
    material_trace_id: str,
) -> None:
    if type(snapshot) is not RoughSorterRuntimeSnapshot:
        raise TypeError("runtime_snapshot must be a RoughSorterRuntimeSnapshot")
    if (
        snapshot.execution.material_execution_id != material_execution_id
        or snapshot.execution.material_trace_id != material_trace_id
    ):
        raise ValueError("runtime snapshot execution identity does not match Fact")


@dataclass(frozen=True, slots=True)
class MaterialEvidenceReadyFact(EvidenceReadyFact):
    runtime_snapshot: RoughSorterRuntimeSnapshot
    material_trace_id: str
    line_run_epoch_id: str
    workline_code: str
    lot_code: str
    date_code: str
    qty: str
    product_no: str
    mfr_pn: str
    po_number: str
    diameter_mm: str
    thickness_mm: str
    shape_result: ShapeResult
    source_position: DevicePosition
    request_operation_id: str

    def __post_init__(self) -> None:
        EvidenceReadyFact.__post_init__(self)
        _runtime_snapshot(
            self.runtime_snapshot,
            material_execution_id=self.material_execution_id,
            material_trace_id=self.material_trace_id,
        )
        for field_name in (
            "material_trace_id",
            "line_run_epoch_id",
            "workline_code",
            "lot_code",
            "date_code",
            "qty",
            "product_no",
            "mfr_pn",
            "po_number",
        ):
            _required(getattr(self, field_name), field_name)
        _millimeters(self.diameter_mm, "diameter_mm")
        _millimeters(self.thickness_mm, "thickness_mm")
        if type(self.shape_result) is not ShapeResult:
            raise ValueError("shape_result must be a ShapeResult")
        _position(
            self.source_position,
            "source_position",
            location_type="MEASUREMENT_POSITION",
            material_trace_id=self.material_trace_id,
        )
        _operation_id(self.request_operation_id, "request_operation_id")


@dataclass(frozen=True, slots=True)
class AdmissionDecidedFact(WmsResultReadyFact):
    runtime_snapshot: RoughSorterRuntimeSnapshot
    material_trace_id: str
    result: AdmissionResult
    source_position: DevicePosition
    device_ready: bool
    pkg_id: str | None = None
    inbound_admission_id: str | None = None
    reason_code: str | None = None
    next_position: DevicePosition | None = None

    def __post_init__(self) -> None:
        WmsResultReadyFact.__post_init__(self)
        _runtime_snapshot(
            self.runtime_snapshot,
            material_execution_id=self.material_execution_id,
            material_trace_id=self.material_trace_id,
        )
        _required(self.material_trace_id, "material_trace_id")
        if type(self.result) is not AdmissionResult:
            raise ValueError("result must be an AdmissionResult")
        _position(
            self.source_position,
            "source_position",
            location_type="MEASUREMENT_POSITION",
            material_trace_id=self.material_trace_id,
        )
        if type(self.device_ready) is not bool:
            raise TypeError("device_ready must be a boolean")
        if self.result is AdmissionResult.ACCEPT:
            self._require_accept()
        elif self.result is AdmissionResult.REJECT:
            self._require_reason_and_destination("NG_POSITION")
        else:
            self._require_reason_only()

    def _require_accept(self) -> None:
        _required(self.pkg_id or "", "pkg_id")
        _required(self.inbound_admission_id or "", "inbound_admission_id")
        if self.next_position is None:
            raise ValueError("ACCEPT requires next_position")
        _position(
            self.next_position,
            "next_position",
            location_type="PIPELINE_INLET",
            material_trace_id=self.material_trace_id,
        )
        if self.reason_code is not None:
            raise ValueError("ACCEPT must not include reason_code")

    def _require_reason_and_destination(self, location_type: str) -> None:
        _required(self.reason_code or "", "reason_code")
        if self.next_position is None:
            raise ValueError(f"{self.result.value} requires next_position")
        _position(
            self.next_position,
            "next_position",
            location_type=location_type,
            material_trace_id=self.material_trace_id,
        )
        if self.pkg_id is not None or self.inbound_admission_id is not None:
            raise ValueError(f"{self.result.value} must not include admission identity")

    def _require_reason_only(self) -> None:
        _required(self.reason_code or "", "reason_code")
        if self.next_position is not None or self.pkg_id is not None or self.inbound_admission_id is not None:
            raise ValueError(f"{self.result.value} contains fields from another result branch")


_DEVICE_STEP_CONTRACT = {
    DeviceStep.MEASUREMENT_TO_INLET: ("MEASUREMENT_DEVICE", "MEASUREMENT_POSITION", "PIPELINE_INLET"),
    DeviceStep.TRANSFER_TO_OUTLET: ("TRANSFER_DEVICE", "PIPELINE_INLET", "PIPELINE_OUTLET"),
    DeviceStep.PLACEMENT_TO_CELL: ("PLACEMENT_DEVICE", "PIPELINE_OUTLET", "RACK_CELL"),
    DeviceStep.MEASUREMENT_TO_NG: ("MEASUREMENT_DEVICE", "MEASUREMENT_POSITION", "NG_POSITION"),
    DeviceStep.PLACEMENT_TO_NG: ("PLACEMENT_DEVICE", "PIPELINE_OUTLET", "NG_POSITION"),
}


@dataclass(frozen=True, slots=True)
class DevicePositionConfirmedFact(DeviceResultReadyFact):
    runtime_snapshot: RoughSorterRuntimeSnapshot
    step: DeviceStep
    device_role: str
    outcome: DeviceOutcome
    source_position: DevicePosition
    target_position: DevicePosition
    actual_position: DevicePosition | None = None
    next_position: DevicePosition | None = None
    next_device_ready: bool | None = None
    request_operation_id: str | None = None
    pkg_id: str | None = None
    inbound_admission_id: str | None = None
    current_rack_id: str | None = None
    target_assignment_id: str | None = None
    placement_sequence: int | None = None
    placed_at_ms: int | None = None
    ng_evidence_id: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        DeviceResultReadyFact.__post_init__(self)
        _runtime_snapshot(
            self.runtime_snapshot,
            material_execution_id=self.material_execution_id,
            material_trace_id=self.material_trace_id,
        )
        if type(self.step) is not DeviceStep or type(self.outcome) is not DeviceOutcome:
            raise ValueError("step and outcome must use rough sorter enums")
        expected_role, source_type, target_type = _DEVICE_STEP_CONTRACT[self.step]
        if self.device_role != expected_role:
            raise ValueError("device_role does not match device step")
        _position(
            self.source_position,
            "source_position",
            location_type=source_type,
            material_trace_id=self.material_trace_id,
        )
        _position(
            self.target_position,
            "target_position",
            location_type=target_type,
            material_trace_id=self.material_trace_id,
        )
        if self.outcome is not DeviceOutcome.SUCCESS:
            _required(self.reason_code or "", "reason_code")
            _reject_present_fields(
                "non-success device result",
                (
                    ("actual_position", self.actual_position),
                    ("next_position", self.next_position),
                    ("next_device_ready", self.next_device_ready),
                    ("request_operation_id", self.request_operation_id),
                    ("pkg_id", self.pkg_id),
                    ("inbound_admission_id", self.inbound_admission_id),
                    ("current_rack_id", self.current_rack_id),
                    ("target_assignment_id", self.target_assignment_id),
                    ("placement_sequence", self.placement_sequence),
                    ("placed_at_ms", self.placed_at_ms),
                    ("ng_evidence_id", self.ng_evidence_id),
                ),
            )
            return
        if self.actual_position != self.target_position:
            raise ValueError("successful device result must confirm the command target")
        if self.step is DeviceStep.MEASUREMENT_TO_INLET:
            self._require_transfer_next()
        elif self.step is DeviceStep.TRANSFER_TO_OUTLET:
            self._require_target_request()
        elif self.step is DeviceStep.PLACEMENT_TO_CELL:
            self._require_placement_report()
        else:
            self._require_ng_report()

    def _require_transfer_next(self) -> None:
        if self.next_position is None:
            raise ValueError("MEASUREMENT_TO_INLET requires next_position")
        _position(
            self.next_position,
            "next_position",
            location_type="PIPELINE_OUTLET",
            material_trace_id=self.material_trace_id,
        )
        if type(self.next_device_ready) is not bool:
            raise TypeError("MEASUREMENT_TO_INLET requires next_device_ready")
        _reject_present_fields(
            self.step.value,
            (
                ("request_operation_id", self.request_operation_id),
                ("pkg_id", self.pkg_id),
                ("inbound_admission_id", self.inbound_admission_id),
                ("current_rack_id", self.current_rack_id),
                ("target_assignment_id", self.target_assignment_id),
                ("placement_sequence", self.placement_sequence),
                ("placed_at_ms", self.placed_at_ms),
                ("ng_evidence_id", self.ng_evidence_id),
                ("reason_code", self.reason_code),
            ),
        )

    def _require_target_request(self) -> None:
        _operation_id(self.request_operation_id or "", "request_operation_id")
        _required(self.pkg_id or "", "pkg_id")
        _required(self.inbound_admission_id or "", "inbound_admission_id")
        _required(self.current_rack_id or "", "current_rack_id")
        _reject_present_fields(
            self.step.value,
            (
                ("next_position", self.next_position),
                ("next_device_ready", self.next_device_ready),
                ("target_assignment_id", self.target_assignment_id),
                ("placement_sequence", self.placement_sequence),
                ("placed_at_ms", self.placed_at_ms),
                ("ng_evidence_id", self.ng_evidence_id),
                ("reason_code", self.reason_code),
            ),
        )

    def _require_placement_report(self) -> None:
        _operation_id(self.request_operation_id or "", "request_operation_id")
        _required(self.pkg_id or "", "pkg_id")
        _required(self.inbound_admission_id or "", "inbound_admission_id")
        _required(self.target_assignment_id or "", "target_assignment_id")
        if type(self.placement_sequence) is not int or self.placement_sequence <= 0:
            raise ValueError("placement_sequence must be a positive integer")
        if type(self.placed_at_ms) is not int or self.placed_at_ms <= 0:
            raise ValueError("placed_at_ms must be a positive integer")
        _reject_present_fields(
            self.step.value,
            (
                ("next_position", self.next_position),
                ("next_device_ready", self.next_device_ready),
                ("current_rack_id", self.current_rack_id),
                ("ng_evidence_id", self.ng_evidence_id),
                ("reason_code", self.reason_code),
            ),
        )

    def _require_ng_report(self) -> None:
        _operation_id(self.request_operation_id or "", "request_operation_id")
        _required(self.ng_evidence_id or "", "ng_evidence_id")
        _required(self.reason_code or "", "reason_code")
        _reject_present_fields(
            self.step.value,
            (
                ("next_position", self.next_position),
                ("next_device_ready", self.next_device_ready),
                ("pkg_id", self.pkg_id),
                ("inbound_admission_id", self.inbound_admission_id),
                ("current_rack_id", self.current_rack_id),
                ("target_assignment_id", self.target_assignment_id),
                ("placement_sequence", self.placement_sequence),
                ("placed_at_ms", self.placed_at_ms),
            ),
        )


@dataclass(frozen=True, slots=True)
class TargetDecidedFact(WmsResultReadyFact):
    runtime_snapshot: RoughSorterRuntimeSnapshot
    material_trace_id: str
    result: TargetResult
    source_position: DevicePosition
    current_rack_id: str
    current_rack_fenced: bool
    device_ready: bool
    target_position: DevicePosition | None = None
    target_assignment_id: str | None = None
    placement_sequence: int | None = None
    expected_height_mm: str | None = None
    reason_code: str | None = None
    request_operation_id: str | None = None

    def __post_init__(self) -> None:
        WmsResultReadyFact.__post_init__(self)
        _runtime_snapshot(
            self.runtime_snapshot,
            material_execution_id=self.material_execution_id,
            material_trace_id=self.material_trace_id,
        )
        _required(self.material_trace_id, "material_trace_id")
        _required(self.current_rack_id, "current_rack_id")
        if type(self.result) is not TargetResult:
            raise ValueError("result must be a TargetResult")
        if type(self.current_rack_fenced) is not bool:
            raise TypeError("current_rack_fenced must be a boolean")
        if type(self.device_ready) is not bool:
            raise TypeError("device_ready must be a boolean")
        _position(
            self.source_position,
            "source_position",
            location_type="PIPELINE_OUTLET",
            material_trace_id=self.material_trace_id,
        )
        if self.result is TargetResult.ASSIGNED:
            self._require_assigned()
        elif self.result is TargetResult.NO_AVAILABLE_CELL:
            _required(self.reason_code or "", "reason_code")
            _operation_id(self.request_operation_id or "", "request_operation_id")
            self._reject_target_fields(
                ("target_position", "target_assignment_id", "placement_sequence", "expected_height_mm")
            )
        elif self.result is TargetResult.REJECT:
            _required(self.reason_code or "", "reason_code")
            if self.target_position is None:
                raise ValueError("REJECT requires target_position")
            _position(
                self.target_position,
                "target_position",
                location_type="NG_POSITION",
                material_trace_id=self.material_trace_id,
            )
            self._reject_target_fields(
                ("target_assignment_id", "placement_sequence", "expected_height_mm", "request_operation_id")
            )
        else:
            _required(self.reason_code or "", "reason_code")
            self._reject_target_fields(
                (
                    "target_position",
                    "target_assignment_id",
                    "placement_sequence",
                    "expected_height_mm",
                    "request_operation_id",
                )
            )

    def _require_assigned(self) -> None:
        _required(self.target_assignment_id or "", "target_assignment_id")
        if self.target_position is None:
            raise ValueError("ASSIGNED requires target_position")
        _position(
            self.target_position,
            "target_position",
            location_type="RACK_CELL",
            material_trace_id=self.material_trace_id,
        )
        if type(self.placement_sequence) is not int or self.placement_sequence <= 0:
            raise ValueError("placement_sequence must be a positive integer")
        _millimeters(self.expected_height_mm or "", "expected_height_mm")
        self._reject_target_fields(("reason_code", "request_operation_id"))

    def _reject_target_fields(self, field_names: tuple[str, ...]) -> None:
        _reject_present_fields(
            self.result.value,
            tuple((field_name, getattr(self, field_name)) for field_name in field_names),
        )


def _required_refs(values: tuple[str, ...], field_name: str) -> None:
    if type(values) is not tuple or not values or len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be a non-empty unique tuple")
    for value in values:
        _required(value, field_name)


@dataclass(frozen=True, slots=True)
class PlacementCompletedFact(WmsResultReadyFact):
    runtime_snapshot: RoughSorterRuntimeSnapshot
    material_trace_id: str
    kind: CompletionKind
    result: CompletionResult
    affected_resource_ids: tuple[str, ...]
    reason_code: str | None = None

    def __post_init__(self) -> None:
        WmsResultReadyFact.__post_init__(self)
        _runtime_snapshot(
            self.runtime_snapshot,
            material_execution_id=self.material_execution_id,
            material_trace_id=self.material_trace_id,
        )
        _required(self.material_trace_id, "material_trace_id")
        if type(self.kind) is not CompletionKind or type(self.result) is not CompletionResult:
            raise ValueError("kind and result must use placement completion enums")
        _required_refs(self.affected_resource_ids, "affected_resource_ids")
        if self.result is CompletionResult.RECONCILING:
            _required(self.reason_code or "", "reason_code")
        elif self.reason_code is not None:
            raise ValueError("recorded completion must not include reason_code")


@dataclass(frozen=True, slots=True)
class RackMoveLegPlan:
    rack_id: str
    source: TransportRackPosition
    target: TransportRackPosition
    target_face: RackFace

    def __post_init__(self) -> None:
        _required(self.rack_id, "rack_id")
        if type(self.source) is not TransportRackPosition or type(self.target) is not TransportRackPosition:
            raise TypeError("source and target must be TransportRackPosition values")
        if self.source == self.target:
            raise ValueError("source and target must differ")
        if type(self.target_face) is not RackFace:
            raise TypeError("target_face must be a RackFace")


@dataclass(frozen=True, slots=True)
class PlacementReleaseEvidence:
    command_code: str
    command_status: PlacementCommandStatus
    command_result_evidence_id: int | None
    confirmation_operation: str | None
    confirmation_operation_id: str | None
    confirmation_status: PlacementConfirmationStatus
    response_result: PlacementResponseResult | None
    response_evidence_id: int | None

    def __post_init__(self) -> None:
        _required(self.command_code, "command_code")
        if type(self.command_status) is not PlacementCommandStatus:
            raise TypeError("command_status must be a PlacementCommandStatus")
        if type(self.confirmation_status) is not PlacementConfirmationStatus:
            raise TypeError("confirmation_status must be a PlacementConfirmationStatus")
        if self.confirmation_status is PlacementConfirmationStatus.ABSENT:
            if any(
                value is not None
                for value in (
                    self.confirmation_operation,
                    self.confirmation_operation_id,
                    self.response_result,
                    self.response_evidence_id,
                )
            ):
                raise ValueError("ABSENT confirmation must not include confirmation or response identity")
        else:
            _required(self.confirmation_operation_id or "", "confirmation_operation_id")
            if self.confirmation_operation != "inbound.material.placement_report@v1":
                raise ValueError("release evidence must use placement_report operation")
        for value, field_name in (
            (self.command_result_evidence_id, "command_result_evidence_id"),
            (self.response_evidence_id, "response_evidence_id"),
        ):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{field_name} must be a positive integer or null")
        if self.response_result is not None and type(self.response_result) is not PlacementResponseResult:
            raise TypeError("response_result must be a PlacementResponseResult or null")


def rack_release_snapshot_ref(
    current_rack_id: str,
    placements: tuple[PlacementReleaseEvidence, ...],
) -> str:
    values = [
        [
            item.command_code,
            item.command_status.value,
            item.command_result_evidence_id,
            item.confirmation_operation,
            item.confirmation_operation_id,
            item.confirmation_status.value,
            item.response_result.value if item.response_result is not None else None,
            item.response_evidence_id,
        ]
        for item in sorted(placements, key=lambda item: item.command_code)
    ]
    encoded = json.dumps([current_rack_id, values], ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RackReleaseSnapshot:
    current_rack_id: str
    placements: tuple[PlacementReleaseEvidence, ...]
    snapshot_ref: str

    def __post_init__(self) -> None:
        _required(self.current_rack_id, "current_rack_id")
        if type(self.placements) is not tuple or any(
            type(item) is not PlacementReleaseEvidence for item in self.placements
        ):
            raise TypeError("placements must be a tuple of PlacementReleaseEvidence")
        command_codes = tuple(item.command_code for item in self.placements)
        if len(command_codes) != len(set(command_codes)):
            raise ValueError("placements must not contain duplicate command identities")
        if self.snapshot_ref != rack_release_snapshot_ref(self.current_rack_id, self.placements):
            raise ValueError("snapshot_ref does not match release evidence")


@dataclass(frozen=True, slots=True)
class ReplacementPlanDecidedFact(WmsResultReadyFact):
    runtime_snapshot: RoughSorterRuntimeSnapshot
    material_trace_id: str
    result: ReplacementResult
    current_rack_id: str
    release_snapshot: RackReleaseSnapshot | None = None
    rack_replacement_id: str | None = None
    old_loaded_rack: RackMoveLegPlan | None = None
    new_empty_rack: RackMoveLegPlan | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        WmsResultReadyFact.__post_init__(self)
        _runtime_snapshot(
            self.runtime_snapshot,
            material_execution_id=self.material_execution_id,
            material_trace_id=self.material_trace_id,
        )
        _required(self.material_trace_id, "material_trace_id")
        _required(self.current_rack_id, "current_rack_id")
        if type(self.result) is not ReplacementResult:
            raise ValueError("result must be a ReplacementResult")
        if self.result is ReplacementResult.READY:
            self._require_ready()
        else:
            _required(self.reason_code or "", "reason_code")
            if any((self.rack_replacement_id, self.old_loaded_rack, self.new_empty_rack, self.release_snapshot)):
                raise ValueError(f"{self.result.value} must not include a rack move plan")

    def _require_ready(self) -> None:
        _required(self.rack_replacement_id or "", "rack_replacement_id")
        if type(self.old_loaded_rack) is not RackMoveLegPlan or type(self.new_empty_rack) is not RackMoveLegPlan:
            raise TypeError("READY requires typed old and new rack plans")
        if self.old_loaded_rack.rack_id != self.current_rack_id:
            raise ValueError("old_loaded_rack must match current_rack_id")
        if self.old_loaded_rack.rack_id == self.new_empty_rack.rack_id:
            raise ValueError("old and new rack identities must differ")
        if type(self.release_snapshot) is not RackReleaseSnapshot:
            raise TypeError("READY requires a typed RackReleaseSnapshot")
        if self.release_snapshot.current_rack_id != self.current_rack_id:
            raise ValueError("release snapshot rack must match current_rack_id")
        if self.reason_code is not None:
            raise ValueError("READY must not include reason_code")


@dataclass(frozen=True, slots=True)
class TransportOutcomePublishedFact(TransportResultReadyFact):
    runtime_snapshot: RoughSorterRuntimeSnapshot
    material_trace_id: str
    rack_replacement_id: str
    leg: TransportLeg
    outcome: TransportOutcome
    rack_id: str
    expected_target: TransportRackPosition
    expected_face: RackFace
    final_position: TransportRackPosition | None = None
    arrival_face: RackFace | None = None
    actual_rack_id: str | None = None
    source_position: DevicePosition | None = None
    request_operation_id: str | None = None
    pkg_id: str | None = None
    inbound_admission_id: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        TransportResultReadyFact.__post_init__(self)
        _runtime_snapshot(
            self.runtime_snapshot,
            material_execution_id=self.material_execution_id,
            material_trace_id=self.material_trace_id,
        )
        for field_name in ("material_trace_id", "rack_replacement_id", "rack_id"):
            _required(getattr(self, field_name), field_name)
        if type(self.leg) is not TransportLeg or type(self.outcome) is not TransportOutcome:
            raise ValueError("leg and outcome must use transport enums")
        if self.leg is not TransportLeg.NEW_IN:
            raise ValueError("material transport outcome only accepts NEW_IN")
        if type(self.expected_target) is not TransportRackPosition or type(self.expected_face) is not RackFace:
            raise TypeError("expected target and face must use SDK transport values")
        if self.outcome is not TransportOutcome.SUCCEEDED:
            _required(self.reason_code or "", "reason_code")
            _reject_present_fields(
                "non-success transport result",
                (
                    ("final_position", self.final_position),
                    ("arrival_face", self.arrival_face),
                    ("actual_rack_id", self.actual_rack_id),
                    ("source_position", self.source_position),
                    ("request_operation_id", self.request_operation_id),
                    ("pkg_id", self.pkg_id),
                    ("inbound_admission_id", self.inbound_admission_id),
                ),
            )
            return
        _required(self.actual_rack_id or "", "actual_rack_id")
        if type(self.final_position) is not TransportRackPosition or type(self.arrival_face) is not RackFace:
            raise TypeError("successful transport outcome requires typed final position and arrival face")
        if self.reason_code is not None:
            raise ValueError("successful transport outcome must not include reason_code")
        self._require_new_rack_target_request()

    def _require_new_rack_target_request(self) -> None:
        if self.source_position is None:
            raise ValueError("NEW_IN success requires source_position")
        _position(
            self.source_position,
            "source_position",
            location_type="PIPELINE_OUTLET",
            material_trace_id=self.material_trace_id,
        )
        _operation_id(self.request_operation_id or "", "request_operation_id")
        _required(self.pkg_id or "", "pkg_id")
        _required(self.inbound_admission_id or "", "inbound_admission_id")


_RECOVERY_WMS_OPERATIONS = {
    "inbound.material.admission_decide@v1",
    "inbound.material.target_decide@v1",
    "inbound.material.placement_report@v1",
    "inbound.material.ng_placement_report@v1",
    "inbound.source_rack.replacement_plan_decide@v1",
}


@dataclass(frozen=True, slots=True)
class RecoveryWmsContinuation:
    operation: str
    operation_id: str
    evidence_refs: tuple[str, ...]
    snapshot_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.operation not in _RECOVERY_WMS_OPERATIONS:
            raise ValueError("unsupported recovery WMS operation")
        _operation_id(self.operation_id, "operation_id")
        _required_refs(self.evidence_refs, "evidence_refs")
        _required_refs(self.snapshot_refs, "snapshot_refs")


@dataclass(frozen=True, slots=True)
class RecoveryDeviceContinuation:
    device_role: str
    task_type: str
    source: DevicePosition
    target: DevicePosition
    device_ready: bool

    def __post_init__(self) -> None:
        _required(self.device_role, "device_role")
        _required(self.task_type, "task_type")
        if type(self.source) is not DevicePosition or type(self.target) is not DevicePosition:
            raise TypeError("source and target must be DevicePosition values")
        _position_identity(self.source, "source")
        _position_identity(self.target, "target")
        if self.source.material_trace_id != self.target.material_trace_id or self.source == self.target:
            raise ValueError("recovery device positions must keep one trace and differ")
        if type(self.device_ready) is not bool:
            raise TypeError("device_ready must be a boolean")


@dataclass(frozen=True, slots=True)
class RecoveryDeferContinuation:
    reason_code: str

    def __post_init__(self) -> None:
        _required(self.reason_code, "reason_code")


RecoveryContinuation = RecoveryWmsContinuation | RecoveryDeviceContinuation | RecoveryDeferContinuation


@dataclass(frozen=True, slots=True)
class RecoveryDecidedFact(BaseRecoveryDecidedFact):
    runtime_snapshot: RoughSorterRuntimeSnapshot
    material_trace_id: str
    reconciling_evidence_id: str
    continuation: RecoveryContinuation | None

    def __post_init__(self) -> None:
        BaseRecoveryDecidedFact.__post_init__(self)
        _runtime_snapshot(
            self.runtime_snapshot,
            material_execution_id=self.material_execution_id,
            material_trace_id=self.material_trace_id,
        )
        _required(self.material_trace_id, "material_trace_id")
        _required(self.reconciling_evidence_id, "reconciling_evidence_id")
        if self.reconciling_evidence_id == self.evidence_id:
            raise ValueError("reconciling_evidence_id must reference prior causal evidence")
        if self.authoritative_position is not None:
            _position_identity(self.authoritative_position, "authoritative_position")
            if self.authoritative_position.material_trace_id != self.material_trace_id:
                raise ValueError("authoritative_position must reference material_trace_id")
        if self.decision is RecoveryDecision.CONTINUE:
            if type(self.continuation) not in {
                RecoveryWmsContinuation,
                RecoveryDeviceContinuation,
                RecoveryDeferContinuation,
            }:
                raise TypeError("CONTINUE requires a typed continuation")
        elif self.continuation is not None:
            raise ValueError("ABORT must not include continuation")


__all__ = [
    "AdmissionDecidedFact",
    "AdmissionResult",
    "CompletionKind",
    "CompletionResult",
    "DeviceOutcome",
    "DevicePositionConfirmedFact",
    "DeviceStep",
    "MaterialEvidenceReadyFact",
    "PlacementCommandStatus",
    "PlacementCompletedFact",
    "PlacementConfirmationStatus",
    "PlacementReleaseEvidence",
    "PlacementResponseResult",
    "RackMoveLegPlan",
    "RackReleaseSnapshot",
    "RecoveryContinuation",
    "RecoveryDecidedFact",
    "RecoveryDecision",
    "RecoveryDeferContinuation",
    "RecoveryDeviceContinuation",
    "RecoveryWmsContinuation",
    "ReplacementPlanDecidedFact",
    "ReplacementResult",
    "RoughSorterRuntimeSnapshot",
    "ShapeResult",
    "TargetDecidedFact",
    "TargetResult",
    "TransportOutcome",
    "TransportOutcomePublishedFact",
    "rack_release_snapshot_ref",
]
