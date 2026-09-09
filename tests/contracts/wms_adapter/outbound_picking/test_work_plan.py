"""Bin 工作计划的 wire、typed 边界及共享派发接入差异。"""

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

OPERATION = "outbound.bin.work_plan@v1"
OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"


def request():
    return {
        "operation_id": OPERATION_ID,
        "operation": OPERATION,
        "timestamp": 100,
        "data": {"task_id": "TASK-1", "bin_code": "BIN-1", "scanned_at": 0},
    }


def response(data, code="DECIDED"):
    return {"operation_id": OPERATION_ID, "code": code, "timestamp": 101, "data": data}


def test_scan_code_is_preserved_and_internal_id_is_not_a_wire_alias():
    from src.app.wms_adapter.outbound_picking.work_plan_typed import encode_request
    from src.app.wms_adapter.outbound_picking.work_plan_wire import parse_bin_work_plan_request

    data = {"task_id": "TASK-1", "bin_code": "001aB", "scanned_at": 0}
    intent = wms_operations.outbound_bin_work_plan(operation_id=OPERATION_ID, **data)
    payload = encode_request(intent, timestamp=100)
    assert payload["data"] == data
    assert parse_bin_work_plan_request(payload).data.bin_code == "001aB"
    payload["data"]["bin_id"] = payload["data"].pop("bin_code")
    with pytest.raises(ValueError):
        parse_bin_work_plan_request(payload)


def test_typed_request_preserves_scan_time_and_is_immutable():
    from src.app.wms_adapter.outbound_picking.work_plan_typed import encode_request

    intent = wms_operations.outbound_bin_work_plan(operation_id=OPERATION_ID, **request()["data"])
    assert encode_request(intent, timestamp=100) == request()
    with pytest.raises(FrozenInstanceError):
        intent.scanned_at = 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "bad id"),
        ("bin_code", ""),
        ("scanned_at", True),
        ("scanned_at", -1),
        ("scanned_at", 2**63),
        ("scanned_at", "0"),
    ],
)
def test_request_rejects_invalid_business_fields(field, value):
    from src.app.wms_adapter.outbound_picking.work_plan_wire import parse_bin_work_plan_request

    body = request()
    body["data"][field] = value
    with pytest.raises((TypeError, ValueError)):
        wms_operations.outbound_bin_work_plan(operation_id=OPERATION_ID, **body["data"])
    with pytest.raises(ValueError):
        parse_bin_work_plan_request(body)


@pytest.mark.parametrize(
    "data",
    [
        {"result": "READY", "cell_ids": ["CELL-2", "CELL-1"]},
        {"result": "NO_WORK"},
        {"result": "WAIT", "retry_after_ms": 60000},
    ],
)
@pytest.mark.asyncio
async def test_work_plan_decisions_complete_obligation_without_automatic_followup(data):
    from src.app.wms_adapter.outbound_picking.work_plan_typed import decode_outcome

    body = response(data)
    client = AsyncMock(
        post=AsyncMock(
            return_value=WmsAccessResult(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                status_code=200,
                response_headers=(("Content-Type", "application/json"),),
                body_present=True,
                json_body=body,
                failure_kind=None,
                json_failure=None,
            )
        )
    )
    payload = request()
    result = await WmsConfirmationAdapter(client).dispatch(
        operation=OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
    )
    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == data["result"]
    assert result.retry_after_ms is None
    assert result.normalized_response == body
    outcome = decode_outcome(result.normalized_response)
    assert type(outcome) is sdk.BinWorkPlanOutcome
    if data["result"] == "READY":
        assert outcome.result.cell_ids == ("CELL-2", "CELL-1")
    elif data["result"] == "WAIT":
        assert outcome.result.retry_after_ms == 60000
    else:
        assert type(outcome.result) is sdk.BinWorkPlanNoWork
    client.post.assert_awaited_once_with(
        "/api/v1/wes/decisions",
        json=payload,
        max_request_body_bytes=256 * 1024,
        max_response_body_bytes=256 * 1024,
        observation=None,
    )


@pytest.mark.parametrize(
    "data",
    [
        {"result": "READY", "cell_ids": []},
        {"result": "READY", "cell_ids": ["C", "C"]},
        {"result": "READY", "cell_ids": ["bad cell"]},
        {"result": "WAIT"},
        {"result": "WAIT", "retry_after_ms": 0},
        {"result": "WAIT", "retry_after_ms": 60001},
        {"result": "WAIT", "retry_after_ms": True},
        {"result": "NO_BATCH", "retry_after_ms": 1},
    ],
)
def test_response_is_closed_and_cells_unique(data):
    from src.app.wms_adapter.outbound_picking.work_plan_typed import decode_outcome

    with pytest.raises(ValueError):
        decode_outcome(response(data))


def test_sdk_ready_requires_immutable_nonempty_unique_cells():
    for cells in ([], (), ["C"], ("C", "C"), ("bad cell",), (1,)):
        with pytest.raises((TypeError, ValueError)):
            sdk.BinWorkPlanReady(cells)
    ready = sdk.BinWorkPlanReady(("C",))
    with pytest.raises(FrozenInstanceError):
        ready.cell_ids = ("D",)


def test_work_plan_requires_matching_response_identity_and_strict_envelopes():
    from src.app.wms_adapter.outbound_picking.work_plan_wire import (
        parse_bin_work_plan_request,
        parse_bin_work_plan_response,
    )

    parsed = parse_bin_work_plan_request(request())
    body = response({"result": "NO_WORK"})
    body["operation_id"] = "019f3405-2200-7b01-8b01-000000000002"
    with pytest.raises(ValueError):
        parse_bin_work_plan_response(200, body, request=parsed)
    for changed in (request() | {"data": None}, request() | {"operation": "other@v1"}):
        with pytest.raises(ValueError):
            parse_bin_work_plan_request(changed)


@pytest.mark.asyncio
async def test_corrupt_frozen_request_is_not_sent():
    client = AsyncMock()
    result = await WmsConfirmationAdapter(client).dispatch(
        operation=OPERATION, operation_id=OPERATION_ID, request_payload=request(), request_digest="changed"
    )
    assert result.code is WmsDispatchCode.RECONCILING
    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "code,data,expected",
    [
        ("UNAVAILABLE", {}, sdk.OperationUnavailable),
        ("CONFLICT", {"reason_code": "REFERENCE_CONFLICT"}, sdk.OperationConflict),
        ("REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data/bin_code"}, sdk.OperationRejected),
    ],
)
def test_persisted_errors_decode_to_shared_typed_results(code, data, expected):
    from src.app.wms_adapter.outbound_picking.work_plan_typed import decode_outcome

    outcome = decode_outcome(response(data, code))
    assert type(outcome.result) is expected
    if code == "REJECTED":
        assert outcome.result.field_path == "/data/bin_code"


@pytest.mark.parametrize(
    "status,code,data,expected",
    [
        (503, "UNAVAILABLE", {}, WmsDispatchCode.RETRY),
        (409, "CONFLICT", {"reason_code": "STATE_CONFLICT"}, WmsDispatchCode.RECONCILING),
        (422, "REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data/bin_code"}, WmsDispatchCode.RECONCILING),
        (200, "DUPLICATE", {}, WmsDispatchCode.RECONCILING),
    ],
)
@pytest.mark.asyncio
async def test_error_mapping(status, code, data, expected):
    client = AsyncMock(
        post=AsyncMock(
            return_value=WmsAccessResult(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                status_code=status,
                response_headers=(("Content-Type", "application/json"),),
                body_present=True,
                json_body=response(data, code),
                failure_kind=None,
                json_failure=None,
            )
        )
    )
    payload = request()
    result = await WmsConfirmationAdapter(client).dispatch(
        operation=OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
    )
    assert result.code is expected


@pytest.mark.parametrize(
    "data",
    [
        {"result": "READY", "cell_ids": ["C"], "retry_after_ms": 1},
        {"result": "NO_WORK", "cell_ids": None},
        {"result": "WAIT", "retry_after_ms": 1, "cell_ids": ["C"]},
    ],
)
def test_redundant_fields_accepted_response_is_closed_and_cells_unique(data):
    from src.app.wms_adapter.outbound_picking.work_plan_wire import parse_bin_work_plan_response

    parsed = parse_bin_work_plan_response(200, response(data))
    assert parsed.data.result == data["result"]
