"""确定空取的封闭决定，不把业务 RETRY 当作技术重试。"""

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

OPERATION = "outbound.source.empty_decide@v1"
OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"


def request(source="BIN_CELL"):
    locator = {"type": source, "rack_id": "RACK-1", "rack_face": "面 A"}
    locator.update({"bin_code": "BIN-1", "cell_id": "CELL-1"} if source == "BIN_CELL" else {"slot_id": "SLOT-1"})
    return {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "timestamp": 0,
        "data": {"task_id": "TASK-1", "source_locator": locator, "observed_at": 123},
    }


def response(data, code="DECIDED"):
    return {"operation_id": OPERATION_ID, "code": code, "timestamp": 0, "data": data}


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


async def dispatch(client, payload=None, **overrides):
    payload = request() if payload is None else payload
    args = {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "request_payload": payload,
        "request_digest": canonical_json_digest(payload),
    }
    return await WmsConfirmationAdapter(client).dispatch(**(args | overrides))


@pytest.mark.parametrize("source", ["BIN_CELL", "RACK_SLOT"])
def test_typed_request_reuses_source_and_preserves_observed_time(source):
    from src.app.wms_adapter.outbound_picking.source_empty_typed import encode_request

    locator = (
        sdk.PickingBinCell("RACK-1", "面 A", "BIN-1", "CELL-1")
        if source == "BIN_CELL"
        else sdk.PickingRackSlot("RACK-1", "面 A", "SLOT-1")
    )
    intent = wms_operations.outbound_source_empty_decide(
        operation_id=OPERATION_ID, task_id="TASK-1", source_locator=locator, observed_at=123
    )
    assert intent.source_locator is locator
    assert encode_request(intent, timestamp=0) == request(source)
    with pytest.raises(FrozenInstanceError):
        intent.observed_at = 124


@pytest.mark.parametrize("source", ["BIN_CELL", "RACK_SLOT"])
@pytest.mark.parametrize(
    "data,kind",
    [
        ({"result": "RETRY"}, "SourceEmptyRetry"),
        ({"result": "SOURCE_DONE"}, "SourceEmptyDone"),
        ({"result": "WAIT", "retry_after_ms": 1000}, "SourceEmptyWait"),
    ],
)
@pytest.mark.asyncio
async def test_all_business_results_complete_current_obligation_without_technical_retry(source, data, kind):
    from src.app.wms_adapter.outbound_picking.source_empty_typed import decode_outcome

    body = response(data)
    client = client_response(body)
    result = await dispatch(client, request(source))
    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.normalized_response == body
    assert result.response_result == data["result"]
    assert result.retry_after_ms is None
    outcome = decode_outcome(body)
    assert type(outcome.result) is getattr(sdk, kind)
    if data["result"] == "WAIT":
        assert outcome.result.retry_after_ms == 1000
    assert client.post.await_args.args == ("/api/v1/wes/decisions",)
    assert client.post.await_args.kwargs["json"] == request(source)


@pytest.mark.parametrize(
    "data",
    [
        {"result": "RETRY", "retry_after_ms": 1000},
        {"result": "SOURCE_DONE", "replacement_sources": []},
        {"result": "WAIT"},
        {"result": "WAIT", "retry_after_ms": 0},
        {"result": "WAIT", "retry_after_ms": 60001},
        {"result": "WAIT", "retry_after_ms": True},
        {"result": "WAIT", "retry_after_ms": None},
        {"result": "REPLACE"},
    ],
)
@pytest.mark.asyncio
async def test_unapproved_results_and_replacement_sources_are_rejected(data):
    result = await dispatch(client_response(response(data)))
    assert result.code is WmsDispatchCode.RECONCILING
    assert result.normalized_response == response(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "bad id"),
        ("observed_at", -1),
        ("observed_at", 2**63),
        ("observed_at", True),
        ("source_locator", {}),
        ("source_locator", {"type": "NG_ZONE", "zone_code": "NG-1"}),
        ("observed_at", None),
    ],
)
@pytest.mark.asyncio
async def test_invalid_request_does_not_reach_http(field, value):
    payload = request()
    payload["data"][field] = value
    client = client_response(response({"result": "RETRY"}))
    assert (await dispatch(client, payload)).code is WmsDispatchCode.RECONCILING
    client.post.assert_not_called()


@pytest.mark.parametrize(
    "override",
    [{"task_id": "bad id"}, {"source_locator": {}}, {"observed_at": -1}, {"observed_at": True}, {"observed_at": 2**63}],
)
def test_sdk_rejects_invalid_intent(override):
    values = {
        "operation_id": OPERATION_ID,
        "task_id": "TASK-1",
        "source_locator": sdk.PickingRackSlot("RACK-1", "A", "S-1"),
        "observed_at": 0,
    }
    with pytest.raises((ValueError, TypeError)):
        wms_operations.outbound_source_empty_decide(**(values | override))


@pytest.mark.parametrize(
    "code,status,data,expected",
    [
        ("UNAVAILABLE", 503, {}, WmsDispatchCode.RETRY),
        ("CONFLICT", 409, {"reason_code": "STATE_CONFLICT"}, WmsDispatchCode.RECONCILING),
        (
            "REJECTED",
            422,
            {"reason_code": "INVALID_DATA", "field_path": "/data/source_locator"},
            WmsDispatchCode.RECONCILING,
        ),
    ],
)
@pytest.mark.asyncio
async def test_shared_errors_remain_distinct_from_business_results(code, status, data, expected):
    from src.app.wms_adapter.outbound_picking.source_empty_typed import decode_outcome

    body = response(data, code)
    result = await dispatch(client_response(body, status))
    assert result.code is expected
    assert result.normalized_response == body
    outcome = decode_outcome(body)
    assert type(outcome) is sdk.SourceEmptyOutcome
    if code == "UNAVAILABLE":
        assert type(outcome.result) is sdk.OperationUnavailable
    else:
        assert outcome.result.reason_code == data["reason_code"]


@pytest.mark.asyncio
async def test_response_identity_drift_retains_evidence():
    body = response({"result": "RETRY"}) | {"operation_id": "019f3405-2200-7b01-8b01-000000000002"}
    result = await dispatch(client_response(body))
    assert result.code is WmsDispatchCode.RECONCILING
    assert result.normalized_response == body


@pytest.mark.asyncio
async def test_request_digest_drift_prevents_send():
    client = client_response(response({"result": "RETRY"}))
    assert (await dispatch(client, request_digest="wrong")).code is WmsDispatchCode.RECONCILING
    client.post.assert_not_called()


@pytest.mark.parametrize("observed_at", [0, 2**63 - 1])
def test_observed_at_preserves_int64_boundaries(observed_at):
    from src.app.wms_adapter.outbound_picking.source_empty_typed import encode_request

    intent = wms_operations.outbound_source_empty_decide(
        operation_id=OPERATION_ID,
        task_id="TASK-1",
        source_locator=sdk.PickingRackSlot("RACK-1", "A", "S-1"),
        observed_at=observed_at,
    )
    assert encode_request(intent, timestamp=0)["data"]["observed_at"] == observed_at


def test_sdk_outcome_rejects_unapproved_result_and_errors():
    with pytest.raises(TypeError):
        sdk.SourceEmptyOutcome({"result": "RETRY"})
    with pytest.raises(ValueError):
        sdk.SourceEmptyOutcome(sdk.OperationConflict("POSITION_CONFLICT"))
    with pytest.raises(ValueError):
        sdk.SourceEmptyWait(60001)
