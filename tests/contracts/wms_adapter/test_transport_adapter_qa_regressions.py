"""后端 QA 发现的 WMS Transport ACK 映射回归。"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from src.app.transport.contracts import MoveRackRequest, RackPosition, TransportCaller, TransportSubmitCode
from src.app.wms_adapter.transport_adapter import WmsTransportAdapter
from src.core.outbound_http import OutboundHttpClosedError


@dataclass
class FakeAccessResult:
    delivery_state: str
    status_code: int | None
    json_body: object
    json_failure: str | None = None


class FakeClient:
    def __init__(self, result: FakeAccessResult) -> None:
        self.result = result

    async def post(self, path: str, *, json: dict[str, object], **kwargs: object) -> FakeAccessResult:
        if isinstance(self.result.json_body, dict) and self.result.json_body.get("request_id") == "CURRENT":
            self.result.json_body["request_id"] = json["request_id"]
        return self.result


class ClosedClient:
    async def post(self, path: str, *, json: dict[str, object], **kwargs: object) -> FakeAccessResult:
        raise OutboundHttpClosedError("closed")


def _request() -> MoveRackRequest:
    return MoveRackRequest(
        "client-request",
        TransportCaller("SORTER"),
        "rack-1",
        RackPosition("A"),
        RackPosition("B"),
    )


def _ack(status: int, code: str, data: dict[str, object]) -> FakeAccessResult:
    return FakeAccessResult(
        "RESPONSE_RECEIVED",
        status,
        {
            "request_id": "CURRENT",
            "code": code,
            "message": "ack",
            "timestamp": 1,
            "data": data,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_retry_after", [True, 0, -1, "1000"])
async def test_busy_ack_discards_non_positive_integer_retry_delay(invalid_retry_after: object) -> None:
    access = _ack(
        429,
        "BUSY",
        {"transport_task_id": "transport-1", "retry_after_ms": invalid_retry_after},
    )

    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.BUSY
    assert result.retry_after_ms is None


@pytest.mark.asyncio
async def test_busy_ack_preserves_positive_integer_retry_delay() -> None:
    access = _ack(429, "BUSY", {"transport_task_id": "transport-1", "retry_after_ms": 1500})

    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.BUSY
    assert result.retry_after_ms == 1500


@pytest.mark.asyncio
async def test_non_busy_ack_discards_retry_delay() -> None:
    access = _ack(503, "UNAVAILABLE", {"transport_task_id": "transport-1", "retry_after_ms": 1500})

    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN
    assert result.retry_after_ms is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (202, "RECEIVED", TransportSubmitCode.RECEIVED),
        (200, "DUPLICATE", TransportSubmitCode.DUPLICATE),
        (400, "REJECTED", TransportSubmitCode.REJECTED),
        (422, "REJECTED", TransportSubmitCode.REJECTED),
        (409, "CONFLICT", TransportSubmitCode.CONFLICT),
        (503, "UNAVAILABLE", TransportSubmitCode.UNAVAILABLE),
        (500, "RECEIVED", TransportSubmitCode.DELIVERY_UNKNOWN),
    ],
)
async def test_ack_status_and_code_are_a_closed_pair(
    status: int,
    code: str,
    expected: TransportSubmitCode,
) -> None:
    data: dict[str, object] = {"transport_task_id": "transport-1"}
    if code == "REJECTED":
        data["reason_code"] = "REJECTED_BY_WMS"
    access = _ack(status, code, data)

    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    assert result.code is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 422])
async def test_rejected_ack_preserves_a_persistable_reason_code(status_code: int) -> None:
    access = _ack(
        status_code,
        "REJECTED",
        {"transport_task_id": "transport-1", "reason_code": "COORDINATED_BIN_EXCHANGE_UNSUPPORTED"},
    )

    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.REJECTED
    assert result.reason_code == "COORDINATED_BIN_EXCHANGE_UNSUPPORTED"


@pytest.mark.asyncio
async def test_ack_for_another_task_is_a_conflict() -> None:
    access = _ack(202, "RECEIVED", {"transport_task_id": "transport-other"})

    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.CONFLICT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "another-request"),
        ("message", 123),
        ("timestamp", True),
        ("timestamp", "1"),
        ("code", []),
        ("code", {}),
    ],
)
async def test_ack_identity_and_scalar_types_fail_closed(field: str, value: object) -> None:
    access = _ack(202, "RECEIVED", {"transport_task_id": "transport-1"})
    assert isinstance(access.json_body, dict)
    access.json_body[field] = value

    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
async def test_rejected_ack_discards_reason_code_that_cannot_be_persisted() -> None:
    access = _ack(400, "REJECTED", {"transport_task_id": "transport-1", "reason_code": "R" * 121})

    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN
    assert result.reason_code is None


@pytest.mark.asyncio
async def test_rejected_ack_discards_unencodable_reason_code() -> None:
    reason_code = json.loads(r'"\ud800"')
    access = _ack(400, "REJECTED", {"transport_task_id": "transport-1", "reason_code": reason_code})

    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN
    assert result.reason_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "data"),
    [
        (202, "RECEIVED", {}),
        (400, "REJECTED", {"transport_task_id": "transport-1"}),
        (202, "RECEIVED", {"transport_task_id": "transport-1", "reason_code": "UNEXPECTED"}),
        (503, "UNAVAILABLE", {"transport_task_id": "transport-1", "retry_after_ms": 1000}),
        (
            400,
            "REJECTED",
            {"transport_task_id": "transport-1", "reason_code": "REJECTED_BY_WMS", "retry_after_ms": 1000},
        ),
    ],
)
async def test_ack_data_must_match_the_code_specific_closed_contract(
    status: int,
    code: str,
    data: dict[str, object],
) -> None:
    result = await WmsTransportAdapter(FakeClient(_ack(status, code, data))).submit(
        _request(), transport_task_id="transport-1"
    )

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
async def test_closed_transport_is_a_deterministic_not_sent_result() -> None:
    result = await WmsTransportAdapter(ClosedClient()).submit(_request(), transport_task_id="transport-1")

    assert result.code is TransportSubmitCode.NOT_SENT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "access",
    [
        FakeAccessResult("NOT_SENT", None, None),
        FakeAccessResult("DELIVERY_UNKNOWN", None, None),
        FakeAccessResult("RESPONSE_RECEIVED", 202, None),
        FakeAccessResult("RESPONSE_RECEIVED", 202, {}, "INVALID_JSON"),
        FakeAccessResult("RESPONSE_RECEIVED", 202, {"unexpected": "shape"}),
    ],
)
async def test_transport_and_malformed_ack_failures_preserve_delivery_certainty(
    access: FakeAccessResult,
) -> None:
    result = await WmsTransportAdapter(FakeClient(access)).submit(_request(), transport_task_id="transport-1")

    expected = (
        TransportSubmitCode.NOT_SENT if access.delivery_state == "NOT_SENT" else TransportSubmitCode.DELIVERY_UNKNOWN
    )
    assert result.code is expected
