"""E03/E07 使用静态 typed adapter，不经过 operation registry。"""

from __future__ import annotations

import hashlib
import json

import pytest

from src.app.wms_adapter.client import WmsAccessResult
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


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
