"""Transport 固定线上接口的闭集合同验收。"""

from __future__ import annotations

import pytest

from src.app.transport.contracts import (
    BinMove,
    HandoffPosition,
    MoveBinsRequest,
    RackBinSlot,
    TransportCaller,
    TransportContractError,
)
from src.app.wms_adapter.transport_wire import (
    POSITION_OPERATION,
    RESULT_OPERATION,
    build_submit_data,
    validate_callback_envelope,
)


def _envelope(operation: str, data: object) -> dict[str, object]:
    return {"request_id": "callback-1", "operation": operation, "timestamp": 1, "data": data}


def _position_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "event_id": "event-1",
        "transport_task_id": "transport-1",
        "bin_id": "bin-1",
        "milestone": "SOURCE_PICKED",
    }
    data.update(overrides)
    return data


def _result_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "event_id": "event-1",
        "transport_task_id": "transport-1",
        "kind": "BIN_MOVE",
        "results": [
            {
                "object_id": "bin-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }
    data.update(overrides)
    return data


def test_submit_data_removes_client_identity_and_preserves_frozen_members() -> None:
    request = MoveBinsRequest(
        "client-1",
        TransportCaller("SORTER", "STATION_A", "run-1"),
        (BinMove("bin-1", RackBinSlot("rack-1", "1"), HandoffPosition("ROLLER_IN")),),
    )

    data = build_submit_data(request, "transport-1")

    assert data == {
        "transport_task_id": "transport-1",
        "kind": "BIN_MOVE",
        "caller": {"workline_id": "SORTER", "station_id": "STATION_A", "correlation_id": "run-1"},
        "moves": [
            {
                "bin_id": "bin-1",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "slot_id": "1"},
                "target": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "callback envelope must be an object"),
        ({1: "value"}, "callback envelope must be an object"),
        (
            {"request_id": "callback-1", "operation": POSITION_OPERATION, "timestamp": 1},
            "callback envelope fields do not match the closed contract",
        ),
        (
            {"request_id": "callback-1", "operation": POSITION_OPERATION, "timestamp": True, "data": {}},
            "timestamp must be an integer",
        ),
        (_envelope("transport.task.unknown@v1", {}), "unsupported transport callback operation"),
    ],
)
def test_callback_envelope_rejects_non_closed_or_unsupported_values(value: object, message: str) -> None:
    with pytest.raises(TransportContractError, match=message):
        validate_callback_envelope(value)


@pytest.mark.parametrize("field", ["request_id", "event_id", "transport_task_id", "bin_id"])
def test_position_callback_rejects_blank_identifiers(field: str) -> None:
    envelope = _envelope(POSITION_OPERATION, _position_data())
    if field == "request_id":
        envelope[field] = " "
    else:
        assert isinstance(envelope["data"], dict)
        envelope["data"][field] = " "

    with pytest.raises(TransportContractError, match=f"{field} must not be blank"):
        validate_callback_envelope(envelope)


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
        final_position={"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "slot_id": "1"},
    )

    assert validate_callback_envelope(_envelope(POSITION_OPERATION, data))["data"] == data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_result_data(kind="UNKNOWN"), "invalid transport kind"),
        (_result_data(results=[]), "results must be a non-empty list"),
        (_result_data(results={}), "results must be a non-empty list"),
        (
            _result_data(
                results=[
                    {"object_id": "bin-1", "status": "FAILED", "position_unknown": True, "failure_code": "FAILED"},
                    {"object_id": "bin-1", "status": "FAILED", "position_unknown": True, "failure_code": "FAILED"},
                ]
            ),
            "duplicate result object_id",
        ),
        (
            _result_data(results=[{"object_id": "bin-1", "status": "UNKNOWN", "position_unknown": True}]),
            "invalid member result status",
        ),
        (
            _result_data(results=[{"object_id": "bin-1", "status": "FAILED", "failure_code": "FAILED"}]),
            "final_position xor position_unknown=true is required",
        ),
        (
            _result_data(
                results=[
                    {
                        "object_id": "bin-1",
                        "status": "FAILED",
                        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                        "position_unknown": False,
                        "failure_code": "FAILED",
                    }
                ]
            ),
            "position_unknown must be literal true",
        ),
        (
            _result_data(results=[{"object_id": "bin-1", "status": "SUCCEEDED", "position_unknown": True}]),
            "SUCCEEDED requires known position and no failure_code",
        ),
        (
            _result_data(
                results=[
                    {
                        "object_id": "bin-1",
                        "status": "SUCCEEDED",
                        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                        "failure_code": "IMPOSSIBLE",
                    }
                ]
            ),
            "SUCCEEDED requires known position and no failure_code",
        ),
        (
            _result_data(results=[{"object_id": "bin-1", "status": "FAILED", "position_unknown": True}]),
            "failure_code must not be blank",
        ),
        (
            _result_data(
                kind="RACK_MOVE",
                results=[
                    {
                        "object_id": "rack-1",
                        "status": "SUCCEEDED",
                        "final_position": {"kind": "RACK_POSITION", "location_code": "B"},
                    }
                ],
            ),
            "known rack result requires arrival_face",
        ),
        (
            _result_data(
                results=[
                    {
                        "object_id": "bin-1",
                        "status": "SUCCEEDED",
                        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                        "arrival_face": "A",
                    }
                ]
            ),
            "arrival_face is not valid for this result",
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
        ({"kind": "RACK_BIN_SLOT", "rack_id": " ", "slot_id": "1"}, "rack_id must not be blank"),
        ({"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "slot_id": " "}, "slot_id must not be blank"),
        ({"kind": "HANDOFF_POSITION", "location_code": " "}, "location_code must not be blank"),
        ({"kind": "UNKNOWN"}, "invalid position kind"),
    ],
)
def test_result_callback_rejects_invalid_position_shapes(position: object, message: str) -> None:
    data = _result_data(results=[{"object_id": "bin-1", "status": "SUCCEEDED", "final_position": position}])

    with pytest.raises(TransportContractError, match=message):
        validate_callback_envelope(_envelope(RESULT_OPERATION, data))


def test_result_callback_accepts_known_rack_and_unknown_failed_member() -> None:
    known = _result_data(
        kind="RACK_ROTATE",
        results=[
            {
                "object_id": "rack-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "RACK_POSITION", "location_code": "ROTATE"},
                "arrival_face": "B",
            }
        ],
    )
    unknown = _result_data(
        results=[
            {
                "object_id": "bin-1",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            }
        ]
    )

    assert validate_callback_envelope(_envelope(RESULT_OPERATION, known))["data"] == known
    assert validate_callback_envelope(_envelope(RESULT_OPERATION, unknown))["data"] == unknown
