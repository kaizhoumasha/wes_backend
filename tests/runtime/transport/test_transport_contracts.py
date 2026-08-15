from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

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
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
    TransportTaskKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_REQUEST_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4471"


def _caller() -> TransportCaller:
    return TransportCaller(workline_id="SORTER", station_id="STATION_A")


def test_transport_service_does_not_depend_on_wms_adapter() -> None:
    service_source = (Path(__file__).resolve().parents[3] / "src/app/transport/service.py").read_text(encoding="utf-8")

    assert "src.app.wms_adapter" not in service_source


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

    move_rack = MoveRackRequest(_REQUEST_ID, _caller(), "rack-1", source, target, RackFace.A)

    assert move_rack.rack_id == "rack-1"
    assert move_rack.target_face is RackFace.A
    assert RotateRackRequest(_REQUEST_ID, _caller(), "rack-1", target, RackFace.B).target_face is RackFace.B
    assert (
        len(
            MoveBinsRequest(
                _REQUEST_ID,
                _caller(),
                (BinMove("bin-1", RackBinSlot("rack-1", RackFace.A, "1-1"), HandoffPosition("ROLLER_IN")),),
            ).moves
        )
        == 1
    )
    assert (
        len(
            ExchangeBinsRequest(
                _REQUEST_ID,
                _caller(),
                (
                    BinExchangePair(
                        "full-1",
                        RackBinSlot("rack-1", RackFace.A, "1-1"),
                        "empty-1",
                        RackBinSlot("rack-2", RackFace.A, "1-1"),
                    ),
                ),
            ).exchange_pairs
        )
        == 1
    )


@pytest.mark.parametrize("client_request_id", ["request-1", "019f12d0-58d7-4b4d-a23a-1b90aa5d4471"])
def test_request_contracts_require_uuid7_client_request_id(client_request_id: str) -> None:
    with pytest.raises(TransportContractError, match="client_request_id must be a UUIDv7"):
        MoveRackRequest(
            client_request_id,
            _caller(),
            "rack-1",
            RackPosition("SOURCE"),
            RackPosition("TARGET"),
            RackFace.A,
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda caller: MoveRackRequest(
            _REQUEST_ID, caller, "rack-1", RackPosition("SOURCE"), RackPosition("TARGET"), RackFace.A
        ),
        lambda caller: RotateRackRequest(_REQUEST_ID, caller, "rack-1", RackPosition("ROTATE"), RackFace.B),
        lambda caller: MoveBinsRequest(
            _REQUEST_ID,
            caller,
            (BinMove("bin-1", RackBinSlot("rack-1", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),),
        ),
        lambda caller: ExchangeBinsRequest(
            _REQUEST_ID,
            caller,
            (
                BinExchangePair(
                    "bin-1",
                    RackBinSlot("rack-1", RackFace.A, "1"),
                    "bin-2",
                    RackBinSlot("rack-2", RackFace.A, "1"),
                ),
            ),
        ),
    ],
)
def test_requests_reject_untyped_caller(factory: Callable[[TransportCaller], object]) -> None:
    invalid_caller = cast("TransportCaller", {"workline_id": "SORTER"})

    with pytest.raises(TransportContractError, match="caller must be a TransportCaller"):
        factory(invalid_caller)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MoveRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            HandoffPosition("ROLLER_IN"),
            RackPosition("STATION_A"),
            RackFace.A,
        ),
        lambda: RotateRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            HandoffPosition("ROLLER_IN"),
            RackFace.B,
        ),
        lambda: BinMove("bin-type", RackPosition("STATION_A"), RackBinSlot("rack-1", RackFace.A, "1")),
        lambda: BinExchangePair(
            "bin-left",
            HandoffPosition("ROLLER_IN"),
            "bin-right",
            RackBinSlot("rack-2", RackFace.A, "1"),
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
        (RackBinSlot, ("rack-1", RackFace.A, "1")),
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
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackPosition("A"),
            RackPosition("B"),
            RackFace.A,
            kind=TransportTaskKind.BIN_MOVE,
        ),
        lambda: RotateRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackPosition("ROTATE"),
            RackFace.B,
            kind=TransportTaskKind.BIN_EXCHANGE,
        ),
        lambda: MoveBinsRequest(
            _REQUEST_ID,
            _caller(),
            (BinMove("bin-1", RackBinSlot("rack-1", RackFace.A, "1"), HandoffPosition("IN")),),
            kind=TransportTaskKind.RACK_MOVE,
        ),
        lambda: ExchangeBinsRequest(
            _REQUEST_ID,
            _caller(),
            (
                BinExchangePair(
                    "bin-1",
                    RackBinSlot("rack-1", RackFace.A, "1"),
                    "bin-2",
                    RackBinSlot("rack-2", RackFace.A, "1"),
                ),
            ),
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
            _REQUEST_ID,
            _caller(),
            "rack-1",
            VendorRackPosition("STATION_A"),
            RackPosition("STATION_B"),
            RackFace.A,
        ),
        lambda: RotateRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            VendorRackPosition("ROTATE_POINT"),
            RackFace.B,
        ),
        lambda: BinMove("bin-vendor-slot", VendorRackBinSlot("rack-1", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),
        lambda: BinMove(
            "bin-vendor-handoff", RackBinSlot("rack-1", RackFace.A, "1"), VendorHandoffPosition("ROLLER_IN")
        ),
        lambda: BinExchangePair(
            "bin-left",
            VendorRackBinSlot("rack-1", RackFace.A, "1"),
            "bin-right",
            RackBinSlot("rack-2", RackFace.A, "1"),
        ),
    ],
)
def test_requests_reject_position_subclasses_with_extra_wire_fields(factory: Callable[[], object]) -> None:
    with pytest.raises(TransportContractError):
        factory()


@pytest.mark.parametrize("count", [0, 3])
def test_exchange_rejects_pair_count_outside_one_to_two(count: int) -> None:
    pair = BinExchangePair(
        "bin-1", RackBinSlot("rack-1", RackFace.A, "1"), "bin-2", RackBinSlot("rack-2", RackFace.A, "1")
    )
    with pytest.raises(TransportContractError):
        ExchangeBinsRequest(_REQUEST_ID, _caller(), tuple(pair for _ in range(count)))


def test_exchange_rejects_reused_bin_or_slot_across_pairs() -> None:
    first = BinExchangePair(
        "bin-1", RackBinSlot("rack-1", RackFace.A, "1"), "bin-2", RackBinSlot("rack-2", RackFace.A, "1")
    )
    reused_bin = BinExchangePair(
        "bin-1", RackBinSlot("rack-3", RackFace.A, "1"), "bin-4", RackBinSlot("rack-4", RackFace.A, "1")
    )
    reused_slot = BinExchangePair(
        "bin-3", RackBinSlot("rack-1", RackFace.A, "1"), "bin-4", RackBinSlot("rack-4", RackFace.A, "1")
    )

    with pytest.raises(TransportContractError):
        ExchangeBinsRequest(_REQUEST_ID, _caller(), (first, reused_bin))
    with pytest.raises(TransportContractError):
        ExchangeBinsRequest(_REQUEST_ID, _caller(), (first, reused_slot))


def test_rack_bin_slot_identity_includes_rack_face() -> None:
    face_a = RackBinSlot("rack-1", RackFace.A, "1")
    face_b = RackBinSlot("rack-1", RackFace.B, "1")

    assert face_a != face_b
    assert face_a.rack_face is RackFace.A
    with pytest.raises(TransportContractError, match="rack_face must be A or B"):
        RackBinSlot("rack-1", cast("RackFace", "C"), "1")


def test_two_pair_exchange_requires_all_left_and_all_right_bins_on_one_face_each() -> None:
    valid = (
        BinExchangePair(
            "left-1",
            RackBinSlot("rack-left", RackFace.A, "1"),
            "right-1",
            RackBinSlot("rack-right", RackFace.A, "1"),
        ),
        BinExchangePair(
            "left-2",
            RackBinSlot("rack-left", RackFace.A, "2"),
            "right-2",
            RackBinSlot("rack-right", RackFace.A, "2"),
        ),
    )
    assert len(ExchangeBinsRequest(_REQUEST_ID, _caller(), valid).exchange_pairs) == 2

    cross_face = (
        valid[0],
        BinExchangePair(
            "left-2",
            RackBinSlot("rack-left", RackFace.B, "2"),
            "right-2",
            RackBinSlot("rack-right", RackFace.B, "2"),
        ),
    )
    with pytest.raises(TransportContractError, match="one rack and face per side"):
        ExchangeBinsRequest(_REQUEST_ID, _caller(), cross_face)


def test_two_pair_exchange_requires_one_rack_and_face_per_side() -> None:
    cross_rack = (
        BinExchangePair(
            "left-1",
            RackBinSlot("rack-left-1", RackFace.A, "1"),
            "right-1",
            RackBinSlot("rack-right-1", RackFace.B, "1"),
        ),
        BinExchangePair(
            "left-2",
            RackBinSlot("rack-left-2", RackFace.A, "2"),
            "right-2",
            RackBinSlot("rack-right-2", RackFace.B, "2"),
        ),
    )

    with pytest.raises(TransportContractError, match="one rack and face per side"):
        ExchangeBinsRequest(_REQUEST_ID, _caller(), cross_rack)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MoveRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackPosition("SAME"),
            RackPosition("SAME"),
            RackFace.A,
        ),
        lambda: BinMove("bin-1", RackBinSlot("rack-1", RackFace.A, "1"), RackBinSlot("rack-1", RackFace.A, "1")),
        lambda: BinMove("bin-1", HandoffPosition("IN"), HandoffPosition("OUT")),
        lambda: BinExchangePair(
            "bin-1", RackBinSlot("rack-1", RackFace.A, "1"), "bin-1", RackBinSlot("rack-2", RackFace.A, "1")
        ),
        lambda: BinExchangePair(
            "bin-1", RackBinSlot("rack-1", RackFace.A, "1"), "bin-2", RackBinSlot("rack-1", RackFace.A, "1")
        ),
    ],
)
def test_requests_reject_degenerate_moves_and_exchanges(factory: Callable[[], object]) -> None:
    with pytest.raises(TransportContractError):
        factory()


def test_rotation_rejects_target_face_outside_closed_enum() -> None:
    with pytest.raises(TransportContractError, match="target_face must be A or B"):
        RotateRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackPosition("ROTATE"),
            cast("RackFace", "C"),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"final_position": RackPosition("A"), "position_unknown": True},
    ],
)
def test_member_outcome_requires_exactly_one_position_fact(kwargs: dict[str, object]) -> None:
    with pytest.raises(TransportContractError, match="xor"):
        TransportMemberOutcome("rack-1", **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("outcome_version", [0, -1])
def test_outcome_version_must_be_positive(outcome_version: int) -> None:
    with pytest.raises(TransportContractError, match="outcome_version must be positive"):
        TransportOutcome(
            transport_task_id="transport-1",
            client_request_id="request-1",
            outcome_version=outcome_version,
            caller=_caller(),
            status=TransportOutcomeStatus.UNKNOWN,
            reason_code="UNKNOWN",
            members=(),
        )


def test_move_bins_accepts_four_members_and_rejects_five() -> None:
    moves = tuple(
        BinMove(f"bin-{index}", RackBinSlot("rack", RackFace.A, str(index)), HandoffPosition("ROLLER_IN"))
        for index in range(5)
    )

    assert len(MoveBinsRequest(_REQUEST_ID, _caller(), moves[:4]).moves) == 4
    with pytest.raises(TransportContractError):
        MoveBinsRequest(_REQUEST_ID, _caller(), moves)


def test_move_bins_allows_shared_handoff_but_not_shared_rack_slot() -> None:
    shared_handoff = HandoffPosition("ROLLER_IN")
    request = MoveBinsRequest(
        _REQUEST_ID,
        _caller(),
        (
            BinMove("bin-1", RackBinSlot("rack", RackFace.A, "1"), shared_handoff),
            BinMove("bin-2", RackBinSlot("rack", RackFace.A, "2"), shared_handoff),
        ),
    )
    assert len(request.moves) == 2

    with pytest.raises(TransportContractError):
        MoveBinsRequest(
            _REQUEST_ID,
            _caller(),
            (
                BinMove("bin-1", RackBinSlot("rack", RackFace.A, "1"), shared_handoff),
                BinMove("bin-2", RackBinSlot("rack", RackFace.A, "1"), HandoffPosition("ROLLER_OUT")),
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
            RackFace.A,
        ),
        lambda: MoveRackRequest(
            _REQUEST_ID,
            _caller(),
            "r" * 101,
            RackPosition("SOURCE"),
            RackPosition("TARGET"),
            RackFace.A,
        ),
        lambda: BinMove("b" * 101, RackBinSlot("rack-1", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),
        lambda: RackBinSlot("r" * 101, RackFace.A, "1"),
    ],
    ids=["client-request-id", "rack-id", "bin-id", "slot-rack-id"],
)
def test_persisted_request_identifiers_reject_values_larger_than_database_columns(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(TransportContractError):
        factory()


def test_transport_caller_keeps_only_local_routing_fields() -> None:
    caller = TransportCaller("SORTER", "STATION_A")

    assert (caller.workline_id, caller.station_id) == ("SORTER", "STATION_A")
    with pytest.raises(TypeError):
        TransportCaller("SORTER", "STATION_A", "legacy-correlation")
    with pytest.raises(TypeError):
        TransportCaller("SORTER", "STATION_A", correlation_id="legacy-correlation")
