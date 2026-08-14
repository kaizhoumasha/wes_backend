"""后端 QA 发现的 WMS Transport ACK 映射回归。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

import pytest

from src.app.transport.contracts import TransportSubmitCode
from src.app.wms_adapter.transport_adapter import WmsTransportAdapter
from src.core.outbound_http import OutboundHttpClosedError


@dataclass
class FakeAccessResult:
    delivery_state: str
    status_code: int | None
    json_body: object
    json_failure: str | None = None
    body_present: bool = True
    response_headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),)


class FakeClient:
    def __init__(self, result: FakeAccessResult) -> None:
        self.result = result

    async def post(self, path: str, *, json: dict[str, object], **kwargs: object) -> FakeAccessResult:
        if isinstance(self.result.json_body, dict) and self.result.json_body.get("operation_id") == "CURRENT":
            self.result.json_body["operation_id"] = json["operation_id"]
        return self.result


class ClosedClient:
    async def post(self, path: str, *, json: dict[str, object], **kwargs: object) -> FakeAccessResult:
        raise OutboundHttpClosedError("closed")


def _snapshot() -> dict[str, object]:
    envelope: dict[str, object] = {
        "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        "operation": "transport.task.submit@v1",
        "timestamp": 1,
        "data": {
            "transport_task_id": "transport-1",
            "kind": "RACK_MOVE",
            "rack_id": "rack-1",
            "source": {"kind": "RACK_POSITION", "location_code": "A"},
            "target": {"kind": "RACK_POSITION", "location_code": "B"},
        },
    }
    encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {
        "operation_id": envelope["operation_id"],
        "timestamp": envelope["timestamp"],
        "payload": envelope["data"],
        "payload_digest": sha256(encoded).hexdigest(),
    }


def _ack(status: int, code: str, data: dict[str, object]) -> FakeAccessResult:
    return FakeAccessResult(
        "RESPONSE_RECEIVED",
        status,
        {
            "operation_id": "CURRENT",
            "code": code,
            "timestamp": 1,
            "data": data,
        },
    )


@pytest.mark.asyncio
async def test_busy_ack_without_retry_delay_uses_service_fallback() -> None:
    access = _ack(429, "BUSY", {"transport_task_id": "transport-1"})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.BUSY
    assert result.retry_after_ms is None


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_retry_after", [True, 0, -1, "1000"])
async def test_busy_ack_discards_non_positive_integer_retry_delay(invalid_retry_after: object) -> None:
    access = _ack(
        429,
        "BUSY",
        {"transport_task_id": "transport-1", "retry_after_ms": invalid_retry_after},
    )

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.BUSY
    assert result.retry_after_ms is None


@pytest.mark.asyncio
async def test_busy_ack_preserves_positive_integer_retry_delay() -> None:
    access = _ack(429, "BUSY", {"transport_task_id": "transport-1", "retry_after_ms": 1500})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.BUSY
    assert result.retry_after_ms == 1500


@pytest.mark.asyncio
async def test_busy_ack_discards_retry_delay_above_contract_limit() -> None:
    access = _ack(429, "BUSY", {"transport_task_id": "transport-1", "retry_after_ms": 60001})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.BUSY
    assert result.retry_after_ms is None


@pytest.mark.asyncio
async def test_non_busy_ack_discards_retry_delay() -> None:
    access = _ack(503, "UNAVAILABLE", {"transport_task_id": "transport-1", "retry_after_ms": 1500})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN
    assert result.retry_after_ms is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (202, "RECEIVED", TransportSubmitCode.RECEIVED),
        (200, "DUPLICATE", TransportSubmitCode.DUPLICATE),
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
        data["reason_code"] = "INVALID_DATA"
    access = _ack(status, code, data)

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 413])
async def test_nonempty_preassociation_error_is_not_promoted_to_authoritative_rejection(status_code: int) -> None:
    access = _ack(
        status_code,
        "REJECTED",
        {"transport_task_id": "transport-1", "reason_code": "PROXY_GENERATED_ERROR"},
    )

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 413])
async def test_empty_preassociation_error_confirms_request_was_not_accepted(status_code: int) -> None:
    access = FakeAccessResult(
        delivery_state="RESPONSE_RECEIVED",
        status_code=status_code,
        json_body=None,
        body_present=False,
    )

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.REJECTED


@pytest.mark.asyncio
async def test_invalid_ack_pair_with_foreign_task_id_remains_delivery_unknown() -> None:
    access = _ack(500, "RECEIVED", {"transport_task_id": "transport-other"})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [422])
async def test_rejected_ack_preserves_a_persistable_reason_code(status_code: int) -> None:
    access = _ack(
        status_code,
        "REJECTED",
        {"transport_task_id": "transport-1", "reason_code": "COORDINATED_BIN_EXCHANGE_UNSUPPORTED"},
    )

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.REJECTED
    assert result.reason_code == "COORDINATED_BIN_EXCHANGE_UNSUPPORTED"


@pytest.mark.asyncio
async def test_rejected_ack_may_omit_transport_task_id() -> None:
    access = _ack(422, "REJECTED", {"reason_code": "INVALID_DATA"})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.REJECTED
    assert result.reason_code == "INVALID_DATA"


@pytest.mark.asyncio
async def test_rejected_ack_rejects_explicit_null_transport_task_id() -> None:
    access = _ack(422, "REJECTED", {"transport_task_id": None, "reason_code": "INVALID_DATA"})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
async def test_transport_ack_requires_json_utf8_response_content_type() -> None:
    access = _ack(202, "RECEIVED", {"transport_task_id": "transport-1"})
    access.response_headers = (("Content-Type", "text/plain"),)

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
async def test_ack_for_another_task_remains_delivery_unknown() -> None:
    access = _ack(202, "RECEIVED", {"transport_task_id": "transport-other"})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type",
    [
        'application/json; charset=u"t"f-8',
        "application/json; charset =utf-8",
        "application/json; charset= utf-8",
    ],
)
async def test_transport_ack_rejects_malformed_utf8_charset(content_type: str) -> None:
    access = _ack(202, "RECEIVED", {"transport_task_id": "transport-1"})
    access.response_headers = (("Content-Type", content_type),)

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation_id", "another-request"),
        ("unexpected", 123),
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

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
async def test_rejected_ack_discards_reason_code_that_cannot_be_persisted() -> None:
    access = _ack(422, "REJECTED", {"transport_task_id": "transport-1", "reason_code": "R" * 121})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN
    assert result.reason_code is None


@pytest.mark.asyncio
async def test_rejected_ack_discards_unencodable_reason_code() -> None:
    reason_code = json.loads(r'"\ud800"')
    access = _ack(422, "REJECTED", {"transport_task_id": "transport-1", "reason_code": reason_code})

    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN
    assert result.reason_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "data"),
    [
        (202, "RECEIVED", {}),
        (422, "REJECTED", {"transport_task_id": "transport-1"}),
        (202, "RECEIVED", {"transport_task_id": "transport-1", "reason_code": "UNEXPECTED"}),
        (503, "UNAVAILABLE", {"transport_task_id": "transport-1", "retry_after_ms": 1000}),
        (
            422,
            "REJECTED",
            {"transport_task_id": "transport-1", "reason_code": "INVALID_DATA", "retry_after_ms": 1000},
        ),
    ],
)
async def test_ack_data_must_match_the_code_specific_closed_contract(
    status: int,
    code: str,
    data: dict[str, object],
) -> None:
    result = await WmsTransportAdapter(FakeClient(_ack(status, code, data))).submit(**_snapshot())

    assert result.code is TransportSubmitCode.DELIVERY_UNKNOWN


@pytest.mark.asyncio
async def test_closed_transport_is_a_deterministic_not_sent_result() -> None:
    result = await WmsTransportAdapter(ClosedClient()).submit(**_snapshot())

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
    result = await WmsTransportAdapter(FakeClient(access)).submit(**_snapshot())

    expected = (
        TransportSubmitCode.NOT_SENT if access.delivery_state == "NOT_SENT" else TransportSubmitCode.DELIVERY_UNKNOWN
    )
    assert result.code is expected
