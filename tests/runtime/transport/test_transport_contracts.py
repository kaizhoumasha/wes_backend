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
    RackPosition,
    RackReference,
    RcsTemplateId,
    RotateRackRequest,
    TransportCaller,
    TransportContractError,
    TransportHandle,
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
    TransportTaskKind,
    ZonePosition,
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

    move_rack = MoveRackRequest(_REQUEST_ID, _caller(), "rack-1", source, target, "90")

    assert move_rack.rack_id == "rack-1"
    assert move_rack.target_face == "90"
    assert RotateRackRequest(_REQUEST_ID, _caller(), "rack-1", target, "270").target_face == "270"
    assert (
        len(
            MoveBinsRequest(
                _REQUEST_ID,
                _caller(),
                (BinMove("bin-1", RackBinSlot("rack-1", "90", "1-1"), HandoffPosition("ROLLER_IN")),),
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
                        RackBinSlot("rack-1", "90", "1-1"),
                        "empty-1",
                        RackBinSlot("rack-2", "90", "1-1"),
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
            "90",
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda caller: MoveRackRequest(
            _REQUEST_ID, caller, "rack-1", RackPosition("SOURCE"), RackPosition("TARGET"), "90"
        ),
        lambda caller: RotateRackRequest(_REQUEST_ID, caller, "rack-1", RackPosition("ROTATE"), "270"),
        lambda caller: MoveBinsRequest(
            _REQUEST_ID,
            caller,
            (BinMove("bin-1", RackBinSlot("rack-1", "90", "1"), HandoffPosition("ROLLER_IN")),),
        ),
        lambda caller: ExchangeBinsRequest(
            _REQUEST_ID,
            caller,
            (
                BinExchangePair(
                    "bin-1",
                    RackBinSlot("rack-1", "90", "1"),
                    "bin-2",
                    RackBinSlot("rack-2", "90", "1"),
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
            "90",
        ),
        lambda: RotateRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            HandoffPosition("ROLLER_IN"),
            "270",
        ),
        lambda: BinMove("bin-type", RackPosition("STATION_A"), RackBinSlot("rack-1", "90", "1")),
        lambda: BinExchangePair(
            "bin-left",
            HandoffPosition("ROLLER_IN"),
            "bin-right",
            RackBinSlot("rack-2", "90", "1"),
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
        (RackBinSlot, ("rack-1", "90", "1")),
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
            "90",
            kind=TransportTaskKind.BIN_MOVE,
        ),
        lambda: RotateRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackPosition("ROTATE"),
            "270",
            kind=TransportTaskKind.BIN_EXCHANGE,
        ),
        lambda: MoveBinsRequest(
            _REQUEST_ID,
            _caller(),
            (BinMove("bin-1", RackBinSlot("rack-1", "90", "1"), HandoffPosition("IN")),),
            kind=TransportTaskKind.RACK_MOVE,
        ),
        lambda: ExchangeBinsRequest(
            _REQUEST_ID,
            _caller(),
            (
                BinExchangePair(
                    "bin-1",
                    RackBinSlot("rack-1", "90", "1"),
                    "bin-2",
                    RackBinSlot("rack-2", "90", "1"),
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
            "90",
        ),
        lambda: RotateRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            VendorRackPosition("ROTATE_POINT"),
            "270",
        ),
        lambda: BinMove("bin-vendor-slot", VendorRackBinSlot("rack-1", "90", "1"), HandoffPosition("ROLLER_IN")),
        lambda: BinMove("bin-vendor-handoff", RackBinSlot("rack-1", "90", "1"), VendorHandoffPosition("ROLLER_IN")),
        lambda: BinExchangePair(
            "bin-left",
            VendorRackBinSlot("rack-1", "90", "1"),
            "bin-right",
            RackBinSlot("rack-2", "90", "1"),
        ),
    ],
)
def test_requests_reject_position_subclasses_with_extra_wire_fields(factory: Callable[[], object]) -> None:
    with pytest.raises(TransportContractError):
        factory()


@pytest.mark.parametrize("count", [0, 3])
def test_exchange_rejects_pair_count_outside_one_to_two(count: int) -> None:
    pair = BinExchangePair("bin-1", RackBinSlot("rack-1", "90", "1"), "bin-2", RackBinSlot("rack-2", "90", "1"))
    with pytest.raises(TransportContractError):
        ExchangeBinsRequest(_REQUEST_ID, _caller(), tuple(pair for _ in range(count)))


def test_exchange_rejects_reused_bin_or_slot_across_pairs() -> None:
    first = BinExchangePair("bin-1", RackBinSlot("rack-1", "90", "1"), "bin-2", RackBinSlot("rack-2", "90", "1"))
    reused_bin = BinExchangePair("bin-1", RackBinSlot("rack-3", "90", "1"), "bin-4", RackBinSlot("rack-4", "90", "1"))
    reused_slot = BinExchangePair("bin-3", RackBinSlot("rack-1", "90", "1"), "bin-4", RackBinSlot("rack-4", "90", "1"))

    with pytest.raises(TransportContractError):
        ExchangeBinsRequest(_REQUEST_ID, _caller(), (first, reused_bin))
    with pytest.raises(TransportContractError):
        ExchangeBinsRequest(_REQUEST_ID, _caller(), (first, reused_slot))


def test_rack_bin_slot_identity_includes_rack_face() -> None:
    face_a = RackBinSlot("rack-1", "90", "1")
    face_b = RackBinSlot("rack-1", "270", "1")

    assert face_a != face_b
    assert face_a.rack_face == "90"
    with pytest.raises(TransportContractError, match="rack_face must be a non-empty string"):
        RackBinSlot("rack-1", cast("str", ""), "1")


def test_two_pair_exchange_accepts_equivalent_pair_orientation() -> None:
    exchange_pairs = (
        BinExchangePair(
            "left-1",
            RackBinSlot("rack-left", "90", "1"),
            "right-1",
            RackBinSlot("rack-right", "90", "1"),
        ),
        BinExchangePair(
            "right-2",
            RackBinSlot("rack-right", "90", "2"),
            "left-2",
            RackBinSlot("rack-left", "90", "2"),
        ),
    )

    assert len(ExchangeBinsRequest(_REQUEST_ID, _caller(), exchange_pairs).exchange_pairs) == 2


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MoveBinsRequest(
            _REQUEST_ID,
            _caller(),
            (
                BinMove("bin-1", RackBinSlot("rack-1", "90", "1"), HandoffPosition("ROLLER_IN")),
                BinMove("bin-2", RackBinSlot("rack-1", "270", "2"), HandoffPosition("ROLLER_OUT")),
            ),
        ),
        lambda: ExchangeBinsRequest(
            _REQUEST_ID,
            _caller(),
            (
                BinExchangePair(
                    "bin-1",
                    RackBinSlot("rack-1", "90", "1"),
                    "bin-2",
                    RackBinSlot("rack-2", "90", "1"),
                ),
                BinExchangePair(
                    "bin-3",
                    RackBinSlot("rack-1", "270", "2"),
                    "bin-4",
                    RackBinSlot("rack-2", "90", "2"),
                ),
            ),
        ),
    ],
)
def test_bin_batches_reject_multiple_faces_for_the_same_rack(factory: Callable[[], object]) -> None:
    with pytest.raises(TransportContractError, match="same rack must use one face"):
        factory()


def test_two_pair_exchange_requires_one_rack_and_face_per_side() -> None:
    cross_rack = (
        BinExchangePair(
            "left-1",
            RackBinSlot("rack-left-1", "90", "1"),
            "right-1",
            RackBinSlot("rack-right-1", "270", "1"),
        ),
        BinExchangePair(
            "left-2",
            RackBinSlot("rack-left-2", "90", "2"),
            "right-2",
            RackBinSlot("rack-right-2", "270", "2"),
        ),
    )

    with pytest.raises(TransportContractError, match="one or two endpoint groups"):
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
            "90",
        ),
        lambda: BinMove("bin-1", RackBinSlot("rack-1", "90", "1"), RackBinSlot("rack-1", "90", "1")),
        lambda: BinMove("bin-1", HandoffPosition("IN"), HandoffPosition("OUT")),
        lambda: BinExchangePair("bin-1", RackBinSlot("rack-1", "90", "1"), "bin-1", RackBinSlot("rack-2", "90", "1")),
        lambda: BinExchangePair("bin-1", RackBinSlot("rack-1", "90", "1"), "bin-2", RackBinSlot("rack-1", "90", "1")),
    ],
)
def test_requests_reject_degenerate_moves_and_exchanges(factory: Callable[[], object]) -> None:
    with pytest.raises(TransportContractError):
        factory()


def test_rotation_rejects_empty_target_face() -> None:
    with pytest.raises(TransportContractError, match="target_face must be a non-empty string"):
        RotateRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackPosition("ROTATE"),
            cast("str", ""),
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
        BinMove(f"bin-{index}", RackBinSlot("rack", "90", str(index)), HandoffPosition("ROLLER_IN"))
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
            BinMove("bin-1", RackBinSlot("rack", "90", "1"), shared_handoff),
            BinMove("bin-2", RackBinSlot("rack", "90", "2"), shared_handoff),
        ),
    )
    assert len(request.moves) == 2

    with pytest.raises(TransportContractError):
        MoveBinsRequest(
            _REQUEST_ID,
            _caller(),
            (
                BinMove("bin-1", RackBinSlot("rack", "90", "1"), shared_handoff),
                BinMove("bin-2", RackBinSlot("rack", "90", "1"), HandoffPosition("ROLLER_OUT")),
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


@pytest.mark.parametrize("value", ["", " ", "value\x00suffix"])
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
            "90",
        ),
        lambda: MoveRackRequest(
            _REQUEST_ID,
            _caller(),
            "r" * 101,
            RackPosition("SOURCE"),
            RackPosition("TARGET"),
            "90",
        ),
        lambda: BinMove("b" * 101, RackBinSlot("rack-1", "90", "1"), HandoffPosition("ROLLER_IN")),
        lambda: RackBinSlot("r" * 101, "90", "1"),
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


@pytest.mark.parametrize(
    ("source", "target", "template"),
    [
        (ZonePosition("ZONE-A"), RackPosition("WORK"), RcsTemplateId.CTU01),
        (RackReference("rack-1"), RackPosition("WORK"), RcsTemplateId.CTU01),
        (RackPosition("SOURCE"), RackPosition("WORK"), RcsTemplateId.CTU01),
        (RackPosition("WORK"), RackReference("rack-1"), RcsTemplateId.CTU03),
        (RackPosition("WORK"), ZonePosition("ZONE-A"), RcsTemplateId.CTU03),
        (RackPosition("WORK"), RackPosition("TARGET"), RcsTemplateId.CTU03),
        (RackPosition("WORK"), RackPosition("TARGET"), RcsTemplateId.F01),
    ],
)
def test_move_rack_accepts_only_approved_position_and_template_edges(
    source: object,
    target: object,
    template: RcsTemplateId,
) -> None:
    request = MoveRackRequest(
        _REQUEST_ID,
        _caller(),
        "rack-1",
        cast("RackPosition", source),
        cast("RackPosition", target),
        "90",
        template,
    )

    assert request.target_face == "90"
    assert request.rcs_template_id is template


@pytest.mark.parametrize("target_face", ["270", "FACE@01", "面-1", " ", "x" * 1000])
def test_face_values_are_opaque_non_empty_strings(target_face: str) -> None:
    request = MoveRackRequest(
        _REQUEST_ID,
        _caller(),
        "rack-1",
        RackPosition("SOURCE"),
        RackPosition("TARGET"),
        target_face,
    )
    slot = RackBinSlot("rack-1", target_face, "slot-1")

    assert request.target_face == target_face
    assert slot.rack_face == target_face


@pytest.mark.parametrize(
    ("invalid_face", "expected_message"),
    [("\x00", "must not contain NUL"), ("\ud800", "must be valid UTF-8")],
)
@pytest.mark.parametrize(
    "factory",
    [
        lambda face: RackBinSlot("rack-1", face, "slot-1"),
        lambda face: MoveRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackPosition("SOURCE"),
            RackPosition("TARGET"),
            face,
        ),
        lambda face: RotateRackRequest(_REQUEST_ID, _caller(), "rack-1", RackPosition("WORK"), face),
        lambda face: TransportMemberOutcome("rack-1", RackPosition("TARGET"), arrival_face=face),
    ],
)
def test_face_values_reject_unrepresentable_values_before_persistence(
    factory: Callable[[str], object],
    invalid_face: str,
    expected_message: str,
) -> None:
    with pytest.raises(TransportContractError, match=expected_message):
        factory(invalid_face)


@pytest.mark.parametrize("target_face", ["", 90, True, None])
def test_required_face_values_reject_empty_or_non_string_values(target_face: object) -> None:
    with pytest.raises(TransportContractError, match="target_face must be a non-empty string"):
        MoveRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackPosition("SOURCE"),
            RackPosition("TARGET"),
            cast("str", target_face),
        )


@pytest.mark.parametrize(
    ("source", "target", "template"),
    [
        (RackReference("rack-1"), ZonePosition("ZONE-A"), RcsTemplateId.CTU01),
        (ZonePosition("ZONE-A"), RackReference("rack-1"), RcsTemplateId.CTU01),
        (RackReference("rack-1"), RackReference("rack-1"), RcsTemplateId.CTU01),
        (ZonePosition("ZONE-A"), ZonePosition("ZONE-B"), RcsTemplateId.CTU01),
        (RackPosition("SOURCE"), ZonePosition("ZONE-A"), RcsTemplateId.F01),
        (RackPosition("SOURCE"), RackPosition("TARGET"), RcsTemplateId.CTU02),
    ],
)
def test_move_rack_rejects_unapproved_position_and_template_edges(
    source: object,
    target: object,
    template: RcsTemplateId,
) -> None:
    with pytest.raises(TransportContractError):
        MoveRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            cast("RackPosition", source),
            cast("RackPosition", target),
            "90",
            template,
        )


def test_rack_reference_must_match_outer_rack_identity() -> None:
    with pytest.raises(TransportContractError, match="RACK location_code must match rack_id"):
        MoveRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackReference("rack-2"),
            RackPosition("WORK"),
            "90",
            RcsTemplateId.CTU01,
        )


def test_rotate_rack_uses_ctu02_and_preserves_opaque_face() -> None:
    request = RotateRackRequest(_REQUEST_ID, _caller(), "rack-1", RackPosition("WORK"), "270")

    assert request.target_face == "270"
    assert request.rcs_template_id is RcsTemplateId.CTU02

    with pytest.raises(TransportContractError, match="CTU02"):
        RotateRackRequest(
            _REQUEST_ID,
            _caller(),
            "rack-1",
            RackPosition("WORK"),
            "270",
            RcsTemplateId.F01,
        )
