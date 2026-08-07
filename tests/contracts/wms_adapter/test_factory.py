from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from src.app.wms_adapter import WmsClient, build_wms_client
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpResult

if TYPE_CHECKING:
    from src.core.outbound_http import OutboundHttpRequest


class _RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[OutboundHttpRequest] = []

    async def send(self, request: OutboundHttpRequest) -> OutboundHttpResult:
        self.requests.append(request)
        return OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=204,
            decoded_body=b"",
        )

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_builder_owns_the_wms_system_id_and_returns_a_usable_client(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _RecordingTransport()
    captured: list[dict[str, object]] = []

    def build_transport(**kwargs: object) -> _RecordingTransport:
        captured.append(kwargs)
        return transport

    monkeypatch.setattr("src.app.wms_adapter.factory.build_outbound_http_transport", build_transport)

    client = build_wms_client(base_url="http://wms.test:8080/", timeout_seconds=3.5)
    result = await client.get("/health")

    assert isinstance(client, WmsClient)
    assert captured == [
        {
            "system_id": "wms",
            "base_url": "http://wms.test:8080/",
            "timeout_seconds": 3.5,
        }
    ]
    assert result.status_code == 204
    assert len(transport.requests) == 1
