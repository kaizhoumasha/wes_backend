"""公共单次收发保护的唯一主要测试 owner。"""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.app.wms_adapter.client import WmsAccessResult, WmsRequestBodyTooLargeError
from src.app.wms_adapter.dispatch import WmsDispatchCode, WmsDispatchResult, receive_json
from src.app.wms_adapter.wire_common import MAX_WMS_EVENT_BODY_BYTES
from src.app.wms_diagnostics.observation import WmsCallObservation
from src.core.outbound_http import OutboundHttpClosedError, OutboundHttpDeliveryState, OutboundHttpFailureKind


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes, expected",
    [
        ({}, None),
        ({"delivery_state": OutboundHttpDeliveryState.NOT_SENT}, "NOT_SENT"),
        ({"delivery_state": OutboundHttpDeliveryState.DELIVERY_UNKNOWN}, "DELIVERY_UNKNOWN"),
        ({"failure_kind": OutboundHttpFailureKind.READ_TIMEOUT}, "RECONCILING"),
        ({"response_headers": (("Content-Type", "text/plain"),)}, "RECONCILING"),
        ({"json_failure": "INVALID_JSON"}, "RECONCILING"),
        ({"body_present": False}, "RECONCILING"),
        ({"json_body": []}, "RECONCILING"),
    ],
)
async def test_receive_json_preserves_delivery_facts_and_checks_response(changes, expected):
    access = replace(
        WmsAccessResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            failure_kind=None,
            status_code=200,
            response_headers=(("Content-Type", "application/json"),),
            body_present=True,
            json_body={"code": "EXAMPLE"},
            json_failure=None,
        ),
        **changes,
    )
    client = AsyncMock()
    client.post.return_value = access
    observation = WmsCallObservation(direction="WES_TO_WMS")
    result = await receive_json(client, "/api/v1/wes/decisions", {"example": True}, observation=observation)
    client.post.assert_awaited_once_with(
        "/api/v1/wes/decisions",
        json={"example": True},
        max_request_body_bytes=MAX_WMS_EVENT_BODY_BYTES,
        max_response_body_bytes=MAX_WMS_EVENT_BODY_BYTES,
        observation=observation,
    )
    if expected is None:
        assert result is access
        assert observation.error_code is None
    else:
        assert isinstance(result, WmsDispatchResult)
        assert result.code == WmsDispatchCode(expected)
        assert observation.error_code is not None
        if "failure_kind" in changes or "response_headers" in changes:
            assert result.normalized_response == access.json_body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, expected",
    [
        (WmsRequestBodyTooLargeError, "RECONCILING"),
        (OutboundHttpClosedError, "NOT_SENT"),
    ],
)
async def test_receive_json_maps_local_send_failures(error, expected):
    client = AsyncMock()
    client.post.side_effect = error("test")
    observation = WmsCallObservation(direction="WES_TO_WMS")
    result = await receive_json(client, "/api/v1/wes/facts", {}, observation=observation)
    assert result.code == WmsDispatchCode(expected)
    assert client.post.await_count == 1
    assert observation.error_code is not None
