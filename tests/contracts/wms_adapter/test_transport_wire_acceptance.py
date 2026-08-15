"""Transport 固定线上接口的闭集合同验收。"""

from __future__ import annotations

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
)
from src.app.transport.submit_snapshot import build_submit_data
from src.app.wms_adapter.transport_wire import (
    POSITION_OPERATION,
    RESULT_OPERATION,
    validate_callback_envelope,
)
from src.core.uuid7 import new_uuid7


def _envelope(operation: str, data: object) -> dict[str, object]:
    return {
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        "operation": operation,
        "timestamp": 1,
        "data": data,
    }


def _position_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "transport_task_id": "transport-1",
        "container_id": "container-1",
        "milestone": "SOURCE_PICKED",
    }
    data.update(overrides)
    return data


def _result_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "transport_task_id": "transport-1",
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "container-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    data.update(overrides)
    return data


def _rack_result_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "transport_task_id": "transport-1",
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "rack-1",
        "status": "SUCCEEDED",
        "final_position": {"kind": "RACK_POSITION", "location_code": "TARGET"},
        "arrival_face": "A",
    }
    data.update(overrides)
    return data


def test_rack_submit_data_uses_one_shared_wire_shape() -> None:
    caller = TransportCaller("SORTER", "STATION_A")
    source = RackPosition("RACK_BUFFER_A")
    target = RackPosition("RACK_BUFFER_B")

    assert build_submit_data(MoveRackRequest(new_uuid7(), caller, "rack-1", source, target, RackFace.B), "move-1") == {
        "transport_task_id": "move-1",
        "kind": "RACK_MOVE",
        "rack_id": "rack-1",
        "source": {"kind": "RACK_POSITION", "location_code": "RACK_BUFFER_A"},
        "target": {"kind": "RACK_POSITION", "location_code": "RACK_BUFFER_B"},
        "target_face": "B",
    }
    assert build_submit_data(RotateRackRequest(new_uuid7(), caller, "rack-1", target, RackFace.A), "rotate-1") == {
        "transport_task_id": "rotate-1",
        "kind": "RACK_ROTATE",
        "rack_id": "rack-1",
        "source": {"kind": "RACK_POSITION", "location_code": "RACK_BUFFER_B"},
        "target": {"kind": "RACK_POSITION", "location_code": "RACK_BUFFER_B"},
        "target_face": "A",
    }


def test_bin_move_submit_data_maps_container_identity_and_sorts_members() -> None:
    request = MoveBinsRequest(
        new_uuid7(),
        TransportCaller("SORTER", "STATION_A"),
        (
            BinMove("container-2", RackBinSlot("rack-1", RackFace.A, "2"), HandoffPosition("ROLLER_IN")),
            BinMove("container-1", RackBinSlot("rack-1", RackFace.A, "1"), HandoffPosition("ROLLER_IN")),
        ),
    )

    data = build_submit_data(request, "transport-1")

    assert data == {
        "transport_task_id": "transport-1",
        "kind": "BIN_MOVE",
        "moves": [
            {
                "container_id": "container-1",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "1"},
                "target": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            },
            {
                "container_id": "container-2",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "2"},
                "target": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            },
        ],
    }


def test_bin_exchange_submit_data_flattens_pairs_and_sorts_members() -> None:
    left = RackBinSlot("rack-1", RackFace.A, "1")
    right = RackBinSlot("rack-2", RackFace.B, "2")
    request = ExchangeBinsRequest(
        new_uuid7(),
        TransportCaller("SORTER", "STATION_A"),
        (BinExchangePair("container-2", left, "container-1", right),),
    )

    assert build_submit_data(request, "transport-1") == {
        "transport_task_id": "transport-1",
        "kind": "BIN_EXCHANGE",
        "moves": [
            {
                "container_id": "container-1",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "B", "slot_id": "2"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "1"},
            },
            {
                "container_id": "container-2",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "1"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "B", "slot_id": "2"},
            },
        ],
    }


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "callback envelope must be an object"),
        ({1: "value"}, "callback envelope must be an object"),
        (
            {"operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472", "operation": POSITION_OPERATION, "timestamp": 1},
            "callback envelope fields do not match the closed contract",
        ),
        (
            {
                "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
                "operation": POSITION_OPERATION,
                "timestamp": True,
                "data": {},
            },
            "timestamp must be an integer",
        ),
        (
            _envelope(POSITION_OPERATION, _position_data()) | {"timestamp": -(2**63) - 1},
            "timestamp must be a non-negative signed 64-bit integer",
        ),
        (
            _envelope(POSITION_OPERATION, _position_data()) | {"timestamp": 2**63},
            "timestamp must be a non-negative signed 64-bit integer",
        ),
        (_envelope("transport.task.unknown@v1", {}), "unsupported transport callback operation"),
    ],
)
def test_callback_envelope_rejects_non_closed_or_unsupported_values(value: object, message: str) -> None:
    with pytest.raises(TransportContractError, match=message):
        validate_callback_envelope(value)


@pytest.mark.parametrize("timestamp", [0, 2**63 - 1])
def test_callback_envelope_accepts_non_negative_signed_64_bit_timestamp_boundaries(timestamp: int) -> None:
    envelope = _envelope(POSITION_OPERATION, _position_data())
    envelope["timestamp"] = timestamp

    assert validate_callback_envelope(envelope)["timestamp"] == timestamp


@pytest.mark.parametrize("field", ["operation_id", "transport_task_id", "container_id"])
def test_position_callback_rejects_blank_identifiers(field: str) -> None:
    envelope = _envelope(POSITION_OPERATION, _position_data())
    if field == "operation_id":
        envelope[field] = " "
    else:
        assert isinstance(envelope["data"], dict)
        envelope["data"][field] = " "

    with pytest.raises(TransportContractError, match=f"{field} must not be blank"):
        validate_callback_envelope(envelope)


@pytest.mark.parametrize(
    ("operation", "data", "field", "limit"),
    [
        (POSITION_OPERATION, _position_data(), "transport_task_id", 80),
        (POSITION_OPERATION, _position_data(), "container_id", 100),
        (RESULT_OPERATION, _result_data(), "transport_task_id", 80),
    ],
)
def test_callback_rejects_identifiers_longer_than_persistence_contract(
    operation: str,
    data: dict[str, object],
    field: str,
    limit: int,
) -> None:
    data[field] = "x" * (limit + 1)

    with pytest.raises(TransportContractError, match=f"{field} exceeds {limit} characters"):
        validate_callback_envelope(_envelope(operation, data))


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_position_data(milestone="UNKNOWN"), "invalid position milestone"),
        (_position_data(milestone="TARGET_PLACED"), "TARGET_PLACED requires final_position"),
        (
            _position_data(final_position={"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"}),
            "final_position is only valid for TARGET_PLACED",
        ),
        (
            _position_data(
                milestone="TARGET_PLACED",
                final_position={"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN", "extra": True},
            ),
            "handoff position fields do not match the closed contract",
        ),
    ],
)
def test_position_callback_enforces_milestone_contract(data: dict[str, object], message: str) -> None:
    with pytest.raises(TransportContractError, match=message):
        validate_callback_envelope(_envelope(POSITION_OPERATION, data))


def test_target_placed_accepts_a_closed_rack_slot_position() -> None:
    data = _position_data(
        milestone="TARGET_PLACED",
        final_position={"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "1"},
    )

    assert validate_callback_envelope(_envelope(POSITION_OPERATION, data))["data"] == data


def test_target_placed_rejects_a_rack_position() -> None:
    data = _position_data(
        milestone="TARGET_PLACED",
        final_position={"kind": "RACK_POSITION", "location_code": "STATION_A"},
    )

    with pytest.raises(
        TransportContractError, match="TARGET_PLACED position must be a rack bin slot or handoff position"
    ):
        validate_callback_envelope(_envelope(POSITION_OPERATION, data))


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_result_data(kind="UNKNOWN"), "invalid transport kind"),
        (_result_data(results=[]), "results must be a non-empty list"),
        (_result_data(results={}), "results must be a non-empty list"),
        (
            _result_data(
                results=[
                    {
                        "container_id": "container-1",
                        "status": "FAILED",
                        "position_unknown": True,
                        "failure_code": "POSITION_UNKNOWN",
                    },
                    {
                        "container_id": "container-1",
                        "status": "FAILED",
                        "position_unknown": True,
                        "failure_code": "POSITION_UNKNOWN",
                    },
                ]
            ),
            "duplicate result container_id",
        ),
        (
            _result_data(results=[{"container_id": "container-1", "status": "UNKNOWN", "position_unknown": True}]),
            "invalid member result status",
        ),
        (
            _result_data(
                results=[{"container_id": "container-1", "status": "FAILED", "failure_code": "RCS_EXECUTION_FAILED"}]
            ),
            "final_position xor position_unknown=true is required",
        ),
        (
            _result_data(
                results=[
                    {
                        "container_id": "container-1",
                        "status": "FAILED",
                        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                        "position_unknown": False,
                        "failure_code": "RCS_EXECUTION_FAILED",
                    }
                ]
            ),
            "position_unknown must be literal true",
        ),
        (
            _result_data(results=[{"container_id": "container-1", "status": "SUCCEEDED", "position_unknown": True}]),
            "SUCCEEDED requires known position and no failure_code",
        ),
        (
            _result_data(
                results=[
                    {
                        "container_id": "container-1",
                        "status": "SUCCEEDED",
                        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                        "failure_code": "IMPOSSIBLE",
                    }
                ]
            ),
            "SUCCEEDED requires known position and no failure_code",
        ),
        (
            _result_data(results=[{"container_id": "container-1", "status": "FAILED", "position_unknown": True}]),
            "failure_code must not be blank",
        ),
        (
            _rack_result_data(arrival_face=None),
            "known rack result requires arrival_face",
        ),
        (
            _result_data(
                results=[
                    {
                        "container_id": "container-1",
                        "status": "SUCCEEDED",
                        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                        "arrival_face": "A",
                    }
                ]
            ),
            "arrival_face is not valid for this result",
        ),
        (
            _rack_result_data(
                status="FAILED",
                final_position={"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                failure_code="RCS_EXECUTION_FAILED",
            ),
            "rack result position must be RACK_POSITION",
        ),
        (
            _result_data(
                results=[
                    {
                        "container_id": "container-1",
                        "status": "FAILED",
                        "final_position": {"kind": "RACK_POSITION", "location_code": "STATION_A"},
                        "failure_code": "RCS_EXECUTION_FAILED",
                    }
                ]
            ),
            "bin result position must be a rack bin slot or handoff position",
        ),
        (
            _result_data(
                results=[
                    {
                        "container_id": "container-1",
                        "status": "FAILED",
                        "position_unknown": True,
                        "failure_code": "F" * 121,
                    }
                ]
            ),
            "invalid failure_code",
        ),
    ],
)
def test_result_callback_rejects_values_outside_closed_member_contract(
    data: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TransportContractError, match=message):
        validate_callback_envelope(_envelope(RESULT_OPERATION, data))


@pytest.mark.parametrize(
    ("position", "message"),
    [
        (None, "position must be an object with kind"),
        ({}, "position must be an object with kind"),
        ({"kind": "RACK_POSITION", "location_code": " "}, "location_code must not be blank"),
        ({"kind": "RACK_BIN_SLOT", "rack_id": " ", "rack_face": "A", "slot_id": "1"}, "rack_id must not be blank"),
        (
            {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": " "},
            "slot_id must not be blank",
        ),
        ({"kind": "HANDOFF_POSITION", "location_code": " "}, "location_code must not be blank"),
        ({"kind": "UNKNOWN"}, "invalid position kind"),
    ],
)
def test_result_callback_rejects_invalid_position_shapes(position: object, message: str) -> None:
    data = _result_data(results=[{"container_id": "container-1", "status": "SUCCEEDED", "final_position": position}])

    with pytest.raises(TransportContractError, match=message):
        validate_callback_envelope(_envelope(RESULT_OPERATION, data))


def test_result_callback_accepts_known_rack_and_unknown_failed_member() -> None:
    known = _rack_result_data(
        kind="RACK_ROTATE",
        final_position={"kind": "RACK_POSITION", "location_code": "ROTATE"},
        arrival_face="B",
    )
    unknown = _result_data(
        results=[
            {
                "container_id": "container-1",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            }
        ]
    )

    assert validate_callback_envelope(_envelope(RESULT_OPERATION, known))["data"] == known
    assert validate_callback_envelope(_envelope(RESULT_OPERATION, unknown))["data"] == unknown


def test_callback_wire_uses_its_own_uuid7_operation_id_without_legacy_business_ids() -> None:
    operation_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
    envelope = {
        "operation_id": operation_id,
        "operation": POSITION_OPERATION,
        "timestamp": 1,
        "data": {
            "transport_task_id": "transport-1",
            "container_id": "container-1",
            "milestone": "SOURCE_PICKED",
        },
    }

    assert validate_callback_envelope(envelope) == envelope
    with pytest.raises(TransportContractError):
        validate_callback_envelope({**envelope, "operation_id": "019f12d0-58d7-4b4d-a23a-1b90aa5d4472"})

    with pytest.raises(TransportContractError, match="lowercase canonical UUIDv7"):
        validate_callback_envelope({**envelope, "operation_id": operation_id.upper()})


def test_result_callback_requires_a_positive_outcome_revision() -> None:
    with pytest.raises(TransportContractError, match="outcome_revision must be a positive integer"):
        validate_callback_envelope(_envelope(RESULT_OPERATION, _result_data(outcome_revision=0)))


def test_result_callback_rejects_outcome_revision_above_signed_int64() -> None:
    with pytest.raises(TransportContractError, match="signed 64-bit range"):
        validate_callback_envelope(_envelope(RESULT_OPERATION, _result_data(outcome_revision=2**63)))


def test_result_callback_rejects_vendor_private_failure_code() -> None:
    data = _result_data(
        results=[
            {
                "container_id": "container-1",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "VENDOR_PRIVATE_CODE",
            }
        ]
    )

    with pytest.raises(TransportContractError, match="invalid failure_code"):
        validate_callback_envelope(_envelope(RESULT_OPERATION, data))


@pytest.mark.parametrize(
    "position",
    [
        {"kind": "RACK_POSITION", "location_code": "L" * 101},
        {"kind": "RACK_BIN_SLOT", "rack_id": "R" * 101, "rack_face": "A", "slot_id": "1"},
        {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "S" * 101},
        {"kind": "HANDOFF_POSITION", "location_code": "L" * 101},
    ],
)
def test_callback_positions_reject_text_longer_than_100_code_points(position: dict[str, object]) -> None:
    data = _result_data(results=[{"container_id": "container-1", "status": "SUCCEEDED", "final_position": position}])

    with pytest.raises(TransportContractError, match="exceeds 100 characters"):
        validate_callback_envelope(_envelope(RESULT_OPERATION, data))
