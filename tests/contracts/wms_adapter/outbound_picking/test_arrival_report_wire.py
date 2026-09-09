from __future__ import annotations

import pytest

from src.app.wms_adapter.outbound_picking.arrival_report_wire import (
    RETURN_RACK_ARRIVAL_REPORT_OPERATION,
    parse_return_rack_arrival_report_request,
    parse_return_rack_arrival_report_response,
)

OPERATION_ID = "019f3404-a100-7b01-8b01-000000000001"


def request():
    return {
        "operation_id": OPERATION_ID,
        "operation": RETURN_RACK_ARRIVAL_REPORT_OPERATION,
        "timestamp": 0,
        "data": {
            "task_id": "PICK-001",
            "transport_task_id": "transport-001",
            "outcome_revision": 1,
            "rack_id": "RETURN-01",
            "final_position": {"type": "RACK_POSITION", "location_code": "WORK-01"},
            "arrival_face": " 面向 A ",
        },
    }


def test_request_preserves_face_and_bounded_integer_values():
    payload = request()
    payload["data"]["outcome_revision"] = 2**63 - 1
    payload["data"]["transport_task_id"] = "T" * 80
    assert parse_return_rack_arrival_report_request(payload).model_dump(mode="json") == payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "bad id"),
        ("rack_id", ""),
        ("transport_task_id", "T" * 81),
        ("transport_task_id", "bad id"),
        ("outcome_revision", True),
        ("outcome_revision", 0),
        ("outcome_revision", 2**63),
        ("outcome_revision", "1"),
        ("outcome_revision", 1.0),
        ("arrival_face", ""),
        ("arrival_face", "😀" * 11),
        ("arrival_face", "A\0"),
        ("arrival_face", 1),
        ("arrival_face", None),
        ("final_position", {"type": "HANDOFF_POSITION", "location_code": "WORK-01"}),
        ("final_position", {"type": "RACK_POSITION", "rack_id": "R"}),
    ],
)
def test_request_rejects_invalid_fields(field, value):
    payload = request()
    payload["data"][field] = value
    with pytest.raises(ValueError):
        parse_return_rack_arrival_report_request(payload)


@pytest.mark.parametrize(
    "field", ["task_id", "transport_task_id", "outcome_revision", "rack_id", "final_position", "arrival_face"]
)
def test_all_business_fields_are_required(field):
    payload = request()
    del payload["data"][field]
    with pytest.raises(ValueError):
        parse_return_rack_arrival_report_request(payload)


@pytest.mark.parametrize(
    "status,code,data",
    [
        (200, "RECORDED", {}),
        (200, "DUPLICATE", {}),
        (503, "UNAVAILABLE", {}),
        (409, "CONFLICT", {"reason_code": "REFERENCE_CONFLICT"}),
        (422, "REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data/a~1b~0c"}),
    ],
)
def test_closed_responses(status, code, data):
    payload = {"operation_id": OPERATION_ID, "code": code, "timestamp": 0, "data": data}
    assert (
        parse_return_rack_arrival_report_response(status, payload).model_dump(mode="json", exclude_unset=True)
        == payload
    )


@pytest.mark.parametrize(
    "status,code,data",
    [
        (202, "RECORDED", {}),
        (200, "RECEIVED", {}),
        (429, "BUSY", {}),
        (200, "RECORDED", None),
        (503, "UNAVAILABLE", None),
        (409, "CONFLICT", {"reason_code": "OTHER"}),
        (422, "REJECTED", {"reason_code": "INVALID_DATA", "field_path": None}),
        (422, "REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/bad~2"}),
        (422, "REJECTED", {"reason_code": "INVALID_ENVELOPE", "field_path": "/data"}),
    ],
)
def test_rejects_response_drift(status, code, data):
    with pytest.raises(ValueError):
        parse_return_rack_arrival_report_response(
            status,
            {
                "operation_id": OPERATION_ID,
                "code": code,
                "timestamp": 1,
                "data": data,
            },
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("timestamp", -1),
        ("timestamp", 2**63),
        ("timestamp", True),
        ("operation_id", "019f3404-a100-4b01-8b01-000000000001"),
        ("operation", "outbound.return_rack.other@v1"),
        ("data", None),
    ],
)
def test_request_rejects_invalid_envelope(field, value):
    payload = request()
    payload[field] = value
    with pytest.raises(ValueError):
        parse_return_rack_arrival_report_request(payload)
