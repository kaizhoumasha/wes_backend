"""货架离场决定的严格边界与共享可靠派发接入。"""

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import wms_operations

from src.app.wms_adapter.client import WmsAccessResult
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.core.outbound_http import OutboundHttpDeliveryState
from src.utils.canonical_json import canonical_json_digest

OPERATION = "outbound.rack.departure_decide@v1"
OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"


def request():
    return {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "timestamp": 0,
        "data": {
            "task_id": "TASK-1",
            "rack_id": "RACK-1",
            "current_location": {"type": "RACK_POSITION", "location_code": "WORK-1"},
            "current_face": "面 A",
        },
    }


def response(data, code="DECIDED"):
    return {"operation_id": OPERATION_ID, "code": code, "timestamp": 0, "data": data}


def ready(location="STORE-1"):
    return {"result": "READY", "rack_destination": {"type": "RACK_POSITION", "location_code": location}}


def client_response(body, status=200):
    return AsyncMock(
        post=AsyncMock(
            return_value=WmsAccessResult(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                status_code=status,
                response_headers=(("Content-Type", "application/json"),),
                body_present=True,
                json_body=body,
                failure_kind=None,
                json_failure=None,
            )
        )
    )


async def dispatch(client, **overrides):
    args = {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "request_payload": request(),
        "request_digest": canonical_json_digest(request()),
    }
    return await WmsConfirmationAdapter(client).dispatch(**(args | overrides))


def test_typed_request_reuses_physical_position_and_preserves_face():
    from src.app.wms_adapter.outbound_picking.departure_typed import encode_request

    position = sdk.TransportRackPosition("WORK-1")
    intent = wms_operations.outbound_rack_departure_decide(
        operation_id=OPERATION_ID, task_id="TASK-1", rack_id="RACK-1", current_location=position, current_face="面 A"
    )
    assert intent.current_location is position
    assert encode_request(intent, timestamp=0) == request()
    with pytest.raises(FrozenInstanceError):
        intent.current_face = "B"


@pytest.mark.parametrize(
    "override",
    [
        {"task_id": "bad id"},
        {"rack_id": ""},
        {"current_face": "x" * 11},
        {"current_location": {"type": "RACK_POSITION", "location_code": "WORK-1"}},
        {"current_location": sdk.TransportRackPosition("bad position")},
    ],
)
def test_sdk_rejects_invalid_departure_intent(override):
    args = {
        "operation_id": OPERATION_ID,
        "task_id": "TASK-1",
        "rack_id": "RACK-1",
        "current_location": sdk.TransportRackPosition("WORK-1"),
        "current_face": "A",
    }
    with pytest.raises((ValueError, TypeError)):
        wms_operations.outbound_rack_departure_decide(**(args | override))


def test_sdk_departure_result_is_closed_and_deeply_immutable():
    for destination in ({"location_code": "STORE-1"}, sdk.TransportRackPosition("bad position")):
        with pytest.raises((TypeError, ValueError)):
            sdk.RackDepartureReady(destination)
    for delay in (0, True, 60001):
        with pytest.raises(ValueError):
            sdk.RackDepartureWait(delay)
    for result in ("READY", sdk.BinWorkPlanNoWork(), sdk.OperationConflict("POSITION_CONFLICT")):
        with pytest.raises((TypeError, ValueError)):
            sdk.RackDepartureOutcome(result)
    result = sdk.RackDepartureReady(sdk.TransportRackPosition("STORE-1"))
    with pytest.raises(FrozenInstanceError):
        result.rack_destination.location_code = "OTHER"


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "bad id"),
        ("rack_id", ""),
        ("current_face", "x" * 11),
        ("current_face", ""),
        ("current_face", "A\0"),
        ("current_face", 1),
        ("current_location", {"type": "RACK_POSITION", "location_code": "bad position"}),
        ("current_location", {"type": "HANDOFF_POSITION", "location_code": "WORK-1"}),
    ],
)
def test_request_rejects_unapproved_role_face_and_position(field, value):
    from src.app.wms_adapter.outbound_picking.departure_wire import parse_rack_departure_request

    body = request()
    body["data"][field] = value
    with pytest.raises(ValueError):
        parse_rack_departure_request(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [ready(), {"result": "WAIT", "retry_after_ms": 60000}])
async def test_decisions_end_current_obligation_without_transport_or_automatic_retry(data):
    from src.app.wms_adapter.outbound_picking.departure_typed import decode_outcome

    body = response(data)
    client = client_response(body)
    result = await dispatch(client)
    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == data["result"]
    assert result.normalized_response == body
    assert result.retry_after_ms is None
    outcome = decode_outcome(result.normalized_response)
    if data["result"] == "READY":
        assert outcome.result.rack_destination == sdk.TransportRackPosition("STORE-1")
    else:
        assert outcome.result.retry_after_ms == 60000
    client.post.assert_awaited_once_with(
        "/api/v1/wes/decisions",
        json=request(),
        max_request_body_bytes=256 * 1024,
        max_response_body_bytes=256 * 1024,
        observation=None,
    )


@pytest.mark.asyncio
async def test_destination_equal_to_current_position_is_reconciling():
    body = response(ready("WORK-1"))
    result = await dispatch(client_response(body))
    assert result.code is WmsDispatchCode.RECONCILING
    assert result.normalized_response == body


@pytest.mark.parametrize(
    "data",
    [
        {"result": "READY"},
        {"result": "READY", "rack_destination": {"type": "RACK", "location_code": "STORE-1"}},
        {"result": "WAIT"},
        {"result": "WAIT", "retry_after_ms": 0},
        {"result": "WAIT", "retry_after_ms": True},
        {"result": "WAIT", "retry_after_ms": 60001},
        {"result": "NO_WORK"},
    ],
)
def test_response_union_is_closed(data):
    from src.app.wms_adapter.outbound_picking.departure_typed import decode_outcome

    with pytest.raises(ValueError):
        decode_outcome(response(data))


@pytest.mark.asyncio
async def test_invalid_frozen_digest_is_rejected_before_http():
    client = AsyncMock()
    assert (await dispatch(client, request_digest="changed")).code is WmsDispatchCode.RECONCILING
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_response_identity_is_not_accepted():
    body = response(ready()) | {"operation_id": "019f3405-2200-7b01-8b01-000000000002"}
    assert (await dispatch(client_response(body))).code is WmsDispatchCode.RECONCILING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,data,expected",
    [
        (503, "UNAVAILABLE", {}, WmsDispatchCode.RETRY),
        (409, "CONFLICT", {"reason_code": "STATE_CONFLICT"}, WmsDispatchCode.RECONCILING),
        (
            422,
            "REJECTED",
            {"reason_code": "INVALID_DATA", "field_path": "/data/current_face"},
            WmsDispatchCode.RECONCILING,
        ),
        (200, "DUPLICATE", {}, WmsDispatchCode.RECONCILING),
    ],
)
async def test_shared_error_mapping(status, code, data, expected):
    assert (await dispatch(client_response(response(data, code), status))).code is expected


@pytest.mark.parametrize(
    "code,data,expected",
    [
        ("UNAVAILABLE", {}, sdk.OperationUnavailable),
        ("CONFLICT", {"reason_code": "REFERENCE_CONFLICT"}, sdk.OperationConflict),
        ("REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data/current_face"}, sdk.OperationRejected),
    ],
)
def test_persisted_errors_preserve_shared_typed_results(code, data, expected):
    from src.app.wms_adapter.outbound_picking.departure_typed import decode_outcome

    result = decode_outcome(response(data, code)).result
    assert type(result) is expected
    if code == "REJECTED":
        assert result.field_path == "/data/current_face"


@pytest.mark.parametrize(
    "data",
    [
        ready() | {"retry_after_ms": 1},
        {"result": "WAIT", "retry_after_ms": 1, "rack_destination": None},
    ],
)
def test_redundant_fields_accepted_response_union_is_closed(data):
    from src.app.wms_adapter.outbound_picking.departure_wire import parse_rack_departure_response

    parsed = parse_rack_departure_response(200, response(data))
    assert parsed.data.result == data["result"]
