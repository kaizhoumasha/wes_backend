from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.app.wms_adapter.client import WmsAccessResult
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.app.wms_adapter.outbound_picking.inbound_batch_adapter import BinInboundBatchAdapter
from src.core.outbound_http import OutboundHttpDeliveryState
from src.utils.canonical_json import canonical_json_digest

OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"
OPERATION = "outbound.bin.inbound_batch@v1"


def request():
    return {
        "operation_id": OPERATION_ID,
        "operation": OPERATION,
        "timestamp": 0,
        "data": {"task_id": "TASK-1", "rack_id": "RACK-1", "rack_face": "A", "max_bin_count": 1},
    }


async def dispatch(client, payload=None, **overrides):
    payload = payload or request()
    args = {
        "operation": OPERATION,
        "operation_id": OPERATION_ID,
        "request_payload": payload,
        "request_digest": canonical_json_digest(payload),
    }
    args.update(overrides)
    return await BinInboundBatchAdapter(client).dispatch(**args)


def client_response(data, code="DECIDED", status=200, operation_id=OPERATION_ID):
    body = {"operation_id": operation_id, "code": code, "timestamp": 0, "data": data}
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {
            "result": "READY",
            "bins": [
                {
                    "bin_code": "BIN-1",
                    "source_locator": {
                        "type": "RACK_BIN_SLOT",
                        "rack_id": "RACK-1",
                        "rack_face": "A",
                        "slot_id": "S-1",
                    },
                }
            ],
        },
        {"result": "NO_BATCH", "retry_after_ms": 1000},
        {"result": "RACK_FACE_DONE"},
    ],
)
async def test_decided_closes_original_obligation_without_material_followup(data):
    client = client_response(data)
    result = await dispatch(client)
    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == data["result"]
    assert result.retry_after_ms is None
    assert result.normalized_response["data"] == data
    client.post.assert_awaited_once_with(
        "/api/v1/wes/decisions",
        json=request(),
        max_request_body_bytes=256 * 1024,
        max_response_body_bytes=256 * 1024,
        observation=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides", [{"operation": "other"}, {"operation_id": "other"}, {"request_digest": "changed"}]
)
async def test_frozen_identity_and_digest_fail_before_send(overrides):
    client = client_response({"result": "RACK_FACE_DONE"})
    result = await dispatch(client, **overrides)
    assert result.code is WmsDispatchCode.RECONCILING
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_source_is_reconciling_and_preserves_response():
    data = {
        "result": "READY",
        "bins": [
            {
                "bin_code": "BIN-1",
                "source_locator": {"type": "RACK_BIN_SLOT", "rack_id": "WRONG", "rack_face": "A", "slot_id": "S-1"},
            }
        ],
    }
    result = await dispatch(client_response(data))
    assert result.code is WmsDispatchCode.RECONCILING
    assert result.normalized_response["data"] == data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,data,expected",
    [
        (503, "UNAVAILABLE", {}, WmsDispatchCode.RETRY),
        (409, "CONFLICT", {"reason_code": "STATE_CONFLICT"}, WmsDispatchCode.RECONCILING),
        (422, "REJECTED", {"reason_code": "INVALID_DATA"}, WmsDispatchCode.RECONCILING),
        (200, "DUPLICATE", {}, WmsDispatchCode.RECONCILING),
    ],
)
async def test_error_mapping(status, code, data, expected):
    assert (await dispatch(client_response(data, code, status))).code is expected


@pytest.mark.asyncio
async def test_response_identity_mismatch():
    result = await dispatch(
        client_response({"result": "RACK_FACE_DONE"}, operation_id="019f3405-2200-7b01-8b01-000000000002")
    )
    assert result.code is WmsDispatchCode.RECONCILING
