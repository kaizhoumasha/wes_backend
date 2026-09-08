from __future__ import annotations

import hashlib
import json

import pytest

from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.app.wms_adapter.outbound_picking.adapter import (
    PickingTaskPrepareAdapter,
)
from src.app.wms_adapter.outbound_picking.wire import PICKING_TASK_PREPARE_OPERATION
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpResult

OPERATION_ID = "019f3400-0e17-7d2a-b944-3cf7953804da"


async def test_prepare_observation_uses_actual_response_branch_and_validation_error() -> None:
    from src.app.wms_diagnostics.observation import WmsCallObservation

    transport = _Transport(
        _response(
            {"operation_id": OPERATION_ID, "code": "PREPARE_ACCEPTED", "timestamp": "bad", "data": {}}, status=202
        )
    )
    request = _request()
    observation = WmsCallObservation(direction="WES_TO_WMS")
    result = await PickingTaskPrepareAdapter(WmsClient(transport)).dispatch(
        operation=PICKING_TASK_PREPARE_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=request,
        request_digest=_digest(request),
        observation=observation,
    )
    assert result.code is WmsDispatchCode.RECONCILING
    assert observation.request_validated is True
    assert observation.response_validated is False
    assert observation.response_errors[0]["loc"] == ("timestamp",)
    assert observation.response_body is not None
    assert len(transport.requests) == 1


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

    assert result.code is WmsDispatchCode.DETERMINATE
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

    expected = WmsDispatchCode.RETRY if status == 503 else WmsDispatchCode.RECONCILING
    assert result.code is expected


@pytest.mark.asyncio
async def test_prepare_adapter_fails_closed_for_request_or_response_identity_mismatch() -> None:
    from src.app.wms_diagnostics.observation import WmsCallObservation

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
    observation = WmsCallObservation(direction="WES_TO_WMS")
    digest_mismatch = await adapter.dispatch(
        operation=PICKING_TASK_PREPARE_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest="0" * 64,
        observation=observation,
    )

    assert response_mismatch.code is WmsDispatchCode.RECONCILING
    assert digest_mismatch.code is WmsDispatchCode.RECONCILING
    assert len(transport.requests) == 1
    assert observation.error_code == "FROZEN_REQUEST_MISMATCH"


@pytest.mark.asyncio
async def test_rejected_response_without_field_path_round_trips_through_typed_outcome():
    from src.app.wms_adapter.outbound_picking.typed import decode_outcome

    response = {
        "operation_id": OPERATION_ID,
        "code": "REJECTED",
        "timestamp": 2,
        "data": {"reason_code": "INVALID_DATA"},
    }
    result = await _dispatch(_Transport(_response(response, status=422)))
    assert result.normalized_response == response
    outcome = decode_outcome(result.normalized_response)
    assert outcome.result.reason_code == "INVALID_DATA"
    assert outcome.result.field_path is None
