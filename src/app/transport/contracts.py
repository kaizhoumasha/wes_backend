"""AGV/CTU 通用搬运能力的稳定领域合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

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


class RackFace(StrEnum):
    A = "A"
    B = "B"


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
    BUSY = "BUSY"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_SENT = "NOT_SENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"


MAX_SUBMIT_ATTEMPTS = 3
TRANSPORT_POSITION_OPERATION = "transport.task.member_position_changed@v1"
TRANSPORT_RESULT_OPERATION = "transport.task.resulted@v1"


def _required(value: str, field_name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransportContractError(f"{field_name} must not be blank")
    if max_length is not None and len(value) > max_length:
        raise TransportContractError(f"{field_name} exceeds {max_length} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise TransportContractError(f"{field_name} must be valid UTF-8") from error
    return value


@dataclass(frozen=True, slots=True)
class TransportCaller:
    workline_id: str
    station_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.workline_id, "workline_id")
        if self.station_id is not None:
            _required(self.station_id, "station_id")


@dataclass(frozen=True, slots=True)
class RackPosition:
    location_code: str
    kind: str = field(default="RACK_POSITION", init=False)

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code")


@dataclass(frozen=True, slots=True)
class RackBinSlot:
    rack_id: str
    slot_id: str
    kind: str = field(default="RACK_BIN_SLOT", init=False)

    def __post_init__(self) -> None:
        _required(self.rack_id, "rack_id", max_length=100)
        _required(self.slot_id, "slot_id")


@dataclass(frozen=True, slots=True)
class HandoffPosition:
    location_code: str
    kind: str = field(default="HANDOFF_POSITION", init=False)

    def __post_init__(self) -> None:
        _required(self.location_code, "location_code")


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
    source: RackPosition
    target: RackPosition
    kind: TransportTaskKind = field(default=TransportTaskKind.RACK_MOVE, init=False)

    def __post_init__(self) -> None:
        _validate_request_identity(self.client_request_id, self.caller)
        _required(self.rack_id, "rack_id", max_length=100)
        if type(self.source) is not RackPosition or type(self.target) is not RackPosition:
            raise TransportContractError("rack source and target must be rack positions")
        if self.source == self.target:
            raise TransportContractError("rack source and target must differ")


@dataclass(frozen=True, slots=True)
class RotateRackRequest:
    client_request_id: str
    caller: TransportCaller
    rack_id: str
    position: RackPosition
    target_face: RackFace
    kind: TransportTaskKind = field(default=TransportTaskKind.RACK_ROTATE, init=False)

    def __post_init__(self) -> None:
        _validate_request_identity(self.client_request_id, self.caller)
        _required(self.rack_id, "rack_id", max_length=100)
        if type(self.position) is not RackPosition:
            raise TransportContractError("rack rotation position must be a rack position")
        if not isinstance(self.target_face, RackFace):
            raise TransportContractError("target_face must be A or B")


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
        source: RackPosition,
        target: RackPosition,
    ) -> TransportHandle: ...

    async def rotate_rack(
        self,
        client_request_id: str,
        caller: TransportCaller,
        rack_id: str,
        position: RackPosition,
        target_face: RackFace,
    ) -> TransportHandle: ...

    async def move_bins(
        self,
        client_request_id: str,
        caller: TransportCaller,
        moves: tuple[BinMove, ...],
    ) -> TransportHandle: ...

    async def exchange_bins(
        self,
        client_request_id: str,
        caller: TransportCaller,
        exchange_pairs: tuple[BinExchangePair, ...],
    ) -> TransportHandle: ...


@dataclass(frozen=True, slots=True)
class TransportMemberOutcome:
    object_id: str
    final_position: TransportPosition | None = None
    position_unknown: bool = False
    failure_code: str | None = None
    arrival_face: RackFace | None = None

    def __post_init__(self) -> None:
        _required(self.object_id, "object_id")
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
    retry_after_ms: int | None = None


class TransportProviderPort(Protocol):
    async def submit(
        self,
        *,
        operation_id: str,
        timestamp: int,
        payload: dict[str, object],
        payload_digest: str,
    ) -> TransportSubmitResult: ...


class TransportOutcomePublisher(Protocol):
    async def publish(self, outcome: TransportOutcome) -> None: ...


def _validate_request_identity(client_request_id: str, caller: TransportCaller) -> None:
    _required(client_request_id, "client_request_id", max_length=120)
    if not is_uuid7(client_request_id):
        raise TransportContractError("client_request_id must be a UUIDv7")
    if type(caller) is not TransportCaller:
        raise TransportContractError("caller must be a TransportCaller")


def _position_key(position: RackBinSlot) -> tuple[str, str]:
    return position.rack_id, position.slot_id


def _reject_duplicates(values: object, field_name: str) -> None:
    materialized = list(values)  # type: ignore[arg-type]
    if len(materialized) != len(set(materialized)):
        raise TransportContractError(f"duplicate {field_name}")


__all__ = [
    "MAX_SUBMIT_ATTEMPTS",
    "TRANSPORT_POSITION_OPERATION",
    "TRANSPORT_RESULT_OPERATION",
    "BinExchangePair",
    "BinMove",
    "ExchangeBinsRequest",
    "HandoffPosition",
    "MoveBinsRequest",
    "MoveRackRequest",
    "RackBinSlot",
    "RackFace",
    "RackPosition",
    "RotateRackRequest",
    "TransportCaller",
    "TransportContractError",
    "TransportHandle",
    "TransportIdempotencyConflict",
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
]
