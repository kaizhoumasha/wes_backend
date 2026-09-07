from __future__ import annotations

import json

import pytest

from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.confirmation_adapter import WmsConfirmationAdapter
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.app.wms_adapter.inbound_material.wire import (
    ADMISSION_OPERATION,
    NG_PLACEMENT_OPERATION,
    PLACEMENT_OPERATION,
)
from src.app.wms_adapter.wire_common import MAX_WMS_EVENT_BODY_BYTES
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpFailureKind, OutboundHttpResult
from tests.contracts.wms_adapter.inbound_material.support import (
    OPERATION_ID,
    OTHER_OPERATION_ID,
    _digest,
    _request,
    _response,
    _Transport,
)


@pytest.mark.asyncio
async def test_adapter_sends_decision_through_wms_client_and_returns_typed_evidence() -> None:
    payload = _request()
    transport = _Transport(
        _response(
            {
                "operation_id": OPERATION_ID,
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            }
        )
    )

    result = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == "ACCEPT"
    assert result.normalized_response == {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    assert transport.requests[0].path == "/api/v1/wes/decisions"


@pytest.mark.asyncio
async def test_adapter_returns_wait_as_determinate_business_result() -> None:
    payload = _request()
    transport = _Transport(
        _response(
            {
                "operation_id": OPERATION_ID,
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "WAIT", "reason_code": "CELL_PENDING", "retry_after_ms": 250},
            }
        )
    )

    result = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is WmsDispatchCode.DETERMINATE
    assert result.response_result == "WAIT"
    assert result.retry_after_ms == 250


@pytest.mark.asyncio
async def test_fact_uses_fact_path_and_recorded_is_determinate() -> None:
    payload = _request(PLACEMENT_OPERATION)
    transport = _Transport(_response({"operation_id": OPERATION_ID, "code": "RECORDED", "timestamp": 2, "data": {}}))

    result = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation=PLACEMENT_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert (result.code, result.response_result) == (WmsDispatchCode.DETERMINATE, "RECORDED")
    assert transport.requests[0].path == "/api/v1/wes/facts"


@pytest.mark.asyncio
async def test_ng_fact_without_pkg_id_omits_the_optional_field_on_the_wire() -> None:
    payload = _request(NG_PLACEMENT_OPERATION)
    transport = _Transport(_response({"operation_id": OPERATION_ID, "code": "RECORDED", "timestamp": 2, "data": {}}))

    result = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation=NG_PLACEMENT_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is WmsDispatchCode.DETERMINATE
    sent = json.loads(transport.requests[0].body)
    assert "pkg_id" not in sent["data"]


@pytest.mark.asyncio
async def test_device_text_is_bounded_by_the_encoded_request_body_not_a_field_cap() -> None:
    payload = _request()
    encoded_one = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    lot_length = len("LOT") + MAX_WMS_EVENT_BODY_BYTES - len(encoded_one)
    payload["data"]["six_in_one"]["LotCode"] = "x" * lot_length  # type: ignore[index]
    response = {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    exact_transport = _Transport(_response(response))

    exact = await WmsConfirmationAdapter(WmsClient(exact_transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert exact.code is WmsDispatchCode.DETERMINATE
    assert len(exact_transport.requests[0].body) == MAX_WMS_EVENT_BODY_BYTES

    payload["data"]["six_in_one"]["LotCode"] += "x"  # type: ignore[index, operator]
    oversized_transport = _Transport(_response(response))
    oversized = await WmsConfirmationAdapter(WmsClient(oversized_transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert oversized.code is WmsDispatchCode.RECONCILING
    assert oversized_transport.requests == []


@pytest.mark.asyncio
async def test_busy_is_retryable_but_invalid_or_conflicting_response_fails_closed() -> None:
    payload = _request()
    busy_transport = _Transport(
        _response(
            {
                "operation_id": OPERATION_ID,
                "code": "BUSY",
                "timestamp": 2,
                "data": {"retry_after_ms": 250},
            },
            status=429,
        )
    )
    busy = await WmsConfirmationAdapter(WmsClient(busy_transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )
    assert (busy.code, busy.retry_after_ms) == (WmsDispatchCode.RETRY, 250)

    conflict_transport = _Transport(
        _response(
            {
                "operation_id": OPERATION_ID,
                "code": "CONFLICT",
                "timestamp": 2,
                "data": {"reason_code": "IDEMPOTENCY_CONFLICT"},
            },
            status=409,
        )
    )
    conflict = await WmsConfirmationAdapter(WmsClient(conflict_transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )
    assert conflict.code is WmsDispatchCode.RECONCILING


@pytest.mark.asyncio
async def test_delivery_unknown_and_request_identity_mismatch_never_become_business_wait() -> None:
    payload = _request()
    transport = _Transport(
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.DELIVERY_UNKNOWN,
            failure_kind=OutboundHttpFailureKind.READ_TIMEOUT,
        )
    )
    unknown = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )
    assert unknown.code is WmsDispatchCode.DELIVERY_UNKNOWN

    mismatch = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest="0" * 64,
    )
    assert mismatch.code is WmsDispatchCode.RECONCILING
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_response_with_wrong_operation_id_is_reconciling_with_response_evidence() -> None:
    payload = _request()
    response_body = {
        "operation_id": OTHER_OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    transport = _Transport(_response(response_body))

    result = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is WmsDispatchCode.RECONCILING
    assert result.normalized_response == response_body


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["INVALID_DTO", "INVALID_STATUS"])
async def test_received_invalid_http_response_is_reconciling_and_never_retryable(case: str) -> None:
    payload = _request()
    valid_body = {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    body = valid_body
    status = 200
    headers = (("Content-Type", "application/json; charset=utf-8"),)
    decoded_body = json.dumps(body, separators=(",", ":")).encode()
    if case == "INVALID_DTO":
        body = {**valid_body, "data": {"result": "ACCEPT"}}
        decoded_body = json.dumps(body, separators=(",", ":")).encode()
    else:
        status = 418
    transport = _Transport(
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=status,
            response_headers=headers,
            decoded_body=decoded_body,
        )
    )

    result = await WmsConfirmationAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is WmsDispatchCode.RECONCILING
    assert result.normalized_response == body
