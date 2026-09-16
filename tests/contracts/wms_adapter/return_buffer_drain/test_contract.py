from copy import deepcopy
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk

OPERATION = "workline.return_buffer.drain_rack_decide@v1"
OPERATION_ID = "019f3406-2200-7b03-8b01-000000000003"
PREVIOUS_ID = "019f3406-2200-7b03-8b01-000000000002"


def request():
    return {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "timestamp": 1,
        "data": {
            "workline_code": "LINE-1",
            "plugin_key": "manual-picking",
            "drain_reason": "PICKING_TASK_COMPLETED",
            "return_candidates": [
                {
                    "sequence_no": n,
                    "bin_code": f"BIN-{n}",
                    "source": {"type": "HANDOFF_POSITION", "location_code": "RETURN-1"},
                }
                for n in (1, 2)
            ],
        },
    }


def response(result="READY"):
    return {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "READY", "rack_id": "RACK-2", "rack_face": "B"}
        if result == "READY"
        else {"result": "WAIT", "reason_code": "NO_DRAIN_RACK_AVAILABLE", "retry_after_ms": 1000},
    }


def intent(**changes):
    values = {
        "operation_id": OPERATION_ID,
        "workline_code": "LINE-1",
        "plugin_key": "manual-picking",
        "drain_reason": "PICKING_TASK_COMPLETED",
        "return_candidates": tuple(sdk.BinReturnCandidate(n, f"BIN-{n}", "RETURN-1") for n in (1, 2)),
    }
    return sdk.wms_operations.workline_return_buffer_drain_rack_decide(**(values | changes))


@pytest.mark.parametrize("result", ["READY", "WAIT"])
def test_strict_wire_and_immutable_typed_roundtrip(result):
    from src.app.wms_adapter.return_buffer_drain.typed import decode_outcome, encode_request
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request, parse_response

    typed = intent()
    assert encode_request(typed, timestamp=1) == request()
    assert parse_response(200, response(result), request=parse_request(request())).data.result == result
    outcome = decode_outcome(parse_response(200, response(result), request=parse_request(request())))
    assert type(outcome.result) is (sdk.ReturnBufferDrainReady if result == "READY" else sdk.ReturnBufferDrainWait)
    with pytest.raises(FrozenInstanceError):
        typed.return_candidates[0].bin_code = "changed"
    with pytest.raises(FrozenInstanceError):
        outcome.result.rack_face = "changed"


def test_encode_request_rejects_wrong_intent_type():
    from src.app.wms_adapter.return_buffer_drain.typed import encode_request

    with pytest.raises(TypeError):
        encode_request(object(), timestamp=1)


@pytest.mark.parametrize(
    "code,status,data,expected",
    [
        ("UNAVAILABLE", 503, {}, "OperationUnavailable"),
        ("CONFLICT", 409, {"reason_code": "STATE_CONFLICT"}, "OperationConflict"),
        ("REJECTED", 422, {"reason_code": "INVALID_DATA", "field_path": "/data"}, "OperationRejected"),
    ],
)
def test_decode_outcome_maps_common_errors(code, status, data, expected):
    from src.app.wms_adapter.return_buffer_drain.typed import decode_outcome
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request, parse_response

    body = response()
    body.update(code=code, data=data)
    parsed = parse_response(status, body, request=parse_request(request()))
    outcome = decode_outcome(parsed)
    assert type(outcome.result).__name__ == expected


@pytest.mark.parametrize("reason", ["PICKING_TASK_COMPLETED", "WORKLINE_STOPPING", "PLUGIN_SWITCHING"])
def test_reason_and_direct_predecessor(reason):
    from src.app.wms_adapter.return_buffer_drain.typed import encode_request

    body = encode_request(intent(drain_reason=reason, previous_operation_id=PREVIOUS_ID), timestamp=1)
    assert body["data"]["previous_operation_id"] == PREVIOUS_ID
    assert body["data"]["drain_reason"] == reason


@pytest.mark.parametrize("case", ["mutable", "gap", "duplicate", "reason", "owner", "self_previous"])
def test_sdk_rejects_invalid_intent(case):
    changes = {
        "mutable": {"return_candidates": [sdk.BinReturnCandidate(1, "BIN-1", "RETURN-1")]},
        "gap": {"return_candidates": (sdk.BinReturnCandidate(2, "BIN-2", "RETURN-1"),)},
        "duplicate": {
            "return_candidates": (
                sdk.BinReturnCandidate(1, "BIN-1", "RETURN-1"),
                sdk.BinReturnCandidate(2, "BIN-1", "RETURN-1"),
            )
        },
        "reason": {"drain_reason": "OTHER"},
        "owner": {"plugin_key": "invalid plugin"},
        "self_previous": {"previous_operation_id": OPERATION_ID},
    }[case]
    with pytest.raises((ValueError, TypeError)):
        intent(**changes)


@pytest.mark.parametrize("value", [0, 60001, True])
def test_sdk_wait_requires_bounded_integer(value):
    with pytest.raises(ValueError):
        sdk.ReturnBufferDrainWait(value)


@pytest.mark.parametrize(
    "path", [(), ("data",), ("data", "return_candidates", 0), ("data", "return_candidates", 0, "source")]
)
def test_request_rejects_extra_fields_at_every_depth(path):
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request

    body = request()
    target = body
    for part in path:
        target = target[part]
    target["unexpected"] = "value"
    with pytest.raises(ValueError):
        parse_request(body)


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "gap",
        "duplicate",
        "many",
        "source",
        "bool",
        "reason",
        "null",
        "previous_id",
        "self_previous",
        "identity",
        "timestamp",
        "plugin",
        "workline",
    ],
)
def test_request_rejects_invalid_values(case):
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request

    body = request()
    data = body["data"]
    candidates = data["return_candidates"]
    if case == "empty":
        data["return_candidates"] = []
    elif case == "gap":
        candidates[1]["sequence_no"] = 3
    elif case == "duplicate":
        candidates[1]["bin_code"] = "BIN-1"
    elif case == "many":
        candidates.extend(deepcopy(candidates) * 2)
    elif case == "source":
        candidates[0]["source"]["type"] = "RACK_POSITION"
    elif case == "bool":
        candidates[0]["sequence_no"] = True
    elif case == "reason":
        data["drain_reason"] = "OTHER"
    elif case == "null":
        data["previous_operation_id"] = None
    elif case == "previous_id":
        data["previous_operation_id"] = "invalid"
    elif case == "self_previous":
        data["previous_operation_id"] = OPERATION_ID
    elif case == "identity":
        body["operation_id"] = "invalid"
    elif case == "timestamp":
        body["timestamp"] = -1
    elif case == "plugin":
        data["plugin_key"] = "bad plugin"
    else:
        data["workline_code"] = ""
    with pytest.raises(ValueError):
        parse_request(body)


@pytest.mark.parametrize("result", ["READY", "WAIT"])
@pytest.mark.parametrize("case", ["extra", "top_extra", "missing", "null", "mixed", "identity", "invalid"])
def test_response_rejects_malformed_union(result, case):
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request, parse_response

    body = response(result)
    data = body["data"]
    required = "rack_face" if result == "READY" else "retry_after_ms"
    if case == "extra":
        data["unexpected"] = 1
    elif case == "top_extra":
        body["unexpected"] = 1
    elif case == "missing":
        del data[required]
    elif case == "null":
        data[required] = None
    elif case == "mixed":
        data["retry_after_ms" if result == "READY" else "rack_id"] = 1
    elif case == "identity":
        body["operation_id"] = PREVIOUS_ID
    else:
        data[required] = "\x00" if result == "READY" else 60001
    with pytest.raises(ValueError):
        parse_response(200, body, request=parse_request(request()))


@pytest.mark.parametrize(
    "code,status,data",
    [
        ("UNAVAILABLE", 503, {}),
        ("CONFLICT", 409, {"reason_code": "STATE_CONFLICT"}),
        ("REJECTED", 422, {"reason_code": "INVALID_DATA", "field_path": "/data"}),
    ],
)
def test_common_errors_are_closed(code, status, data):
    from src.app.wms_adapter.return_buffer_drain.wire import parse_request, parse_response

    body = response()
    body.update(code=code, data=data)
    assert parse_response(status, body, request=parse_request(request())).code == code
    for target in ((), ("data",)):
        invalid = deepcopy(body)
        (invalid if not target else invalid["data"])["extra"] = 1
        with pytest.raises(ValueError):
            parse_response(status, invalid)
    with pytest.raises(ValueError):
        parse_response(200, body)


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
        body["data"]["plugin_key"] = "changed"
    if case == "response_drift":
        answer["data"]["rack_face"] = None
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
        operation_id=PREVIOUS_ID if case == "identity" else OPERATION_ID,
        request_payload=body,
        request_digest=digest,
    )
    assert result.code == (WmsDispatchCode.DETERMINATE if case in ("READY", "WAIT") else WmsDispatchCode.RECONCILING)
    assert client.post.await_count == (0 if case in ("digest", "identity", "request_drift") else 1)
    if case in ("READY", "WAIT"):
        assert result.response_result == case
        assert result.retry_after_ms is None
