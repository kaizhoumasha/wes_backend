from __future__ import annotations

import json

import pytest

from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpResult
from src.utils.canonical_json import canonical_json_digest

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"


class _Transport:
    def __init__(self, response: OutboundHttpResult) -> None:
        self.response = response
        self.requests = []

    async def send(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.response

    async def aclose(self) -> None:
        return None


def _response(status: int, body: dict[str, object]) -> OutboundHttpResult:
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=status,
        response_headers=(("Content-Type", "application/json; charset=utf-8"),),
        decoded_body=json.dumps(body, separators=(",", ":")).encode(),
    )


def _admission_request() -> dict[str, object]:
    return {
        "operation_id": OPERATION_ID,
        "operation": "outbound.manual_bin.work_admission_decide@v1",
        "timestamp": 1_788_389_900_000,
        "data": {"task_id": "PICK-001", "bin_code": "BIN-001", "scanned_at": 1_788_389_899_900},
    }


@pytest.mark.asyncio
async def test_wait_is_determinate_without_automatic_same_identity_follow_up() -> None:
    request = _admission_request()
    transport = _Transport(
        _response(
            200,
            {
                "operation_id": OPERATION_ID,
                "code": "DECIDED",
                "timestamp": 1_788_389_900_001,
                "data": {"result": "WAIT", "retry_after_ms": 1000},
            },
        )
    )

    result = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation="outbound.manual_bin.work_admission_decide@v1",
        operation_id=OPERATION_ID,
        request_payload=request,
        request_digest=canonical_json_digest(request),
    )

    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == "WAIT"
    assert result.retry_after_ms is None
    assert transport.requests[0].path == "/api/v1/wes/decisions"


@pytest.mark.asyncio
@pytest.mark.parametrize("response_task", ["PICK-001", "PICK-OTHER"])
async def test_admission_sends_task_and_reconciles_a_different_response_task(response_task: str) -> None:
    request = _admission_request()
    transport = _Transport(
        _response(
            200,
            {
                "operation_id": OPERATION_ID,
                "code": "DECIDED",
                "timestamp": 1_788_389_900_001,
                "data": {"result": "WORK_REQUIRED", "task_id": response_task},
            },
        )
    )
    result = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation="outbound.manual_bin.work_admission_decide@v1",
        operation_id=OPERATION_ID,
        request_payload=request,
        request_digest=canonical_json_digest(request),
    )
    expected = WmsDispatchCode.DETERMINATE if response_task == "PICK-001" else WmsDispatchCode.RECONCILING
    assert result.code is expected
    assert result.response_result == ("WORK_REQUIRED" if response_task == "PICK-001" else None)
    assert len(transport.requests) == 1
    assert json.loads(transport.requests[0].body)["data"]["task_id"] == "PICK-001"
