from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from src.app.transport.contracts import (
    BinExchangePair,
    BinMove,
    ExchangeBinsRequest,
    HandoffPosition,
    MoveBinsRequest,
    MoveRackRequest,
    RackBinSlot,
    RackFace,
    RackPosition,
    RotateRackRequest,
    TransportCaller,
    TransportContractError,
    TransportHandle,
    TransportOutcome,
    TransportOutcomeStatus,
    TransportTaskKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _caller() -> TransportCaller:
    return TransportCaller(workline_id="SORTER", station_id="STATION_A", correlation_id="run-1")


@dataclass(frozen=True, slots=True)
class VendorRackPosition(RackPosition):
    vendor_code: str = "VENDOR"


@dataclass(frozen=True, slots=True)
class VendorRackBinSlot(RackBinSlot):
    vendor_code: str = "VENDOR"


@dataclass(frozen=True, slots=True)
class VendorHandoffPosition(HandoffPosition):
    vendor_code: str = "VENDOR"


def test_four_request_contracts_accept_minimal_valid_data() -> None:
    source = RackPosition("ROUGH_SORTER")
    target = RackPosition("STATION_A")

    assert MoveRackRequest("req-rack", _caller(), "rack-1", source, target).rack_id == "rack-1"
    assert RotateRackRequest("req-rotate", _caller(), "rack-1", target, RackFace.B).target_face is RackFace.B
    assert (
        len(
            MoveBinsRequest(
                "req-bins",
                _caller(),
                (BinMove("bin-1", RackBinSlot("rack-1", "1-1"), HandoffPosition("ROLLER_IN")),),
            ).moves
        )
        == 1
    )
    assert (
        len(
            ExchangeBinsRequest(
                "req-exchange",
                _caller(),
                (BinExchangePair("full-1", RackBinSlot("rack-1", "1-1"), "empty-1", RackBinSlot("rack-2", "1-1")),),
            ).exchange_pairs
        )
        == 1
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MoveRackRequest(
            "req-rack-type",
            _caller(),
            "rack-1",
            HandoffPosition("ROLLER_IN"),
            RackPosition("STATION_A"),
        ),
        lambda: RotateRackRequest(
            "req-rotate-type",
            _caller(),
            "rack-1",
            HandoffPosition("ROLLER_IN"),
            RackFace.B,
        ),
        lambda: BinMove("bin-type", RackPosition("STATION_A"), RackBinSlot("rack-1", "1")),
        lambda: BinExchangePair(
            "bin-left",
            HandoffPosition("ROLLER_IN"),
            "bin-right",
            RackBinSlot("rack-2", "1"),
        ),
    ],
)
def test_requests_reject_positions_outside_their_closed_types(factory: Callable[[], object]) -> None:
    with pytest.raises(TransportContractError):
        factory()


@pytest.mark.parametrize(
    ("position_type", "args"),
    [
        (RackPosition, ("STATION_A",)),
        (RackBinSlot, ("rack-1", "1")),
        (HandoffPosition, ("ROLLER_IN",)),
    ],
)
def test_position_discriminator_cannot_be_overridden(position_type: object, args: tuple[str, ...]) -> None:
    with pytest.raises(TypeError):
        position_type(*args, kind="VENDOR_POSITION")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MoveRackRequest(
            "req-kind-rack",
            _caller(),
            "rack-1",
            RackPosition("A"),
            RackPosition("B"),
            kind=TransportTaskKind.BIN_MOVE,
        ),
        lambda: RotateRackRequest(
            "req-kind-rotate",
            _caller(),
            "rack-1",
            RackPosition("ROTATE"),
            RackFace.B,
            kind=TransportTaskKind.BIN_EXCHANGE,
        ),
        lambda: MoveBinsRequest(
            "req-kind-bins",
            _caller(),
            (BinMove("bin-1", RackBinSlot("rack-1", "1"), HandoffPosition("IN")),),
            kind=TransportTaskKind.RACK_MOVE,
        ),
        lambda: ExchangeBinsRequest(
            "req-kind-exchange",
            _caller(),
            (BinExchangePair("bin-1", RackBinSlot("rack-1", "1"), "bin-2", RackBinSlot("rack-2", "1")),),
            kind=TransportTaskKind.RACK_ROTATE,
        ),
    ],
)
def test_request_kind_is_fixed_by_request_type(factory: Callable[[], object]) -> None:
    with pytest.raises(TypeError):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MoveRackRequest(
            "req-vendor-rack",
            _caller(),
            "rack-1",
            VendorRackPosition("STATION_A"),
            RackPosition("STATION_B"),
        ),
        lambda: RotateRackRequest(
            "req-vendor-rotate",
            _caller(),
            "rack-1",
            VendorRackPosition("ROTATE_POINT"),
            RackFace.B,
        ),
        lambda: BinMove("bin-vendor-slot", VendorRackBinSlot("rack-1", "1"), HandoffPosition("ROLLER_IN")),
        lambda: BinMove("bin-vendor-handoff", RackBinSlot("rack-1", "1"), VendorHandoffPosition("ROLLER_IN")),
        lambda: BinExchangePair(
            "bin-left",
            VendorRackBinSlot("rack-1", "1"),
            "bin-right",
            RackBinSlot("rack-2", "1"),
        ),
    ],
)
def test_requests_reject_position_subclasses_with_extra_wire_fields(factory: Callable[[], object]) -> None:
    with pytest.raises(TransportContractError):
        factory()


@pytest.mark.parametrize("count", [0, 3])
def test_exchange_rejects_pair_count_outside_one_to_two(count: int) -> None:
    pair = BinExchangePair("bin-1", RackBinSlot("rack-1", "1"), "bin-2", RackBinSlot("rack-2", "1"))
    with pytest.raises(TransportContractError):
        ExchangeBinsRequest("req", _caller(), tuple(pair for _ in range(count)))


def test_exchange_rejects_reused_bin_or_slot_across_pairs() -> None:
    first = BinExchangePair("bin-1", RackBinSlot("rack-1", "1"), "bin-2", RackBinSlot("rack-2", "1"))
    reused_bin = BinExchangePair("bin-1", RackBinSlot("rack-3", "1"), "bin-4", RackBinSlot("rack-4", "1"))
    reused_slot = BinExchangePair("bin-3", RackBinSlot("rack-1", "1"), "bin-4", RackBinSlot("rack-4", "1"))

    with pytest.raises(TransportContractError):
        ExchangeBinsRequest("req-bin", _caller(), (first, reused_bin))
    with pytest.raises(TransportContractError):
        ExchangeBinsRequest("req-slot", _caller(), (first, reused_slot))


def test_move_bins_accepts_four_members_and_rejects_five() -> None:
    moves = tuple(
        BinMove(f"bin-{index}", RackBinSlot("rack", str(index)), HandoffPosition("ROLLER_IN")) for index in range(5)
    )

    assert len(MoveBinsRequest("req-4", _caller(), moves[:4]).moves) == 4
    with pytest.raises(TransportContractError):
        MoveBinsRequest("req-5", _caller(), moves)


def test_move_bins_allows_shared_handoff_but_not_shared_rack_slot() -> None:
    shared_handoff = HandoffPosition("ROLLER_IN")
    request = MoveBinsRequest(
        "req-ok",
        _caller(),
        (
            BinMove("bin-1", RackBinSlot("rack", "1"), shared_handoff),
            BinMove("bin-2", RackBinSlot("rack", "2"), shared_handoff),
        ),
    )
    assert len(request.moves) == 2

    with pytest.raises(TransportContractError):
        MoveBinsRequest(
            "req-bad",
            _caller(),
            (
                BinMove("bin-1", RackBinSlot("rack", "1"), shared_handoff),
                BinMove("bin-2", RackBinSlot("rack", "1"), HandoffPosition("ROLLER_OUT")),
            ),
        )


def test_handle_and_outcome_expose_only_stable_plugin_contract() -> None:
    handle = TransportHandle(transport_task_id="transport-1", client_request_id="request-1")
    outcome = TransportOutcome(
        transport_task_id=handle.transport_task_id,
        client_request_id=handle.client_request_id,
        outcome_version=2,
        caller=_caller(),
        status=TransportOutcomeStatus.UNKNOWN,
        reason_code="TRANSPORT_RESULT_TIMEOUT",
        members=(),
    )

    assert outcome.outcome_version == 2
    assert outcome.status is TransportOutcomeStatus.UNKNOWN


@pytest.mark.parametrize("value", ["", " "])
def test_identifiers_fail_closed(value: str) -> None:
    with pytest.raises(TransportContractError):
        TransportCaller(workline_id=value)
    with pytest.raises(TransportContractError):
        RackPosition(value)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MoveRackRequest(
            "r" * 121,
            _caller(),
            "rack-1",
            RackPosition("SOURCE"),
            RackPosition("TARGET"),
        ),
        lambda: MoveRackRequest(
            "request-long-rack",
            _caller(),
            "r" * 101,
            RackPosition("SOURCE"),
            RackPosition("TARGET"),
        ),
        lambda: BinMove("b" * 101, RackBinSlot("rack-1", "1"), HandoffPosition("ROLLER_IN")),
        lambda: RackBinSlot("r" * 101, "1"),
    ],
    ids=["client-request-id", "rack-id", "bin-id", "slot-rack-id"],
)
def test_persisted_request_identifiers_reject_values_larger_than_database_columns(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(TransportContractError):
        factory()
