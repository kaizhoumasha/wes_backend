from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.app.wms_adapter.outbound_picking.wire import (
    PICKING_TASK_PREPARE_OPERATION,
    PickingTaskPrepareRequest,
    parse_picking_task_prepare_request,
    parse_picking_task_prepare_response,
)


def _request() -> dict[str, object]:
    return {
        "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804da",
        "operation": PICKING_TASK_PREPARE_OPERATION,
        "timestamp": 1786060810000,
        "data": {"task_id": "PICK-20260811-001", "workline_code": "SORTING-LINE-01"},
    }


def test_prepare_request_parser_accepts_the_approved_closed_wire() -> None:
    request = parse_picking_task_prepare_request(_request())

    assert isinstance(request, PickingTaskPrepareRequest)
    assert request.data.task_id == "PICK-20260811-001"
    assert request.data.workline_code == "SORTING-LINE-01"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("operation_id",), "019f3400-0e17-6d2a-b944-3cf7953804da"),
        (("operation",), "outbound.picking_task.prepare"),
        (("timestamp",), 0),
        (("data", "task_id"), " PICK-1"),
        (("data", "workline_code"), ""),
    ],
)
def test_prepare_request_parser_rejects_values_outside_the_approved_contract(
    path: tuple[str, ...], value: object
) -> None:
    payload = deepcopy(_request())
    target = payload
    for segment in path[:-1]:
        target = target[segment]  # type: ignore[assignment,index]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        parse_picking_task_prepare_request(payload)


def test_prepare_request_parser_rejects_unknown_fields() -> None:
    payload = _request()
    data = payload["data"]
    assert isinstance(data, dict)
    data["station_code"] = "STATION-1"

    with pytest.raises(ValidationError):
        parse_picking_task_prepare_request(payload)


@pytest.mark.parametrize(
    ("status", "body", "expected_code"),
    [
        (
            202,
            {"operation_id": _request()["operation_id"], "code": "PREPARE_ACCEPTED", "timestamp": 2, "data": {}},
            "PREPARE_ACCEPTED",
        ),
        (
            503,
            {"operation_id": _request()["operation_id"], "code": "UNAVAILABLE", "timestamp": 2, "data": {}},
            "UNAVAILABLE",
        ),
        (
            409,
            {
                "operation_id": _request()["operation_id"],
                "code": "CONFLICT",
                "timestamp": 2,
                "data": {"reason_code": "STATE_CONFLICT"},
            },
            "CONFLICT",
        ),
        (
            422,
            {
                "operation_id": _request()["operation_id"],
                "code": "REJECTED",
                "timestamp": 2,
                "data": {"reason_code": "INVALID_DATA", "field_path": "/data/task_id"},
            },
            "REJECTED",
        ),
    ],
)
def test_prepare_response_parser_accepts_only_the_approved_http_code_pairs(
    status: int, body: dict[str, object], expected_code: str
) -> None:
    assert parse_picking_task_prepare_response(status, body).code == expected_code


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, {"operation_id": _request()["operation_id"], "code": "PREPARE_ACCEPTED", "timestamp": 2, "data": {}}),
        (202, {"operation_id": _request()["operation_id"], "code": "UNAVAILABLE", "timestamp": 2, "data": {}}),
        (
            202,
            {
                "operation_id": _request()["operation_id"],
                "code": "PREPARE_ACCEPTED",
                "timestamp": 2,
                "data": {"extra": True},
            },
        ),
        (
            409,
            {
                "operation_id": _request()["operation_id"],
                "code": "CONFLICT",
                "timestamp": 2,
                "data": {"reason_code": "UNKNOWN"},
            },
        ),
        (
            422,
            {
                "operation_id": _request()["operation_id"],
                "code": "REJECTED",
                "timestamp": 2,
                "data": {"reason_code": "INVALID_ENVELOPE", "field_path": "/data/task_id"},
            },
        ),
    ],
)
def test_prepare_response_parser_rejects_unapproved_response_shapes(status: int, body: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        parse_picking_task_prepare_response(status, body)
