from __future__ import annotations

import hashlib
import json

import pytest

from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.app.wms_adapter.outbound_picking.arrival_report_adapter import (
    ReturnRackArrivalReportAdapter,
)
from src.app.wms_adapter.outbound_picking.arrival_report_wire import RETURN_RACK_ARRIVAL_REPORT_OPERATION
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpResult

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
        "operation": RETURN_RACK_ARRIVAL_REPORT_OPERATION,
        "timestamp": 1786060810000,
        "data": {
            "task_id": "PICK-20260811-001",
            "transport_task_id": "transport-001",
            "outcome_revision": 1,
            "rack_id": "RETURN-01",
            "final_position": {"type": "RACK_POSITION", "location_code": "WORK-01"},
            "arrival_face": "A",
        },
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
    return await ReturnRackArrivalReportAdapter(WmsClient(transport)).dispatch(
        operation=RETURN_RACK_ARRIVAL_REPORT_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=request,
        request_digest=_digest(request),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["RECORDED", "DUPLICATE"])
async def test_arrival_report_adapter_sends_to_facts_path_and_closes_only_recorded_facts(code: str) -> None:
    transport = _Transport(
        _response(
            {"operation_id": OPERATION_ID, "code": code, "timestamp": 2, "data": {}},
            status=200,
        )
    )

    result = await _dispatch(transport)

    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == code
    assert result.retry_after_ms is None
    assert result.normalized_response == {
        "operation_id": OPERATION_ID,
        "code": code,
        "timestamp": 2,
        "data": {},
    }
    assert transport.requests[0].path == "/api/v1/wes/facts"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(503, "UNAVAILABLE"), (409, "CONFLICT"), (422, "REJECTED")])
async def test_arrival_report_adapter_maps_retry_and_determinate_failures(status: int, code: str) -> None:
    data: dict[str, object] = {}
    if code == "CONFLICT":
        data = {"reason_code": "STATE_CONFLICT"}
    elif code == "REJECTED":
        data = {"reason_code": "INVALID_ENVELOPE"}
    transport = _Transport(
        _response({"operation_id": OPERATION_ID, "code": code, "timestamp": 2, "data": data}, status=status)
    )

    result = await _dispatch(transport)

    expected = WmsDispatchCode.RETRY if status == 503 else WmsDispatchCode.RECONCILING
    assert result.code is expected


@pytest.mark.asyncio
async def test_arrival_report_adapter_fails_closed_for_request_or_response_identity_mismatch() -> None:
    transport = _Transport(
        _response(
            {
                "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804db",
                "code": "RECORDED",
                "timestamp": 2,
                "data": {},
            },
            status=200,
        )
    )
    adapter = ReturnRackArrivalReportAdapter(WmsClient(transport))
    payload = _request()

    response_mismatch = await _dispatch(transport)
    digest_mismatch = await adapter.dispatch(
        operation=RETURN_RACK_ARRIVAL_REPORT_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest="0" * 64,
    )

    assert response_mismatch.code is WmsDispatchCode.RECONCILING
    assert digest_mismatch.code is WmsDispatchCode.RECONCILING
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_invalid_frozen_arrival_request_is_not_sent() -> None:
    transport = _Transport(
        _response(
            {"operation_id": OPERATION_ID, "code": "RECORDED", "timestamp": 2, "data": {}},
            status=200,
        )
    )
    payload = _request()
    payload["data"]["arrival_face"] = "A" * 11
    result = await _dispatch(transport, payload)
    assert result.code is WmsDispatchCode.RECONCILING
    assert transport.requests == []
