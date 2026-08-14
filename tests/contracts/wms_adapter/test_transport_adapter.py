from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

import pytest

from src.app.transport.contracts import TransportSubmitCode
from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.transport_adapter import WmsTransportAdapter


@dataclass
class FakeAccessResult:
    delivery_state: object
    status_code: int | None
    json_body: object
    json_failure: str | None = None
    response_headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),)


class Value:
    def __init__(self, value: str) -> None:
        self.value = value


class FakeClient:
    def __init__(self, result: FakeAccessResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object], dict[str, object]]] = []

    async def post(self, path: str, *, json: dict[str, object], **kwargs: object) -> FakeAccessResult:
        self.calls.append((path, json, kwargs))
        if (
            isinstance(self.result.json_body, dict)
            and self.result.json_body.get("operation_id") == "ignored-by-mapping"
        ):
            self.result.json_body["operation_id"] = json["operation_id"]
        return self.result


class NoSendTransport:
    def __init__(self) -> None:
        self.send_count = 0

    async def send(self, request: object) -> object:
        self.send_count += 1
        raise AssertionError("oversized request must be rejected before send")

    async def aclose(self) -> None:
        return None


def _snapshot(
    operation_id: str,
    timestamp: int,
    payload: dict[str, object],
    *,
    payload_digest: str | None = None,
) -> dict[str, object]:
    return {
        "operation_id": operation_id,
        "timestamp": timestamp,
        "payload": payload,
        "payload_digest": payload_digest or _payload_digest(operation_id, timestamp, payload),
    }


def _payload_digest(operation_id: str, timestamp: int, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        {
            "operation_id": operation_id,
            "operation": "transport.task.submit@v1",
            "timestamp": timestamp,
            "data": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()


@pytest.mark.asyncio
async def test_exchange_pairs_send_one_fixed_persisted_snapshot() -> None:
    client = FakeClient(
        FakeAccessResult(
            Value("RESPONSE_RECEIVED"),
            202,
            {
                "operation_id": "ignored-by-mapping",
                "code": "RECEIVED",
                "timestamp": 1,
                "data": {"transport_task_id": "transport-1"},
            },
        )
    )
    payload = {
        "transport_task_id": "transport-1",
        "kind": "BIN_EXCHANGE",
        "exchange_pairs": [
            {
                "left_bin_id": "bin-1",
                "left_location": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "1"},
                "right_bin_id": "bin-2",
                "right_location": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "A", "slot_id": "1"},
            },
            {
                "left_bin_id": "bin-3",
                "left_location": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "2"},
                "right_bin_id": "bin-4",
                "right_location": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "A", "slot_id": "2"},
            },
        ],
    }

    result = await WmsTransportAdapter(client).submit(**_snapshot("019f12d0-58d7-7b4d-a23a-1b90aa5d4472", 1, payload))

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
    payload = {
        "transport_task_id": "transport-1",
        "kind": "BIN_EXCHANGE",
        "exchange_pairs": [
            {
                "left_bin_id": "bin-1",
                "left_location": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "1"},
                "right_bin_id": "bin-2",
                "right_location": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "A", "slot_id": "1"},
            }
        ],
    }

    result = await WmsTransportAdapter(client).submit(**_snapshot("019f12d0-58d7-7b4d-a23a-1b90aa5d4472", 1, payload))

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
async def test_oversized_request_is_a_deterministic_payload_rejection() -> None:
    transport = NoSendTransport()
    payload = {
        "transport_task_id": "transport-oversized",
        "kind": "BIN_MOVE",
        "moves": [
            {
                "bin_id": "bin-oversized",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "1"},
                "target": {"kind": "HANDOFF_POSITION", "location_code": "x" * (256 * 1024)},
            }
        ],
    }

    result = await WmsTransportAdapter(WmsClient(transport)).submit(
        **_snapshot("019f12d0-58d7-7b4d-a23a-1b90aa5d4472", 1, payload)
    )

    assert result.code is TransportSubmitCode.REJECTED
    assert result.reason_code == "PAYLOAD_TOO_LARGE"
    assert transport.send_count == 0


@pytest.mark.asyncio
async def test_submit_wire_uses_the_persisted_operation_snapshot_without_local_caller() -> None:
    operation_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
    client = FakeClient(
        FakeAccessResult(
            Value("RESPONSE_RECEIVED"),
            202,
            {
                "operation_id": operation_id,
                "code": "RECEIVED",
                "timestamp": 1710000000123,
                "data": {"transport_task_id": "transport-1"},
            },
        )
    )
    payload = {
        "transport_task_id": "transport-1",
        "kind": "BIN_MOVE",
        "moves": [
            {
                "bin_id": "bin-1",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "1"},
                "target": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
            }
        ],
    }

    result = await WmsTransportAdapter(client).submit(**_snapshot(operation_id, 1710000000000, payload))

    assert result.code is TransportSubmitCode.RECEIVED
    assert client.calls == [
        (
            "/api/v1/wes/transport-requests",
            {
                "operation_id": operation_id,
                "operation": "transport.task.submit@v1",
                "timestamp": 1710000000000,
                "data": payload,
            },
            {"max_request_body_bytes": 256 * 1024, "max_response_body_bytes": 256 * 1024},
        )
    ]


@pytest.mark.asyncio
async def test_submit_rejects_a_tampered_persisted_payload_digest_before_send() -> None:
    transport = NoSendTransport()
    payload = {
        "transport_task_id": "transport-1",
        "kind": "BIN_MOVE",
        "moves": [],
    }

    result = await WmsTransportAdapter(WmsClient(transport)).submit(
        **_snapshot(
            "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
            1,
            payload,
            payload_digest="0" * 64,
        )
    )

    assert result.code is TransportSubmitCode.REJECTED
    assert result.reason_code == "PAYLOAD_DIGEST_MISMATCH"
    assert transport.send_count == 0
