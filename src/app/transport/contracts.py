"""AGV/CTU 通用搬运能力的稳定领域合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.core.uuid7 import is_uuid7


class TransportContractError(ValueError):
    """搬运请求不满足封闭合同。"""


class TransportIdempotencyConflict(TransportContractError):
    """同一调用幂等号对应了不同请求。"""


class TransportResourceConflict(TransportContractError):
    """请求涉及的资源已被其他非终态搬运任务占用。"""


class TransportTaskKind(StrEnum):
    RACK_MOVE = "RACK_MOVE"
    RACK_ROTATE = "RACK_ROTATE"
    BIN_MOVE = "BIN_MOVE"
    BIN_EXCHANGE = "BIN_EXCHANGE"


class RcsTemplateId(StrEnum):
    CTU01 = "CTU01"
    CTU02 = "CTU02"
    CTU03 = "CTU03"
    F01 = "F01"


class TransportTaskStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RECONCILING = "RECONCILING"


class TransportOutcomeStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class TransportSubmitCode(StrEnum):
    RECEIVED = "RECEIVED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_SENT = "NOT_SENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"


class TransportIngressDisposition(StrEnum):
    RECEIVED = "RECEIVED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"


class _TransportDiagnosticEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TransportIngressAttempt(_TransportDiagnosticEvent):
    request_id: str = Field(min_length=1, max_length=120)
    operation_id: str | None = Field(default=None, max_length=36)
    operation: str | None = Field(default=None, max_length=80)
    transport_task_id: str | None = Field(default=None, max_length=80)
    kind: TransportTaskKind | None = None
    outcome_revision: int | None = Field(default=None, ge=1)
    received_at: str
    disposition: TransportIngressDisposition
    status_code: int = Field(ge=100, le=599)
    error_code: str | None = Field(default=None, max_length=120)
    observed_body_bytes: int = Field(ge=0)


class TransportEvidenceUpdate(_TransportDiagnosticEvent):
    evidence_id: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=36)
    operation: str = Field(min_length=1, max_length=80)
    transport_task_id: str = Field(min_length=1, max_length=80)
    outcome_revision: int | None = Field(default=None, ge=1)
    status: Literal["APPLIED", "CONFLICT"]
    conflict_code: str | None = Field(default=None, max_length=120)
    task_status: TransportTaskStatus | None = None
    reason_code: str | None = Field(default=None, max_length=120)
    processed_at: str


MAX_SUBMIT_ATTEMPTS = 3
TRANSPORT_DEBUG_CALLER_WORKLINE_ID = "TRANSPORT_DEBUG"
TRANSPORT_POSITION_OPERATION = "transport.task.member_position_changed@v1"
TRANSPORT_RESULT_OPERATION = "transport.task.resulted@v1"


def _required(value: str, field_name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransportContractError(f"{field_name} must not be blank")
    if "\x00" in value:
        raise TransportContractError(f"{field_name} must not contain NUL")
    if max_length is not None and len(value) > max_length:
        raise TransportContractError(f"{field_name} exceeds {max_length} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise TransportContractError(f"{field_name} must be valid UTF-8") from error
    return value


def _opaque_face(value: object, field_name: str) -> None:
    if type(value) is not str or value == "":
        raise TransportContractError(f"{field_name} must be a non-empty string")
    if "\x00" in value:
        raise TransportContractError(f"{field_name} must not contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise TransportContractError(f"{field_name} must be valid UTF-8") from error


@dataclass(frozen=True, slots=True)
class TransportCaller:
    workline_id: str
    station_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.workline_id, "workline_id")
        if self.station_id is not None:
            _required(self.station_id, "station_id")


@dataclass(frozen=True, slots=True)
class TransportExecutionAuthority:
    """只在 WES 内部冻结的执行 authority，不进入北向 Transport wire。"""

    workline_id: int
    line_run_epoch_id: int
    bin_execution_id: int | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("workline_id", self.workline_id),
            ("line_run_epoch_id", self.line_run_epoch_id),
            ("bin_execution_id", self.bin_execution_id),
        ):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{field_name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class RackPosition:
    location_code: str
    kind: str = field(default="RACK_POSITION", init=False)

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code", max_length=100)


@dataclass(frozen=True, slots=True)
class RackReference:
    location_code: str
    kind: str = field(default="RACK", init=False)

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code", max_length=100)


@dataclass(frozen=True, slots=True)
class ZonePosition:
    location_code: str
    kind: str = field(default="ZONE", init=False)

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code", max_length=100)


type RackMovePosition = RackReference | ZonePosition | RackPosition


@dataclass(frozen=True, slots=True)
class RackBinSlot:
    rack_id: str
    rack_face: str
    slot_id: str
    kind: str = field(default="RACK_BIN_SLOT", init=False)

    def __post_init__(self) -> None:
        _required(self.rack_id, "rack_id", max_length=100)
        _opaque_face(self.rack_face, "rack_face")
        _required(self.slot_id, "slot_id", max_length=100)


@dataclass(frozen=True, slots=True)
class HandoffPosition:
    location_code: str
    kind: str = field(default="HANDOFF_POSITION", init=False)

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code", max_length=100)


type TransportPosition = RackPosition | RackBinSlot | HandoffPosition


@dataclass(frozen=True, slots=True)
class BinMove:
    bin_id: str
    source: RackBinSlot | HandoffPosition
    target: RackBinSlot | HandoffPosition

    def __post_init__(self) -> None:
        _required(self.bin_id, "bin_id", max_length=100)
        if type(self.source) not in {RackBinSlot, HandoffPosition} or type(self.target) not in {
            RackBinSlot,
            HandoffPosition,
        }:
            raise TransportContractError("bin source and target must be rack bin slots or handoff positions")
        if self.source == self.target:
            raise TransportContractError("bin source and target must differ")
        if not isinstance(self.source, RackBinSlot) and not isinstance(self.target, RackBinSlot):
            raise TransportContractError("bin move must include a rack bin slot")


@dataclass(frozen=True, slots=True)
class BinExchangePair:
    left_bin_id: str
    left_location: RackBinSlot
    right_bin_id: str
    right_location: RackBinSlot

    def __post_init__(self) -> None:
        _required(self.left_bin_id, "left_bin_id", max_length=100)
        _required(self.right_bin_id, "right_bin_id", max_length=100)
        if type(self.left_location) is not RackBinSlot or type(self.right_location) is not RackBinSlot:
            raise TransportContractError("exchange positions must be rack bin slots")
        if self.left_bin_id == self.right_bin_id:
            raise TransportContractError("exchange bins must differ")
        if self.left_location == self.right_location:
            raise TransportContractError("exchange positions must differ")


@dataclass(frozen=True, slots=True)
class MoveRackRequest:
    client_request_id: str
    caller: TransportCaller
    rack_id: str
    source: RackMovePosition
    target: RackMovePosition
    target_face: str
    rcs_template_id: RcsTemplateId = RcsTemplateId.F01
    kind: TransportTaskKind = field(default=TransportTaskKind.RACK_MOVE, init=False)

    def __post_init__(self) -> None:
        _validate_request_identity(self.client_request_id, self.caller)
        _required(self.rack_id, "rack_id", max_length=100)
        allowed_types = {RackReference, ZonePosition, RackPosition}
        if type(self.source) not in allowed_types or type(self.target) not in allowed_types:
            raise TransportContractError("rack source and target must be rack move positions")
        if self.source == self.target:
            raise TransportContractError("rack source and target must differ")
        _opaque_face(self.target_face, "target_face")
        if type(self.rcs_template_id) is not RcsTemplateId:
            raise TransportContractError("rcs_template_id must be a supported template")
        for position in (self.source, self.target):
            if type(position) is RackReference and position.location_code != self.rack_id:
                raise TransportContractError("RACK location_code must match rack_id")
        allowed_edges = {
            RcsTemplateId.CTU01: {
                (ZonePosition, RackPosition),
                (RackReference, RackPosition),
                (RackPosition, RackPosition),
            },
            RcsTemplateId.CTU03: {
                (RackPosition, RackReference),
                (RackPosition, ZonePosition),
                (RackPosition, RackPosition),
            },
            RcsTemplateId.F01: {(RackPosition, RackPosition)},
        }
        if (type(self.source), type(self.target)) not in allowed_edges.get(self.rcs_template_id, set()):
            raise TransportContractError("rack source, target, and rcs_template_id are not an approved edge")


@dataclass(frozen=True, slots=True)
class RotateRackRequest:
    client_request_id: str
    caller: TransportCaller
    rack_id: str
    position: RackPosition
    target_face: str
    rcs_template_id: RcsTemplateId = RcsTemplateId.CTU02
    kind: TransportTaskKind = field(default=TransportTaskKind.RACK_ROTATE, init=False)

    def __post_init__(self) -> None:
        _validate_request_identity(self.client_request_id, self.caller)
        _required(self.rack_id, "rack_id", max_length=100)
        if type(self.position) is not RackPosition:
            raise TransportContractError("rack rotation position must be a rack position")
        _opaque_face(self.target_face, "target_face")
        if self.rcs_template_id is not RcsTemplateId.CTU02:
            raise TransportContractError("rack rotation requires rcs_template_id CTU02")


@dataclass(frozen=True, slots=True)
class MoveBinsRequest:
    client_request_id: str
    caller: TransportCaller
    moves: tuple[BinMove, ...]
    kind: TransportTaskKind = field(default=TransportTaskKind.BIN_MOVE, init=False)

    def __post_init__(self) -> None:
        _validate_request_identity(self.client_request_id, self.caller)
        if not 1 <= len(self.moves) <= 4:
            raise TransportContractError("moves must contain 1..4 members")
        _reject_duplicates((move.bin_id for move in self.moves), "bin_id")
        slots = [
            position
            for move in self.moves
            for position in (move.source, move.target)
            if isinstance(position, RackBinSlot)
        ]
        _reject_duplicates((_position_key(slot) for slot in slots), "rack bin slot")
        _validate_one_face_per_rack(slots)


@dataclass(frozen=True, slots=True)
class ExchangeBinsRequest:
    client_request_id: str
    caller: TransportCaller
    exchange_pairs: tuple[BinExchangePair, ...]
    kind: TransportTaskKind = field(default=TransportTaskKind.BIN_EXCHANGE, init=False)

    def __post_init__(self) -> None:
        _validate_request_identity(self.client_request_id, self.caller)
        if not 1 <= len(self.exchange_pairs) <= 2:
            raise TransportContractError("exchange_pairs must contain 1..2 pairs")
        _reject_duplicates(
            (bin_id for pair in self.exchange_pairs for bin_id in (pair.left_bin_id, pair.right_bin_id)),
            "bin_id",
        )
        _reject_duplicates(
            (
                _position_key(position)
                for pair in self.exchange_pairs
                for position in (pair.left_location, pair.right_location)
            ),
            "rack bin slot",
        )
        slots = [position for pair in self.exchange_pairs for position in (pair.left_location, pair.right_location)]
        _validate_one_face_per_rack(slots)
        endpoint_groups = {(slot.rack_id, slot.rack_face) for slot in slots}
        if not 1 <= len(endpoint_groups) <= 2:
            raise TransportContractError("exchange pairs must use one or two endpoint groups")
        if len(endpoint_groups) == 2 and any(
            (pair.left_location.rack_id, pair.left_location.rack_face)
            == (pair.right_location.rack_id, pair.right_location.rack_face)
            for pair in self.exchange_pairs
        ):
            raise TransportContractError("exchange members must cross endpoint groups")


type TransportRequest = MoveRackRequest | RotateRackRequest | MoveBinsRequest | ExchangeBinsRequest


@dataclass(frozen=True, slots=True)
class TransportHandle:
    transport_task_id: str
    client_request_id: str

    def __post_init__(self) -> None:
        _required(self.transport_task_id, "transport_task_id")
        _required(self.client_request_id, "client_request_id")


class TransportPort(Protocol):
    async def move_rack(
        self,
        client_request_id: str,
        caller: TransportCaller,
        rack_id: str,
        source: RackMovePosition,
        target: RackMovePosition,
        target_face: str,
        rcs_template_id: RcsTemplateId = RcsTemplateId.F01,
        *,
        execution_authority: TransportExecutionAuthority | None = None,
    ) -> TransportHandle: ...

    async def rotate_rack(
        self,
        client_request_id: str,
        caller: TransportCaller,
        rack_id: str,
        position: RackPosition,
        target_face: str,
        rcs_template_id: RcsTemplateId = RcsTemplateId.CTU02,
        *,
        execution_authority: TransportExecutionAuthority | None = None,
    ) -> TransportHandle: ...

    async def move_bins(
        self,
        client_request_id: str,
        caller: TransportCaller,
        moves: tuple[BinMove, ...],
        *,
        execution_authority: TransportExecutionAuthority | None = None,
    ) -> TransportHandle: ...

    async def exchange_bins(
        self,
        client_request_id: str,
        caller: TransportCaller,
        exchange_pairs: tuple[BinExchangePair, ...],
        *,
        execution_authority: TransportExecutionAuthority | None = None,
    ) -> TransportHandle: ...


@dataclass(frozen=True, slots=True)
class TransportMemberOutcome:
    object_id: str
    final_position: TransportPosition | None = None
    position_unknown: bool = False
    failure_code: str | None = None
    arrival_face: str | None = None

    def __post_init__(self) -> None:
        _required(self.object_id, "object_id")
        if self.arrival_face is not None:
            _opaque_face(self.arrival_face, "arrival_face")
        if (self.final_position is None) == (self.position_unknown is False):
            raise TransportContractError("final_position xor position_unknown=true is required")


@dataclass(frozen=True, slots=True)
class TransportOutcome:
    transport_task_id: str
    client_request_id: str
    outcome_version: int
    caller: TransportCaller
    status: TransportOutcomeStatus
    reason_code: str | None
    members: tuple[TransportMemberOutcome, ...]

    def __post_init__(self) -> None:
        _required(self.transport_task_id, "transport_task_id")
        _required(self.client_request_id, "client_request_id")
        if self.outcome_version < 1:
            raise TransportContractError("outcome_version must be positive")


@dataclass(frozen=True, slots=True)
class TransportSubmitResult:
    code: TransportSubmitCode
    transport_task_id: str
    reason_code: str | None = None


class TransportProviderPort(Protocol):
    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
    ) -> TransportSubmitResult: ...


class TransportOutcomePublisher(Protocol):
    async def publish(self, outcome: TransportOutcome) -> None: ...


def _validate_request_identity(client_request_id: str, caller: TransportCaller) -> None:
    _required(client_request_id, "client_request_id", max_length=120)
    if not is_uuid7(client_request_id):
        raise TransportContractError("client_request_id must be a UUIDv7")
    if type(caller) is not TransportCaller:
        raise TransportContractError("caller must be a TransportCaller")


def _position_key(position: RackBinSlot) -> tuple[str, str, str]:
    return position.rack_id, position.rack_face, position.slot_id


def _validate_one_face_per_rack(slots: list[RackBinSlot]) -> None:
    faces_by_rack: dict[str, set[str]] = {}
    for slot in slots:
        faces_by_rack.setdefault(slot.rack_id, set()).add(slot.rack_face)
    if any(len(faces) != 1 for faces in faces_by_rack.values()):
        raise TransportContractError("same rack must use one face")


def _reject_duplicates(values: object, field_name: str) -> None:
    materialized = list(values)  # type: ignore[arg-type]
    if len(materialized) != len(set(materialized)):
        raise TransportContractError(f"duplicate {field_name}")


__all__ = [
    "MAX_SUBMIT_ATTEMPTS",
    "TRANSPORT_DEBUG_CALLER_WORKLINE_ID",
    "TRANSPORT_POSITION_OPERATION",
    "TRANSPORT_RESULT_OPERATION",
    "BinExchangePair",
    "BinMove",
    "ExchangeBinsRequest",
    "HandoffPosition",
    "MoveBinsRequest",
    "MoveRackRequest",
    "RackBinSlot",
    "RackMovePosition",
    "RackPosition",
    "RackReference",
    "RcsTemplateId",
    "RotateRackRequest",
    "TransportCaller",
    "TransportContractError",
    "TransportEvidenceUpdate",
    "TransportExecutionAuthority",
    "TransportHandle",
    "TransportIdempotencyConflict",
    "TransportIngressAttempt",
    "TransportIngressDisposition",
    "TransportMemberOutcome",
    "TransportOutcome",
    "TransportOutcomePublisher",
    "TransportOutcomeStatus",
    "TransportPort",
    "TransportPosition",
    "TransportProviderPort",
    "TransportRequest",
    "TransportResourceConflict",
    "TransportSubmitCode",
    "TransportSubmitResult",
    "TransportTaskKind",
    "TransportTaskStatus",
    "ZonePosition",
]
