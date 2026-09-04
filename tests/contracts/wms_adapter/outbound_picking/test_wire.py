from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.app.wms_adapter.outbound_picking.openapi import PICKING_TASK_ISSUED_EVENT_REQUEST_SCHEMA
from src.app.wms_adapter.outbound_picking.wire import (
    PICKING_TASK_ISSUED_OPERATION,
    PickingTaskIssuedEvent,
    parse_picking_task_issued_event,
)


def _valid_event() -> dict[str, object]:
    return {
        "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
        "operation": PICKING_TASK_ISSUED_OPERATION,
        "timestamp": 1786060800000,
        "data": {
            "task_id": "PICK-20260811-001",
            "task_type": "MANUAL",
            "queue_revision": 1,
            "dispatch_sequence": 100,
            "not_before": 1786060800000,
        },
    }


def test_picking_task_issued_parser_accepts_the_approved_closed_wire() -> None:
    event = parse_picking_task_issued_event(_valid_event())

    assert isinstance(event, PickingTaskIssuedEvent)
    assert event.operation == PICKING_TASK_ISSUED_OPERATION
    assert event.data.task_id == "PICK-20260811-001"
    assert event.data.task_type == "MANUAL"
    assert event.data.queue_revision == 1
    assert event.data.dispatch_sequence == 100
    assert event.data.not_before == 1786060800000


def test_picking_task_issued_parser_accepts_immediately_eligible_not_before_zero() -> None:
    payload = _valid_event()
    data = payload["data"]
    assert isinstance(data, dict)
    data["not_before"] = 0

    event = parse_picking_task_issued_event(payload)

    assert event.data.not_before == 0


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("data", "queue_revision"), 2),
        (("data", "task_type"), "UNKNOWN"),
        (("data", "queue_revision"), True),
        (("data", "dispatch_sequence"), 0),
        (("data", "not_before"), None),
        (("data", "task_id"), " task-1"),
        (("timestamp",), 0),
    ],
)
def test_picking_task_issued_parser_rejects_values_outside_the_approved_contract(
    path: tuple[str, ...], value: object
) -> None:
    payload = deepcopy(_valid_event())
    target = payload
    for segment in path[:-1]:
        target = target[segment]  # type: ignore[assignment,index]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        parse_picking_task_issued_event(payload)


@pytest.mark.parametrize("field", ["workline_code", "transport_task_id", "bin_id"])
def test_picking_task_issued_parser_rejects_unapproved_business_fields(field: str) -> None:
    payload = deepcopy(_valid_event())
    data = payload["data"]
    assert isinstance(data, dict)
    data[field] = "not-allowed"

    with pytest.raises(ValidationError):
        parse_picking_task_issued_event(payload)


def test_picking_task_issued_openapi_schema_is_closed_and_exact() -> None:
    schema = PICKING_TASK_ISSUED_EVENT_REQUEST_SCHEMA

    assert schema["additionalProperties"] is False
    assert schema["properties"]["operation"]["enum"] == [PICKING_TASK_ISSUED_OPERATION]
    data_schema = schema["properties"]["data"]
    assert data_schema["additionalProperties"] is False
    assert data_schema["required"] == ["task_id", "task_type", "queue_revision", "dispatch_sequence"]
    assert data_schema["properties"]["task_type"] == {"type": "string", "enum": ["MANUAL", "AUTO"]}
    assert data_schema["properties"]["queue_revision"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 1,
    }
    assert data_schema["properties"]["not_before"]["minimum"] == 0
