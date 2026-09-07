from __future__ import annotations

from copy import deepcopy

import pytest

from src.app.wms_adapter.outbound_picking.inbound_batch_wire import (
    parse_bin_inbound_batch_request,
    parse_bin_inbound_batch_response,
)

OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"


def request():
    return {
        "operation_id": OPERATION_ID,
        "operation": "outbound.bin.inbound_batch@v1",
        "timestamp": 0,
        "data": {"task_id": "PICK-1", "rack_id": "RACK-1", "rack_face": "正面", "max_bin_count": 2},
    }


def bin_item(bin_code="BIN-1", slot_id="S-1"):
    return {
        "bin_code": bin_code,
        "source_locator": {"type": "RACK_BIN_SLOT", "rack_id": "RACK-1", "rack_face": "正面", "slot_id": slot_id},
    }


def response(data):
    return {"operation_id": OPERATION_ID, "code": "DECIDED", "timestamp": 0, "data": data}


@pytest.mark.parametrize(
    "data",
    [
        {"result": "READY", "bins": [bin_item()]},
        {"result": "NO_BATCH", "retry_after_ms": 1},
        {"result": "NO_BATCH", "retry_after_ms": 60000},
        {"result": "RACK_FACE_DONE"},
    ],
)
def test_closed_decisions(data):
    parsed = parse_bin_inbound_batch_response(200, response(data), request=parse_bin_inbound_batch_request(request()))
    assert parsed.model_dump(mode="json") == response(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_bin_count", 0),
        ("max_bin_count", 5),
        ("max_bin_count", True),
        ("max_bin_count", "2"),
        ("rack_face", "a" * 11),
        ("rack_face", ""),
        ("rack_face", "a\x00"),
        ("rack_id", "退料架 1"),
        ("task_id", ""),
        ("extra", 1),
    ],
)
def test_invalid_request_fields(field, value):
    body = request()
    body["data"][field] = value
    with pytest.raises(ValueError):
        parse_bin_inbound_batch_request(body)


@pytest.mark.parametrize(
    "data",
    [
        {"result": "READY", "bins": []},
        {"result": "READY", "bins": [bin_item(), bin_item()]},
        {"result": "READY", "bins": [bin_item(), bin_item("BIN-2")]},
        {"result": "READY", "bins": [bin_item()], "retry_after_ms": 1},
        {"result": "NO_BATCH", "retry_after_ms": 0},
        {"result": "NO_BATCH", "retry_after_ms": 60001},
        {"result": "NO_BATCH", "retry_after_ms": True},
        {"result": "NO_BATCH", "bins": [], "retry_after_ms": 1},
        {"result": "RACK_FACE_DONE", "bins": None},
        {"result": "WAIT", "retry_after_ms": 1},
    ],
)
def test_invalid_decisions(data):
    with pytest.raises(ValueError):
        parse_bin_inbound_batch_response(200, response(data))


def test_ready_matches_requested_capacity_and_exact_face():
    body = response({"result": "READY", "bins": [bin_item(), bin_item("BIN-2", "S-2")]})
    req = request()
    req["data"]["max_bin_count"] = 1
    with pytest.raises(ValueError):
        parse_bin_inbound_batch_response(200, body, request=parse_bin_inbound_batch_request(req))
    for field in ("rack_id", "rack_face"):
        changed = deepcopy(body)
        changed["data"]["bins"][0]["source_locator"][field] = "other"
        with pytest.raises(ValueError):
            parse_bin_inbound_batch_response(200, changed, request=parse_bin_inbound_batch_request(request()))


@pytest.mark.parametrize(
    "status,code,data",
    [
        (503, "UNAVAILABLE", {}),
        (409, "CONFLICT", {"reason_code": "REFERENCE_CONFLICT"}),
        (422, "REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data/rack_id"}),
    ],
)
def test_errors_and_status_pairing(status, code, data):
    body = {"operation_id": OPERATION_ID, "code": code, "timestamp": 0, "data": data}
    assert parse_bin_inbound_batch_response(status, body).model_dump(exclude_unset=True) == body
    with pytest.raises(ValueError):
        parse_bin_inbound_batch_response(200, body)


@pytest.mark.parametrize("value", [-1, 2**63, True, "1", None])
def test_timestamp_rejects_non_integer_or_out_of_range(value):
    body = request()
    body["timestamp"] = value
    with pytest.raises(ValueError):
        parse_bin_inbound_batch_request(body)
    body = response({"result": "RACK_FACE_DONE"})
    body["timestamp"] = value
    with pytest.raises(ValueError):
        parse_bin_inbound_batch_response(200, body)


@pytest.mark.parametrize(
    "data",
    [
        {"reason_code": "INVALID_DATA", "field_path": None},
        {"reason_code": "INVALID_DATA", "field_path": "/bad~2"},
        {"reason_code": "UNSUPPORTED_OPERATION", "field_path": "/data"},
        {"reason_code": "UNRECOGNIZED"},
    ],
)
def test_rejection_data_is_closed(data):
    body = {"operation_id": OPERATION_ID, "code": "REJECTED", "timestamp": 0, "data": data}
    with pytest.raises(ValueError):
        parse_bin_inbound_batch_response(422, body)
