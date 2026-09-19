from copy import deepcopy
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk

OPERATION = "workline.return_buffer.drain_rack_decide@v1"
OPERATION_ID = "019f3406-2200-7b03-8b01-000000000003"


def request(required_slot_count: int = 3):
    return {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "timestamp": 1,
        "data": {"workline_code": "LINE-1", "required_slot_count": required_slot_count},
    }


def response(result: str = "READY"):
    return {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {
            "result": "READY",
            "racks": [
                {"rack_id": "RACK-2", "rack_face": ["90", "270"]},
                {"rack_id": "RACK-3", "rack_face": ["90"]},
            ],
        }
        if result == "READY"
        else {"result": "WAIT", "reason_code": "NO_DRAIN_RACK_AVAILABLE", "retry_after_ms": 1000},
    }


def intent(**changes):
    values = {"operation_id": OPERATION_ID, "workline_code": "LINE-1", "required_slot_count": 3}
    return sdk.wms_operations.workline_return_buffer_drain_rack_decide(**(values | changes))


@pytest.mark.parametrize("result", ["READY", "WAIT"])
def test_strict_wire_and_immutable_typed_roundtrip(result):
    from src.app.wms_adapter.return_buffer_drain.typed import decode_outcome, encode_request
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request, parse_response

    typed = intent()
    assert encode_request(typed, timestamp=1) == request()
    parsed = parse_response(200, response(result), request=parse_request(request()))
    outcome = decode_outcome(parsed)
    assert type(outcome.result) is (sdk.ReturnBufferDrainReady if result == "READY" else sdk.ReturnBufferDrainWait)
    if result == "READY":
        assert outcome.result.racks == (
            sdk.RackFaceSequence("RACK-2", ("90", "270")),
            sdk.RackFaceSequence("RACK-3", ("90",)),
        )
        with pytest.raises(FrozenInstanceError):
            outcome.result.racks[0].rack_faces = ("A",)


@pytest.mark.parametrize(
    "changes", [{"required_slot_count": 0}, {"required_slot_count": True}, {"workline_code": "bad line"}]
)
def test_sdk_rejects_invalid_intent(changes):
    with pytest.raises((ValueError, TypeError)):
        intent(**changes)


@pytest.mark.parametrize("value", [0, 60001, True])
def test_sdk_wait_requires_bounded_integer(value):
    with pytest.raises(ValueError):
        sdk.ReturnBufferDrainWait(value)


def test_rack_face_sequence_is_nonempty_unique_and_immutable():
    assert sdk.RackFaceSequence("R1", ("90", "270")).rack_faces == ("90", "270")
    for value in ([], (), ("90", "90")):
        with pytest.raises((ValueError, TypeError)):
            sdk.RackFaceSequence("R1", value)


@pytest.mark.parametrize("path", [(), ("data",)])
def test_request_rejects_extra_fields(path):
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request

    body = request()
    target = body if not path else body["data"]
    target["unexpected"] = 1
    with pytest.raises(ValueError):
        parse_request(body)


@pytest.mark.parametrize("case", ["zero", "bool", "identity", "timestamp", "workline", "legacy"])
def test_request_rejects_invalid_values(case):
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request

    body = request()
    if case == "zero":
        body["data"]["required_slot_count"] = 0
    elif case == "bool":
        body["data"]["required_slot_count"] = True
    elif case == "identity":
        body["operation_id"] = "invalid"
    elif case == "timestamp":
        body["timestamp"] = -1
    elif case == "workline":
        body["data"]["workline_code"] = ""
    else:
        body["data"]["plugin_key"] = "manual-picking"
    with pytest.raises(ValueError):
        parse_request(body)


@pytest.mark.parametrize(
    "case", ["empty", "duplicate_rack", "empty_faces", "duplicate_face", "too_many_faces", "mixed", "legacy"]
)
def test_ready_response_rejects_invalid_plan(case):
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request, parse_response

    body = response()
    racks = body["data"]["racks"]
    if case == "empty":
        body["data"]["racks"] = []
    elif case == "duplicate_rack":
        racks[1]["rack_id"] = "RACK-2"
    elif case == "empty_faces":
        racks[0]["rack_face"] = []
    elif case == "duplicate_face":
        racks[0]["rack_face"] = ["90", "90"]
    elif case == "too_many_faces":
        racks[0]["rack_face"] = ["1", "2", "3", "4"]
    elif case == "mixed":
        body["data"]["retry_after_ms"] = 1
    else:
        body["data"] = {"result": "READY", "rack_id": "R1", "rack_face": "90"}
    with pytest.raises(ValueError):
        parse_response(200, body, request=parse_request(request()))


def test_wait_rejects_racks_and_invalid_retry():
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request, parse_response

    for data in (
        {"result": "WAIT", "reason_code": "NO_DRAIN_RACK_AVAILABLE", "retry_after_ms": 0},
        {"result": "WAIT", "reason_code": "NO_DRAIN_RACK_AVAILABLE", "retry_after_ms": 1000, "racks": []},
    ):
        with pytest.raises(ValueError):
            parse_response(
                200,
                {"operation_id": OPERATION_ID, "code": "DECIDED", "timestamp": 2, "data": data},
                request=parse_request(request()),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["READY", "WAIT", "digest", "identity", "request_drift", "response_drift"])
async def test_static_adapter_dispatch_is_bounded_and_fails_closed(case):
    from src.app.wms_adapter.client import WmsAccessResult
    from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
    from src.app.wms_adapter.dispatch import WmsDispatchCode
    from src.core.outbound_http import OutboundHttpDeliveryState
    from src.utils.canonical_json import canonical_json_digest

    body, answer = request(), response("WAIT" if case == "WAIT" else "READY")
    digest = canonical_json_digest(body)
    if case == "digest":
        digest = "0" * 64
    if case == "request_drift":
        body["data"]["required_slot_count"] = 2
    if case == "response_drift":
        answer["data"]["racks"] = []
    client = AsyncMock(
        post=AsyncMock(
            return_value=WmsAccessResult(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                status_code=200,
                response_headers=(("Content-Type", "application/json"),),
                body_present=True,
                json_body=answer,
                failure_kind=None,
                json_failure=None,
            )
        )
    )
    result = await WmsConfirmationAdapter(client).dispatch(
        operation=OPERATION,
        operation_id="019f3406-2200-7b03-8b01-000000000002" if case == "identity" else OPERATION_ID,
        request_payload=body,
        request_digest=digest,
    )
    assert result.code == (WmsDispatchCode.DETERMINATE if case in ("READY", "WAIT") else WmsDispatchCode.RECONCILING)
    assert client.post.await_count == (0 if case in ("digest", "identity", "request_drift") else 1)
