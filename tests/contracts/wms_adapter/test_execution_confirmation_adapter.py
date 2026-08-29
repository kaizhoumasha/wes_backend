"""E03/E07 使用静态 typed adapter，不经过 operation registry。"""

from __future__ import annotations

import hashlib
import json

import pytest

from src.app.wms_adapter.client import OutboundHttpClosedError, WmsAccessResult, WmsRequestBodyTooLargeError
from src.app.wms_adapter.execution_confirmation_adapter import (
    ExecutionConfirmationDispatchCode,
    WmsExecutionConfirmationAdapter,
)
from src.core.outbound_http import OutboundHttpDeliveryState


class _Client:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def post(self, path: str, *, json: dict[str, object], **_kwargs: object) -> WmsAccessResult:
        self.calls.append((path, json))
        return WmsAccessResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            failure_kind=None,
            status_code=200,
            response_headers=(("Content-Type", "application/json; charset=utf-8"),),
            body_present=True,
            json_body=self.response,
            json_failure=None,
        )


class _ControlledClient:
    def __init__(self, outcome: WmsAccessResult | Exception) -> None:
        self.outcome = outcome
        self.calls = 0

    async def post(self, *_args: object, **_kwargs: object) -> WmsAccessResult:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _e03_payload() -> dict[str, object]:
    return {
        "dispatch_key": "E03-001",
        "inbound_key": "IN-001",
        "material_code": "MAT-001",
        "quantity": "1",
        "pkg_id": "PKG-001",
        "location_code": "CELL-001",
    }


def _e03_response() -> dict[str, object]:
    return {
        "dispatch_key": "E03-001",
        "provider_reference": "PROVIDER-E03",
        "source_version": "1",
        "inbound_key": "IN-001",
        "wms_document_no": "DOC-001",
        "inventory_source_version": "1",
    }


def _access(
    *,
    delivery_state: OutboundHttpDeliveryState,
    status_code: int | None = None,
    json_body: object = None,
    response_headers: tuple[tuple[str, str], ...] = (),
    body_present: bool = False,
    json_failure: str | None = None,
) -> WmsAccessResult:
    return WmsAccessResult(
        delivery_state=delivery_state,
        failure_kind=None,
        status_code=status_code,
        response_headers=response_headers,
        body_present=body_present,
        json_body=json_body,  # type: ignore[arg-type]
        json_failure=json_failure,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "path", "request_payload", "response"),
    [
        (
            "wms.inventory.confirm_inbound@v1",
            "/inventory/confirm-inbound",
            {
                "dispatch_key": "E03-001",
                "inbound_key": "IN-001",
                "material_code": "MAT-001",
                "quantity": "1",
                "pkg_id": "PKG-001",
                "location_code": "CELL-001",
            },
            {
                "dispatch_key": "E03-001",
                "provider_reference": "PROVIDER-E03",
                "source_version": "1",
                "inbound_key": "IN-001",
                "wms_document_no": "DOC-001",
                "inventory_source_version": "1",
            },
        ),
        (
            "wms.fulfillment.notify_pkg_binding@v1",
            "/fulfillment/pkg-bindings",
            {
                "dispatch_key": "E07-001",
                "pkg_id": "PKG-001",
                "bin_id": "BIN-001",
                "slot_id": "SLOT-001",
                "rack_id": "RACK-001",
                "station_code": "STATION-001",
            },
            {
                "dispatch_key": "E07-001",
                "provider_reference": "PROVIDER-E07",
                "source_version": "1",
                "pkg_id": "PKG-001",
                "binding_reference": "BIND-001",
            },
        ),
    ],
)
async def test_static_typed_adapter_owns_fixed_paths_and_terminal_identity(
    operation: str,
    path: str,
    request_payload: dict[str, object],
    response: dict[str, object],
) -> None:
    client = _Client(response)
    adapter = WmsExecutionConfirmationAdapter(client)  # type: ignore[arg-type]

    result = await adapter.dispatch(
        operation=operation,
        operation_id=str(request_payload["dispatch_key"]),
        request_payload=request_payload,
        request_digest=_digest(request_payload),
    )

    assert result.code is ExecutionConfirmationDispatchCode.DETERMINATE
    assert result.response_result == "RECORDED"
    assert result.normalized_response == response
    assert client.calls == [(path, request_payload)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "operation_id", "payload", "digest"),
    [
        ("unsupported", "E03-001", _e03_payload(), _digest(_e03_payload())),
        ("wms.inventory.confirm_inbound@v1", "E03-001", _e03_payload(), "wrong-digest"),
        ("wms.inventory.confirm_inbound@v1", "other", _e03_payload(), _digest(_e03_payload())),
        (
            "wms.inventory.confirm_inbound@v1",
            "E03-001",
            {"dispatch_key": "E03-001"},
            _digest({"dispatch_key": "E03-001"}),
        ),
    ],
)
async def test_adapter_reconciles_invalid_operation_identity_digest_or_request(
    operation: str,
    operation_id: str,
    payload: dict[str, object],
    digest: str,
) -> None:
    client = _ControlledClient(AssertionError("invalid request must not reach WMS"))
    adapter = WmsExecutionConfirmationAdapter(client)  # type: ignore[arg-type]

    result = await adapter.dispatch(
        operation=operation,
        operation_id=operation_id,
        request_payload=payload,
        request_digest=digest,
    )

    assert result.code is ExecutionConfirmationDispatchCode.RECONCILING
    assert client.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("access", "expected"),
    [
        (
            _access(delivery_state=OutboundHttpDeliveryState.NOT_SENT),
            ExecutionConfirmationDispatchCode.NOT_SENT,
        ),
        (
            _access(delivery_state=OutboundHttpDeliveryState.DELIVERY_UNKNOWN),
            ExecutionConfirmationDispatchCode.DELIVERY_UNKNOWN,
        ),
        (
            _access(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                status_code=429,
                json_body={"retry": True},
                body_present=True,
            ),
            ExecutionConfirmationDispatchCode.RETRY,
        ),
        (
            _access(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                status_code=503,
                json_body={"retry": True},
                body_present=True,
            ),
            ExecutionConfirmationDispatchCode.RETRY,
        ),
    ],
)
async def test_adapter_preserves_transport_uncertainty_and_retryable_status(
    access: WmsAccessResult,
    expected: ExecutionConfirmationDispatchCode,
) -> None:
    payload = _e03_payload()
    adapter = WmsExecutionConfirmationAdapter(_ControlledClient(access))  # type: ignore[arg-type]

    result = await adapter.dispatch(
        operation="wms.inventory.confirm_inbound@v1",
        operation_id="E03-001",
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "access",
    [
        _access(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=500,
            json_body={"error": "failed"},
            body_present=True,
        ),
        _access(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=200,
            json_body=_e03_response(),
            response_headers=(),
            body_present=True,
        ),
        _access(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=200,
            json_body=[_e03_response()],
            response_headers=(("Content-Type", "application/json"),),
            body_present=True,
        ),
        _access(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=200,
            json_body={**_e03_response(), "dispatch_key": "other"},
            response_headers=(("Content-Type", "application/json"),),
            body_present=True,
        ),
    ],
)
async def test_adapter_reconciles_non_terminal_or_malformed_http_responses(access: WmsAccessResult) -> None:
    payload = _e03_payload()
    adapter = WmsExecutionConfirmationAdapter(_ControlledClient(access))  # type: ignore[arg-type]

    result = await adapter.dispatch(
        operation="wms.inventory.confirm_inbound@v1",
        operation_id="E03-001",
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is ExecutionConfirmationDispatchCode.RECONCILING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (WmsRequestBodyTooLargeError("too large"), ExecutionConfirmationDispatchCode.RECONCILING),
        (OutboundHttpClosedError("closed"), ExecutionConfirmationDispatchCode.NOT_SENT),
    ],
)
async def test_adapter_maps_local_pre_send_failures_without_claiming_delivery(
    error: Exception,
    expected: ExecutionConfirmationDispatchCode,
) -> None:
    payload = _e03_payload()
    adapter = WmsExecutionConfirmationAdapter(_ControlledClient(error))  # type: ignore[arg-type]

    result = await adapter.dispatch(
        operation="wms.inventory.confirm_inbound@v1",
        operation_id="E03-001",
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is expected
