from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.app.transport.contracts import (
    BinExchangePair,
    ExchangeBinsRequest,
    RackBinSlot,
    TransportCaller,
    TransportSubmitCode,
)
from src.app.wms_adapter.transport_adapter import WmsTransportAdapter


@dataclass
class FakeAccessResult:
    delivery_state: object
    status_code: int | None
    json_body: object
    json_failure: str | None = None


class Value:
    def __init__(self, value: str) -> None:
        self.value = value


class FakeClient:
    def __init__(self, result: FakeAccessResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object], dict[str, object]]] = []

    async def post(self, path: str, *, json: dict[str, object], **kwargs: object) -> FakeAccessResult:
        self.calls.append((path, json, kwargs))
        if isinstance(self.result.json_body, dict) and self.result.json_body.get("request_id") == "ignored-by-mapping":
            self.result.json_body["request_id"] = json["request_id"]
        return self.result


@pytest.mark.asyncio
async def test_exchange_pairs_use_one_fixed_transport_request() -> None:
    client = FakeClient(
        FakeAccessResult(
            Value("RESPONSE_RECEIVED"),
            202,
            {
                "request_id": "ignored-by-mapping",
                "code": "RECEIVED",
                "message": "accepted",
                "timestamp": 1,
                "data": {"transport_task_id": "transport-1"},
            },
        )
    )
    request = ExchangeBinsRequest(
        "client-1",
        TransportCaller("SORTER"),
        (
            BinExchangePair("bin-1", RackBinSlot("rack-1", "1"), "bin-2", RackBinSlot("rack-2", "1")),
            BinExchangePair("bin-3", RackBinSlot("rack-1", "2"), "bin-4", RackBinSlot("rack-2", "2")),
        ),
    )

    result = await WmsTransportAdapter(client).submit(request, transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.RECEIVED
    assert len(client.calls) == 1
    path, envelope, kwargs = client.calls[0]
    assert path == "/api/v1/wes/transport-requests"
    assert envelope["operation"] == "transport.task.submit@v1"
    assert envelope["data"]["kind"] == "BIN_EXCHANGE"
    assert len(envelope["data"]["exchange_pairs"]) == 2
    assert kwargs == {
        "max_request_body_bytes": 256 * 1024,
        "max_response_body_bytes": 256 * 1024,
    }


@pytest.mark.asyncio
async def test_delivery_unknown_is_not_interpreted_as_rejection() -> None:
    client = FakeClient(FakeAccessResult(Value("DELIVERY_UNKNOWN"), None, None))
    request = ExchangeBinsRequest(
        "client-1",
        TransportCaller("SORTER"),
        (BinExchangePair("bin-1", RackBinSlot("rack-1", "1"), "bin-2", RackBinSlot("rack-2", "1")),),
    )

    result = await WmsTransportAdapter(client).submit(request, transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN
