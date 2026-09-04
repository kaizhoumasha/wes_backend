from __future__ import annotations

import hashlib
import json

import pytest

from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.outbound_picking.adapter import (
    PickingTaskPrepareAdapter,
    PickingTaskPrepareDispatchCode,
)
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpFailureKind, OutboundHttpResult

OPERATION_ID = "019f3400-0e17-7d2a-b944-3cf7953804da"


class _Transport:
    def __init__(self, response: OutboundHttpResult) -> None:
        self.response = response
        self.requests = []

    async def send(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.response

    async def aclose(self) -> None:
        return None


def _request() -> dict[str, object]:
    return {
        "operation_id": OPERATION_ID,
        "operation": PICKING_TASK_PREPARE_OPERATION,
        "timestamp": 1786060810000,
        "data": {"task_id": "PICK-20260811-001", "workline_code": "SORTING-LINE-01"},
    }


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _response(body: dict[str, object], *, status: int) -> OutboundHttpResult:
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=status,
        response_headers=(("Content-Type", "application/json; charset=utf-8"),),
        decoded_body=json.dumps(body, separators=(",", ":")).encode(),
    )


async def _dispatch(transport: _Transport, payload: dict[str, object] | None = None):  # type: ignore[no-untyped-def]
    request = payload or _request()
    return await PickingTaskPrepareAdapter(WmsClient(transport)).dispatch(
        operation=PICKING_TASK_PREPARE_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=request,
        request_digest=_digest(request),
    )


@pytest.mark.asyncio
async def test_prepare_adapter_sends_to_decision_path_and_accepts_only_prepare_accepted() -> None:
    transport = _Transport(
        _response(
            {"operation_id": OPERATION_ID, "code": "PREPARE_ACCEPTED", "timestamp": 2, "data": {}},
            status=202,
        )
    )

    result = await _dispatch(transport)

    assert result.code is PickingTaskPrepareDispatchCode.DETERMINATE
    assert result.response_result == "PREPARE_ACCEPTED"
    assert result.normalized_response == {
        "operation_id": OPERATION_ID,
        "code": "PREPARE_ACCEPTED",
        "timestamp": 2,
        "data": {},
    }
    assert transport.requests[0].path == "/api/v1/wes/decisions"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(503, "UNAVAILABLE"), (409, "CONFLICT"), (422, "REJECTED")])
async def test_prepare_adapter_maps_retry_and_determinate_failures(status: int, code: str) -> None:
    data: dict[str, object] = {}
    if code == "CONFLICT":
        data = {"reason_code": "STATE_CONFLICT"}
    elif code == "REJECTED":
        data = {"reason_code": "INVALID_ENVELOPE"}
    transport = _Transport(
        _response({"operation_id": OPERATION_ID, "code": code, "timestamp": 2, "data": data}, status=status)
    )

    result = await _dispatch(transport)

    expected = PickingTaskPrepareDispatchCode.RETRY if status == 503 else PickingTaskPrepareDispatchCode.RECONCILING
    assert result.code is expected


@pytest.mark.asyncio
async def test_prepare_adapter_preserves_not_sent_and_delivery_unknown() -> None:
    not_sent = _Transport(
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.NOT_SENT,
            failure_kind=OutboundHttpFailureKind.CONNECT_ERROR,
        )
    )
    unknown = _Transport(
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.DELIVERY_UNKNOWN,
            failure_kind=OutboundHttpFailureKind.READ_TIMEOUT,
        )
    )

    assert (await _dispatch(not_sent)).code is PickingTaskPrepareDispatchCode.NOT_SENT
    assert (await _dispatch(unknown)).code is PickingTaskPrepareDispatchCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
async def test_prepare_adapter_fails_closed_for_request_or_response_identity_mismatch() -> None:
    transport = _Transport(
        _response(
            {
                "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804db",
                "code": "PREPARE_ACCEPTED",
                "timestamp": 2,
                "data": {},
            },
            status=202,
        )
    )
    adapter = PickingTaskPrepareAdapter(WmsClient(transport))
    payload = _request()

    response_mismatch = await _dispatch(transport)
    digest_mismatch = await adapter.dispatch(
        operation=PICKING_TASK_PREPARE_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest="0" * 64,
    )

    assert response_mismatch.code is PickingTaskPrepareDispatchCode.RECONCILING
    assert digest_mismatch.code is PickingTaskPrepareDispatchCode.RECONCILING
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_prepare_adapter_rejects_invalid_json_response_headers() -> None:
    response = _response(
        {"operation_id": OPERATION_ID, "code": "PREPARE_ACCEPTED", "timestamp": 2, "data": {}},
        status=202,
    )
    response = OutboundHttpResult(
        delivery_state=response.delivery_state,
        status_code=response.status_code,
        response_headers=(("Content-Type", "text/plain"),),
        decoded_body=response.decoded_body,
    )
    result = await _dispatch(_Transport(response))

    assert result.code is PickingTaskPrepareDispatchCode.RECONCILING
