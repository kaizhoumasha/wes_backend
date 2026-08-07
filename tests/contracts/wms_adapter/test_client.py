from __future__ import annotations

import asyncio
import importlib
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from src.app.wms_adapter import WmsAccessResult, WmsClient
from src.core.outbound_http import (
    OutboundHttpClosedError,
    OutboundHttpDeliveryState,
    OutboundHttpFailureKind,
    OutboundHttpMethod,
    OutboundHttpRequestError,
    OutboundHttpResult,
)

if TYPE_CHECKING:
    from src.core.outbound_http import OutboundHttpRequest


class _FakeTransport:
    def __init__(
        self,
        result: OutboundHttpResult | None = None,
        *,
        send_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.result = result or _response(body=b"{}")
        self.send_error = send_error
        self.close_error = close_error
        self.requests: list[OutboundHttpRequest] = []
        self.close_calls = 0

    async def send(self, request: OutboundHttpRequest) -> OutboundHttpResult:
        self.requests.append(request)
        if self.send_error is not None:
            raise self.send_error
        return self.result

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def _response(
    *,
    body: bytes,
    status_code: int = 200,
    headers: tuple[tuple[str, str], ...] = (),
) -> OutboundHttpResult:
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=status_code,
        response_headers=headers,
        decoded_body=body,
    )


def test_wms_adapter_exposes_only_the_public_surface() -> None:
    module = importlib.import_module("src.app.wms_adapter")

    assert module.__all__ == ["WmsAccessResult", "WmsClient", "build_wms_client"]


@pytest.mark.asyncio
async def test_get_builds_one_transport_request_without_a_json_body() -> None:
    transport = _FakeTransport(_response(body=b'{"ok":true}'))
    client = WmsClient(transport)

    result = await client.get(
        "/inventory",
        query={"sku": "A-1", "site": "CN"},
        headers={"X-Request-Id": "request-1"},
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method is OutboundHttpMethod.GET
    assert request.path == "/inventory"
    assert request.query == (("sku", "A-1"), ("site", "CN"))
    assert request.headers == (("X-Request-Id", "request-1"),)
    assert request.body == b""
    assert result.json_body == {"ok": True}


@pytest.mark.asyncio
async def test_request_accepts_method_and_path_as_positional_arguments() -> None:
    transport = _FakeTransport()

    await WmsClient(transport).request(OutboundHttpMethod.GET, "/inventory")

    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_post_encodes_compact_utf8_json_and_owns_content_type() -> None:
    transport = _FakeTransport()
    client = WmsClient(transport)

    await client.post(
        "/tasks",
        json={"message": "中文", "items": [1, True, None]},
        headers={"X-Request-Id": "request-2"},
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method is OutboundHttpMethod.POST
    assert request.headers == (("X-Request-Id", "request-2"), ("Content-Type", "application/json"))
    assert request.body == b'{"message":"\xe4\xb8\xad\xe6\x96\x87","items":[1,true,null]}'


@pytest.mark.asyncio
async def test_post_encodes_finite_float_json_values() -> None:
    transport = _FakeTransport()

    await WmsClient(transport).post("/tasks", json={"quantity": 1.25})

    assert transport.requests[0].body == b'{"quantity":1.25}'


@pytest.mark.asyncio
async def test_post_none_means_a_json_null_body() -> None:
    transport = _FakeTransport()

    await WmsClient(transport).post("/tasks", json=None)

    assert transport.requests[0].query == ()
    assert transport.requests[0].headers == (("Content-Type", "application/json"),)
    assert transport.requests[0].body == b"null"


@pytest.mark.asyncio
async def test_request_rejects_get_json_and_post_without_json_before_send() -> None:
    transport = _FakeTransport()
    client = WmsClient(transport)

    with pytest.raises(OutboundHttpRequestError, match="GET must not contain JSON"):
        await client.request(method=OutboundHttpMethod.GET, path="/tasks", json={})
    with pytest.raises(OutboundHttpRequestError, match="POST requires JSON"):
        await client.request(method=OutboundHttpMethod.POST, path="/tasks")

    assert transport.requests == []


@pytest.mark.asyncio
async def test_transport_rejects_invalid_path_and_duplicate_headers_before_send() -> None:
    transport = _FakeTransport()
    client = WmsClient(transport)

    with pytest.raises(OutboundHttpRequestError, match="relative path"):
        await client.get("https://wms.test/inventory")
    with pytest.raises(OutboundHttpRequestError, match="duplicate header"):
        await client.get("/inventory", headers={"X-Request-Id": "one", "x-request-id": "two"})

    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("header_name", ["Content-Type", "content-type", "CONTENT-TYPE"])
async def test_caller_cannot_override_content_type(header_name: str) -> None:
    transport = _FakeTransport()

    with pytest.raises(OutboundHttpRequestError, match="Content-Type is owned by WmsClient"):
        await WmsClient(transport).post("/tasks", json={}, headers={header_name: "text/plain"})

    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header_name", "header_value"),
    [
        ("Host", "other-service.internal"),
        ("host", "other-service.internal"),
        ("Content-Length", "0"),
        ("content-length", "0"),
        ("Transfer-Encoding", "chunked"),
        ("transfer-encoding", "chunked"),
        ("Content-Encoding", "gzip"),
        ("content-encoding", "gzip"),
    ],
)
async def test_caller_cannot_override_origin_or_body_framing_headers(
    header_name: str,
    header_value: str,
) -> None:
    transport = _FakeTransport()

    with pytest.raises(OutboundHttpRequestError, match="header is owned by WmsClient"):
        await WmsClient(transport).post("/tasks", json={}, headers={header_name: header_value})

    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_json",
    [
        ("tuple",),
        {1: "non-string-key"},
        object(),
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
async def test_post_rejects_non_json_values_before_send(invalid_json: object) -> None:
    transport = _FakeTransport()

    with pytest.raises((TypeError, ValueError)):
        await WmsClient(transport).post("/tasks", json=invalid_json)

    assert transport.requests == []


@pytest.mark.asyncio
async def test_post_rejects_a_circular_json_container_before_send() -> None:
    transport = _FakeTransport()
    circular: list[object] = []
    circular.append(circular)

    with pytest.raises(ValueError, match="circular"):
        await WmsClient(transport).post("/tasks", json=circular)

    assert transport.requests == []


@pytest.mark.asyncio
async def test_post_propagates_utf8_encoding_errors_before_send() -> None:
    transport = _FakeTransport()

    with pytest.raises(UnicodeEncodeError):
        await WmsClient(transport).post("/tasks", json="\ud800")

    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delivery_state", "failure_kind"),
    [
        (OutboundHttpDeliveryState.NOT_SENT, OutboundHttpFailureKind.POOL_TIMEOUT),
        (OutboundHttpDeliveryState.NOT_SENT, OutboundHttpFailureKind.CONNECT_TIMEOUT),
        (OutboundHttpDeliveryState.NOT_SENT, OutboundHttpFailureKind.CONNECT_ERROR),
        (OutboundHttpDeliveryState.DELIVERY_UNKNOWN, OutboundHttpFailureKind.WRITE_TIMEOUT),
        (OutboundHttpDeliveryState.DELIVERY_UNKNOWN, OutboundHttpFailureKind.WRITE_ERROR),
        (OutboundHttpDeliveryState.DELIVERY_UNKNOWN, OutboundHttpFailureKind.READ_TIMEOUT),
        (OutboundHttpDeliveryState.DELIVERY_UNKNOWN, OutboundHttpFailureKind.READ_ERROR),
        (OutboundHttpDeliveryState.DELIVERY_UNKNOWN, OutboundHttpFailureKind.REMOTE_PROTOCOL_ERROR),
        (OutboundHttpDeliveryState.DELIVERY_UNKNOWN, OutboundHttpFailureKind.TOTAL_TIMEOUT),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.READ_TIMEOUT),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.READ_ERROR),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.REMOTE_PROTOCOL_ERROR),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.TOTAL_TIMEOUT),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.RESPONSE_HEADER_LIMIT_EXCEEDED),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.RESPONSE_METADATA_INVALID),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.RESPONSE_CHUNK_LIMIT_EXCEEDED),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.RESPONSE_WIRE_LIMIT_EXCEEDED),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.RESPONSE_DECODED_LIMIT_EXCEEDED),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.RESPONSE_COMPRESSION_RATIO_EXCEEDED),
        (
            OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            OutboundHttpFailureKind.RESPONSE_CONTENT_ENCODING_UNSUPPORTED,
        ),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.RESPONSE_CONTENT_ENCODING_INVALID),
        (OutboundHttpDeliveryState.RESPONSE_RECEIVED, OutboundHttpFailureKind.RESPONSE_CLEANUP_FAILED),
    ],
)
async def test_every_transport_failure_preserves_only_transport_facts(
    delivery_state: OutboundHttpDeliveryState,
    failure_kind: OutboundHttpFailureKind,
) -> None:
    status_code = 502 if delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED else None
    transport = _FakeTransport(
        OutboundHttpResult(
            delivery_state=delivery_state,
            status_code=status_code,
            failure_kind=failure_kind,
        )
    )

    result = await WmsClient(transport).get("/inventory")

    assert result == WmsAccessResult(
        delivery_state=delivery_state,
        failure_kind=failure_kind,
        status_code=status_code,
        response_headers=(),
        body_present=False,
        json_body=None,
        json_failure=None,
    )


@pytest.mark.asyncio
async def test_empty_body_and_json_null_remain_distinguishable() -> None:
    empty_result = await WmsClient(_FakeTransport(_response(body=b""))).get("/empty")
    null_result = await WmsClient(_FakeTransport(_response(body=b"null"))).get("/null")

    assert empty_result.body_present is False
    assert empty_result.json_body is None
    assert empty_result.json_failure is None
    assert null_result.body_present is True
    assert null_result.json_body is None
    assert null_result.json_failure is None


@pytest.mark.asyncio
async def test_valid_json_is_decoded_for_any_http_status_and_content_type() -> None:
    transport = _FakeTransport(
        _response(
            body=b'{"code":"BUSINESS_RESULT"}',
            status_code=409,
            headers=(("Content-Type", "text/plain"), ("X-WMS", "primary")),
        )
    )

    result = await WmsClient(transport).get("/decision")

    assert result.status_code == 409
    assert result.response_headers == (("Content-Type", "text/plain"), ("X-WMS", "primary"))
    assert result.json_body == {"code": "BUSINESS_RESULT"}
    assert result.json_failure is None


@pytest.mark.asyncio
async def test_finite_float_json_response_is_decoded() -> None:
    result = await WmsClient(_FakeTransport(_response(body=b'{"quantity":1.25}'))).get("/inventory")

    assert result.json_body == {"quantity": 1.25}
    assert result.json_failure is None


@pytest.mark.asyncio
async def test_invalid_utf8_preserves_http_facts_and_reports_json_failure() -> None:
    result = await WmsClient(_FakeTransport(_response(body=b"\xff", status_code=502))).get("/decision")

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 502
    assert result.body_present is True
    assert result.json_body is None
    assert result.json_failure == "INVALID_UTF8"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b" ", b"not-json", b"NaN", b"Infinity", b"-Infinity"])
async def test_invalid_json_preserves_http_facts_and_reports_json_failure(body: bytes) -> None:
    result = await WmsClient(_FakeTransport(_response(body=body, status_code=500))).get("/decision")

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 500
    assert result.body_present is True
    assert result.json_body is None
    assert result.json_failure == "INVALID_JSON"


@pytest.mark.asyncio
async def test_deep_json_freeze_failure_preserves_http_facts() -> None:
    body = b"[" * 500 + b"0" + b"]" * 500

    result = await WmsClient(_FakeTransport(_response(body=body, status_code=502))).get("/decision")

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 502
    assert result.body_present is True
    assert result.json_body is None
    assert result.json_failure == "INVALID_JSON"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"1e400", b'{"nested":[1e400]}'])
async def test_exponent_overflow_reports_invalid_json_without_losing_http_facts(body: bytes) -> None:
    result = await WmsClient(
        _FakeTransport(_response(body=body, status_code=502, headers=(("X-WMS", "primary"),)))
    ).get("/decision")

    assert result.delivery_state is OutboundHttpDeliveryState.RESPONSE_RECEIVED
    assert result.status_code == 502
    assert result.response_headers == (("X-WMS", "primary"),)
    assert result.body_present is True
    assert result.json_body is None
    assert result.json_failure == "INVALID_JSON"


@pytest.mark.asyncio
async def test_send_cancellation_and_closed_error_propagate_without_translation() -> None:
    cancellation = asyncio.CancelledError()
    cancelled_transport = _FakeTransport(send_error=cancellation)
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await WmsClient(cancelled_transport).get("/inventory")
    assert cancelled.value is cancellation
    assert len(cancelled_transport.requests) == 1

    closed_error = OutboundHttpClosedError("closed")
    closed_transport = _FakeTransport(send_error=closed_error)
    with pytest.raises(OutboundHttpClosedError) as closed:
        await WmsClient(closed_transport).get("/inventory")
    assert closed.value is closed_error
    assert len(closed_transport.requests) == 1


@pytest.mark.asyncio
async def test_aclose_delegates_without_translating_errors() -> None:
    close_error = RuntimeError("close failed")
    transport = _FakeTransport(close_error=close_error)

    with pytest.raises(RuntimeError) as closed:
        await WmsClient(transport).aclose()

    assert closed.value is close_error
    assert transport.close_calls == 1


@pytest.mark.asyncio
async def test_repeated_aclose_remains_idempotent_through_the_transport() -> None:
    transport = _FakeTransport()
    client = WmsClient(transport)

    await client.aclose()
    await client.aclose()

    assert transport.close_calls == 2


def test_access_result_is_immutable() -> None:
    result = WmsAccessResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        failure_kind=None,
        status_code=200,
        response_headers=(),
        body_present=True,
        json_body={},
        json_failure=None,
    )

    with pytest.raises(FrozenInstanceError):
        result.status_code = 201  # type: ignore[misc]


@pytest.mark.asyncio
async def test_access_result_json_body_is_deeply_immutable() -> None:
    result = await WmsClient(_FakeTransport(_response(body=b'{"items":[{"status":"READY"}]}'))).get("/decision")

    assert isinstance(result.json_body, Mapping)
    items = result.json_body["items"]
    assert isinstance(items, tuple)
    item = items[0]
    assert isinstance(item, Mapping)
    with pytest.raises(TypeError):
        item["status"] = "DONE"  # type: ignore[index]
